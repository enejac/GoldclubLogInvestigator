# SAS-CONFIG-PARITY-90-171 - live .90 vs live .171 (authorized remediation pass)

Read-only diagnostic + authorized remediation pass, 2026-06-16 (updated after
`.171` became reachable). Reference (known-good) cabinet **10.0.0.90** (`GST20664`,
EGM `GCC_ST_20664_01`); subject cabinet **10.0.0.171** (`GST19737`, EGM
`GCC_ST_19737_01`).

Access this pass: BOTH cabinets read **live**. `.90` over `\\10.0.0.90\c$`; `.171`
over `\\10.0.0.171\c$` and PsExec SYSTEM (`GOLD-CLUB\test`). No service stopped,
no config edited, no bytes injected, no reboot (see "Actions taken" - the
remediation trigger was not met).

---

## HEADLINE VERDICT

**This is NOT a configuration difference, and `.171`'s SAS stack is NOT down.**

1. **Every SAS-governing config file is byte-identical between `.90` and `.171`**,
   except for legitimate **machine-identity** fields and **game/denom content**.
   There is **no behavioral/structural SAS difference** to fix.
2. `.90` live config is **byte-identical to `.90`'s own factory-default snapshot** -
   `.90` was **not hand-modified**; it has no hidden extra config that manufactures
   the poll stream.
3. **Both cabinets are running the identical stack** right now (CommCtrl,
   CommCtrlSAS, GoldClub.Aurum.Services, OneHand, LogDaemon) with the **identical
   loopback bridge established** (Aurum <-> CommCtrlSAS on `31100`/`31150`).
4. **The `.171` "poll stream" seen earlier today was self-injected, not real.** The
   `qGMID1:80/81` burst at 14:50 matches the prior session's `WdPollInject`
   loopback injection byte-for-byte and second-for-second. `.171` has **no organic
   host-poll history**; `.90` has 15 MB of continuous organic polling.

=> No config edit was warranted (the only diffs are identity/content, which must
stay machine-correct). No stack restart was performed: the remediation trigger
("a real config diff exists") is not met, the stack is already healthy and
identical to `.90`, and a restart re-runs the same successful init - it cannot
make an **external** serial poller start sending bytes. **Polls/WAT ownership will
not return from a PC-side config change or stack restart**, because the PC-side
config and stack are already identical to the known-good cabinet. The remaining
differentiator is external to the PC (the SAS host/poller on `.171`'s COM11/MUX
serial channel).

---

## 1. FULL CONFIG DIFF TABLE (.90 live vs .171 live)

SHA-256 over every `*.xml/*.ini/*.txt` under `modules\hw`, `aurum`, `CommCtrl`,
`CommCtrlSAS`, `system` (bios\etc\application) and `services\aurum\config`.

| File | .90 vs .171 | Nature |
|---|---|---|
| `modules\hw\Drivers\ChainLink\link0/1/2.xml` | IDENTICAL | structural (HW PnP bus) |
| `modules\hw\endpoints\tcp\connection0/1/2.xml` | IDENTICAL | structural (`127.0.0.1:30500/30600` = CommCtrl HW bus, not SAS) |
| `modules\hw\drivermanager\loadList.xml` | IDENTICAL | structural |
| `system\network.xml` | IDENTICAL (`GS15` both) | structural |
| `CommCtrlSAS\CommControler.ini` | IDENTICAL (`<11> <921600>`) | the only SAS serial config; COM+baud only |
| `CommCtrl\CommControler.ini` | IDENTICAL | structural |
| `aurum\SASControler1\options.xml` | IDENTICAL | behavioral (perf/queue) |
| `aurum\SASControler2\options.xml` | IDENTICAL | behavioral |
| `services\aurum\config\SASControler1\SASsetupData.xml` | IDENTICAL | **behavioral SAS (PollRate 150, AFT, validation) - same** |
| `services\aurum\config\SASControler1\crcfileslist.txt` | IDENTICAL | structural |
| `services\aurum\config\SASControler1\ClientsSet.xml` | **DIFF** | **machine-identity only** (see 2a) |
| `services\aurum\config\AurumSetup.xml` | **DIFF** | **machine-identity + game content + state counter** (see 2b) |
| `system\soundvolume.xml` | DIFF | non-SAS (audio volume), ignored |

