# 10.0.0.171 AFT Landing Plan — make the EGM "owned" so WAT2AFT commits

Goal: land a **real AFT credit on `10.0.0.171`** the same way it works on the
reference cabinet `10.0.0.90`, by bringing the SAS session **online and sustained**
so `GoldClub.Aurum.Services` (WAT2AFT) finds an **owned device** and can drive
`requestTransfer → authorizeTransfer → commitTransfer` to completion.

This is the staged, **go/no-go** plan. Stage 0 on `.90` is **complete**; the poll
source is **external hardware** on the MUX upstream channel (see
`poll-source.md`). Software/loopback paths alone cannot bring `.171` online.
Nothing here injects money or SAS frames against `.171` until the physical prerequisite
below is met.

**Related investigations (2026-06-16):**
- [COM11 mux stack](5ed4f26a-b560-4739-9ae5-bf22f67dc5a8) — no Windows-side fault
- [Poll source identity](359d2ede-a84f-4626-a596-76840f9ce0e8) → `poll-source.md`
- Alt-credit paths → `alt-credit-paths.md` (no viable non-SAS commit vector)

- **Target cabinet:** `10.0.0.171`, EGM `GCC_ST_19737_01`
- **Reference cabinet:** `10.0.0.90`, EGM `GCC_ST_20664_01`, asset `777` (AFT proven working)
- **Status (read-only verified 2026-06-16):** `GoldClub.Aurum.Services` is **RUNNING**;
  WAT2AFT still logs `NO OWNED DEVICE FOUND FOR WAT. WAT2AFT WILL NOW EXIT` on a
  ~30 s loop (last seen `2026-06-16T12:35:03+01:00`). `CommCtrlSAS`/`CommCtrl` are
  **not Windows services** on `.171` (they run as processes, so the bridge can be
  present while the SAS session is dead).
- **MUX/COM11 stack (2026-06-16, [COM11 mux diagnosis](5ed4f26a-b560-4739-9ae5-bf22f67dc5a8)):**
  no Windows/driver/COM-stack fault on `.171`. COM11 opens, MUX negotiates CH2/SA1,
  bridge listens — but **zero `80`/`81` bytes arrive from the physical COM11/MUX
  side** (bridge S2C = bare TCP ACKs only). Poll source is **upstream of Windows**
  (external SAS host on MUX upstream channel and/or MUX firmware delta: `.90`
  `SI-1.0.3` vs `.171` `2.0.1`). **Loopback poll injection was proven insufficient**
  (Aurum decodes `qGMID1:80/81` but EGM stays offline). **Admin access to `.171` is
  currently blocked** (`GOLD-CLUB\test` not in Credential Manager for `10.0.0.171`).

---

## 1. Why `.171` does not credit (root cause)

On `.90`, the chain works because the SAS session is **alive**: a host polls
(`0x80`/`0x81` general polls + AFT long polls) and the slave answers, so the EGM
is *online*, WAT2AFT *owns* the device, and an injected `0x72` is executed and
committed (see `hops/hop3-aurum-wat2aft-to-aft-xml.md` and
`../RUNBOOK.md`).

On `.171` the **session is offline**:

| Layer | `.90` (works) | `.171` (broken) |
|---|---|---|
| SAS poll/response cycle | live `80/81` + responses on the bridge | **silent** — no payload polls (ACK-anchor was needed for inject) |
| EGM online state | online & sustained | offline |
| WAT2AFT device ownership | owns the device | `NO OWNED DEVICE FOUND FOR WAT` → exits every ~30 s |
| AFT `0x72` injection | ingested **and committed** (`CREDITED`) | ingested at transport, **never executed** (`CREDITED:NO`) |

So the `0x72` transport win on `.171` is necessary but **not sufficient**: WAT2AFT
will not act on it until an owned, online device exists. **Online state is governed
by CommCtrlSAS's real serial-link on COM11**, not by loopback traffic alone — restoring
polls requires a **physical SAS host on the MUX upstream channel** (or an invasive
COM11 serial host emulator that replaces CommCtrlSAS), not loopback injection alone.

> **Disproven path:** loopback `80`/`81` injection + table-driven `WdRespond` cannot
> bring `.171` online without a real serial-side poll source. Stage 0 on `.90` shows
> enumeration/capability handshake happens on **COM11 serial**, not on loopback
> (loopback carries only `1B80`/`1B81` + `00`). Do not advance Stages 1–3 below
> until COM11 upstream polls are restored or a serial emulator GO is granted.

---

## 2. Config changes already made on `.171` (do not repeat)

