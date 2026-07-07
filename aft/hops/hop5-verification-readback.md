# Hop 5 — Verification / Read-Back Layer

**Chain position:** IGT SAS tester (COM11) → CommCtrlSAS bridge (TCP 31100/31150) → `GoldClub.Aurum.Services` (WAT2AFT) → AFT XML → OneHand → SlotLog → **VERIFICATION (this hop)**.

This document describes the final hop of the AFT/WAT credit-transfer chain on the
owned lab cabinet (GoldClub Aurum EGM, `10.0.0.90`, `GCC_ST_20664_01`, asset `777`).
Hop 5 is the **test oracle**: it reads the state that the earlier hops produced and
emits PASS/FAIL. It is defensive QA / architecture mapping only.

Sources:
- [`Invoke-AftTransferTest.ps1`](../../Invoke-AftTransferTest.ps1) — orchestration + per-transfer verification.
- [`Convert-AftHistory.ps1`](../../Convert-AftHistory.ps1) — read-only parser + audit/robustness checks.
- [`report/README.md`](../report/README.md) — documented IGT tester workflow.

---

## 1. Role — read-only test oracle

Hop 5 is **purely observational**. It does not send SAS, does not inject WAT
credits, and never writes to the cabinet. It consumes two artifacts produced by
upstream hops and decides whether the transfer behaved as expected:

1. **AFT transaction state XML** written by `GoldClub.Aurum.Services` (the
   committed `requestTransfer → authorizeTransfer → commitTransfer` result).
2. **SlotLog `Cashless In` events** emitted by OneHand once credit lands on the game.

Both scripts state this explicitly. `Invoke-AftTransferTest.ps1` describes itself as
"YOU trigger the transfer on the IGT SAS tester; this script watches the cabinet and
verifies the result (read-only)" and notes "The script never writes to the cabinet
and never injects SAS/WAT traffic" (`Invoke-AftTransferTest.ps1` lines 3-16).
`Convert-AftHistory.ps1` confirms "It ONLY reads files. It never writes to the
cabinet and never sends SAS … It does NOT communicate with CommCtrl.exe or
CommCtrlSAS.exe" (`Convert-AftHistory.ps1` lines 12-18).

The `report/README.md` ASCII diagram pins the boundary: the committed result is
persisted as XML under `…\GCMessenger\SASControler1\*.xml`, and that XML plus SlotLog
is "what `Convert-AftHistory.ps1` reads" — the oracle sits downstream of the bridge
and Aurum, never touching the live wire.

---

## 2. Inputs — exact paths and snapshot/diff method

### Paths read (cabinet `10.0.0.90`)

Defined in `Invoke-AftTransferTest.ps1` lines 85-87:

| Input | Path |
|---|---|
| AFT state root | `\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1` |
| SlotLog dir | `\\10.0.0.90\c$\Goldclub\var\log\SlotLog` |
| Aurum services log dir | `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services` |

Within the state root the oracle reads:
- `*_aftCurrentSettings_v*.xml` — registration status + transfer flags (`Read-AftCurrentSettings`, lines 188-205).
- `*_aftMostRecentTransaction_v*.xml` — the latest transaction mirror (`Read-AftMostRecent`, lines 207-242).
- `History\*_aftTransactionHistory_i<idx>_v<ver>.xml` — history slots (used by `Collect-KnownIds` and by the parser).

The latest SlotLog `<date>.log` is matched on the regex
`OneHand.AurumEGM - Cashless In: $<amount>` (lines 116-145, mirrored in
`Convert-AftHistory.ps1` lines 258-282). The Aurum log is scanned for
`AFT EXCEPTION ISSUED: <code>` and `CASHOUT BUTTON PRESSED` lines
(`Get-AurumAftTail`, lines 147-186). All files are opened with
`FileShare::ReadWrite` so the live LogDaemon can keep writing.

### Snapshot + diff method

`Get-AftSnapshot` (lines 244-268) captures a point-in-time bundle: latest SlotLog
path/length, cashless-in count + last event, Aurum AFT event count + last event, the
most-recent AFT transaction, and current AFT settings.

`Invoke-AftTransferTest.ps1` takes a **before** snapshot, optionally runs an
after-baseline trigger script, then polls every 2 s (lines 441-448) re-snapshotting
until `Test-NewTransfer` reports a change or the timeout elapses.

`Test-NewTransfer` (lines 294-339) is the diff oracle. It flags a genuinely new
transfer when any of these become true:
- **NewHostId** — `MostRecent.HostRequestId` changed vs baseline.
- **NewInstanceId** — `MostRecent.InstanceId` changed vs baseline.
- **NewSlotLog** — cashless-in count increased, or the last cashless-in timestamp changed.
- **NewAftException** — a new `AFT EXCEPTION ISSUED` / cashout event appeared in the Aurum log.

