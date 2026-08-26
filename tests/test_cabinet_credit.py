"""Unit tests for cabinet_credit (no AFT inject in LogInvestigator)."""

from __future__ import annotations

from unittest.mock import patch

from automation.cabinet_credit import ensure_cabinet_balance, run_aft_credit_transfer


def test_ensure_balance_skips_transfer_when_sufficient():
    with patch("automation.cabinet_credit.read_cabinet_balance_credits", return_value=5000):
        ok, msg, bal = ensure_cabinet_balance("10.0.0.90", min_credits=80)
    assert ok is True
    assert bal == 5000
    assert "balance ok" in msg


def test_ensure_balance_does_not_inject_when_low():
    with patch("automation.cabinet_credit.read_cabinet_balance_credits", return_value=0):
        ok, msg, bal = ensure_cabinet_balance("10.0.0.90", min_credits=80)
    assert ok is False
    assert bal == 0
    assert "AFT inject is not available" in msg


def test_run_aft_credit_transfer_disabled():
    ok, msg = run_aft_credit_transfer("10.0.0.90")
    assert ok is False
    assert "not available" in msg