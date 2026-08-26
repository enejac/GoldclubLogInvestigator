from __future__ import annotations

from pathlib import Path


def is_smb_alive(ip: str, timeout: float = 1.0) -> bool:
    """
    Quick pre-check to avoid long UNC timeouts when the cabinet is unreachable.

    Checks whether TCP/445 is reachable on the target host.
    """
    import socket

    host = (ip or "").strip()
    if not host:
        return False
    try:
        with socket.create_connection((host, 445), timeout=timeout):
            return True
    except Exception:
        return False


def unc_host_of(path_str: str) -> str:
    """Host component of a UNC path, or ``""`` for local paths."""
    s = (path_str or "").strip().replace("/", "\\")
    if not s.startswith("\\\\"):
        return ""
    parts = [p for p in s[2:].split("\\") if p != ""]
    if not parts:
        return ""
    head = parts[0].strip()
    if head in ("?", "."):
        if len(parts) > 2 and parts[1].strip().upper() == "UNC":
            return parts[2].strip()
        return ""
    return head


def is_dir_reachable(path_str: str, *, timeout: float = 1.5) -> bool:
    """
    ``Path.is_dir()`` that cannot hang for a minute on a dead cabinet.

    Windows retries an unreachable UNC host for 20-60 s before failing, which is
    long enough to freeze the GUI. Probing TCP/445 first turns the common
    "cabinet is off" case into a bounded check; a live host answers ``is_dir()``
    quickly.
    """
    raw = (path_str or "").strip()
    if not raw:
        return False
    host = unc_host_of(raw)
    if host and not is_smb_alive(host, timeout=timeout):
        return False
    try:
        return Path(raw).is_dir()
    except OSError:
        return False

