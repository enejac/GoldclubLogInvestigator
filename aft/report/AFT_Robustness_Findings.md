# AFT Robustness Findings and Threat Model

Generated: 2026-06-05
Cabinet: lab EGM `10.0.0.90` (`GCC_ST_20664_01`, asset `777`)

Scope: read-only review of the cabinet's AFT/WAT credit path using live logs, AFT
state XML, passive WinDivert captures of the localhost `CommCtrlSAS` bridge, and
live socket enumeration. No forged credit transfer was built or sent.

> **2026-06-15 UPDATE (supersedes the "0x72 not on the bridge" conclusion).**
> Finding #5 below is correct that *passive* capture saw only `1B 80`/`1B 81`
> polls and `00` idle replies — but its implication that a host-originated raw SAS
> `0x72` cannot ride the CommCtrlSAS bridge is now **disproven by active testing**.
> A raw `0x72` `$1,000` promo transfer was committed twice (no IGT tester) by
> injecting a TCP segment INTO the EXISTING `31150 → Aurum:<ephemeral>` flow at the
> correct sequence number via WinDivert. A *new* socket to `31150` still fails
> (it is never merged into the live stream); injecting into the *existing* flow
> succeeds. The earlier Hop-3 .NET-remoting direct-post idea FAILED for a brand-new
> transfer (`RequestTransferPosted` `NullReferenceException`). Full method and
> evidence: [`..\RUNBOOK.md`](../RUNBOOK.md). The
> verified findings below remain accurate as read-only observations.

---

## A. VERIFIED KEY FINDINGS (confirmed facts only)

Every item below is directly evidenced by the cabinet's own logs, AFT state XML,
passive packet captures, or live socket enumeration collected this session.
Nothing in this section is inferred. All speculative/attacker-capability material
is isolated in section D ("Unverified / Hypothesis").

1. **Real money credit reached the game via the AFT/WAT path.**
   SlotLog `GM2AU_aurumExecute` at `2026-06-05T11:00:53.302+01:00`:
   `Cashless In: $1,000.00` followed by `Aurum cashable credit state increased to 598900`.

2. **8 unique successful host-to-EGM in-house cashable transfers are recorded** in
   the cabinet's AFT state (de-duplicated from 10 raw current/history rows). Each
   is `100000` cents, `transferType = TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE`,
   status `FULL_TRANSFER_SUCCESSFUL`. Cumulative cashable AFT meter reached
   `800000` cents ($8,000.00).

3. **The transfer is carried by the Aurum WAT2AFT path, with a clean three-phase
   handshake.** The state file records `AurumTransaction xsi:type="WatTransaction"`
   with `requestTransfer` -> `authorizeTransfer` -> `commitTransfer`, and
   `commitTransfer` reports `transferException="0"` (no error). Service banner
   `WAT2AFT UP! v.1.0.35.31197` appears in the Aurum log.

4. **The gaming machine is NOT registered, yet transfers commit.** This is the
   core robustness finding. `aftCurrentSettings` shows:
   - `registrationStatus = GAMING_MACHINE_NOT_REGISTERED`
   - `registrationKey = AAAAAAAAAAAAAAAAAAAAAAAAAAA=` (all-zero, 20 bytes)
   - `transferAllowedOnlyIfLocked = false`
   Despite the unregistered state and all-zero key, the $1,000 transfers in
   finding #2 still completed successfully.

5. **The transfer does not appear as a raw SAS `0x72` long-poll on the captured
   bridge.** Five passive WinDivert windows (60/90/120/120/120 s) on the
   `CommCtrlSAS` loopback ports `31100`/`31150` showed only SAS general polling
   (`1B 80` / `1B 81`) and EGM idle replies (`00`). No `0x72` (AFT transfer) and
   no `0x73` (AFT registration) bytes were observed. On this cabinet the credit
   is committed through the Aurum/WAT2AFT service path, not a host-originated raw
   SAS `0x72` visible on that bridge.

