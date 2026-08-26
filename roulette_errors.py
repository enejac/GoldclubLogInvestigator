"""
Roulette ERROR N catalog — screens that close Godot and open a dedicated error UI.

Primary log signature (ruleta* folders only, not slot)::

    INFO [:] <TRIAL error=\"30\" type=\"DISPLAYED\"> … </TRIAL>

Secondary aliases (same catalog)::

    ERRO [:] Trial expired!!!
    WARN [:] TRIAL EXPIRED ON …
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent


def _resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return _REPO_ROOT


_CATALOG_PATH = _resource_root() / "data" / "roulette_error_catalog.json"

# Godot closed / error window shown.
_TRIAL_DISPLAYED_RE = re.compile(
    r"""<TRIAL\s+error\s*=\s*["'](\d+)["']\s+type\s*=\s*["']DISPLAYED["']""",
    re.IGNORECASE,
)
# Clear / recovery — do not treat as an active error screen.
_TRIAL_SUCCEEDED_RE = re.compile(
    r"""<TRIAL\s+error\s*=\s*["'](\d+)["']\s+type\s*=\s*["']SUCCEEDED["']""",
    re.IGNORECASE,
)
_TRIAL_EXPIRED_RE = re.compile(
    r"""\bTrial\s+expired!!!|\bTRIAL\s+EXPIRED\s+ON\b""",
    re.IGNORECASE,
)

# Folders where classic Roulette ERROR screens are logged.
_ROULETTE_PATH_HINTS = ("ruleta", "godot")


@dataclass(frozen=True, slots=True)
class RouletteErrorInfo:
    code: int
    title: str
    error_type: str
    probable_cause: str


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    code: int
    title: str
    details: tuple[str, ...]
    possible_faults: tuple[str, ...]
    solution: str | None
    alias_of: int | None


_CATALOG: dict[int, _CatalogEntry] = {}


def _load_catalog() -> dict[int, _CatalogEntry]:
    if not _CATALOG_PATH.is_file():
        return {}
    try:
        raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[int, _CatalogEntry] = {}
    errors = raw.get("errors") if isinstance(raw, dict) else None
    if not isinstance(errors, dict):
        return {}
    for key, item in errors.items():
        if not isinstance(item, dict):
            continue
        try:
            code = int(item.get("code") if item.get("code") is not None else key)
        except (TypeError, ValueError):
            continue
        details = tuple(
            str(x).strip() for x in (item.get("details") or []) if str(x).strip()
        )
        faults = tuple(
            str(x).strip()
            for x in (item.get("possibleFaults") or [])
            if str(x).strip()
        )
        alias_raw = item.get("aliasOf")
        try:
            alias_of = int(alias_raw) if alias_raw is not None else None
        except (TypeError, ValueError):
            alias_of = None
        out[code] = _CatalogEntry(
            code=code,
            title=str(item.get("title") or f"Roulette ERROR {code}").strip(),
            details=details,
            possible_faults=faults,
            solution=str(item.get("solution") or "").strip() or None,
            alias_of=alias_of,
        )
    return out


def reload_roulette_error_catalog() -> None:
    """Reload catalog from disk (tests)."""
    global _CATALOG
    _CATALOG = _load_catalog()


_CATALOG = _load_catalog()


def is_roulette_error_log_path(path: str | Path | None) -> bool:
    """True for ruleta / godot log folders (never SlotLog / OneHand)."""
    if path is None:
        return False
    try:
        folder = Path(path).parent.name.casefold()
    except (TypeError, ValueError, OSError):
        folder = str(path).casefold().replace("\\", "/")
    if not folder:
        return False
    return any(h in folder for h in _ROULETTE_PATH_HINTS)


def extract_displayed_error_code(line: str) -> int | None:
    """Return ERROR N when the dedicated error window is displayed."""
    if not line:
        return None
    if _TRIAL_SUCCEEDED_RE.search(line):
        return None
    m = _TRIAL_DISPLAYED_RE.search(line)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if _TRIAL_EXPIRED_RE.search(line):
        return 30
    return None


def _resolve_entry(code: int) -> _CatalogEntry | None:
    entry = _CATALOG.get(code)
    if entry is None:
        return None
    if entry.alias_of is not None:
        aliased = _CATALOG.get(entry.alias_of)
        if aliased is not None:
            return aliased
    return entry


