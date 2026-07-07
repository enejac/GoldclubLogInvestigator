"""analytics_engine — session RNG / feature counts from StateNode-like rows."""

from __future__ import annotations

from dataclasses import dataclass

from analytics_engine import calculate_session_analytics


@dataclass
class _FakeNode:
    state_name: str
    previous_state: str | None = None


def test_calculate_session_analytics_counts_spins_fg_bonus() -> None:
    events = [
        _FakeNode("Game.Idle", None),
        _FakeNode("Game.Playing", "Game.Idle"),
        _FakeNode("Game.Idle", "Game.Playing"),
        _FakeNode("Game.Playing", "Game.Idle"),
        _FakeNode("Game.FreeGames", "Game.Idle"),
        _FakeNode("Game.Idle", "Game.FreeGames"),
        _FakeNode("Game.RouletteBonus", "Game.Idle"),
        _FakeNode("Game.Idle", "Game.RouletteBonus"),
    ]
    s = calculate_session_analytics(events)
    assert s["total_spins"] == 2
    assert s["free_games_triggered"] == 1
    assert s["bonuses_triggered"] == 1
    assert "spins" in s["fg_frequency"]
    assert "spins" in s["bonus_frequency"]


def test_calculate_session_analytics_no_div_zero() -> None:
    s = calculate_session_analytics(
        [_FakeNode("Game.Playing", "Game.Idle")],
    )
    assert s["free_games_triggered"] == 0
    assert "N/A" in s["fg_frequency"]


def test_slotlog_style_rotating_and_bonus_chain() -> None:
    """Short state names from SlotLog (lab cabinet)."""
    events = [
        _FakeNode("Idle", "MultigamerDialog"),
        _FakeNode("Rotating", "Idle"),
        _FakeNode("ShowingWin", "Rotating"),
        _FakeNode("Idle", "ShowingWin"),
        _FakeNode("PreparingBonus", "ShowingWin"),
        _FakeNode("Bonus", "PreparingBonus"),
        _FakeNode("AfterBonus", "Bonus"),
        _FakeNode("ShowingWin", "AfterBonus"),
    ]
    s = calculate_session_analytics(events)
    assert s["total_spins"] == 1
    assert s["bonuses_triggered"] == 1
    assert s["free_games_triggered"] == 0
