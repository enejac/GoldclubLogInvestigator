"""
Resolve Goldclub log / state / theme paths for UNC, on-cabinet local, and USB exports.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from config import DEFAULT_LOCAL_LOG_ROOT, DEFAULT_REMOTE_IP, format_unc_log_root

_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
# Octet-validated so a nonsense address (999.1.2.3) is not mistaken for a cabinet
# and sent into UNC probing.
_RE_IPV4 = re.compile(rf"(?:[\\/]+)?({_IPV4_OCTET}(?:\.{_IPV4_OCTET}){{3}})(?![\d.])")
_RE_USB_LOG_FOLDER = re.compile(r"^log_\d{2}_\d{2}_\d{4}$", re.IGNORECASE)

LOCAL_GOLDCLUB_ROOT = Path(r"C:\Goldclub")
LOCAL_LOG_ROOT = Path(DEFAULT_LOCAL_LOG_ROOT)
LOCAL_STATE_GCM = (
    LOCAL_GOLDCLUB_ROOT / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger"
)
LOCAL_THEMES_ROOT = LOCAL_GOLDCLUB_ROOT / "slot" / "themes"

_GCM_REL = Path("GoldClub.Aurum.Services") / "GCMessenger"
_GCM_REL_ALT = Path("goldclub.aurum.services") / "GCMessenger"
_LOG_SUBSYSTEM_MARKERS = frozenset(
    {
        "slotlog",
        "goldclub.aurum.services",
        "goldclub.logging.logdaemon",
        "onehand",
        "ruleta",
        "godot",
        "roulette",
    }
)


class GoldclubLayoutKind(str, Enum):
    UNC = "unc"
    LOCAL_CABINET = "local_cabinet"
    USB_EXPORT = "usb_export"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class GoldclubLayout:
    scan_root: Path
    log_root: Path
    state_gcmessenger: Path | None
    themes_root: Path | None
    goldclub_root: Path | None
    kind: GoldclubLayoutKind
    cabinet_ip: str | None = None


def normalize_path_str(path_str: str) -> str:
    root_raw = (path_str or "").strip()
    if not root_raw:
        return ""
    normalized = root_raw.replace("/", "\\")
    if root_raw.startswith("//") or root_raw.startswith("\\\\"):
        normalized = "\\\\" + normalized.lstrip("\\")
    return normalized


def extract_ip_from_path(path_str: str) -> str:
    s = normalize_path_str(path_str)
    m = _RE_IPV4.search(s)
    return (m.group(1) if m else "").strip()


_ADMIN_SHARE_RE = re.compile(r"^([A-Za-z])\$$")


def local_filesystem_path_for_scan_root(scan_root: str) -> str:
    """
    Rewrite a UNC admin-share path to *this* machine as a local drive path.

    ``\\\\127.0.0.1\\c$\\Goldclub\\var\\log`` becomes ``C:\\Goldclub\\var\\log``
    when Investigator is running on that host. Meter XML then reads from the
    local NTFS stack instead of looping through SMB - near-instant on the EGM.

    Remote cabinet UNC paths and already-local paths are returned unchanged
    (normalized). Does not require the rewritten path to exist.
    """
    normalized = normalize_path_str(scan_root)
    if not normalized.startswith("\\\\"):
        return normalized

    from network.scanner_utils import unc_host_of

    host = unc_host_of(normalized)
    if not host:
        return normalized

    try:
        from network.app_runtime import is_this_host
    except Exception:  # noqa: BLE001 -- keep path helpers import-safe
        return normalized
    if not is_this_host(host):
        return normalized

    # \\host\c$\Goldclub\var\log - or \\?\UNC\host\c$\...
    parts = [p for p in normalized[2:].split("\\") if p != ""]
    if parts and parts[0] in ("?", ".") and len(parts) > 2 and parts[1].upper() == "UNC":
        parts = parts[2:]
    if len(parts) < 2:
        return normalized
    # parts[0] is the host; parts[1] must be an admin share (c$, d$, g$, ...).
    if parts[0].casefold() != host.casefold():
        return normalized
    m = _ADMIN_SHARE_RE.match(parts[1])
    if not m:
        return normalized
    drive = m.group(1).upper()
    rest = parts[2:]
    if rest:
        return f"{drive}:\\" + "\\".join(rest)
    return f"{drive}:\\"

def path_is_local_filesystem(path_str: str) -> bool:
    """True when *path_str* reads from a local drive (not a remote UNC share)."""
    local = local_filesystem_path_for_scan_root(path_str)
    return bool(local) and not local.startswith("\\\\")


_UNC_HOST_PROBE_TTL_SEC = 5.0
_unc_host_probe_cache: dict[str, tuple[float, bool]] = {}


def _unc_host_answering(path_str: str) -> bool:
    """
    ``True`` for local paths and for UNC hosts whose SMB port answers.

    Path resolution walks a dozen candidate roots; on an unreachable cabinet each
    ``is_dir()`` blocks 20-60 s, which is why opening SAS Verify against a dead
    share used to freeze the window. One bounded TCP/445 probe per host (cached
    for a few seconds, since a resolve makes many calls in a row) turns that into
    an immediate answer.
    """
    from network.scanner_utils import is_smb_alive, unc_host_of

    host = unc_host_of(str(path_str))
    if not host:
        return True
    try:
        from network.app_runtime import is_this_host

        if is_this_host(host):
            return True
    except Exception:  # noqa: BLE001 -- probe must stay soft
        pass
    now = time.monotonic()
    cached = _unc_host_probe_cache.get(host)
    if cached is not None and (now - cached[0]) < _UNC_HOST_PROBE_TTL_SEC:
        return cached[1]
    alive = is_smb_alive(host, timeout=1.5)
    _unc_host_probe_cache[host] = (now, alive)
    return alive


def _path_exists_dir(p: Path) -> bool:
    if not _unc_host_answering(p):
        return False
    try:
        return p.is_dir()
    except OSError:
        return False


def unc_share_scan_root_reachable(scan_root: str, *, smb_timeout: float = 1.5) -> bool:
    """True when a UNC scan root's host answers SMB and the path is a directory.

    Used by SAS Verify share-recovery probes so a dead cabinet does not block
    for 20–60 s on bare ``Path.is_dir()`` while COM/game recovery waits too.
    """
    normalized = normalize_path_str(scan_root)
    if not normalized.startswith("\\\\"):
        return False
    path = Path(normalized)
    host = extract_ip_from_path(normalized)
    if host:
        from network.scanner_utils import is_smb_alive

        try:
            from network.app_runtime import is_this_host

            if is_this_host(host):
                pass
            elif not is_smb_alive(host, timeout=smb_timeout):
                return False
        except Exception:  # noqa: BLE001
            if not is_smb_alive(host, timeout=smb_timeout):
                return False
    elif not _unc_host_answering(path):
        return False
    return _path_exists_dir(path)


def should_arm_share_recovery_after_empty_load(scan_root: str) -> bool:
    """Arm the share watcher only when the UNC path looks unreachable.

    An empty Machine load on a reachable share with a resolved state tree is
    usually RAM Clear / missing XML — not an access failure — so mtime retry
    and empty-machine retry should handle it instead of looping share recovery.
    """
    normalized = normalize_path_str(scan_root)
    if not normalized.startswith("\\\\"):
        return False
    if not unc_share_scan_root_reachable(normalized):
        return True
    layout = resolve_goldclub_layout(normalized)
    if layout is not None and layout.state_gcmessenger is not None:
        return False
    return True


def _path_exists_file(p: Path) -> bool:
    if not _unc_host_answering(p):
        return False
    try:
        return p.is_file()
    except OSError:
        return False


def _resolve_var_log_base(path: Path) -> Path | None:
    cur: Path | None = path
    for _ in range(14):
        if cur is None:
            break
        parts = [p.lower() for p in cur.parts]
        if len(parts) >= 2 and parts[-1] == "log" and parts[-2] == "var":
            return cur
        # Bare …\var (after prefer_var_root_when_meters_under_state) — use …\var\log.
        if parts and parts[-1] == "var":
            log_child = cur / "log"
            if _path_exists_dir(log_child):
                return log_child
        parent = cur.parent
        cur = parent if parent != cur else None
    if _looks_like_log_tree(path):
        return path
    return None


def _resolve_meter_state_base(path: Path) -> Path | None:
    """
    If *path* is a meter-state root (or a folder under one), return that root.

    Covers roulette ``…\\ruleta\\var`` and slot ``…\\GCMessenger`` (and their
    ``gm2au`` / ``SASControler*`` children). Used when the scan root is the
    meters folder itself rather than ``…\\var\\log``.
    """
    cur: Path | None = path
    for _ in range(10):
        if cur is None:
            break
        name = cur.name.lower()
        if name == "var" and cur.parent.name.lower() == "ruleta":
            return cur
        if name == "gcmessenger":
            return cur
        if name in ("gm2au", "sascontroler1", "sascontroller1"):
            return cur.parent
        parent = cur.parent
        cur = parent if parent != cur else None
    return None


def _looks_like_log_tree(path: Path) -> bool:
    if not _path_exists_dir(path):
        return False
    try:
        for child in path.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower()
            if name in _LOG_SUBSYSTEM_MARKERS or "slotlog" in name or "onehand" in name:
                return True
    except OSError:
        return False
    return False


def _is_usb_export_folder(path: Path) -> bool:
    if _RE_USB_LOG_FOLDER.match(path.name or ""):
        return _looks_like_log_tree(path)
    return False


def _gcmessenger_from_goldclub_root(goldclub_root: Path) -> Path:
    primary = goldclub_root / "var" / "state" / _GCM_REL
    if _path_exists_dir(primary):
        return primary
    alt = goldclub_root / "var" / "state" / _GCM_REL_ALT
    if _path_exists_dir(alt):
        return alt
    return primary


def _ruleta_var_meter_root(goldclub_root: Path) -> Path:
    """Roulette live meters: ``…\\ruleta\\var\\{gm2au,SASControler1}\\DeviceManagerData.xml_*``."""
    return goldclub_root / "ruleta" / "var"


def _meter_state_roots_for_goldclub(goldclub_root: Path) -> tuple[Path, ...]:
    """
    Candidate parents of ``gm2au`` / ``SASControler1`` meter XML.

    Slot Aurum uses ``var\\state\\…\\GCMessenger``. Roulette keeps the same XML
    schema under ``ruleta\\var`` (not under GCMessenger). Prefer ruleta\\var when
    it contains DeviceManagerData so hybrid cabinets resolve correctly.
    """
    return (
        _ruleta_var_meter_root(goldclub_root),
        goldclub_root / "var" / "state" / _GCM_REL,
        goldclub_root / "var" / "state" / _GCM_REL_ALT,
    )


def _gcm_rel_variants(base: Path) -> tuple[Path, Path]:
    return (base / _GCM_REL, base / _GCM_REL_ALT)


def _first_existing_dir(candidates: tuple[Path, ...]) -> Path | None:
    for p in candidates:
        if _path_exists_dir(p):
            return p
    return None


def _state_has_device_manager_data(gcm: Path) -> bool:
    """True when GCMessenger has at least one DeviceManagerData.xml_* meter file."""
    for folder in ("SASControler1", "gm2au", "SASController1"):
        for i in range(1, 5):
            if _path_exists_file(gcm / folder / f"DeviceManagerData.xml_{i}"):
                return True
    return False


def prefer_var_root_when_meters_under_state(scan_root: str) -> str:
    """Shrink ``…\\var\\log`` → ``…\\var`` when sibling ``…\\var\\state`` holds meters.

    Startup and help text prefer the ``var`` tree so the Scan root field matches
    where DeviceManagerData actually lives. A persisted ``…\\var\\log`` hint still
    loads via layout sibling resolution, but the status line then reports
    "not found under …\\var\\log" after a brief empty window (e.g. post RAM Clear).
    Remapping the displayed root to ``…\\var`` keeps messaging accurate.
    """
    normalized = normalize_path_str(scan_root)
    if not normalized:
        return (scan_root or "").strip()
    path = Path(normalized)
    parts = [p.lower() for p in path.parts]
    if len(parts) < 2 or parts[-1] != "log" or parts[-2] != "var":
        return normalized
    var_root = path.parent
    state_parent = var_root / "state"
    for gcm in _gcm_rel_variants(state_parent):
        if _state_has_device_manager_data(gcm) or _path_exists_dir(gcm):
            return str(var_root)
    return normalized


def _pick_state_gcmessenger(candidates: tuple[Path, ...]) -> Path | None:
    """Prefer a GCMessenger folder that actually contains meter XML."""
    existing = [p for p in candidates if _path_exists_dir(p)]
    if not existing:
        return None
    with_meters = [p for p in existing if _state_has_device_manager_data(p)]
    return with_meters[0] if with_meters else existing[0]


def _drive_root_of(path: Path) -> Path | None:
    try:
        anchor = path.anchor
        if not anchor:
            return None
        return Path(anchor)
    except Exception:
        return None


def _state_gcm_candidates_for_log_root(log_root: Path) -> tuple[Path, ...]:
    """Candidate meter-state roots near a log tree (slot GCMessenger + roulette ruleta\\var)."""
    export = log_root
    parent = log_root.parent
    grand = parent.parent if parent != log_root else parent
    drive = _drive_root_of(log_root)

    cands: list[Path] = []
    seen: set[str] = set()

    def add_root(p: Path) -> None:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            cands.append(p)

    def add_gcm_under(state_parent: Path) -> None:
        for p in _gcm_rel_variants(state_parent):
            add_root(p)

    def add_goldclub_meter_roots(goldclub: Path) -> None:
        for p in _meter_state_roots_for_goldclub(goldclub):
            add_root(p)

    # Adjacent to the log tree (USB export / Alegro G:\var\… layout)
    add_gcm_under(export / "state")
    add_gcm_under(export / "var" / "state")
    add_gcm_under(parent / "state")
    add_gcm_under(grand / "state")
    # …\var\log → …\var\state and sibling …\Goldclub\var\state / ruleta\var
    if export.name.lower() == "log" and parent.name.lower() == "var":
        add_gcm_under(parent / "state")
        install = grand
        add_goldclub_meter_roots(install / "Goldclub")
        add_goldclub_meter_roots(install / "goldclub")
        add_root(install / "ruleta" / "var")
        add_gcm_under(install / "var" / "state")
    # …\var\log\ruleta → install may be goldclub parent
    if (
        export.name.lower() == "ruleta"
        and parent.name.lower() == "log"
        and grand.name.lower() == "var"
    ):
        install = grand.parent
        add_goldclub_meter_roots(install)
        add_root(install / "ruleta" / "var")

    parts_l = [p.lower() for p in log_root.parts]
    under_goldclub = "goldclub" in parts_l
    usb_export = bool(_RE_USB_LOG_FOLDER.match(log_root.name or "")) or _is_usb_export_folder(
        log_root
    )

    # Same-drive Goldclub / ruleta (hybrid: game on G:, platform on C: or G:\Goldclub).
    # Skip when the scan root is already under a Goldclub/USB tree — otherwise a
    # temp test path on C: (or a USB export) steals meters from C:\Goldclub.
    if drive is not None and not under_goldclub and not usb_export:
        add_goldclub_meter_roots(drive / "Goldclub")
        add_goldclub_meter_roots(drive / "goldclub")
        add_root(drive / "ruleta" / "var")
        add_gcm_under(drive / "var" / "state")

    # C:\Goldclub fallback is only for hybrid layouts (game on G:, platform on
    # C:) where the scan root is NOT already inside a Goldclub or USB export
    # tree. Adding it unconditionally made Auto-fetch / USB resolves steal
    # meters from a leftover local install whenever the scan-local GCMessenger
    # folder existed but had no DeviceManagerData yet.
    if not under_goldclub and not usb_export:
        add_goldclub_meter_roots(LOCAL_GOLDCLUB_ROOT)

    return tuple(cands)


def _themes_candidates_for_log_root(log_root: Path) -> tuple[Path, ...]:
    export = log_root
    parent = log_root.parent
    return (
        export,
        export / "themes",
        export / "Themes",
        parent / "themes",
        parent / "Themes",
        LOCAL_THEMES_ROOT,
    )


def _resolve_themes_root(log_root: Path) -> Path | None:
    for base in _themes_candidates_for_log_root(log_root):
        if _path_exists_file(base / "mgconfig.xml"):
            return base
    return _first_existing_dir(_themes_candidates_for_log_root(log_root))


def resolve_goldclub_layout(scan_root: str) -> GoldclubLayout | None:
    normalized = normalize_path_str(scan_root)
    if not normalized:
        return None
    path = Path(normalized)

    ip = extract_ip_from_path(normalized)
    if ip and normalized.startswith("\\\\"):
        log_root = _resolve_var_log_base(path) or path
        # Resolve the share once, then only probe meter roots under it.
        # Do NOT call _state_gcm_candidates_for_log_root here — that list
        # includes C:\Goldclub and other local-drive guesses, each of which
        # is a slow SMB miss when the scan root is already \\ip\c$\….
        goldclub_picked = Path(rf"\\{ip}\c$\Goldclub")
        for gc_name in ("Goldclub", "goldclub"):
            cand = Path(rf"\\{ip}\c$\{gc_name}")
            if _path_exists_dir(cand):
                goldclub_picked = cand
                break
        unc_candidates = _meter_state_roots_for_goldclub(goldclub_picked)
        # Adjacent …\var\state next to a …\var\log scan root (same share only).
        if log_root.name.lower() == "log" and log_root.parent.name.lower() == "var":
            unc_candidates = (
                *unc_candidates,
                *_gcm_rel_variants(log_root.parent / "state"),
            )
        state_gcm = _pick_state_gcmessenger(tuple(unc_candidates))
        themes = goldclub_picked / "slot" / "themes"
        return GoldclubLayout(
            scan_root=path,
            log_root=log_root,
            state_gcmessenger=state_gcm,
            themes_root=themes,
            goldclub_root=goldclub_picked,
            kind=GoldclubLayoutKind.UNC,
            cabinet_ip=ip,
        )

    log_root = _resolve_var_log_base(path)
    if log_root is None and _is_usb_export_folder(path):
        log_root = path
    meter_only = _resolve_meter_state_base(path) if log_root is None else None
    if log_root is None and meter_only is None:
        return None

    # Meter-only roots (ruleta\var / GCMessenger) act as both log_root and state.
    effective_root = log_root if log_root is not None else meter_only
    assert effective_root is not None

    goldclub_root: Path | None = None
    parts_lower = [p.lower() for p in effective_root.parts]
    if "goldclub" in parts_lower:
        idx = parts_lower.index("goldclub")
        goldclub_root = Path(*effective_root.parts[: idx + 1])

    if goldclub_root is not None and _path_exists_dir(goldclub_root):
        state_gcm = _pick_state_gcmessenger(
            (
                *_meter_state_roots_for_goldclub(goldclub_root),
                *_state_gcm_candidates_for_log_root(effective_root),
                *((meter_only,) if meter_only is not None else ()),
            )
        )
        themes = goldclub_root / "slot" / "themes"
        kind = GoldclubLayoutKind.LOCAL_CABINET
    elif log_root is not None and _is_usb_export_folder(log_root):
        state_gcm = _pick_state_gcmessenger(_state_gcm_candidates_for_log_root(log_root))
        themes = _resolve_themes_root(log_root)
        goldclub_root = None
        kind = GoldclubLayoutKind.USB_EXPORT
    elif meter_only is not None and log_root is None:
        # Bare G:\ruleta\var (or a pasted GCMessenger path) — no Goldclub parent.
        state_gcm = meter_only
        themes = _resolve_themes_root(meter_only)
        kind = GoldclubLayoutKind.CUSTOM
    else:
        state_gcm = _pick_state_gcmessenger(
            _state_gcm_candidates_for_log_root(effective_root)
        )
        themes = _resolve_themes_root(effective_root)
        kind = GoldclubLayoutKind.CUSTOM

    return GoldclubLayout(
        scan_root=path,
        log_root=effective_root,
        state_gcmessenger=state_gcm,
        themes_root=themes,
        goldclub_root=goldclub_root,
        kind=kind,
        cabinet_ip=ip or None,
    )


def meter_state_roots_for_layout(layout: GoldclubLayout | None) -> list[Path]:
    """All candidate parents of ``gm2au`` / ``SASControler1`` for *layout*.

    Slot cabinets use GCMessenger; roulette uses ``ruleta\\var``. Hybrid installs
    may have both — callers that need per-theme perf meters should scan every root
    that exists, not only ``layout.state_gcmessenger``.
    """
    if layout is None:
        return []
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None:
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        if _path_exists_dir(path):
            roots.append(path)

    _add(layout.state_gcmessenger)
    goldclub_root = getattr(layout, "goldclub_root", None)
    if goldclub_root is not None:
        for cand in _meter_state_roots_for_goldclub(goldclub_root):
            _add(cand)
    return roots


def device_manager_data_files(state_gcmessenger: Path) -> list[Path]:
    files: list[Path] = []
    for folder in ("SASControler1", "gm2au", "SASController1"):
        for i in range(1, 5):
            files.append(state_gcmessenger / folder / f"DeviceManagerData.xml_{i}")
    return files


def layout_requires_smb(layout: GoldclubLayout | None) -> bool:
    return layout is not None and layout.kind == GoldclubLayoutKind.UNC


_PRIORITY_GAME_DRIVES: tuple[str, ...] = (
    "G:",
    "C:",
    "D:",
    "E:",
    "F:",
    "H:",
    "I:",
    "J:",
    "K:",
    "L:",
    "M:",
    "N:",
    "O:",
    "P:",
    "Q:",
    "R:",
    "S:",
    "T:",
    "U:",
    "V:",
    "W:",
    "X:",
    "Y:",
    "Z:",
)


@dataclass(frozen=True, slots=True)
class StartupScanDiscovery:
    """Auto-detected scan root at application startup."""

    mode: str  # "local" | "remote"
    scan_root: str
    game_kind: str | None = None  # "slot" | "roulette" | "export"
    remote_ip: str | None = None


def _goldclub_root_from_path(path: Path) -> Path | None:
    parts = [p.lower() for p in path.parts]
    if "goldclub" not in parts:
        return None
    idx = parts.index("goldclub")
    return Path(*path.parts[: idx + 1])


def _find_slot_install_root() -> Path | None:
    """Return the slot game folder when OneHand.exe (or game-start.exe) is present."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(raw: Path) -> None:
        key = str(raw).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(raw)

    for drive in _PRIORITY_GAME_DRIVES:
        add(Path(f"{drive}\\Goldclub\\slot"))
    add(LOCAL_GOLDCLUB_ROOT / "slot")

    markers = ("OneHand.exe", "bin/OneHand.exe", "game-start.exe")
    for root in candidates:
        for rel in markers:
            if _path_exists_file(root / rel):
                return root
    return None


