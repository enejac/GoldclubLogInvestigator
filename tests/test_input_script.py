import re

from automation.input_script import (
    WINGRAPH_CONTENT_ROWS,
    WINGRAPH_FORBIDDEN_KEYS,
    combo_to_wingraph_grid,
    script_for_forced_combo,
    script_for_wingraph_symbol_indexes_spin,
    wingraph_cell_click_target,
    wingraph_close_click_target,
    wingraph_reset_click_target,
    wingraph_symbol_spin_click_target,
)


def test_combo_to_wingraph_grid_three_rows_only() -> None:
    grid = combo_to_wingraph_grid("0 0 0 0 0|1 1 1 1 1|2 2 2 2 2")
    assert len(grid) == 3
    assert grid == [
        [0, 0, 0, 0, 0],
        [1, 1, 1, 1, 1],
        [2, 2, 2, 2, 2],
    ]


def test_wingraph_cell_click_targets() -> None:
    assert wingraph_cell_click_target(0, 0).startswith("WinGraph@")
    assert wingraph_symbol_spin_click_target().startswith("WinGraph@")


def test_wingraph_script_uses_mouse_three_rows() -> None:
    grid = combo_to_wingraph_grid("0|1|2")
    assert len(grid) == WINGRAPH_CONTENT_ROWS
    script = script_for_wingraph_symbol_indexes_spin(grid)
    steps = script["steps"]
    assert any(s.get("value") == "F11" for s in steps if s.get("type") == "key")
    clicks = [s for s in steps if s.get("type") == "click_window"]
    assert len(clicks) >= 21  # preflight + 2x15 cell + SPIN x2 + Close
    assert clicks[-1]["value"] == wingraph_close_click_target()
    assert wingraph_symbol_spin_click_target() in [c["value"] for c in clicks]
    key_vals = [s["value"] for s in steps if s.get("type") == "key"]
    assert not any(k in WINGRAPH_FORBIDDEN_KEYS for k in key_vals)
    assert "E" not in key_vals
    assert not any(s.get("type") == "key" and s.get("value") == "TAB" for s in steps)
    click_vals = [s["value"] for s in clicks]
    assert wingraph_reset_click_target() in click_vals
    digit_text = [s["value"] for s in steps if s.get("type") == "text" and s["value"].isdigit()]
    # Script may reuse defaults / skip redundant digits; require each row id appears.
    assert digit_text.count("0") >= 5
    assert digit_text.count("1") >= 5
    assert digit_text.count("2") >= 5
    assert wingraph_close_click_target() in click_vals
    assert not any(s.get("type") == "key" and s.get("value") == "HOME" for s in steps)


def test_forced_combo_default_is_wingraph() -> None:
    script = script_for_forced_combo(combo_text="0|1|2")
    assert any(s.get("value") == "F11" for s in script["steps"] if s.get("type") == "key")


def test_forced_combo_reuse_wingraph_skips_f11() -> None:
    script = script_for_forced_combo(combo_text="1|2|3", open_f11=False)
    assert not any(s.get("value") == "F11" for s in script["steps"] if s.get("type") == "key")
    assert script["steps"][1]["type"] == "focus_window"