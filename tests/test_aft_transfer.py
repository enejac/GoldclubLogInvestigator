"""Tests for AFT transfer lexicon, parsers, and correlator."""

from __future__ import annotations

from aft_transfer import (
    AftBucket,
    AftLogEvent,
    correlate_aft_log_lines,
    correlate_aft_transfer,
    normalize_bucket_label,
    parse_aft_log_line,
)


def test_normalize_bucket_promo_and_cashable() -> None:
    assert normalize_bucket_label("promo") == AftBucket.NON_RESTRICTED
    assert normalize_bucket_label("non-restricted") == AftBucket.NON_RESTRICTED
    assert normalize_bucket_label("cashable") == AftBucket.CASHABLE
    assert normalize_bucket_label("nonCash") == AftBucket.RESTRICTED


def test_parse_onehand_promo_txn41() -> None:
    line = (
        "2026-06-15T15:00:30.748+01:00 INFO [:18554352] "
        "Withdraw successful GCC_ST_20664_01 in 100000(promo:0); 0(cashable:0); 0(cashable:0)"
    )
    ev = parse_aft_log_line(line, source="onehand_gm2au")
    assert ev is not None
    assert ev.bucket == AftBucket.NON_RESTRICTED
    assert ev.amount_cents == 100000


def test_parse_onehand_cashable_txn55() -> None:
    line = (
        "2026-06-17T09:31:42.498+01:00 INFO [:32303986] "
        "Withdraw successful GCC_ST_20664_01 in 100000(cashable:0); 0(cashable:0); 0(cashable:0)"
    )
    ev = parse_aft_log_line(line)
    assert ev is not None
    assert ev.bucket == AftBucket.CASHABLE


def test_parse_slotlog_cashless_in() -> None:
    line = (
        "2026-06-17T09:31:42.482+01:00 INFO  [GM2AU_aurumExecute] "
        "OneHand.AurumEGM - Cashless In: $10,000.00"
    )
    ev = parse_aft_log_line(line, source="slotlog")
    assert ev is not None
    assert ev.amount_dollars == 10000.0
    assert ev.bucket is None


def test_parse_aurum_amounts_mismatch_txn54() -> None:
    line = "2026-06-17T09:29:26.000+01:00 INFO AFT REQUESTED AND OBTAINED TRANSFER AMOUNTS MISMATCH"
    ev = parse_aft_log_line(line, source="aurum")
    assert ev is not None
    assert ev.status == "amounts_mismatch"


def test_correlate_happy_path_txn55() -> None:
    lines = [
        "TRANSFER REQUEST FROM SERVER STARTED Test Transaction55 Cashable(1000000)",
        "Withdraw successful GCC_ST_20664_01 in 100000(cashable:0); 0(cashable:0); 0(cashable:0)",
        "Transfer IN $10,000.00(cashable:0); $0.00(cashable:0); $0.00(cashable:0)",
        "OneHand.AurumEGM - Cashless In: $10,000.00",
    ]
    result = correlate_aft_log_lines(lines)
    assert AftBucket.CASHABLE in result.buckets_seen
    assert result.bucket_consistent is True
    assert result.suggested_issue_id is None


def test_correlate_mismatch_path_txn54() -> None:
    lines = [
        "AFT REQUESTED AND OBTAINED TRANSFER AMOUNTS MISMATCH",
        "TRANSFER REQUEST FROM SERVER FINISHED: TransferStatus(UNEXPECTED_ERROR), Cashable Com(0)",
    ]
    result = correlate_aft_log_lines(lines)
    assert result.suggested_issue_id == "GCI-AFT-001"
    assert result.bucket_consistent is False


def test_correlate_multiple_buckets_flags_issue() -> None:
    events = [
        AftLogEvent(source="a", raw_line="x", bucket=AftBucket.CASHABLE),
        AftLogEvent(source="b", raw_line="y", bucket=AftBucket.NON_RESTRICTED),
    ]
    result = correlate_aft_transfer(events)
    assert result.suggested_issue_id == "GCI-AFT-001"
    assert result.bucket_consistent is False
