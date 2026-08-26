"""Geometry checks for the Slot Roulette (OneHand RouletteGame) surface."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import pytest

from automation.roulette_slot_areas import (
    COL_X,
    LAYOUT_SLOT,
    NUMBER_AREAS_PX,
    OUTSIDE_AREAS_PX,
    PANEL_Y,
    ROW_Y,
    UI_AREAS_PX,
    ZERO_X,
)
from automation.roulette_surface import surface_for

REGISTRY = Path("automation") / "layouts" / "slot_hitboxes.json"


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_surface_registry_lookup():
    assert surface_for("slot") is LAYOUT_SLOT
    assert LAYOUT_SLOT.skin == "RouletteGame"
    assert LAYOUT_SLOT.client_w == 1920
    assert LAYOUT_SLOT.client_h == 3240
    assert PANEL_Y == 2160


def test_board_has_every_european_spot():
    assert len(NUMBER_AREAS_PX) == 37
    assert set(NUMBER_AREAS_PX) == {"0", *(str(n) for n in range(1, 37))}
    assert "00" not in NUMBER_AREAS_PX
    assert len(OUTSIDE_AREAS_PX) == 12


def test_number_cells_sit_in_the_expected_grid_slot():
    assert NUMBER_AREAS_PX["3"] == (COL_X[0], ROW_Y[0], COL_X[1], ROW_Y[1])
    assert NUMBER_AREAS_PX["2"] == (COL_X[0], ROW_Y[1], COL_X[1], ROW_Y[2])
    assert NUMBER_AREAS_PX["1"] == (COL_X[0], ROW_Y[2], COL_X[1], ROW_Y[3])
    assert NUMBER_AREAS_PX["36"] == (COL_X[11], ROW_Y[0], COL_X[12], ROW_Y[1])
    assert NUMBER_AREAS_PX["0"] == (ZERO_X[0], ROW_Y[0], ZERO_X[1], ROW_Y[3])


def test_dozens_sit_below_the_grid():
    assert OUTSIDE_AREAS_PX["1-12"][1] >= NUMBER_AREAS_PX["1"][3]
    assert OUTSIDE_AREAS_PX["1-12"][0] == COL_X[0]
    assert OUTSIDE_AREAS_PX["1-12"][2] == COL_X[4]
    assert OUTSIDE_AREAS_PX["25-36"][2] == COL_X[12]


def test_even_money_row_covers_the_grid_width_without_gaps():
    order = ("1-18", "EVEN", "RED", "BLACK", "ODD", "19-36")
    boxes = [OUTSIDE_AREAS_PX[name] for name in order]
    assert boxes[0][0] == COL_X[0]
    assert boxes[-1][2] == COL_X[12]
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] == right[0], "even-money boxes must tile without gaps"


def test_no_box_overlaps_another_in_the_same_group():
    for group, boxes in (
        ("numbers", NUMBER_AREAS_PX),
        ("outside", OUTSIDE_AREAS_PX),
        ("ui", UI_AREAS_PX),
    ):
        clashes = [
            (m, n) for (m, a), (n, b) in combinations(boxes.items(), 2) if _overlaps(a, b)
        ]
        assert clashes == [], f"slot {group} boxes overlap: {clashes}"
    clashes = [
        (m, n)
        for m, a in UI_AREAS_PX.items()
        for n, b in {**NUMBER_AREAS_PX, **OUTSIDE_AREAS_PX}.items()
        if _overlaps(a, b)
    ]
    assert clashes == [], f"slot chrome overlaps the cloth: {clashes}"


def test_show_wins_sits_below_the_zero_column():
    # Earlier draft put SHOW_WINS beside the zero tip and overlapped "0".
    assert UI_AREAS_PX["SHOW_WINS"][1] >= NUMBER_AREAS_PX["0"][3]
    assert UI_AREAS_PX["HOT_COLD"][1] >= NUMBER_AREAS_PX["0"][3]
    assert UI_AREAS_PX["HOT_COLD"][1] >= UI_AREAS_PX["SHOW_WINS"][3] - 5


def test_more_games_is_forbidden():
    assert "MORE_GAMES" in LAYOUT_SLOT.forbidden


def test_help_overlay_controls_do_not_overlap():
    boxes = LAYOUT_SLOT.overlays["help"]
    assert set(boxes) == {"HELP_PANEL", "HELP_PREV", "HELP_BACK", "HELP_NEXT"}
    nav = ("HELP_PREV", "HELP_BACK", "HELP_NEXT")
    clashes = [
        (m, n)
        for (m, a), (n, b) in combinations(((k, boxes[k]) for k in nav), 2)
        if _overlaps(a, b)
    ]
    assert clashes == []
    assert "HELP_PANEL" in LAYOUT_SLOT.info_only


def test_hitbox_registry_matches_surface():
    assert REGISTRY.is_file(), "run: python -m automation.roulette_slot_full_map"
    data = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    assert data["layout_id"] == "slot"
    assert data["client"] == {"width": 1920, "height": 3240}
    buttons = data["buttons"]
    assert set(NUMBER_AREAS_PX).issubset(buttons)
    assert set(OUTSIDE_AREAS_PX).issubset(buttons)
    assert set(UI_AREAS_PX).issubset(buttons)
    # Click centres land inside their own boxes.
    for name, box in {**NUMBER_AREAS_PX, **OUTSIDE_AREAS_PX, **UI_AREAS_PX}.items():
        entry = buttons[name]
        cx = entry["click_center_px"]["x"]
        cy = entry["click_center_px"]["y"]
        assert box[0] <= cx <= box[2], name
        assert box[1] <= cy <= box[3], name


def test_click_centres_resolve_from_store():
    from automation.roulette_layout_store import resolve_hitbox_center

    for name in ("0", "17", "SPIN", "chip_20", "DOUBLE"):
        pt = resolve_hitbox_center(name, layout_id="slot")
        assert pt is not None, name
        assert 0.0 <= pt.x_pct <= 100.0, name
        # Cloth / chrome live on the bottom panel of the 3240 client.
        assert pt.y_pct >= 60.0, name


@pytest.mark.parametrize("name", ["0", "17", "1-12", "SPIN"])
def test_surface_build_hitbox_fields(name):
    fields = LAYOUT_SLOT.build_hitbox_fields(name)
    assert fields is not None
    assert fields["client"] == {"width": 1920, "height": 3240}


def test_racetrack_has_european_pockets():
    race = LAYOUT_SLOT.overlays["racetrack"]
    pockets = {k.removeprefix("race_") for k in race if k.startswith("race_")}
    assert pockets == {"0", *{str(n) for n in range(1, 37)}}
    assert "00" not in pockets
    assert "RACE_SERIES_5_8" in race


def test_double_not_repeat():
    assert "DOUBLE" in UI_AREAS_PX
    assert "REPEAT" not in UI_AREAS_PX
