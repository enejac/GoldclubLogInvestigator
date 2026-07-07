import re

from automation.cabinet_preflight import (
    MULTIGAMER_TUTANKHAMEN_TILE_PCT,
    cabinet_on_game_selector,
    cabinet_has_tutankhamen_loaded,
    merge_input_scripts,
    read_cabinet_balance_credits,
    script_for_multigamer_select_tutankhamen,
    tutankhamen_tile_click_target,
)
from automation.input_script import script_for_forced_combo


def _patch_tail(monkeypatch, lines: list[str]) -> None:
    import automation.cabinet_preflight as mod

    def fake_tail(_path, *, max_lines=400):
        return lines[-max_lines:]

    monkeypatch.setattr(mod, "_tail_lines", lambda _ip, max_lines=400: fake_tail(None, max_lines=max_lines))
    monkeypatch.setattr(mod, "_latest_slotlog", lambda _ip: mod.Path("fake.log"))


def test_tutankhamen_tile_coords() -> None:
    assert tutankhamen_tile_click_target() == "OneHand@39.0,47.0"
    assert MULTIGAMER_TUTANKHAMEN_TILE_PCT == (39.0, 47.0)


def test_multigamer_select_script_clicks_tile() -> None:
    script = script_for_multigamer_select_tutankhamen()
    clicks = [s for s in script["steps"] if s.get("type") == "click_window"]
    assert len(clicks) == 1
    assert clicks[0]["value"] == tutankhamen_tile_click_target()


def test_on_game_selector_when_textgameselect_active(monkeypatch) -> None:
    _patch_tail(
        monkeypatch,
        [
            "INFO Information item removed: ['TextGameSelect'] 'Select A Game'",
            "INFO Information item added: ['TextGameSelect'] 'Select A Game'",
        ],
    )
    assert cabinet_on_game_selector("10.0.0.90") is True


def test_not_on_selector_when_theme_loaded(monkeypatch) -> None:
    _patch_tail(
        monkeypatch,
        [
            "INFO Information item added: ['TextGameSelect'] 'Select A Game'",
            "INFO Information item removed: ['TextGameSelect'] 'Select A Game'",
            "INFO LoadTheme take: [00:00:01] ThemeName [ TutankhamenGSBHW ]",
        ],
    )
    assert cabinet_on_game_selector("10.0.0.90") is False
    assert cabinet_has_tutankhamen_loaded("10.0.0.90") is True


def test_in_game_when_loadtheme_aged_out(monkeypatch) -> None:
    _patch_tail(
        monkeypatch,
        [
            "INFO Information item added: ['TextGameSelect'] 'Select A Game'",
            "INFO Information item removed: ['TextGameSelect'] 'Select A Game'",
            "INFO HWController - Force HW Tick",
            "INFO *** GAME STARTED *** Game no. 5365 [Total bet:20] (TutankhamenGSBHW)",
            "INFO ::.. GAME ENDED ..:: Game no. 5365 [CreditStatus: 759265]",
        ],
    )
    assert cabinet_has_tutankhamen_loaded("10.0.0.90") is True


def test_multigamer_select_script_no_forbidden_keys() -> None:
    script = script_for_multigamer_select_tutankhamen()
    steps = script["steps"]
    assert steps[0]["type"] == "focus_process"
    assert steps[1]["value"] == tutankhamen_tile_click_target()
    keys = [s.get("value") for s in steps if s.get("type") == "key"]
    assert "E" not in keys
    assert "ESC" not in keys


def test_forced_combo_with_game_select_prepends_steps() -> None:
    base = script_for_forced_combo(combo_text="0|1|2", include_game_select=False)
    full = script_for_forced_combo(combo_text="0|1|2", include_game_select=True)
    assert len(full["steps"]) > len(base["steps"])
    select_clicks = [s for s in full["steps"][:5] if s.get("type") == "click_window"]
    assert len(select_clicks) == 1
    assert select_clicks[0]["value"] == tutankhamen_tile_click_target()
    assert any(s.get("value") == "F11" for s in full["steps"] if s.get("type") == "key")


def test_read_balance_from_promo_bucket(monkeypatch) -> None:
    _patch_tail(
        monkeypatch,
        [
            "INFO Aurum promo credit state increased to 100000",
            "INFO Cashless In: $1,000.00",
        ],
    )
    assert read_cabinet_balance_credits("10.0.0.90") == 100_000


def test_merge_input_scripts() -> None:
    a = {"defaultKeyDelayMs": 40, "steps": [{"type": "sleep", "ms": 1}]}
    b = {"steps": [{"type": "key", "value": "F11"}]}
    merged = merge_input_scripts(a, b)
    assert merged["defaultKeyDelayMs"] == 40
    assert len(merged["steps"]) == 2
