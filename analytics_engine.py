"""
RNG / session analytics from state-timeline nodes (``StateNode``).

## Log syntax (OneHand / SlotMachine)

State transitions are parsed in ``timeline_engine`` from lines matching::

    Change MachineState from <old> to <new>

(case-insensitive; optional ``OneHand.MachineState -`` prefix on the line).

Cabinet SlotLog may also emit ``Machine State changed from …`` on other components;
the canonical rows used for the timeline still come from ``Change MachineState`` lines.

Each :class:`timeline_engine.StateNode` represents time spent in ``state_name``;
``previous_state`` is the *from* side of the transition that opened that segment.

## Heuristics (validated against ``SlotLog/2026-03-29.log``, lab GST cabinet)

Observed states include ``Idle``, ``MultigamerDialog``, ``Rotating``, ``ShowingWin``,
``PreparingBonus``, ``Bonus``, ``AfterBonus``. High-frequency ``Idle`` ↔
``MultigamerDialog`` is the game selector, **not** a base spin.

- **Base game spin**: transition **into** ``Rotating`` (reels) from an idle / wait
  style state, or legacy ``Game.Playing`` / ``spin`` / ``reel`` style names — excluding
  free-game and bonus feature states.
- **Free games**: transition **into** a free-game state from a non–free-game state.
- **Bonus**: transition **into** a bonus / feature state from a non-bonus state;
  ``AfterBonus`` and ``multigamer`` paths are excluded from “bonus” classification.
"""

from __future__ import annotations

from typing import Any

def _norm(s: str | None) -> str:
    return (s or "").lower()


def _is_free_games_state(state: str) -> bool:
    n = _norm(state)
    return any(
        x in n
        for x in (
            "freegame",
            "freegames",
            "freespin",
            "freespins",
            "free_spin",
            "freegamespin",
        )
    )


def _is_bonus_state(state: str) -> bool:
    n = _norm(state)
    if "multigamer" in n:
        return False
    # Post-feature cleanup; do not count as a bonus trigger / feature segment.
    if "afterbonus" in n or "after_bonus" in n:
        return False
    return any(
        x in n
        for x in (
            "bonus",
            "holdandspin",
            "hold_spin",
            "holdandwin",
            "featuregame",
            "pickbonus",
            "roulettebonus",
            "preparingbonus",
        )
    )


def _is_base_spin_enter(state_name: str, previous_state: str | None) -> bool:
    """True if this segment opens a base-game spin (not free/bonus feature)."""
    if _is_free_games_state(state_name) or _is_bonus_state(state_name):
        return False
    n = _norm(state_name)
    p = _norm(previous_state)
    # OneHand short names (SlotLog): reels start in ``Rotating``.
    rotating_enter = "rotating" in n
    play_like = rotating_enter or any(
        x in n
        for x in (
            "playing",
            "spin",
            "reel",
            "basegame",
            "basespin",
            "game.playing",
            "ingame",
        )
    )
    idle_like = (
        not p
        or any(
            x in p
            for x in (
                "idle",
                "bet",
                "wait",
                "ready",
                "select",
                "think",
                "wager",
                "game.idle",
                "multigamerdialog",
            )
        )
    )
    return idle_like and play_like


def _format_frequency(total_spins: int, triggers: int) -> str:
    if triggers <= 0:
        return "N/A (no triggers)"
    if total_spins <= 0:
        return "N/A (no base spins counted)"
    n = total_spins / triggers
    rounded = int(round(n))
    if abs(n - rounded) < 0.05:
        return f"1 in {rounded} spins"
    return f"1 in {n:.1f} spins"


def calculate_session_analytics(events: list[Any]) -> dict[str, Any]:
    """
    Count spin / feature transitions from timeline nodes.

    Each event should provide ``state_name`` and ``previous_state`` (e.g. :class:`timeline_engine.StateNode`).
    Free-game and bonus counts increment on **entry** into that feature (previous segment was not
    already in the same class), so multi-step bonus flows do not inflate triggers.
    """
    total_spins = 0
    free_games_triggered = 0
    bonuses_triggered = 0

    for ev in events:
        sn = getattr(ev, "state_name", None) or ""
        prev = getattr(ev, "previous_state", None)
        prev_s = prev or ""

        if _is_free_games_state(sn) and not _is_free_games_state(prev_s):
            free_games_triggered += 1
        if _is_bonus_state(sn) and not _is_bonus_state(prev_s):
            bonuses_triggered += 1

        if _is_base_spin_enter(sn, prev):
            total_spins += 1

    return {
        "total_spins": total_spins,
        "free_games_triggered": free_games_triggered,
        "bonuses_triggered": bonuses_triggered,
        "fg_frequency": _format_frequency(total_spins, free_games_triggered),
        "bonus_frequency": _format_frequency(total_spins, bonuses_triggered),
        "log_syntax_note": "Change MachineState from <old> to <new> (see timeline_engine)",
    }
