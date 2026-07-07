# Hop 4 — Aurum/OneHand applies the committed transfer to the game → SlotLog "Cashless In: $X"

> Defensive QA / architecture mapping of ONE hop of the AFT/WAT credit-transfer
> chain on a **legitimate, owned lab gaming cabinet** (GoldClub Aurum EGM at
> `10.0.0.90`, cabinet `GCC_ST_20664_01`, asset `777`). Read-only against the
> cabinet. No credit was injected or forged.

Full verified credit path (for context):

```
IGT SAS tester (COM11)
  → CommCtrlSAS bridge (TCP 31100/31150)
    → GoldClub.Aurum.Services (WAT2AFT)
      → AFT XML (request/authorize/commit)
        → OneHand  ← THIS HOP (Hop 4)
          → SlotLog "Cashless In: $X"  ← observation point
            → verification
```

**This hop is an OUTPUT / observation point**, not a control or injection point.
By the time anything reaches Hop 4, the transfer has *already been committed* by
the Aurum/WAT2AFT service (Hop 3). OneHand simply applies the committed amount to
the game's credit meter and writes the human-readable result to SlotLog.

---

## 1. Role

`OneHand` (the slot/game client, namespace `OneHand.AurumEGM`) is the component
that **applies the committed Aurum transfer to the on-screen game credit** and
**records the human-readable result** into SlotLog.

- It runs **downstream of the commit**. The decision to move money — request →
  authorize → commit — already happened in `GoldClub.Aurum.Services` (Hop 3),
  persisted into the AFT transaction-state XML
  (`…\GCMessenger\SASControler1\…aftMostRecentTransaction_v*.xml` /
  `History\…aftTransactionHistory_*.xml`).
- OneHand **does NOT decide transfers.** It does not authorize, validate, or
  gate the money movement. It consumes the committed result, bumps the credit
  state, and logs `Cashless In: $X`.
- The reference record in `report/AFT_Robustness_Findings.md` (Section A #1
  and Section B steps 6–8) ties this together: a committed `100000`-cent transfer
  surfaces here as `Cashless In: $1,000.00` and an
  `Aurum … credit state increased to …` line, with the documented
  `SlotLog balance after = 598900`.

Source: `report/AFT_Robustness_Findings.md` (Section A #1, Section B steps 6–8,
reference record table).

---

## 2. Mechanism

The hop is observable as a tight cluster of SlotLog lines emitted under the
`[GM2AU_aurumExecute]` context (Game-Machine → Aurum execute):

1. **Apply / information item** — OneHand's `InformationManager` records the
   applied amount:
   `OneHand.Utilities.InformationManager - Information item added: ['Cashless In: $X']`.
2. **Authoritative cashless-in line** — the line verification keys on:
   `OneHand.AurumEGM - Cashless In: $X`.
3. **Credit-state increase line** — the game credit meter is bumped:
   `OneHand.AurumEGM - Aurum cashable credit state increased to <cents>` (or
   `Aurum promo credit state increased to <cents>` when the committed transfer is
   a non-restricted / promo bucket rather than cashable).

The credit-state line is what proves the money was actually applied to the game
(the dollar `Cashless In` line alone is just a formatted message). On this
cabinet the bucket of the source transfer is reflected in the wording —
`cashable …` vs `promo …` — which mirrors the
`Cashable / Restricted / NonRestricted` split decided upstream in Hop 3.

Source: `report/AFT_Robustness_Findings.md` (Section B steps 6–7);
`.cursor/rules/reference-slotlog-logs.mdc` (SlotLog format/location).

---

## 3. Real dump (cabinet `10.0.0.90`, today's file)

File: `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-15.log` (read-only, opened
with shared read/write because the live LogDaemon holds it open for writing).

### 3a. The reference `$1,000.00` event at `2026-06-15T14:43:11`

Full surrounding context (note the cabinet writes each line twice; the
authoritative match is the `OneHand.AurumEGM - Cashless In:` line):

```
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - OnAuthorizePendingMulti - WatAuthorizePending last lock: OnlineLock or CashlessTransfer
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.Utilities.InformationManager - Information item added: ['Cashless In: $1,000.00'] [INFO] [5] 'Cashless In: $1,000.00' on 13833.2104132
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Aurum promo credit state increased to 100000
2026-06-15T14:43:16.975+01:00 INFO  [SlotMachine] OneHand.Utilities.InformationManager - Information item added: ['TextGameSelect'] [INFO] [1000000] 'Select A Game' on 13838.3033847
```

