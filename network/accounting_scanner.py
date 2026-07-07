"""
Remote ``gm2au`` state read over the admin share.

Screen capture / QR decoding is bypassed for speed (SAS vs XML workflow). The
``local_capture_path`` argument to :func:`fetch_accounting_data` is kept for API
compatibility but ignored while QR scanning is disabled.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)


def _gm2au_unc_root(ip: str) -> Path:
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
    """Read ``gm2au`` file or newest file in that directory; return text or error string."""
    try:
        if gm_path.is_file():
            return gm_path.read_text(encoding="utf-8", errors="replace")
        if gm_path.is_dir():
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
            "Check that GoldClub.Aurum.Services state is deployed on the cabinet."
        )
    except OSError as e:
        return f"Could not read backend state from {gm_path}:\n{e}"


def _read_gm2au_state_for_ui(gm_path: Path) -> str:
    """Read gm2au from disk and return formatted meters + raw XML for display."""
    return _parse_gm2au_meters(_read_state_file_text(gm_path))


def fetch_accounting_data(ip_address: str, local_capture_path: str) -> tuple[str, str]:
    """
    Read ``gm2au`` state from the cabinet (SMB). QR / screen capture is bypassed.

    ``local_capture_path`` is unused while QR scanning is disabled (API compatibility).

    Returns ``(qr_text, state_text)`` — errors are returned as human-readable strings
    in the appropriate side, not raised.
    """
    _ = local_capture_path  # retained for callers; no capture while QR is disabled
    ip = (ip_address or "").strip()
    if not ip:
        return "No IP address provided.", ""

    # --- Part 1 (Capture and Decode QR) — disabled for fast SAS/XML comparison ---
    # local = Path(local_capture_path)
    # ok, msg = capture_remote_screen(ip, str(local))
    # if not ok:
    #     return f"Screen capture failed: {msg}", _read_gm2au_state_for_ui(...)
    # ... OpenCV cv2.imread / QRCodeDetector.detectAndDecode ...
    # finally: unlink capture file
    qr_text = "QR Scanning Disabled (Bypassed for fast SAS/XML comparison)."

    # --- Part 2 (Fetch XML state via SMB) ---
    state_text = _read_gm2au_state_for_ui(_gm2au_unc_root(ip))
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
