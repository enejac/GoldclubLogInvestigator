from __future__ import annotations


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

