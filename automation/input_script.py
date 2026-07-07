from __future__ import annotations

from dataclasses import dataclass

from automation.cabinet_preflight import (
    merge_input_scripts,
    script_for_multigamer_select_tutankhamen,
)
from automation.tutankhamen_symbols import (
    tutankhamen_combo_0_1_2,
    tutankhamen_symbol_sweep_combos_text,
)

WINGRAPH_GRID_ROWS = 5
WINGRAPH_GRID_COLS = 5
WINGRAPH_CONTENT_ROWS = 3  # only top 3 visible symbol rows (manual workflow)

# WinGraph Control client-area click targets (%), calibrated from lab Force Win layout.
# Tweak in one place if dialog scale differs on another cabinet.
WINGRAPH_WINDOW_TITLE = "WinGraph"
# Force Win is the leftmost tab; ~12% hits the State tab on lab layout (see screenshots).
WINGRAPH_FORCE_WIN_TAB_PCT = (7.0, 4.0)
WINGRAPH_STATE_TAB_PCT = (22.0, 4.0)  # middle tab — never click or send keys here
WINGRAPH_GRID_ORIGIN_PCT = (7.0, 63.0)  # top-left spinner (row 0, col 0)
WINGRAPH_CELL_STEP_PCT = (5.25, 4.25)  # derived: cell (4,4) at ~28%,80%
WINGRAPH_SYMBOL_SPIN_PCT = (35.0, 68.0)  # "SYMBOL indexes SPIN" — right of grid
WINGRAPH_RESET_PCT = (46.0, 68.0)  # "reset" between SYMBOL SPIN and REEL STOP
WINGRAPH_FORCE_WIN_CHECKBOX_PCT = (18.0, 92.0)
WINGRAPH_APPLY_PCT = (52.0, 92.0)
WINGRAPH_CLOSE_PCT = (78.0, 92.0)  # Close button — never ESC

# OneHand hotkeys — never send after grid fill or spin click:
#   E    = exit (closes OneHand)
#   ESC  = closes WinGraph / game UI
WINGRAPH_FORBIDDEN_KEYS = frozenset({"E", "ESC", "ESCAPE"})


@dataclass(frozen=True, slots=True)
class InputStep:
    type: str  # "key" | "text" | "sleep" | "click_window"
    value: str | None = None
    ms: int | None = None


TUTANKHAMEN_GSBHW_LINES_0_1_2 = tutankhamen_combo_0_1_2(pipe_separated=False, wingraph_5x5=True)
TUTANKHAMEN_GSBHW_LINES_0_1_2_PIPE = tutankhamen_combo_0_1_2(pipe_separated=True, wingraph_5x5=True)
TUTANKHAMEN_GSBHW_SYMBOL_SWEEP_PIPE_TEXT = tutankhamen_symbol_sweep_combos_text(
    pipe_separated=True, wingraph_5x5=True
)


def normalize_combo_text(combo: str, *, row_sep: str = "\t") -> str:
    if "|" in combo:
        return row_sep.join(part.strip() for part in combo.split("|") if part.strip())
    return combo.replace("\n", row_sep)


def _split_combo_rows(combo_text: str) -> list[str]:
    if "|" in combo_text:
        return [p.strip() for p in combo_text.split("|") if p.strip()]
    return [ln.strip() for ln in combo_text.splitlines() if ln.strip()]


def _parse_row_symbols(combo_text: str) -> list[int]:
    return [int(row.split()[0]) for row in _split_combo_rows(combo_text)]


def combo_to_wingraph_grid(
    combo_text: str,
    *,
    grid_rows: int = WINGRAPH_GRID_ROWS,
    content_rows_only: bool = True,
    pad_symbol: int = 0,
) -> list[list[int]]:
    """
    Build the WinGraph SYMBOL indexes grid from pipe-separated row specs.

    Default: **3 content rows x 5 cols** only (do not touch padded rows 4-5).
    """
    row_symbols = _parse_row_symbols(combo_text)
    if content_rows_only:
        row_symbols = row_symbols[:WINGRAPH_CONTENT_ROWS]
        return [[sym] * WINGRAPH_GRID_COLS for sym in row_symbols]
    if len(row_symbols) > grid_rows:
        raise ValueError(f"combo has {len(row_symbols)} rows; WinGraph grid allows {grid_rows}")
    padded = row_symbols + [pad_symbol] * (grid_rows - len(row_symbols))
    return [[sym] * WINGRAPH_GRID_COLS for sym in padded]


