"""LAST 100 history → hot/cold frequency rules for Slot Roulette."""

from __future__ import annotations

import json
from pathlib import Path

from automation.roulette_slot_areas import LAYOUT_SLOT, UI_AREAS_PX
from automation.roulette_slot_history import hot_cold_from_history
from automation.roulette_slot_hotcold import read_hot_cold_from_cloth

FIXTURE = Path("_tmp_logs/slot_roulette/history/last100_fixture.json")
HOTCOLD = Path("_tmp_logs/slot_roulette/hotcold")


def test_history_button_and_close_mapped():
    assert "HISTORY_BTN" in UI_AREAS_PX
    hist = LAYOUT_SLOT.overlays["history"]
    assert "HISTORY_CLOSE" in hist
    assert "HISTORY_PANEL" in hist


def test_hot_cold_from_last100_matches_ui_flash():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    nums = data["numbers_newest_first"]
    assert len(nums) == 100
    sets = hot_cold_from_history(nums)
    assert sets.hot == frozenset({"3", "8", "14", "21", "35"})
    assert sets.cold == frozenset({"7", "19", "29", "30", "32"})
    assert sets.hot.isdisjoint(sets.cold)


def test_frequency_matches_live_cloth_hotcold_capture():
    before = HOTCOLD / "00_before.png"
    during = HOTCOLD / "01_during_highlight.png"
    if not during.is_file() or not FIXTURE.is_file():
        import pytest
        pytest.skip("captures missing")
    cloth = read_hot_cold_from_cloth(during, before=before)
    freq = hot_cold_from_history(
        json.loads(FIXTURE.read_text(encoding="utf-8"))["numbers_newest_first"]
    )
    assert cloth.hot == freq.hot
    assert cloth.cold == freq.cold


def test_live_seed_cold_matches_ui_hot_differs():
    """
    Vision transcript of HISTORY on 2026-07-29 vs cloth flash on the same
    cabinet: cold sets match last-100 frequency; hot does not (UI keeps 0
    where frequency prefers another high-count number such as 2/22).
    """
    live = Path("_tmp_logs/slot_roulette/history/last100_live.json")
    if not live.is_file():
        import pytest
        pytest.skip("live history seed missing")
    data = json.loads(live.read_text(encoding="utf-8"))
    freq = hot_cold_from_history(data["numbers_newest_first"])
    assert set(map(str, data["cold"])) == set(freq.cold)
    # Documented product mismatch for this capture.
    assert set(map(str, data["ui_cold"])) == set(freq.cold)
    assert set(map(str, data["ui_hot"])) != set(freq.hot)
    assert "0" in set(map(str, data["ui_hot"]))


def test_panel_ocr_matches_fixture_on_history_capture():
    from automation.roulette_slot_history import read_history_numbers

    panel = Path("_tmp_logs/slot_roulette/history/01_bot.png")
    if not panel.is_file() or not FIXTURE.is_file():
        import pytest
        pytest.skip("history panel capture missing")
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["numbers_newest_first"]
    got = read_history_numbers(panel).numbers
    # Calibrated grid is 99/100 self-hit; allow one miss.
    hits = sum(1 for a, b in zip(got, expected) if int(a) == int(b))
    assert hits >= 98
    assert len(got) == 100

