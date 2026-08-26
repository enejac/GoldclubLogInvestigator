from automation.roulette_layout import NUMBER_SPOTS, SPIN_BUTTON
from automation.roulette_layout_store import resolve_target
from automation.roulette_script import script_for_random_bets_and_spin


def test_american_numbers_complete() -> None:
    # 0..36 plus synthetic key 37 for double-zero.
    assert set(range(37)).issubset(NUMBER_SPOTS)
    assert 37 in NUMBER_SPOTS
    assert NUMBER_SPOTS[37].name == "00"


def test_random_script_uses_clicks_and_spin() -> None:
    script = script_for_random_bets_and_spin(min_bets=3, max_bets=3, seed=1)
    steps = script["steps"]
    assert any(s.get("type") == "focus_process" for s in steps)
    clicks = [
        s
        for s in steps
        if s.get("type") in ("click_window", "click_post", "click_post_sync")
    ]
    assert len(clicks) >= 4  # chip + 3 bets (+ spin retries)
    # The script clicks the calibrated START centre, not the geometric default.
    spin_spec = resolve_target(SPIN_BUTTON).as_spec()
    assert any(spin_spec in (s.get("value") or "") for s in clicks)
    assert "meta" in script
    assert len(script["meta"]["bets"]) == 3


def test_random_script_post_mode() -> None:
    script = script_for_random_bets_and_spin(min_bets=2, max_bets=2, seed=2, click_mode="post")
    types = {s.get("type") for s in script["steps"]}
    assert "click_post" in types or "click_post_sync" in types