def _steps_spinner_value(symbol_id: int) -> list[dict]:
    """
    Replace spinner value after cell focus.

    WinGraph spinners often keep a leading digit (e.g. ``1``) so typing ``4`` becomes ``14``
    (MEGA / magic-spin symbol). Clear aggressively, neutralize to ``0``, then type digits slowly.
    """
    if symbol_id < 0 or symbol_id > 14:
        raise ValueError(f"symbol_id must be 0..14, got {symbol_id}")
    steps: list[dict] = [{"type": "sleep", "ms": 100}]
    for _ in range(6):
        steps.extend(
            [
                {"type": "key", "value": "BACKSPACE"},
                {"type": "sleep", "ms": 20},
            ]
        )
    steps.extend(
        [
            {"type": "key", "value": "DELETE"},
            {"type": "sleep", "ms": 30},
            {"type": "key", "value": "^a"},
            {"type": "sleep", "ms": 40},
            {"type": "text", "value": "0"},
            {"type": "sleep", "ms": 50},
            {"type": "key", "value": "^a"},
            {"type": "sleep", "ms": 40},
        ]
    )
    for ch in str(symbol_id):
        steps.extend(
            [
                {"type": "text", "value": ch},
                {"type": "sleep", "ms": 70 if symbol_id >= 10 else 55},
            ]
        )
    steps.append({"type": "sleep", "ms": 90})
    return steps


def _wingraph_preflight_steps() -> list[dict]:
    """
    After F11 opens WinGraph: stay on Force Win (not State), clear stale grid via reset,
    anchor keyboard focus on the symbol grid before typing.
    """
    tab = wingraph_force_win_tab_click_target()
    reset = wingraph_reset_click_target()
    anchor = wingraph_cell_click_target(0, 0)
    return [
        {"type": "focus_window", "value": WINGRAPH_WINDOW_TITLE, "ms": 250},
        {"type": "click_window", "value": tab, "ms": 120},
        {"type": "sleep", "ms": 120},
        {"type": "click_window", "value": tab, "ms": 120},
        {"type": "sleep", "ms": 150},
        {"type": "click_window", "value": reset, "ms": 150},
        {"type": "sleep", "ms": 250},
        {"type": "click_window", "value": anchor, "ms": 100},
        {"type": "sleep", "ms": 120},
    ]


def wingraph_cell_click_target(row: int, col: int) -> str:
    ox, oy = WINGRAPH_GRID_ORIGIN_PCT
    sx, sy = WINGRAPH_CELL_STEP_PCT
    x = ox + col * sx
    y = oy + row * sy
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_symbol_spin_click_target() -> str:
    x, y = WINGRAPH_SYMBOL_SPIN_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_force_win_tab_click_target() -> str:
    x, y = WINGRAPH_FORCE_WIN_TAB_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_reset_click_target() -> str:
    x, y = WINGRAPH_RESET_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_force_win_checkbox_click_target() -> str:
    x, y = WINGRAPH_FORCE_WIN_CHECKBOX_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_close_click_target() -> str:
    x, y = WINGRAPH_CLOSE_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def wingraph_apply_click_target() -> str:
    x, y = WINGRAPH_APPLY_PCT
    return f"{WINGRAPH_WINDOW_TITLE}@{x:.1f},{y:.1f}"


def _wingraph_close_steps() -> list[dict]:
    """Dismiss WinGraph via Close (not ESC) so the next F11 opens a clean dialog."""
    close = wingraph_close_click_target()
    return [
        {"type": "focus_window", "value": WINGRAPH_WINDOW_TITLE, "ms": 200},
        {"type": "click_window", "value": close, "ms": 150},
        {"type": "sleep", "ms": 400},
    ]


