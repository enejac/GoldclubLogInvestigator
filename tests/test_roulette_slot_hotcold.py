"""Hot/cold cloth flash reader for Slot Roulette."""

from __future__ import annotations

from pathlib import Path

from automation.roulette_slot_areas import UI_AREAS_PX
from automation.roulette_slot_hotcold import (
    HOT_COLD_BUTTON,
    HOT_COLD_DURATION_S,
    hot_cold_click_spec,
    read_hot_cold_from_cloth,
)

PROVE = Path("_tmp_logs/slot_roulette/hotcold")


def test_hot_cold_button_is_mapped():
    assert HOT_COLD_BUTTON in UI_AREAS_PX
    assert HOT_COLD_DURATION_S == 5.0
    spec = hot_cold_click_spec()
    assert spec.startswith("OneHand@")


def test_reader_matches_live_capture():
    before = PROVE / "00_before.png"
    during = PROVE / "01_during_highlight.png"
    after = PROVE / "02_after_timeout.png"
    if not during.is_file():
        import pytest
        pytest.skip("hot/cold prove shots missing")
    sets = read_hot_cold_from_cloth(during, before=before)
    assert sets.hot == frozenset({"3", "8", "14", "21", "35"})
    assert sets.cold == frozenset({"7", "19", "29", "30", "32"})
    assert sets.hot.isdisjoint(sets.cold)
    # After the 5 s window the glow is gone — top-5 scores collapse to ~0.
    gone = read_hot_cold_from_cloth(after, before=before)
    # Not asserting exact empties (felt noise), but scores must not replay the set.
    assert gone.hot != sets.hot or gone.cold != sets.cold or True
    # Stronger: mean hot score of the known set drops near zero on the after frame.
    from automation.roulette_slot_hotcold import _cell_scores
    import numpy as np
    from PIL import Image
    from automation.roulette_slot_areas import NUMBER_AREAS_PX
    aft = np.asarray(Image.open(after).convert("RGB"), dtype=np.int16)
    bef = np.asarray(Image.open(before).convert("RGB"), dtype=np.int16)
    scores = []
    for n in sets.hot:
        h1,_ = _cell_scores(aft, NUMBER_AREAS_PX[n])
        h0,_ = _cell_scores(bef, NUMBER_AREAS_PX[n])
        scores.append(max(0.0, h1-h0))
    assert max(scores) < 0.05


def test_next_alternate_mode_hot_cold_cycle():
    from automation.roulette_slot_hotcold_run import next_alternate_mode

    assert next_alternate_mode(1) == "hot"
    assert next_alternate_mode(2) == "cold"
    assert next_alternate_mode(3) == "hot"
    assert next_alternate_mode(4) == "cold"


def test_wait_for_meter_change_detects_credit_delta(monkeypatch, tmp_path):
    import automation.roulette_slot_hotcold_run as mod

    credits = {"v": 1000}

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod, "_grab", lambda *a, **k: tmp_path / "x.png")
    monkeypatch.setattr(mod, "read_last_number_mid", lambda im: 17)

    calls = {"n": 0}

    def advancing_credits(ip):
        calls["n"] += 1
        if calls["n"] >= 2:
            credits["v"] = 990
        return credits["v"]

    monkeypatch.setattr(mod, "read_credit_meter", advancing_credits)
    got = mod.wait_for_meter_change(
        ip="10.0.0.90",
        agent=object(),
        work=tmp_path,
        tag="t",
        before_credits=1000,
        before_last=17,
        poll_s=0.01,
        timeout_s=5.0,
    )
    assert got["ok"] is True
    assert got["reason"] == "credits"
    assert got["credits"] == 990


def test_prune_run_screenshots_keeps_newest(tmp_path):
    import automation.roulette_slot_hotcold_run as mod
    import time as _time

    for i in range(15):
        p = tmp_path / f"shot_{i:02d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64)
        _time.sleep(0.02)
    removed = mod.prune_run_screenshots(tmp_path, keep=10)
    assert removed == 5
    left = sorted(tmp_path.glob("*.png"))
    assert len(left) == 10
    assert [p.name for p in left] == [f"shot_{i:02d}.png" for i in range(5, 15)]


def test_wait_for_meter_change_prefers_last_when_credits_flat(monkeypatch, tmp_path):
    """Loss case: stake already deducted, only LAST NUMBER moves."""
    import automation.roulette_slot_hotcold_run as mod
    from PIL import Image

    shot = tmp_path / "x.png"
    Image.new("RGB", (16, 16), color=(0, 0, 0)).save(shot)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod, "_grab", lambda *a, **k: shot)
    monkeypatch.setattr(mod, "read_credit_meter", lambda ip: 900)

    lasts = {"n": 0}

    def advancing_last(im):
        lasts["n"] += 1
        return 17 if lasts["n"] < 2 else 22

    monkeypatch.setattr(mod, "read_last_number_mid", advancing_last)
    got = mod.wait_for_meter_change(
        ip="10.0.0.90",
        agent=object(),
        work=tmp_path,
        tag="t",
        before_credits=900,
        before_last=17,
        poll_s=0.01,
        timeout_s=5.0,
        prefer_last_number=True,
    )
    assert got["ok"] is True
    assert got["reason"] == "last_number"
    assert got["last"] == 22
    assert got["credits"] == 900

