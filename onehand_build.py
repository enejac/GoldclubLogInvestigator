"""OneHand Debug vs Release from the SlotLog ``MainFrm`` banner.

Proven on lab cabinet ``10.0.0.76`` (11 Sep 2026). A Debug OneHand SKU prints
this INFO *message* after version + static init, then loads
``Themes\\mgconfig.xml``:

    INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0
    INFO  [1] OneHand.MainFrm - Static initialization (i0)
    INFO  [1] OneHand.MainFrm - DB

``DB`` is the MainFrm message, not log4net level DEBUG. Root logging on that
cabinet is INFO; a level-based search misses it.

Do **not** classify from:

- PE ``FileVersionInfo.IsDebug`` / ``VS_FF_DEBUG`` (Debug SKUs leave it unset)
- CLR ``DebuggableAttribute`` or a CodeView ``\\Debug\\`` PDB path
- leftover ``ProductVersion=Debug`` / ``AssemblyConfiguration``
- denom catalog length (live ``DenominationList`` still ends at 5000; no theme
  or Aurum XML has ``<int>50000</int>``)
"""

from __future__ import annotations

import glob
import re
from pathlib import Path

BUILD_DEBUG = "Debug"
BUILD_RELEASE = "Release"

# Exact MainFrm banner tokens (whole message after the dash).
_BANNER_RE = re.compile(
    r"OneHand\.MainFrm\s+-\s+(?P<token>DB|RL|REL|RELEASE)\s*$",
    re.IGNORECASE,
)
_SLOT_VER_RE = re.compile(
    r"OneHand\.MainFrm\s+-\s+SlotMachine\s+v",
    re.IGNORECASE,
)
_STATIC_INIT_RE = re.compile(
    r"OneHand\.MainFrm\s+-\s+Static initialization",
    re.IGNORECASE,
)
# First lines that only appear after the Debug/Release banner on a real boot.
_PAST_BANNER_RE = re.compile(
    r"Themes[\\/]mgconfig\.xml|loaded assembly|Start loading theme",
    re.IGNORECASE,
)

_STARTUP_WINDOW = 16
_SLOTLOG_GLOBS = (
    "**/SlotLog/**/*.log",
    "**/*SlotLog*.log",
    "**/*slotlog*.log",
    "**/OneHand*/**/*.log",
)


def _banner_kind(line: str) -> str | None:
    match = _BANNER_RE.search(line)
    if not match:
        return None
    token = match.group("token").upper()
    if token == "DB":
        return BUILD_DEBUG
    return BUILD_RELEASE


def _is_startup_line(line: str) -> bool:
    return bool(_SLOT_VER_RE.search(line) or _STATIC_INIT_RE.search(line))


def classify_onehand_build_from_lines(lines: list[str]) -> str | None:
    """Return ``Debug``, ``Release``, or ``None`` from SlotLog lines.

    Uses the latest ``SlotMachine v`` / ``Static initialization`` block, then
    the following MainFrm banner. A lone ``MainFrm - DB`` still counts as
    Debug. Startup that reaches ``mgconfig.xml`` without ``DB`` is Release.
    A truncated boot that never gets past static init is left unknown.
    """
    last_init: int | None = None
    for index, line in enumerate(lines):
        if _is_startup_line(line):
            last_init = index

    if last_init is not None:
        window = lines[last_init : last_init + _STARTUP_WINDOW]
        saw_past_banner = False
        for line in window:
            kind = _banner_kind(line)
            if kind is not None:
                return kind
            if _PAST_BANNER_RE.search(line):
                saw_past_banner = True
        if saw_past_banner:
            return BUILD_RELEASE
        return None

    for line in lines:
        if _banner_kind(line) == BUILD_DEBUG:
            return BUILD_DEBUG
    return None


def classify_onehand_build_from_text(text: str) -> str | None:
    return classify_onehand_build_from_lines(text.splitlines())


def slotlog_search_roots(scan_root: Path) -> list[Path]:
    """Likely ``var/log`` folders for a slot scan target or log bundle."""
    try:
        root = Path(scan_root)
    except (OSError, TypeError, ValueError):
        return []

    bases = [root]
    name = root.name.casefold()
    if name in {"slot", "ruleta", "goldclub", "log"}:
        bases.append(root.parent)
    if name == "slot":
        bases.append(root.parent)

    candidates: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        for cand in (
            base / "var" / "log",
            base / "Goldclub" / "var" / "log",
            base,
        ):
            key = str(cand).casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(cand)
    return candidates


def iter_slotlog_files(scan_root: Path, *, limit: int = 12) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in slotlog_search_roots(scan_root):
        for pattern in _SLOTLOG_GLOBS:
            try:
                matches = glob.glob(str(root / pattern), recursive=True)
            except OSError:
                continue
            for raw in matches:
                key = str(raw).casefold()
                if key in seen:
                    continue
                seen.add(key)
                path = Path(raw)
                try:
                    if path.is_file():
                        files.append(path)
                except OSError:
                    continue
    files.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )
    return files[:limit]


def detect_onehand_build_from_log_dir(scan_root: Path | str) -> str | None:
    """Newest SlotLog wins. ``None`` when there is no startup banner yet."""
    try:
        root = Path(scan_root)
    except (OSError, TypeError, ValueError):
        return None
    for path in iter_slotlog_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kind = classify_onehand_build_from_text(text)
        if kind is not None:
            return kind
    return None


def format_onehand_build_suffix(kind: str | None) -> str:
    token = (kind or "").strip()
    if token in {BUILD_DEBUG, BUILD_RELEASE}:
        return f" ({token})"
    return ""