def _find_roulette_install_root() -> Path | None:
    """Return the roulette USB/cabinet root when ruleta/Ruleta.exe is present."""
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(raw: Path) -> None:
        key = str(raw).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(raw)

    for drive in _PRIORITY_GAME_DRIVES:
        add(Path(f"{drive}\\"))
        add(Path(f"{drive}\\Goldclub"))

    for root in candidates:
        ruleta = root / "ruleta"
        if not _path_exists_dir(ruleta):
            continue
        for name in ("Ruleta.exe", "ruleta.exe"):
            if _path_exists_file(ruleta / name):
                return root
        try:
            for path in ruleta.glob("*.exe"):
                if path.name.lower() == "ruleta.exe":
                    return root
        except OSError:
            continue
    return None


def _log_root_for_slot_install(install_root: Path) -> Path | None:
    goldclub = _goldclub_root_from_path(install_root)
    candidates: list[Path] = []
    if goldclub is not None:
        candidates.append(goldclub / "var" / "log")
    parent = install_root.parent
    if parent != install_root:
        candidates.append(parent / "var" / "log")
    # Never auto-pick G:\ here — that drive is optional and needs a user prompt.
    candidates.append(LOCAL_LOG_ROOT)
    return _first_existing_dir(tuple(candidates))


def _log_root_for_roulette_install(install_root: Path) -> Path | None:
    """Prefer ``…\\var\\log`` so sibling folders are scanned.

    Roulette errors often live under ``ruleta Roulette``, ``godot``, ``godot1``,
    Aurum, etc. Narrowing to ``var\\log\\ruleta`` alone misses those (e.g.
    ``ERR: number on screen``). Fall back to ``…\\var\\log\\ruleta`` only when
    the parent log tree is missing.
    """
    goldclub = _goldclub_root_from_path(install_root)
    candidates: list[Path] = []
    if goldclub is not None:
        candidates.extend(
            (
                goldclub / "var" / "log",
                goldclub / "var" / "log" / "ruleta",
            )
        )
    candidates.extend(
        (
            install_root / "var" / "log",
            install_root / "var" / "log" / "ruleta",
            Path(r"D:\var\log"),
            Path(r"D:\var\log\ruleta"),
            Path(r"C:\Goldclub\var\log"),
            LOCAL_LOG_ROOT,
            LOCAL_LOG_ROOT / "ruleta",
        )
    )
    # Drop any G:\ candidates that came from an install_root on the game drive.
    candidates = [p for p in candidates if not is_game_image_drive_path(p)]
    return _first_existing_dir(tuple(candidates))


