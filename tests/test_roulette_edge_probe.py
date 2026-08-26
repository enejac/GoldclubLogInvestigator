"""
The corner test's judgement, checked without touching a cabinet.

What matters here is that a box is only called sound when both of its corners
produced the *same* event, that a box which is too big is described as such
rather than dismissed, and that a named bet is never probed as if it cost one
chip - the cabinet drops those below its outside minimum, which looks exactly
like a missed click and would be reported as a mapping failure.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from automation.roulette_edge_probe import (
    BATCH,
    INSET_PX,
    BetWindow,
    CornerHit,
    EdgeVerdict,
    Spot,
    bet_for,
    bet_signature,
    collect_spots,
    compare_corners,
    expected_id,
    pays_chip_stake,
    plan_batches,
    clean_hit,
    put_signature,
    render_report,
    summarise,
)
from automation.roulette_target import cabinet_target


def spot(box=(100, 200, 140, 240), *, family="straight", expect="17", bid="17") -> Spot:
    return Spot(bid, box, family, expect=expect)


def hit(corner: str, signature: str = "") -> CornerHit:
    return CornerHit(corner, (0, 0), signature=signature)


class TestCorners:
    def test_the_clicked_pixels_sit_one_step_inside_the_drawn_box(self) -> None:
        s = spot((100, 200, 140, 240))
        assert s.top_left == (100 + INSET_PX, 200 + INSET_PX)
        # The far edge is exclusive, so the last pixel of the box is x1 - 1.
        assert s.bottom_right == (140 - 1 - INSET_PX, 240 - 1 - INSET_PX)

    def test_both_corners_stay_inside_the_box(self) -> None:
        s = spot((10, 10, 16, 16))
        for x, y in (s.top_left, s.bottom_right):
            assert s.box[0] <= x < s.box[2]
            assert s.box[1] <= y < s.box[3]

    def test_a_box_too_thin_to_have_two_corners_is_recognised(self) -> None:
        assert spot((10, 10, 13, 40)).too_small
        assert not spot((10, 10, 20, 40)).too_small


class TestVerdicts:
    def test_two_corners_that_agree_prove_the_box(self) -> None:
        v = compare_corners(
            spot(), hit("top_left", "Fields:17"), hit("bottom_right", "Fields:17"), oracle="middleware"
        )
        assert (v.verdict, v.ok) == ("same", True)
        assert v.suggested_box is None

    def test_two_corners_that_disagree_name_both_bets(self) -> None:
        v = compare_corners(
            spot(), hit("top_left", "Fields:17"), hit("bottom_right", "Fields:20"), oracle="middleware"
        )
        assert v.verdict == "differs"
        assert "Fields:17" in v.note and "Fields:20" in v.note

    def test_a_dead_bottom_corner_suggests_a_smaller_box_without_applying_it(self) -> None:
        v = compare_corners(
            spot((100, 200, 140, 240)), hit("top_left", "Fields:17"), hit("bottom_right"), oracle="middleware"
        )
        assert v.verdict == "bottom_right"
        assert v.suggested_box == (100, 200, 138, 238)
        # The origin is untouched: the probe knows which edge is wrong, not where it belongs.
        assert v.suggested_box[:2] == v.box[:2]

    def test_a_dead_top_corner_pulls_that_corner_in_instead(self) -> None:
        v = compare_corners(
            spot((100, 200, 140, 240)), hit("top_left"), hit("bottom_right", "Fields:17"), oracle="middleware"
        )
        assert v.verdict == "top_left"
        assert v.suggested_box == (102, 202, 140, 240)

    def test_a_silent_control_is_reported_not_guessed_at(self) -> None:
        v = compare_corners(spot(), hit("top_left"), hit("bottom_right"), oracle="godot_put")
        assert (v.verdict, v.suggested_box) == ("silent", None)
        assert "neither corner" in v.note

    def test_agreeing_on_the_wrong_bet_is_still_a_failure(self) -> None:
        v = compare_corners(
            spot(expect="17"),
            hit("top_left", "Fields:20"),
            hit("bottom_right", "Fields:20"),
            oracle="middleware",
        )
        assert v.verdict == "differs"
        assert "Id=17" in v.note

    def test_a_skipped_box_does_not_count_as_a_failure(self) -> None:
        v = EdgeVerdict("x", "chrome", (0, 0, 2, 2), "skipped", "none")
        assert v.ok
        assert summarise([v])["failed"] == 0


class TestEvidence:
    def test_the_cloth_reads_the_same_however_the_bets_are_ordered(self) -> None:
        a = [{"BetType": "Fields", "Id": "17"}, {"BetType": "Fields", "Id": "2"}]
        assert bet_signature(a) == bet_signature(list(reversed(a)))

    def test_one_bet_can_be_picked_out_of_a_crowded_cloth(self) -> None:
        bets = [{"BetType": "Fields", "Id": "17"}, {"BetType": "Fields", "Id": "37"}]
        assert bet_for(bets, "37") == "Fields:37"
        assert bet_for(bets, "5") == ""

    def test_the_put_lines_the_client_writes_become_one_comparable_string(self) -> None:
        lines = [
            "2026-07-28T10:00:00 INFO Sending put action PlaceBet data [\"17\"]",
            "2026-07-28T10:00:00 INFO something else entirely",
            "2026-07-28T10:00:01 INFO Sending put action CancelAllBets",
        ]
        assert put_signature(lines) == 'PlaceBet(["17"])+CancelAllBets'

    def test_a_log_with_nothing_in_it_is_not_an_event(self) -> None:
        assert put_signature(["nothing here"]) == ""


class TestStake:
    @pytest.mark.parametrize(
        "bid,expect",
        [("17", "17"), ("mini_17", "17"), ("split_1+2", "1+2"), ("basket", "0+1+2+3+37")],
    )
    def test_number_bets_cost_one_chip(self, bid: str, expect: str) -> None:
        assert pays_chip_stake(bid, expect)

    @pytest.mark.parametrize(
        "bid,expect",
        [
            ("EVEN", "2+4+6+8+10+12+14+16+18+20+22+24+26+28+30+32+34+36"),
            ("1-12", "1+2+3+4+5+6+7+8+9+10+11+12"),
            ("mini_ODD", ""),
            ("mini_2to1_top", ""),
        ],
    )
    def test_named_bets_pay_the_outside_minimum_however_they_are_drawn(
        self, bid: str, expect: str
    ) -> None:
        assert not pays_chip_stake(bid, expect)

    def test_a_verified_spot_is_believed_over_its_own_name(self) -> None:
        entry = {"kind": "bet", "expect": {"BetType": "Fields", "Id": "37"}}
        assert expected_id(entry, "00", "straight") == "37"

    def test_double_zero_is_known_to_report_as_37(self) -> None:
        assert expected_id({}, "00", "straight") == "37"
        assert expected_id({}, "race_00", "race") == "37"

    def test_chrome_is_never_given_a_bet_to_expect(self) -> None:
        assert expected_id({"place_bet": ["17"]}, "MENU", "chrome") == ""


class TestPlanning:
    def test_only_spots_with_a_known_id_share_a_read(self) -> None:
        known = [spot(bid=str(n), expect=str(n)) for n in range(1, 9)]
        unknown = [spot(bid="MENU", family="chrome", expect="")]
        units = plan_batches(known + unknown, batch=BATCH)
        assert [len(u) for u in units] == [8, 1]
        assert units[-1][0].button_id == "MENU"

    def test_neighbours_do_not_end_up_in_the_same_batch(self) -> None:
        # A click that slips one cell sideways must land on something no other spot
        # in its own batch claims, or the read cannot say which click it came from.
        spots = [spot(bid=str(n), expect=str(n)) for n in range(1, 17)]
        units = plan_batches(spots, batch=8)
        for unit in units:
            ids = sorted(int(s.button_id) for s in unit)
            assert all(b - a > 1 for a, b in zip(ids, ids[1:]))

    def test_batching_can_be_turned_off(self) -> None:
        spots = [spot(bid=str(n), expect=str(n)) for n in range(1, 5)]
        assert all(len(u) == 1 for u in plan_batches(spots, batch=1))

    def test_every_spot_is_planned_exactly_once(self) -> None:
        spots = [spot(bid=str(n), expect=str(n)) for n in range(1, 20)]
        spots.append(spot(bid="MENU", family="chrome", expect=""))
        planned = [s.button_id for unit in plan_batches(spots) for s in unit]
        assert sorted(planned) == sorted(s.button_id for s in spots)

    def test_a_box_too_small_to_probe_is_still_reported_on(self) -> None:
        thin = spot((10, 10, 12, 40), bid="thin", expect="1")
        assert thin in [s for unit in plan_batches([thin]) for s in unit]

    def test_the_chip_buttons_are_probed_after_every_bet(self) -> None:
        # Clicking chip_100 arms a 100-credit stake, so any bet probed after it
        # is played for a hundred times the intended price.
        spots = [
            spot(bid="chip_100", family="chrome", expect=""),
            spot(bid="MENU", family="chrome", expect=""),
            spot(bid="race_17", family="race", expect=""),
            spot(bid="17", expect="17"),
        ]
        order = [s.button_id for unit in plan_batches(spots, batch=1) for s in unit]
        assert order.index("chip_100") == len(order) - 1
        assert order.index("race_17") < order.index("MENU")


class TestRegistry:
    @pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
    def test_the_sweep_reads_real_boxes_out_of_the_registry(self, layout_id: str) -> None:
        spots = collect_spots(layout_id)
        assert spots, f"{layout_id} has no mapped controls"
        for s in spots:
            assert s.box[2] > s.box[0] and s.box[3] > s.box[1]

    @pytest.mark.parametrize("layout_id", ["layout1", "layout2"])
    def test_low_stake_bets_stay_out_unless_they_are_asked_for(self, layout_id: str) -> None:
        default = {s.button_id for s in collect_spots(layout_id)}
        outside = {s.button_id for s in collect_spots(layout_id, families=["outside"])}
        assert outside
        assert not (default & outside)

    def test_asking_for_one_control_gets_that_control(self) -> None:
        spots = collect_spots("layout2", only=["17"])
        assert [s.button_id for s in spots] == ["17"]

    def test_the_controls_that_must_never_be_clicked_are_left_out(self) -> None:
        from automation.roulette_edge_probe import NEVER_CLICK

        for layout_id in ("layout1", "layout2"):
            assert not {s.button_id for s in collect_spots(layout_id)} & NEVER_CLICK


class _StubSession:
    """Just enough of a Session for the betting-window gate."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def log_dir(self, name: str) -> Path:
        return self._folder