`AurumSetup.xml` on `.171` was edited this session (remote config — **frozen now**):

- `OwnerHostId = 1` and `LastConfigurationChange = 2` for: `WAT`, `voucher`,
  `handpay`, `noteAcceptor`, `bonus[0]`, `bonus[1]`.
- Added `<WatAccounts>` with `idNumber = GCC_ST_19737_01`.
- `GoldClub.Aurum.Services`, `CommCtrl`, `CommCtrlSAS` were restarted; services and
  sockets recovered.

These are assumed correct and **must not be modified further**. The remaining gap
is runtime SAS liveness, not config.

---

## 3. What is prepared in this repo (safe, offline)

| Artifact | State | Role |
|---|---|---|
| `Invoke-Stage0SasSniff.ps1` + `WdSniff.cs` + `decode_stage0.py` | ready | **Stage 0** read-only, direction-labeled SAS capture on `.90` (SNIFF\|RECV_ONLY — physically cannot inject). |
| `build_sas_response_table.py` | **new, this session** | Offline. Turns a Stage 0 dump into a `poll → verbatim slave response` JSON table. **Invents nothing**; a poll absent from the table = responder stays silent. The *only* sanctioned source for responder bytes. |
| `WdInject.cs` + `Invoke-WinDivertAft.ps1` + `Send-TestAft1000.ps1` | ready (proven on `.90`) | **Stage 4** AFT `0x72` injector. ACK-anchor already handles idle-but-established links. Targets `.171` with `-IP 10.0.0.171`. |
| `Invoke-AftTransferTest.ps1` / `Convert-AftHistory.ps1` / `Parse-AftHistory.ps1` | ready | **Verification oracle** (hop 5) — read-only AFT XML + SlotLog PASS/FAIL. |

### The one piece still to build: `WdRespond` (a SAS-slave responder)

Not created yet **on purpose** — it cannot be written honestly before the response
table exists (it would have to guess bytes). When Stage 0 lands, build it as a fork
of `WdSniff.cs`/`WdInject.cs` with these hard constraints:

- **Table-driven only.** It loads the `*.responsetable.json` produced by
  `build_sas_response_table.py` and answers a poll **only** with the recorded
  verbatim response for that exact poll key. **No fallback synthesis.** A poll not
  in the table → no reply (logged as `UNKNOWN_POLL`).
- **Refuses to start without a table** (exit non-zero, clear message).
- **Mode-staged** (mirrors §4): `passthru` (observe only, no send) → `gp` (answer
  general polls only) → `enum` (also answer the enumeration/capability long polls
  present in the table). Each mode is an explicit opt-in flag, default `passthru`.
- Same WinDivert hygiene as `WdInject.cs`: idempotent `CloseOnce()`, **never**
  `sc stop`/`sc delete` per run (avoids the `STOP_PENDING` wedge documented in
  `../RUNBOOK.md` §5).

---

## 4. The go/no-go sequence (after Stage 0 transcript arrives)

Each stage has an explicit **GO gate**. Do not advance until it is green. No live
SAS/AFT action against `.171` happens before Stage 1, and no money transfer before
Stage 4.

### Stage 0 — capture ground truth on `.90` (in progress, do not duplicate)

Run by the separate background task. Output: a `../captures/stage0-90-*.txt` dump of
byte-level poll/response pairs on the `.90` bridge, including the bring-up /
enumeration handshake.

**GO gate 0:** dump exists, `decode_stage0.py` shows the steady poll cycle **and**
the non-trivial enumeration long polls with `CRC=OK` responses.

First command once the dump is in hand:

```powershell
# decode + build the verbatim response table (offline; touches no cabinet)
python decode_stage0.py ".\../captures/<stage0-dump>.txt"
python build_sas_response_table.py ".\../captures/<stage0-dump>.txt" -o ".\../captures/sas-response-table.json"
```

Inspect `sas-response-table.json`: confirm `80`/`81` general-poll responses and the
enumeration/capability long-poll responses (candidates to look for, but **use only
what the table actually contains**): `0x1F` machine ID, `0x54` SAS version/serial,
`0x74` AFT game lock & status, `0x72 … FF …` AFT status interrogate, `0xA0`/`0x56`
enabled-games, plus any meter polls that precede `ALL WAT TRANSACTIONS FINISHED`
on `.90`.

### Stage 1 — passive passthru on `.171` (prove framing/direction, send nothing)

