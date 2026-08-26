# AFT / WAT Audit Report

Read-only audit of the lab cabinet's AFT (Advanced Funds Transfer) / WAT credit
path. All files in this folder are **generated** by the parser; do not hand-edit
them.

- Generator: [`../../Convert-AftHistory.ps1`](../../Convert-AftHistory.ps1)
- Default cabinet: `10.0.0.90` (`GCC_ST_20664_01`, asset `777`)

```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator
.\Convert-AftHistory.ps1                       # cabinet 10.0.0.90 -> .\aft\report
.\Convert-AftHistory.ps1 -ComputerName 10.0.0.90 -Verbose

# IGT tester mimic + verify (you trigger COM11; script watches + checks)
.\Invoke-AftTransferTest.ps1                  # default $1,000, 180s timeout
.\Invoke-AftTransferTest.ps1 -ExpectedAmountCents 100000 -TimeoutSec 300
.\Invoke-AftTransferTest.ps1 -BaselineOnly    # snapshot only (no wait)
```

> **2026-06-15 update:** WinDivert IN-STREAM injection on the CommCtrlSAS →
> Aurum bridge (`..\..\lab\Invoke-WinDivertAft.ps1` / `..\..\lab\Send-TestAft1000.ps1 -Send`)
> replaces only the `0x72` frame; a SAS host must still be connected and polling.
> See [`..\RUNBOOK.md`](../RUNBOOK.md). The audit tooling in this folder remains
> strictly read-only and is used to *verify* such transfers, not to send them.

### IGT tester test workflow

1. **Baseline** (optional): `.\Invoke-AftTransferTest.ps1 -BaselineOnly` — confirms share reachability and prints current most-recent AFT + SlotLog tail.
2. **Run test**: `.\Invoke-AftTransferTest.ps1` — captures before audit, then waits up to 180s.
3. **On the IGT SAS tester** (COM11, not COM4): initiate host → EGM cashable AFT for the expected amount (default **$1,000**, asset **777**).
4. **Script detects** new `hostRequestId` / `instanceId` in AFT XML and/or a new SlotLog `Cashless In` line.
5. **After audit** lands in `aft-test\<timestamp>-after\`; verification checks status, amount, asset, commit exception, and SlotLog match.

Reports are read-only. The script does **not** send SAS `0x72` or inject WAT credits — that path is the tester → `CommCtrlSAS` → Aurum, same as production lab QA.

The script is strictly read-only. It never writes to the cabinet and never sends
SAS traffic.

---

## How the script relates to `CommCtrl.exe` (important)

It does **not** talk to `CommCtrl.exe` / `CommCtrlSAS.exe` at all. That is the
single most common misunderstanding, so to be explicit:

```
 IGT/SAS tester / host
        │  SAS over serial (COM11)
        ▼
 CommCtrlSAS.exe        ← serial<->TCP SAS bridge (loopback 31100 / 31150)
        │  TCP
        ▼
 GoldClub.Aurum.Services  (WAT2AFT)   ← decides + commits the transfer
        │  writes state                 (requestTransfer→authorizeTransfer→commitTransfer)
        ▼
 …\GCMessenger\SASControler1\*.xml   ←★ THIS is what Convert-AftHistory.ps1 reads
        │  applied to game
        ▼
 OneHand  →  SlotLog "Cashless In: $X"  ←★ used for reconciliation
