from automation.roulette_script import script_for_strategy_plan
from automation.roulette_strategies import (
    STRATEGIES,
    StrategyState,
    apply_result,
    decompose_units,
    plan_for_strategy,
)


def test_catalog_has_ten_classic_plus_random() -> None:
    ids = [s.id for s in STRATEGIES]
    assert ids[0] == "random"
    assert "full_board" in ids
    assert "random_bot" not in ids
    assert len(STRATEGIES) == 12
    for expected in (
        "martingale",
        "grand_martingale",
        "paroli",
        "dalembert",
        "fibonacci",
        "labouchere",
        "oscar",
        "james_bond",
        "parlay",
        "system31",
    ):
        assert expected in ids


def test_full_board_plan_chunks_and_catalog() -> None:
    from automation.roulette_layout import board_catalog
    from automation.roulette_script import script_for_board_scan

    cat = board_catalog()
    assert cat["straights"] == 38  # 0..36 + 00
    assert cat["bet_targets"] > 100
    assert cat["scan_targets"] > cat["bet_targets"]
    state = StrategyState()
    plan = plan_for_strategy("full_board", state, base_unit=1)
    assert plan.stake_units > 0
    assert plan.stake_units <= 40
    script = script_for_board_scan([p.spot for p in plan.placements], press_spin=False)
    assert script["meta"]["strategy"] == "full_board"
    apply_result("full_board", state, won=False, stake_units=plan.stake_units)
    assert state.scan_index == plan.stake_units


def test_martingale_doubles_after_losses() -> None:
    state = StrategyState()
    # Use base_unit=10 so doubles stay above the even-money cloth min.
    p0 = plan_for_strategy("martingale", state, base_unit=10, market="RED")
    assert p0.stake_units == 10
    apply_result("martingale", state, won=False, base_unit=10, stake_units=10)
    p1 = plan_for_strategy("martingale", state, base_unit=10, market="RED")
    assert p1.stake_units == 20
    apply_result("martingale", state, won=False, base_unit=10, stake_units=20)
    p2 = plan_for_strategy("martingale", state, base_unit=10, market="RED")
    assert p2.stake_units == 40
    apply_result("martingale", state, won=True, base_unit=10, stake_units=40)
    p3 = plan_for_strategy("martingale", state, base_unit=10, market="RED")
    assert p3.stake_units == 10


def test_grand_martingale_sequence() -> None:
    state = StrategyState()
    stakes = []
    for _ in range(4):
        plan = plan_for_strategy(
            "grand_martingale", state, base_unit=10, market="BLACK", max_units=10_000
        )
        stakes.append(plan.stake_units)
        apply_result(
            "grand_martingale", state, won=False, base_unit=10, stake_units=plan.stake_units
        )
    assert stakes == [10, 30, 70, 150]


def test_fibonacci_moves() -> None:
    state = StrategyState()
    assert plan_for_strategy("fibonacci", state, base_unit=1).stake_units == 10
    apply_result("fibonacci", state, won=False, base_unit=1, stake_units=10)
    apply_result("fibonacci", state, won=False, base_unit=1, stake_units=10)
    apply_result("fibonacci", state, won=False, base_unit=1, stake_units=10)
    assert plan_for_strategy("fibonacci", state, base_unit=1).stake_units == 10  # fib 3 floored
    # Use base_unit=10 so progression steps are visible above the cloth min.
    state2 = StrategyState()
    assert plan_for_strategy("fibonacci", state2, base_unit=10).stake_units == 10
    apply_result("fibonacci", state2, won=False, base_unit=10, stake_units=10)
    apply_result("fibonacci", state2, won=False, base_unit=10, stake_units=10)
    apply_result("fibonacci", state2, won=False, base_unit=10, stake_units=20)
    assert plan_for_strategy("fibonacci", state2, base_unit=10).stake_units == 30


