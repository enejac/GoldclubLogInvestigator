"""
Alegro Godot roulette (RouletteGUI2) click targets as client-area percentages.

Calibrated from live 1920x1080 screenshots on lab cabinet 10.0.0.90
(American layout with 0/00, Spanish UI, START timer at top-center).
"""

from __future__ import annotations

from dataclasses import dataclass

ROULETTE_FOCUS_PROCESS = "godot"
ROULETTE_WINDOW_TITLE = "RouletteGUI2"

# American grid geometry (client %)
_ZERO00 = (9.0, 42.0)
_ZERO0 = (9.0, 54.0)
_GRID_LEFT = 14.5
_GRID_RIGHT = 81.5
_ROW_TOP = 40.0  # 3,6,9,...,36
_ROW_MID = 48.5  # 2,5,8,...,35
_ROW_BOT = 57.0  # 1,4,7,...,34
_ROW_YS = (_ROW_BOT, _ROW_MID, _ROW_TOP)

_CHIP_PCT: list[tuple[str, float, float]] = [
    ("chip_1", 39.0, 90.5),
    ("chip_5", 45.0, 90.5),
    ("chip_10", 50.0, 91.0),
    ("chip_50", 55.5, 90.5),
    ("chip_100", 61.0, 90.5),
]

_OUTSIDE_PCT: list[tuple[str, float, float]] = [
    ("1-12", 28.0, 28.5),
    ("13-24", 50.0, 28.5),
    ("25-36", 72.0, 28.5),
    ("1-18", 18.0, 72.0),
    ("ODD", 30.0, 72.0),
    ("RED", 49.5, 72.0),  # layout1 verified via board mapper (RCM -10u)
    ("BLACK", 55.5, 72.0),
    ("EVEN", 68.0, 72.0),
    ("19-36", 82.0, 72.0),
    ("2to1_top", 86.5, 40.0),
    ("2to1_mid", 86.5, 48.5),
    ("2to1_bot", 86.5, 57.0),
]

# Top / bottom chrome (safe automation targets — no COBRAR / LLAMAR).
_UI_PCT: list[tuple[str, float, float]] = [
    # Top-right game/layout selector (cards+dice icon, directly above the lock).
    # Lab .90 2026-07-24: button box 1454,7-1513,65 px.
    # NOT the bottom-right ChangeView (square<->race view).
    ("LAYOUT_SWITCH", 77.24, 3.33),
    # Top info bar: tapping CRÉDITO swaps the readouts between credits and currency.
    ("CREDIT_DISPLAY", 9.92, 6.16),
    # History strip: stats panel opener + paging chevrons.
    ("STATISTICS", 2.92, 16.48),
    ("HISTORY_PREV", 6.12, 16.76),
    ("HISTORY_NEXT", 93.75, 16.76),
    ("VECINOS", 18.0, 83.0),
    ("FINALES", 26.0, 83.0),
    ("COMPLETO", 34.0, 83.0),
    ("VECINOS_0", 70.0, 83.0),
    ("HUERFANOS", 78.0, 83.0),
    ("VECINOS_00", 86.5, 83.0),
    # Square <-> race-track toggle (Godot ChangeView / SquareToRace).
    # Lab map 2026-07-24 on .90: icon centroid right of 19-36, above VECINOS DEL 00.
    # Click verified: red-cell heuristic 80537 -> 60643 -> 80537 (square/race/square).
    ("CHANGE_VIEW", 94.5, 73.9),
    ("DENOM", 7.5, 91.5),
    ("MUESTRA_GANANCIAS", 14.0, 91.5),
    ("CALIENTE_FRIO", 20.5, 91.5),
    ("PAYTABLE", 28.0, 91.5),
    # BORRADOR = drag-eraser (does NOT clear board). CANCELAR TODO is to its right.
    # Lab calibrate 2026-07-24: board clears only for cancel x_pct >= 83 at y=90.5
    # (80% kept the bet — was missing CANCELAR TODO).
    ("BORRADOR", 74.0, 90.5),
    ("CANCELAR_TODO", 85.0, 90.5),
    ("REPETIR", 91.0, 90.5),
]

_START_PCT = (49.5, 6.5)


@dataclass(frozen=True, slots=True)
class ClickTarget:
    name: str
    x_pct: float
    y_pct: float

    def as_spec(self, process: str = ROULETTE_FOCUS_PROCESS) -> str:
        return f"{process}@{self.x_pct:.2f},{self.y_pct:.2f}"


def _col_x(col: int) -> float:
    return _GRID_LEFT + (col + 0.5) * (_GRID_RIGHT - _GRID_LEFT) / 12.0


def _american_number_positions() -> dict[int, ClickTarget]:
    """Map 0-36 -> click targets. Key 37 is used for 00 (double-zero)."""
    out: dict[int, ClickTarget] = {
        0: ClickTarget("0", *_ZERO0),
        37: ClickTarget("00", *_ZERO00),
    }
    for col in range(12):
        x = _col_x(col)
        for row, y, base in (
            (0, _ROW_BOT, 1),
            (1, _ROW_MID, 2),
            (2, _ROW_TOP, 3),
        ):
            n = col * 3 + base
            out[n] = ClickTarget(str(n), x, y)
    return out