def discover_startup_scan_target(
    *,
    remote_ip: str | None = None,
    exe_dir: Path | None = None,
) -> StartupScanDiscovery:
    """
    Pick a scan root when the app starts.

    1. USB log export ``log_DD_MM_YYYY`` next to the exe (if present)
    2. Local slot install (``OneHand.exe`` under ``…\\Goldclub\\slot``) → ``…\\var\\log``
       — never the ``G:\\`` game-image drive (that requires an explicit user prompt)
    3. Local roulette install (``ruleta\\Ruleta.exe``) → ``…\\var\\log``
       (full tree including ``ruleta Roulette`` / ``godot*``; not only ``…\\var\\log\\ruleta``)
       — same G:\\ exclusion as slot
    4. Remote UNC fallback (``\\\\<ip>\\c$\\Goldclub\\var\\log``)
    """
    portable = discover_portable_scan_roots(exe_dir=exe_dir)
    for raw in portable:
        folder = Path(raw)
        if _RE_USB_LOG_FOLDER.match(folder.name or ""):
            return StartupScanDiscovery(
                mode="local",
                scan_root=raw,
                game_kind="export",
            )

    slot_install = _find_slot_install_root()
    if slot_install is not None and not is_game_image_drive_path(slot_install):
        log_root = _log_root_for_slot_install(slot_install)
        if log_root is not None and not is_game_image_drive_path(log_root):
            return StartupScanDiscovery(
                mode="local",
                scan_root=str(log_root),
                game_kind="slot",
            )

    roulette_install = _find_roulette_install_root()
    if roulette_install is not None and not is_game_image_drive_path(roulette_install):
        log_root = _log_root_for_roulette_install(roulette_install)
        if log_root is not None and not is_game_image_drive_path(log_root):
            return StartupScanDiscovery(
                mode="local",
                scan_root=str(log_root),
                game_kind="roulette",
            )

    ip = (remote_ip or DEFAULT_REMOTE_IP).strip() or DEFAULT_REMOTE_IP
    refined = _refine_log_scan_root_from_install(
        hint=format_unc_log_root(ip),
        remote_ip=ip,
    )
    if refined is not None and not is_game_image_drive_path(refined.scan_root):
        return refined
    return StartupScanDiscovery(
        mode="remote",
        scan_root=format_unc_log_root(ip),
        game_kind=None,
        remote_ip=ip,
    )


