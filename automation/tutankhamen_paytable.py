"""
Expected TutankhamenGSBHW wins for symbol-sweep forced combos @ bet mult 2 (total 20).

Sweep uses non-overlapping triplets: 0|1|2, 3|4|5, 6|7|8, 9|10|11 (symbols 12–14 excluded — jackpots).
Each forced row maps to payline L1 (top), L2 (middle), L3 (bottom) on the 3-row grid.

Validation compares GameData reported win tokens (LnW*, FEATURE[*]) against total_win
and, when calibrated, per-line pays for the three forced symbols.
"""

from __future__ import annotations

from automation.gamedata_parser import GameDataSpin, line_win_credits, sum_reported_win_credits

THEME_ID = "TutankhamenGSBHW"
BET_MULTIPLIER = 2
TOTAL_BET_CREDITS = 20

# Forced WinGraph row index -> GameData line number (lab 10.0.0.90).
FORCED_ROW_TO_LINE = {0: 1, 1: 2, 2: 3}

# Per-symbol 5-OAK line credit @ bet 20 on its forced row (calibrated from lab GameData).
SYMBOL_5OAK_LINE_CREDITS_AT_BET_20: dict[int, int] = {
    0: 3000,
    1: 5000,
    2: 3000,
    3: 200,
    4: 200,
    5: 200,
    6: 100,
    7: 100,
    8: 50,
    9: 600,
    10: 50,
}

EXPECTED_TOTAL_WIN: dict[int, int] = {
    0: 14_200,
    1: 600,
    2: 250,
    3: 650,
}


def expected_line_credit(*, symbol_id: int, row_index: int) -> int | None:
    _ = row_index
    return SYMBOL_5OAK_LINE_CREDITS_AT_BET_20.get(symbol_id)


def expected_total_win(sweep_index: int) -> int | None:
    return EXPECTED_TOTAL_WIN.get(sweep_index)


def expected_line_wins_for_rows(top: int, middle: int, bottom: int) -> dict[int, int]:
    out: dict[int, int] = {}
    for row_idx, sym in enumerate((top, middle, bottom)):
        line = FORCED_ROW_TO_LINE[row_idx]
        pay = expected_line_credit(symbol_id=sym, row_index=row_idx)
        if pay is not None:
            out[line] = pay
    return out


def validate_reported_credits(spin: GameDataSpin) -> tuple[bool, str]:
    if spin.total_win_credits is None:
        return False, "missing total_win_credits in GameData"
    win_field = spin.raw_fields[12] if len(spin.raw_fields) > 12 else ""
    reported = sum_reported_win_credits(win_field)
    if reported != spin.total_win_credits:
        return False, f"reported sum {reported} != total {spin.total_win_credits}"
    return True, f"reported credits ok ({reported})"


def validate_forced_row_line_wins(
    spin: GameDataSpin,
    *,
    top: int,
    middle: int,
    bottom: int,
) -> tuple[bool, str]:
    win_field = spin.raw_fields[12] if len(spin.raw_fields) > 12 else ""
    actual = line_win_credits(win_field)
    expected = expected_line_wins_for_rows(top, middle, bottom)
    if not expected:
        return True, "no per-line golden for these symbols yet"
    mismatches: list[str] = []
    for line, exp in expected.items():
        got = actual.get(line)
        if got is None:
            mismatches.append(f"L{line} missing (expected {exp})")
        elif got != exp:
            mismatches.append(f"L{line} expected {exp} got {got}")
    if mismatches:
        return False, "; ".join(mismatches)
    return True, "line pays ok"


def validate_spin(
    spin: GameDataSpin,
    *,
    sweep_index: int,
    top: int,
    middle: int,
    bottom: int,
) -> tuple[bool, str]:
    ok, msg = validate_reported_credits(spin)
    if not ok:
        return ok, msg

    line_ok, line_msg = validate_forced_row_line_wins(spin, top=top, middle=middle, bottom=bottom)
    if not line_ok:
        return False, line_msg

    expected_total = expected_total_win(sweep_index)
    if expected_total is not None and spin.total_win_credits != expected_total:
        return False, f"paytable mismatch: expected total {expected_total} actual={spin.total_win_credits}"

    if expected_total is not None:
        return True, f"paytable ok (total={spin.total_win_credits}; {line_msg})"
    return True, f"{msg}; {line_msg}"
