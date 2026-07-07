"""Background filter index rebuild for large incident lists.

Matched indices are collapsed into :class:`gui.view_model.DisplayRow` in the
view model (see ``collapse_consecutive_incidents_to_rows``); this module only
produces the raw ``list[int]`` filter order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from timeline_engine import parse_iso_timestamp_sort_key

if TYPE_CHECKING:
    from parser import Incident


@dataclass(frozen=True, slots=True)
class QuickFilterSnapshot:
    """Additive OR chips: if none active, all incidents pass; else match any active chip."""

    critical: bool = False
    warn: bool = False
    math_fails: bool = False
    drift: bool = False
    known_issues: bool = False

    def any_active(self) -> bool:
        return (
            self.critical
            or self.warn
            or self.math_fails
            or self.drift
            or self.known_issues
        )


from parser_rules import match_known_issue


def quick_filter_match(inc: Incident, q: QuickFilterSnapshot) -> bool:
    if not q.any_active():
        return True
    snip = (inc.line_snippet or "").upper()
    et = (inc.error_type or "")
    et_u = et.upper()
    if q.critical:
        if inc.severity == "CRITICAL":
            return True
        if "FATAL" in snip or "FATAL" in et_u:
            return True
    if q.warn:
        if inc.severity == "MEDIUM":
            return True
        if inc.severity == "WARN":
            return True
        if "WARN" in snip or "WARNING" in snip:
            return True
    if q.math_fails:
        if getattr(inc, "validation_status", None) == "FAIL":
            return True
        if et == "CRITICAL MATH DISCREPANCY":
            return True
    if q.drift:
        if et == "SYSTEM DRIFT DETECTED":
            return True
    if q.known_issues:
        if match_known_issue(inc.line_snippet or "", inc.severity or "") is not None:
            return True
    return False


def _incident_blob(inc: Incident) -> str:
    ts_part = (
        inc.timestamp.isoformat()
        if inc.timestamp is not None
        else ""
    )
    parts = [
        ts_part,
        inc.game,
        inc.severity,
        inc.error_type,
        inc.probable_cause,
        inc.log_file_path,
        str(inc.line_number),
        inc.line_snippet,
        str(inc.first_cause_line or ""),
        inc.first_cause_snippet or "",
        getattr(inc, "validation_status", "") or "",
        getattr(inc, "validation_detail", "") or "",
    ]
    return " ".join(parts).lower()


def _timeline_match(
    inc: Incident,
    t0: float | None,
    t1: float | None,
) -> bool:
    if t0 is None or t1 is None:
        return True
    k = parse_iso_timestamp_sort_key(inc.timestamp)
    if k is None:
        return False
    return t0 <= k <= t1


def _brush_time_slice_match(
    inc: Incident,
    t0: float | None,
    t1: float | None,
) -> bool:
    """Session mini-timeline brush (same bounds test as state timeline window)."""
    return _timeline_match(inc, t0, t1)


def _session_match(inc: Incident, session_start_ts: float | None) -> bool:
    """If ``session_start_ts`` is set, drop incidents whose log time is strictly before it."""
    if session_start_ts is None:
        return True
    k = parse_iso_timestamp_sort_key(inc.timestamp)
    if k is None:
        return True
    return k >= session_start_ts


class FilterIndexEmitter(QObject):
    """Lives on the GUI thread; ``done`` is emitted from pool threads (queued delivery)."""

    done = Signal(list, int)


class _FilterRunnable(QRunnable):
    def __init__(
        self,
        incidents: list[Incident],
        needle: str,
        emitter: FilterIndexEmitter,
        seq: int,
        timeline_t0: float | None,
        timeline_t1: float | None,
        brush_t0: float | None,
        brush_t1: float | None,
        session_start_ts: float | None,
        quick_filters: QuickFilterSnapshot,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._incidents = incidents
        self._needle = needle.strip().lower()
        self._emitter = emitter
        self._seq = seq
        self._timeline_t0 = timeline_t0
        self._timeline_t1 = timeline_t1
        self._brush_t0 = brush_t0
        self._brush_t1 = brush_t1
        self._session_start_ts = session_start_ts
        self._quick_filters = quick_filters

    def run(self) -> None:
        out: list[int] = []
        for i, inc in enumerate(self._incidents):
            if not _session_match(inc, self._session_start_ts):
                continue
            if not quick_filter_match(inc, self._quick_filters):
                continue
            if not _timeline_match(inc, self._timeline_t0, self._timeline_t1):
                continue
            if not _brush_time_slice_match(inc, self._brush_t0, self._brush_t1):
                continue
            if self._needle and self._needle not in _incident_blob(inc):
                continue
            out.append(i)
        self._emitter.done.emit(out, self._seq)


def schedule_filter_build(
    pool: QThreadPool,
    incidents: list[Incident],
    needle: str,
    emitter: FilterIndexEmitter,
    seq: int,
    timeline_t0: float | None = None,
    timeline_t1: float | None = None,
    brush_t0: float | None = None,
    brush_t1: float | None = None,
    session_start_ts: float | None = None,
    quick_filters: QuickFilterSnapshot | None = None,
) -> None:
    qf = quick_filters or QuickFilterSnapshot()
    pool.start(
        _FilterRunnable(
            incidents,
            needle,
            emitter,
            seq,
            timeline_t0,
            timeline_t1,
            brush_t0,
            brush_t1,
            session_start_ts,
            qf,
        )
    )
