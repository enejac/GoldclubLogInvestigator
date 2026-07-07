# IGT SAS tester -> Aurum : raw AFT transfer traffic (lab cabinet)

Example of the **raw traffic an IGT SAS tester sends to Aurum** when it performs an
AFT "transfer funds" (the $1,000 promo bonus used on the lab cabinet).

- Cabinet: `10.0.0.90` (`GST20664`), EGM `GCC_ST_20664_01`, asset `777`
- Captured from the live Aurum SAS messenger log:
  `\\10.0.0.90\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\<date>.log`
- The `qGMID1:<hex>` lines are the **literal bytes on the SAS serial link** as the
  messenger receives/decodes them. Each `qGMID1:` line is one SAS message.

## Path of the bytes

```
IGT SAS tester (SAS host, general poll + 0x72 command)
   -> RS-232 SAS link
   -> CommCtrlSAS.exe  (serial <-> TCP bridge, loopback ports 31100 / 31150)
   -> GoldClub.Aurum.Services (sasmsgr of SASControler1)  <-- bytes logged here as qGMID1:
   -> Aurum WAT2AFT -> writes AFT state XML -> OneHand credits the EGM
```

The same transfer, once committed by Aurum, is persisted as XML at:
`\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1\GCC_ST_20664_01_aftMostRecentTransaction_v2.xml`

---

## 1. Raw captured exchange (verbatim from the messenger log)

A real transfer-funds exchange. `80`/`81` are the alternating SAS **general polls**
(address 1, with the link-sync/wake bit), then the host sends the **0x72 AFT
transfer** message, then it polls the machine's AFT status with another 0x72.

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

- `81` / `80` ............... SAS general poll to gaming machine address 1 (wake bit toggles 0x80/0x81)
- `0172 45 ...` ............. **AFT transfer funds** command (0x72), this is the actual transfer
- `0172 02 FF 00 0F22` ...... AFT status interrogate (transfer index `FF` = most recent), host reading result

### Earlier full-byte example (transferType cashable = 0, transfer code = 0)

```text
2026-06-15T10:30:10.489+01:00 INFO [:] qGMID1:017244000000000000000000000000000000100000000903000000000000000000000000000000000000000000001100657374205472616E73616374696F6E31053020200C00004B02
2026-06-15T10:30:11.323+01:00 INFO [:] qGMID1:017202FF000F22
```

---

## 2. Byte-level decode of the 0x72 AFT transfer command

Decoding the `10:30:10` message
`017244000000000000000000000000000000100000000903000000000000000000000000000000000000000000001100657374205472616E73616374696F6E31053020200C00004B02`:

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

The `13:14:12` message is the same layout with `length=0x45`, txn id length `0x12`
(`"est Transaction22"`), and the same `$1,000.00` non-restricted amount and asset `777`.

---

## 3. SAS 0x72 (AFT transfer funds) field reference

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

Notes:
- Amounts are **BCD in cents**. `0000100000` = 100000 cents = `$1,000.00`.
- `transfer code = 0xFF` (`0172 02 FF 00 ....`) is the **interrogate** the host sends
  right after, to read the transfer status / completion from the EGM.
- The captured transfers were **non-restricted (promo)**; cashable/restricted columns are 0.

---

## 4. TCP-level view (WinDivert capture of the bridge)

The SAS link is bridged over loopback TCP by CommCtrlSAS. A WinDivert capture
(`\\10.0.0.90\c$\Windows\Temp\aurumtap\dump.txt`) shows the same poll bytes on the
bridge sockets, and the Aurum remoting POST on `:50011`:

```text
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B80   <- SAS poll 0x80 over bridge
TCP [SrcPort=31150 DstPort=56616 ...]   payload 1B81   <- SAS poll 0x81 over bridge
TCP [SrcPort=56615 DstPort=31100 ...]   payload 00     <- EGM ack byte back over bridge
TCP [SrcPort=51300 DstPort=50011 ...]   "POST /SASControler1 HTTP/1.1 ..."  <- Aurum .NET remoting
```

- `31100` / `31150` = CommCtrlSAS serial<->TCP bridge endpoints (the `1B` prefix is the
  bridge's framing byte; `80`/`81` are the SAS poll bytes).
- `50011` = Aurum `SASControler1` secure .NET remoting endpoint (`GST20664:50011`).

So the byte that matters for "tester sends an AFT transfer to Aurum" is the
`0172 44/45 ...` message in section 1; everything else is polling/transport framing.