6. **`CommCtrlSAS` exposes only those two SAS sockets.** Live `netstat`/process
   enumeration: `CommCtrlSAS.exe` (PID 4660) holds only `31100` and `31150`, and
   `GoldClub.Aurum.Services` (PID 4872) connects to exactly those. There is no
   third SAS socket carrying the transfer.

### Latest verified transaction (reference record)

| Field | Value |
|---|---|
| Completed | `2026-06-05 11:00:53` (BCD `20260605110053`) |
| Status | `FULL_TRANSFER_SUCCESSFUL` |
| Type | `TRANSFER_INHOUSE_AMOUNT_FROM_HOST_TO_GAMING_MACHINE` |
| Requested / obtained cashable | `100000` / `100000` cents ($1,000.00) |
| Restricted / non-restricted | `0` / `0` |
| Asset number | `777` (`CQMAAA==`, bytes `09 03 00 00`) |
| WAT account | `GCC_ST_20664_01_cashable` |
| Credit type / pool | `cashable` / `0` |
| WAT transaction id | `14` (request = commit) |
| Host request id | `500bd5b0-daa6-4457-8597-77aafeed9bb9` |
| Commit transfer exception | `0` |
| SlotLog balance after | `598900` |

### Evidence sources

Cabinet:
- `…\GCMessenger\SASControler1\GCC_ST_20664_01_aftMostRecentTransaction_v2.xml`
- `…\GCMessenger\SASControler1\GCC_ST_20664_01_aftCurrentSettings_v2.xml`
- `…\GCMessenger\SASControler1\History\GCC_ST_20664_01_aftTransactionHistory_i*_v*.xml`
- `\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-05.log`
- `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services\2026-06-05.log`

Local generated outputs (read-only parser `Convert-AftHistory.ps1`):
- `aft-transactions.csv` — 8 de-duplicated transactions
- `aft-transactions-raw.csv` — 10 raw rows (current + history mirrors)
- `aft-transactions.json`
- `aft-summary.json`

---

## B. OBSERVED CREDIT PATHWAY (verified sequence)

This is the exact path the $1,000 transfer followed, reconstructed from
timestamps in the cabinet's own logs and state:

1. IGT/SAS tester maintains the SAS/MUX link on `COM11`, bridged by
   `CommCtrlSAS.exe` to loopback `31100`/`31150` (observed: continuous `80/81`
   general polls).
2. `GoldClub.Aurum.Services` (WAT2AFT) creates an `AurumTransaction` of type
   `WatTransaction` (`transferInitiatedTime 11:00:53.213`).
3. `requestTransfer` (account `GCC_ST_20664_01_cashable`, `100000`, txn `14`).
4. `authorizeTransfer` (same amount/account, accepted).
5. `commitTransfer` (`transferAmount=100000`, `transferException=0`, `11:00:53.314`).
6. `GM2AU_aurumExecute` applies it in OneHand: `Cashless In: $1,000.00`.
7. `Aurum cashable credit state increased to 598900`.
8. AFT history is finalized as `FULL_TRANSFER_SUCCESSFUL` with cumulative
   cashable AFT meter `800000`.

---

## C. SAFE "FAKE ATTACK" TEST PLAN

Goal: test whether the AFT/WAT trust boundary rejects invalid, replayed, or
mis-bound transactions using the legitimate IGT tester / host flow. Do not build
or run an unbacked credit-forge tool. Each case below is evaluated by observing
whether the cabinet accepts or rejects the legitimate tester request, then
correlating AFT history, SlotLog, and host/account evidence.

### Preconditions

- Written authorization for the lab cabinet only (`10.0.0.90`).
- Cabinet isolated from production systems.
- Current AFT baseline exported with `Convert-AftHistory.ps1`.
- Current SlotLog and AFT state preserved before testing.
- Known cabinet identity: `GCC_ST_20664_01`, asset `777`.

