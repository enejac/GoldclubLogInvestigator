"""Aggressive random straight staking for max-throughput soak."""

from __future__ import annotations

from unittest.mock import patch

import automation.roulette_slot_hotcold_run as hc


def test_random_straight_targets_count_and_unique():
    with patch.object(hc, "slot_straight_ids", return_value=[str(i) for i in range(37)]):
        t = hc.random_straight_targets(5)
    assert len(t) == 5
    assert len(set(t)) == 5
    assert all(x.isdigit() for x in t)


def test_aggressive_bet_spin_uses_shorter_spin_ms():
    prev_f, prev_a = hc.FAST_MODE, hc.AGGRESSIVE_MODE
    try:
        hc.FAST_MODE = True
        hc.AGGRESSIVE_MODE = True
        steps = hc.bet_and_spin_steps(["1", "2", "3", "4", "5"])
    finally:
        hc.FAST_MODE = prev_f
        hc.AGGRESSIVE_MODE = prev_a
    assert int(steps[-1]["ms"]) == hc.AGGRESSIVE_SPIN_MS
    assert int(steps[-1]["ms"]) < hc.FAST_SPIN_MS
