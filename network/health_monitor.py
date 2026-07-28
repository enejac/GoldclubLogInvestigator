"""
Remote physical memory stats via WMIC (``/node``) for correlation with incidents.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from network.lab_access import (
    FleetAllowlistError,
    LabCredentialError,
    ensure_lab_smb_credential,
    require_lab_fleet_ip,
)

logger = logging.getLogger(__name__)

GameClientKind = Literal["slot", "roulette"]

_WMIC_LINE_RE = re.compile(r"^([A-Za-z]+)=(.*)$")
_INVALID_REMOTE_CABINET_IPS: frozenset[str] = frozenset(
    {"0.0.0.0", "127.0.0.1", "255.255.255.255"}
)


def _scan_root_looks_roulette(scan_root: str | None) -> bool:
    """
    True when a path *component* names the roulette install.

    Matching the bare substring anywhere in the string turned unrelated folders
    (``D:\\exports\\roulette-tickets\\slotlogs``) into roulette cabinets, so the
    test runs per segment and still accepts suffixed variants like ``ruleta2``.
    """
    root = (scan_root or "").replace("/", "\\").casefold().rstrip("\\")
    if not root:
        return False
    for seg in root.split("\\"):
        for name in ("ruleta", "roulette"):
            if seg == name or (seg.startswith(name) and seg[len(name) :].isdigit()):
                return True
    return False


def resolve_game_client_kind_detailed(
    *,
    hint: str | None = None,
    scan_root: str | None = None,
) -> tuple[GameClientKind, bool]:
    """
    Same as :func:`resolve_game_client_kind` plus whether the kind was *determined*.

    The second element is ``False`` when nothing identified the cabinet and the
    slot default was applied — an unreachable share looks exactly like a slot
    machine otherwise, which silently picks the wrong SAS wire order.
    """
    h = (hint or "").strip().lower()
    if _scan_root_looks_roulette(scan_root):
        return "roulette", True
    if h == "roulette":
        return "roulette", True
    if h == "slot":
        return "slot", True
    # Shared …\Goldclub\var\log (no "ruleta" in path): probe Ruleta.exe like AFT.
    if scan_root:
        try:
            from network.goldclub_paths import detect_game_kind_from_scan_root

            detected = detect_game_kind_from_scan_root(scan_root)
            if detected == "roulette":
                return "roulette", True
            if detected == "slot":
                return "slot", True
        except Exception:
            logger.debug("game kind probe failed for %s", scan_root, exc_info=True)
    logger.debug("game kind undetermined for %r (hint=%r) — defaulting to slot", scan_root, hint)
    return "slot", False


def resolve_game_client_kind(
    *,
    hint: str | None = None,
    scan_root: str | None = None,
) -> GameClientKind:
    """Pick slot vs roulette from scan-root path and/or an explicit hint.

    A ``…\\ruleta`` / roulette scan path wins over a stale ``hint="slot"`` so the
    SAS dialog never warns about ``OneHand.exe`` on roulette cabinets.
    """
    kind, _determined = resolve_game_client_kind_detailed(hint=hint, scan_root=scan_root)
    return kind


def game_client_exe_name(kind: GameClientKind) -> str:
    # Roulette meters need the Godot frontend up — not Ruleta.exe (host/backend).
    return "godot.exe" if kind == "roulette" else "OneHand.exe"


def game_client_process_basenames(kind: GameClientKind) -> tuple[str, ...]:
    """``tasklist`` / ``Get-Process`` base names (no .exe)."""
    if kind == "roulette":
        # Godot UI process; Ruleta.exe alone does not open SAS RX.
        return ("godot", "godot1")
    return ("OneHand",)


def game_client_process_match_script(kind: GameClientKind) -> str:
    """PowerShell expression that prints RUNNING or STOPPED."""
    if kind == "roulette":
        # Match godot, godot1, Godot_v3, etc.
        return (
            "if (@(Get-Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.ProcessName -like 'godot*' }).Count -gt 0) "
            "{ 'RUNNING' } else { 'STOPPED' }"
        )
    names = ", ".join(f"'{b}'" for b in game_client_process_basenames(kind))
    return (
        f"if (Get-Process -Name {names} -ErrorAction SilentlyContinue) "
        "{ 'RUNNING' } else { 'STOPPED' }"
    )


# Single source of truth: network.app_runtime (launch-time local EGM detection).
from network.app_runtime import (  # noqa: E402
    is_running_on_local_egm,
    is_this_host,
    local_ipv4_addresses,
)


def is_valid_remote_cabinet_ip(ip_address: str) -> bool:
    """True only for known lab fleet IPs (rejects blank/loopback/broadcast/non-fleet)."""
    ip = (ip_address or "").strip()
    if not ip or ip in _INVALID_REMOTE_CABINET_IPS:
        return False
    try:
        require_lab_fleet_ip(ip)
        return True
    except (FleetAllowlistError, ValueError):
        return False


def _parse_wmic_value_block(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WMIC_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            continue
        try:
            out[key] = int(val)
        except ValueError:
            continue
    return out


def _parse_working_set_bytes_sum(text: str) -> int | None:
    """Sum all ``WorkingSetSize`` lines (bytes); multiple ``OneHand.exe`` instances possible."""
    total = 0
    found = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WMIC_LINE_RE.match(line)
        if not m or m.group(1) != "WorkingSetSize":
            continue
        try:
            total += int(m.group(2).strip())
            found = True
        except ValueError:
            continue
    return total if found else None


def _fetch_onehand_process_mb(ip: str) -> float | None:
    """
    ``WorkingSetSize`` in bytes for ``OneHand.exe`` via remote WMIC.
    Returns MB (rounded), or ``None`` if the process is not found / query fails.
    """
    cmd = [
        "wmic",
        f"/node:{ip}",
        "process",
        "where",
        "name='OneHand.exe'",
        "get",
        "WorkingSetSize",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 45,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC OneHand process query failed for %s: %s", ip, e)
        return None

    out = r.stdout or ""
    b = _parse_working_set_bytes_sum(out)
    if b is None or b <= 0:
        return None
    return round(b / (1024.0 * 1024.0), 2)


def _wmic_process_output(ip: str, image_name: str) -> tuple[int, str]:
    """Query remote process ids via WMIC for ``image_name`` (e.g. ``Ruleta.exe``)."""
    cmd = [
        "wmic",
        f"/node:{ip}",
        "process",
        "where",
        f"name='{image_name}'",
        "get",
        "ProcessId",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 30,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC %s presence query failed for %s: %s", image_name, ip, e)
        return -1, ""
    return int(r.returncode), r.stdout or ""


def _wmic_onehand_process_output(ip: str) -> tuple[int, str]:
    """Back-compat: query remote ``OneHand.exe`` process ids via WMIC."""
    return _wmic_process_output(ip, "OneHand.exe")


@dataclass(frozen=True, slots=True)
class OneHandStatus:
    running: bool | None
    smb_reachable: bool


GameClientStatus = OneHandStatus


def cabinet_smb_reachable(ip_address: str, *, timeout: float = 2.5) -> bool:
    """
    True when the cabinet admin share / log root is reachable (TCP/445 + UNC probe).

    WMIC can fail even when SMB works; always probe share access before blaming the network.
    """
    from network.scanner_utils import is_smb_alive

    ip = (ip_address or "").strip()
    if not ip:
        return False
    if not is_smb_alive(ip, timeout=timeout):
        return False
    ensure_lab_smb_credential(ip)
    from pathlib import Path

    log_root = Path(rf"\\{ip}\c$\Goldclub\var\log")
    try:
        return log_root.is_dir()
    except OSError:
        return False


def _parse_wmic_running(out: str, *, code: int) -> bool | None:
    if code != 0:
        return None
    if "No Instance(s) Available" in out:
        return False
    for raw in out.splitlines():
        line = raw.strip()
        if not line.startswith("ProcessId="):
            continue
        val = line.split("=", 1)[1].strip()
        if val.isdigit() and int(val) > 0:
            return True
    return False


def _game_client_via_wmic(ip: str, kind: GameClientKind) -> bool | None:
    results: list[bool | None] = []
    for base in game_client_process_basenames(kind):
        image = base if base.lower().endswith(".exe") else f"{base}.exe"
        code, out = _wmic_process_output(ip, image)
        results.append(_parse_wmic_running(out, code=code))
    if any(r is True for r in results):
        return True
    if results and all(r is False for r in results):
        return False
    return None


def _onehand_via_wmic(ip: str) -> bool | None:
    return _game_client_via_wmic(ip, "slot")


def _game_client_via_winrm(ip: str, kind: GameClientKind) -> bool | None:
    """Process presence via WinRM Invoke-Command (fast, reliable — lab standard).

    Preferred over WMIC (deprecated/blocked on newer Windows) and PsExec (needs a
    bundled ``tools/psexec.exe``). Returns ``None`` only when WinRM itself is
    unreachable, so callers can fall back.
    """
    from automation.remote_exec import winrm_run_inline

    script = game_client_process_match_script(kind)
    try:
        result = winrm_run_inline(ip=ip, script=script, timeout=25)
    except (LabCredentialError, FleetAllowlistError, ValueError, OSError) as e:
        logger.debug("WinRM %s presence query failed for %s: %s", kind, ip, e)
        return None
    blob = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "RUNNING" in blob:
        return True
    if "STOPPED" in blob:
        return False
    return None


def _game_client_via_psexec(ip: str, kind: GameClientKind) -> bool | None:
    from automation.remote_exec import psexec_run, resolve_psexec_path

    if not resolve_psexec_path():
        return None
    ps_cmd = game_client_process_match_script(kind)
    try:
        result = psexec_run(
            ip=ip,
            remote_argv=["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            as_system=True,
            timeout=90,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, LabCredentialError, FleetAllowlistError) as e:
        logger.debug("PsExec %s presence query failed for %s: %s", kind, ip, e)
        return None
    blob = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "RUNNING" in blob:
        return True
    if "STOPPED" in blob or result.returncode == 0:
        return False
    return None


def _onehand_via_psexec(ip: str) -> bool | None:
    return _game_client_via_psexec(ip, "slot")


def _game_client_via_local_process(kind: GameClientKind) -> bool:
    """True when the game client is running on this Windows machine."""
    if os.name != "nt":
        return False
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 8,
        "check": False,
    }
    run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if kind == "roulette":
        try:
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    game_client_process_match_script(kind),
                ],
                **run_kw,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return "RUNNING" in (proc.stdout or "")
    for base in game_client_process_basenames(kind):
        image = f"{base}.exe"
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                **run_kw,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if image.lower() in (proc.stdout or "").lower():
            return True
    return False


def _onehand_via_local_process() -> bool:
    return _game_client_via_local_process("slot")


def check_game_client_status_local(*, kind: GameClientKind = "slot") -> OneHandStatus:
    """Local process probe for Investigator running on the EGM or from USB."""
    return OneHandStatus(running=_game_client_via_local_process(kind), smb_reachable=True)


def check_onehand_status_local() -> OneHandStatus:
    return check_game_client_status_local(kind="slot")


def local_egm_game_client_running() -> bool:
    """True when OneHand / Ruleta / godot is running on *this* Windows device.

    Used to gate the optional ``G:\\`` game-image prompt: only offer it when the
    SAS verify exe is on the same machine that is actually running the EGM client.
    """
    if os.name != "nt":
        return False
    # One tasklist blob — avoid N× console probes on the UI thread.
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 8,
        "check": False,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    try:
        proc = subprocess.run(["tasklist", "/NH"], **run_kw)
    except (OSError, subprocess.TimeoutExpired):
        return False
    blob = (proc.stdout or "").lower()
    if "onehand.exe" in blob:
        return True
    if "ruleta.exe" in blob:
        return True
    # godot, godot1, Godot_v3, …
    for line in blob.splitlines():
        name = line.strip().split(None, 1)[0] if line.strip() else ""
        if name.startswith("godot"):
            return True
    return False


def check_game_client_status(
    ip_address: str,
    *,
    kind: GameClientKind = "slot",
    allow_psexec: bool = True,
) -> OneHandStatus | None:
    """Probe cabinet reachability, then game client via WinRM (WMIC/PsExec fallback).

    ``allow_psexec`` gates the slow Sysinternals fallback. Callers that verify the
    SAS link another way (e.g. a live COM capture over MUX) should pass
    ``allow_psexec=False`` and only reach for PsExec when that also fails, so the
    fast path stays snappy and silent.
    """
    ip = (ip_address or "").strip()
    if not ip or os.name != "nt":
        return None
    if not is_valid_remote_cabinet_ip(ip):
        # Only this machine may be answered from local processes. Any other host
        # (hostname, non-fleet address) would report the workstation's own
        # OneHand/godot as if it were the cabinet's.
        if is_this_host(ip):
            return check_game_client_status_local(kind=kind)
        logger.debug("game client probe skipped for non-fleet host %r", ip)
        return None
    smb = cabinet_smb_reachable(ip)
    if not smb:
        return OneHandStatus(running=None, smb_reachable=False)
    # WinRM first (lab standard: fast, reliable, full access). WMIC is deprecated
    # and PsExec needs a bundled tools/psexec.exe, so both are fallbacks only.
    running = _game_client_via_winrm(ip, kind)
    if running is None:
        running = _game_client_via_wmic(ip, kind)
    if running is None and allow_psexec:
        running = _game_client_via_psexec(ip, kind)
    return OneHandStatus(running=running, smb_reachable=True)


def check_game_client_via_psexec(
    ip_address: str,
    *,
    kind: GameClientKind = "slot",
) -> OneHandStatus | None:
    """Last-resort PsExec-only verification (no WinRM/WMIC).

    Returns ``None`` silently when PsExec is unavailable (no bundled
    ``tools/psexec.exe``) or the host is off the lab fleet — callers treat that as
    "could not verify" only after every other path has failed.
    """
    ip = (ip_address or "").strip()
    if not ip or os.name != "nt" or not is_valid_remote_cabinet_ip(ip):
        return None
    running = _game_client_via_psexec(ip, kind)
    return OneHandStatus(running=running, smb_reachable=True)


def check_onehand_status(ip_address: str) -> OneHandStatus | None:
    """Probe cabinet reachability, then OneHand via WMIC with PsExec fallback."""
    return check_game_client_status(ip_address, kind="slot")


def is_onehand_running(ip_address: str) -> bool | None:
    """
    Return whether ``OneHand.exe`` is running on the cabinet at ``ip_address``.

    * ``True`` / ``False`` when a remote query answered.
    * ``None`` when the host could not be queried.
    """
    status = check_onehand_status(ip_address)
    if status is None:
        return None
    return status.running


def game_client_warning_text(
    ip_address: str,
    *,
    running: bool | None,
    smb_reachable: bool | None = None,
    com_meters_ok: bool = False,
    com_meters_failed: bool = False,
    kind: GameClientKind = "slot",
) -> str:
    """Human-readable warning when the slot/roulette game client is down.

    Silent by design: a successful COM capture (``com_meters_ok``) proves the link
    and suppresses everything. An inconclusive remote probe (``running is None``)
    is only surfaced once the COM/MUX capture has *also* definitively failed
    (``com_meters_failed``) — otherwise the tool stays quiet while fallbacks run.
    """
    if com_meters_ok:
        return ""
    ip = (ip_address or "").strip()
    if ip == "local":
        label = "this EGM"
    elif ip:
        label = ip
    else:
        label = "the cabinet"
    exe = game_client_exe_name(kind)
    if running is True:
        return ""
    if running is False:
        client = (
            "Godot frontend"
            if kind == "roulette"
            else "game client"
        )
        return (
            f"<b style='color:#b45309;'>Warning:</b> "
            f"<code>{exe}</code> is <b>not running</b> on <b>{label}</b>. "
            f"The SAS host link often returns no RX until the {client} is started "
            f"on the EGM (Aurum / CommCtrl stack)."
        )
    if smb_reachable is False:
        if not is_valid_remote_cabinet_ip(ip_address):
            return ""
        return (
            f"<b style='color:#92400e;'>Warning:</b> "
            f"Could not reach cabinet <b>{label}</b> "
            f"(SMB/log share unreachable — check lab network and credentials)."
        )
    # running is None: every verification path (WinRM → COM/MUX → PsExec) was
    # inconclusive. Stay silent unless the SAS COM capture also failed outright.
    if not com_meters_failed:
        return ""
    return (
        f"<b style='color:#92400e;'>Warning:</b> "
        f"Could not verify <code>{exe}</code> on <b>{label}</b> "
        f"(remote WinRM/PsExec and SAS COM capture all failed — "
        f"check the game client and SAS host cable)."
    )


def onehand_warning_text(
    ip_address: str,
    *,
    running: bool | None,
    smb_reachable: bool | None = None,
    com_meters_ok: bool = False,
    com_meters_failed: bool = False,
    kind: GameClientKind | None = None,
    scan_root: str | None = None,
) -> str:
    """Human-readable warning for UI when the game client is down or not verified."""
    resolved = resolve_game_client_kind(hint=kind, scan_root=scan_root)
    return game_client_warning_text(
        ip_address,
        running=running,
        smb_reachable=smb_reachable,
        com_meters_ok=com_meters_ok,
        com_meters_failed=com_meters_failed,
        kind=resolved,
    )


def get_remote_memory_stats(ip_address: str) -> dict[str, Any] | None:
    """
    Query OS RAM (KB) and ``OneHand.exe`` working set (bytes → MB) via remote WMIC.

    Returns ``{"total_mb", "free_mb", "used_mb", "used_pct", "process_mb"}`` or ``None``
    if the OS query fails. ``process_mb`` is ``None`` when the game process is not found.
    """
    ip = (ip_address or "").strip()
    if not ip:
        return None

    if os.name != "nt":
        return None

    if not is_valid_remote_cabinet_ip(ip):
        # A stray IP would otherwise cost a 45 s WMIC timeout per incident row.
        logger.debug("remote memory query skipped for non-fleet host %r", ip)
        return None

    cmd = [
        "wmic",
        f"/node:{ip}",
        "OS",
        "get",
        "FreePhysicalMemory,TotalVisibleMemorySize",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 45,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC memory query failed for %s: %s", ip, e)
        return None

    if r.returncode != 0:
        logger.debug(
            "WMIC non-zero exit for %s: %s %s",
            ip,
            r.stderr,
            r.stdout,
        )
        return None

    vals = _parse_wmic_value_block(r.stdout or "")
    free_kb = vals.get("FreePhysicalMemory")
    total_kb = vals.get("TotalVisibleMemorySize")
    if free_kb is None or total_kb is None or total_kb <= 0:
        return None

    total_mb = total_kb / 1024.0
    free_mb = free_kb / 1024.0
    used_mb = total_mb - free_mb
    if used_mb < 0:
        used_mb = 0.0
    used_pct = (used_mb / total_mb) * 100.0 if total_mb > 0 else 0.0

    process_mb: float | None = _fetch_onehand_process_mb(ip)

    return {
        "total_mb": round(total_mb, 2),
        "free_mb": round(free_mb, 2),
        "used_mb": round(used_mb, 2),
        "used_pct": round(used_pct, 2),
        "process_mb": process_mb,
    }