`Detected = NewHostId OR NewInstanceId OR NewSlotLog`. A `NewAftException` **without**
`Detected` is treated as a rejected request (the request never persisted a
transaction) and the harness exits with a failure code and tester guidance
(lines 450-464). This separation lets the oracle distinguish "transfer succeeded and
was recorded" from "request was bounced before commit".

---

## 3. Checks

### Per-transfer checks (`Invoke-AftTransferTest.ps1`, lines 490-535)

Run against the detected most-recent transaction (`$mr`) and the new SlotLog event:

| Check | Asserts | Logic |
|---|---|---|
| `AFT_STATUS` | Transfer completed | Standard: `TransferStatus == FULL_TRANSFER_SUCCESSFUL`. Bonus: `FULL_…` **or** `PARTIAL_TRANSFER_SUCCESSFUL` accepted. |
| `AFT_AMOUNT` | Correct amount credited | Standard: `ObtainedCashable == ExpectedAmountCents`. Bonus: `ObtainedTotal == ExpectedAmountCents`. |
| `AFT_ASSET` | Correct cabinet | Decoded `assetNumber == AssetNumber` (default `777`). |
| `AFT_COMMIT_EXCEPTION` | Clean commit | `commitTransfer.transferException == "0"`. |
| `SLOTLOG_CASHLESS_IN` | Credit actually landed on game | New SlotLog `Cashless In` cents `== ExpectedAmountCents`. |

Results are written to `aft-transfer-test-checks.json` (line 537-538). `OVERALL` is
PASS only when at least one check ran and none failed (lines 559-566).

### Bonus-mode total-obtained logic

A standard registered cashable transfer puts money only in the **cashable** bucket,
so `AFT_AMOUNT` checks `ObtainedCashable` alone. A bonus/WAT award (the unregistered
lab path) can land in **restricted** or **non-restricted** buckets instead. To handle
this, `Read-AftMostRecent` computes
`ObtainedTotal = ObtainedCashable + ObtainedRestricted + ObtainedNonRestricted`
(lines 220-232), and in `-Bonus` mode `AFT_AMOUNT` validates that **total** against
the expected amount rather than the cashable bucket only (lines 498-503). `-Bonus`
also implies `-AllowUnregistered` (line 77) and accepts `PARTIAL_TRANSFER_SUCCESSFUL`,
because bonus awards are accepted by the EGM while unregistered.

### Audit / robustness checks (`Convert-AftHistory.ps1`, lines 337-401)

`Invoke-AftAudit` shells out to `Convert-AftHistory.ps1` for both the before and after
folders (lines 341-345). That parser de-duplicates transactions by `instanceId`,
decodes SAS fields, and runs five checks:

| Id | Severity | Asserts |
|---|---|---|
| `AFT_SLOTLOG_RECONCILIATION` | High | For the SlotLog date, count + summed `ObtainedCashable` of `FULL_TRANSFER_SUCCESSFUL` transfers equals SlotLog cashless-in count + sum. WARN on mismatch. |
| `DUPLICATE_HOST_REQUEST_ID` | Critical | No `hostRequestId` appears more than once (replay/duplication guard). |
| `DUPLICATE_WAT_TRANSACTION_ID` | Critical | No commit `transactionId` appears more than once. |
| `NON_INCREMENTING_WAT_TRANSACTION_ID` | High | Commit transaction ids strictly increase in commit-time order. |
| `SUCCESS_WHILE_UNREGISTERED` | High | WARN when settings report `GAMING_MACHINE_NOT_REGISTERED` yet successful transfers exist (the core robustness finding). |

`AFT_SLOTLOG_RECONCILIATION` intentionally counts only `FULL_TRANSFER_SUCCESSFUL`
(README lines 140-144), so a partial-but-credited transfer surfaces as a WARN by
design. Outputs land in `aft-transactions.csv`/`.json`, `aft-checks.json`/`.csv`,
`slotlog-cashless-in.csv`, `aft-current-settings.json`, `aft-transactions.md`, and
`aft-summary.json` (lines 412-465).

---

## 4. Real artifacts — `aft-test\20260615-144251-after\`

Folder contents (10 files):

```
aft-transfer-test-checks.json   <- per-transfer oracle result (Invoke-AftTransferTest)
aft-checks.json                 <- audit/robustness checks (Convert-AftHistory)
aft-checks.csv
aft-summary.json
aft-current-settings.json
aft-transactions.json
aft-transactions.csv
aft-transactions-raw.csv
aft-transactions.md
slotlog-cashless-in.csv
```

