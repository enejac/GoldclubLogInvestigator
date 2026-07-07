#!/usr/bin/env python3
"""
Hop 3 integration (read-only): summarize WAT2AFT transaction lifecycle and
correlate with Hop 4 evidence.

This script is intentionally NON-INJECTING. It only reads artifacts produced by
existing QA flows:
  - aft/test-runs/**/aft-transactions.json
  - aft/test-runs/**/slotlog-cashless-in.csv (optional correlation)
  - GoldClub.Aurum.Services/*.log (optional Aurum service log for Hop 3->4 timing)
  - SlotLog/*.log (optional SlotLog for Hop 4 Cashless In + credit state lines)

Outputs:
  - A concise markdown report with commit ordering, amounts, creditType, timing,
    and Hop 4 correlation (Cashless In match, credit state bump, Aurum service lines).
  - A JSONL stream suitable for further tooling.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


def _parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _safe_int(v):
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Hop3Txn:
    source_file: str
    instance_id: str | None
    transfer_status: str | None
    transfer_type: str | None
    account_id: str | None
    credit_type: str | None
    wat_amount: int | None
    commit_exception: int | None
    request_txn_id: int | None
    commit_txn_id: int | None
    initiated: datetime | None
    committed: datetime | None
    completed_bcd: str | None
    transaction_id_text: str | None
    obtained_cashable: int | None
    obtained_restricted: int | None
    obtained_nonrestricted: int | None

    @property
    def obtained_total(self) -> int:
        return (self.obtained_cashable or 0) + (self.obtained_restricted or 0) + (self.obtained_nonrestricted or 0)


def load_aft_transactions(path: Path) -> list[Hop3Txn]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    out: list[Hop3Txn] = []
    for row in raw:
        out.append(
            Hop3Txn(
                source_file=str(row.get("SourceFile") or ""),
                instance_id=row.get("InstanceId"),
                transfer_status=row.get("TransferStatus"),
                transfer_type=row.get("TransferType"),
                account_id=row.get("AccountId"),
                credit_type=row.get("CreditType"),
                wat_amount=_safe_int(row.get("WatAmount")),
                commit_exception=_safe_int(row.get("CommitTransferException")),
                request_txn_id=_safe_int(row.get("RequestTransactionId")),
                commit_txn_id=_safe_int(row.get("CommitTransactionId")),
                initiated=_parse_dt(row.get("TransferInitiatedTime")),
                committed=_parse_dt(row.get("CommitTransferDateTime")),
                completed_bcd=row.get("CompletedBcd"),
                transaction_id_text=row.get("TransactionIdText"),
                obtained_cashable=_safe_int(row.get("ObtainedCashable")),
                obtained_restricted=_safe_int(row.get("ObtainedRestricted")),
                obtained_nonrestricted=_safe_int(row.get("ObtainedNonRestricted")),
            )
        )
    return out


def load_slotlog_cashless_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()})
    return rows


def _iter_sorted(txns):
    def key(t):
        return (t.committed or t.initiated or datetime.min)
    return sorted(list(txns), key=key)


@dataclass(frozen=True)
class SlotlogCashlessEvent:
    timestamp: datetime | None
    amount_cents: int
    amount_text: str
    raw_line: str


@dataclass(frozen=True)
class AurumServiceEvent:
    timestamp: datetime | None
    event_type: str
    raw_line: str


def _parse_slotlog_timestamp(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def load_slotlog_cashless_events(path: Path) -> list[SlotlogCashlessEvent]:
    events: list[SlotlogCashlessEvent] = []
    rx_cashless = re.compile(
        r'^(?P<ts>\S+)\s+.*OneHand\.AurumEGM\s+-\s+Cashless In:\s*\$(?P<amt>[\d,]+\.\d{2})'
    )
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = rx_cashless.match(line)
            if m:
                ts = _parse_slotlog_timestamp(m.group("ts"))
                amt_text = m.group("amt").replace(",", "")
                cents = int(float(amt_text) * 100)
                events.append(SlotlogCashlessEvent(
                    timestamp=ts,
                    amount_cents=cents,
                    amount_text=m.group("amt"),
                    raw_line=line.rstrip(),
                ))
    return events


def load_aurum_service_events(path: Path) -> list[AurumServiceEvent]:
    events: list[AurumServiceEvent] = []
    rx_request_started = re.compile(
        r'^(?P<ts>\S+)\s+.*TRANSFER REQUEST FROM SERVER STARTED:.*'
    )
    rx_all_wat_finished = re.compile(
        r'^(?P<ts>\S+)\s+.*ALL WAT TRANSACTIONS FINISHED'
    )
    rx_request_finished = re.compile(
        r'^(?P<ts>\S+)\s+.*TRANSFER REQUEST FROM SERVER FINISHED:.*'
    )
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = rx_request_started.match(line)
            if m:
                events.append(AurumServiceEvent(
                    timestamp=_parse_slotlog_timestamp(m.group("ts")),
                    event_type="REQUEST_STARTED",
                    raw_line=line.rstrip(),
                ))
                continue
            m = rx_all_wat_finished.match(line)
            if m:
                events.append(AurumServiceEvent(
                    timestamp=_parse_slotlog_timestamp(m.group("ts")),
                    event_type="ALL_WAT_FINISHED",
                    raw_line=line.rstrip(),
                ))
                continue
            m = rx_request_finished.match(line)
            if m:
                events.append(AurumServiceEvent(
                    timestamp=_parse_slotlog_timestamp(m.group("ts")),
                    event_type="REQUEST_FINISHED",
                    raw_line=line.rstrip(),
                ))
    return events


@dataclass
class Hop4Correlation:
    slotlog_match: SlotlogCashlessEvent | None = None
    slotlog_delta_ms: float | None = None
    aurum_request_started: AurumServiceEvent | None = None
    aurum_all_wat_finished: AurumServiceEvent | None = None
    aurum_request_finished: AurumServiceEvent | None = None
    aurum_started_delta_ms: float | None = None
    aurum_finished_delta_ms: float | None = None


def _find_nearest_event(
    events,
    target: datetime,
    window_ms: int = 5000,
    amount_cents: int | None = None,
    event_type: str | None = None,
):
    best = None
    best_delta = None
    for ev in events:
        if ev.timestamp is None:
            continue
        if amount_cents is not None and isinstance(ev, SlotlogCashlessEvent):
            if ev.amount_cents != amount_cents:
                continue
        if event_type is not None and isinstance(ev, AurumServiceEvent):
            if ev.event_type != event_type:
                continue
        delta = abs((ev.timestamp - target).total_seconds() * 1000)
        if delta <= window_ms and (best_delta is None or delta < best_delta):
            best = ev
            best_delta = delta
    return best, best_delta


def correlate_hop4(
    txns: list[Hop3Txn],
    slotlog_events: list[SlotlogCashlessEvent] | None,
    aurum_events: list[AurumServiceEvent] | None,
) -> list[Hop4Correlation]:
    results: list[Hop4Correlation] = []
    for t in txns:
        corr = Hop4Correlation()
        commit_time = t.committed or t.initiated
        if commit_time is None:
            results.append(corr)
            continue

        if slotlog_events:
            ev, delta = _find_nearest_event(
                slotlog_events, commit_time,
                window_ms=5000,
                amount_cents=t.obtained_total,
            )
            corr.slotlog_match = ev
            corr.slotlog_delta_ms = delta

        if aurum_events:
            ev_started, _ = _find_nearest_event(
                aurum_events, commit_time,
                window_ms=5000,
                event_type="REQUEST_STARTED",
            )
            corr.aurum_request_started = ev_started
            if ev_started and ev_started.timestamp:
                corr.aurum_started_delta_ms = (commit_time - ev_started.timestamp).total_seconds() * 1000

            ev_finished, _ = _find_nearest_event(
                aurum_events, commit_time,
                window_ms=5000,
                event_type="ALL_WAT_FINISHED",
            )
            corr.aurum_all_wat_finished = ev_finished
            if ev_finished and ev_finished.timestamp:
                corr.aurum_finished_delta_ms = (ev_finished.timestamp - commit_time).total_seconds() * 1000

            ev_req_finished, _ = _find_nearest_event(
                aurum_events, commit_time,
                window_ms=30000,
                event_type="REQUEST_FINISHED",
            )
            corr.aurum_request_finished = ev_req_finished

        results.append(corr)
    return results


def render_markdown(
    txns: list[Hop3Txn],
    slotlog_rows: list[dict[str, Any]] | None,
    title: str,
    correlations: list[Hop4Correlation] | None = None,
    slotlog_events: list[SlotlogCashlessEvent] | None = None,
    aurum_events: list[AurumServiceEvent] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    lines.append(f"- **transactions**: {len(txns)}")
    ok = sum(1 for t in txns if (t.commit_exception == 0 and (t.transfer_status or "").endswith("SUCCESSFUL")))
    lines.append(f"- **commit ok (exception=0 & *SUCCESSFUL*)**: {ok}")
    if slotlog_rows is not None:
        lines.append(f"- **slotlog cashless rows (csv)**: {len(slotlog_rows)}")
    if slotlog_events is not None:
        lines.append(f"- **slotlog cashless events (log parsed)**: {len(slotlog_events)}")
    if aurum_events is not None:
        lines.append(f"- **aurum service events (log parsed)**: {len(aurum_events)}")
    lines.append("")

    if correlations is not None:
        matched = sum(1 for c in correlations if c.slotlog_match is not None)
        unmatched = len(correlations) - matched
        lines.append("### Hop 3 -> Hop 4 correlation summary")
        lines.append("")
        lines.append(f"- **matched to Cashless In**: {matched}")
        lines.append(f"- **unmatched (no Cashless In within 5s)**: {unmatched}")
        lines.append("")

    lines.append("### Commit timeline (Hop 3)")
    lines.append("")

    if correlations is not None:
        lines.append("| commit_time | commit_id | credit_type | wat_amount | obtained_total | status | exception | txn_text | source | hop4_cashless_in | hop4_delta_ms | aurum_started_ms | aurum_finished_ms |")
        lines.append("|---|---:|---|---:|---:|---|---:|---|---|---|---:|---:|---:|")
        for t, corr in zip(_iter_sorted(txns), correlations):
            ct = t.committed.isoformat() if t.committed else ""
            hop4_amt = f"${corr.slotlog_match.amount_text}" if corr.slotlog_match else ""
            hop4_delta = f"{corr.slotlog_delta_ms:.0f}" if corr.slotlog_delta_ms is not None else ""
            aurum_started = f"{corr.aurum_started_delta_ms:.0f}" if corr.aurum_started_delta_ms is not None else ""
            aurum_finished = f"{corr.aurum_finished_delta_ms:.0f}" if corr.aurum_finished_delta_ms is not None else ""
            lines.append(
                "| "
                + " | ".join([
                    ct,
                    str(t.commit_txn_id or ""),
                    str(t.credit_type or ""),
                    str(t.wat_amount or ""),
                    str(t.obtained_total),
                    str(t.transfer_status or ""),
                    str(t.commit_exception if t.commit_exception is not None else ""),
                    str(t.transaction_id_text or ""),
                    str(Path(t.source_file).name),
                    hop4_amt,
                    hop4_delta,
                    aurum_started,
                    aurum_finished,
                ])
                + " |"
            )
    else:
        lines.append("| commit_time | commit_id | credit_type | wat_amount | obtained_total | status | exception | txn_text | source |")
        lines.append("|---|---:|---|---:|---:|---|---:|---|---|")
        for t in _iter_sorted(txns):
            ct = t.committed.isoformat() if t.committed else ""
            lines.append(
                "| "
                + " | ".join([
                    ct,
                    str(t.commit_txn_id or ""),
                    str(t.credit_type or ""),
                    str(t.wat_amount or ""),
                    str(t.obtained_total),
                    str(t.transfer_status or ""),
                    str(t.commit_exception if t.commit_exception is not None else ""),
                    str(t.transaction_id_text or ""),
                    str(Path(t.source_file).name),
                ])
                + " |"
            )
    lines.append("")

    if correlations is not None:
        unmatched_txns = [
            (t, corr) for t, corr in zip(_iter_sorted(txns), correlations)
            if corr.slotlog_match is None
        ]
        if unmatched_txns:
            lines.append("### Unmatched transactions (no Hop 4 Cashless In within 5s)")
            lines.append("")
            for t, corr in unmatched_txns:
                ct = t.committed.isoformat() if t.committed else "(no commit time)"
                label = t.transaction_id_text or "(no label)"
                lines.append(f"- **{label}** @ {ct} -- {t.credit_type} ${t.obtained_total / 100:.2f}")
            lines.append("")

    lines.append("### Notes")
    lines.append("")
    lines.append("- This report is **read-only**: it does not send SAS/WAT traffic and does not modify any cabinet.")
    lines.append("- Hop 4 correlation matches each Hop 3 commit to the nearest SlotLog Cashless In line within 5 seconds by amount.")
    lines.append("- Aurum service deltas: aurum_started_ms = commit - REQUEST_STARTED; aurum_finished_ms = ALL_WAT_FINISHED - commit.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aft-transactions", type=Path, required=True)
    ap.add_argument("--slotlog-cashless", type=Path, required=False)
    ap.add_argument("--slotlog-file", type=Path, required=False)
    ap.add_argument("--aurum-log", type=Path, required=False)
    ap.add_argument("--out-md", type=Path, required=True)
    ap.add_argument("--out-jsonl", type=Path, required=False)
    ap.add_argument("--title", type=str, default="Hop 3 (WAT2AFT) integration report")
    args = ap.parse_args()

    txns = load_aft_transactions(args.aft_transactions)
    slotlog_rows = load_slotlog_cashless_csv(args.slotlog_cashless) if args.slotlog_cashless else None

    slotlog_events = load_slotlog_cashless_events(args.slotlog_file) if args.slotlog_file else None
    aurum_events = load_aurum_service_events(args.aurum_log) if args.aurum_log else None

    correlations = correlate_hop4(txns, slotlog_events, aurum_events) if (slotlog_events or aurum_events) else None

    md = render_markdown(txns, slotlog_rows, args.title, correlations, slotlog_events, aurum_events)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(md, encoding="utf-8")

    if args.out_jsonl:
        args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        sorted_txns = _iter_sorted(txns)
        with args.out_jsonl.open("w", encoding="utf-8", newline="\n") as f:
            for i, t in enumerate(sorted_txns):
                corr = correlations[i] if correlations else None
                record = {
                    "commit_time": t.committed.isoformat() if t.committed else None,
                    "commit_txn_id": t.commit_txn_id,
                    "request_txn_id": t.request_txn_id,
                    "credit_type": t.credit_type,
                    "wat_amount": t.wat_amount,
                    "obtained_total": t.obtained_total,
                    "transfer_status": t.transfer_status,
                    "commit_exception": t.commit_exception,
                    "transaction_id_text": t.transaction_id_text,
                    "source_file": t.source_file,
                }
                if corr:
                    record["hop4_matched"] = corr.slotlog_match is not None
                    record["hop4_amount_text"] = corr.slotlog_match.amount_text if corr.slotlog_match else None
                    record["hop4_delta_ms"] = corr.slotlog_delta_ms
                    record["aurum_started_delta_ms"] = corr.aurum_started_delta_ms
                    record["aurum_finished_delta_ms"] = corr.aurum_finished_delta_ms
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
