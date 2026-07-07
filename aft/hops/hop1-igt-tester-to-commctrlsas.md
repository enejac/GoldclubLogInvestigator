# Hop 1 — IGT SAS tester ↔ CommCtrlSAS (serial SAS / MUX, COM11)

> Defensive QA / robustness architecture mapping of **one hop** of the AFT/WAT
> credit-transfer chain on the **owned lab cabinet** (GoldClub Aurum EGM,
> host `10.0.0.90` / `GST20664`, asset `777`). Read-only documentation: no
> traffic was sent to the cabinet for this file.

Full verified credit path (this document covers **Hop 1**, the first link):

```
[Hop 1]  IGT SAS tester  --(RS-232 serial SAS / MUX, COM11)-->  CommCtrlSAS.exe
[Hop 2]  CommCtrlSAS.exe --(loopback TCP 31100 / 31150, 0x1B framing)--> GoldClub.Aurum.Services (WAT2AFT)
[Hop 3]  WAT2AFT --> AFT XML state --> OneHand --> SlotLog "Cashless In" --> verification
```

---

## 1. Endpoints & transport

| Property | Value | Source |
|---|---|---|
| Upstream endpoint (SAS host / master) | IGT SAS tester acting as the SAS host (general poller + long-poll command originator) | `../protocol-raw-traffic.md` §"Path of the bytes" |
| Downstream endpoint (SAS slave / EGM side) | `CommCtrlSAS.exe` serial endpoint on the cabinet, which bridges the link to Aurum | `../protocol-raw-traffic.md`; `AFT_Robustness_Findings.md` §B.1 |
| Physical medium | RS-232 serial SAS link (IGT MUX), **COM11** on the cabinet | `AFT_Robustness_Findings.md` §B.1; `\\10.0.0.90\c$\Goldclub\services\CommCtrlSAS\CommControler.ini` |
| Serial line speed (baud) | **921600** baud (MUX line rate; configured in `CommControler.ini`) | `\\10.0.0.90\c$\Goldclub\services\CommCtrlSAS\CommControler.ini` |
| SAS gaming-machine address | **address 1** (`01` first byte of every long poll) | `../protocol-raw-traffic.md` §1 / §3 |
| Link framing / CRC | SAS messages, **CRC-16/KERMIT** (poly `0x1021`, reflected `0x8408`), CRC sent little-endian | `Invoke-WinDivertAft.ps1` `Get-SasCrc16` |
| Bridge handoff (downstream of this hop) | CommCtrlSAS re-emits the SAS bytes over loopback TCP `31100`/`31150` with a `0x1B` framing prefix (this is **Hop 2**, shown only for context) | `../protocol-raw-traffic.md` §4 |

Cabinet serial config (verbatim, read-only) at
`\\10.0.0.90\c$\Goldclub\services\CommCtrlSAS\CommControler.ini`:

```ini
# Configuration file for communications serial port settings
# !!!CAUTION!!!EDITING THIS FILE MIGHT RESULT IN UNPREDICTED BEHAVIOUR!!!
# COM	baudrate
<11>	<921600>
# Edited with: CommConfig
# Date: 28.8.2008 12:24:59
```

This confirms the IGT-tester side of CommCtrlSAS is bound to **COM11 @ 921600 baud**.

---

## 2. Protocol

The IGT tester drives the link as the SAS **host**. Two classes of traffic appear:

### 2a. SAS general poll / link sync (`0x80` / `0x81`)

The host continuously emits a 1-byte general poll to the gaming machine (address 1).
The low nibble is the address; the alternating high bit is the SAS **link-sync / wake
("toggle") bit**, so successive polls alternate `0x80` / `0x81`. The EGM answers with
either an idle byte (`00`) or a queued exception code.

```text
qGMID1:81        SAS general poll to address 1 (toggle bit set)
qGMID1:80        SAS general poll to address 1 (toggle bit clear)
```
Source: `../protocol-raw-traffic.md` §1.

### 2b. `0x72` — AFT "transfer funds" long poll (the actual credit transfer)

