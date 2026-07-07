"""Fleet log clock drift helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from network.fleet_scanner import (
    _parse_line_timestamp_utc,
    _read_last_nonempty_line,
    check_clock_drift,
)


def test_parse_line_timestamp_utc() -> None:
    config.compile_patterns()
    line = "2026-03-24T12:00:00.500+00:00 INFO  [SlotMachine] ping"
    dt = _parse_line_timestamp_utc(line)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.month == 3 and dt.day == 24


def test_read_last_nonempty_line(tmp_path: Path) -> None:
    p = tmp_path / "a.log"
    p.write_text("first\n\n  \nlast line here\n", encoding="utf-8")
    assert _read_last_nonempty_line(str(p)) == "last line here"


def test_check_clock_drift_uses_newest_log_and_last_line(tmp_path: Path) -> None:
    config.compile_patterns()
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    ts = old.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "older.log").write_text(f"{ts} INFO old\n", encoding="utf-8")
    newer = datetime.now(timezone.utc) - timedelta(seconds=15)
    ts2 = newer.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    (tmp_path / "newer.log").write_text(f"noise\n{ts2} INFO tail\n", encoding="utf-8")
    drift = check_clock_drift(str(tmp_path))
    assert drift is not None
    assert abs(drift - 15.0) < 8.0  # allow test execution slack


def test_check_clock_drift_empty_dir(tmp_path: Path) -> None:
    assert check_clock_drift(str(tmp_path)) is None
