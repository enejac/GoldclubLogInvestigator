# SAS address vs IGT tester - lab checklist (roulette / fresh EGM)

**Validated 2026-07-17** on `10.0.0.90` (roulette `GRT330106` / `GCC_RT_330106_01`).

## Symptom (looks like MUX / cable failure - it is not)

| What you see | Meaning |
|---|---|
| IGT SAS Test Program: **TX only**, no RX lines | Host polls; EGM does not answer that address |
| MUX LEDs: **red blinks**, green stale while IGT open | Host TX on the wire; no matching slave reply |
| IGT closed: green blinks again after ~1-2 s | Idle **chirp** from EGM (its real SAS address) |
| Cables / MUX / COM USB adapter "known good" | Still fails if **protocol address** mismatches |

Do **not** chase COM ports, baud, or registration first for this pattern.

## Two different numbers (easy to confuse)

| Setting | Where | What it is | Lab default |
|---|---|---|---|
| **Port** | PC `C:\SAS test\sastest.ini` -> `Port = N` | Windows **COM** port of the USB-RS232 cable | **4** (COM4) - keep this |
| **Address** | Same ini -> `Address = N` | SAS **gaming-machine address** (1-127), first byte of polls | Lab IGT = **1** |
| **SASAddress** | EGM `ClientsSet.xml` | Address Aurum / sasmsgr answers as | **Must match IGT Address** |

`Address = 11` is **not** COM11. COM11 on older slot docs is the **cabinet** CommCtrlSAS serial side @ 921600 - unrelated to the PC USB COM port.

### Poll bytes (sanity)

| SAS address | General polls (toggle) | Idle chirp / ACK nibble in sasmsgr |
|---|---|---|
| **1** | `0x80` / `0x81` | `rGMID1:01` |
| **11** | `0x8A` / `0x8B` | `rGMID1:0B` |

## Root cause on .90 (2026-07-17)

Fresh / image config had:

```xml
<!-- C:\goldclub\services\aurum\config\SASControler1\ClientsSet.xml -->
<SASAddress>11</SASAddress>
```

while lab IGT kept:

```ini
Port = 4
Address = 1
```

Evidence:

- sasmsgr idle: continuous `rGMID1:0B` (address 11)
- With IGT on addr 1: `qGMID1:80` / `81` logged, **COM4 RX = 0**
- Polling addr 11 on COM4: **RX present**
- After setting EGM `<SASAddress>1</SASAddress>` + Aurum restart: `rGMID1:01`, COM4 RX on addr-1 polls

**Fix applied on EGM** (preferred for this lab - keep IGT at Address 1):

1. Backup `ClientsSet.xml`
2. Set `<SASAddress>1</SASAddress>`
3. `Restart-Service GoldClub.Aurum.Services`
4. Confirm sasmsgr shows `rGMID1:01` (not `0B`)
5. Reopen IGT with `Port=4`, `Address=1` -> TX **and** RX

## Fresh EGM build - do this every time

After image / Aurum / roulette drop, **before** blaming MUX:

1. **Read** `C:\goldclub\services\aurum\config\SASControler1\ClientsSet.xml`
   -> `<SASAddress>` must be **`1`** for lab IGT default.

2. **Also check** (identity only - do not copy from another cabinet):
   - `<AurumEgmId>` matches this machine
   - `<WakeUpPort>` / `<Port>` match this cabinet CommCtrlSAS bridge
     (roulette lab often **30500 / 30550**; older slot docs use **31100 / 31150**)

3. **If SASAddress != 1:** set to `1`, backup the file, restart `GoldClub.Aurum.Services`.

4. **Verify without IGT UI** (optional, from lab PC on COM4 @ 19200):
   - Poll `0x80`/`0x81` -> expect RX
   - Or read sasmsgr: idle chirp `rGMID1:01`

5. **Then** open IGT: `Port` = USB COM (e.g. 4), `Address` = **1**.

### Paths (roulette .90)

| Item | Path |
|---|---|
| Aurum SAS clients | `C:\goldclub\services\aurum\config\SASControler1\ClientsSet.xml` |
| SAS setup (poll rate, AFT flags) | `...\SASControler1\SASsetupData.xml` |
| sasmsgr log | `C:\goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\YYYY-MM-DD.log` |
| CommCtrlSAS (cabinet serial / MUX) | `C:\goldclub\services\CommCtrlSAS\` + service **GoldClub Serial Communication Gateway SAS** |
| Lab IGT ini (PC) | `C:\SAS test\sastest.ini` - change **Address** only if you deliberately keep EGM != 1 |

## What this is *not*

- Registration (`GAMING_MACHINE_NOT_REGISTERED`) - does **not** block general-poll RX
- `AnswerOnlyWhenGamesAreMapped` - secondary; wrong address alone gives TX-only
- Changing PC `Port` to 11 - wrong layer; breaks the USB serial adapter

## Related docs

- Roulette AFT / Dallas: [`roulette/README.md`](roulette/README.md)
- Slot hop map (COM11 = **cabinet** serial, address 1 assumed): [`../aft/hops/hop1-igt-tester-to-commctrlsas.md`](../aft/hops/hop1-igt-tester-to-commctrlsas.md)
- Config parity note (slot .90 vs .171 expected `SASAddress 1`): [`../aft/investigations/config-parity.md`](../aft/investigations/config-parity.md)
