"""Millisecond timeline of meter movement, for diagnosing paint pairing.

A game moves several meters at once, but the two sources arrive far apart: the
cabinet XML is a ~40 ms file read while a SAS capture takes seconds. Deciding
whether the table paints them *together* is impossible by eye — the whole
question is milliseconds wide. This records the timeline instead:

* ``round_open`` / ``round_close`` — Auto fetch round boundaries
* ``machine_landed`` / ``sas_landed`` — when each source arrived (and whether
  the Machine paint was deferred to wait for its pair)
* ``paint`` — a table rebuild
* ``value_change`` — one meter cell changing, with column, old and new value
* ``flash_start`` / ``flash_stop`` — the orange row pulse

Every record carries ``t_ms``, milliseconds since the trace opened, so the
spread between the first and last meter of one game is a subtraction. Read the
result with ``python -m automation.meter_trace_report``.

Off by default and gated behind a single module-global check, so a normal run
pays nothing. Turn it on with the ``SASVERIFY_METER_TRACE`` environment
variable (``1`` for the default path, or a path of your own).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

ENV_VAR = "SASVERIFY_METER_TRACE"

_TRACER: "MeterTimingTrace | None" = None


class MeterTimingTrace:
    """Append-only JSONL sink. One line per event, flushed immediately."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()
        self._fh = self.path.open("a", encoding="utf-8")
        self.write(
            "trace_open",
            {"wall": datetime.now().isoformat(timespec="milliseconds"), "pid": os.getpid()},
        )

    def write(self, kind: str, fields: dict) -> None:
        rec = {"t_ms": round((time.perf_counter() - self._t0) * 1000.0, 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, default=str)
        with self._lock:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except (ValueError, OSError):
                pass

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except (ValueError, OSError):
                pass


def default_trace_path() -> Path:
    """``meter_trace/meter_trace_<stamp>.jsonl`` beside the exe / repo root."""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / "meter_trace" / f"meter_trace_{stamp}.jsonl"


def enable_tracing(path: str | os.PathLike | None = None) -> Path:
    global _TRACER
    if _TRACER is not None:
        return _TRACER.path
    target = Path(path) if path else default_trace_path()
    _TRACER = MeterTimingTrace(target)
    return target


def disable_tracing() -> None:
    global _TRACER
    if _TRACER is not None:
        _TRACER.close()
        _TRACER = None


def tracing_enabled() -> bool:
    return _TRACER is not None


def trace_path() -> Path | None:
    return _TRACER.path if _TRACER is not None else None


def trace_event(kind: str, **fields) -> None:
    """Record one event. A no-op (one global read) when tracing is off."""
    tracer = _TRACER
    if tracer is None:
        return
    tracer.write(kind, fields)


def enable_from_environment() -> Path | None:
    """Honour ``SASVERIFY_METER_TRACE``: ``1``/``true`` or an explicit path."""
    raw = (os.environ.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    if raw.lower() in {"0", "false", "no", "off"}:
        return None
    if raw.lower() in {"1", "true", "yes", "on"}:
        return enable_tracing()
    return enable_tracing(raw)