def detect_game_kind_from_scan_root(scan_root: str | None) -> str | None:
    """Probe Goldclub install markers near *scan_root* (Ruleta.exe vs OneHand).

    Used when the path is a shared ``…\\var\\log`` that does not contain ``ruleta``
    in the string — same Ruleta detection AFT uses. Returns None when unknown.
    """
    install = _install_root_from_scan_hint(scan_root or "")
    if install is None:
        return None
    return _detect_game_kind_at_install_root(install)


def _path_is_generic_var_log(path_str: str) -> bool:
    """True when *path_str* is ``…/var/log`` (not already ``…/var/log/ruleta``)."""
    p = Path(normalize_path_str(path_str))
    return p.name.lower() == "log"


def _install_root_from_scan_hint(scan_root: str) -> Path | None:
    """Goldclub or roulette USB drive root inferred from a log scan path."""
    normalized = normalize_path_str(scan_root)
    if not normalized:
        return None

    layout = resolve_goldclub_layout(normalized)
    if layout is not None:
        if layout.goldclub_root is not None and _path_exists_dir(layout.goldclub_root):
            return layout.goldclub_root
        if layout.cabinet_ip:
            unc = Path(rf"\\{layout.cabinet_ip}\c$\Goldclub")
            if _path_exists_dir(unc):
                return unc

    path = Path(normalized)
    goldclub = _goldclub_root_from_path(path)
    if goldclub is not None and _path_exists_dir(goldclub):
        return goldclub

    cur: Path | None = path
    for _ in range(8):
        if cur is None:
            break
        if _path_exists_dir(cur / "ruleta"):
            return cur
        nxt = cur.parent
        cur = nxt if nxt != cur else None
    return None