Build `WdRespond` and run it in `passthru` against `.171:31150` (and `31100`). It
forwards every packet unchanged and logs what it *would* answer from the table.
This validates port roles, `0x1B` framing, and that the table keys match real
`.171` polls — **without emitting a single byte**.

**GO gate 1:** passthru shows live polls on `.171` whose keys exist in the table,
and `injected=False` throughout. If `.171` is fully silent (no polls at all), that
is a different problem (bridge/COM peer) — stop and report, do not proceed.

### Stage 2 — answer general polls (bring the EGM online, sustained)

Switch `WdRespond` to `gp` mode: reply to `0x80`/`0x81` with the **table's** general-
poll responses only. Watch the WAT2AFT log on `.171`.

**GO gate 2:** the `NO OWNED DEVICE FOUND FOR WAT … WILL NOW EXIT` loop **stops**
and the EGM/session reads online for a sustained window (e.g. ≥ 60 s with no
re-exit). If it still exits, ownership needs the enumeration replies → Stage 3.

### Stage 3 — answer enumeration/capability (WAT2AFT fully owns the device)

Switch to `enum` mode: additionally answer the enumeration/capability long polls
from the table. Goal: WAT2AFT proceeds far enough that a transfer would be accepted.

**GO gate 3:** with the responder running, the service reaches the owned/ready
state — concretely, a benign AFT **status interrogate** (`0172 02 FF 00 0F22`,
which reads, does not transfer) is answered and WAT2AFT no longer reports no owned
device; ideally a dry interrogate yields a clean `ALL WAT TRANSACTIONS FINISHED`
path with no pending error.

### Stage 4 — one low-risk AFT control transfer + verify

Only now run the proven injector against `.171`, **once**, with a conservative,
clearly-test transfer:

- **Fresh transaction id** (let the script auto-pick; never reuse — replays ingest
  without crediting).
- **Non-restricted (promo) default** (`-nr`), a **small** amount — not the maximum,
  not `9999999999`, not cashable. Keep the responder running so the session stays
  online during the transfer + interrogate.

```powershell
# DryRun first (prints the packet, injects nothing):
.\Send-TestAft1000.ps1 -IP 10.0.0.171 -Amount 1000 -nr
# then the single real attempt (responder running in `enum` mode):
.\Send-TestAft1000.ps1 -IP 10.0.0.171 -Amount 1000 -nr -Send
```

**GO gate 4 (success):** the verification chain (`../RUNBOOK.md` §6)
shows on `.171`: sasmsgr full-hex `qGMID1:0172…` match → SlotLog `Cashless In` +
`promo credit state increased` → OneHand `Transfer IN` / `Withdraw successful
GCC_ST_19737_01`. Confirm read-only with the oracle:

```powershell
.\Invoke-AftTransferTest.ps1 -IP 10.0.0.171   # read-only PASS/FAIL
```

---

## 5. Guardrails (this whole plan)

- **No SAS/AFT emission against `.171` before its stage gate is green.** Stage 1 is
  passthru (send nothing); Stage 4 is a single small promo transfer.
- **Never synthesize SAS bytes.** Responder answers only verbatim table entries;
  unknown poll → silence.
- **No further remote config edits.** §2 changes are frozen.
- **No service restarts / reboots** unless strictly required and explicitly
  approved — prefer none. Read-only verification (`sc query`, log tail over `c$`,
  `Get-NetTCPConnection`) is fine.
- **WinDivert hygiene:** never per-run `sc stop`/`sc delete` (wedges the driver into
  `STOP_PENDING`); use the safe teardown only when `STOPPED`.

---

## 6. Current blockers (post-investigation)

| Item | Blocked? | Why |
|---|---|---|
| Stage 0 capture + response table | **No** — done | `../captures/stage0-90-*`; loopback carries only `80`/`81` + `00` |
| `WdRespond` / loopback slave | **Disproven for `.171`** | Online gated on COM11 serial; poll inject insufficient |
| AFT inject on `.171` | **Yes** — WAT offline | `Invoke-WinDivertAft.ps1` ready; needs owned device first |
| **Physical prerequisite** | **Yes** | External SAS host on MUX upstream channel (`poll-source.md`) |
| Alt-credit without SAS | **No viable path** | `alt-credit-paths.md` |
| Live `.171` diagnostics | **Yes** | SMB/PsExec denied; need `GOLD-CLUB\test` cred for `10.0.0.171` |

**Exact first action after Stage 0 completes:** decode the dump and build the table
(the two commands under **GO gate 0** in §4), then inspect
`sas-response-table.json` for the `80/81` + enumeration responses before writing
`WdRespond` and starting Stage 1 passthru.
