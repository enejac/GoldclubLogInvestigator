"""Process-wide launch context: local EGM vs workstation.

Detected once at startup so RAM Clear, SAS Verify, and other tools default to
local operations when Investigator runs on a cabinet. Remote ops are used only
when the user explicitly targets a *different* EGM IP.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path

from network.lab_access import LAB_FLEET_IPS


@dataclass(frozen=True, slots=True)
class AppRuntimeContext:
    """Immutable snapshot of where this process is running."""

    on_local_egm: bool
    game_kind: str | None  # "slot" | "roulette" | None
    local_ips: frozenset[str]
    local_scan_root: str | None
    install_root: str | None


_cached: AppRuntimeContext | None = None


_LOCAL_IDENTITY_TTL_SEC = 60.0
_local_ips_cache: tuple[float, frozenset[str]] | None = None
_local_names_cache: tuple[float, frozenset[str]] | None = None

_LOOPBACK_ALIASES = frozenset(
    {"127.0.0.1", "localhost", ".", "::1", "0:0:0:0:0:0:0:1"}
)


def _probe_local_ipv4_addresses() -> frozenset[str]:
    found: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127."):
                found.add(addr)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Unconnected UDP: no packets leave the box, but a missing route can
            # still stall, so keep it bounded — this runs during GUI startup.
            s.settimeout(0.25)
            s.connect(("10.0.0.1", 1))
            addr = s.getsockname()[0]
            if addr and not addr.startswith("127."):
                found.add(addr)
    except OSError:
        pass
    return frozenset(found)


def local_ipv4_addresses() -> frozenset[str]:
    """IPv4 addresses assigned to this machine (best-effort, cached briefly)."""
    global _local_ips_cache
    now = time.monotonic()
    cached = _local_ips_cache
    if cached is not None and (now - cached[0]) < _LOCAL_IDENTITY_TTL_SEC:
        return cached[1]
    ips = _probe_local_ipv4_addresses()
    _local_ips_cache = (now, ips)
    return ips


def local_hostnames() -> frozenset[str]:
    """Short and fully qualified names for this machine, casefolded."""
    global _local_names_cache
    now = time.monotonic()
    cached = _local_names_cache
    if cached is not None and (now - cached[0]) < _LOCAL_IDENTITY_TTL_SEC:
        return cached[1]
    names: set[str] = set()
    for probe in (socket.gethostname, socket.getfqdn):
        try:
            raw = (probe() or "").strip().casefold()
        except OSError:
            continue
        if not raw or raw == "localhost":
            continue
        names.add(raw)
        names.add(raw.split(".", 1)[0])
    resolved = frozenset(n for n in names if n)
    _local_names_cache = (now, resolved)
    return resolved


def is_this_host(ip: str) -> bool:
    """
    True when ``ip`` names this machine.

    Accepts IPv4, loopback aliases and the machine's own hostname, because scan
    roots reach cabinets as ``\\\\GST20664\\c$\\…`` just as often as by address.
    """
    host = (ip or "").strip().strip("[]")
    if not host:
        return False
    key = host.casefold()
    if key in _LOOPBACK_ALIASES or key.startswith("127."):
        return True
    if host in local_ipv4_addresses():
        return True
    return key in local_hostnames()


def _has_local_game_binary() -> bool:
    markers = (
        Path(r"C:\\goldclub\\ruleta\\Ruleta.exe"),
        Path(r"C:\\Goldclub\\ruleta\\Ruleta.exe"),
        Path(r"C:\\goldclub\\ruleta\\ruleta.exe"),
        Path(r"C:\\Goldclub\\ruleta\\ruleta.exe"),
        Path(r"C:\\Goldclub\\slot\\OneHand.exe"),
        Path(r"C:\\goldclub\\slot\\OneHand.exe"),
    )
    for path in markers:
        try:
            if path.is_file():
                return True
        except OSError:
            continue
    return False


def _is_on_cabinet_install_root(root: Path | None) -> bool:
    """True for `C:\\Goldclub` installs — not a workstation-mounted game USB (`G:\\`)."""
    if root is None:
        return False
    key = str(root).replace("/", "\\").casefold().rstrip("\\")
    return key == r"c:\\goldclub" or key.startswith(r"c:\\goldclub\\")


def is_running_on_local_egm() -> bool:
    """True when this process is on a lab cabinet (C: install, binary, or fleet IP)."""
    return get_app_runtime().on_local_egm


def detect_app_runtime() -> AppRuntimeContext:
    """Probe the machine once for local GoldClub install / cabinet identity."""
    from network.goldclub_paths import (
        _find_roulette_install_root,
        _find_slot_install_root,
        _log_root_for_roulette_install,
        _log_root_for_slot_install,
    )

    ips = local_ipv4_addresses()
    slot = _find_slot_install_root()
    roulette = _find_roulette_install_root()

    game_kind: str | None = None
    local_scan_root: str | None = None
    install_root: Path | None = None

    # Prefer C: cabinet install over G: game-image USB for "on EGM" identity.
    cabinet_roulette = roulette if _is_on_cabinet_install_root(roulette) else None
    cabinet_slot = slot if _is_on_cabinet_install_root(slot) else None

    if cabinet_roulette is not None:
        game_kind = "roulette"
        install_root = cabinet_roulette
        log_root = _log_root_for_roulette_install(cabinet_roulette)
        if log_root is not None:
            local_scan_root = str(log_root)
    elif cabinet_slot is not None:
        game_kind = "slot"
        install_root = cabinet_slot
        log_root = _log_root_for_slot_install(cabinet_slot)
        if log_root is not None:
            local_scan_root = str(log_root)
    elif roulette is not None:
        # Workstation / USB image — useful scan root, but not "on local EGM".
        game_kind = "roulette"
        install_root = roulette
        log_root = _log_root_for_roulette_install(roulette)
        if log_root is not None:
            local_scan_root = str(log_root)
    elif slot is not None:
        game_kind = "slot"
        install_root = slot
        log_root = _log_root_for_slot_install(slot)
        if log_root is not None:
            local_scan_root = str(log_root)

    on_egm = bool(
        cabinet_roulette is not None
        or cabinet_slot is not None
        or _has_local_game_binary()
        or bool(ips & LAB_FLEET_IPS)
    )
    if game_kind is None and _has_local_game_binary():
        if any(
            Path(p).is_file()
            for p in (
                r"C:\\goldclub\\ruleta\\Ruleta.exe",
                r"C:\\Goldclub\\ruleta\\Ruleta.exe",
                r"C:\\goldclub\\ruleta\\ruleta.exe",
                r"C:\\Goldclub\\ruleta\\ruleta.exe",
            )
        ):
            game_kind = "roulette"
        else:
            game_kind = "slot"

    return AppRuntimeContext(
        on_local_egm=on_egm,
        game_kind=game_kind,
        local_ips=ips,
        local_scan_root=local_scan_root,
        install_root=str(install_root) if install_root is not None else None,
    )


def get_app_runtime() -> AppRuntimeContext:
    """Cached launch context (call :func:
efresh_app_runtime to re-probe)."""
    global _cached
    if _cached is None:
        _cached = detect_app_runtime()
    return _cached


def refresh_app_runtime() -> AppRuntimeContext:
    """Force a fresh probe (tests / rare remounts)."""
    global _cached, _local_ips_cache, _local_names_cache
    _local_ips_cache = None
    _local_names_cache = None
    _cached = detect_app_runtime()
    return _cached


def wants_remote_operations(*, ui_remote: bool, target_ip: str | None) -> bool:
    """
    True only when the UI is in Remote mode *and* the IP is a different host.

    On a local EGM, Remote + this cabinet's own IP is treated as local.
    """
    if not ui_remote:
        return False
    ip = (target_ip or "").strip()
    if not ip:
        return False
    if is_this_host(ip):
        return False
    return True


def effective_remote_ip(*, ui_remote: bool, target_ip: str | None) -> str | None:
    """IP for remote tools, or `None` when operations should stay local."""
    if wants_remote_operations(ui_remote=ui_remote, target_ip=target_ip):
        return (target_ip or "").strip() or None
    return None


def prefer_local_connection_at_launch(
    *,
    saved_mode: str,
    saved_remote_ip: str,
) -> bool:
    """
    Whether startup should force Local mode.

    On a cabinet, ignore a saved Remote setting that points at this machine
    (or has no meaningful other-EGM target). Explicit Remote to another fleet
    IP is preserved.
    """
    ctx = get_app_runtime()
    if not ctx.on_local_egm:
        return False
    mode = (saved_mode or "").strip().lower()
    if mode != "remote":
        return True
    ip = (saved_remote_ip or "").strip()
    if not ip or is_this_host(ip):
        return True
    return False