def _detect_game_kind_at_install_root(install_root: Path) -> str | None:
    """Detect slot vs roulette from install markers (no Path.glob — UNC-safe)."""
    # Prefer Ruleta when Ruleta.exe is present — roulette images may still ship a slot/ tree.
    ruleta = install_root / "ruleta"
    if _path_exists_dir(ruleta):
        for name in ("Ruleta.exe", "ruleta.exe"):
            if _path_exists_file(ruleta / name):
                return "roulette"
    slot_dir = install_root / "slot"
    if _path_exists_dir(slot_dir):
        for rel in ("OneHand.exe", "game-start.exe", "bin/OneHand.exe"):
            if _path_exists_file(slot_dir / Path(rel)):
                return "slot"
    return None


def _refine_log_scan_root_from_install(
    *,
    hint: str,
    remote_ip: str | None,
) -> StartupScanDiscovery | None:
    """Pick log root from OneHand / Ruleta install markers.

    Roulette → ``…\\var\\log`` (full tree). Slot → ``…\\var\\log``.
    An explicit ``…\\var\\log\\ruleta`` hint is kept as roulette when that folder exists.
    """
    hint_norm = normalize_path_str(hint)
    if not hint_norm:
        return None

    ip = extract_ip_from_path(hint_norm) or ((remote_ip or "").strip() or None)
    hint_path = Path(hint_norm)
    if hint_path.name.lower() == "ruleta" and _path_exists_dir(hint_path):
        mode = "remote" if hint_norm.startswith("\\\\") else "local"
        return StartupScanDiscovery(
            mode=mode,
            scan_root=hint_norm,
            game_kind="roulette",
            remote_ip=ip,
        )

    install_root = _install_root_from_scan_hint(hint_norm)
    if install_root is None and ip:
        unc = Path(rf"\\{ip}\c$\Goldclub")
        if _path_exists_dir(unc):
            install_root = unc
    if install_root is None:
        return None

    kind = _detect_game_kind_at_install_root(install_root)
    if kind == "slot":
        slot_root = install_root / "slot"
        log_root = _log_root_for_slot_install(
            slot_root if _path_exists_dir(slot_root) else install_root
        )
    elif kind == "roulette":
        log_root = _log_root_for_roulette_install(install_root)
    else:
        return None

    if log_root is None:
        return None

    mode = "remote" if hint_norm.startswith("\\\\") else "local"
    return StartupScanDiscovery(
        mode=mode,
        scan_root=str(log_root),
        game_kind=kind,
        remote_ip=ip,
    )