A variable-length SAS long poll: `address | 0x72 | length | <data...> | CRC16`.
This is the message that actually carries the $1,000 promo transfer.
Source: `../protocol-raw-traffic.md` §2/§3; builder in `Invoke-WinDivertAft.ps1`.

### 2c. `0x72` interrogate (transfer status read-back)

Immediately after the transfer, the host sends a short `0x72` with transfer code
`0xFF` (interrogate most-recent transfer) to read the completion status:

```text
qGMID1:017202FF000F22     0x72 interrogate, transfer index FF = most recent
```
Source: `../protocol-raw-traffic.md` §1 / §3 notes.

### 2d. `0x73` — AFT registration long poll

The SAS AFT **registration** command. On this cabinet it is effectively unused: the
EGM reports `GAMING_MACHINE_NOT_REGISTERED` with an all-zero 20-byte registration key,
yet transfers still commit, and **no `0x73` was ever observed**.
Source: `AFT_Robustness_Findings.md` §A.4 and §A.5.

### Full byte-level decode of the `0x72` AFT transfer command

Decode of the captured `10:30:10` message
`017244000000000000000000000000000000100000000903000000000000000000000000000000000000000000001100657374205472616E73616374696F6E31053020200C00004B02`
(verbatim from `../protocol-raw-traffic.md` §2):

```text
01            address          gaming machine address 1
72            command          AFT transfer funds (0x72)
44            length           0x44 = 68 data bytes follow (before CRC)
--- data (68 bytes) ---
00            transfer code    0x00 = transfer in-house amount to gaming machine
00            transaction idx  0x00
00            transfer type    0x00 = cashable transfer type field
00 00 00 00 00   cashable amount        BCD 0000000000 = $0.00
00 00 00 00 00   restricted amount      BCD 0000000000 = $0.00
00 00 10 00 00   non-restricted amount  BCD 0000100000 = $1,000.00  (promo)
00            transfer flags   0x00
09 03 00 00   asset number     little-endian 0x00000309 = 777
00 x20        registration key 20 bytes, all zero (machine NOT registered)
11            txn id length    0x11 = 17 bytes
00 65 73 74 20 54 72 61 6E 73 61 63 74 69 6F 6E 31
              transaction id   ASCII ".est Transaction1"  (first byte 0x00; tester
                               labels it "Test Transaction1")
05 30 20 20   expiration       BCD (00 05 30 ... date/expiry field)
0C 00         pool id          0x000C
00            receipt data len 0x00 (no receipt data)
... lock/timeout + ...
4B 02         CRC-16           message checksum
```

### SAS `0x72` field reference table

(verbatim from `../protocol-raw-traffic.md` §3)

| Offset | Bytes | Field | Notes |
|-------:|------:|-------|-------|
| 0 | 1 | Address | EGM SAS address (1) |
| 1 | 1 | Command | `0x72` AFT transfer funds |
| 2 | 1 | Length | data byte count (excludes addr/cmd/length and CRC) |
| 3 | 1 | Transfer code | `0x00` to gaming machine, `0x80` cancel, `0xFF` interrogate |
| 4 | 1 | Transaction index | `0x00` for a new transfer |
| 5 | 1 | Transfer type | cashable / restricted / nonrestricted / win selector |
| 6 | 5 | Cashable amount | BCD, cents |
| 11 | 5 | Restricted amount | BCD, cents |
| 16 | 5 | Non-restricted amount | BCD, cents (promo path here) |
| 21 | 1 | Transfer flags | bit flags |
| 22 | 4 | Asset number | little-endian binary (777) |
| 26 | 20 | Registration key | zero when machine not registered |
| 46 | 1 | Transaction ID length | bytes |
| 47 | n | Transaction ID | ASCII text id from the tester |
| .. | 4 | Expiration | BCD date / days |
| .. | 2 | Pool ID | restricted pool |
| .. | 1 | Receipt data length | + receipt data |
| .. | 2 | Lock timeout | |
| end | 2 | CRC-16 | SAS Kermit CRC |

