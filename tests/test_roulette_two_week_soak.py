from datetime import timedelta, timezone
from pathlib import Path

from automation.roulette_two_week_soak import (
    DEFAULT_UNTIL,
    parse_until,
    resume_cycle_number,
)

SI = timezone(timedelta(hours=2))


def test_parse_until_naive_is_slovenia_cest():
    dt = parse_until("2026-08-17T07:30:00")
    assert dt.tzinfo is not None
    assert dt.astimezone(SI).hour == 7
    assert dt.day == 17 and dt.month == 8 and dt.year == 2026


def test_default_until_string():
    assert DEFAULT_UNTIL.startswith("2026-08-17")


def test_resume_cycle_number(tmp_path: Path):
    assert resume_cycle_number(tmp_path) == 0
    session = tmp_path / "session.jsonl"
    session.write_text(
        '{"cycle": 12, "ok": true}\n{"cycle": 55, "ok": false}\n{"cycle": 40}\n',
        encoding="utf-8",
    )
    assert resume_cycle_number(tmp_path) == 55