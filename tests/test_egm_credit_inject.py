"""Unit tests for product-aware credit inject routing (no live cabinet)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from automation import egm_credit_inject as eci


def test_script_paths_exist_in_repo():
    assert eci.SLOT_AFT.is_file()
    assert eci.BILL_INJECT.is_file()
    assert eci.DALLAS_SLOT.is_file()
    assert eci.ROULETTE_AFT.is_file() or eci.ROULETTE_AFT_LAB.is_file()


def test_ensure_credits_skips_inject_when_balance_ok():
    fake = eci.CreditRead(kind="slot", credits=200_000, source="device_manager")
    with (
        patch.object(eci, "detect_live_egm_kind", return_value="slot"),
        patch.object(eci, "read_credits", return_value=fake),
        patch.object(eci, "inject_bill") as bill,
        patch.object(eci, "inject_aft") as aft,
    ):
        ok, msg, after = eci.ensure_credits("10.0.0.90", min_credits=50_000)
    assert ok is True
    assert after.credits == 200_000
    assert "balance ok" in msg
    bill.assert_not_called()
    aft.assert_not_called()


def test_slot_prefer_bill_then_aft():
    low = eci.CreditRead(kind="slot", credits=10, source="device_manager")
    high = eci.CreditRead(kind="slot", credits=100_000, source="device_manager")
    reads = [low, high]
    with (
        patch.object(eci, "detect_live_egm_kind", return_value="slot"),
        patch.object(eci, "read_credits", side_effect=lambda *a, **k: reads.pop(0)),
        patch.object(eci, "inject_bill", return_value=(True, "bill Slot exit=0")) as bill,
        patch.object(eci, "inject_aft", return_value=(True, "aft slot exit=0")) as aft,
        patch.object(eci.time, "sleep"),
    ):
        ok, msg, after = eci.ensure_credits(
            "10.0.0.90", min_credits=50_000, aft_cents=100_000, settle_sec=0
        )
    assert ok is True
    assert after.credits == 100_000
    bill.assert_called_once()
    aft.assert_not_called()
    assert "bill Slot" in msg


def test_roulette_prefer_aft_then_bill():
    low = eci.CreditRead(kind="roulette", credits=10, source="middleware_8090")
    high = eci.CreditRead(kind="roulette", credits=100_000, source="middleware_8090")
    reads = [low, high]
    with (
        patch.object(eci, "detect_live_egm_kind", return_value="roulette"),
        patch.object(eci, "read_credits", side_effect=lambda *a, **k: reads.pop(0)),
        patch.object(eci, "inject_aft", return_value=(True, "aft roulette exit=0")) as aft,
        patch.object(eci, "inject_bill", return_value=(True, "bill Roulette exit=0")) as bill,
        patch.object(eci.time, "sleep"),
    ):
        ok, msg, after = eci.ensure_credits(
            "10.0.0.90", min_credits=50_000, aft_cents=100_000, settle_sec=0
        )
    assert ok is True
    assert after.credits == 100_000
    aft.assert_called_once()
    bill.assert_not_called()
    assert "aft roulette" in msg


def test_inject_aft_picks_slot_script(tmp_path: Path):
    log = tmp_path / "aft.log"
    with patch.object(eci, "_run_ps", return_value=0) as run:
        ok, msg = eci.inject_aft("10.0.0.90", kind="slot", amount_cents=1000, log=log)
    assert ok is True
    assert run.call_args.args[0] == eci.SLOT_AFT
    assert "-Send" in run.call_args.args[1]
    assert "-nr" in run.call_args.args[1]


def test_inject_aft_picks_roulette_script(tmp_path: Path):
    with patch.object(eci, "_run_ps", return_value=0) as run:
        ok, _msg = eci.inject_aft("10.0.0.90", kind="roulette", amount_cents=1000)
    assert ok is True
    script = run.call_args.args[0]
    assert script in (eci.ROULETTE_AFT, eci.ROULETTE_AFT_LAB)

def test_inject_aft_passes_transfer_type_flag(tmp_path: Path):
    log = tmp_path / "aft.log"
    with patch.object(eci, "_run_ps", return_value=0) as run:
        ok, msg = eci.inject_aft(
            "10.0.0.90",
            kind="slot",
            amount_cents=1000,
            transfer_type="cashable",
            log=log,
        )
    assert ok is True
    args = run.call_args.args[1]
    assert "-c" in args
    assert "-nr" not in args