### 3b. Several more real `Cashless In` events from the same file

A selection of the authoritative `OneHand.AurumEGM - Cashless In:` lines (today's
file recorded 15 such authoritative events, plus their duplicated/info-item
mirrors):

```
2026-06-15T10:30:10.732+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T10:35:43.190+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T11:32:07.244+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T11:42:39.989+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T11:43:09.457+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T11:47:44.733+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T12:16:21.393+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $10,001,000.00
2026-06-15T12:18:48.796+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $0.01
2026-06-15T12:19:35.665+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $90,000,000.00
2026-06-15 13:14:12 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
2026-06-15T14:43:11.874+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
```

Notes on the real data:
- Two timestamp formats appear in the same file (`…T14:43:11.874+01:00` and the
  `T`-less `2026-06-15 13:14:12`), exactly as the SlotLog reference rule warns.
  The verification regex tolerates both because it only anchors on the leading
  non-whitespace token (`\S+`) for the timestamp.
- The very large / `$0.01` amounts above are other lab QA test stimuli on this
  bench, not the Hop-4 reference transfer. They illustrate that the log line is a
  *formatted echo* of whatever amount was applied — it carries no independent
  trust.

### 3c. Matching upstream Aurum service line (Hop 3 → Hop 4 correlation)

File: `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services\2026-06-15.log`.
The commit that produced the `14:43:11` Cashless In appears ~150 ms earlier in the
Aurum service log:

```
2026-06-15T14:43:11.723+01:00 INFO [:] *****  [GCC_ST_20664_01]  [12]  TRANSFER REQUEST FROM SERVER STARTED: TransferType(TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE), TransferCode(TRANSFER_REQUEST_FULL_TRANSFER_ONLY), Cashable(0), Restricted(0), NonRestricted(100000), ID( est Transaction23).
2026-06-15T14:43:11.956+01:00 INFO [:] *****  [GCC_ST_20664_01]  [77]  TRANSACTION PUT TO HISTORY AT POSITION (30).
2026-06-15T14:43:11.957+01:00 INFO [:] *****  [GCC_ST_20664_01]  [85]  SENDING 'TRANSACTION FINISHED' TO SERVER.
2026-06-15T14:43:11.959+01:00 INFO [:] *****  [GCC_ST_20664_01]  [73]  ALL WAT TRANSACTIONS FINISHED.
```

This confirms the ordering: the Aurum service commits the `100000`-cent transfer
(here in the `NonRestricted` bucket, hence the `Aurum promo credit state
increased to 100000` line in SlotLog), *then* OneHand applies it and emits
`Cashless In: $1,000.00`. Hop 4 is strictly the consequence of Hop 3.

---

## 4. How verification consumes it

Two read-only scripts treat the `OneHand.AurumEGM - Cashless In:` line as the
authoritative game-side evidence of an applied transfer. Both open the live
SlotLog with **shared read/write** because the LogDaemon keeps it open for
writing — a plain exclusive read would fail.

### `Convert-AftHistory.ps1` → `Get-SlotLogCashlessIn`

```258:282:Convert-AftHistory.ps1
function Get-SlotLogCashlessIn {
    param([string] $Path)
    $result = New-Object System.Collections.Generic.List[object]
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $result }
    $rx = [regex]'^(?<ts>\S+).*OneHand\.AurumEGM - Cashless In:\s*\$(?<amt>[\d,]+\.\d{2})'
    # Open with shared read/write: the live LogDaemon keeps this file open for writing.
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $reader = New-Object System.IO.StreamReader($fs)
        while ($null -ne ($line = $reader.ReadLine())) {
            $m = $rx.Match($line)
            if ($m.Success) {
                $amtText = $m.Groups['amt'].Value
                $cents = [int64]([double]($amtText -replace ',','') * 100)
                ...
```

This feeds the `AFT_SLOTLOG_RECONCILIATION` check, which compares the count and
summed cents of SlotLog `Cashless In` events against the successful AFT
transactions for the same date (`slotlog-cashless-in.csv` + `aft-checks.json`).

### `Invoke-AftTransferTest.ps1` → `Get-SlotLogCashlessTail`

