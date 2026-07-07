# Hop 2 — CommCtrlSAS.exe bridge ↔ GoldClub.Aurum.Services (loopback SAS-over-TCP)

Defensive QA / robustness architecture mapping of **one hop** of the verified
AFT/WAT credit-transfer chain on the owned lab cabinet.

- Cabinet: `10.0.0.90` (`GST20664`), EGM `GCC_ST_20664_01`, asset `777`
- Hop under study: **the loopback TCP link between `CommCtrlSAS.exe` (serial↔TCP
  bridge) and `GoldClub.Aurum.Services` (SAS messenger)** on ports `31100`/`31150`,
  with the `0x1B` bridge framing prefix.
- This document is read-only analysis. No traffic was sent to the cabinet while
  producing it.

Full chain for context (this file documents only hop 2):

```
IGT SAS tester (COM11)
  -> CommCtrlSAS.exe        (serial<->TCP bridge, loopback 31100/31150, 0x1B framing)   <-- HOP 2
  -> GoldClub.Aurum.Services (WAT2AFT, "sasmsgr of SASControler1")
  -> AFT XML -> OneHand -> SlotLog -> verification
```

---

## 1. Endpoints & transport

The physical SAS RS-232 link (`COM11`) is bridged onto loopback TCP by
`CommCtrlSAS.exe`. Two ports carry the bridged SAS byte stream:

| Port | Owner | Role |
|---|---|---|
| `31100` | `CommCtrlSAS.exe` | bridge endpoint (EGM-side ack flow `…->31100`) |
| `31150` | `CommCtrlSAS.exe` | bridge endpoint (host poll flow `31150->…`) |

Source: `../protocol-raw-traffic.md` §4 documents the bridge ports and the
WinDivert view; `report/AFT_Robustness_Findings.md` #6 confirms socket
ownership from live enumeration:

> `CommCtrlSAS.exe` (PID 4660) holds only `31100` and `31150`, and
> `GoldClub.Aurum.Services` (PID 4872) connects to exactly those. There is no
> third SAS socket carrying the transfer.
> — `report/AFT_Robustness_Findings.md` #6

So the ownership/direction is:

- **`CommCtrlSAS.exe` listens / owns both bridge sockets** (`31100`, `31150`).
- **`GoldClub.Aurum.Services` (the "sasmsgr of SASControler1") connects to those
  sockets** as the SAS messenger client and logs every decoded SAS frame as a
  `qGMID1:<hex>` line.

The WinDivert capture of the live bridge (`\\10.0.0.90\c$\Windows\Temp\aurumtap\dump.txt`,
quoted in `../protocol-raw-traffic.md` §4) shows the byte directions:

```text
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B80   <- SAS poll 0x80 over bridge
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B81   <- SAS poll 0x81 over bridge
TCP [SrcPort=56615 DstPort=31100 ...]   payload 00     <- EGM ack byte back over bridge
```

### The `0x1B` framing byte

Each SAS frame put onto the bridge is prefixed with a single `0x1B` byte. This is
the **bridge's framing/escape prefix**, not part of the SAS message itself — the
SAS frame proper begins at the byte after `0x1B` (e.g. `0x80`, `0x81`, or
`0x01 0x72 …`).

> `31100` / `31150` = CommCtrlSAS serial<->TCP bridge endpoints (the `1B` prefix
> is the bridge's framing byte; `80`/`81` are the SAS poll bytes).
> — `../protocol-raw-traffic.md` §4

> The `1B` byte is the CommCtrlSAS TCP bridge framing prefix seen on the
> `31150 -> Aurum SAS messenger` stream. The SAS packet itself starts at `01 72`.
> — `Invoke-WinDivertAft.ps1` (payload build: `0x1B` + SAS frame)

---

## 2. Protocol — framed SAS over TCP

On-wire format for one message across this hop:

```
[ 0x1B ] [ raw SAS frame ... ]
   |          |
   |          +-- SAS bytes: address, command, length, data, CRC-16 (or a bare poll byte)
   +------------- bridge framing prefix (one byte, 0x1B)
```

Two traffic classes dominate the live stream:

**a) General polls (the steady-state traffic).** The host alternately polls SAS
address 1 with the wake/link-sync bit toggling between `0x80` and `0x81`. On the
bridge these appear framed as `1B80` / `1B81`:

