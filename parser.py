"""
Parser: read log lines, attach theme/game context, classify severity,
and identify the first preceding anomaly (first cause) in a local window.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import config
from config import (
    COMPILED_ANOMALY_PATTERNS,
    COMPILED_SEVERITY_RULES,
    FILE_ENCODINGS,
    FIRST_CAUSE_LOOKBACK_LINES,
    MAX_LINE_LENGTH,
    OPEN_RETRIES,
    OPEN_RETRY_DELAY_SEC,
    resolve_probable_cause,
)
from logic_validator import RouletteSessionValidator, load_roulette_validation_config
from parser_rules import apply_triage_rules
from rules_engine import get_rules_manager
from timeline_engine import (
    EnvFingerprint,
    ParseResult,
    StateNode,
    StateTimelineBuilder,
    load_roulette_bonus_rules,
    merge_incidents_into_nodes,
)

logger = logging.getLogger(__name__)

# Consecutive lines past the scan end bound before a file is abandoned.
_PAST_SCAN_END_STREAK_LIMIT = 50

# YYYY-MM-DD T or space HH:MM:SS, optional fractional seconds, optional Z or ±offset.
_TIMESTAMP_REGEX = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def extract_datetime_from_line(line: str) -> datetime | None:
    """
    First timestamp on the line (ISO ``T`` or space separator); ignores leading junk.

    Naive values are treated as UTC when compared to scan bounds / sort keys.
    """
    match = _TIMESTAMP_REGEX.search(line)
    if not match:
        return None
    ts_str = match.group(1).strip().replace(" ", "T")
    ts_str = ts_str.replace("Z", "+00:00")
    # ``+0100`` / ``+0530`` → ``+01:00`` / ``+05:30`` for :meth:`datetime.fromisoformat`
    if re.search(r"[+-]\d{4}$", ts_str) and not re.search(r"[+-]\d{2}:\d{2}$", ts_str):
        ts_str = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts_str)
    try:
        dt = datetime.fromisoformat(ts_str)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _log_wall_timestamp_string(line: str) -> str | None:
    """
    First ISO-like timestamp as it appears in the log (cabinet wall clock / offset).

    Used for UI so the incident header matches ``Matched line`` and first-cause snippets.
    """
    if not line:
        return None
    match = _TIMESTAMP_REGEX.search(line)
    if not match:
        return None
    return match.group(1).strip()


# Cabinet id (gst…) on Aurum.Services / SlotLog paths: often in INFO lines, e.g.
# ``msgrUri:http://GST20664:50011/SASControler1`` (may appear well after file start).
_MACHINE_GST_RE = re.compile(r"\b(gst\d+)\b", re.IGNORECASE)
_INFO_TOKEN_RE = re.compile(r"\bINFO\b", re.IGNORECASE)
# GoldClub.Logging.LogDaemon e.g. ``Logging::Log() Spawning v1.2.1 ...`` (clr=/os= optional, same line).
_SPAWNING_V_RE = re.compile(
    r"Logging::Log\(\)\s+Spawning\s+v(?P<app>[\w\.\-]+)",
    re.IGNORECASE,
)
_CLR_KV_RE = re.compile(r"\bclr\s*=\s*([^,]+)", re.IGNORECASE)
_OS_KV_RE = re.compile(r"\bos\s*=\s*([^,]+)", re.IGNORECASE)
# SlotLog / OneHand client, e.g. ``… INFO  [SlotMachine] OneHand.MainFrm - SlotMachine v2.1.0 …``
_ONEHAND_SLOT_VER_RE = re.compile(
    r"OneHand\.MainFrm\s+-\s+SlotMachine\s+v([0-9.]+)",
)
# Theme hints beyond ``Themes\Folder`` (SlotLog / OneHand style lines).
_THEME_LOADING_RE = re.compile(
    r"Start\s+loading\s+theme.*?[Tt]hemes[\\/]([^\s\\/:]+)",
    re.IGNORECASE,
)
_THEME_TABLE_RE = re.compile(
    r"Active\s+table\s+logic\s+data\s+set\s*\(\s*theme:\s*([^\s\)]+)",
    re.IGNORECASE,
)
# e.g. ``[Loading Game] … ThemeName [Link2Win]``
_THEME_NAME_BRACKET_RE = re.compile(
    r"ThemeName\s*\[([^\]]+)\]",
    re.IGNORECASE,
)

# Dashboard label when no single game theme is active (game closed, menu, selector).
MULTIGAME_SELECTOR_GAME = "Multigame Selector"

# GoldClub roulette / Aurum logs emit multi-line exception blocks; only the primary
# ``Exception:`` / ``MessageDispatcher`` lines should become incidents.
_GOLDCLUB_EXCEPTION_COMPANION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bExceptionType:\s", re.IGNORECASE),
    re.compile(r"\bStackTrace:\s", re.IGNORECASE),
    re.compile(r"\bInnerException:\s*$", re.IGNORECASE),
    re.compile(r"\bTargetSite:\s", re.IGNORECASE),
    re.compile(r"\bCRIT\b.*\bSource:\s", re.IGNORECASE),
)


def subsystem_label_from_log_path(path: Path | str) -> str:
    """Infer subsystem context from a log file path (roulette folder layout)."""
    p = Path(path)
    folder = p.parent.name.strip()
    if not folder or folder.lower() == "log":
        return "unknown"
    low = folder.casefold()
    if "ruleta" in low:
        return "Roulette"
    if low.startswith("godot"):
        return "Godot UI"
    if low.startswith("bios") or low == "bios":
        return "BiOS"
    if "aurum" in low:
        return "Aurum"
    if "logdaemon" in low or "logging" in low:
        return "LogDaemon"
    if low in {"commctrl", "hwsubsys", "setup", "game-start", "start-game"}:
        return folder
    return folder


def _should_suppress_incident_line(line: str) -> bool:
    """Skip stack/continuation lines inside GoldClub CRIT exception dumps."""
    if any(p.search(line) for p in _GOLDCLUB_EXCEPTION_COMPANION_RES):
        return True
    if config.STACK_TRACE_LINE_RE is not None and config.STACK_TRACE_LINE_RE.search(line):
        return True
    return False


def _maybe_append_classified_incident(
    *,
    line: str,
    incidents: list[Incident],
    timestamp: datetime | None,
    current_game: str,
    last_known_game: str | None,
    path_str: str,
    lineno: int,
    buffer: deque[tuple[int, str]],
) -> None:
    if _should_suppress_incident_line(line):
        return
    custom = _try_custom_rule_match(line)
    if custom:
        err_type, severity = custom
        incidents.append(
            _incident_for_classified_line(
                timestamp=timestamp,
                game=current_game,
                last_known_game=last_known_game,
                severity=severity,
                error_type=err_type,
                path_str=path_str,
                lineno=lineno,
                line=line,
                buffer=buffer,
            )
        )
        return
    # Roulette-only: ERROR N window closes Godot UI (never slot / OneHand).
    from roulette_errors import match_roulette_error_screen

    roulette_err = match_roulette_error_screen(line, path_str)
    if roulette_err is not None:
        incidents.append(
            _incident_for_classified_line(
                timestamp=timestamp,
                game=current_game,
                last_known_game=last_known_game,
                severity="CRITICAL",
                error_type=roulette_err.error_type,
                path_str=path_str,
                lineno=lineno,
                line=line,
                buffer=buffer,
                probable_cause_override=roulette_err.probable_cause,
            )
        )
        return
    classified = _classify_line(line)
    if not classified:
        return
    _, report_label, severity = classified
    incidents.append(
        _incident_for_classified_line(
            timestamp=timestamp,
            game=current_game,
            last_known_game=last_known_game,
            severity=severity,
            error_type=report_label,
            path_str=path_str,
            lineno=lineno,
            line=line,
            buffer=buffer,
        )
    )
# Incidents at ERROR/FATAL/CRITICAL may include previous theme after unload / multigame.
_GAME_PREV_CONTEXT_SEVERITIES = frozenset({"ERROR", "FATAL", "CRITICAL"})
# Exit sequence: production OneHand / SlotMachine phrases first, then generic heuristics.
_MULTIGAME_SELECTOR_RE = re.compile(
    r"(?ix)"
    r"KeyType:MultiGameSelection"
    r"|OnStart\s+the\s+GameSelectorDisplay"
    r"|Start:\s*UnloadTheme"
    r"|to\s+MultigamerDialog\b"
    r"|\bmultigame\s*selector\b"
    r"|(?:close|closing|closed)\s+(?:the\s+)?active\s+theme\b"
    r"|theme\s+unloaded\b"
    r"|return(?:ing)?\s+to\s+(?:the\s+)?lobby\b"
    r"|return(?:ing)?\s+to\s+(?:the\s+)?(?:main\s+)?menu\b"
    r"|(?:show|open)(?:ing)?\s+(?:the\s+)?(?:game\s+)?select(?:or|ion)\b"
    r"|(?:game|slot)\s+closed\b"
    r"|(?:exit|close|quit)(?:ting|ted)?\s+(?:from\s+)?(?:the\s+)?(?:active\s+)?(?:game|theme|slot)\b"
    r"|unload(?:ing|ed)?\s+(?:the\s+)?(?:current\s+)?theme\b"
    r"|theme\s+(?:unload(?:ed|ing)?|closed|closing|dispose|disposed|disposing)\b"
    r"|(?:active\s+)?theme\s+(?:destroyed|destroying|dispose|disposed)\b"
    r"|destroy(?:ing|ed)?\s+(?:the\s+)?(?:active\s+)?theme\b"
    r"|(?:left|leave|leav(?:es|ing))\s+(?:the\s+)?(?:active\s+)?theme\b"
    r"|selector\s*(?:screen|menu|view|state)\b"
    r"|(?:press(?:ed)?|hit)\s+['\u2018\u2019\"]?[xX]['\u2018\u2019\"]?\s+to\s+(?:close|exit)\b"
    r"|(?:debug|ui|key).{0,48}['\u2018\u2019\"]?[xX]['\u2018\u2019\"]?.{0,24}(?:press(?:ed)?|keydown|close\s+game)\b"
    r"|\b(?:close|exit)\s+button\b.{0,48}(?:press(?:ed)?|click(?:ed)?)\b"
)


def extract_onehand_from_lines(lines: Iterable[str]) -> str | None:
    """
    Return the last version token captured across ``lines``.

    Each element is typically one log line; the caller may pass a one-line iterable
    per line from ``parse_log_file`` so extraction stays in the same pass.
    """
    last: str | None = None
    for line in lines:
        m = _ONEHAND_SLOT_VER_RE.search(line)
        if m:
            last = m.group(1)
    return last


def _path_prefers_machine_id_extraction(path: Path) -> bool:
    s = str(path).replace("\\", "/").lower()
    return "goldclub.aurum.services" in s or "slotlog" in s


_TRAILING_ELLIPSIS_RE = re.compile(r"\s*\.\.\.\s*$")


def _strip_trailing_ellipsis(value: str) -> str | None:
    """Trim trailing ``...`` the spawn banner appends (e.g. ``…6.2.9200.0 ...``)."""
    cleaned = _TRAILING_ELLIPSIS_RE.sub("", value.strip())
    return cleaned or None


def _env_fingerprint_from_line(line: str) -> EnvFingerprint | None:
    m = _SPAWNING_V_RE.search(line)
    if not m:
        return None
    app = m.group("app")
    clr_m = _CLR_KV_RE.search(line)
    os_m = _OS_KV_RE.search(line)
    clr_s = _strip_trailing_ellipsis(clr_m.group(1)) if clr_m else None
    os_s = _strip_trailing_ellipsis(os_m.group(1)) if os_m else None
    return EnvFingerprint(
        app_version=app,
        clr_version=clr_s or None,
        os_version=os_s or None,
    )


# Subsystems known to log the FULL fingerprint (``Spawning … clr=… os=…``) on one line.
# ``GoldClub.Logging.LogDaemon`` truncates it (``Spawning v1.2.1 ...``), so it is
# deliberately absent here. Ordered by how reliably/early the banner appears.
_ENV_FP_SUBSYSTEMS: tuple[str, ...] = (
    "BiOS2",
    "GoldClub.Aurum.Services",
    "OneHandConfigurer",
    "HardwareSetup App",
    "CommCtrl",
)
# Hard cap so one enrichment read can never walk a whole multi-MB file: the spawn banner
# is emitted right after a (re)start, near the top of the newest daily log.
_ENV_FP_MAX_LINES = 20_000


def _resolve_var_log_base(path: Path) -> Path | None:
    """Return the ``…/var/log`` directory at or above ``path`` (UNC-safe), else None."""
    cur: Path | None = path
    for _ in range(12):
        if cur is None:
            break
        parts = [p.lower() for p in cur.parts]
        if len(parts) >= 2 and parts[-1] == "log" and parts[-2] == "var":
            return cur
        parent = cur.parent
        cur = parent if parent != cur else None
    # ``path`` may already be the log base (holds known subsystem subfolders).
    try:
        if any((path / sub).is_dir() for sub in _ENV_FP_SUBSYSTEMS):
            return path
    except OSError:
        pass
    return None


def _newest_log_file(folder: Path) -> Path | None:
    newest: Path | None = None
    newest_mtime = -1.0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file() or not entry.name.lower().endswith(".log"):
                        continue
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = Path(entry.path)
    except OSError:
        return None
    return newest


def _first_clr_os_fingerprint_in_file(path: Path) -> EnvFingerprint | None:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i >= _ENV_FP_MAX_LINES:
                    break
                if "clr=" not in line and "os=" not in line:
                    continue
                fp = _env_fingerprint_from_line(line)
                if fp and (fp.clr_version or fp.os_version):
                    return fp
    except OSError:
        return None
    return None


def extract_env_fingerprint_from_logtree(scan_root: str | Path) -> EnvFingerprint | None:
    """Best-effort, bounded CLR/OS fingerprint from sibling subsystem logs.

    Some subsystems (notably ``BiOS2``) log the full ``Spawning … clr=… os=…`` banner
    while ``GoldClub.Logging.LogDaemon`` truncates it. When the primary scan root does
    not include such a subsystem, this locates ``…/var/log`` relative to ``scan_root``
    and reads ONE newest file per known subsystem (line-capped, stops at first match).

    Reads only from logs (never the local machine's OS) so the result reflects the EGM,
    and is cheap enough to call once at the end of a scan. Returns None if not found.
    """
    if not scan_root:
        return None
    base = _resolve_var_log_base(Path(str(scan_root)))
    if base is None:
        return None
    for sub in _ENV_FP_SUBSYSTEMS:
        folder = base / sub
        try:
            if not folder.is_dir():
                continue
        except OSError:
            continue
        newest = _newest_log_file(folder)
        if newest is None:
            continue
        fp = _first_clr_os_fingerprint_in_file(newest)
        if fp is not None:
            return fp
    return None


# ``OneHand.MainFrm`` periodically logs memory, e.g.:
#   ``… [Current process:7,764MB 3,612MB free memory of total 7,122MB]``
# (comma is a thousands separator). Captured: process MB, free MB, total MB.
_MEMORY_LINE_RE = re.compile(
    r"Current\s+process:\s*([\d.,]+)\s*MB\s+([\d.,]+)\s*MB\s+free\s+memory\s+of\s+total\s+([\d.,]+)\s*MB",
    re.IGNORECASE,
)
# Memory samples live in SlotLog; cap the scan so one parse can't walk forever.
_EVENT_RAM_MAX_LINES = 600_000


def _parse_mb(token: str) -> float | None:
    try:
        return float(token.replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_memory_line(line: str) -> dict | None:
    """Return ``{process_mb, free_mb, total_mb, used_mb}`` from a memory log line, else None."""
    m = _MEMORY_LINE_RE.search(line)
    if not m:
        return None
    proc = _parse_mb(m.group(1))
    free = _parse_mb(m.group(2))
    total = _parse_mb(m.group(3))
    if total is None or free is None:
        return None
    return {
        "process_mb": proc,
        "free_mb": free,
        "total_mb": total,
        "used_mb": total - free,
    }


def resolve_slotlog_file_for_incident(incident_log_path: str | Path) -> Path | None:
    """Locate the SlotLog daily file covering an incident, derived from its own path.

    Prefers the same-dated SlotLog file (``SlotLog/<same-name>.log``); falls back to the
    newest SlotLog file, or the incident's own file if SlotLog can't be resolved.
    """
    p = Path(str(incident_log_path))
    try:
        if p.parent.name.lower() == "slotlog" and p.is_file():
            return p
    except OSError:
        pass
    base = _resolve_var_log_base(p)
    slotdir = base / "SlotLog" if base is not None else None
    try:
        if slotdir is not None and slotdir.is_dir():
            same = slotdir / p.name
            if same.is_file():
                return same
            newest = _newest_log_file(slotdir)
            if newest is not None:
                return newest
    except OSError:
        pass
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def find_nearest_memory_sample(
    file_path: str | Path, target_dt: datetime | None
) -> dict | None:
    """Scan ``file_path`` for the memory sample nearest ``target_dt`` (chronological early-stop)."""
    target = _to_utc(target_dt)
    best: dict | None = None
    best_abs: float | None = None
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i >= _EVENT_RAM_MAX_LINES:
                    break
                if "free memory of total" not in line:
                    continue
                mem = parse_memory_line(line)
                if mem is None:
                    continue
                sample_dt = _to_utc(extract_datetime_from_line(line))
                ts_match = _TIMESTAMP_REGEX.search(line)
                sample = {
                    **mem,
                    "sample_ts": ts_match.group(1) if ts_match else None,
                    "delta_sec": None,
                }
                if target is None or sample_dt is None:
                    if best is None:
                        best = sample
                    continue
                delta = (sample_dt - target).total_seconds()
                sample["delta_sec"] = delta
                ad = abs(delta)
                if best_abs is None or ad < best_abs:
                    best, best_abs = sample, ad
                elif sample_dt > target:
                    # Past the event and getting worse: logs are chronological → stop.
                    break
    except OSError:
        return None
    return best


def parse_event_ram_sample(
    incident_log_path: str | Path, target_line: str | None
) -> dict | None:
    """Find the RAM sample (from SlotLog) nearest an incident's time. Log-only, no host query."""
    target_dt = extract_datetime_from_line(target_line or "")
    slotlog = resolve_slotlog_file_for_incident(incident_log_path)
    if slotlog is None:
        return None
    return find_nearest_memory_sample(slotlog, target_dt)


@dataclass(frozen=True, slots=True)
class Incident:
    """One reported finding from a log file."""

    timestamp: datetime | None
    game: str
    severity: str
    error_type: str
    probable_cause: str
    log_file_path: str
    line_number: int
    line_snippet: str
    first_cause_line: int | None = None
    first_cause_snippet: str | None = None
    validation_status: str | None = None
    validation_detail: str | None = None
    remote_ram_used_pct: float | None = None
    """Physical RAM use percent on remote cabinet (WMIC), filled async after live tail."""
    remote_process_mb: float | None = None
    """``OneHand.exe`` working set (MB) on remote host when captured."""
    remote_ram_total_mb: float | None = None
    """Total physical RAM (MB) on remote host when captured (tooltip)."""
    remote_ram_used_mb: float | None = None
    """Used physical RAM (MB) on remote host when captured (tooltip)."""
    event_ram_used_mb: float | None = None
    """System RAM used (MB) sampled from logs near a CRITICAL event (total − free)."""
    event_ram_total_mb: float | None = None
    """Total physical RAM (MB) from the same sampled log line."""
    event_ram_free_mb: float | None = None
    """Free physical RAM (MB) from the same sampled log line."""
    event_process_mb: float | None = None
    """``OneHand`` process memory (MB) from the same sampled log line."""
    event_ram_sample_ts: str | None = None
    """Wall-clock timestamp string of the sampled memory line."""
    event_ram_delta_sec: float | None = None
    """Signed seconds between the memory sample and the event (sample − event)."""
    event_ram_status: str | None = None
    """``ok`` / ``unavailable`` once an async parse has been attempted (else None)."""
    live_ram_used_mb: float | None = None
    """Live WMIC physical RAM used (MB) captured ~at detection during Live Watch."""
    live_ram_total_mb: float | None = None
    """Live WMIC total physical RAM (MB) at capture."""
    live_ram_used_pct: float | None = None
    """Live WMIC physical RAM use percent at capture."""
    live_ram_process_mb: float | None = None
    """Live ``OneHand`` working set (MB) at capture (matches Task Manager Processes)."""
    live_ram_captured_ts: str | None = None
    """UTC ISO timestamp of the live capture (proves how close to the error it was)."""
    live_ram_delta_sec: float | None = None
    """Signed seconds between the live capture and the event (capture − event)."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def message(self) -> str:
        """Matched log line text (used for notification ignore matching)."""
        return (self.line_snippet or "").strip()

    def timestamp_display(self) -> str:
        """
        Prefer the timestamp substring from the matched line (same as log / first-cause).

        Stack / continuation lines often lack a leading clock; those show stored UTC with
        an explicit ``UTC`` suffix so they are not confused with cabinet local time.
        """
        wall = _log_wall_timestamp_string(self.line_snippet or "")
        if wall:
            return wall
        if self.timestamp is None:
            return "—"
        return self.timestamp.strftime("%Y-%m-%d %H:%M:%S") + " UTC"


def _utc_scan_bound(dt: datetime | None) -> datetime | None:
    """Normalize optional scan bound to timezone-aware UTC for ``datetime`` comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _apply_game_state(
    line: str, current_game: str, last_known_game: str | None
) -> tuple[str, str | None]:
    """
    Update active game and last-loaded theme from one log line.

    Unload / multigame lines set ``current_game`` to ``MULTIGAME_SELECTOR_GAME`` but
    **do not** clear ``last_known_game`` (the theme that just closed).
    Theme load lines set both to the extracted name.
    """
    if _MULTIGAME_SELECTOR_RE.search(line):
        lk = last_known_game
        if lk is None and current_game not in ("unknown", MULTIGAME_SELECTOR_GAME):
            lk = current_game
        return MULTIGAME_SELECTOR_GAME, lk

    m = _THEME_NAME_BRACKET_RE.search(line)
    if m:
        name = m.group(1).strip()
        if name:
            return name, name

    m = _THEME_TABLE_RE.search(line)
    if m:
        name = m.group(1).strip()
        return name, name

    m = _THEME_LOADING_RE.search(line)
    if m:
        name = m.group(1).strip()
        return name, name

    if config.THEME_RE is not None:
        m = config.THEME_RE.search(line)
        if m:
            name = m.group(1).strip()
            return name, name

    return current_game, last_known_game


def _extract_game(line: str, fallback: str) -> str:
    """
    Active game / theme for a single line (no ``last_known_game`` threading).

    Used by tests; full parsing uses :func:`_apply_game_state`.
    """
    current, _ = _apply_game_state(line, fallback, None)
    return current


def _display_game_for_incident(
    current_game: str, last_known_game: str | None, severity: str
) -> str:
    """Attach previous theme to incidents in multigame / unknown context for hard severities."""
    if severity.strip().upper() not in _GAME_PREV_CONTEXT_SEVERITIES:
        return current_game
    if not last_known_game or last_known_game == MULTIGAME_SELECTOR_GAME:
        return current_game
    if current_game == MULTIGAME_SELECTOR_GAME or current_game == "unknown":
        return f"{current_game} (Prev: {last_known_game})"
    return current_game


def _classify_line(line: str) -> tuple[str, str, str] | None:
    """
    Return ``(rule_name, report_label, severity)`` for the first matching rule, else None.
    """
    for rule_name, label, severity, patterns in COMPILED_SEVERITY_RULES:
        for p in patterns:
            if p.search(line):
                return rule_name, label, severity
    return None


def _try_custom_rule_match(line: str) -> tuple[str, str] | None:
    """
    First user-defined rule that matches ``line``: ``(error_type, severity)``.
    Evaluated before built-in severity rules.
    """
    for rule, pat in get_rules_manager().iter_compiled():
        if pat.search(line):
            return rule.error_type, rule.normalized_severity()
    return None


def _incident_for_classified_line(
    *,
    timestamp: datetime | None,
    game: str,
    last_known_game: str | None,
    severity: str,
    error_type: str,
    path_str: str,
    lineno: int,
    line: str,
    buffer: deque[tuple[int, str]],
    probable_cause_override: str | None = None,
) -> Incident:
    """
    Build one incident row. ``timestamp`` must be the **effective** time for this
    line (from :func:`extract_datetime_from_line` when present, else the last
    timestamp seen earlier in the file) so stack/continuation lines inherit the
    parent log line's clock instead of ``None`` / ``—`` in the UI.
    """
    fc = _first_anomaly_in_buffer(buffer)
    fc_line, fc_snip = (fc[0], fc[1]) if fc else (None, None)
    snippet = line.strip()[:500]
    display_game = _display_game_for_incident(game, last_known_game, severity)
    if probable_cause_override:
        probable = probable_cause_override
    else:
        triaged = apply_triage_rules(snippet, severity)
        probable = triaged if triaged is not None else resolve_probable_cause(line)
    return Incident(
        timestamp=timestamp,
        game=display_game,
        severity=severity,
        error_type=error_type,
        probable_cause=probable,
        log_file_path=path_str,
        line_number=lineno,
        line_snippet=snippet,
        first_cause_line=fc_line,
        first_cause_snippet=fc_snip,
        validation_status=None,
        validation_detail=None,
    )


def _first_anomaly_in_buffer(
    buffer: deque[tuple[int, str]],
) -> tuple[int, str] | None:
    """Oldest line in the rolling buffer that matches any anomaly pattern."""
    for lineno, text in buffer:
        if any(p.search(text) for p in COMPILED_ANOMALY_PATTERNS):
            return lineno, text.strip()[:500]
    return None


def _read_text_lines(path: Path) -> Iterator[str]:
    """
    Yield lines from ``path``, trying encodings and retrying on transient errors.

    Handles active logs and slow UNC shares via ``OPEN_RETRIES``.
    """
    last_error: OSError | None = None

    for attempt in range(OPEN_RETRIES):
        for encoding in FILE_ENCODINGS:
            try:
                with open(
                    path,
                    "r",
                    encoding=encoding,
                    errors="replace",
                    newline="",
                ) as handle:
                    yield from handle
                return
            except PermissionError as e:
                last_error = e
                logger.debug(
                    "Permission denied reading %s (attempt %s/%s): %s",
                    path,
                    attempt + 1,
                    OPEN_RETRIES,
                    e,
                )
                time.sleep(OPEN_RETRY_DELAY_SEC)
                break
            except OSError as e:
                last_error = e
                # Network / sharing violations: retry, then try next encoding
                win_err = getattr(e, "winerror", None)
                if win_err in (32, 33, 5):  # in use, lock, access denied
                    logger.debug("OS error on %s: %s", path, e)
                    time.sleep(OPEN_RETRY_DELAY_SEC)
                    break
                raise

    if last_error:
        logger.warning("Giving up reading %s: %s", path, last_error)
        raise last_error


@dataclass
class LiveFileParseState:
    """Mutable tail-follow state for one log file (byte offset + parser context)."""

    path_str: str
    byte_offset: int
    pending_fragment: str
    next_line_number: int
    current_game: str
    last_known_game: str | None = None
    current_file_timestamp: datetime | None = None
    buffer: deque[tuple[int, str]] = field(
        default_factory=lambda: deque(maxlen=FIRST_CAUSE_LOOKBACK_LINES)
    )


def count_newlines_in_prefix(path: Path, max_bytes: int) -> int:
    """Count ``\\n`` bytes in ``path`` from the start up to ``max_bytes`` (for line numbering)."""
    if max_bytes <= 0:
        return 0
    total = 0
    read = 0
    try:
        with open(path, "rb") as handle:
            while read < max_bytes:
                chunk = handle.read(min(65536, max_bytes - read))
                if not chunk:
                    break
                total += chunk.count(b"\n")
                read += len(chunk)
    except OSError:
        return 0
    return total


def create_live_state_at_eof(path: Path) -> LiveFileParseState | None:
    """
    Begin tailing at end-of-file: next appended line uses line number ``lines+1``.

    Returns ``None`` if the file cannot be stat'd.
    """
    return _create_live_state_at_offset(path, None)


def create_live_state_after_rewind(
    path: Path,
    *,
    max_backfill_bytes: int = config.LIVE_WATCH_REWIND_MAX_BYTES,
) -> LiveFileParseState | None:
    """
    Resume tailing a log that shrank (truncate-in-place or same-name replacement).

    Starting at EOF would drop everything already written to the new file, so the
    tail restarts at byte 0. Very large replacements are capped at
    ``max_backfill_bytes`` (measured back from EOF, aligned to a line start) to
    keep one poll from emitting a whole file as a live batch.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    start = 0
    if max_backfill_bytes > 0 and size > max_backfill_bytes:
        start = _next_line_start_at_or_after(path, size - max_backfill_bytes)
    return _create_live_state_at_offset(path, start)


def _create_live_state_at_offset(path: Path, offset: int | None) -> LiveFileParseState | None:
    """Build tail state at ``offset`` (``None`` means end-of-file)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    start = size if offset is None else max(0, min(int(offset), size))
    lines = count_newlines_in_prefix(path, start)
    return LiveFileParseState(
        path_str=str(path),
        byte_offset=start,
        pending_fragment="",
        next_line_number=lines + 1,
        current_game=subsystem_label_from_log_path(path),
        last_known_game=None,
        buffer=deque(maxlen=FIRST_CAUSE_LOOKBACK_LINES),
    )


def _next_line_start_at_or_after(path: Path, offset: int) -> int:
    """Offset of the first line start at/after ``offset`` so no partial line is parsed."""
    if offset <= 0:
        return 0
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            window = handle.read(MAX_LINE_LENGTH + 1)
    except OSError:
        return offset
    idx = window.find(b"\n")
    if idx < 0:
        return offset
    return offset + idx + 1


def feed_live_byte_chunk(
    state: LiveFileParseState,
    chunk: bytes,
    *,
    encoding: str = "utf-8",
) -> tuple[list[Incident], str | None]:
    """
    Decode ``chunk`` as new bytes after ``state.byte_offset`` (caller advances file cursor).

    Updates ``state`` (offset, pending fragment, line numbers, theme buffer) and
    returns new ``Incident`` rows — same rules as ``parse_log_file`` per line.

    Second return value is the active game / theme if it **changed** in this chunk
    to a non-``unknown`` value (last line wins), including ``Multigame Selector``.
    """
    incidents: list[Incident] = []
    if not chunk:
        return incidents, None

    game_before_chunk = state.current_game
    state.byte_offset += len(chunk)
    text = state.pending_fragment + chunk.decode(encoding, errors="replace")
    parts = text.split("\n")
    state.pending_fragment = parts.pop()

    for part in parts:
        line = part.rstrip("\r")
        lineno = state.next_line_number
        state.next_line_number += 1
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "…"

        state.current_game, state.last_known_game = _apply_game_state(
            line, state.current_game, state.last_known_game
        )
        extracted_ts = extract_datetime_from_line(line)
        if extracted_ts is not None:
            state.current_file_timestamp = extracted_ts
        effective_ts = (
            extracted_ts if extracted_ts is not None else state.current_file_timestamp
        )
        _maybe_append_classified_incident(
            line=line,
            incidents=incidents,
            timestamp=effective_ts,
            current_game=state.current_game,
            last_known_game=state.last_known_game,
            path_str=state.path_str,
            lineno=lineno,
            buffer=state.buffer,
        )

        state.buffer.append((lineno, line))

    game_hint: str | None = None
    if (
        state.current_game != "unknown"
        and state.current_game != game_before_chunk
    ):
        game_hint = state.current_game

    return incidents, game_hint


def _parse_txrx_sas_file(
    path: Path,
    *,
    scan_start_time: datetime | None = None,
    scan_end_time: datetime | None = None,
) -> ParseResult:
    """Build ``StateNode`` + INFO ``Incident`` rows from ``TXRXData.dat`` SAS traffic."""
    from network.sas_decoder import extract_sas_timeline_events

    scan_lo = _utc_scan_bound(scan_start_time)
    scan_hi = _utc_scan_bound(scan_end_time)
    incidents: list[Incident] = []
    raw_nodes: list[StateNode] = []
    try:
        event_iter = enumerate(extract_sas_timeline_events(str(path)), start=1)
    except OSError as e:
        logger.error("Failed to open SAS TXRX log %s: %s", path, e)
        return ParseResult([], [], None, None, None, None, None)
    for i, (dt_utc, text) in event_iter:
        if scan_lo is not None and dt_utc < scan_lo:
            continue
        if scan_hi is not None and dt_utc > scan_hi:
            break
        sk = dt_utc.timestamp()
        iso = dt_utc.isoformat()
        title = text if len(text) <= 200 else text[:197] + "…"
        trig = text if len(text) <= 500 else text[:497] + "…"
        raw_nodes.append(
            StateNode(
                timestamp=iso,
                end_timestamp=iso,
                timestamp_sort_key=sk,
                end_timestamp_sort_key=sk,
                state_name=title,
                previous_state="[SAS Protocol]",
                duration_sec=0.0,
                trigger_event=trig,
                line_number=i,
                end_line_number=i,
                log_file_path=str(path.resolve()),
                health="ok",
                bonus_label=None,
                incident_count=0,
            )
        )
        pc = text if len(text) <= 2000 else text[:1997] + "…"
        snip = text if len(text) <= 500 else text[:497] + "…"
        incidents.append(
            Incident(
                timestamp=dt_utc,
                game="[SAS Protocol]",
                severity="INFO",
                error_type="[SAS Protocol]",
                probable_cause=pc,
                log_file_path=str(path.resolve()),
                line_number=i,
                line_snippet=snip,
            )
        )
    nodes = merge_incidents_into_nodes(raw_nodes, incidents)
    return ParseResult(
        incidents,
        nodes,
        None,
        None,
        None,
        None,
        None,
    )


def parse_log_file(
    path: Path,
    *,
    scan_start_time: datetime | None = None,
    scan_end_time: datetime | None = None,
) -> ParseResult:
    """
    Parse a single log file: severity incidents plus state-machine timeline nodes.

    Theme/game context follows ``_extract_game`` (including **Multigame Selector**
    when menu / unload / close lines appear). First-cause uses a rolling window
    of prior lines.
    State transitions use ``Change MachineState from X to Y`` (see ``timeline_engine``).

    When ``scan_start_time`` / ``scan_end_time`` are set, each line is filtered by an
    **effective** timestamp: the line's own parse if present, else the last timestamp
    seen earlier in the file (continuation / stack lines). Out-of-range lines are
    skipped; parsing stops after the first line whose effective time is strictly
    after ``scan_end_time`` (assumes chronological logs).
    """
    path = Path(path)
    if path.name.lower() == "txrxdata.dat":
        return _parse_txrx_sas_file(
            path,
            scan_start_time=scan_start_time,
            scan_end_time=scan_end_time,
        )

    incidents: list[Incident] = []
    buffer: deque[tuple[int, str]] = deque(maxlen=FIRST_CAUSE_LOOKBACK_LINES)
    current_game = subsystem_label_from_log_path(path)
    last_known_game: str | None = None
    bonus_rules = load_roulette_bonus_rules()
    stb = StateTimelineBuilder(str(path), bonus_rules)
    last_lineno = 0
    last_ts: datetime | None = None
    last_line_text = ""
    validation_cfg = load_roulette_validation_config()
    rsv: RouletteSessionValidator | None = (
        RouletteSessionValidator(str(path), validation_cfg)
        if validation_cfg
        else None
    )
    prefer_machine_id_path = _path_prefers_machine_id_extraction(path)
    parsed_machine_id: str | None = None
    env_app: str | None = None
    env_clr: str | None = None
    env_os: str | None = None
    onehand_version: str | None = None
    scan_lo = _utc_scan_bound(scan_start_time)
    scan_hi = _utc_scan_bound(scan_end_time)
    current_file_timestamp: datetime | None = None
    past_end_streak = 0

    try:
        line_iter = enumerate(_read_text_lines(path), start=1)
    except OSError as e:
        logger.error("Failed to open %s: %s", path, e)
        return ParseResult(incidents, [], None, None, None, None, None)

    for lineno, raw in line_iter:
        last_lineno = lineno
        line = raw.rstrip("\r\n")
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "…"

        extracted_ts = extract_datetime_from_line(line)
        if extracted_ts is not None:
            current_file_timestamp = extracted_ts
        effective_ts = (
            extracted_ts if extracted_ts is not None else current_file_timestamp
        )

        if effective_ts is not None:
            if scan_lo is not None and effective_ts < scan_lo:
                continue
            if scan_hi is not None and effective_ts > scan_hi:
                # Logs are usually chronological, so stopping here saves a lot of
                # work — but a single clock-skewed or future-dated line must not
                # discard the rest of the file. Only give up once several lines
                # in a row are past the bound.
                past_end_streak += 1
                if past_end_streak >= _PAST_SCAN_END_STREAK_LIMIT:
                    break
                continue
            past_end_streak = 0

        if (
            prefer_machine_id_path
            and parsed_machine_id is None
            and _INFO_TOKEN_RE.search(line)
        ):
            m_id = _MACHINE_GST_RE.search(line)
            if m_id:
                parsed_machine_id = m_id.group(1).lower()

        fp_line = _env_fingerprint_from_line(line)
        if fp_line:
            if fp_line.app_version:
                env_app = fp_line.app_version
            if fp_line.clr_version:
                env_clr = fp_line.clr_version
            if fp_line.os_version:
                env_os = fp_line.os_version

        oh = extract_onehand_from_lines([line])
        if oh is not None:
            onehand_version = oh

        last_ts = effective_ts if effective_ts is not None else last_ts
        last_line_text = line

        # Theme may appear on the same line as the error or earlier
        current_game, last_known_game = _apply_game_state(
            line, current_game, last_known_game
        )

        _maybe_append_classified_incident(
            line=line,
            incidents=incidents,
            timestamp=effective_ts,
            current_game=current_game,
            last_known_game=last_known_game,
            path_str=str(path),
            lineno=lineno,
            buffer=buffer,
        )

        stb.on_line(
            lineno,
            line,
            effective_ts.isoformat() if effective_ts is not None else None,
        )
        if rsv is not None:
            rsv.on_line(lineno, line, effective_ts, current_game)
        buffer.append((lineno, line))

    stb.finalize(last_lineno)
    nodes = merge_incidents_into_nodes(stb.nodes, incidents)
    val_stats: dict | None = None
    if rsv is not None:
        extra, val_stats = rsv.finalize(last_lineno, last_ts, last_line_text, current_game)
        incidents.extend(extra)
    env_fp: EnvFingerprint | None = None
    if env_app or env_clr or env_os:
        env_fp = EnvFingerprint(app_version=env_app, clr_version=env_clr, os_version=env_os)
    last_theme: str | None = current_game if current_game != "unknown" else None
    return ParseResult(
        incidents,
        nodes,
        val_stats,
        parsed_machine_id,
        env_fp,
        onehand_version,
        last_theme,
    )


def parse_log_file_safe(
    path: Path,
    *,
    scan_start_time: datetime | None = None,
    scan_end_time: datetime | None = None,
) -> ParseResult:
    """Wrapper that never raises; logs unexpected failures."""
    try:
        return parse_log_file(
            path,
            scan_start_time=scan_start_time,
            scan_end_time=scan_end_time,
        )
    except Exception:
        logger.exception("Unexpected error parsing %s", path)
        return ParseResult([], [], None, None, None, None, None)


def read_log_context(
    path: Path,
    center_line: int,
    *,
    context_before: int = 8,
    context_after: int = 96,
) -> str:
    """
    Read a numbered slice of a log file around ``center_line`` (1-based).

    Used by the GUI inspector to show stack traces without loading the whole file.
    Tries the same encodings and retry policy as normal parsing.
    """
    start = max(1, center_line - context_before)
    end = center_line + context_after
    lines_out: list[str] = []
    try:
        for lineno, raw in enumerate(_read_text_lines(path), start=1):
            if lineno < start:
                continue
            if lineno > end:
                break
            text = raw.rstrip("\r\n")
            if len(text) > MAX_LINE_LENGTH:
                text = text[:MAX_LINE_LENGTH] + "…"
            lines_out.append(f"{lineno:6d} | {text}")
    except OSError as e:
        return f"(Could not read file: {path}\n{e})"
    if not lines_out:
        return f"(No lines in range {start}–{end} for {path})"
    return "\n".join(lines_out)