Notes (from same source):
- Amounts are **BCD in cents**. `0000100000` = 100000 cents = `$1,000.00`.
- `transfer code = 0xFF` is the **interrogate** the host sends right after, to read
  the transfer status / completion from the EGM.
- The captured transfers were **non-restricted (promo)**; cashable/restricted are 0.

---

## 3. Real packet dump (annotated)

### 3a. Captured exchange on the serial link (verbatim from the messenger log)

Each `qGMID1:` line is one SAS message as decoded off the serial link by the Aurum
SAS messenger (`GoldClub.Aurum.Services sasmsgr of SASControler1`).
Source: `../protocol-raw-traffic.md` §1.

```text
2026-06-15T13:14:12.161+01:00 INFO [:] qGMID1:81
2026-06-15T13:14:12.384+01:00 INFO [:] qGMID1:80
2026-06-15T13:14:12.609+01:00 INFO [:] qGMID1:017245000000000000000000000000000000100000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3232053020200C00000A45
2026-06-15T13:14:12.836+01:00 INFO [:] qGMID1:81
2026-06-15T13:14:13.036+01:00 INFO [:] qGMID1:80
2026-06-15T13:14:13.235+01:00 INFO [:] qGMID1:81
2026-06-15T13:14:13.442+01:00 INFO [:] qGMID1:017202FF000F22
2026-06-15T13:14:13.644+01:00 INFO [:] qGMID1:80
```

- `81` / `80` ............ SAS general poll to address 1 (toggle/wake bit flips)
- `0172 45 ...` .......... **AFT transfer funds** (`0x72`), the actual $1,000 transfer
- `0172 02 FF 00 0F22` ... AFT status interrogate (index `FF` = most recent)

### 3b. Field-by-field annotation of the `0x72` transfer packet

Using the fully-decoded `10:30:10` example
`017244...4B02` (the `13:14:12` packet is identical except `length=0x45`,
`txn id length=0x12` for `"est Transaction22"`, CRC `0A45`):

```text
01                       address          gaming machine address 1
72                       command          0x72 AFT transfer funds
44                       length           68 data bytes follow (before CRC)
00                       transfer code    in-house amount, host -> EGM
00                       transaction idx  new transfer
00                       transfer type    cashable type field
00 00 00 00 00           cashable         BCD = $0.00
00 00 00 00 00           restricted       BCD = $0.00
00 00 10 00 00           non-restricted   BCD 0000100000 = $1,000.00 (promo)
00                       transfer flags
09 03 00 00              asset number     LE 0x00000309 = 777
00 (x20)                 registration key all-zero (NOT registered)
11                       txn id length    17 bytes
00 65 73 74 20 54 72     transaction id   0x00 + ASCII "est Transaction1"
61 6E 73 61 63 74 69                       (reported as "Test Transaction1")
6F 6E 31
05 30 20 20              expiration       BCD date/expiry
0C 00                    pool id          0x000C
00                       receipt data len no receipt data
                         (lock/timeout fields)
4B 02                    CRC-16           SAS Kermit checksum, little-endian
```

### 3c. Builder-produced packet shape (`New-AftTransferPacket`)

`Invoke-WinDivertAft.ps1` reconstructs the same SAS `0x72` layout in `New-AftTransferPacket`:
`address | 0x72 | len | code=00 | idx=00 | type=00 | cashable BCD(0) | restricted BCD(0)
| nonRestricted BCD(amount) | flags=00 | asset LE (uint32) | 20 zero key bytes |
txnLen | (0x00 + "est Transaction<N>") | 05 30 20 20 | 0C 00 | 00 | CRC16(LE)`.
For Hop 2 the script additionally prepends the `0x1B` bridge framing byte
(`bridgePayload = 0x1B + sasPacket`) — that prefix belongs to the TCP bridge hop, not
the serial SAS link of Hop 1.
Source: `Invoke-WinDivertAft.ps1` (`New-AftTransferPacket`, `Get-SasCrc16`, and the `0x1B` bridge-prefix build step).