def portable_app_dir() -> Path:
    """Directory containing LogInvestigator.exe (USB stick root when run portably)."""
    return Path(sys.executable).resolve().parent


def is_usb_log_export_path(path: str | Path | None) -> bool:
    """True for portable log export folders ``log_DD_MM_YYYY``."""
    normalized = normalize_path_str(str(path or ""))
    if not normalized:
        return False
    return bool(_RE_USB_LOG_FOLDER.match(Path(normalized).name or ""))


def resolve_log_scan_root(
    hint: str | None = None,
    *,
    remote_ip: str | None = None,
    exe_dir: Path | None = None,
) -> StartupScanDiscovery:
    """
    Resolve the log scan folder for SAS verification and log scanning.

    Slot cabinets use ``…\\var\\log`` (SlotLog and Aurum subfolders).
    Roulette cabinets use ``…\\var\\log`` (full tree: ``ruleta``, ``ruleta Roulette``,
    ``godot*``, Aurum, …). An explicit ``…\\var\\log\\ruleta`` hint is preserved.

    Reuses :func:`discover_startup_scan_target` for local/USB installs and
    refines generic ``…\\var\\log`` hints (including UNC) using OneHand vs
    Ruleta markers on the cabinet share.
    """
    hint_norm = normalize_path_str(hint or "")
    ip = extract_ip_from_path(hint_norm) or ((remote_ip or "").strip() or None)

    startup = discover_startup_scan_target(
        remote_ip=remote_ip,
        exe_dir=exe_dir or portable_app_dir(),
    )
    if not hint_norm.startswith("\\\\") and startup.game_kind in (
        "slot",
        "roulette",
        "export",
    ):
        if not hint_norm or _path_is_generic_var_log(hint_norm):
            return startup

    if hint_norm:
        refined = _refine_log_scan_root_from_install(hint=hint_norm, remote_ip=ip)
        if refined is not None:
            return refined
        mode = "remote" if hint_norm.startswith("\\\\") else "local"
        return StartupScanDiscovery(
            mode=mode,
            scan_root=hint_norm,
            game_kind=None,
            remote_ip=ip,
        )
    return startup


def _layout_has_meter_state(scan_root: str, *, require_files: bool = True) -> bool:
    layout = resolve_goldclub_layout(scan_root)
    if layout is None or layout.state_gcmessenger is None:
        return False
    if _state_has_device_manager_data(layout.state_gcmessenger):
        return True
    if require_files:
        return False
    return _path_exists_dir(layout.state_gcmessenger)