Only **3 files differ**; two are SAS-adjacent and both differ **only** in
machine-correct fields; the third is unrelated (sound).

## 2. THE TWO SAS-RELATED DIFFS, LINE BY LINE

### 2a. `ClientsSet.xml` - one field, machine-identity (DO NOT CHANGE)

```text
.90 :  <AurumEgmId>GCC_ST_20664_01</AurumEgmId>
.171:  <AurumEgmId>GCC_ST_19737_01</AurumEgmId>
```

Everything else identical: `SASAddress 1`, `Port 31100`, `WakeUpPort 31150`,
`HostURL localhost`, all AFT flags, validation, denom. `GCC_ST_19737_01` is the
correct EGM id for `.171`. **No behavioral change.**

### 2b. `AurumSetup.xml` - identity + game content + state counter (DO NOT CHANGE)

Diffs fall into three buckets, all of which must stay machine-correct:

- **Machine identity (correct for .171):** `NetworkHostName GST19737`,
  `ServiceURI http://GST19737:50010/GM2AU`, `MessengerURI .../GST19737:50011/...`,
  every `EgmId GCC_ST_19737_01`, `CabinetSerialNumber 19737`.
- **Game/denom content:** `.171` additionally has a `denomId="100"` denom and its
  full combo list (extra game/denom offering). Commercial content, not SAS wiring.
- **State counter:** `ConfigurationId` `51` (.90) vs `241` (.171) - per-cabinet
  config-version counter.

**Behavioral SAS topology is identical.** Parsed device-ownership table (the part
that governs WAT/AFT ownership and the "NO OWNED DEVICE FOUND FOR WAT" symptom) is
identical on both, e.g.:

```text
WAT#0           owner=1 enabled=false lcc=2   (both .90 and .171)
voucher#0       owner=1 enabled=false lcc=2   (both)
handpay#0       owner=1 enabled=false lcc=2   (both)
bonus#0/#1      owner=1 enabled=false lcc=2   (both)
communications#1 owner=1 enabled=false lcc=0 reqplay=true (both)
```

Messenger roles identical: `GM2AU` = `MessengerType EGM` (`:50010`), `SASControler1`
= `MessengerType HOST` (`:50011`, Aurum's internal HTTP mesh role - not a SAS
serial master). WAT accounts (cashable/noncashable/promo) identical.

## 3. .90 LIVE vs .90 FACTORY-DEFAULT SNAPSHOT - .90 NOT hand-modified

`var\state\maintenance\factory-defaults\configuration\bios\etc\application\...`
hashes **IDENTICAL** to `.90` live for every ChainLink link, TCP connection,
loadList, network.xml, both CommControler.ini, and aurum SASControler1/2 options.
The snapshot's `services\aurum\config\SASControler1\` contains only
`crcfileslist.txt` (AurumSetup.xml/ClientsSet.xml/SASsetupData.xml are runtime
config, not snapshotted). **Conclusion: a factory reset on `.90` would restore the
exact SAS config that runs today - there is no hand-added poll-source config.**

## 4. LIVE RUNTIME STATE (both cabinets, this pass)

| | .90 (GST20664) | .171 (GST19737) |
|---|---|---|
| CommCtrl | running | running (pid 4920) |
| CommCtrlSAS (bridge) | running pid 2400 | **running pid 3444** |
| GoldClub.Aurum.Services (slave) | running pid 7316 | **running pid 2848** |
| OneHand / LogDaemon | running | running |
| goldclub services | all 5 Running | all 5 Running |
| Bridge `31100`/`31150` | Aurum<->CommCtrlSAS Established (loopback) | **Aurum<->CommCtrlSAS Established (loopback)** |

