"""
TutankhamenGSBHW F11 / WinGraph forced-reel symbol IDs (0-based math indices).
"""

from __future__ import annotations

TUTANKHAMEN_SYMBOL_NAMES: dict[int, str] = {
    0: "TOP1/WILD",
    1: "HIGH1/Pharaoh",
    2: "HIGH2/Anubis",
    3: "HIGH3/Ring",
    4: "HIGH4/Eye",
    5: "HIGH5/Ankh",
    6: "LOW1/A",
    7: "LOW2/K",
    8: "LOW3/Q",
    9: "LOW4/J",
    10: "LOW5/10",
    11: "SCA1/Scatter",
    12: "MINI",
    13: "MAJOR",
    14: "MEGA",
}

TUTANKHAMEN_SYMBOL_ID_MIN = 0
TUTANKHAMEN_SYMBOL_ID_MAX = 14

# MINI / MAJOR / MEGA — trigger hold-spin / magic-wheel; never use in F11 line-win sweeps.
TUTANKHAMEN_JACKPOT_SYMBOL_IDS = frozenset({12, 13, 14})

# Non-overlapping triplets covering line/scatter symbols 0–11 only (4 spins).
TUTANKHAMEN_SWEEP_GROUPS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (9, 10, 11),
)
TUTANKHAMEN_SWEEP_COUNT = len(TUTANKHAMEN_SWEEP_GROUPS)


def tutankhamen_row(symbol_id: int) -> str:
    if symbol_id < TUTANKHAMEN_SYMBOL_ID_MIN or symbol_id > TUTANKHAMEN_SYMBOL_ID_MAX:
        raise ValueError(f"symbol_id must be 0..14, got {symbol_id}")
    if symbol_id in TUTANKHAMEN_JACKPOT_SYMBOL_IDS:
        raise ValueError(f"jackpot symbol {symbol_id} must not be used in line-win F11 sweeps")
    return " ".join(str(symbol_id) for _ in range(5))


def tutankhamen_three_row_combo(
    top: int,
    middle: int,
    bottom: int,
    *,
    pipe_separated: bool = True,
) -> str:
    rows = (tutankhamen_row(top), tutankhamen_row(middle), tutankhamen_row(bottom))
    sep = "|" if pipe_separated else "\n"
    return sep.join(rows)


def tutankhamen_wingraph_5x5_combo(
    row_values: list[int],
    *,
    pipe_separated: bool = True,
    pad_symbol: int = 0,
) -> str:
    """WinGraph Force Win grid is 5x5; extra rows pad with *pad_symbol* (default 0)."""
    if len(row_values) < 1 or len(row_values) > 5:
        raise ValueError("row_values must have 1..5 entries")
    padded = list(row_values) + [pad_symbol] * (5 - len(row_values))
    rows = tuple(tutankhamen_row(v) for v in padded)
    sep = "|" if pipe_separated else "\n"
    return sep.join(rows)


def tutankhamen_combo_0_1_2(*, pipe_separated: bool = True, wingraph_5x5: bool = True) -> str:
    """Baseline: WILD / Pharaoh / Anubis on top three visible rows."""
    if wingraph_5x5:
        return tutankhamen_wingraph_5x5_combo([0, 1, 2], pipe_separated=pipe_separated)
    return tutankhamen_three_row_combo(0, 1, 2, pipe_separated=pipe_separated)


def tutankhamen_symbol_sweep_combos(*, pipe_separated: bool = True, wingraph_5x5: bool = True) -> list[str]:
    combos: list[str] = []
    for top, middle, bottom in TUTANKHAMEN_SWEEP_GROUPS:
        if wingraph_5x5:
            combos.append(
                tutankhamen_wingraph_5x5_combo([top, middle, bottom], pipe_separated=pipe_separated)
            )
        else:
            combos.append(tutankhamen_three_row_combo(top, middle, bottom, pipe_separated=pipe_separated))
    return combos


def tutankhamen_sweep_group(sweep_index: int) -> tuple[int, int, int]:
    if sweep_index < 0 or sweep_index >= TUTANKHAMEN_SWEEP_COUNT:
        raise ValueError(f"sweep_index must be 0..{TUTANKHAMEN_SWEEP_COUNT - 1}, got {sweep_index}")
    return TUTANKHAMEN_SWEEP_GROUPS[sweep_index]


def tutankhamen_symbol_sweep_combos_text(*, pipe_separated: bool = True, wingraph_5x5: bool = True) -> str:
    return "\n".join(tutankhamen_symbol_sweep_combos(pipe_separated=pipe_separated, wingraph_5x5=wingraph_5x5))