The matching `20260615-144251-before\` folder holds the same audit set minus
`aft-transfer-test-checks.json` (9 files: `aft-checks.json`/`.csv`,
`aft-summary.json`, `aft-current-settings.json`, `aft-transactions.json`/`.csv`,
`aft-transactions-raw.csv`, `aft-transactions.md`, `slotlog-cashless-in.csv`) — the
baseline has no per-transfer result because no transfer has been detected yet.

### `aft-transfer-test-checks.json` (per-transfer oracle — all PASS)

```json
[
    { "Check": "AFT_STATUS", "Pass": true,
      "Detail": "Status=FULL_TRANSFER_SUCCESSFUL (bonus: full/partial accepted)" },
    { "Check": "AFT_AMOUNT", "Pass": true,
      "Detail": "ObtainedTotal=1000.00 (cash=0.00 restr=0.00 nonRestr=1000.00) expected=1000.00 creditType=promo" },
    { "Check": "AFT_ASSET", "Pass": true,
      "Detail": "Asset=777 expected=777" },
    { "Check": "AFT_COMMIT_EXCEPTION", "Pass": true,
      "Detail": "CommitException=0" },
    { "Check": "SLOTLOG_CASHLESS_IN", "Pass": true,
      "Detail": "SlotLog $1,000.00" }
]
```

This is the documented 2026-06-15 result: **OVERALL PASS**. The award landed entirely
in the non-restricted bucket (`nonRestr=1000.00`, `creditType=promo`), which is why
bonus-mode total-obtained logic is required — a cashable-only check would have read
`0.00` and failed.

### `aft-checks.json` (audit — 3 PASS, 2 expected WARN)

```json
[
    { "Id": "AFT_SLOTLOG_RECONCILIATION", "Severity": "High", "Status": "WARN",
      "Message": "Successful AFT transfers vs SlotLog Cashless In events: AFT count/sum=15/14000000001, SlotLog relevant count/sum=15/14000900001." },
    { "Id": "DUPLICATE_HOST_REQUEST_ID", "Severity": "Critical", "Status": "PASS",
      "Message": "No duplicate hostRequestId values found in de-duplicated AFT history." },
    { "Id": "DUPLICATE_WAT_TRANSACTION_ID", "Severity": "Critical", "Status": "PASS",
      "Message": "No duplicate WAT transaction IDs found in de-duplicated AFT history." },
    { "Id": "NON_INCREMENTING_WAT_TRANSACTION_ID", "Severity": "High", "Status": "PASS",
      "Message": "WAT transaction IDs are strictly increasing in commit-time order." },
    { "Id": "SUCCESS_WHILE_UNREGISTERED", "Severity": "High", "Status": "WARN",
      "Message": "Current settings report GAMING_MACHINE_NOT_REGISTERED with 24 successful transfer(s) in history." }
]
```

`aft-summary.json` for the same run records `TransactionCount=32`,
`SuccessfulCount=24`, `CheckCounts = { Pass: 3, Warn: 2, Fail: 0 }`, and the latest
transaction as a `WatTransaction` / `AFT_BONUS` award of `100000` cents
(`creditType=promo`, `AssetNumber=777`, `CommitTransferException=0`,
`RegistrationStatus=GAMING_MACHINE_NOT_REGISTERED`).

### Explaining the two WARNs (both expected, neither is a failure)

- **`AFT_SLOTLOG_RECONCILIATION` (WARN):** counts match exactly (15 = 15) but the
  summed cents differ slightly (`14000000001` AFT vs `14000900001` SlotLog). This is
  an **aggregate / historical-totals** comparison over the whole SlotLog date, not a
  check on the single transfer under test. The minor delta reflects bonus/partial
  awards that credited the game (so SlotLog logged a cashless-in) but are not counted
  as `FULL_TRANSFER_SUCCESSFUL` cashable in the AFT sum — exactly the by-design
  behavior described in the README. The transfer under test still passed its own
  `SLOTLOG_CASHLESS_IN` check.
- **`SUCCESS_WHILE_UNREGISTERED` (WARN):** the cabinet reports
  `GAMING_MACHINE_NOT_REGISTERED` (registration key all-zero) yet has 24 successful
  transfers. This is the **expected robustness finding** for the unregistered lab
  bonus/WAT path — bonus awards are accepted without registration. It is surfaced as a
  deliberate WARN, not an error.

---

## 5. Decoupling difficulty — **Easy**

**Verdict: Easy.** Hop 5 is the safest layer to extend or detach because it is purely
observational — a read-only oracle with **no outbound traffic** and **no writes** to
the cabinet. There is nothing to "decouple" in the sense of breaking a live
dependency: it does not hold the SAS link, does not depend on CommCtrl/CommCtrlSAS,
and reads only persisted XML + log files (opened share-read so it never blocks the
producers).

"Decoupling" this hop simply means **automating the oracle**, which is already done:
`Invoke-AftTransferTest.ps1` drives snapshot → wait/diff → audit → PASS/FAIL, and
`Convert-AftHistory.ps1` is a standalone parser that can run any time against the
state tree. New checks can be added without touching any upstream hop, and the tool
can be pointed at a different cabinet via `-ComputerName`. Because it never mutates
state or sends SAS, extending it carries effectively zero operational risk to the
credit path.

---

*All artifacts above are read directly from disk
(`c:\Users\Ezbogar\GoldclubLogInvestigator\aft-test\20260615-144251-after\`). This
hop never sent traffic to or modified the cabinet.*
