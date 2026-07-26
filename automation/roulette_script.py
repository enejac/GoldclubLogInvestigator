"""Build InputAgent scripts for roulette: random or strategy bets + START."""

from __future__ import annotations

import random
from typing import Any

from automation.roulette_layout import (
    CANCEL_ALL_BUTTON,
    CHIP_SPOTS,
    DEFAULT_CHIP,
    NUMBER_SPOTS,
    OUTSIDE_SPOTS,
    ROULETTE_FOCUS_PROCESS,
    SPIN_BUTTON,
    UI_BY_NAME,
    ClickTarget,
)
from automation.roulette_layout_store import resolve_target
from automation.roulette_strategies import (
    StrategyBetPlan,
    decompose_units,
)
from automation.roulette_surface import surface_for
from automation.roulette_ui_areas import (
    CLIENT_H,
    CLIENT_W,
)


def _click(
    target: ClickTarget,
    *,
    ms: int = 90,
    mode: str = "post",
    calibrate: bool = True,
) -> dict[str, Any]:
    """
    mode:
      - ``post`` / ``post_sync``: PostMessage (invisible; preferred when Godot accepts it)
      - ``window``: SendInput absolute (moves cursor; reliable on Alegro godot)

    When *calibrate* is true, coordinates come from the active layout calibration
    (layout1 by default). Board-mapper nudges pass ``calibrate=False``.
    """
    if calibrate:
        target = resolve_target(target)
    if mode == "window":
        step_type = "click_window"
    elif mode == "post_sync":
        step_type = "click_post_sync"
    else:
        step_type = "click_post"
    return {
        "type": step_type,
        "value": target.as_spec(ROULETTE_FOCUS_PROCESS),
        "ms": ms,
    }


def _append_spin(steps: list[dict[str, Any]], *, click_mode: str) -> None:
    # Brief pause so START finishes enabling after the open-window settle
    # (countdown disc / bottom-right button stay grey for ~1s after the log).
    steps.append({"type": "sleep", "ms": 200})
    mode = click_mode if click_mode != "post_sync" else "post"
    for _ in range(2):
        steps.append(_click(SPIN_BUTTON, ms=130, mode=mode))
        steps.append({"type": "sleep", "ms": 160})


def script_for_random_bets_and_spin(
    *,
    min_bets: int = 3,
    max_bets: int = 7,
    include_outside: bool = True,
    press_spin: bool = True,
    seed: int | None = None,
    click_mode: str = "window",
) -> dict[str, Any]:
    """
    One betting-phase script: select a chip, place random bets, press START.

    Default ``click_mode=window`` uses SendInput absolute coords (works on Alegro
    ``godot`` when MainWindowHandle is 0). Use ``post`` for invisible PostMessage.
    """
    rng = random.Random(seed)
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 250},
    ]

    chip = DEFAULT_CHIP
    chip_mode = "post_sync" if click_mode.startswith("post") else "window"
    steps.append(_click(chip, ms=140, mode=chip_mode))
    steps.append({"type": "sleep", "ms": 120})

    pool: list[ClickTarget] = [NUMBER_SPOTS[n] for n in range(37)]
    if include_outside:
        pool.extend(OUTSIDE_SPOTS)

    n_bets = rng.randint(min_bets, max_bets)
    picks = [rng.choice(pool) for _ in range(n_bets)]
    mode = click_mode if click_mode != "post_sync" else "post"
    for spot in picks:
        steps.append(_click(spot, ms=90, mode=mode))
        steps.append({"type": "sleep", "ms": int(rng.uniform(60, 140))})

    if press_spin:
        _append_spin(steps, click_mode=click_mode)

    return {
        "defaultKeyDelayMs": 35,
        "steps": steps,
        "meta": {
            "chip": chip.name,
            "bets": [p.name for p in picks],
            "spin": press_spin,
            "click_mode": click_mode,
            "strategy": "random",
            "stake_units": n_bets,
        },
    }


def script_for_board_scan(
    targets: list[ClickTarget],
    *,
    press_spin: bool = False,
    click_mode: str = "window",
    clear_every: int = 8,
) -> dict[str, Any]:
    """
    Click every target in *targets* during one open window.

    Chips / UI buttons are clicked as controls. Bet surfaces use chip_1 and
    CANCELAR TODO every *clear_every* bet clicks to limit credit burn.
    """
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 250},
    ]
    chip_mode = "post_sync" if click_mode.startswith("post") else "window"
    click_m = click_mode if click_mode != "post_sync" else "post"
    chip_names = {c.name for c in CHIP_SPOTS}
    ui_names = set(UI_BY_NAME)
    labels: list[str] = []
    bet_since_clear = 0
    chip_armed = False

    steps.append(_click(CANCEL_ALL_BUTTON, ms=100, mode=click_m))
    steps.append({"type": "sleep", "ms": 120})

    for target in targets:
        if target.name in chip_names or target.name in ui_names:
            steps.append(_click(target, ms=110, mode=click_m))
            steps.append({"type": "sleep", "ms": 90})
            labels.append(target.name)
            if target.name in chip_names:
                chip_armed = target.name == DEFAULT_CHIP.name
            continue

        if not chip_armed:
            steps.append(_click(DEFAULT_CHIP, ms=120, mode=chip_mode))
            steps.append({"type": "sleep", "ms": 90})
            steps.append(_click(DEFAULT_CHIP, ms=100, mode=chip_mode))
            steps.append({"type": "sleep", "ms": 90})
            chip_armed = True

        steps.append(_click(target, ms=70, mode=click_m))
        steps.append({"type": "sleep", "ms": 55})
        labels.append(target.name)
        bet_since_clear += 1
        if clear_every > 0 and bet_since_clear >= clear_every:
            steps.append(_click(CANCEL_ALL_BUTTON, ms=90, mode=click_m))
            steps.append({"type": "sleep", "ms": 100})
            bet_since_clear = 0
            chip_armed = False

    steps.append(_click(CANCEL_ALL_BUTTON, ms=100, mode=click_m))
    steps.append({"type": "sleep", "ms": 100})

    if press_spin:
        _append_spin(steps, click_mode=click_mode)

    return {
        "defaultKeyDelayMs": 35,
        "steps": steps,
        "meta": {
            "chip": DEFAULT_CHIP.name,
            "bets": labels,
            "spin": press_spin,
            "click_mode": click_mode,
            "strategy": "full_board",
            "stake_units": len(labels),
            "notes": f"board scan {len(labels)} clicks",
        },
    }