```text
1B80   <- bridge frame:  0x1B prefix + SAS general poll 0x80 (address 1)
1B81   <- bridge frame:  0x1B prefix + SAS general poll 0x81 (address 1)
```

**b) EGM idle reply.** When the machine has nothing to report it returns a single
`00` ack byte on the EGM-side flow (`…->31100`):

```text
00     <- EGM idle/ack reply over the bridge
```

The actual AFT transfer (SAS `0x72`) is *not* normally visible as a raw long-poll
on this bridge — on this cabinet the credit is committed through the Aurum/WAT2AFT
service path, and the captures over five passive windows showed **only**
`1B 80` / `1B 81` polls and `00` idle replies:

> Five passive WinDivert windows (60/90/120/120/120 s) on the `CommCtrlSAS`
> loopback ports `31100`/`31150` showed only SAS general polling (`1B 80` /
> `1B 81`) and EGM idle replies (`00`). No `0x72` (AFT transfer) and no `0x73`
> (AFT registration) bytes were observed.
> — `report/AFT_Robustness_Findings.md` #5

(When a `0x72` *does* traverse the link, the SAS messenger decodes and logs it as
a `qGMID1:0172…` line — see §3.)

---

## 3. Real dump

### 3a. Representative real bridge traffic (live `sasmsgr` log)

Sampled read-only from the latest messenger log:
`\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\2026-06-15.log`.
Each `qGMID1:` line is one SAS frame as decoded by the messenger after the bridge
strips the `0x1B` prefix. The steady state is the alternating `81`/`80` general
poll:

```text
2026-06-15T08:13:19.104+01:00 INFO [:] qGMID1:81
2026-06-15T08:13:19.105+01:00 INFO [:] qGMID1:80
2026-06-15T08:13:19.105+01:00 INFO [:] qGMID1:81
2026-06-15T08:13:19.106+01:00 INFO [:] qGMID1:80
2026-06-15T08:13:19.106+01:00 INFO [:] qGMID1:81
2026-06-15T08:13:19.107+01:00 INFO [:] qGMID1:80
```

A real AFT transfer exchange from the same family of logs (the `0x72` transfer
followed by the `0xFF` interrogate the host sends to read the result):

```text
2026-06-15T13:14:12.384+01:00 INFO [:] qGMID1:80
2026-06-15T13:14:12.609+01:00 INFO [:] qGMID1:017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3232053020200C00000A45
2026-06-15T13:14:12.836+01:00 INFO [:] qGMID1:81
2026-06-15T13:14:13.442+01:00 INFO [:] qGMID1:017202FF000F22
2026-06-15T13:14:13.644+01:00 INFO [:] qGMID1:80
```

Source: lines verbatim from the live `2026-06-15.log`; AFT exchange decode in
`../protocol-raw-traffic.md` §1–§2 (`0172 45 …` = AFT transfer funds,
`0172 02 FF 00 0F22` = AFT status interrogate).

### 3b. New-socket inject (failed) vs in-stream inject (succeeded)

Two different injection strategies were tried on this hop on **2026-06-15**:

**(i) New TCP socket — FAILED.** An early test opened a *new* `TcpClient` to
`127.0.0.1:31150` and wrote the framed `0x1B + 0x72` payload (e.g. the
`…Transaction39` packet below). The TCP write completed, but the messenger log
did **not** record it — no `qGMID1:017245…` line appeared, because a separate
connection is never merged into the live serial-backed SAS session (see §4).

```text
SAS packet:     017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3739053020200C00007E90
Bridge payload: 1B017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3739053020200C00007E90
```

**(ii) In-stream WinDivert inject — SUCCEEDED.** The proven method injects a TCP
segment INTO the EXISTING `31150 -> Aurum:<ephemeral>` flow at
`SEQ = origSeq + origPayloadLen` (so Aurum reads it as the next in-order SAS
bytes). Two independent $1,000 promo credits landed this way, SAS host connected. Run B
(txn 41), with the full packet logged by the messenger:

```text
2026-06-15T15:00:30.520+01:00 INFO [:] qGMID1:017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3431053020200C0000C56B
2026-06-15T15:00:30.722+01:00 INFO  [GM2AU_aurumExecute] OneHand.AurumEGM - Cashless In: $1,000.00
```

Source: `../../Invoke-WinDivertAft.ps1` / `../../WdInject.cs` (build + inject + verify);
full method and evidence in `../RUNBOOK.md`.

---

