"""Unit tests for bet-spot verification logic (no cabinet)."""

from __future__ import annotations

from automation import roulette_verify_bets as vb


def test_expected_numbers_straights_and_double_zero() -> None:
    assert vb.expected_numbers("17") == (17,)
    assert vb.expected_numbers("0") == (0,)
    # 00 is number 37 everywhere in the PlaceBet grammar and in PlayerDataBets.
    assert vb.expected_numbers("00") == (37,)
    assert vb.expected_numbers("NOT_A_BET") is None


def test_expected_numbers_outside_coverage() -> None:
    assert vb.expected_numbers("1-12") == tuple(range(1, 13))
    assert vb.expected_numbers("19-36") == tuple(range(19, 37))
    assert vb.expected_numbers("2to1_bot") == (1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34)
    assert vb.expected_numbers("2to1_top") == (3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36)
    assert 0 not in vb.expected_numbers("RED")
    assert set(vb.expected_numbers("RED")) & set(vb.expected_numbers("BLACK")) == set()
    assert len(vb.expected_numbers("RED")) == 18


def test_reported_numbers_reads_middleware_shapes() -> None:
    assert vb.reported_numbers({"Id": "17", "BetType": "Fields"}) == (17,)
    assert vb.reported_numbers({"Id": "1+2+3", "BetType": "Street"}) == (1, 2, 3)
    assert vb.reported_numbers({"Id": "Red", "Numbers": [3, 1, 5]}) == (1, 3, 5)
    assert vb.reported_numbers({"Id": "Red"}) == ()


def test_batching_keeps_outside_bets_alone() -> None:
    queue = ["1", "2", "RED", "3"]
    assert vb._next_batch(queue, 3, 9, 2) == ["1", "2"]
    assert vb._next_batch(queue, 3, 9, 2) == ["RED"]
    assert queue == ["3"]


def test_batching_is_capped_by_spare_credits() -> None:
    # One credit above the floor leaves room for a single standing chip.
    assert vb._next_batch(["1", "2", "3"], 3, 3, 2) == ["1", "2"]
    assert vb._next_batch(["1", "2", "3"], 3, 2, 2) == ["1"]


def test_bet_targets_covers_all_mapped_spots() -> None:
    targets = vb.bet_targets()
    assert len(targets) == 50
    assert targets[:2] == ["0", "00"]
    assert len([t for t in targets if t.isdigit() and t not in ("0", "00")]) == 36
    assert len([t for t in targets if t in vb.OUTSIDE_COVERAGE]) == 12
