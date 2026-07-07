from __future__ import annotations

from automation.gamedata_parser import parse_gamedata_line, sum_reported_win_credits


def test_parse_gamedata_line_extracts_game_spin_bet_and_wins() -> None:
    line = (
        "2026-06-16T00:00:12.901+01:00 INFO [:33534532] "
        "G:PR3_BigWinHD;115106;30;1;30;1;BaseGame;;186696;False;35;0/0;"
        "WIN LI5_1[L1W5]|WIN SC30[W30];EAAAAEQ9HFudkgAqYXfC=="
    )
    ev = parse_gamedata_line(line)
    assert ev is not None
    assert ev.game == "PR3_BigWinHD"
    assert ev.spin_id == 115106
    assert ev.bet_credits == 30
    assert ev.total_win_credits == 35
    assert len(ev.wins) == 2
    assert ev.wins[0].code.startswith("LI")
    assert "L1W5" in ev.wins[0].detail
    assert ev.wins[1].code == "SC30"
    assert "W30" in ev.wins[1].detail


def test_parse_gamedata_line_handles_no_win_field() -> None:
    line = (
        "2026-06-16T00:00:14.975+01:00 INFO [:33534532] "
        "G:PR3_BigWinHD;115107;30;1;30;1;BaseGame;;186666;False;0;0/0;|;EAAAABCD=="
    )
    ev = parse_gamedata_line(line)
    assert ev is not None
    assert ev.total_win_credits == 0
    assert ev.wins == ()


def test_sum_reported_win_credits_includes_feature() -> None:
    win_field = "WIN LI600_1[L2W600]|WIN SC0[]WIN FEATURE[580]"
    assert sum_reported_win_credits(win_field) == 1180

