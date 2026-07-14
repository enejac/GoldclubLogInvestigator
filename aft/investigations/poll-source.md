# POLL-SOURCE-90-171 - what generates the SAS general-poll stream (80/81)

Read-only diagnostic, 2026-06-16. Goal: identify EXACTLY what produces the SAS
host general-poll stream (`80`/`81`) on the working cabinet **10.0.0.90** and why
**10.0.0.171** lacks it. Classify the source as (a) external hardware,
(b) internal process, or (c) network.

Method: `.90` inspected **live** (PsExec SYSTEM via `C:\Tools\PSTools\PsExec.exe`,
`c$` read-only); `.171` reconstructed from repo artifacts (`../captures/stage0-171-*`,
`com11-emulator-plan.md`, `171-landing-plan.md`) because `.171` admin is
blocked (`\\10.0.0.171\c$` denied, no cached creds, PsExec Access denied).
**No service was stopped, no config edited, no bytes injected, no reboot.** The
only writes were my own scratch files under `.90 C:\Windows\Temp`, which were
deleted afterward.

---

## VERDICT

**(a) EXTERNAL HARDWARE** for *organic* steady-state operation on `.90`. The `80`/`81`
polls normally reach CommCtrlSAS over the physical COM11 serial line through the
USB multiplexer.

**Lab inject exception (2026-07-09):** `WdPollInject` **Option D** simulates the same
`1B81`/`1B80` poll stream on TCP `31150` without any serial host — txn 83/84 credited
$1000 on `.90`. See [`no-physical-polls-layers.md`](../no-physical-polls-layers.md).
This does **not** replace CommCtrlSAS or open COM11; it substitutes Hop 2 poll bytes
when the bridge TCP session is alive.

- **(b) internal process - RULED OUT** for organic polls:
  COM11 is held exclusively by `CommCtrlSAS.exe` (PID 2400), and the only TCP
  peer on the bridge ports `31100`/`31150` is Aurum (PID 7316) over loopback.
  No host/poller/tester process or service exists or runs.
- **(c) network - RULED OUT.** `netstat` on `.90` shows the SAS bridge ports
  carry **only loopback** `CommCtrlSAS <-> Aurum` traffic; there is no remote
  peer feeding polls. Aurum's `10.100.0.255` `50010/50011` connections are its
  own internal `SASControler` HTTP mesh and exist regardless of the poll stream.

**Replicability on `.171`:** NOT reproducible by software/loopback on `.171`.
The polls originate on the COM11 serial side, upstream of CommCtrlSAS. Restoring
them requires a **physical SAS host on the MUX upstream channel** (the faithful
`.90` setup) or investigating a MUX firmware/wiring/host delta between cabinets -
not a loopback inject and not a PC-side fix. `.171` already has the MUX, an open
COM11, a negotiated station, and the loopback bridge; what it lacks is the
external poller driving bytes into the MUX.

---

## EVIDENCE

### 1. `.90` processes (live, PsExec SYSTEM) - machine `GST20664`

| Process | PID | Role |
|---|---|---|
| `CommCtrlSAS.exe` | 2400 | serial<->TCP **bridge**; owns COM11 + ports 31100/31101/31150/31151 |
| `GoldClub.Aurum.Services.exe` | 7316 | SAS **slave** (answers `00`; logs `qGMID1:`) |
| `CommCtrl.exe` | 4256 | non-SAS serial gateway (ports 30000-30800) |
| `OneHand.exe` | 2012 | game client |

No process named or containing host, poll, tester, poller, or emulator runs.

### 2. `.90` netstat (-ano) - SAS bridge ports are loopback-only

```text
TCP  0.0.0.0:31100        LISTENING    2400     # CommCtrlSAS
TCP  0.0.0.0:31150        LISTENING    2400     # CommCtrlSAS
TCP  127.0.0.1:31100      127.0.0.1:55903  ESTABLISHED  2400
TCP  127.0.0.1:31150      127.0.0.1:55904  ESTABLISHED  2400
TCP  127.0.0.1:55903      127.0.0.1:31100  ESTABLISHED  7316   # Aurum -> bridge
TCP  127.0.0.1:55904      127.0.0.1:31150  ESTABLISHED  7316   # Aurum -> bridge
TCP  10.100.0.255:50011   LISTENING    7316     # Aurum SASControler1 (internal mesh)
```

Only ONE peer (Aurum, 7316) connects to the bridge, over 127.0.0.1. No external
IP touches 31100/31150 => the poll source is NOT on the network.

