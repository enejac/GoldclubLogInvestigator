"""Geometry checks for the layout2 (Crycle) surface and its hitbox registry."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import pytest

from automation.roulette_bet_catalog import catalog
from automation.roulette_layout2_areas import COL_X, LAYOUT2, ROW_Y, ZERO_SPLIT_Y, ZERO_X
from automation.roulette_layout2_full_map import build_buttons, inside_anchor_boxes
from automation.roulette_layout_store import resolve_hitbox_center
from automation.roulette_surface import surface_for

REGISTRY = Path("automation") / "layouts" / "layout2_hitboxes.json"


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_surface_registry_lookup():
    assert surface_for("layout2") is LAYOUT2
    assert surface_for("layout2").skin == "futura_doublezeroCrycle"
    with pytest.raises(ValueError):
        surface_for("layout9")


def test_board_has_every_american_spot():
    assert len(LAYOUT2.numbers) == 38
    assert set(LAYOUT2.numbers) == {"0", "00", *(str(n) for n in range(1, 37))}
    assert len(LAYOUT2.outside) == 12


def test_number_cells_sit_in_the_expected_grid_slot():
    # Cloth rows top->bottom are 3,6,9.. / 2,5,8.. / 1,4,7..
    assert LAYOUT2.numbers["3"] == (COL_X[0], ROW_Y[0], COL_X[1], ROW_Y[1])
    assert LAYOUT2.numbers["2"] == (COL_X[0], ROW_Y[1], COL_X[1], ROW_Y[2])
    assert LAYOUT2.numbers["1"] == (COL_X[0], ROW_Y[2], COL_X[1], ROW_Y[3])
    assert LAYOUT2.numbers["36"] == (COL_X[11], ROW_Y[0], COL_X[12], ROW_Y[1])
    assert LAYOUT2.numbers["34"] == (COL_X[11], ROW_Y[2], COL_X[12], ROW_Y[3])
    # 00 takes the upper half of the zero column, 0 the lower half.
    assert LAYOUT2.numbers["00"] == (ZERO_X[0], ROW_Y[0], ZERO_X[1], ZERO_SPLIT_Y)
    assert LAYOUT2.numbers["0"] == (ZERO_X[0], ZERO_SPLIT_Y, ZERO_X[1], ROW_Y[3])


def test_dozens_sit_below_the_grid_unlike_layout1():
    layout1 = surface_for("layout1")
    # layout1 draws the dozens above the numbers, layout2 below them.
    assert layout1.outside["1-12"][3] <= layout1.numbers["3"][1]
    assert LAYOUT2.outside["1-12"][1] >= LAYOUT2.numbers["1"][3]
    # Each dozen spans exactly four number columns.
    assert LAYOUT2.outside["1-12"][0] == COL_X[0]
    assert LAYOUT2.outside["1-12"][2] == COL_X[4]
    assert LAYOUT2.outside["25-36"][2] == COL_X[12]


def test_even_money_row_covers_the_grid_width_without_gaps():
    order = ("1-18", "ODD", "RED", "BLACK", "EVEN", "19-36")
    boxes = [LAYOUT2.outside[name] for name in order]
    assert boxes[0][0] == COL_X[0]
    assert boxes[-1][2] == COL_X[12]
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] == right[0], "even-money boxes must tile without gaps"


@pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
def test_no_box_overlaps_another_in_the_same_group(layout_id):
    surface = surface_for(layout_id)
    for group in ("numbers", "outside", "ui"):
        boxes = getattr(surface, group)
        clashes = [
            (m, n) for (m, a), (n, b) in combinations(boxes.items(), 2) if _overlaps(a, b)
        ]
        assert clashes == [], f"{layout_id} {group} boxes overlap: {clashes}"
    clashes = [
        (m, n)
        for m, a in surface.ui.items()
        for n, b in surface.bet_areas_px.items()
        if _overlaps(a, b)
    ]
    assert clashes == [], f"{layout_id} chrome overlaps the cloth: {clashes}"


@pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
def test_no_two_controls_on_one_sub_screen_overlap(layout_id):
    # Sub-screens may cover the cloth — that is what they are for — but two
    # controls drawn side by side on the same screen must not share a pixel, or a
    # click has no single answer.
    surface = surface_for(layout_id)
    for screen, boxes in surface.overlays.items():
        clashes = [
            (m, n) for (m, a), (n, b) in combinations(boxes.items(), 2) if _overlaps(a, b)
        ]
        assert clashes == [], f"{layout_id} {screen} boxes overlap: {clashes}"


def test_racetrack_covers_the_wheel_in_wheel_order():
    pockets = {
        name: box
        for name, box in LAYOUT2.overlays["racetrack"].items()
        if name.startswith("race_")
    }
    assert len(pockets) == 38
    assert {p.removeprefix("race_") for p in pockets} == set(LAYOUT2.numbers)
    # 0 sits at the top of the oval and 00 directly opposite it at the bottom,
    # which is what pins the wheel order onto the capture.
    zero, double = pockets["race_0"], pockets["race_00"]
    assert zero[1] < double[1]
    assert abs((zero[0] + zero[2]) - (double[0] + double[2])) < 20
    # Neighbours on the wheel are neighbours on the oval.
    for a, b in (("race_0", "race_28"), ("race_00", "race_1"), ("race_17", "race_5")):
        ax, ay = (pockets[a][0] + pockets[a][2]) / 2, (pockets[a][1] + pockets[a][3]) / 2
        bx, by = (pockets[b][0] + pockets[b][2]) / 2, (pockets[b][1] + pockets[b][3]) / 2
        assert abs(ax - bx) + abs(ay - by) < 160, f"{a} and {b} are not adjacent"


def test_small_cloth_inside_the_oval_mirrors_the_big_one():
    mini = {
        name.removeprefix("mini_"): box
        for name, box in LAYOUT2.overlays["racetrack"].items()
        if name.startswith("mini_")
    }
    # Same spots as the big cloth, drawn small inside the oval.
    assert set(mini) == set(LAYOUT2.numbers) | set(LAYOUT2.outside)
    for name, small in mini.items():
        big = LAYOUT2.numbers.get(name) or LAYOUT2.outside[name]
        assert (small[2] - small[0]) < (big[2] - big[0]), f"mini_{name} is not smaller"
    # The zero column keeps 00 above 0, and the grid keeps 1 under 2 under 3.
    assert mini["00"][3] == mini["0"][1]
    assert mini["3"][3] == mini["2"][1] and mini["2"][3] == mini["1"][1]
    # Every pocket and every small-cloth spot is inside the oval's footprint.
    pockets = [b for n, b in LAYOUT2.overlays["racetrack"].items() if n.startswith("race_")]
    oval = (
        min(b[0] for b in pockets), min(b[1] for b in pockets),
        max(b[2] for b in pockets), max(b[3] for b in pockets),
    )
    for name, box in mini.items():
        assert oval[0] < box[0] and box[2] < oval[2], f"mini_{name} sticks out of the oval"
        assert oval[1] < box[1] and box[3] < oval[3], f"mini_{name} sticks out of the oval"


@pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
def test_click_centre_lands_inside_its_own_box(layout_id):
    surface = surface_for(layout_id)
    for name, box in surface.all_areas_px.items():
        target = resolve_hitbox_center(name, layout_id)
        assert target is not None, f"{layout_id} {name} has no click point"
        x = target.x_pct / 100 * surface.client_w
        y = target.y_pct / 100 * surface.client_h
        if name in surface.sliders:
            continue  # a slider aims at a value, not at the box centre
        assert box[0] <= x <= box[2], f"{layout_id} {name} click x outside box"
        assert box[1] <= y <= box[3], f"{layout_id} {name} click y outside box"


def test_slider_maps_values_to_the_measured_travel():
    lo = LAYOUT2.sliders["OPACITY_SLIDER"]["handle_min_x"]
    hi = LAYOUT2.sliders["OPACITY_SLIDER"]["handle_max_x"]
    assert LAYOUT2.slider_value_to_px("OPACITY_SLIDER", 0.0)[0] == lo
    assert LAYOUT2.slider_value_to_px("OPACITY_SLIDER", 1.0)[0] == hi
    mid = LAYOUT2.slider_value_to_px("OPACITY_SLIDER", 0.5)[0]
    assert abs(LAYOUT2.slider_px_to_value("OPACITY_SLIDER", mid) - 0.5) < 0.01
    # Out-of-range values clamp instead of running off the track.
    assert LAYOUT2.slider_value_to_px("OPACITY_SLIDER", 5.0)[0] == hi
    assert LAYOUT2.slider_px_to_value("OPACITY_SLIDER", lo - 500) == 0.0


def test_inside_anchors_use_catalog_ids_and_sit_on_shared_edges():
    anchors = inside_anchor_boxes()
    known = catalog()
    lines = [k for k in anchors if k.split("_")[0] in ("split", "street", "corner", "sixline")]
    unknown = [k for k in lines if k not in known]
    assert unknown == [], f"anchor ids the PlaceBet catalog does not know: {unknown}"
    # 1+2 share a horizontal line inside the first grid column.
    x0, y0, x1, y1 = anchors["split_1_2"]
    assert y0 < ROW_Y[2] < y1
    assert COL_X[0] < (x0 + x1) / 2 < COL_X[1]
    # 1+4 share the vertical line between the first two columns.
    x0, y0, x1, y1 = anchors["split_1_4"]
    assert x0 < COL_X[1] < x1
    # A corner sits where four cells meet.
    x0, y0, x1, y1 = anchors["corner_1_2_4_5"]
    assert x0 < COL_X[1] < x1 and y0 < ROW_Y[2] < y1


def test_zero_column_edges_match_the_bets_the_core_offers():
    anchors = inside_anchor_boxes()
    # One bet per grid cell along the zero column line, each inside its own row.
    # 00 owns the rows above the divider, 0 the rows below, and cell 2 straddles
    # it, which is why its edge is the three-number split_0_2_37 and there is no
    # separate 0+2 or 2+00 bet (proven live on .90).
    assert "split_0_2" not in anchors and "split_2_37" not in anchors
    for name, row in (("split_3_37", 0), ("split_0_2_37", 1), ("split_0_1", 2)):
        x0, y0, x1, y1 = anchors[name]
        assert x0 < ZERO_X[1] < x1, f"{name} must straddle the zero column line"
        assert y0 >= ROW_Y[row] and y1 <= ROW_Y[row + 1], f"{name} outside its cell row"
    # The 0/00 split is the horizontal line inside the zero column itself.
    x0, y0, x1, y1 = anchors["split_0_37"]
    assert y0 < ZERO_SPLIT_Y < y1
    assert ZERO_X[0] < (x0 + x1) / 2 < ZERO_X[1]
    # The five-number bet sits on the outer bottom corner, not on the divider.
    x0, y0, x1, y1 = anchors["basket_0_1_2_3_37"]
    assert x0 < ZERO_X[1] < x1 and y0 < ROW_Y[3] < y1


def test_registry_matches_the_measured_surface():
    data = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    assert data["layout_id"] == "layout2"
    buttons = data["buttons"]
    assert len(buttons) == len(build_buttons())
    bets = [b for b, e in buttons.items() if e.get("kind") == "bet"]
    assert len(bets) == 50, "the verifier only sweeps entries marked kind=bet"
    for name, entry in buttons.items():
        assert entry["id"] == name
        assert entry.get("click_area_pct"), f"{name} has no click area"
        assert entry["client"] == {"width": 1920, "height": 1080}
    # Cash out must stay flagged so no sweep ever clicks it.
    assert buttons["COBRAR"]["automation"] == "forbidden"
    assert buttons["HISTORY_STRIP"]["automation"] == "read_only"
    # A rebuild must not demote a spot the middleware already proved. Older
    # entries carry `verified: true` instead of a proof block, hence the isinstance.
    demoted = [
        name
        for name, entry in buttons.items()
        if isinstance(entry.get("verified"), dict)
        and entry["verified"].get("reported_numbers")
        and entry.get("kind") == "anchor_unverified"
    ]
    assert demoted == [], f"proven anchors rebuilt as unverified: {demoted}"


def test_layout1_exports_still_work_after_the_surface_refactor():
    from automation import roulette_ui_areas as ua

    assert ua.LAYOUT1.layout_id == "layout1"
    assert ua.build_hitbox_fields("17")["kind"] == "bet"
    assert ua.build_ui_hitbox_fields("IDIOMA")["kind"] == "ui"
    assert ua.ALL_CLICK_AREAS_PCT["17"] == ua.LAYOUT1.all_areas_pct["17"]
    assert ua.slider_value_to_px("OPACITY_SLIDER", 1.0)[0] == 1233
