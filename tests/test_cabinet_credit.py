"""Unit tests for cabinet_credit (mocked AFT)."""

from __future__ import annotations

from unittest.mock import patch

from automation.cabinet_credit import ensure_cabinet_balance, run_aft_credit_transfer


def test_ensure_balance_skips_transfer_when_sufficient():
    with patch("automation.cabinet_credit.read_cabinet_balance_credits", return_value=5000):
        with patch("automation.cabinet_credit.run_aft_credit_transfer") as xfer:
            ok, msg, bal = ensure_cabinet_balance("10.0.0.90", min_credits=80)
    assert ok is True
    assert bal == 5000
    assert "balance ok" in msg
    xfer.assert_not_called()


def test_ensure_balance_transfers_when_zero():
    reads = [0, 0, 100_000]

    def _read(_ip):
        return reads.pop(0) if reads else 100_000

    with patch("automation.cabinet_credit.read_cabinet_balance_credits", side_effect=_read):
        with patch(
            "automation.cabinet_credit.run_aft_credit_transfer",
            return_value=(True, "sent"),
        ) as xfer:
            with patch("automation.cabinet_credit.time.sleep"):
                ok, msg, bal = ensure_cabinet_balance("10.0.0.90", min_credits=80)
    assert ok is True
    assert bal == 100_000
    xfer.assert_called_once()


def test_run_aft_missing_script(tmp_path, monkeypatch):
    monkeypatch.setattr("automation.cabinet_credit._repo_root", lambda: tmp_path)
    ok, msg = run_aft_credit_transfer("10.0.0.90")
    assert ok is False
    assert "missing" in msg