### 3. `.90` serial device enumeration - the MUX is on COM11 over USB

```text
Ports  OK  MUX/SAS (COM:11)   USB\VID_0483&PID_5740\206139555241   (STM32 USB VCP)
```

This is the `VE MULTIPLEXER 4004145 SI 2CH` that `CommCtrlSAS` logs as
`CheckForMux Detected`. The polls arrive into CommCtrlSAS from this serial line.
A second local process cannot open COM11 while CommCtrlSAS holds it, so an
internal serial-side generator is impossible.

### 4. Config is identical - not the differentiator

`.90` `C:\Goldclub\services\CommCtrlSAS\CommControler.ini`:

```text
<11>    <921600>
```

Same `<11> <921600>` on `.171` (per `com11-emulator-plan.md`). Aurum SAS
adapter config `SASControlerIds = "SASControler, SASControler2"` on `.90`.

### 5. `.90` services - no poll generator; only bridge + slave

Running GoldClub services: `GoldClub.Aurum.Services`, `... Serial Communication
Gateway` (CommCtrl), `... Serial Communication Gateway SAS` (CommCtrlSAS),
`... Hardware Subsystem`, `GoldClub.Logging.LogDaemon`. The only SAS binaries on
disk are `CommCtrlSAS.exe` (bridge) and `GoldClub.Aurum.Adapters.SAS.dll`
(slave). `AuProgsWAPservicesTester.exe` / `SASSetup.exe` exist on disk but are
**NOT running** (absent from the process list) - neither generates the live stream.

### 6. `.90` bus capture (steady) - the poll stream, framed by the bridge

`../captures/stage0-90-steady-20260616-141116.txt` (676 pkts / 45 s, ~200 ms cadence):

```text
S2C  General Poll 81  x113     # CommCtrlSAS -> Aurum, 1B81
S2C  General Poll 80  x112     # CommCtrlSAS -> Aurum, 1B80
C2S  RT/exception 00  x113     # Aurum -> CommCtrlSAS, slave reply 00
14:11:58.795 HOST  >> 1B81
14:11:58.797 SLAVE << 00
14:11:58.996 HOST  >> 1B80
```

### 7. `.90` slave log - host polling is live RIGHT NOW

`...\GoldClub.Aurum.Services sasmsgr of SASControler1\2026-06-16.log` tail
(captured 16:54 today), steady ~200 ms alternation:

```text
2026-06-16T16:54:52.451+01:00 INFO [:] qGMID1:81
2026-06-16T16:54:52.662+01:00 INFO [:] qGMID1:80
```

### 8. `.171` bus capture - polls are ABSENT

`../captures/stage0-171-steady-20260616-144705.txt` (294 pkts / 30 s):

```text
1B81 occurrences : 0
1B80 occurrences : 0
hex=00 (slave 00): 0
hex=01 occurrences: 142    # Aurum -> 31150 keepalive only
```

Every S2C (CommCtrlSAS -> Aurum) packet is a bare ACK (`len=0 hex=-`). Aurum just
emits `01` keepalives; CommCtrlSAS relays no polls because it **receives none from
the serial side**.

### 9. `.171` bridge came up clean - the delta is purely the missing serial poller

`.171` `CommCtrlSAS\2026-06-16.log` (quoted in `com11-emulator-plan.md`):

```text
serial port \\.\COM11 open on baudrate 921600
CCommConnection::CheckForMux Detected: VE MULTIPLEXER 4004145 SI 2CH - 2.0.1
Listening on comm 11
CH 2
SA 1
Connection 3110000001 established on port 31100
Connection 3115000002 established on port 31150
```

COM11 opens, the same MUX model negotiates CH2/SA1, the loopback listeners come
up - **yet zero `80`/`81` flows.** Same software, same config, same MUX model,
same bridge: the only missing element is the **external host that drives polls
into the MUX upstream channel** on `.90` but not on `.171`.

---

## KEY DIFF (one line)

`.90`: serial -> `1B81`/`1B80` @~200 ms -> Aurum `00` (676 pkts/45 s, live now).
`.171`: serial silent -> 0x `1B80`/`1B81`, only Aurum `01` keepalives (294 pkts/30 s).
Same CommCtrlSAS, same `CommControler.ini` (`<11> <921600>`), same MUX model. The
generator lives on the COM11/MUX **serial host side**, external to the PC.

