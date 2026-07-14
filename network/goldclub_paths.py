"""
Resolve Goldclub log / state / theme paths for UNC, on-cabinet local, and USB exports.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from config import DEFAULT_LOCAL_LOG_ROOT, DEFAULT_REMOTE_IP, format_unc_log_root

_RE_IPV4 = re.compile(r"(?:[\\/]+)?(\d{1,3}(?:\.\d{1,3}){3})")
_RE_USB_LOG_FOLDER = re.compile(r"^log_\d{2}_\d{2}_\d{4}$", re.IGNORECASE)

LOCAL_GOLDCLUB_ROOT = Path(r"C:\Goldclub")
LOCAL_LOG_ROOT = Path(DEFAULT_LOCAL_LOG_ROOT)
LOCAL_STATE_GCM = (
    LOCAL_GOLDCLUB_ROOT / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger"
)
LOCAL_THEMES_ROOT = LOCAL_GOLDCLUB_ROOT / "slot" / "themes"

_GCM_REL = Path("GoldClub.Aurum.Services") / "GCMessenger"
_LOG_SUBSYSTEM_MARKERS = frozenset(
    {"slotlog", "goldclub.aurum.services", "goldclub.logging.logdaemon", "onehand"}
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


def _path_exists_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _path_exists_file(p: Path) -> bool:
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
        parent = cur.parent
        cur = parent if parent != cur else None
    if _looks_like_log_tree(path):
        return path
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
    return goldclub_root / "var" / "state" / _GCM_REL


def _first_existing_dir(candidates: tuple[Path, ...]) -> Path | None:
    for p in candidates:
        if _path_exists_dir(p):
            return p
    return None


def _state_gcm_candidates_for_log_root(log_root: Path) -> tuple[Path, ...]:
    export = log_root
    parent = log_root.parent
    grand = parent.parent if parent != log_root else parent
    return (
        export / "state" / _GCM_REL,
        export / "var" / "state" / _GCM_REL,
        parent / "state" / _GCM_REL,
        grand / "state" / _GCM_REL,
        LOCAL_STATE_GCM,
    )


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
        goldclub = Path(rf"\\{ip}\c$\Goldclub")
        state_gcm = goldclub / "var" / "state" / _GCM_REL
        themes = goldclub / "slot" / "themes"
        return GoldclubLayout(
            scan_root=path,
            log_root=log_root,
            state_gcmessenger=state_gcm,
            themes_root=themes,
            goldclub_root=goldclub,
            kind=GoldclubLayoutKind.UNC,
            cabinet_ip=ip,
        )

    log_root = _resolve_var_log_base(path)
    if log_root is None and _is_usb_export_folder(path):
        log_root = path
    if log_root is None:
        return None

    goldclub_root: Path | None = None
    parts_lower = [p.lower() for p in log_root.parts]
    if "goldclub" in parts_lower:
        idx = parts_lower.index("goldclub")
        goldclub_root = Path(*log_root.parts[: idx + 1])

    if goldclub_root is not None and _path_exists_dir(goldclub_root):
        state_gcm = _gcmessenger_from_goldclub_root(goldclub_root)
        themes = goldclub_root / "slot" / "themes"
        kind = GoldclubLayoutKind.LOCAL_CABINET
    elif _is_usb_export_folder(log_root):
        state_gcm = _first_existing_dir(_state_gcm_candidates_for_log_root(log_root))
        themes = _resolve_themes_root(log_root)
        goldclub_root = None
        kind = GoldclubLayoutKind.USB_EXPORT
    else:
        state_gcm = _first_existing_dir(_state_gcm_candidates_for_log_root(log_root))
        themes = _resolve_themes_root(log_root)
        kind = GoldclubLayoutKind.CUSTOM

    return GoldclubLayout(
        scan_root=path,
        log_root=log_root,
        state_gcmessenger=state_gcm,
        themes_root=themes,
        goldclub_root=goldclub_root,
        kind=kind,
        cabinet_ip=ip or None,
    )


def device_manager_data_files(state_gcmessenger: Path) -> list[Path]:
    files: list[Path] = []
    for folder in ("SASControler1", "gm2au"):
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
    candidates.extend(
        (
            Path(r"G:\Goldclub\var\log"),
            LOCAL_LOG_ROOT,
        )
    )
    return _first_existing_dir(tuple(candidates))


def _log_root_for_roulette_install(install_root: Path) -> Path | None:
    goldclub = _goldclub_root_from_path(install_root)
    candidates: list[Path] = []
    if goldclub is not None:
        candidates.extend(
            (
                goldclub / "var" / "log" / "ruleta",
                goldclub / "var" / "log",
            )
        )
    candidates.extend(
        (
            install_root / "var" / "log" / "ruleta",
            install_root / "var" / "log",
            Path(r"G:\Goldclub\var\log\ruleta"),
            Path(r"G:\Goldclub\var\log"),
            Path(r"D:\var\log\ruleta"),
            Path(r"D:\var\log"),
            Path(r"C:\Goldclub\var\log\ruleta"),
            LOCAL_LOG_ROOT / "ruleta",
            LOCAL_LOG_ROOT,
        )
    )
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
    3. Local roulette install (``ruleta\\Ruleta.exe``) → ``…\\var\\log\\ruleta`` (or ``…\\var\\log``)
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
    if slot_install is not None:
        log_root = _log_root_for_slot_install(slot_install)
        if log_root is not None:
            return StartupScanDiscovery(
                mode="local",
                scan_root=str(log_root),
                game_kind="slot",
            )

    roulette_install = _find_roulette_install_root()
    if roulette_install is not None:
        log_root = _log_root_for_roulette_install(roulette_install)
        if log_root is not None:
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
    if refined is not None:
        return refined
    return StartupScanDiscovery(
        mode="remote",
        scan_root=format_unc_log_root(ip),
        game_kind=None,
        remote_ip=ip,
    )


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
    slot_dir = install_root / "slot"
    if _path_exists_dir(slot_dir):
        for rel in ("OneHand.exe", "game-start.exe", "bin/OneHand.exe"):
            if _path_exists_file(slot_dir / rel.replace("/", "\\")):
                return "slot"
    ruleta = install_root / "ruleta"
    if _path_exists_dir(ruleta):
        for name in ("Ruleta.exe", "ruleta.exe"):
            if _path_exists_file(ruleta / name):
                return "roulette"
        try:
            for exe in ruleta.glob("*.exe"):
                if exe.name.lower() == "ruleta.exe":
                    return "roulette"
        except OSError:
            pass
    return None


def _refine_log_scan_root_from_install(
    *,
    hint: str,
    remote_ip: str | None,
) -> StartupScanDiscovery | None:
    """Pick ``var/log`` vs ``var/log/ruleta`` from OneHand / Ruleta install markers."""
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


def resolve_log_scan_root(
    hint: str | None = None,
    *,
    remote_ip: str | None = None,
    exe_dir: Path | None = None,
) -> StartupScanDiscovery:
    """
    Resolve the log scan folder for SAS verification and log scanning.

    Slot cabinets use ``…\\var\\log`` (SlotLog and Aurum subfolders).
    Roulette cabinets prefer ``…\\var\\log\\ruleta`` when present.

    Reuses :func:`discover_startup_scan_target` for local/USB installs and
    refines generic ``…\\var\\log`` hints (including UNC) using OneHand vs
    Ruleta markers on the cabinet share.
    """
    hint_norm = normalize_path_str(hint or "")
    ip = extract_ip_from_path(hint_norm) or ((remote_ip or "").strip() or None)

    startup = discover_startup_scan_target(remote_ip=remote_ip, exe_dir=exe_dir)
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


def discover_portable_scan_roots(*, exe_dir: Path | None = None) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []

    def add(p: Path) -> None:
        s = str(p)
        if s not in seen and _path_exists_dir(p):
            seen.add(s)
            out.append(s)

    base = exe_dir or Path(sys.executable).resolve().parent
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
