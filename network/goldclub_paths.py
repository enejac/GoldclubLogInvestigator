"""
Resolve Goldclub log / state / theme paths for UNC, on-cabinet local, and USB exports.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from config import DEFAULT_LOCAL_LOG_ROOT

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
