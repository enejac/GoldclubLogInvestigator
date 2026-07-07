# GCI-AFT-001 — AFT transfer type naming mismatch

| Field | Value |
|-------|-------|
| **Type** | Bug |
| **Component** | AFT / WAT / Log correlation |
| **Priority** | Medium |
| **Local ID** | GCI-AFT-001 |
| **Jira bug** | TBD-GC-XXXX |
| **Jira test cases** | TBD-GC-TC-XXXX, TBD-GC-TC-YYYY |
| **Cabinet** | 10.0.0.90 (GST20664), reference lab |

## Summary

Inconsistent AFT transfer bucket naming across sasmsgr, Aurum, OneHand, SlotLog, WAT XML meters, and GoldClub Log Investigator tooling causes false credit-detection misses, harder case packs, and manual grep across log subfolders.

## Title

`{software_version} => AFT/WAT => transfer type labels disagree across log subsystems`

## Key details

- Host-side SAS `0x72` transfer tooling and runbooks use **cashable**, **restricted**, and **non-restricted (promo)** for the three BCD amount fields.
- Aurum main log uses **Cashable(N)**, **NonRestricted**, **Restricted** in `TRANSFER REQUEST FROM SERVER` lines.
- OneHand GM2AU / TRANSACTION EVENTS use parenthetical bucket tags **`(promo:0)`** and **`(cashable:0)`** where `:0` is a **pool index**, not a zero amount.
- WAT XML / DeviceManager meters use **`wat_promoInAmt`** (non-restricted), **`wat_nonCashInAmt`** (restricted), **`wat_cashableInAmt`** (cashable).
- SlotLog emits generic **`Cashless In: $X`** without bucket, plus separate **`promo credit state increased`** or **`cashable credit state increased`** lines.
- Investigator post-transfer credit verification only matched **`promo credit state increased`** for non-default types; cashable success often matched **`Cashless In`** only.
- Canonical mapping lives in `data/known_issues.json` and `aft_transfer.py`.

## Actual result

Cross-layer correlation requires ad-hoc mental translation. Example **promo $1k** (txn 41, 2026-06-15):

```text
OneHand GM2AU: Withdraw successful GCC_ST_20664_01 in 100000(promo:0); 0(cashable:0); 0(cashable:0)
OneHand TRANSACTION EVENTS: Transfer IN $1,000.00(promo:0); $0.00(cashable:0); $0.00(cashable:0)
SlotLog: Cashless In: $1,000.00 ; Aurum promo credit state increased to 100000
```

Example **cashable $10k** (txn 55, 2026-06-17):

```text
Aurum: TRANSFER REQUEST FROM SERVER STARTED … Transaction55 … Cashable(1000000)
OneHand GM2AU: Withdraw successful GCC_ST_20664_01 in 100000(cashable:0); …
OneHand TRANSACTION EVENTS: Transfer IN $10,000.00(cashable:0); …
SlotLog: Cashless In: $10,000.00
```

Example **failed transfer** (txn 54 — operational, not naming-only):

```text
Aurum: CASHOUT BUTTON PRESSED → AFT EXCEPTION ISSUED: 66 → TRANSFER AMOUNTS MISMATCH
Aurum: TRANSFER REQUEST FINISHED: TransferStatus(UNEXPECTED_ERROR), Cashable Com(0)
SlotLog: Handpay Out: $10,000.00 (not Cashless In)
```

## Expected result

All layers expose a single canonical bucket vocabulary (`cashable`, `restricted`, `non_restricted`) or a documented, machine-parseable mapping. Investigator and lab QA tooling should correlate ingest to credit using normalized types without false negatives.

## Impact

- Automated credit checks or manual QA may report **no matching credit line** when credit landed under a different log phrase.
- Case packs and defect tickets lack automatic links to known AFT naming patterns.
- Meter reconciliation already required aggregate 0017/0018 handling; log-line tools need the same discipline.

## Proposed fix

1. `data/known_issues.json` — known-issue catalog with Jira placeholders.
2. `aft_transfer.py` — lexicon, line parsers, correlator.
3. `parser_rules.py` — regex triggers + `match_known_issue()`.
4. Defect Ticket / case pack **Related tracking** block when patterns match.
5. Investigator SlotLog correlation — patterns for cashable/restricted credit state lines.

## References

- `aft/RUNBOOK.md` — amount / transfer-type parameters
- `aft/hops/hop4-onehand-to-slotlog.md`
- `gui/view_model.py` — SAS 0017/0018 aggregate WAT buckets