### Test Cases

| # | Test | Control Under Test | Expected PASS Result | Evidence To Capture |
|---|---|---|---|---|
| 1 | Normal $1,000 AFT-in via tester | Baseline transfer path | Transfer commits once | AFT history row, SlotLog `Cashless In`, host debit |
| 2 | Replay same `hostRequestId` | Host replay protection | Reject; no second credit | No new successful AFT row; rejection log |
| 3 | Replay same WAT transaction ID | Transaction ID monotonicity | Reject; no second credit | No duplicate `RequestTransactionId` / `CommitTransactionId` |
| 4 | Use wrong asset number (not `777`) | Asset binding | Reject | Rejection log; no SlotLog cashless-in |
| 5 | Amount above policy limit | Amount/policy enforcement | Reject or cap per policy | Transfer exception / no unexpected credit |
| 6 | Transfer during lock/handpay/payout | State gating | Reject or queue safely | State transition logs and AFT status |
| 7 | Require registration then use wrong/zero key | Registration-as-gate | Reject | `registrationStatus`, transfer status |
| 8 | Normal transfer with host ledger check | Backing debit | EGM credit equals host-side debit | Host debit record + AFT/SlotLog reconciliation |

### Per-Test Procedure

1. Run `.\Convert-AftHistory.ps1` before the test and save the generated
   `report` files.
2. Execute the test from the legitimate tester/host interface.
3. Run `.\Convert-AftHistory.ps1` again.
4. Compare:
   - `aft-transactions.csv`
   - `aft-checks.json`
   - `slotlog-cashless-in.csv`
   - relevant host/account ledger records
5. Mark the test failed if credit lands on the EGM without the expected control
   passing first.

---

## D. AUTOMATED READ-ONLY CHECKS

`Convert-AftHistory.ps1` now emits these files:

- `aft-transactions.csv` - de-duplicated transaction audit table
- `aft-transactions.json` - JSON version of the same table
- `aft-transactions-raw.csv` - raw rows including current/history mirrors
- `slotlog-cashless-in.csv` - authoritative `OneHand.AurumEGM - Cashless In`
  events from SlotLog
- `aft-checks.csv` / `aft-checks.json` - detection/reconciliation results
- `aft-summary.json` - summary including current settings and check counts

Current check results after the latest run:

| Check | Severity | Status | Meaning |
|---|---:|---|---|
| `AFT_SLOTLOG_RECONCILIATION` | High | PASS | AFT history for `2026-06-05` reconciles with authoritative SlotLog cashless-in events. |
| `DUPLICATE_HOST_REQUEST_ID` | Critical | PASS | No repeated `hostRequestId` values in de-duplicated AFT history. |
| `DUPLICATE_WAT_TRANSACTION_ID` | Critical | PASS | No repeated WAT transaction IDs in de-duplicated AFT history. |
| `NON_INCREMENTING_WAT_TRANSACTION_ID` | High | PASS | WAT transaction IDs increase monotonically in commit-time order. |
| `SUCCESS_WHILE_UNREGISTERED` | High | WARN | Successful transfers exist while current AFT settings report `GAMING_MACHINE_NOT_REGISTERED`. |

The final warning is intentional and remains the main robustness finding unless
the deployment design explicitly states that WAT authorization, not SAS AFT
registration, is the active trust boundary.

---

## E. RECOMMENDED MONITORING RULES

1. Alert when a successful transfer appears while current settings report
   `GAMING_MACHINE_NOT_REGISTERED`.
2. Alert on any duplicate `hostRequestId`.
3. Alert on any duplicate or non-incrementing WAT transaction ID.
4. Reconcile AFT obtained cashable totals against SlotLog `Cashless In` totals
   per day.
5. Reconcile both of the above against the host/account debit ledger. This is
   the decisive check for detecting unbacked credit.