"""Millisecond meter trace: recording, and the spread report built on it."""

from __future__ import annotations

import json

import pytest

from automation.meter_trace_report import load, split_rounds
from gui import meter_timing_trace as mt


@pytest.fixture(autouse=True)
def _no_leaked_tracer():
    mt.disable_tracing()
    yield
    mt.disable_tracing()


def test_tracing_is_off_by_default_and_costs_nothing() -> None:
    assert mt.tracing_enabled() is False
    assert mt.trace_path() is None
    # Must not raise or write anywhere when disabled.
    mt.trace_event("value_change", code="0000", column="machine")


def test_events_are_recorded_with_millisecond_offsets(tmp_path) -> None:
    target = tmp_path / "trace.jsonl"
    mt.enable_tracing(target)
    assert mt.tracing_enabled() is True

    mt.trace_event("round_open")
    mt.trace_event("value_change", code="0000", column="machine", old="1", new="2")
    mt.trace_event("flash_start", codes=["0000"])
    mt.disable_tracing()

    recs = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    kinds = [r["kind"] for r in recs]
    assert kinds == ["trace_open", "round_open", "value_change", "flash_start"]
    assert all(isinstance(r["t_ms"], (int, float)) for r in recs)
    # Monotonic, non-decreasing offsets.
    offsets = [r["t_ms"] for r in recs]
    assert offsets == sorted(offsets)
    change = recs[2]
    assert change["code"] == "0000" and change["new"] == "2"


def test_enabling_twice_keeps_the_first_file(tmp_path) -> None:
    first = mt.enable_tracing(tmp_path / "a.jsonl")
    second = mt.enable_tracing(tmp_path / "b.jsonl")
    assert first == second
    assert not (tmp_path / "b.jsonl").exists()


def test_env_var_off_values_do_not_enable(monkeypatch) -> None:
    for value in ("", "0", "false", "off"):
        monkeypatch.setenv(mt.ENV_VAR, value)
        assert mt.enable_from_environment() is None
        assert mt.tracing_enabled() is False


def test_env_var_accepts_an_explicit_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "from_env.jsonl"
    monkeypatch.setenv(mt.ENV_VAR, str(target))
    assert mt.enable_from_environment() == target
    assert target.exists()


# --- report -------------------------------------------------------------


def test_rounds_split_on_each_open(tmp_path) -> None:
    target = tmp_path / "t.jsonl"
    mt.enable_tracing(target)
    mt.trace_event("value_change", code="X", column="machine")  # before any round
    mt.trace_event("round_open")
    mt.trace_event("value_change", code="0000", column="machine")
    mt.trace_event("round_open")
    mt.trace_event("value_change", code="0001", column="sas_6f")
    mt.disable_tracing()

    rounds = split_rounds(load(target))
    assert len(rounds) == 3  # pre-round group, then two real rounds
    assert rounds[1][0]["kind"] == "round_open"
    assert rounds[2][0]["kind"] == "round_open"


def test_a_reseed_is_not_counted_as_meter_movement(tmp_path) -> None:
    """A reload blanks a column and refills it — that is not a moved meter."""
    target = tmp_path / "t.jsonl"
    mt.enable_tracing(target)
    mt.trace_event("round_open")
    mt.trace_event(
        "value_change", code="0000", column="machine", old="", new="500", increased=False
    )
    mt.trace_event(
        "value_change", code="0001", column="machine", old="500", new="600", increased=True
    )
    mt.disable_tracing()

    events = load(target)
    moved = [e for e in events if e.get("kind") == "value_change" and e.get("increased")]
    assert [e["code"] for e in moved] == ["0001"]