def _inside_line_targets() -> list[ClickTarget]:
    """Splits, streets, corners, six-lines + common 0/00 edges."""
    out: list[ClickTarget] = []
    # Vertical splits (same column): 1-2, 2-3, 4-5, ...
    for col in range(12):
        for r0, r1 in ((0, 1), (1, 2)):
            n0 = col * 3 + (r0 + 1)
            n1 = col * 3 + (r1 + 1)
            x = _col_x(col)
            y = (_ROW_YS[r0] + _ROW_YS[r1]) / 2.0
            out.append(ClickTarget(f"split_{n0}_{n1}", x, y))
    # Horizontal splits (same row, adjacent columns)
    for row in range(3):
        for col in range(11):
            n0 = col * 3 + (row + 1)
            n1 = (col + 1) * 3 + (row + 1)
            x = (_col_x(col) + _col_x(col + 1)) / 2.0
            y = _ROW_YS[row]
            out.append(ClickTarget(f"split_{n0}_{n1}", x, y))
    # Streets (three numbers in a column)
    for col in range(12):
        n0 = col * 3 + 1
        x = _col_x(col)
        y = _ROW_MID
        out.append(ClickTarget(f"street_{n0}_{n0+1}_{n0+2}", x, y))
    # Corners (2x2)
    for col in range(11):
        for row in range(2):
            a = col * 3 + (row + 1)
            b = a + 1
            c = (col + 1) * 3 + (row + 1)
            d = c + 1
            x = (_col_x(col) + _col_x(col + 1)) / 2.0
            y = (_ROW_YS[row] + _ROW_YS[row + 1]) / 2.0
            out.append(ClickTarget(f"corner_{a}_{b}_{c}_{d}", x, y))
    # Six-lines (two adjacent streets)
    for col in range(11):
        a = col * 3 + 1
        x = (_col_x(col) + _col_x(col + 1)) / 2.0
        y = (_ROW_BOT + _ROW_TOP) / 2.0
        out.append(ClickTarget(f"sixline_{a}_{a+5}", x, y))
    # Zero basket / common edges
    out.extend(
        [
            ClickTarget("split_0_00", (_ZERO0[0] + _ZERO00[0]) / 2.0, (_ZERO0[1] + _ZERO00[1]) / 2.0),
            ClickTarget("split_0_1", (_ZERO0[0] + _col_x(0)) / 2.0, (_ZERO0[1] + _ROW_BOT) / 2.0),
            ClickTarget("split_0_2", (_ZERO0[0] + _col_x(0)) / 2.0, (_ZERO0[1] + _ROW_MID) / 2.0),
            ClickTarget("split_00_2", (_ZERO00[0] + _col_x(0)) / 2.0, (_ZERO00[1] + _ROW_MID) / 2.0),
            ClickTarget("split_00_3", (_ZERO00[0] + _col_x(0)) / 2.0, (_ZERO00[1] + _ROW_TOP) / 2.0),
            ClickTarget("basket_0_00_1_2_3", (_ZERO0[0] + _col_x(0)) / 2.0, _ROW_MID),
        ]
    )
    return out


NUMBER_SPOTS: dict[int, ClickTarget] = _american_number_positions()

CHIP_SPOTS: list[ClickTarget] = [
    ClickTarget(name, x, y) for name, x, y in _CHIP_PCT
]

DEFAULT_CHIP = CHIP_SPOTS[0]

OUTSIDE_SPOTS: list[ClickTarget] = [
    ClickTarget(name, x, y) for name, x, y in _OUTSIDE_PCT
]

UI_BUTTONS: list[ClickTarget] = [
    ClickTarget(name, x, y) for name, x, y in _UI_PCT
]

UI_BY_NAME: dict[str, ClickTarget] = {b.name: b for b in UI_BUTTONS}

INSIDE_LINE_SPOTS: list[ClickTarget] = _inside_line_targets()

SPIN_BUTTON = ClickTarget("START", *_START_PCT)
CANCEL_ALL_BUTTON = UI_BY_NAME["CANCELAR_TODO"]
CLEAR_ONE_BUTTON = UI_BY_NAME["BORRADOR"]
REPEAT_BUTTON = UI_BY_NAME["REPETIR"]


def all_bet_spots() -> list[ClickTarget]:
    """Straight + outside (legacy random pool)."""
    spots = list(NUMBER_SPOTS.values())
    spots.extend(OUTSIDE_SPOTS)
    return spots


def all_bet_targets() -> list[ClickTarget]:
    """Every betting surface: straights, outside, splits/streets/corners/six-lines."""
    spots = list(NUMBER_SPOTS.values())
    spots.extend(OUTSIDE_SPOTS)
    spots.extend(INSIDE_LINE_SPOTS)
    return spots


def all_scan_targets(*, include_ui: bool = True, include_chips: bool = True) -> list[ClickTarget]:
    """Full board scan order: chips -> UI -> every bet surface."""
    out: list[ClickTarget] = []
    if include_chips:
        out.extend(CHIP_SPOTS)
    if include_ui:
        out.extend(UI_BUTTONS)
    out.extend(all_bet_targets())
    return out


def board_catalog() -> dict[str, int]:
    return {
        "straights": len(NUMBER_SPOTS),
        "outside": len(OUTSIDE_SPOTS),
        "inside_lines": len(INSIDE_LINE_SPOTS),
        "chips": len(CHIP_SPOTS),
        "ui_buttons": len(UI_BUTTONS),
        "bet_targets": len(all_bet_targets()),
        "scan_targets": len(all_scan_targets()),
    }
