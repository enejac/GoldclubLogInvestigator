from automation.tutankhamen_symbols import (
    TUTANKHAMEN_SWEEP_COUNT,
    tutankhamen_row,
    tutankhamen_symbol_sweep_combos,
    tutankhamen_three_row_combo,
)


def test_tutankhamen_row() -> None:
    assert tutankhamen_row(0) == "0 0 0 0 0"
    assert tutankhamen_row(14) == "14 14 14 14 14"


def test_tutankhamen_three_row_combo_pipe() -> None:
    assert tutankhamen_three_row_combo(0, 1, 2) == "0 0 0 0 0|1 1 1 1 1|2 2 2 2 2"


def test_tutankhamen_symbol_sweep_non_overlapping_triplets() -> None:
    combos = tutankhamen_symbol_sweep_combos(wingraph_5x5=False)
    assert len(combos) == TUTANKHAMEN_SWEEP_COUNT == 4
    assert combos[0] == "0 0 0 0 0|1 1 1 1 1|2 2 2 2 2"
    assert combos[1] == "3 3 3 3 3|4 4 4 4 4|5 5 5 5 5"
    assert combos[-1] == "9 9 9 9 9|10 10 10 10 10|11 11 11 11 11"
    assert "14 14 14 14 14" not in "\n".join(combos)


def test_jackpot_symbols_rejected_in_sweep_rows() -> None:
    try:
        tutankhamen_row(14)
        raise AssertionError("expected ValueError for jackpot symbol 14")
    except ValueError as e:
        assert "jackpot" in str(e).lower()
