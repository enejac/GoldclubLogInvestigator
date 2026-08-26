"""Screen catalog, control relocation and registry writes for the screen mapper."""

from __future__ import annotations

import json

import numpy as np
import pytest

from automation.roulette_screen_map import (
    AMBIGUOUS_MARGIN,
    SKIN_FIT_MIN,
    Relocation,
    ScreenMatch,
    _identity_distance,
    _match_brightness,
    apply_to_registry,
    candidate_ids,
    discover_controls,
    identify_skin,
    known_screens,
    map_refusal,
    relocate_control,
    screen_by_key,
    skin_fit,
    skin_mismatch,
    skin_unreadable,
    slugify_screen_key,
    suggest_screen,
    validate_new_screen_name,
)
from automation.roulette_surface import surface_for

rng = np.random.default_rng(7)


def _noise(w: int = 640, h: int = 480) -> np.ndarray:
    return rng.integers(0, 90, size=(h, w, 3), dtype=np.int16)


def _paint(frame: np.ndarray, box, value: int = 230) -> np.ndarray:
    out = frame.copy()
    x0, y0, x1, y1 = box
    out[y0:y1, x0:x1] = value
    # A flat block matches anywhere; give it structure so the search has something
    # to lock onto, the way a real button's glyph does.
    out[y0 + 4 : y0 + 12, x0 + 6 : x1 - 6] = 20
    out[y1 - 14 : y1 - 6, x0 + 20 : x0 + 40] = 120
    return out


# --- catalog ---------------------------------------------------------------


@pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
def test_catalog_lists_the_cloth_and_every_sub_screen_once(layout_id):
    specs = known_screens(layout_id)
    keys = [s.key for s in specs]
    assert len(keys) == len(set(keys)), f"duplicate screens: {keys}"
    assert keys[0] == "base"
    assert specs[0].kind == "cloth"
    assert "statistics" in keys
    assert all(s.control_ids for s in specs if s.source != "registry")


def test_a_screen_that_was_just_named_is_listed_before_it_has_controls(monkeypatch):
    import automation.roulette_screen_map as mod

    monkeypatch.setattr(
        mod,
        "load_hitboxes",
        lambda _lid: {
            "buttons": {},
            "screens": {"history_v2": {"name": "History v2", "kind": "overlay"}},
        },
    )
    spec = screen_by_key("layout2", "history_v2")
    assert spec is not None
    assert (spec.name, spec.kind, spec.control_ids) == ("History v2", "overlay", ())


def test_racetrack_is_a_cloth_screen_not_a_panel():
    spec = screen_by_key("layout2", "racetrack")
    assert spec is not None
    assert spec.kind == "cloth"
    assert any(bid.startswith("race_") for bid in spec.control_ids)


def test_new_screen_names_are_slugged_and_checked():
    assert slugify_screen_key("History v2 (new!)") == "history_v2_new"
    assert validate_new_screen_name("layout2", "History V2") == "history_v2"
    for bad in ("", "x", "2fast", "help", "base"):
        with pytest.raises(ValueError):
            validate_new_screen_name("layout2", bad)


def test_candidate_ids_are_prefixed_and_skip_taken_ones():
    ids = candidate_ids("history_v2", 3, taken={"HISTORY_V2_BTN02"})
    assert ids == ["HISTORY_V2_BTN01", "HISTORY_V2_BTN03", "HISTORY_V2_BTN04"]


# --- suggestion ------------------------------------------------------------


def _match(
    key: str,
    score: float,
    matched: bool = True,
    basis: str = "baseline",
    kind: str = "overlay",
) -> ScreenMatch:
    return ScreenMatch(key, key, kind, "mapped", score, basis, matched)


def _cloth_match(key: str, score: float, matched: bool = True) -> ScreenMatch:
    return _match(key, score, matched, kind="cloth")


def test_suggestion_needs_a_clear_winner():
    assert suggest_screen([]) == (None, False)
    assert suggest_screen([_match("help", 0.9, matched=False)]) == (None, False)

    best, confident = suggest_screen([_match("help", 0.02), _match("menu", 0.9)])
    assert (best.key, confident) == ("help", True)

    close = AMBIGUOUS_MARGIN / 2
    best, confident = suggest_screen([_match("help", 0.02), _match("menu", 0.02 + close)])
    assert (best.key, confident) == ("help", False)