```

- `CommCtrl.exe` / `CommCtrlSAS.exe` are the **SAS transport bridges** (serial to
  loopback TCP). They carry the live SAS byte stream.
- The committed transfer result is **persisted as XML** by
  `GoldClub.Aurum.Services`. The parser reads those XML files plus SlotLog. It
  never needs the bridge process to be running.
- If you specifically want to watch the **live SAS wire** (e.g. confirm whether a
  raw `0x72` AFT long-poll appears), that is a separate **passive capture** on the
  `CommCtrlSAS` loopback ports — not this report. See the robustness findings doc
  for the capture results.

So a script that tries to "read from CommCtrl.exe" to build this report is aimed
at the wrong layer. The authoritative, persisted record is the state XML.

> Note on the earlier draft script: importing `PSReadLine` is unnecessary (it is a
> console line-editor, unrelated to JSON/CSV), and field names like
> `AssetItemHex` / `AssetItemNumber` do not exist — the correct fields are
> `AssetNumberHex` / `AssetNumber`. `Convert-AftHistory.ps1` produces those
> directly, so downstream tooling should consume its CSV/JSON rather than
> re-deriving them.

---

## Source data on the cabinet

`\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1`

| File pattern | Meaning |
|---|---|
| `*_aftCurrentSettings_v{1,2}.xml` | Current AFT settings (registration status/key, transfer flags). Two rolling versions; the parser uses the highest `<version>`. |
| `*_aftMostRecentTransaction_v{1,2}.xml` | Mirror of the latest transaction (root `AftTransactionSerializer`). |
| `History\*_aftTransactionHistory_i<idx>_v<ver>.xml` | History slots (root `AftTransactionCarrier`). Empty slots are ~231-byte placeholders with no `<aftTransaction>` and are skipped. |

Reconciliation log: `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\<date>.log`
(matched on `OneHand.AurumEGM - Cashless In: $...`). Opened with shared read
because the live LogDaemon holds it open.

---

## Generated files

| File | Description |
|---|---|
| `aft-transactions.csv` / `.json` | De-duplicated transactions (one row per `instanceId`). |
| `aft-transactions-raw.csv` | Every parsed row before de-duplication (current + history mirrors). |
| `aft-transactions.md` | Human-readable ledger table. |
| `aft-current-settings.json` | Current AFT settings snapshot. |
| `aft-checks.csv` / `.json` | Robustness check results. |
| `slotlog-cashless-in.csv` | Authoritative SlotLog cashless-in events for the compared date. |
| `aft-summary.json` | Run summary: counts, totals, latest transaction, settings, check tallies. |

De-duplication: rows are grouped by `instanceId`; the row carrying a
`HistoryIndex` is preferred (then newest commit time), because the
"most recent transaction" file mirrors a history slot with the same `instanceId`.

---

## SAS field decoding

| Output field | Source element | Decoding |
|---|---|---|
| `AssetNumber` | `assetNumber` | base64 → 4 little-endian bytes → uint (e.g. `CQMAAA==` → `09 03 00 00` → `777`). |
| `AssetNumberHex` | `assetNumber` | base64 → hex string. |
| `CompletedBcd` / `CompletedAtBcd` | `transactionCompletedDateTime` | base64 → 7 BCD bytes → `yyyyMMddHHmmss` (e.g. `ICYGERFDRg==` → `2026-06-11 11:43:46`). |
| `TransactionIdText` | `transactionId/char[]` | decimal byte values → ASCII (e.g. `Test Transaction`). |
| `*Dollars` | cents | invariant-culture `cents/100` formatted `F2` (always `8100.00`, never locale `8.100,00`). |
| WAT fields | `aurumTransactions/AurumTransaction[WatTransaction]` | `requestTransfer` / `commitTransfer` / `account` attributes. |

---

## Robustness checks

| Id | Severity | Meaning |
|---|---|---|
| `AFT_SLOTLOG_RECONCILIATION` | High | FULL successful AFT transfers for the SlotLog date reconcile (count + sum) with SlotLog cashless-in events. |
| `DUPLICATE_HOST_REQUEST_ID` | Critical | No repeated `hostRequestId`. |
| `DUPLICATE_WAT_TRANSACTION_ID` | Critical | No repeated commit transaction id. |
| `NON_INCREMENTING_WAT_TRANSACTION_ID` | High | Commit transaction ids strictly increase in commit-time order. |
| `SUCCESS_WHILE_UNREGISTERED` | High | Successful transfers exist while settings report `GAMING_MACHINE_NOT_REGISTERED` (core finding). |

`AFT_SLOTLOG_RECONCILIATION` intentionally counts only `FULL_TRANSFER_SUCCESSFUL`.
A `PARTIAL_TRANSFER_SUCCESSFUL` (e.g. a `$100` partial that still credits the
game and emits a cashless-in event) will therefore surface as a WARN mismatch —
that is by design, to flag transfers that moved credit without a full-transfer
record.

---

## Findings

See [`AFT_Robustness_Findings.md`](AFT_Robustness_Findings.md) for the full,
verified findings and threat model.