def _assert_no_forbidden_keys(steps: list[dict]) -> None:
    """Raise if script would send OneHand exit keys (E) or UI-close keys (ESC)."""
    for step in steps:
        if step.get("type") != "key":
            continue
        val = (step.get("value") or "").strip().upper()
        if val in WINGRAPH_FORBIDDEN_KEYS:
            raise ValueError(f"forbidden key in WinGraph script: {val!r}")


def script_for_wingraph_symbol_indexes_spin(
    grid: list[list[int]],
    *,
    open_f11: bool = True,
    close_after_spin: bool = True,
    open_delay_ms: int = 800,
    cell_delay_ms: int = 90,
) -> dict:
    """
    Automate WinGraph Control (F11) via **mouse clicks** (keyboard TAB does not work).

    Fills **3 rows x 5 cols** only, sets values via clear + type, clicks SYMBOL indexes SPIN.
    Closes WinGraph after spin so the next combo starts from F11 + reset (avoids stale ``14`` cells).

    Never sends E or ESC (both close OneHand / game UI).
  """
    if len(grid) != WINGRAPH_CONTENT_ROWS or any(len(row) != WINGRAPH_GRID_COLS for row in grid):
        raise ValueError(f"grid must be {WINGRAPH_CONTENT_ROWS}x{WINGRAPH_GRID_COLS}")

    steps: list[dict] = [{"type": "focus_process", "value": "OneHand", "ms": 150}]
    if open_f11:
        steps.extend(
            [
                {"type": "key", "value": "F11"},
                {"type": "sleep", "ms": open_delay_ms},
            ]
        )
    else:
        steps.extend(
            [
                {"type": "focus_window", "value": WINGRAPH_WINDOW_TITLE, "ms": 300},
                {"type": "sleep", "ms": 250},
            ]
        )
    steps.extend(_wingraph_preflight_steps())

    for row_idx, row in enumerate(grid):
        for col_idx, sym in enumerate(row):
            target = wingraph_cell_click_target(row_idx, col_idx)
            click_ms = 100 if sym >= 10 else 85
            steps.extend(
                [
                    {"type": "click_window", "value": target, "ms": click_ms},
                    {"type": "sleep", "ms": 50},
                    {"type": "click_window", "value": target, "ms": click_ms},
                ]
            )
            steps.extend(_steps_spinner_value(sym))
            steps.append({"type": "sleep", "ms": cell_delay_ms})

    # Refocus before spin — last cell edit can leave focus on a spinner.
    # Do not send E (exit OneHand) or ESC (closes WinGraph / game).
    spin_target = wingraph_symbol_spin_click_target()
    steps.extend(
        [
            {"type": "sleep", "ms": 300},
            {"type": "focus_window", "value": WINGRAPH_WINDOW_TITLE, "ms": 200},
            {"type": "click_window", "value": spin_target, "ms": 150},
            {"type": "sleep", "ms": 300},
            {"type": "click_window", "value": spin_target, "ms": 150},
            {"type": "sleep", "ms": 800},
        ]
    )
    if close_after_spin:
        steps.extend(_wingraph_close_steps())
    _assert_no_forbidden_keys(steps)
    return {"defaultKeyDelayMs": 35, "steps": steps}


def script_for_forced_combo(
    *,
    combo_text: str,
    spin_key: str = "SPACE",
    mode: str = "wingraph",
    include_game_select: bool = False,
    open_f11: bool = True,
) -> dict:
    """Build WinGraph forced-symbol script from pipe-separated rows (e.g. ``0..|1..|2..``)."""
    _ = spin_key
    if mode != "wingraph":
        raise ValueError(f"unsupported mode {mode!r}; use wingraph")
    grid = combo_to_wingraph_grid(combo_text)
    wingraph = script_for_wingraph_symbol_indexes_spin(grid, open_f11=open_f11)
    if not include_game_select:
        return wingraph
    return merge_input_scripts(script_for_multigamer_select_tutankhamen(), wingraph)


def script_tutankhamen_symbol_sweep_first() -> dict:
    return script_for_forced_combo(combo_text=TUTANKHAMEN_GSBHW_LINES_0_1_2_PIPE)
