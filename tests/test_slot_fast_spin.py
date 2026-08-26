"""Fast-mode bet+SPIN step ordering for max-throughput soak."""

from __future__ import annotations

import automation.roulette_slot_hotcold_run as hc


def test_fast_bet_and_spin_ends_with_short_spin_delay():
    prev = hc.FAST_MODE
    try:
        hc.FAST_MODE = True
        steps = hc.bet_and_spin_steps(["17", "20", "32", "5", "8"])
        spin_val = hc._click("SPIN", hc.FAST_SPIN_MS)["value"]
    finally:
        hc.FAST_MODE = prev
    assert steps[0]["type"] == "focus_process"
    assert steps[-1]["type"] == "click"
    assert steps[-1]["value"] == spin_val
    assert int(steps[-1]["ms"]) == hc.FAST_SPIN_MS
    assert int(steps[-1]["ms"]) <= 120
    bet_clicks = [s for s in steps if s["type"] == "click"]
    assert len(bet_clicks) == 1 + 1 + 5 + 1  # cancel, chip, 5 targets, spin


def test_normal_mode_still_spins_in_combined_steps():
    prev = hc.FAST_MODE
    try:
        hc.FAST_MODE = False
        steps = hc.bet_and_spin_steps(["17"])
    finally:
        hc.FAST_MODE = prev
    assert steps[-1]["type"] == "click"
    assert int(steps[-1]["ms"]) >= 200