## 4. Why a NEW socket fails — and what works instead

The bridge ports (`31100`/`31150`) accept a new inbound TCP connection — the
write to `127.0.0.1:31150` succeeds at the socket layer. **But the bytes written
on that new connection are not merged into the live `CommCtrlSAS ↔ Aurum` SAS
stream.** They are not forwarded to the serial side and are never decoded by the
messenger, so no `qGMID1:` line is produced and no AFT transfer is initiated.

Evidence the *new-socket* approach fails:

1. **2026-06-15 new-socket test** (§3b-i): framed `0x1B+0x72` payload written to
   `:31150` → TCP write OK → **no matching `qGMID1:` line** in the messenger log
   ("not merged into the live CommCtrlSAS stream").
2. **`AFT_Robustness_Findings.md` #6** — `CommCtrlSAS.exe` owns exactly the two
   sockets and Aurum connects to exactly those; there is **no third SAS socket**
   to ride, and a freshly-opened socket is a separate connection that the bridge
   does not splice into the serial-backed session.

The SAS protocol is a polled master/slave bus: the host (real serial peer) owns
the timing and the framing. The bridge faithfully relays *that one serial
session*. An extra TCP client speaking into the listener is not the polled peer,
so its bytes have no place in the master/slave exchange.

**What works (proven 2026-06-15): inject into the EXISTING flow, not a new
socket.** Instead of opening a separate connection, WinDivert injects a single TCP
segment INTO the live `31150 -> Aurum:<ephemeral>` flow (the server->client
direction owned by `CommCtrlSAS`), at `SEQ = origSeq + origPayloadLen`. Because
the segment carries the next in-order sequence number on the *established*
connection, Aurum's TCP stack accepts it as the continuation of the real SAS
stream and the messenger decodes it — producing the `qGMID1:0172…` line and the
committed transfer. The ephemeral destination port is discovered live (never
hardcoded). A post-injection RST/reconnect of the SAS link is expected and
harmless. Tooling: `../../Invoke-WinDivertAft.ps1` / `../../WdInject.cs`; method and
evidence in `../RUNBOOK.md`.

---

## 5. Decoupling difficulty

**Verdict: WORKING via in-stream WinDivert injection (NOT a new socket).**

| Approach | Feasibility on this hop | Notes |
|---|---|---|
| Inject a segment INTO the existing `31150 → ephemeral` flow at `SEQ+payloadLen` via WinDivert | **Works (proven)** | 2026-06-15: two $1,000 promo credits, SAS host connected. Aurum accepts it as next in-order SAS bytes. `../../Invoke-WinDivertAft.ps1` / `../../WdInject.cs` |
| Open a new TCP socket to `31100`/`31150` and write a framed `0x72` | **Fails (proven)** | TCP write OK, no `qGMID1:` line, not merged into live stream (§3b-i, `Findings` #6) |
| Find a third/alternate SAS socket to ride | **Not available** | Only `31100`/`31150` exist; Aurum binds exactly those (`Findings` #6) |
| Be the actual serial peer on `COM11` | Possible but out of scope | Replacing the IGT tester on the physical SAS link (this is hop 1, upstream) |
| Hook / patch the existing `CommCtrlSAS.exe` process | Possible but invasive | Code injection into the live bridge process; unnecessary now |

The decisive insight: the bridge will not multiplex an *arbitrary new* TCP client
into the SAS session, but it will carry bytes injected into the *existing*
session at the correct sequence number — because at the TCP layer those bytes are
indistinguishable from the real stream's continuation. That is why hop 2 is the
working decouple point.

---

## Source map

- Endpoints, `0x1B` framing, TCP direction view: `../protocol-raw-traffic.md` §4
- AFT `0x72` decode / `qGMID1:` meaning: `../protocol-raw-traffic.md` §1–§3
- Only `1B80`/`1B81` + `00` seen on passive capture; socket ownership; no third
  socket: `report/AFT_Robustness_Findings.md` #5, #6
- Bridge payload construction (`0x1B` + SAS frame), in-stream injection, and
  verify-against-sasmsgr: `Invoke-WinDivertAft.ps1`, `WdInject.cs`,
  `Send-TestAft1000.ps1`; full method `RUNBOOK.md`
- Capture method/ports: `Invoke-AurumTrafficCapture.ps1`
- Live representative traffic + failed-injection absence:
  `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\2026-06-15.log`
