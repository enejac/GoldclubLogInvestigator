"""Unit tests for Accounting meter exercise planner (no live cabinet)."""

from __future__ import annotations

from automation.sas_meter_exercise import CANNOT_FORGE, STEP_EXPECT, _deltas
from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES


def test_step_expect_covers_driveable_families() -> None:
    assert "000B" in STEP_EXPECT["bill_200k"]
    assert "00A0" in STEP_EXPECT["aft_cashable"]
    assert "00A2" in STEP_EXPECT["aft_restricted"]
    assert "00A4" in STEP_EXPECT["aft_non_restricted"]
    assert "0005" in STEP_EXPECT["play_spins"]


def test_cannot_forge_lists_ticket_and_hopper_gaps() -> None:
    for code in ("0015", "006E", "0002", "001D", "0018"):
        assert code in CANNOT_FORGE


def test_deltas_only_nonzero() -> None:
    before = {c: "0" for c in DEFAULT_6F_VERIFY_POLL_CODES}
    after = dict(before)
    after["0000"] = "100"
    after["000B"] = "0"
    d = _deltas(before, after)
    assert len(d) == 1
    assert d[0]["code"] == "0000"
    assert d[0]["delta"] == 100
