"""
Remote physical memory stats via WMIC (``/node``) for correlation with incidents.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_LAB_USER = r"GOLD-CLUB\test"
_LAB_PASS = "test"

_WMIC_LINE_RE = re.compile(r"^([A-Za-z]+)=(.*)$")
_INVALID_REMOTE_CABINET_IPS: frozenset[str] = frozenset(
    {"0.0.0.0", "127.0.0.1", "255.255.255.255"}
)


def is_valid_remote_cabinet_ip(ip_address: str) -> bool:
    """False for blank/loopback/broadcast — not valid WMIC/PsExec cabinet targets."""
    ip = (ip_address or "").strip()
    if not ip or ip in _INVALID_REMOTE_CABINET_IPS:
        return False
    return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip))


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


def _wmic_onehand_process_output(ip: str) -> tuple[int, str]:
    """Query remote ``OneHand.exe`` process ids via WMIC."""
    cmd = [
        "wmic",
        f"/node:{ip}",
        "process",
        "where",
        "name='OneHand.exe'",
        "get",
        "ProcessId",
        "/Value",
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 30,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("WMIC OneHand presence query failed for %s: %s", ip, e)
        return -1, ""
    return int(r.returncode), r.stdout or ""


@dataclass(frozen=True, slots=True)
class OneHandStatus:
    running: bool | None
    smb_reachable: bool


def _ensure_lab_smb_credential(ip: str) -> None:
    """Idempotent cmdkey mapping for lab cabinet admin share (see LabAccess.ps1)."""
    if os.name != "nt":
        return
    host = (ip or "").strip()
    if not host:
        return
    run_kw: dict = {"capture_output": True, "text": True, "timeout": 10}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["cmdkey", f"/add:{host}", "/user:" + _LAB_USER, "/pass:" + _LAB_PASS],
            **run_kw,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("cmdkey lab credential for %s failed: %s", host, e)


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
    _ensure_lab_smb_credential(ip)
    from pathlib import Path

    log_root = Path(rf"\\{ip}\c$\Goldclub\var\log")
    try:
        return log_root.is_dir()
    except OSError:
        return False


def _onehand_via_wmic(ip: str) -> bool | None:
    code, out = _wmic_onehand_process_output(ip)
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


def _onehand_via_psexec(ip: str) -> bool | None:
    from automation.remote_exec import psexec_run, resolve_psexec_path

    if not resolve_psexec_path():
        return None
    ps_cmd = (
        "if (Get-Process -Name OneHand -ErrorAction SilentlyContinue) "
        "{ 'RUNNING' } else { 'STOPPED' }"
    )
    try:
        result = psexec_run(
            ip=ip,
            remote_argv=["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            username=_LAB_USER,
            password=_LAB_PASS,
            as_system=True,
            timeout=90,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        logger.debug("PsExec OneHand presence query failed for %s: %s", ip, e)
        return None
    blob = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "RUNNING" in blob:
        return True
    if "STOPPED" in blob or result.returncode == 0:
        return False
    return None


def _onehand_via_local_process() -> bool:
    """True when OneHand.exe is running on this Windows machine (USB/on-cabinet runs)."""
    if os.name != "nt":
        return False
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 8,
        "check": False,
    }
    run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OneHand.exe", "/NH"],
            **run_kw,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "onehand.exe" in (proc.stdout or "").lower()


def check_onehand_status_local() -> OneHandStatus:
    """Local process probe for Investigator running on the EGM or from USB."""
    return OneHandStatus(running=_onehand_via_local_process(), smb_reachable=True)


def check_onehand_status(ip_address: str) -> OneHandStatus | None:
    """Probe cabinet reachability, then OneHand via WMIC with PsExec fallback."""
    ip = (ip_address or "").strip()
    if not ip or os.name != "nt":
        return None
    if not is_valid_remote_cabinet_ip(ip):
        return check_onehand_status_local()
    smb = cabinet_smb_reachable(ip)
    if not smb:
        return OneHandStatus(running=None, smb_reachable=False)
    running = _onehand_via_wmic(ip)
    if running is None:
        running = _onehand_via_psexec(ip)
    return OneHandStatus(running=running, smb_reachable=True)


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


def onehand_warning_text(
    ip_address: str,
    *,
    running: bool | None,
    smb_reachable: bool | None = None,
    com_meters_ok: bool = False,
) -> str:
    """Human-readable warning for UI when OneHand is down or not verified."""
    if com_meters_ok:
        return ""
    ip = (ip_address or "").strip()
    if ip == "local":
        label = "this EGM"
    elif ip:
        label = ip
    else:
        label = "the cabinet"
    if running is True:
        return ""
    if running is False:
        return (
            f"<b style='color:#b45309;'>Warning:</b> "
            f"<code>OneHand.exe</code> is <b>not running</b> on <b>{label}</b>. "
            f"The SAS host link often returns no RX until the game client is started "
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
    return (
        f"<b style='color:#92400e;'>Warning:</b> "
        f"Could not verify <code>OneHand.exe</code> on <b>{label}</b> "
        f"(remote WMIC/PsExec query failed). "
        f"If <b>Get Meters</b> returned SAS data, the link is fine — ignore this banner."
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