```116:145:Invoke-AftTransferTest.ps1
function Get-SlotLogCashlessTail {
    param(
        [string] $Path,
        [int]    $FromByteOffset = 0
    )
    $out = @()
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $out }
    $rx = [regex]'^(?<ts>\S+).*OneHand\.AurumEGM - Cashless In:\s*\$(?<amt>[\d,]+\.\d{2})'
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($FromByteOffset -gt 0 -and $FromByteOffset -lt $fs.Length) {
            $fs.Seek($FromByteOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
        }
        ...
```

Same regex, but with a byte-offset seek so the harness can read only the *new*
tail of the file after the baseline snapshot. It is used to detect a fresh
`Cashless In` during the wait loop (`$newSlot`) and to assert
`SLOTLOG_CASHLESS_IN` PASS (the SlotLog amount equals the expected cents).

Key regex behavior shared by both:
- Anchors timestamp on `^(?<ts>\S+)` — tolerant of both observed timestamp
  formats.
- Requires the literal `OneHand.AurumEGM - Cashless In:` context, so the
  duplicated `InformationManager` "Information item added" mirror lines are
  intentionally **not** matched (avoids double-counting).
- Captures the dollar amount with thousands separators and exactly two decimals,
  then strips commas to compute cents.

Sources: `Convert-AftHistory.ps1` (`Get-SlotLogCashlessIn`, reconciliation check);
`Invoke-AftTransferTest.ps1` (`Get-SlotLogCashlessTail`, `Test-NewTransfer`,
`SLOTLOG_CASHLESS_IN` check).

---

## 5. Decoupling difficulty — **N/A for credit injection**

**Verdict: NOT a meaningful decouple / injection point.** It is an
observation/output sink.

| Aspect | Rating |
|---|---|
| Decouple to inject *real* game credit here | **Hard / impossible** — N/A |
| Forge the SlotLog line as a log artifact | **Trivial / Easy** — but meaningless |

Reasoning:

- **The credit comes from Hop 3, not here.** The actual money movement is the
  Aurum/WAT2AFT `commitTransfer` and the `… credit state increased to …` meter
  bump that OneHand applies from the committed transaction. The
  `Cashless In: $X` text line is a *downstream formatted echo*. Removing,
  rewriting, or injecting that text line does not create or move credit.
- **Forging is trivial but worthless.** Anyone who can write to the SlotLog file
  could append a fake `OneHand.AurumEGM - Cashless In: $9,999.00` line and it
  would satisfy the verification regex. But that produces **no real game credit**
  — there would be no matching committed AFT transaction in the state XML, no
  `credit state increased` meter change, and the `AFT_SLOTLOG_RECONCILIATION`
  check in `Convert-AftHistory.ps1` would immediately diverge (SlotLog event
  count/sum ≠ AFT obtained-cashable count/sum). A forged line is therefore a
  *detectable log artifact*, not an exploit.
- **Hardening of value lives upstream and at reconciliation.** The defensible
  controls are (a) the WAT/AFT commit boundary in Hop 3 and (b) the cross-source
  reconciliation against AFT state XML and the host/account debit ledger. Hop 4
  should be treated purely as evidence to be *corroborated*, never trusted on its
  own.

---

## Summary

Hop 4 is where `OneHand` applies an **already-committed** Aurum/WAT2AFT transfer
to the game's credit meter and writes the human-readable `OneHand.AurumEGM -
Cashless In: $X` line (plus an `Aurum … credit state increased to …` line) into
SlotLog under the `[GM2AU_aurumExecute]` context — confirmed live on cabinet
`10.0.0.90` for the reference `$1,000.00` event at `2026-06-15T14:43:11.874`,
correlated to the Aurum service's `TRANSFER REQUEST FROM SERVER STARTED …
NonRestricted(100000)` commit ~150 ms earlier. Verification consumes this line
via the identical regex in `Get-SlotLogCashlessIn` (`Convert-AftHistory.ps1`) and
`Get-SlotLogCashlessTail` (`Invoke-AftTransferTest.ps1`), both opening the file
shared-read because LogDaemon holds it open. **Verdict: this is an
observation/output point, not a control or injection point — rated N/A for credit
injection (Hard/impossible to use to create real credit) and trivial to fake only
as a meaningless log artifact that the AFT↔SlotLog reconciliation would
immediately flag.**