def format_roulette_error(code: int) -> RouletteErrorInfo:
    """Build UI label + probable-cause text for ERROR ``code``."""
    entry = _resolve_entry(code)
    error_type = f"Roulette ERROR {code}"
    if entry is None:
        return RouletteErrorInfo(
            code=code,
            title=f"Unknown Roulette ERROR {code}",
            error_type=error_type,
            probable_cause=(
                f"CRITICAL: Roulette ERROR {code} screen displayed (Godot UI closed). "
                f"No catalog entry for this code — collect ruleta Roulette log around "
                f'<TRIAL error="{code}" type="DISPLAYED"> and check hardware/COM/sensors.'
            ),
        )

    parts: list[str] = [
        f"CRITICAL: Roulette ERROR {code} — {entry.title}",
        "Godot UI closes and a dedicated Roulette error window opens (game interrupted).",
    ]
    if entry.details:
        parts.append(" ".join(entry.details))
    if entry.possible_faults:
        parts.append("Possible faults: " + "; ".join(entry.possible_faults))
    if entry.solution:
        parts.append(f"Solution: {entry.solution}")
    if entry.alias_of is not None and entry.code != entry.alias_of:
        # Using alias target already; mention original code if caller passed 64 etc.
        pass
    # If catalog entry was 64 aliased to 63, title comes from 63; note alias.
    raw = _CATALOG.get(code)
    if raw is not None and raw.alias_of is not None:
        parts.append(f"(Catalog: ERROR {code} is the same class as ERROR {raw.alias_of}.)")

    return RouletteErrorInfo(
        code=code,
        title=entry.title,
        error_type=error_type,
        probable_cause=" ".join(parts),
    )


def match_roulette_error_screen(
    line: str,
    path: str | Path | None = None,
) -> RouletteErrorInfo | None:
    """
    If ``line`` is a Roulette error-screen event on a roulette log path, return
    enriched info; otherwise None (slot / other subsystems never match).
    """
    if path is not None and not is_roulette_error_log_path(path):
        return None
    # When path is omitted (triage on snippet alone), still require a Roulette signature.
    code = extract_displayed_error_code(line)
    if code is None:
        return None
    if path is None and not (
        _TRIAL_DISPLAYED_RE.search(line) or _TRIAL_EXPIRED_RE.search(line)
    ):
        return None
    return format_roulette_error(code)


def describe_roulette_error_line(line: str) -> str | None:
    """Probable-cause string for triage, or None."""
    info = match_roulette_error_screen(line, path=None)
    return info.probable_cause if info is not None else None


def find_roulette_error_in_text(*blobs: str) -> RouletteErrorInfo | None:
    """Scan incident meta / preceding lines for a displayed Roulette ERROR N."""
    for blob in blobs:
        if not blob:
            continue
        # Whole blob first (meta often has Error type: Roulette ERROR 30 on one line).
        info = match_roulette_error_screen(str(blob), path=None)
        if info is not None:
            return info
        m = re.search(r"\bRoulette\s+ERROR\s+(\d+)\b", str(blob), re.I)
        if m:
            try:
                return format_roulette_error(int(m.group(1)))
            except ValueError:
                pass
        for line in str(blob).splitlines():
            info = match_roulette_error_screen(line, path=None)
            if info is not None:
                return info
    return None


def format_catalog_block_for_ai(info: RouletteErrorInfo) -> str:
    """Compact block for defect-ticket / audit AI user prompts."""
    return (
        f"ERROR {info.code}: {info.title}\n"
        f"{info.probable_cause}\n"
        "Tracking: GCI-ROULETTE-007 (Roulette ERROR N screen — Godot UI closed).\n"
        "Use this catalog entry as ROOT CAUSE when the fault is the dedicated error "
        "window (TRIAL DISPLAYED / Trial expired), not a downstream Godot kill alone."
    )


def list_roulette_error_codes() -> list[int]:
    return sorted(_CATALOG)


def catalog_as_dict() -> dict[str, Any]:
    """Debug / tests helper."""
    return {
        str(code): {
            "code": e.code,
            "title": e.title,
            "details": list(e.details),
            "possibleFaults": list(e.possible_faults),
            "solution": e.solution,
            "aliasOf": e.alias_of,
        }
        for code, e in sorted(_CATALOG.items())
    }