`.171` `CommCtrlSAS` log confirms a clean serial bring-up (COM11 open @921600,
`VE MULTIPLEXER 4004145 SI 2CH` detected, `SA 1`, listeners + connections up).
**The stack is healthy and structurally identical to `.90`.**

## 5. DECISIVE: .171's earlier "poll stream" was self-injected, not organic

`.171` `sasmsgr of SASControler1\2026-06-16.log` is only **186 lines / 9446 bytes**,
spanning **14:50:03 -> 14:50:40 (~37 s)** plus one injected AFT at 11:02. That 37 s
window is the prior session's `WdPollInject` loopback run:

```text
pollinject-171-poll-20260616-154237.txt:
 [14:50:03.081] WDPOLLINJECT mode=poll serverPort=31150 ... runSeconds=40 intervalMs=200
 [14:50:03.303] INJECT #1 frame=1B81 ...
 [14:50:03.500] INJECT #2 frame=1B80 ...
.171 sasmsgr:
 2026-06-16T14:50:03.314+01:00 qGMID1:81   <- matches INJECT #1 (1B81 @ .303)
 2026-06-16T14:50:03.501+01:00 qGMID1:80   <- matches INJECT #2 (1B80 @ .500)
```

By contrast `.90` `sasmsgr` is **~15 MB of continuous organic `qGMID1:80/81`**.
=> `.171` has **never** had a genuine, host-originated poll stream; its only polls
were synthesized over loopback by us. This corrects the earlier capture-based
impression and removes any "intermittent real polling" hypothesis for `.171`.

(Current real-time note: neither cabinet's `sasmsgr` is being written at the
instant of this pass; that log is logging-level gated and was elevated only during
prior capture windows. The authoritative live "is it polling" proof remains the
loopback sniff captures: `stage0-90-*` show `.90` polling, `stage0-171-steady-*`
show `.171` organically silent.)

## 6. BEHAVIORAL vs MACHINE-SPECIFIC JUDGEMENT

| Field | Class | Action |
|---|---|---|
| ChainLink/TCP/loadList/network/CommControler.ini/options/SASsetupData | behavioral+structural | identical - nothing to do |
| device-ownership table, messenger roles, WAT accounts (AurumSetup) | behavioral | identical - nothing to do |
| `AurumEgmId`, `EgmId`, `NetworkHostName`, `ServiceURI`, `MessengerURI`, `CabinetSerialNumber` | machine-identity | keep .171 values - DO NOT change |
| `denomId=100` + combos | game content | keep .171 values - DO NOT change |
| `ConfigurationId` (51 vs 241) | per-cabinet state counter | keep .171 value - DO NOT change |
| `soundvolume.xml` | non-SAS | ignore |

## 7. ACTIONS TAKEN

- **Config edits: NONE.** No behavioral/structural diff exists; the only diffs are
  machine-identity/content which the guardrails forbid overwriting. With nothing
  legitimate to change, no `.bak` backups were needed (no file was modified).
- **Stack restart: NOT performed.** The authorized remediation (fix + restart +
  verify) is gated on "IF THEY DIFFER". No behavioral diff exists, so the trigger
  is not met. Additionally: `.171`'s stack is already up, healthy, and identical to
  `.90`'s; restarting re-runs the same initialization that already succeeds
  (COM11/MUX/CH2/SA1/bridge) and cannot cause an **external** serial device to
  begin polling. Restarting a live cabinet for no expected benefit was judged
  unwarranted.
- **Money/AFT: untouched** (as required).

## 8. POST-PASS VERIFICATION RESULT

- **Did 80/81 polls return? NO** - and nothing was changed that could return them.
  `.171` is organically silent (its only `80/81` were injected by us). Config and
  stack are already at parity with `.90`.
- **Did WAT ownership return / "NO OWNED DEVICE FOUND FOR WAT" stop? N/A** - WAT
  device ownership (`WAT#0 owner=1`) is **already identical to `.90`**; the WAT/AFT
  symptom is downstream of the missing live SAS session, not of an ownership
  misconfig. Without an organic poll stream the SAS session never goes fully online,
  so WAT cannot transact regardless of config.