def test_labouchere_win_and_loss() -> None:
    state = StrategyState(lab_seq=[1, 2, 3, 4])
    plan = plan_for_strategy("labouchere", state, base_unit=1, market="RED")
    assert plan.stake_units == 10  # 1+4=5 floored to outside min 10
    apply_result("labouchere", state, won=True, base_unit=1, stake_units=10)
    assert state.lab_seq == [2, 3]
    plan2 = plan_for_strategy("labouchere", state, base_unit=1, market="RED")
    assert plan2.stake_units == 10  # 2+3=5 floored
    apply_result("labouchere", state, won=False, base_unit=1, stake_units=10)
    assert state.lab_seq == [2, 3, 5]


def test_system31_and_paroli_reset() -> None:
    state = StrategyState()
    apply_result("system31", state, won=False, base_unit=1, stake_units=10)
    apply_result("system31", state, won=False, base_unit=1, stake_units=10)
    assert plan_for_strategy("system31", state, base_unit=1).stake_units == 10  # third 1 floored
    apply_result("system31", state, won=True, base_unit=1, stake_units=10)
    assert state.system31_index == 0

    state2 = StrategyState()
    apply_result("paroli", state2, won=True, base_unit=1, stake_units=10)
    apply_result("paroli", state2, won=True, base_unit=1, stake_units=10)
    assert plan_for_strategy("paroli", state2, base_unit=1).stake_units == 10  # 4 floored? win_streak=2 → 4*1=4→10
    apply_result("paroli", state2, won=True, base_unit=10, stake_units=40)  # hits 3 → reset
    assert state2.win_streak == 0


def test_parlay_resets_after_three_spins_even_on_wins() -> None:
    """Parlay is a 3-spin burst: WWW must reset even though Paroli would continue."""
    state = StrategyState()
    stakes = []
    for _ in range(3):
        plan = plan_for_strategy("parlay", state, base_unit=10, market="RED")
        stakes.append(plan.stake_units)
        apply_result("parlay", state, won=True, base_unit=10, stake_units=plan.stake_units)
    assert stakes == [10, 20, 40]
    assert state.win_streak == 0
    assert state.series_spins == 0
    # Next burst starts at base again
    assert plan_for_strategy("parlay", state, base_unit=10, market="RED").stake_units == 10


def test_parlay_resets_on_loss_mid_burst() -> None:
    state = StrategyState()
    p0 = plan_for_strategy("parlay", state, base_unit=10, market="RED")
    assert p0.stake_units == 10
    apply_result("parlay", state, won=True, base_unit=10, stake_units=10)
    p1 = plan_for_strategy("parlay", state, base_unit=10, market="RED")
    assert p1.stake_units == 20
    apply_result("parlay", state, won=False, base_unit=10, stake_units=20)
    p2 = plan_for_strategy("parlay", state, base_unit=10, market="RED")
    assert p2.stake_units == 10
    assert state.series_spins == 0


def test_system31_restarts_after_final_step_loss() -> None:
    from automation.roulette_strategies import SYSTEM_31

    state = StrategyState()
    state.system31_index = len(SYSTEM_31) - 1  # already on last 8
    plan = plan_for_strategy("system31", state, base_unit=10, market="RED")
    assert plan.stake_units == 80
    apply_result("system31", state, won=False, base_unit=10, stake_units=80)
    assert state.system31_index == 0
    assert plan_for_strategy("system31", state, base_unit=10, market="RED").stake_units == 10


def test_james_bond_layout_and_script() -> None:
    state = StrategyState()
    plan = plan_for_strategy("james_bond", state, base_unit=1)
    assert plan.stake_units >= 20
    names = {p.spot.name for p in plan.placements}
    assert "19-36" in names
    assert "0" in names and "00" in names
    script = script_for_strategy_plan(plan)
    assert any(s.get("type") == "click_window" for s in script["steps"])
    assert script["meta"]["strategy"] == "james_bond"


def test_decompose_units() -> None:
    from automation.roulette_strategies import CHIP_VALUES

    parts = decompose_units(37)
    total = sum(CHIP_VALUES[chip.name] * n for chip, n in parts)
    assert total == 37
