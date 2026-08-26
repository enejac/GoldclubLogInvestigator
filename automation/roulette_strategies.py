"""
Classic roulette betting strategies for Alegro automation.

Stakes are in *display credit units* (chip_1 = 1). Most systems target even-money
outside bets (Red/Black, Odd/Even, High/Low). James Bond uses a multi-spot layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from automation.roulette_layout import (
    CHIP_SPOTS,
    NUMBER_SPOTS,
    OUTSIDE_SPOTS,
    ClickTarget,
    all_scan_targets,
)

# Chip face values on Alegro (matches chip_1..chip_100 UI).
CHIP_VALUES: dict[str, int] = {
    "chip_1": 1,
    "chip_5": 5,
    "chip_10": 10,
    "chip_50": 50,
    "chip_100": 100,
}

CHIP_BY_VALUE: dict[int, ClickTarget] = {
    CHIP_VALUES[c.name]: c for c in CHIP_SPOTS if c.name in CHIP_VALUES
}

OUTSIDE_BY_NAME: dict[str, ClickTarget] = {s.name: s for s in OUTSIDE_SPOTS}

# Even-money markets available on the Alegro Spanish cloth.
EVEN_MONEY_MARKETS: dict[str, str] = {
    "RED": "RED",
    "BLACK": "BLACK",
    "ODD": "ODD",  # IMPAR
    "EVEN": "EVEN",  # PAR
    "LOW": "1-18",
    "HIGH": "19-36",
}

FIBONACCI = (1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987)
SYSTEM_31 = (1, 1, 1, 2, 2, 4, 4, 8, 8)

# Hard cap so progression cannot request impossible chip stacks on lab tables.
DEFAULT_MAX_UNITS = 100
# Alegro .90: straight-up min is 1; even-money outside snaps to min 10 (validated live).
EVEN_MONEY_MIN_UNITS = 10
# Clicks per open window for full_board scan (~22s open; keep under ~12s of clicks).
FULL_BOARD_CHUNK = 40


@dataclass
class Placement:
    spot: ClickTarget
    units: int


@dataclass
class StrategyBetPlan:
    placements: list[Placement]
    stake_units: int
    label: str
    notes: str = ""


@dataclass
class StrategyState:
    """Mutable per-run state shared across rounds."""

    loss_streak: int = 0
    win_streak: int = 0
    units: int = 1  # current stake in base units (D'Alembert / Oscar)
    fib_index: int = 0
    lab_seq: list[int] = field(default_factory=lambda: [1, 2, 3, 4])
    oscar_deficit: int = 0  # units still needed for +1 series profit
    system31_index: int = 0
    last_won: bool | None = None
    series_profit: int = 0
    series_spins: int = 0  # Parlay: spins in the current 3-spin burst
    rounds_played: int = 0
    scan_index: int = 0  # full_board cursor into scan target list


@dataclass(frozen=True, slots=True)
class StrategyInfo:
    id: str
    name: str
    kind: str
    summary: str
    needs_even_money: bool = True


STRATEGIES: list[StrategyInfo] = [
    StrategyInfo(
        "random",
        "Random",
        "Coverage",
        "Click every safe mapped hitbox across layout1 and layout2: outside and "
        "straight cloth bets, UI chrome, layout switching, language / help / history / "
        "stats / menu overlays, racetrack + mini-board. Systematic order by default "
        "(full catalog); optional shuffle in Bot options. Every click is logged to "
        "click_log.jsonl. Never clicks cashout / PIN lock / attendant.",
        needs_even_money=False,
    ),
    StrategyInfo(
        "full_board",
        "Full board scan",
        "Coverage",
        "Click every mapped control across rounds: chips, UI buttons (VECINOS/FINALES/"
        "COMPLETO/sectors/BORRADOR/CANCEL/REPETIR), all straights, outside bets, and "
        "inside lines (splits/streets/corners/six-lines). Clears between chunks to "
        "limit credit burn. Use enough rounds to finish the catalog.",
        needs_even_money=False,
    ),
    StrategyInfo(
        "martingale",
        "1. Classic Martingale",
        "Negative progression",
        "Even-money: double after every loss; reset to base after a win. "
        "Recovers losses + 1 unit on the first win; hits table/bankroll limits fast on "
        "double-zero (~47.37% win rate).",
    ),
    StrategyInfo(
        "grand_martingale",
        "2. Grand Martingale",
        "Aggressive negative",
        "Even-money: after a loss, double previous stake and add one extra base unit "
        "(10->30->70->150...). Larger recovery payout, much steeper bust risk.",
    ),
    StrategyInfo(
        "paroli",
        "3. Reverse Martingale (Paroli)",
        "Positive progression",
        "Even-money: double after every win; reset after a loss or after a 3-win target. "
        "Risks house money on hot streaks; base units still bleed to the house edge.",
    ),
    StrategyInfo(
        "dalembert",
        "4. D’Alembert",
        "Conservative negative",
        "Even-money: +1 unit after a loss, −1 unit after a win (floor at base). "
        "Smoother than Martingale; zeros still drag a 50/50 session negative.",
    ),
    StrategyInfo(
        "fibonacci",
        "5. Fibonacci",
        "Sequence progression",
        "Even-money: advance one Fibonacci step on loss, two steps back on win. "
        "Gentler than Martingale (10 losses ≈ 55 units vs 1,024).",
    ),
    StrategyInfo(
        "labouchere",
        "6. Labouchère (Cancellation)",
        "Custom objective",
        "Even-money: stake = first+last of a number line (default 1-2-3-4). "
        "Win crosses ends off; loss appends the lost stake. Long cold runs explode the line.",
    ),
    StrategyInfo(
        "oscar",
        "7. Oscar’s Grind",
        "Positive/flat hybrid",
        "Even-money: aim for +1 unit per series. Flat after losses; +1 after wins "
        "only until the series clears. Protects bankroll; slow bleed on long cold flats.",
    ),
    StrategyInfo(
        "james_bond",
        "8. James Bond",
        "Flat multi-bet",
        "Every spin (scaled units): 14 on 19-36, 5 across 13-18, 1 on 0 (+1 on 00). "
        "~66% hit rate per spin; uncovered pockets wipe a large block at once.",
        needs_even_money=False,
    ),
    StrategyInfo(
        "parlay",
        "9. Parlay (Plus-Plan)",
        "Short positive burst",
        "Even-money: Paroli with a hard 3-win cap - after the third spin (win or lose) "
        "pocket/reset to base. Many small base losses for occasional 3-win bursts.",
    ),
    StrategyInfo(
        "system31",
        "10. 31 System",
        "Bounded negative",
        "Even-money: fixed 9-step block 1,1,1,2,2,4,4,8,8 (31 units). "
        "Advance only on loss; any win (or two early wins) resets to the start.",
    ),
]

STRATEGY_BY_ID: dict[str, StrategyInfo] = {s.id: s for s in STRATEGIES}


def even_money_spot(market: str) -> ClickTarget:
    key = EVEN_MONEY_MARKETS.get(market.upper(), "RED")
    spot = OUTSIDE_BY_NAME.get(key)
    if spot is None:
        raise KeyError(f"unknown even-money market {market!r}")
    return spot


def decompose_units(units: int) -> list[tuple[ClickTarget, int]]:
    """Break *units* into (chip_target, click_count) using available denominations."""
    left = max(0, int(units))
    out: list[tuple[ClickTarget, int]] = []
    for value in (100, 50, 10, 5, 1):
        chip = CHIP_BY_VALUE.get(value)
        if chip is None or left < value:
            continue
        n = left // value
        out.append((chip, n))
        left -= n * value
    return out


def _clamp(units: int, *, base: int, max_units: int) -> int:
    return max(base, min(int(units), max_units))


def plan_for_strategy(
    strategy_id: str,
    state: StrategyState,
    *,
    base_unit: int = 1,
    market: str = "RED",
    max_units: int = DEFAULT_MAX_UNITS,
    rng_plan: Callable[[], StrategyBetPlan] | None = None,
) -> StrategyBetPlan:
    """Compute the next-round stake layout for *strategy_id*."""
    sid = (strategy_id or "random").lower()
    base = max(1, int(base_unit))

    if sid == "random":
        if rng_plan is not None:
            return rng_plan()
        return StrategyBetPlan([], 0, "random", "use random script builder")

    if sid == "full_board":
        targets = all_scan_targets(include_ui=True, include_chips=True)
        n = len(targets)
        start = max(0, int(state.scan_index))
        if start >= n:
            start = 0
            state.scan_index = 0
        end = min(start + FULL_BOARD_CHUNK, n)
        chunk = targets[start:end]
        placements = [Placement(spot, 1) for spot in chunk]
        return StrategyBetPlan(
            placements,
            len(placements),
            "full_board",
            f"scan clicks {start + 1}-{end} of {n} "
            f"(chips+UI+straights+outside+inside lines)",
        )

    if sid == "james_bond":
        # Scaled classic 200-unit block -> 20xbase (14+5+1), plus 00 insurance.
        # Alegro map has no six-line target; spread the 5xbase across 13-18 straights.
        high = even_money_spot("HIGH")
        zero = NUMBER_SPOTS[0]
        zero0 = NUMBER_SPOTS[37]
        six_targets = [NUMBER_SPOTS[n] for n in (13, 14, 15, 16, 17, 18)]
        six_total = 5 * base
        placements: list[Placement] = [Placement(high, 14 * base)]
        six_units = [0] * len(six_targets)
        for i in range(six_total):
            six_units[i % len(six_targets)] += 1
        for spot, u in zip(six_targets, six_units):
            if u > 0:
                placements.append(Placement(spot, u))
        placements.append(Placement(zero, max(1, base // 2 + base % 2)))
        placements.append(Placement(zero0, max(1, base // 2)))
        stake = sum(p.units for p in placements)
        return StrategyBetPlan(
            placements,
            stake,
            "james_bond",
            f"flat block {stake}u (14x high + 5x 13-18 + 0/00)",
        )

    spot = even_money_spot(market)

    def _even(units: int) -> int:
        # Outside markets reject stakes below the cloth minimum.
        return max(EVEN_MONEY_MIN_UNITS, _clamp(units, base=base, max_units=max_units))

    if sid == "martingale":
        units = _even(base * (2 ** state.loss_streak))
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "martingale",
            f"loss_streak={state.loss_streak} -> {units}u on {spot.name}",
        )

    if sid == "grand_martingale":
        # 10, 30, 70, 150... = base * (2^(n+1) - 1)
        units = _even(base * (2 ** (state.loss_streak + 1) - 1))
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "grand_martingale",
            f"loss_streak={state.loss_streak} -> {units}u on {spot.name}",
        )

    if sid in ("paroli", "parlay"):
        units = _even(base * (2 ** state.win_streak))
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            sid,
            f"win_streak={state.win_streak} -> {units}u on {spot.name}",
        )

    if sid == "dalembert":
        units = _even(base * max(1, state.units))
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "dalembert",
            f"level={state.units} -> {units}u on {spot.name}",
        )

    if sid == "fibonacci":
        idx = max(0, min(state.fib_index, len(FIBONACCI) - 1))
        units = _even(base * FIBONACCI[idx])
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "fibonacci",
            f"fib[{idx}]={FIBONACCI[idx]} -> {units}u on {spot.name}",
        )

    if sid == "labouchere":
        seq = state.lab_seq or [1, 2, 3, 4]
        if len(seq) == 1:
            step = seq[0]
        else:
            step = seq[0] + seq[-1]
        units = _even(base * step)
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "labouchere",
            f"line={seq} stake={step}u -> {units}u on {spot.name}",
        )

    if sid == "oscar":
        units = _even(base * max(1, state.units))
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "oscar",
            f"series_bet={state.units}u deficit={state.oscar_deficit} -> {units}u on {spot.name}",
        )

    if sid == "system31":
        idx = max(0, min(state.system31_index, len(SYSTEM_31) - 1))
        units = _even(base * SYSTEM_31[idx])
        return StrategyBetPlan(
            [Placement(spot, units)],
            units,
            "system31",
            f"step[{idx}]={SYSTEM_31[idx]} -> {units}u on {spot.name}",
        )

    raise KeyError(f"unknown strategy {strategy_id!r}")


def apply_result(
    strategy_id: str,
    state: StrategyState,
    *,
    won: bool,
    base_unit: int = 1,
    stake_units: int = 0,
) -> None:
    """Update *state* after a settled round."""
    sid = (strategy_id or "random").lower()
    state.rounds_played += 1
    state.last_won = won
    base = max(1, int(base_unit))

    if sid == "full_board":
        # stake_units carries the click-chunk size from the plan.
        state.scan_index += max(0, int(stake_units))
        return

    if sid in ("random", "james_bond"):
        if won:
            state.win_streak += 1
            state.loss_streak = 0
        else:
            state.loss_streak += 1
            state.win_streak = 0
        return

    if sid == "martingale":
        if won:
            state.loss_streak = 0
            state.win_streak += 1
        else:
            state.loss_streak += 1
            state.win_streak = 0
        return

    if sid == "grand_martingale":
        if won:
            state.loss_streak = 0
            state.win_streak += 1
        else:
            state.loss_streak += 1
            state.win_streak = 0
        return

    if sid == "paroli":
        if won:
            state.win_streak += 1
            state.loss_streak = 0
            if state.win_streak >= 3:
                state.win_streak = 0  # pocket after 3 wins
        else:
            state.win_streak = 0
            state.loss_streak += 1
        return

    if sid == "parlay":
        # Paroli-style doubles within a burst, but the burst is hard-capped at
        # 3 spins (win or lose on the 3rd) and any mid-burst loss also resets.
        if won:
            state.win_streak += 1
            state.loss_streak = 0
        else:
            state.win_streak = 0
            state.loss_streak += 1
        state.series_spins += 1
        if (not won) or state.win_streak >= 3 or state.series_spins >= 3:
            state.win_streak = 0
            state.series_spins = 0
        return

    if sid == "dalembert":
        if won:
            state.units = max(1, state.units - 1)
            state.win_streak += 1
            state.loss_streak = 0
        else:
            state.units += 1
            state.loss_streak += 1
            state.win_streak = 0
        return

    if sid == "fibonacci":
        if won:
            state.fib_index = max(0, state.fib_index - 2)
            state.win_streak += 1
            state.loss_streak = 0
        else:
            state.fib_index = min(len(FIBONACCI) - 1, state.fib_index + 1)
            state.loss_streak += 1
            state.win_streak = 0
        return

    if sid == "labouchere":
        seq = list(state.lab_seq or [1, 2, 3, 4])
        if won:
            if len(seq) <= 2:
                seq = [1, 2, 3, 4]  # target complete -> new line
            else:
                seq = seq[1:-1]
            state.win_streak += 1
            state.loss_streak = 0
        else:
            # Append the *line* stake (base units), not the cloth-floored credit
            # amount. Outside min (10) would otherwise inflate the cancellation line.
            lost = seq[0] + seq[-1] if len(seq) > 1 else seq[0]
            seq.append(max(1, int(lost)))
            state.loss_streak += 1
            state.win_streak = 0
        state.lab_seq = seq
        return

    if sid == "oscar":
        # Series aims for +1 base unit profit.
        stake_level = max(1, state.units)
        if won:
            state.oscar_deficit = max(0, state.oscar_deficit - stake_level)
            state.win_streak += 1
            state.loss_streak = 0
            if state.oscar_deficit == 0 and state.series_profit + stake_level >= 1:
                # Series complete.
                state.units = 1
                state.oscar_deficit = 0
                state.series_profit = 0
            else:
                # Increase by 1, but not past what finishes the series at +1.
                need = max(1, 1 + state.oscar_deficit)
                state.units = min(stake_level + 1, need)
                state.series_profit += stake_level
        else:
            state.oscar_deficit += stake_level
            state.loss_streak += 1
            state.win_streak = 0
            # Keep stake flat after losses.
        return

    if sid == "system31":
        if won:
            # Any win ends the block and restarts at step 0.
            state.system31_index = 0
            state.win_streak += 1
            state.loss_streak = 0
        else:
            # Advance on loss; after losing the final 8-unit step the 31-unit
            # block is exhausted — next series starts at the beginning.
            if state.system31_index >= len(SYSTEM_31) - 1:
                state.system31_index = 0
            else:
                state.system31_index += 1
            state.loss_streak += 1
            state.win_streak = 0
        return


def strategy_catalog() -> list[dict[str, Any]]:
    """UI-friendly list of strategies."""
    return [
        {
            "id": s.id,
            "name": s.name,
            "kind": s.kind,
            "summary": s.summary,
            "needs_even_money": s.needs_even_money,
        }
        for s in STRATEGIES
    ]