def test_identity_ignores_a_global_dim_but_not_a_new_picture():
    frame = _paint(_noise(), (80, 90, 240, 200))
    dimmed = (frame * 0.45).astype(frame.dtype)
    assert _identity_distance(frame, dimmed) < 0.05
    assert _identity_distance(frame, _noise()) > 0.5


# --- what is open ----------------------------------------------------------


def test_no_refusal_when_the_named_screen_proved_itself():
    matches = [_match("help", 0.02), _match("base", 0.9, matched=False)]
    assert map_refusal(matches, "help", is_new=False) == ""


def test_a_rail_open_over_the_cloth_does_not_block_mapping_either_one():
    # Both are genuinely on screen: the menu rail is drawn over the base cloth. On .90
    # the rail's own footprint matched perfectly while the dimmed cloth scored 0.34,
    # and ranking-based logic refused to map the cloth because of it.
    matches = [_match("menu", 0.00), _cloth_match("base", 0.34)]
    assert map_refusal(matches, "base", is_new=False) == ""
    assert map_refusal(matches, "menu", is_new=False) == ""


def test_a_modal_can_be_mapped_while_the_cloth_behind_it_still_matches():
    # The language chooser covers a fourteenth of the screen, so the cloth kept matching
    # at 0.17 with the modal wide open and the modal itself had no baseline yet.
    matches = [_match("language", 0.15, basis="evidence"), _cloth_match("base", 0.17)]
    assert map_refusal(matches, "language", is_new=False) == ""
    # A bare cloth is different: nothing is drawn over it, so no panel is open.
    bare = [_cloth_match("base", 0.02)]
    assert "not 'language'" in map_refusal(bare, "language", is_new=False)
    assert "already-mapped" in map_refusal(bare, "history_v2", is_new=True)


def test_refusal_names_what_is_open_instead():
    matches = [_match("base", 0.01), _match("help", 0.88, matched=False)]
    complaint = map_refusal(matches, "help", is_new=False)
    assert "does not look like 'help'" in complaint
    assert "base" in complaint


def test_refusal_when_the_named_screen_has_nothing_to_check_against():
    matches = [_match("base", 0.01), _match("help", 0.4, basis="evidence", matched=False)]
    complaint = map_refusal(matches, "help", is_new=False)
    assert "no baseline to check against" in complaint

    # With nothing proven either way the operator's word stands.
    guesses = [_match("help", 0.3, basis="evidence"), _match("menu", 0.4, basis="evidence")]
    assert map_refusal(guesses, "help", is_new=False) == ""


def test_naming_a_new_screen_is_refused_only_when_one_is_already_proven():
    proven = [_cloth_match("base", 0.01)]
    assert "already-mapped" in map_refusal(proven, "history_v2", is_new=True)

    unproven = [_match("base", 0.9, matched=False), _match("menu", 0.3, basis="evidence")]
    assert map_refusal(unproven, "history_v2", is_new=True) == ""


def test_an_evidence_match_is_only_ever_a_guess():
    # Panels without baselines cannot be told apart — the open language chooser
    # scored the menu's boxes best on .90 — so evidence must not read as proof.
    best, confident = suggest_screen(
        [_match("menu", 0.23, basis="evidence"), _match("language", 0.37, basis="evidence")]
    )
    assert (best.key, confident) == ("menu", False)
    # A baseline match still outranks and still counts, even next to evidence.
    best, confident = suggest_screen(
        [_match("base", 0.01), _match("menu", 0.23, basis="evidence")]
    )
    assert (best.key, confident) == ("base", True)


# --- relocation ------------------------------------------------------------


def test_control_that_did_not_move_reads_as_unchanged():
    box = (100, 100, 220, 160)
    baseline = _paint(_noise(), box)
    rel = relocate_control(baseline, baseline.copy(), box, "BTN")
    assert rel.status == "unchanged"
    assert (rel.dx, rel.dy) == (0, 0)
    assert rel.new_box == box


def test_a_shifted_control_is_found_again_at_its_new_box():
    box = (100, 100, 220, 160)
    background = _noise()
    baseline = _paint(background, box)
    moved = _paint(background, (box[0] + 17, box[1] - 9, box[2] + 17, box[3] - 9))

    rel = relocate_control(baseline, moved, box, "BTN")
    assert rel.status == "moved"
    assert (rel.dx, rel.dy) == (17, -9)
    assert rel.new_box == (117, 91, 237, 151)
    assert rel.score > 0.9


