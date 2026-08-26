"""
Remote / local ``gm2au`` state read for accounting summaries.

Screen capture / QR decoding is bypassed for speed (SAS vs XML workflow). The
``local_capture_path`` argument to :func:`fetch_accounting_data` is kept for API
compatibility but ignored while QR scanning is disabled.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)


def _preferred_device_manager_file(gm2au_dir: Path) -> Path | None:
    """Prefer ``DeviceManagerData.xml_1`` / ``_2`` over unrelated files in gm2au."""
    for name in (
        "DeviceManagerData.xml_1",
        "DeviceManagerData.xml_2",
        "DeviceManagerData.xml_3",
        "DeviceManagerData.xml_4",
    ):
        cand = gm2au_dir / name
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def resolve_gm2au_dir(ip_or_scan_root: str) -> Path | None:
    """
    Resolve the ``gm2au`` folder that holds ``DeviceManagerData.xml_*``.

    Works for:
    - remote IP (``10.0.0.90``) via SMB admin share
    - UNC / local scan roots (``\\\\ip\\c$\\Goldclub\\var\\log``, ``C:\\Goldclub\\…``)
    - roulette ``ruleta\\var\\gm2au`` and slot ``…\\GCMessenger\\gm2au``
    """
    from network.goldclub_paths import (
        extract_ip_from_path,
        resolve_goldclub_layout,
        resolve_sas_verify_scan_root,
    )

    raw = (ip_or_scan_root or "").strip()
    if not raw:
        return None

    hint = raw
    remote_ip = extract_ip_from_path(raw)
    if remote_ip is None and "\\" not in raw and "/" not in raw and raw.count(".") == 3:
        # Bare IPv4 → remote cabinet
        remote_ip = raw
        hint = rf"\\{raw}\c$\Goldclub\var\log"

    discovery = resolve_sas_verify_scan_root(hint, remote_ip=remote_ip)
    layout = resolve_goldclub_layout(discovery.scan_root)
    if layout is not None and layout.state_gcmessenger is not None:
        gm2au = layout.state_gcmessenger / "gm2au"
        try:
            if gm2au.is_dir():
                return gm2au
        except OSError:
            pass

    # Explicit roulette / slot fallbacks (local + UNC)
    ip = remote_ip or extract_ip_from_path(discovery.scan_root)
    fallbacks: list[Path] = []
    if ip:
        for gc in ("Goldclub", "goldclub"):
            fallbacks.append(Path(rf"\\{ip}\c$\{gc}\ruleta\var\gm2au"))
            fallbacks.append(
                Path(
                    rf"\\{ip}\c$\{gc}\var\state\GoldClub.Aurum.Services\GCMessenger\gm2au"
                )
            )
    fallbacks.extend(
        [
            Path(r"C:\Goldclub\ruleta\var\gm2au"),
            Path(r"C:\goldclub\ruleta\var\gm2au"),
            Path(r"G:\Goldclub\ruleta\var\gm2au"),
            Path(r"G:\goldclub\ruleta\var\gm2au"),
            Path(r"G:\ruleta\var\gm2au"),
            Path(
                r"C:\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger\gm2au"
            ),
        ]
    )
    for p in fallbacks:
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def _gm2au_unc_root(ip: str) -> Path:
    """Back-compat helper: gm2au dir for a cabinet IP (may not exist)."""
    found = resolve_gm2au_dir(ip)
    if found is not None:
        return found
    ip = (ip or "").strip()
    return Path(rf"\\{ip}\c$\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger\gm2au")


def _parse_gm2au_meters(raw_text: str) -> str:
    """Strip binary headers from gm2au XML and extract key meters while ignoring namespaces."""
    xml_start = raw_text.find("<?xml")
    if xml_start == -1:
        return raw_text

    cleaned_xml = raw_text[xml_start:]

    try:
        root = ET.fromstring(cleaned_xml)

        def strip_ns(tag_string: str) -> str:
            return tag_string.split("}")[-1] if tag_string else ""

        def get_attr(element: ET.Element, attr_name: str, default: str = "0") -> str:
            for k, v in element.attrib.items():
                if strip_ns(k) == attr_name:
                    return v
            return default

        output = ["=== 📊 EXTRACTED METERS ==="]

        for device in root.iter():
            if strip_ns(device.tag) == "Devices" and get_attr(
                device, "DeviceClass", ""
            ) == "processor":
                for child in device.iter():
                    if strip_ns(child.tag) == "creditMeters":
                        output.append("\n[ CURRENT CREDITS ]")
                        output.append(f"Cashable : {get_attr(child, 'cashable', '0')}")
                        output.append(f"Promo    : {get_attr(child, 'promo', '0')}")
                        output.append(f"NonCash  : {get_attr(child, 'nonCash', '0')}")

        for device in root.iter():
            if strip_ns(device.tag) == "Devices" and get_attr(
                device, "DeviceClass", ""
            ) == "meters":
                output.append("\n[ ACCOUNTING METERS ]")
                for child in device.iter():
                    if strip_ns(child.tag) == "perfMeter":
                        name = get_attr(child, "meterName", "Unknown")
                        val = get_attr(child, "meterValue", "0")
                        output.append(f"{name:<20}: {val}")

                output.append("\n[ CURRENCY METERS ]")
                for child in device.iter():
                    if strip_ns(child.tag) == "curMeter":
                        name = get_attr(child, "meterName", "Unknown")
                        denom = get_attr(child, "denomId", "?")
                        val = get_attr(child, "meterValue", "0")
                        output.append(f"{name} ({denom}c denom) : {val}")

        output.append("\n" + "=" * 28 + "\n\n=== RAW STATE FILE ===\n" + cleaned_xml)
        return "\n".join(output)

    except ET.ParseError as e:
        return f"=== ⚠️ XML PARSE ERROR: {e} ===\n\n{cleaned_xml}"


def _read_state_file_text(gm_path: Path) -> str:
    """Read ``gm2au`` DeviceManagerData (or newest file); return text or error string."""
    try:
        if gm_path.is_file():
            return gm_path.read_text(encoding="utf-8", errors="replace")
        if gm_path.is_dir():
            preferred = _preferred_device_manager_file(gm_path)
            if preferred is not None:
                return preferred.read_text(encoding="utf-8", errors="replace")
            files = [p for p in gm_path.iterdir() if p.is_file()]
            if not files:
                return (
                    f"No files found in directory:\n{gm_path}\n"
                    "(gm2au exists but is empty.)"
                )
            newest = max(files, key=lambda p: p.stat().st_mtime_ns)
            return newest.read_text(encoding="utf-8", errors="replace")
        return (
            f"Backend state path not found (not a file or directory):\n{gm_path}\n"
            "Check ruleta\\var\\gm2au (roulette) or GCMessenger\\gm2au (slot) on the cabinet."
        )
    except OSError as e:
        return f"Could not read backend state from {gm_path}:\n{e}"


def _read_gm2au_state_for_ui(gm_path: Path) -> str:
    """Read gm2au from disk and return formatted meters + raw XML for display."""
    return _parse_gm2au_meters(_read_state_file_text(gm_path))


def fetch_accounting_data(ip_address: str, local_capture_path: str) -> tuple[str, str]:
    """
    Read ``gm2au`` state from the cabinet (SMB or local). QR / screen capture is bypassed.

    ``ip_address`` may be a bare IP, UNC path, or local Goldclub/log root.
    ``local_capture_path`` is unused while QR scanning is disabled (API compatibility).

    Returns ``(qr_text, state_text)`` — errors are returned as human-readable strings
    in the appropriate side, not raised.
    """
    _ = local_capture_path  # retained for callers; no capture while QR is disabled
    target = (ip_address or "").strip()
    if not target:
        return "No IP address provided.", ""

    qr_text = "QR Scanning Disabled (Bypassed for fast SAS/XML comparison)."

    gm2au = resolve_gm2au_dir(target)
    if gm2au is None:
        return qr_text, (
            "Backend gm2au state not found.\n"
            "Tried roulette ruleta\\var\\gm2au and slot GCMessenger\\gm2au "
            f"for target={target!r}."
        )
    state_text = _read_gm2au_state_for_ui(gm2au)
    return qr_text, state_text


def fetch_accounting_data_with_tempfile(ip_address: str) -> tuple[str, str]:
    """
    Like :func:`fetch_accounting_data` but allocates a temp path under the OS temp dir.
    """
    import tempfile
    import time

    ip = (ip_address or "").strip()
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in ip)[:40]
    stamp = int(time.time() * 1000) % 1_000_000_000_000
    path = str(Path(tempfile.gettempdir()) / f"glci_accounting_{safe}_{stamp}.jpg")
    return fetch_accounting_data(ip_address, path)