class TestOracles:
    """A sweep with nothing to judge by must say so instead of clicking for an hour."""

    class _Empty(_StubSession):
        def __init__(self, folder: Path, *, middleware: bool = True) -> None:
            super().__init__(folder)
            self.middleware = middleware

        def player_state(self, cancel: bool = False) -> dict:
            if self.middleware:
                return {"ok": True, "credits": 500_000, "bets": []}
            return {"ok": False, "error": "WinRM cannot complete the operation"}

    def test_a_cabinet_with_no_middleware_cannot_prove_a_bet(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import missing_oracles

        gaps = missing_oracles(self._Empty(tmp_path, middleware=False), [spot()])
        assert len(gaps) == 1
        assert "middleware is not answering" in gaps[0]
        assert "WinRM" in gaps[0]

    def test_a_target_with_no_game_log_cannot_judge_chrome(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import missing_oracles

        chrome = spot(bid="MENU", family="chrome", expect="")
        gaps = missing_oracles(self._Empty(tmp_path / "gone"), [chrome])
        assert len(gaps) == 1
        assert "no godot log" in gaps[0]

    def test_asking_for_pixel_evidence_removes_that_objection(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import missing_oracles

        chrome = spot(bid="MENU", family="chrome", expect="")
        assert missing_oracles(self._Empty(tmp_path / "gone"), [chrome], pixel=True) == []

    def test_a_ready_target_raises_no_objection(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import missing_oracles

        (tmp_path / "2026-07-28.log").write_text("boot\n", encoding="utf-8")
        assert missing_oracles(self._Empty(tmp_path), [spot()]) == []


class TestRacetrackView:
    def test_the_oval_and_its_mini_cloth_are_a_separate_pass(self) -> None:
        from automation.roulette_edge_probe import view_for

        assert view_for(spot(bid="race_17", family="race")) == "race"
        assert view_for(spot(bid="mini_17", family="mini")) == "race"
        assert view_for(spot(bid="17")) == "square"
        assert view_for(spot(bid="MENU", family="chrome", expect="")) == "square"

    def test_racetrack_spots_are_skipped_rather_than_clicked_on_the_wrong_cloth(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import automation.roulette_edge_probe as probe

        monkeypatch.setattr(probe, "ensure_probe_view", lambda *a, **k: False)
        session = TestCreditFloor._Broke(tmp_path / "missing", probe.CREDIT_FLOOR * 10)
        verdicts = probe.probe_spots(
            session,
            [spot(bid="race_17", family="race"), spot(bid="mini_17", family="mini")],
            batch=1,
            progress=lambda _l: None,
        )
        assert [v.verdict for v in verdicts] == ["skipped", "skipped"]
        assert session.clicked == 0
        assert "racetrack view" in verdicts[0].note

    def test_the_skin_selectors_are_never_clicked(self) -> None:
        from automation.roulette_edge_probe import NEVER_CLICK

        assert {"PANO", "LAYOUT_SWITCH"} <= NEVER_CLICK


class TestCreditFloor:
    class _Broke(_StubSession):
        def __init__(self, folder: Path, credits: int) -> None:
            super().__init__(folder)
            self.credits = credits
            self.clicked = 0

        def put(self, action: str, data: str = "") -> dict:
            return {"success": True}

        def player_state(self, cancel: bool = False) -> dict:
            return {"ok": True, "credits": self.credits, "bets": []}

        def pixel_step(self, x: int, y: int, ms: int = 0) -> dict:
            return {"type": "click_screen_px", "at": (x, y)}

        def run(self, steps, timeout: int = 60) -> tuple[bool, str]:
            self.clicked += len(list(steps))
            return True, ""

    def test_bets_are_left_unprobed_rather_than_called_missed(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import CREDIT_FLOOR, probe_spots

        session = self._Broke(tmp_path / "missing", CREDIT_FLOOR - 1)
        [verdict] = probe_spots(
            session, [spot(bid="17", expect="17")], batch=1, progress=lambda _l: None
        )
        assert verdict.verdict == "skipped"
        assert "Top up" in verdict.note
        assert session.clicked == 0

    def test_a_funded_cabinet_is_probed_normally(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import CREDIT_FLOOR, probe_spots

        session = self._Broke(tmp_path / "missing", CREDIT_FLOOR * 10)
        [verdict] = probe_spots(
            session, [spot(bid="17", expect="17")], batch=1, progress=lambda _l: None
        )
        assert verdict.verdict != "skipped"
        assert session.clicked > 0

    def test_the_same_failure_three_times_over_stops_the_sweep(self, tmp_path: Path) -> None:
        from automation.roulette_edge_probe import MAX_CONSECUTIVE_ERRORS, probe_spots

        class _Gone(self._Broke):
            def run(self, steps, timeout: int = 60):
                raise FileNotFoundError("script.json is gone from the staging folder")

        session = _Gone(tmp_path / "missing", 10_000_000)
        spots = [spot(bid=str(n), expect=str(n)) for n in range(1, 13)]
        verdicts = probe_spots(session, spots, batch=1, progress=lambda _l: None)
        assert len(verdicts) == len(spots), "every control is still accounted for"
        errors = [v for v in verdicts if v.verdict == "error"]
        assert len(errors) == MAX_CONSECUTIVE_ERRORS
        unprobed = [v for v in verdicts if v.verdict == "skipped"]
        assert len(unprobed) == len(spots) - MAX_CONSECUTIVE_ERRORS
        assert "its own plumbing" in unprobed[0].note


class TestBettingWindow:
    def _window(self, tmp_path: Path, text: str = "") -> tuple[BetWindow, Path]:
        log = tmp_path / "2026-07-28.log"
        log.write_text(text, encoding="utf-8")
        return BetWindow(_StubSession(tmp_path)), log

    def test_with_no_log_in_reach_the_gate_stands_aside(self, tmp_path: Path) -> None:
        window = BetWindow(_StubSession(tmp_path / "missing"))
        assert window.blind
        assert window.ensure(need=99.0, timeout=0.0)

    def test_a_closed_cloth_has_no_time_left(self, tmp_path: Path) -> None:
        window, _ = self._window(tmp_path, "boot\n")
        assert window.remaining() == 0.0
        assert not window.ensure(need=5.0, timeout=0.2)

    def test_an_open_window_reports_the_time_it_has_left(self, tmp_path: Path) -> None:
        window, log = self._window(tmp_path)
        log.write_text("INFO Bets are open\n", encoding="utf-8")
        assert window.ensure(need=5.0, timeout=2.0)
        assert 15.0 < window.remaining() <= 22.0

    def test_the_close_marker_shuts_the_gate_again(self, tmp_path: Path) -> None:
        window, log = self._window(tmp_path)
        log.write_text("INFO Bets are open\n", encoding="utf-8")
        assert window.ensure(need=5.0, timeout=2.0)
        with log.open("a", encoding="utf-8") as fh:
            fh.write("INFO Bets are closed\n")
        assert not window.ensure(need=5.0, timeout=0.3)

    def test_several_probes_share_one_window(self, tmp_path: Path) -> None:
        window, log = self._window(tmp_path)
        log.write_text("INFO Bets are open\n", encoding="utf-8")
        started = time.time()
        for _ in range(4):
            assert window.ensure(need=5.0, timeout=2.0)
        # No probe waited for a fresh marker, so this cost nothing.
        assert time.time() - started < 2.0


class TestBorderBand:
    """
    The band along a cell border belongs to the split that shares it.

    A click one pixel inside a straight's box books ``Split:15+18`` rather than the
    number, which is what the live cabinet does, so "the number came back" is not
    enough to call a corner clean.
    """

    def test_the_number_on_its_own_is_a_clean_hit(self) -> None:
        assert clean_hit([{"BetType": "Fields", "Id": "17"}], "17")

    def test_a_split_that_covers_the_number_is_not(self) -> None:
        assert not clean_hit([{"BetType": "Split", "Id": "17+20"}], "17")

    def test_the_number_plus_a_split_is_still_a_border_click(self) -> None:
        bets = [{"Id": "17"}, {"Id": "17+20"}]
        assert not clean_hit(bets, "17")

    def test_a_neighbour_bet_that_does_not_cover_the_number_is_ignored(self) -> None:
        # Another spot in the same batch left this behind.
        bets = [{"Id": "17"}, {"Id": "5+8"}]
        assert clean_hit(bets, "17")

    def test_an_empty_cloth_is_not_a_hit(self) -> None:
        assert not clean_hit([], "17")

    def test_a_partial_number_is_not_mistaken_for_the_number(self) -> None:
        # "1" must not be read out of "17", which naive substring matching would do.
        assert not clean_hit([{"Id": "17"}], "1")
        assert not clean_hit([{"Id": "13+16"}], "1")

    def test_the_ladder_walks_inwards(self) -> None:
        from automation.roulette_edge_probe import MARGIN_LADDER

        assert list(MARGIN_LADDER) == sorted(MARGIN_LADDER)
        assert MARGIN_LADDER[0] > INSET_PX


class TestMeasuredCorrections:
    def _verdict(self, box=(100, 200, 140, 240)) -> EdgeVerdict:
        return compare_corners(
            spot(box, bid="17", expect="17"),
            hit("top_left", "Split:15+18"),
            hit("bottom_right", "Split:17+20"),
            oracle="middleware",
        )

    def test_a_measured_margin_replaces_the_guessed_box(self, monkeypatch) -> None:
        import automation.roulette_edge_probe as probe

        monkeypatch.setattr(probe, "BetWindow", lambda *a, **k: None)
        monkeypatch.setattr(probe, "measure_corner_margin", lambda *a, **k: {"17": 6})
        verdict = self._verdict()
        [refined] = probe.refine_margins(
            None, [verdict], [spot(bid="17", expect="17")]
        )
        assert refined.suggested_box == (106, 206, 134, 234)
        assert "6 px inside the box" in refined.note

    def test_a_spot_whose_corners_never_come_clean_keeps_its_note(self, monkeypatch) -> None:
        import automation.roulette_edge_probe as probe

        monkeypatch.setattr(probe, "BetWindow", lambda *a, **k: None)
        monkeypatch.setattr(probe, "measure_corner_margin", lambda *a, **k: {"17": None})
        verdict = self._verdict()
        before = verdict.suggested_box
        [refined] = probe.refine_margins(None, [verdict], [spot(bid="17", expect="17")])
        assert refined.suggested_box == before

    def test_inside_bets_are_left_alone(self, monkeypatch) -> None:
        # A split is *meant* to sit on a shared border; pulling it off would be wrong.
        import automation.roulette_edge_probe as probe

        called = []
        monkeypatch.setattr(probe, "measure_corner_margin", lambda *a, **k: called.append(1) or {})
        anchor = spot(family="inside", bid="split_1+2", expect="1+2")
        verdict = compare_corners(
            anchor, hit("top_left", "Split:1+2"), hit("bottom_right"), oracle="middleware"
        )
        probe.refine_margins(None, [verdict], [anchor])
        assert not called

    def test_nothing_is_measured_when_everything_passed(self, monkeypatch) -> None:
        import automation.roulette_edge_probe as probe

        called = []
        monkeypatch.setattr(probe, "measure_corner_margin", lambda *a, **k: called.append(1) or {})
        good = compare_corners(
            spot(), hit("top_left", "Fields:17"), hit("bottom_right", "Fields:17"),
            oracle="middleware",
        )
        assert probe.refine_margins(None, [good], [spot()]) == [good]
        assert not called

class TestReport:
    def test_the_report_names_every_control_and_its_verdict(self) -> None:
        verdicts = [
            compare_corners(
                spot(bid="17"), hit("top_left", "Fields:17"), hit("bottom_right", "Fields:17"),
                oracle="middleware",
            ),
            compare_corners(
                spot(bid="20", expect="20"), hit("top_left", "Fields:20"), hit("bottom_right"),
                oracle="middleware",
            ),
        ]
        summary = summarise(verdicts)
        assert (summary["tested"], summary["sound"], summary["failed"]) == (2, 1, 1)
        assert summary["failures"] == ["20"]

        text = render_report(
            verdicts, layout_id="layout2", target=cabinet_target("10.0.0.90"), summary=summary
        )
        assert "`17`" in text and "`20`" in text
        assert "10.0.0.90" in text
        assert "Suggested corrections (not applied)" in text