def test_a_shifted_control_is_still_found_through_the_dim():
    # "No más apuestas" dims the whole game. Raw pixels then differ everywhere, so the
    # baseline is put back on the frame's scale before anything is matched.
    box = (100, 100, 220, 160)
    background = _noise()
    baseline = _paint(background, box)
    moved = _paint(background, (box[0] + 11, box[1] + 6, box[2] + 11, box[3] + 6))
    dimmed = (moved * 0.5).astype(moved.dtype)

    rel = relocate_control(_match_brightness(baseline, dimmed), dimmed, box, "BTN")
    assert rel.status == "moved"
    assert (rel.dx, rel.dy) == (11, 6)


def test_a_control_that_is_not_drawn_carries_no_evidence():
    # The SERIES pills are only painted while their rail is on; a blank patch
    # matches every offset equally, so it must not be reported as having moved.
    blank = np.full((480, 640, 3), 40, dtype=np.int16)
    rel = relocate_control(blank, blank.copy(), (100, 100, 220, 160), "SERIES_PILL")
    assert rel.status == "featureless"
    assert rel.new_box == (100, 100, 220, 160)
    assert (rel.dx, rel.dy) == (0, 0)


def test_a_control_hidden_behind_a_panel_is_not_moved_to_its_look_alike():
    # The menu rail covered five outside bets on .90 and the search offered their
    # twins elsewhere at 71-85%. Writing that would move bets that never moved.
    box = (100, 100, 220, 160)
    twin = (300, 100, 420, 160)
    background = _paint(_noise(), twin)
    baseline = _paint(background, box)
    frame = baseline.copy()
    frame[box[1] : box[3], box[0] : box[2]] = 70  # a rail drawn over the control

    rel = relocate_control(baseline, frame, box, "1-18")
    assert rel.status == "covered"
    assert rel.new_box == box, "a covered control keeps its box"
    assert (rel.dx, rel.dy) == (0, 0)


def test_a_control_that_is_gone_is_reported_lost_not_moved():
    box = (100, 100, 220, 160)
    baseline = _paint(_noise(), box)
    rel = relocate_control(baseline, _noise(), box, "BTN")
    assert rel.status == "lost"
    assert rel.new_box is None


# --- discovery -------------------------------------------------------------


def _smooth_cloth(w: int = 640, h: int = 480) -> np.ndarray:
    """A featureless background, the way the cloth reads next to drawn chrome."""
    return rng.integers(30, 38, size=(h, w, 3), dtype=np.int16)


def test_discovery_reports_painted_areas_no_mapped_box_covers():
    base_cloth = _smooth_cloth()
    known = (60, 60, 180, 130)
    fresh = (300, 240, 460, 340)
    frame = _paint(_paint(base_cloth, known), fresh)

    boxes = discover_controls(frame, base_cloth, occupied=[known])
    assert boxes, "the unmapped block should be offered as a candidate"
    hit = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    # Fragments are merged outwards, so a candidate may be a little wider than the
    # control it found; it must cover it, not match it to the pixel.
    assert hit[0] <= fresh[0] + 16 and hit[1] <= fresh[1] + 16
    assert hit[2] >= fresh[2] - 16 and hit[3] >= fresh[3] - 16
    # The mapped control is claimed already, so nothing may be proposed over it.
    assert all(not (b[0] < known[2] and known[0] < b[2] and b[1] < known[3] and known[1] < b[3])
               for b in boxes)


def test_discovery_stays_quiet_without_a_cloth_reference():
    assert discover_controls(_noise(), None, occupied=[]) == []


def test_discovery_ignores_a_screen_that_only_dimmed():
    # Opening a panel dims the whole game behind it. A raw pixel diff calls that a
    # full-screen candidate; edges survive the dim, so nothing should be offered.
    cloth = _paint(_smooth_cloth(), (60, 60, 180, 130))
    dimmed = (cloth * 0.55).astype(cloth.dtype)
    assert discover_controls(dimmed, cloth, occupied=[]) == []


# --- skin fingerprint ------------------------------------------------------