## 9. PLAIN CONCLUSION

`.171`'s lack of SAS `80/81` activity is **not** caused by any PC-side config
difference and **not** by a stopped service. The SAS config XMLs are byte-identical
to the known-good `.90` (and to `.90`'s factory snapshot) save for machine-identity
and game content; both stacks run the same processes with the same loopback bridge.
The one piece of "evidence" that `.171` had ever polled was our own loopback
injection. The actual differentiator is **upstream of the PC**: the external SAS
host/poller on `.90`'s COM11/MUX serial channel that drives `80/81` into
CommCtrlSAS is absent on `.171` (consistent with the noted MUX firmware delta
`.90 SI-1.0.3` vs `.171 2.0.1`). **No config edit or stack restart will restore
polling**; restoring it requires the external serial poll source (or an explicitly
authorized COM11 serial-host emulator that replaces CommCtrlSAS - out of scope and
not done here).

---

# STACK RESTART EMPIRICAL TEST (.171) - 2026-06-16 ~22:46 (authorized full clean restart)

User-authorized empirical test: the user is certain the missing poll stream is NOT
hardware and config was already proven byte-identical to `.90`. So we did the one
thing the prior pass deliberately had not: **cleanly restart the whole GoldClub SAS
stack on `.171` via the platform's own service control and watch whether organic
`80/81` polling returns on its own.** Read-only otherwise; no AFT/money; no config
edits; WinDivert left demand-start (no per-run `sc delete`).

## VERDICT: **NO.** Organic 80/81 polling did NOT return after the clean stack restart.

A textbook-clean service restart (COM11 re-opened, MUX re-detected, CH2/SA1
re-negotiated, loopback bridge re-established, device-ownership table re-enumerated)
produced **zero** host polls. This **corroborates** that the poll source is upstream
on the COM11/MUX serial side, not the PC stack.

## 1. PRE-STATE (read-only baseline, 22:44)

- Services (all `Running`, StartType `Manual`, no declared OS dependencies):
  `GoldClub Hardware Subsystem`, `GoldClub Serial Communication Gateway` (CommCtrl),
  `GoldClub Serial Communication Gateway SAS` (CommCtrlSAS), `GoldClub.Aurum.Services`,
  `GoldClub.Logging.LogDaemon`.
- Processes: `CommCtrl` pid 4920, `CommCtrlSAS` pid 3444, `GoldClub.Aurum.Services`
  pid 2848 (all started 12:33 today); `OneHand` pid 3372 (since 6/15); `Bootstrap`
  watchdog pid 2252 (since 6/2). **No injector (WdPollInject/WdInject/WdRespond/
  WdSniff/SasSerialEmulator) running.**
- Bridge: `127.0.0.1:31100`/`:31150` Established loopback under CommCtrlSAS pid 3444.
- CommCtrlSAS log: clean 12:33 bring-up (COM11 @921600, `VE MULTIPLEXER 4004145 SI
  2CH - 2.0.1`, CH2/SA1, bridge), benign reconnect at 14:55. No poll content.
- sasmsgr log: **frozen at the 14:50:03->14:50:40 `qGMID1:80/81` burst = the prior
  WdPollInject window.** No organic polling at baseline.

Key structural fact: the SAS poll chain (CommCtrl -> CommCtrlSAS -> Aurum) is
**service-controlled**, while `OneHand` and the `Bootstrap` watchdog are **separate
processes**. => the chain can be restarted purely via service control without ever
touching the watched OneHand/Bootstrap (no watchdog-reboot risk).

## 2. RESTART ACTIONS (22:46:26 -> 22:46:39, via Stop-Service/Start-Service as SYSTEM)

Stop (reverse dependency), each confirmed `Stopped`:
- 22:46:26.366 Aurum -> Stopped (.900)
- 22:46:26.902 CommCtrlSAS -> Stopped (27.157)
- 22:46:27.159 CommCtrl -> Stopped (27.415); COM11 fully released (0 CommCtrlSAS procs)

Start (forward dependency), each confirmed `Running`:
- 22:46:29.478 CommCtrl -> Running (new pid 5848)
- 22:46:31.061 CommCtrlSAS -> Running (new pid 5524)
- 22:46:32.646 Aurum -> Running (new pid 3068)

Watchdog safety (the whole point of using service control): **OneHand pid 3372 and
Bootstrap pid 2252 were UNCHANGED before/after** => no crash, no watchdog reboot.
Hardware Subsystem and LogDaemon left running (LogDaemon kept up so logging continued;
Hardware Subsystem excluded because OneHand depends on it and it is not in the SAS
poll path). No reboot needed - the service restart was the clean path.

## 3. POST-STATE (22:46:33 -> 22:47:47) - serial bring-up identical to known-good

CommCtrlSAS (new pid 5524) log:
```text
22:46:32.971 serial port \\.\COM11 open on baudrate 921600
22:46:33.172 CCommConnection::CheckForMux Detected: VE MULTIPLEXER 4004145 SI 2CH - 2.0.1
22:46:33.674 CH 2
22:46:34.176 SA 1
22:46:42.963 Connection 3110000001 established on port 31100
22:46:42.964 Connection 3115000002 established on port 31150
```
Bridge TCP: `127.0.0.1:31100`/`:31150` Established loopback under pid 5524. MUX
firmware string reports **SI 2CH - 2.0.1** (still the `.90` SI-1.0.3 vs `.171` 2.0.1
delta). SASControler1 re-enumerated the device-ownership table at 22:47:59
(incl. `GCC_ST_19737_01.0.WAT`) - **no `NO OWNED DEVICE FOUND FOR WAT` lines**; WAT
device is owned, it simply has no live session to transact on.

## 4. THE TEST - read-only loopback sniff (SNIFF mode, cannot inject)

`Invoke-Stage0SasSniff.ps1 -IP 10.0.0.171 -Mode steady -Seconds 60`
Capture `../captures/stage0-171-steady-20260616-234125.txt`, window
**22:48:49.470 -> 22:49:49.505** (~2 min after the bridge came back), 566 packets:

```text
  S2C (CommCtrlSAS->Aurum, host poll)  : 0      <- ZERO 1B80 / 1B81
  C2S (Aurum->CommCtrlSAS, slave resp) : 283    <- only 50208->31150
  decoded: C2S RT/exception 01  x283 (Aurum keepalive on wakeup port, ~200ms)
  SAS LONG POLLS / general poll 80/81  : none
  connection changes during window     : none (steady-state)
```

sasmsgr after restart: **still frozen at 14:50:40; 0 `qGMID` lines >= 22:00** - no
new organic poll stream was logged. Injector cross-check during/after: **no
WdPollInject/WdInject/WdRespond/WdSniff/SasSerialEmulator process running**, and the
only `80/81` anywhere in the logs are the old 14:50 injected burst whose timestamps
do not overlap this 22:48-22:49 window. The 283 C2S frames are bare `01` keepalives,
not poll responses. => the zero polls are an **organic** zero, not a masked injector.

## 5. PLAIN VERDICT

**NO - organic 80/81 polling did not return after a clean full-stack restart.**
Stopping and restarting CommCtrl -> CommCtrlSAS -> Aurum via the platform's service
mechanism re-ran the exact, successful serial init (COM11 @921600, same MUX
`4004145 SI 2CH - 2.0.1`, CH2/SA1, loopback bridge, device ownership) - and the bus
stayed organically silent (0 host polls in 60s; Aurum only emits `01` keepalives).
This is the empirical confirmation that **the poll source is upstream of the PC, on
the COM11/MUX serial side** (an external SAS host/poller driving bytes into the MUX,
present on `.90`, absent on `.171`). No PC-side action - config parity (already
proven) or a clean stack restart (proven here) - makes the polls appear. The
remaining differentiator is the external serial poll source (and/or the MUX
firmware delta `.90 SI-1.0.3` vs `.171 2.0.1`).