def script_for_strategy_plan(
    plan: StrategyBetPlan,
    *,
    press_spin: bool = True,
    click_mode: str = "window",
) -> dict[str, Any]:
    """
    Build a click script that places *plan* placements (multi-chip capable) then START.
    """
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 250},
    ]
    chip_mode = "post_sync" if click_mode.startswith("post") else "window"
    click_m = click_mode if click_mode != "post_sync" else "post"
    # Clear leftover cloth bets (click twice — first may only focus).
    for _ in range(2):
        steps.append(_click(CANCEL_ALL_BUTTON, ms=100, mode=click_m))
        steps.append({"type": "sleep", "ms": 120})
    bet_labels: list[str] = []
    last_chip_name: str | None = None

    for placement in plan.placements:
        if placement.units <= 0:
            continue
        for chip, count in decompose_units(placement.units):
            if chip.name != last_chip_name:
                # Double-select chip so a missed first click cannot leave prior denom.
                steps.append(_click(chip, ms=120, mode=chip_mode))
                steps.append({"type": "sleep", "ms": 80})
                steps.append(_click(chip, ms=100, mode=chip_mode))
                steps.append({"type": "sleep", "ms": 100})
                last_chip_name = chip.name
            for _ in range(count):
                steps.append(_click(placement.spot, ms=70, mode=click_m))
                steps.append({"type": "sleep", "ms": 70})
                bet_labels.append(f"{placement.spot.name}x{chip.name}")

    if press_spin:
        _append_spin(steps, click_mode=click_mode)

    return {
        "defaultKeyDelayMs": 35,
        "steps": steps,
        "meta": {
            "chip": last_chip_name or DEFAULT_CHIP.name,
            "bets": bet_labels,
            "spin": press_spin,
            "click_mode": click_mode,
            "strategy": plan.label,
            "stake_units": plan.stake_units,
            "notes": plan.notes,
        },
    }


def _point_spec(x_px: float, y_px: float, *, client_w: int = CLIENT_W, client_h: int = CLIENT_H) -> str:
    return (
        f"{ROULETTE_FOCUS_PROCESS}@"
        f"{x_px / client_w * 100:.3f},{y_px / client_h * 100:.3f}"
    )


def script_for_slider_set(
    value: float,
    *,
    name: str = "OPACITY_SLIDER",
    layout_id: str | None = None,
    use_drag: bool = False,
    from_value: float | None = None,
    settle_ms: int = 700,
) -> dict[str, Any]:
    """
    Set a mapped slider (OPACIDAD) to *value* in 0.0..1.0.

    A tap on the track is enough: Godot's DragScrollButton reports the picked X,
    so the handle jumps to the tapped position (verified within 1 px on .90).
    Set *use_drag* to grab the handle and slide it instead, which needs
    *from_value* (defaults to full opacity, where the handle parks).
    """
    surface = surface_for(layout_id)
    if name not in surface.sliders:
        raise KeyError(f"{name!r} is not a mapped slider; known: {tuple(surface.sliders)}")
    cw, ch = surface.client_w, surface.client_h
    x, y = surface.slider_value_to_px(name, value)
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 200},
    ]
    if use_drag:
        x0, y0 = surface.slider_value_to_px(name, 1.0 if from_value is None else from_value)
        steps.append(
            {
                "type": "drag_window",
                "value": (
                    f"{_point_spec(x0, y0, client_w=cw, client_h=ch)}>"
                    f"{x / cw * 100:.3f},{y / ch * 100:.3f}"
                ),
                "ms": 600,
            }
        )
    else:
        steps.append(
            {
                "type": "click_window",
                "value": _point_spec(x, y, client_w=cw, client_h=ch),
                "ms": 150,
            }
        )
    steps.append({"type": "sleep", "ms": max(0, settle_ms)})

    return {
        "defaultKeyDelayMs": 25,
        "steps": steps,
        "meta": {
            "strategy": "slider_set",
            "layout_id": surface.layout_id,
            "slider": name,
            "value": round(min(1.0, max(0.0, float(value))), 4),
            "handle_px": {"x": x, "y": y},
            "gesture": "drag" if use_drag else "tap",
        },
    }