def _sas_verify_fallback_roots(discovery: StartupScanDiscovery) -> list[str]:
    """Alternate log roots to try when the first pick cannot resolve meter state."""
    roots: list[str] = []
    seen: set[str] = set()

    def add(raw: str | Path | None) -> None:
        if raw is None:
            return
        s = normalize_path_str(str(raw))
        if not s:
            return
        key = s.casefold()
        if key in seen:
            return
        seen.add(key)
        roots.append(s)

    add(discovery.scan_root)
    primary = Path(normalize_path_str(discovery.scan_root))
    if primary.name.lower() == "ruleta":
        add(primary.parent)

    install = _install_root_from_scan_hint(discovery.scan_root)
    if install is not None:
        add(install / "var" / "log" / "ruleta")
        add(install / "var" / "log")
        if install.name.lower() == "goldclub":
            goldclub = install
        else:
            goldclub = _goldclub_root_from_path(install) or (install / "Goldclub")
        add(goldclub / "var" / "log" / "ruleta")
        add(goldclub / "var" / "log")

    drive = _drive_root_of(primary)
    if drive is not None:
        add(drive / "Goldclub" / "var" / "log" / "ruleta")
        add(drive / "Goldclub" / "var" / "log")
        add(drive / "var" / "log" / "ruleta")
        add(drive / "var" / "log")

    add(LOCAL_LOG_ROOT / "ruleta")
    add(LOCAL_LOG_ROOT)
    add(Path(r"C:\Goldclub\var\log\ruleta"))
    add(Path(r"C:\Goldclub\var\log"))
    # G:\ is never listed here — only offered via an explicit user permission prompt.
    return [r for r in roots if not is_game_image_drive_path(r)]


def resolve_sas_verify_scan_root(
    hint: str | None = None,
    *,
    remote_ip: str | None = None,
    exe_dir: Path | None = None,
) -> StartupScanDiscovery:
    """
    Resolve scan root for the SAS accounting dialog.

    Starts from :func:`resolve_log_scan_root`, then if cabinet meter state
    (``DeviceManagerData.xml_*`` under ``ruleta\\var`` or GCMessenger) cannot be
    resolved — common on roulette hybrid layouts where logs are on
    ``G:\\var\\log\\ruleta`` but Aurum/game state lives under ``C:\\Goldclub`` or
    ``G:\\Goldclub`` — tries alternate roots until a path with meter state is found.
    """
    discovery = resolve_log_scan_root(
        hint, remote_ip=remote_ip, exe_dir=exe_dir
    )
    if _layout_has_meter_state(discovery.scan_root, require_files=True):
        return discovery

    ip = discovery.remote_ip or extract_ip_from_path(discovery.scan_root) or (
        (remote_ip or "").strip() or None
    )
    hint_on_game_drive = is_game_image_drive_path(discovery.scan_root)
    soft_match: StartupScanDiscovery | None = None
    for candidate in _sas_verify_fallback_roots(discovery):
        if candidate == discovery.scan_root:
            continue
        # Never remap a UNC/local hint onto the G:\ game-image drive silently —
        # a share hiccup used to swap the Scan root to G:\ one second after
        # open, comparing a different machine. G:\ needs explicit permission
        # (the dialog asks); only a hint already on G:\ may stay there.
        if is_game_image_drive_path(candidate) and not hint_on_game_drive:
            continue
        if not _path_exists_dir(Path(candidate)):
            continue
        kind = discovery.game_kind
        if kind is None:
            refined = _refine_log_scan_root_from_install(hint=candidate, remote_ip=ip)
            kind = refined.game_kind if refined else None
        mode = "remote" if candidate.startswith("\\\\") else "local"
        remapped = StartupScanDiscovery(
            mode=mode,
            scan_root=candidate,
            game_kind=kind or discovery.game_kind,
            remote_ip=ip,
        )
        if _layout_has_meter_state(candidate, require_files=True):
            return remapped
        if soft_match is None and _layout_has_meter_state(
            candidate, require_files=False
        ):
            soft_match = remapped
    return soft_match or discovery


def _remote_cabinet_reachable(ip: str | None, *, timeout: float = 1.5) -> bool:
    host = (ip or "").strip()
    if not host:
        return False
    from network.scanner_utils import is_smb_alive

    if not is_smb_alive(host, timeout=timeout):
        return False
    try:
        from network.lab_access import ensure_lab_smb_credential

        ensure_lab_smb_credential(host)
    except Exception:  # noqa: BLE001 -- reachability must stay soft
        pass
    return True


def _local_d_drive_scan_candidates(exe_dir: Path | None = None) -> tuple[str, ...]:
    """Prefer D:\\ Goldclub / USB export folders for the final local fallback."""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        s = str(p)
        key = s.casefold()
        if key in seen:
            return
        if _path_exists_dir(p):
            seen.add(key)
            out.append(s)

    for rel in (
        Path(r"D:\Goldclub\var\log"),
        Path(r"D:\Goldclub\var\log\ruleta"),
        Path(r"D:\var\log"),
        Path(r"D:\var\log\ruleta"),
    ):
        add(rel)

    base = exe_dir or portable_app_dir()
    # Prefer D: portable roots when the exe itself is on D:.
    if str(base).upper().startswith("D:"):
        for raw in discover_portable_scan_roots(exe_dir=base):
            add(Path(raw))
    else:
        for raw in discover_portable_scan_roots(exe_dir=base):
            if str(raw).upper().startswith("D:"):
                add(Path(raw))
        for raw in discover_portable_scan_roots(exe_dir=base):
            add(Path(raw))
    return tuple(out)


def _sas_host_com_present() -> bool:
    """True when Windows lists a plausible SAS/MUX serial device."""
    try:
        from network.sas_serial_meters import enumerate_serial_ports, pick_sas_com_port

        ports = enumerate_serial_ports()
        if not ports:
            return False
        for kind, on_cab in (("slot", False), ("roulette", True), ("roulette", False)):
            if pick_sas_com_port("", ports, game_kind=kind, on_cabinet=on_cab):
                return True
    except Exception:
        return False
    return False