def _cloth_of(layout_id: str) -> np.ndarray:
    """
    A frame with cell borders exactly where *layout_id* draws them.

    The lines are one pixel wide, like the real cloth's: a thick saturated band has
    no gradient in its middle, which is precisely where the fingerprint looks.
    """
    surface = surface_for(layout_id)
    frame = rng.integers(24, 40, size=(surface.client_h, surface.client_w, 3), dtype=np.int16)
    for x0, y0, x1, y1 in surface.numbers.values():
        frame[y0:y1, x0] = 220
        frame[y0:y1, min(x1, surface.client_w - 1)] = 220
        frame[y0, x0:x1] = 220
        frame[min(y1, surface.client_h - 1), x0:x1] = 220
    return frame


@pytest.mark.parametrize(("shown", "other"), [("layout1", "layout2"), ("layout2", "layout1")])
def test_skin_fingerprint_knows_which_cloth_is_on_screen(shown, other):
    frame = _cloth_of(shown)
    assert skin_fit(frame, shown) >= SKIN_FIT_MIN
    assert skin_fit(frame, shown) > skin_fit(frame, other)
    assert identify_skin(frame)[0][0] == shown

    assert skin_mismatch(frame, shown) == ""


def test_the_other_skin_being_up_is_named():
    # The real skins differ enough on their own (2.3 against 0.6 on .90); here the
    # lines layout1 expects are wiped so only layout2's grid can still be found.
    frame = _cloth_of("layout2")
    for x0, _, x1, _ in surface_for("layout1").numbers.values():
        frame[:, x0] = 33
        frame[:, min(x1, frame.shape[1] - 1)] = 33

    assert skin_fit(frame, "layout1") < SKIN_FIT_MIN
    complaint = skin_mismatch(frame, "layout1")
    assert "layout2" in complaint, complaint


def test_a_covered_cloth_is_reported_unreadable_not_wrong():
    # A full-screen panel hides the grid, so neither skin can be found. That is not
    # evidence of the wrong skin, and mapping the panel must not be blocked by it.
    frame = np.roll(_cloth_of("layout1"), 47, axis=1)
    assert all(skin_fit(frame, lid) < SKIN_FIT_MIN for lid in ("layout1", "layout2"))
    assert skin_unreadable(frame, "layout1")
    assert skin_mismatch(frame, "layout1") == ""


# --- registry --------------------------------------------------------------


def test_registry_write_moves_known_boxes_and_adds_named_ones(tmp_path):
    path = tmp_path / "layoutX_hitboxes.json"
    path.write_text(
        json.dumps(
            {"buttons": {"HELP_EXIT": {"x": 843, "y": 959, "width": 233, "height": 114,
                                       "kind": "overlay", "click_area_pct": {}}}}
        ),
        encoding="utf-8",
    )

    out = apply_to_registry(
        layout_id="layout2",
        screen_key="help",
        screen_name="Help",
        kind="overlay",
        relocations=[
            Relocation("HELP_EXIT", (843, 959, 1076, 1073), (853, 949, 1086, 1063), 10, -10,
                       0.97, "moved"),
            Relocation("HELP_BACK", (36, 959, 269, 1073), (36, 959, 269, 1073), 0, 0, 0.99,
                       "unchanged"),
        ],
        new_boxes={"HELP_BTN01": (400, 300, 520, 360)},
        client=(1920, 1080),
        hitboxes_path=path,
    )
    assert out["moved"] == ["HELP_EXIT"]
    assert out["added"] == ["HELP_BTN01"]

    data = json.loads(path.read_text(encoding="utf-8"))
    exit_entry = data["buttons"]["HELP_EXIT"]
    assert (exit_entry["x"], exit_entry["y"]) == (853, 949)
    assert exit_entry["click_center_px"] == {"x": 969, "y": 1006}
    assert exit_entry["remapped"]["dx"] == 10
    assert exit_entry["kind"] == "overlay"
    # Untouched controls keep their geometry; only movers are rewritten.
    assert "HELP_BACK" not in data["buttons"]

    added = data["buttons"]["HELP_BTN01"]
    assert added["overlay"] == "help"
    assert "not verified" in added["proof"]
    assert data["screens"]["help"]["name"] == "Help"
    assert data["status"]["screen_map_last"]["moved"] == 1
