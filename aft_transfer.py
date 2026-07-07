"""
AFT transfer log lexicon, line parsers, and cross-layer correlator.

Canonical buckets: cashable | restricted | non_restricted
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AftBucket(str, Enum):
    CASHABLE = "cashable"
    RESTRICTED = "restricted"
    NON_RESTRICTED = "non_restricted"


# Aliases per source subsystem (lowercase keys for lookup).
AFT_TRANSFER_LEXICON: dict[AftBucket, tuple[str, ...]] = {
    AftBucket.CASHABLE: (
        "cashable",
        "wat_cashableinamt",
        "wat_cashableinamt",
    ),
    AftBucket.RESTRICTED: (
        "restricted",
        "noncash",
        "non_cash",
        "wat_noncashinamt",
        "wat_noncashinamt",
    ),
    AftBucket.NON_RESTRICTED: (
        "promo",
        "non-restricted",
        "nonrestricted",
        "non_restricted",
        "wat_promoinamt",
        "wat_promoinamt",
    ),
}

_ALIAS_TO_BUCKET: dict[str, AftBucket] = {}
for bucket, aliases in AFT_TRANSFER_LEXICON.items():
    for alias in aliases:
        _ALIAS_TO_BUCKET[alias.lower().replace("-", "").replace("_", "")] = bucket


def normalize_bucket_label(label: str | None) -> AftBucket | None:
    """Map a subsystem label to a canonical bucket."""
    if not label:
        return None
    key = label.strip().lower().replace("-", "").replace("_", "")
    if key in _ALIAS_TO_BUCKET:
        return _ALIAS_TO_BUCKET[key]
    if key == "nonrestricted":
        return AftBucket.NON_RESTRICTED
    if key.startswith("cashable"):
        return AftBucket.CASHABLE
    if key.startswith("promo"):
        return AftBucket.NON_RESTRICTED
    if key.startswith("restricted") or key.startswith("noncash"):
        return AftBucket.RESTRICTED
    return None


@dataclass(frozen=True, slots=True)
class AftLogEvent:
    source: str
    raw_line: str
    bucket: AftBucket | None = None
    amount_cents: int | None = None
    amount_dollars: float | None = None
    transaction_id: str | None = None
    status: str | None = None


@dataclass
class AftCorrelation:
    events: list[AftLogEvent] = field(default_factory=list)
    buckets_seen: set[AftBucket] = field(default_factory=set)
    transaction_ids: set[str] = field(default_factory=set)
    bucket_consistent: bool = True
    suggested_issue_id: str | None = None
    notes: list[str] = field(default_factory=list)


_RE_SASMSGR = re.compile(r"qGMID1:(?P<hex>0172[0-9A-Fa-f]+)", re.IGNORECASE)
_RE_AURUM_START = re.compile(
    r"TRANSFER REQUEST FROM SERVER STARTED.*?Transaction(?P<txn>\d+).*?"
    r"(?:Cashable\((?P<cash>\d+)\)|NonRestricted\((?P<nr>\d+)\)|Restricted\((?P<rest>\d+)\))",
    re.IGNORECASE,
)
_RE_AURUM_MISMATCH = re.compile(
    r"AFT REQUESTED AND OBTAINED TRANSFER AMOUNTS MISMATCH", re.IGNORECASE
)
_RE_AURUM_UNEXPECTED = re.compile(
    r"TRANSFER REQUEST FROM SERVER FINISHED:.*UNEXPECTED_ERROR", re.IGNORECASE
)
_RE_ONEHAND_WITHDRAW = re.compile(
    r"Withdraw successful GCC_ST_\d+_01 in (?P<amt>\d+)\((?P<bucket>promo|cashable|restricted):(?P<pool>\d+)\)",
    re.IGNORECASE,
)
_RE_ONEHAND_TRANSFER_IN = re.compile(
    r"Transfer IN \$(?P<dollars>[\d,]+\.\d{2})\((?P<bucket>promo|cashable|restricted):(?P<pool>\d+)\)",
    re.IGNORECASE,
)
_RE_SLOTLOG_CASHLESS = re.compile(
    r"Cashless In: \$(?P<dollars>[\d,]+\.\d{2})", re.IGNORECASE
)
_RE_SLOTLOG_CREDIT_STATE = re.compile(
    r"Aurum (?P<bucket>cashable|promo|restricted) credit state increased to (?P<amt>\d+)",
    re.IGNORECASE,
)
_RE_TXN_IN_LINE = re.compile(r"Test Transaction(?P<txn>\d+)", re.IGNORECASE)


def _parse_dollars(text: str) -> float:
    return float(text.replace(",", ""))


def parse_aft_log_line(line: str, *, source: str = "unknown") -> AftLogEvent | None:
    """Parse one log line into an AFT event when recognized."""
    if not line or not line.strip():
        return None

    m = _RE_SASMSGR.search(line)
    if m:
        txn = None
        hex_payload = m.group("hex")
        tm = re.search(r"657374205472616E73616374696F6E(?P<txn>[0-9A-Fa-f]{2})", hex_payload, re.I)
        if tm:
            try:
                txn_ascii = bytes.fromhex(tm.group("txn")).decode("ascii", errors="ignore")
                txn = f"Test Transaction{txn_ascii}"
            except ValueError:
                pass
        return AftLogEvent(source=source or "sasmsgr", raw_line=line.strip(), transaction_id=txn)

    m = _RE_AURUM_MISMATCH.search(line)
    if m:
        return AftLogEvent(
            source=source or "aurum",
            raw_line=line.strip(),
            status="amounts_mismatch",
        )

    m = _RE_AURUM_UNEXPECTED.search(line)
    if m:
        return AftLogEvent(
            source=source or "aurum",
            raw_line=line.strip(),
            status="unexpected_error",
        )

    m = _RE_AURUM_START.search(line)
    if m:
        txn = m.group("txn")
        transaction_id = f"Test Transaction{txn}" if txn else None
        bucket = None
        amount = None
        if m.group("cash"):
            bucket = AftBucket.CASHABLE
            amount = int(m.group("cash"))
        elif m.group("nr"):
            bucket = AftBucket.NON_RESTRICTED
            amount = int(m.group("nr"))
        elif m.group("rest"):
            bucket = AftBucket.RESTRICTED
            amount = int(m.group("rest"))
        return AftLogEvent(
            source=source or "aurum",
            raw_line=line.strip(),
            bucket=bucket,
            amount_cents=amount,
            transaction_id=transaction_id,
            status="started",
        )

    m = _RE_ONEHAND_WITHDRAW.search(line)
    if m:
        bucket = normalize_bucket_label(m.group("bucket"))
        return AftLogEvent(
            source=source or "onehand_gm2au",
            raw_line=line.strip(),
            bucket=bucket,
            amount_cents=int(m.group("amt")),
            status="withdraw",
        )

    m = _RE_ONEHAND_TRANSFER_IN.search(line)
    if m:
        bucket = normalize_bucket_label(m.group("bucket"))
        return AftLogEvent(
            source=source or "onehand_txn_events",
            raw_line=line.strip(),
            bucket=bucket,
            amount_dollars=_parse_dollars(m.group("dollars")),
            status="transfer_in",
        )

    m = _RE_SLOTLOG_CASHLESS.search(line)
    if m:
        return AftLogEvent(
            source=source or "slotlog",
            raw_line=line.strip(),
            amount_dollars=_parse_dollars(m.group("dollars")),
            status="cashless_in",
        )

    m = _RE_SLOTLOG_CREDIT_STATE.search(line)
    if m:
        bucket = normalize_bucket_label(m.group("bucket"))
        return AftLogEvent(
            source=source or "slotlog",
            raw_line=line.strip(),
            bucket=bucket,
            amount_cents=int(m.group("amt")),
            status="credit_state",
        )

    tm = _RE_TXN_IN_LINE.search(line)
    if tm and "TRANSFER" in line.upper():
        return AftLogEvent(
            source=source or "aurum",
            raw_line=line.strip(),
            transaction_id=f"Test Transaction{tm.group('txn')}",
        )

    return None


def parse_aft_log_lines(lines: list[str]) -> list[AftLogEvent]:
    """Parse many lines; skips unrecognized lines."""
    out: list[AftLogEvent] = []
    for line in lines:
        ev = parse_aft_log_line(line)
        if ev is not None:
            out.append(ev)
    return out


def correlate_aft_transfer(events: list[AftLogEvent]) -> AftCorrelation:
    """
    Correlate parsed AFT events in a time window.

    Sets ``suggested_issue_id`` to GCI-AFT-001 when bucket labels disagree or
    Aurum reports amounts mismatch / unexpected error.
    """
    result = AftCorrelation(events=list(events))
    for ev in events:
        if ev.bucket is not None:
            result.buckets_seen.add(ev.bucket)
        if ev.transaction_id:
            result.transaction_ids.add(ev.transaction_id)
        if ev.status in ("amounts_mismatch", "unexpected_error"):
            result.bucket_consistent = False
            result.suggested_issue_id = "GCI-AFT-001"
            result.notes.append(f"Aurum status: {ev.status}")

    if len(result.buckets_seen) > 1:
        result.bucket_consistent = False
        result.suggested_issue_id = "GCI-AFT-001"
        buckets = ", ".join(sorted(b.value for b in result.buckets_seen))
        result.notes.append(f"Multiple canonical buckets in window: {buckets}")

    return result


def correlate_aft_log_lines(lines: list[str]) -> AftCorrelation:
    """Convenience: parse lines then correlate."""
    return correlate_aft_transfer(parse_aft_log_lines(lines))