---

## 4. Where it is logged / captured

- **Authoritative serial-link evidence:** the Aurum SAS messenger decodes and logs each
  raw SAS message off the link as `qGMID1:<hex>` in
  `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\<date>.log`.
  This is where the real `0172 44/45 ...` transfer bytes are visible.
  Source: `../protocol-raw-traffic.md` §intro / §1.

- **Passive bridge captures only saw polling, not the transfer.** Five passive WinDivert
  windows (60/90/120/120/120 s) on the CommCtrlSAS loopback ports `31100`/`31150`
  (captured with `Invoke-AurumTrafficCapture.ps1`) showed **only** SAS general polls
  (`1B 80` / `1B 81`) and EGM idle replies (`00`). **No `0x72` (AFT transfer) and no
  `0x73` (registration) bytes were observed** on that bridge — on this cabinet the credit
  commits through the Aurum/WAT2AFT service path rather than a host-originated raw SAS
  `0x72` visible on the bridge.
  Source: **`AFT_Robustness_Findings.md` §A finding #5** (and §A.6: CommCtrlSAS exposes only `31100`/`31150`).

- **TCP-level view of the bridge** (Hop 2 context), from
  `\\10.0.0.90\c$\Windows\Temp\aurumtap\dump.txt`:

```text
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B80   <- SAS poll 0x80 over bridge
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B81   <- SAS poll 0x81 over bridge
TCP [SrcPort=56615 DstPort=31100 ...]   payload 00     <- EGM ack byte back over bridge
TCP [SrcPort=51300 DstPort=50011 ...]   "POST /SASControler1 HTTP/1.1 ..."  <- Aurum remoting
```
Source: `../protocol-raw-traffic.md` §4.

---

## 5. Decoupling difficulty: **HARD**

**Verdict: HARD** to replace / emulate the IGT tester at this hop.

Justification (what an emulator must reproduce to stand in for the IGT tester on COM11):

1. **Hardware-level serial SAS, not a socket write.** This hop is RS-232 over the IGT
   **MUX on COM11 @ 921600 baud** (confirmed in `CommControler.ini`). A substitute needs a
   real (or faithfully emulated) serial host endpoint on that port, not a TCP connection.
   The WinDivert approach (`Invoke-WinDivertAft.ps1`) deliberately targets the **TCP bridge
   (Hop 2)** with a `0x1B` prefix precisely *because* injecting on the raw serial link is not
   a simple write.
2. **Continuous real-time general polling.** SAS requires the host to keep polling
   (`0x80`/`0x81`) on a tight cadence (~200 ms intervals in the capture) and to honor the
   link-sync / wake (toggle) bit. An emulator must sustain this polling loop and maintain
   sync, not just emit one command. Source: `../protocol-raw-traffic.md` §1.
3. **Correct SAS long-poll framing + CRC.** Each `0x72` must carry valid length, BCD
   amounts, little-endian asset, optional 20-byte registration key, and a correct
   **CRC-16/KERMIT** (poly `0x8408`) trailer. Source: `Invoke-WinDivertAft.ps1` `Get-SasCrc16`.
4. **Interrogate / status state machine.** The host must follow the transfer with the
   `0x72`/`0xFF` interrogate and interpret the EGM's status reply — a stateful exchange,
   not fire-and-forget. Source: `../protocol-raw-traffic.md` §1/§3.
5. **The transfer never appeared on the easy (TCP) surface.** Passive bridge captures
   only saw polls/idle bytes; the real `0x72` lives on the serial link / messenger log
   (`AFT_Robustness_Findings.md` finding #5). So you cannot decouple this hop merely by
   observing or replaying TCP — you need a genuine serial SAS host emulator.

Because reproducing this hop requires a **serial SAS host emulator on COM11** with correct
RS-232/MUX timing, continuous general polling, link sync, valid SAS framing/CRC, and a
stateful transfer/interrogate exchange — far beyond a simple socket write — **Hop 1 is
HARD to decouple.**
