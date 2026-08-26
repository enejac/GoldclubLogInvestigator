"""
State machine timeline extraction from Goldclub-style logs.

Used from ``parser.parse_log_file`` in the same pass as incident detection (no second read).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_BONUS_JSON = _DATA_DIR / "RouletteBonusMath.json"

# Change MachineState from X to Y (tolerate extra spaces / punctuation)
RE_MACHINE_STATE = re.compile(
    r"Change\s+MachineState\s+from\s+(.+?)\s+to\s+(.+?)\s*$",
    re.IGNORECASE,
)

RE_GAME_STARTED = re.compile(
    r"\*{0,3}\s*GAME\s+STARTED\s*\*{0,3}",
    re.IGNORECASE,
)
RE_KEYTYPE = re.compile(r"KeyType\s*:\s*(\w+)", re.IGNORECASE)
RE_CREDIT_CHANGE = re.compile(
    r"(?:Aurum\s+)?(?:cashable\s+)?credit\s+.+?\s+(?:decreased|increased|changed)",
    re.IGNORECASE,
)


def _posix_assuming_utc(dt: datetime) -> float:
    """POSIX seconds, reading a naive timestamp as UTC like the rest of the app."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.astimezone(timezone.utc).timestamp()


def parse_iso_timestamp_sort_key(ts: datetime | str | None) -> float | None:
    """Return POSIX timestamp for sorting / duration, or ``None``."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return _posix_assuming_utc(ts)
    s = str(ts).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if config.TIMESTAMP_RE is not None:
        m = config.TIMESTAMP_RE.search(s)
        if m:
            frag = m.group(0).replace("Z", "+00:00")
            for candidate in (frag, s):
                try:
                    return _posix_assuming_utc(datetime.fromisoformat(candidate))
                except ValueError:
                    continue
    # Space-separated or other layouts not covered by ``config.TIMESTAMP_RE``.
    from parser import extract_datetime_from_line

    dt = extract_datetime_from_line(s)
    if dt is None:
        return None
    return _posix_assuming_utc(dt)


@dataclass(frozen=True, slots=True)
class StateNode:
    """One segment where the machine stayed in ``state_name`` until the next transition."""

    timestamp: str | None
    end_timestamp: str | None
    timestamp_sort_key: float
    end_timestamp_sort_key: float
    state_name: str
    previous_state: str | None
    duration_sec: float | None
    trigger_event: str | None
    line_number: int
    end_line_number: int | None
    log_file_path: str
    health: str  # "ok" | "warn" | "error"
    bonus_label: str | None
    incident_count: int = 0


@dataclass(frozen=True, slots=True)
class EnvFingerprint:
    """Runtime fingerprint from ``Logging::Log() Spawning v…`` (optional clr=/os= on same line)."""

    app_version: str | None = None
    clr_version: str | None = None
    os_version: str | None = None

    def any_set(self) -> bool:
        return bool(self.app_version or self.clr_version or self.os_version)


@dataclass
class ParseResult:
    """Single-pass parse output."""

    incidents: list[Any]
    state_nodes: list[StateNode]
    validation_stats: dict[str, Any] | None = None
    machine_id: str | None = None
    """Logged cabinet / machine id (e.g. gst20664) from first matching INFO line in Aurum.Services / SlotLog paths."""
    env_fingerprint: EnvFingerprint | None = None
    onehand_version: str | None = None
    """``SlotMachine v…`` from ``OneHand.MainFrm - SlotMachine v…`` (SlotLog / OneHand client lines)."""
    last_active_theme: str | None = None
    """Last non-unknown theme/game name after a full file parse (for dashboard)."""


def load_roulette_bonus_rules() -> list[tuple[str, str]]:
    """Return [(substring, display_label), ...] from RouletteBonusMath.json."""
    rules: list[tuple[str, str]] = []
    path = _BONUS_JSON
    if not path.is_file():
        logger.debug("Bonus config missing: %s", path)
        return rules
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("bonus_state_patterns", []):
            sub = str(item.get("substring", "")).strip()
            lab = str(item.get("display_label", sub)).strip()
            if sub:
                rules.append((sub, lab))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Could not load %s: %s", path, e)
    return rules


def _bonus_label_for_state(state_name: str, rules: list[tuple[str, str]]) -> str | None:
    for sub, lab in rules:
        if sub.lower() in state_name.lower():
            return lab
    return None


class StateTimelineBuilder:
    """Accumulates ``StateNode`` rows for one log file."""

    def __init__(self, log_file_path: str, bonus_rules: list[tuple[str, str]]) -> None:
        self._path = log_file_path
        self._bonus_rules = bonus_rules
        self.nodes: list[StateNode] = []
        self._pending: dict[str, Any] | None = None
        self._recent_triggers: list[str] = []
        self._last_sort_key: float = 0.0

    def on_line(self, lineno: int, line: str, ts: str | None) -> None:
        if RE_GAME_STARTED.search(line):
            self._recent_triggers.append(_trim_event(line, "GAME STARTED"))
        else:
            km = RE_KEYTYPE.search(line)
            if km:
                self._recent_triggers.append(f"KeyType:{km.group(1)}")
            elif RE_CREDIT_CHANGE.search(line):
                self._recent_triggers.append(_trim_event(line, "Credit"))

        m = RE_MACHINE_STATE.search(line.strip())
        if not m:
            return

        old_s = _clean_state_token(m.group(1))
        new_s = _clean_state_token(m.group(2))
        parsed_sort = parse_iso_timestamp_sort_key(ts)
        # A transition logged without a timestamp inherits the last known one:
        # falling back to 0.0 would date it to 1970 and drag the whole segment to
        # the front of every sorted timeline.
        sort_key = parsed_sort if parsed_sort is not None else self._last_sort_key
        if parsed_sort is not None:
            self._last_sort_key = parsed_sort

        if self._pending is not None:
            p = self._pending
            duration: float | None = None
            start_sort = p.get("start_sort")
            if parsed_sort is not None and p.get("start_parsed") and start_sort is not None:
                duration = max(0.0, parsed_sort - start_sort)
            bonus = _bonus_label_for_state(p["state_name"], self._bonus_rules)
            self.nodes.append(
                StateNode(
                    timestamp=p["start_ts"],
                    end_timestamp=ts,
                    timestamp_sort_key=start_sort if start_sort is not None else sort_key,
                    end_timestamp_sort_key=sort_key,
                    state_name=p["state_name"],
                    previous_state=p.get("prev_from_line"),
                    duration_sec=duration,
                    trigger_event=p.get("trigger"),
                    line_number=p["start_line"],
                    end_line_number=lineno - 1,
                    log_file_path=self._path,
                    health="ok",
                    bonus_label=bonus,
                    incident_count=0,
                )
            )

        trigger = " · ".join(self._recent_triggers) if self._recent_triggers else None
        self._recent_triggers = []
        self._pending = {
            "state_name": new_s,
            "prev_from_line": old_s,
            "start_ts": ts,
            "start_sort": sort_key,
            "start_parsed": parsed_sort is not None,
            "start_line": lineno,
            "trigger": trigger,
        }

    def finalize(self, last_line_no: int) -> None:
        if self._pending is None:
            return
        p = self._pending
        bonus = _bonus_label_for_state(p["state_name"], self._bonus_rules)
        start_sort = p.get("start_sort")
        sk = start_sort if start_sort is not None else self._last_sort_key
        self.nodes.append(
            StateNode(
                timestamp=p["start_ts"],
                end_timestamp=None,
                timestamp_sort_key=sk,
                end_timestamp_sort_key=sk,
                state_name=p["state_name"],
                previous_state=p.get("prev_from_line"),
                duration_sec=None,
                trigger_event=p.get("trigger"),
                line_number=p["start_line"],
                end_line_number=last_line_no,
                log_file_path=self._path,
                health="ok",
                bonus_label=bonus,
                incident_count=0,
            )
        )
        self._pending = None


def _trim_event(line: str, prefix: str) -> str:
    t = line.strip()
    return (prefix + ": " + t[:100]) if len(t) < 120 else (prefix + ": " + t[:100] + "…")


def _clean_state_token(s: str) -> str:
    return " ".join(s.split()).strip(" `\"'")[:200]


def merge_incidents_into_nodes(
    nodes: list[StateNode],
    incidents: list[Any],
) -> list[StateNode]:
    """Set ``health`` and ``incident_count`` from incidents pinned by line range + path."""
    if not nodes:
        return nodes

    out: list[StateNode] = []
    for n in nodes:
        end_ln = n.end_line_number if n.end_line_number is not None else n.line_number
        pinned = [
            inc
            for inc in incidents
            if getattr(inc, "log_file_path", "") == n.log_file_path
            and n.line_number <= getattr(inc, "line_number", -1) <= end_ln
        ]
        cnt = len(pinned)
        health = "ok"
        sev = {getattr(i, "severity", "") for i in pinned}
        if "CRITICAL" in sev:
            health = "error"
        elif "MEDIUM" in sev or "LOW" in sev:
            health = "warn"
        out.append(
            replace(n, health=health, incident_count=cnt),
        )
    return out