def resolve_sas_verify_standalone_scan_root(
    hint: str | None = None,
    *,
    remote_ip: str | None = None,
    exe_dir: Path | None = None,
) -> StartupScanDiscovery:
    """
    Scan-root policy for SasVerifyMeters.exe (Machine / snapshot column).

    Live SAS/MUX capture is separate (COM). Folder selection order:

    1. Explicit ``hint`` / ``--scan-root``
    2. Local Goldclub only when this process is on the EGM
    3. Reachable remote cabinet UNC (workstation + host COM -> Machine from remote)
    4. Local `D:\\` / USB export, then any discoverable local root, when remote is down
    """
    hint_norm = normalize_path_str(hint or "")
    ip = ((remote_ip or "").strip() or extract_ip_from_path(hint_norm) or DEFAULT_REMOTE_IP).strip()
    base = exe_dir or portable_app_dir()

    if hint_norm:
        return resolve_sas_verify_scan_root(
            hint_norm, remote_ip=ip or None, exe_dir=base
        )

    try:
        from network.health_monitor import is_running_on_local_egm

        on_egm = bool(is_running_on_local_egm())
    except Exception:
        on_egm = False

    # Host COM on a workstation must NOT pick local `G:\\` — that XML is often a
    # different image than the EGM on the other end of the SAS cable. And even on
    # the EGM itself, the G:\ game-image drive is never selected silently — the
    # dialog asks for permission first (see discover_local_game_image_scan_root).
    if on_egm:
        local = resolve_sas_verify_scan_root(None, remote_ip=None, exe_dir=base)
        if (
            local.scan_root
            and local.mode == "local"
            and not is_game_image_drive_path(local.scan_root)
        ):
            return local

    if _remote_cabinet_reachable(ip):
        remote = resolve_sas_verify_scan_root(
            format_unc_log_root(ip),
            remote_ip=ip,
            exe_dir=base,
        )
        if remote.scan_root:
            return StartupScanDiscovery(
                mode="remote",
                scan_root=remote.scan_root,
                game_kind=remote.game_kind,
                remote_ip=ip,
            )

    # Remote down: prefer `D:\\` USB, then any local discoverable root (incl. `G:\\`).
    for candidate in _local_d_drive_scan_candidates(base):
        discovered = resolve_sas_verify_scan_root(
            candidate, remote_ip=None, exe_dir=base
        )
        if discovered.scan_root:
            return StartupScanDiscovery(
                mode="local",
                scan_root=discovered.scan_root,
                game_kind=discovered.game_kind or (
                    "export" if is_usb_log_export_path(discovered.scan_root) else None
                ),
                remote_ip=None,
            )

    # Discoverable local roots on the G:\ game-image drive are never auto-picked:
    # on a workstation they are a different image than the EGM on the SAS cable,
    # and on the EGM the user must explicitly approve a local-files-only compare.
    if not _sas_host_com_present():
        local_fallback = resolve_sas_verify_scan_root(None, remote_ip=None, exe_dir=base)
        if (
            local_fallback.scan_root
            and local_fallback.mode == "local"
            and not is_game_image_drive_path(local_fallback.scan_root)
        ):
            return local_fallback

    return StartupScanDiscovery(
        mode="local",
        scan_root="",
        game_kind=None,
        remote_ip=ip,
    )


def is_game_image_drive_path(p: str | Path | None) -> bool:
    """True for paths on the ``G:\\`` game-image drive (never auto-selected)."""
    s = normalize_path_str(str(p or ""))
    return s[:2].upper() == "G:"


def discover_local_game_image_scan_root(*, exe_dir: Path | None = None) -> str:
    """
    The local ``G:\\`` scan root for an *optional* permission prompt.

    Auto-selection of G:\\ was removed on purpose — callers show this candidate in
    a Yes/No dialog instead. Returns "" when no G:\\ Goldclub/roulette root exists.
    """
    _ = exe_dir  # reserved for callers that pass portable_app_dir()
    candidates = (
        Path(r"G:\var\log\ruleta"),
        Path(r"G:\var\log"),
        Path(r"G:\Goldclub\var\log\ruleta"),
        Path(r"G:\Goldclub\var\log"),
        Path(r"G:\ruleta\var"),
    )
    for cand in candidates:
        if _path_exists_dir(cand):
            return str(cand)
    # Last resort: roulette/slot markers anywhere under G:\
    roulette = _find_roulette_install_root()
    if roulette is not None and is_game_image_drive_path(roulette):
        log_root = None
        # Inline look-up without the G:\\ filter used by normal discovery.
        goldclub = _goldclub_root_from_path(roulette)
        for p in (
            *(
                (
                    goldclub / "var" / "log",
                    goldclub / "var" / "log" / "ruleta",
                )
                if goldclub is not None
                else ()
            ),
            roulette / "var" / "log",
            roulette / "var" / "log" / "ruleta",
            Path(r"G:\var\log"),
        ):
            if _path_exists_dir(p):
                log_root = p
                break
        if log_root is not None:
            return str(log_root)
    return ""



def discover_portable_scan_roots(*, exe_dir: Path | None = None) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []

    def add(p: Path) -> None:
        s = str(p)
        if s not in seen and _path_exists_dir(p):
            seen.add(s)
            out.append(s)

    base = exe_dir or portable_app_dir()
    search_dirs: list[Path] = [base]
    logfiles = base / "_LogFiles"
    if _path_exists_dir(logfiles):
        search_dirs.append(logfiles)
    if base.parent != base:
        sibling = base.parent / "_LogFiles"
        if _path_exists_dir(sibling):
            search_dirs.append(sibling)

    usb_log_dirs: list[Path] = []
    for folder in search_dirs:
        try:
            for child in folder.iterdir():
                if child.is_dir() and _RE_USB_LOG_FOLDER.match(child.name):
                    usb_log_dirs.append(child)
        except OSError:
            continue
    for child in sorted(usb_log_dirs, key=lambda p: p.stat().st_mtime, reverse=True):
        add(child)

    add(LOCAL_LOG_ROOT)
    return tuple(out)
