# Bill acceptor on roulette `.90` — KeyCtrl `:30300`

Captured 2026-07-27 via `DallasSpliceRoulette.exe` capture mode on
`CommCtrl:30300` ↔ `ruleta.exe` (read-only WinDivert tap).

**Status:** physical insert **proven** (×2) and synthetic inject **proven** (`captured`
profile, **200,000 credits** @ 14:01:43). HTML flow report:
[`../../aft/diagrams/igt-ruleta-bill-flow-report.html`](../../aft/diagrams/igt-ruleta-bill-flow-report.html).

## Path

```
Physical bill validator (COM3)
  → CommCtrl Serial Gateway
  → loopback TCP :30300
  → ruleta.exe (KeyCtrl / KeybdEmul)
  → credit + SAS bill meters (000B / LP $31–$37)
```

TITO is **COM4 → :30400** (separate bus). AFT is **SAS :30550** (cashless, not bills).

## Idle heartbeat

| Dir | ASCII | Notes |
|-----|-------|-------|
| S2C | `899\r\n` | CommCtrl poll |
| C2S | `AL\r\n` | ruleta alive |
| C2S | `BZ 0\r\n` | bill-zone poll |
| S2C | `888\r\n` | zone 0 ack |
| C2S | `BZ 1\r\n` | bill-zone poll |
| S2C | `999\r\n` | zone 1 ack |

## Bill accept sequence (observed during live insert)

Typical burst (timestamps from `bill_sniff_20260727_100634`):

1. **S2C** `821 68\r\n` — bill event / escrow (denom code TBD)
2. **C2S** `SF 7\r\nSF 3\r\n` — status flags
3. **C2S** `BL 0\r\nCN 0\r\nCN 0\r\n` — bill line / credit notify (stacker path)
4. **S2C** `807\r\n`, `803\r\n`, `701\r\n` (×2), `666\r\n` — accept choreography

After zone toggle:

5. **C2S** `BZ 1\r\n` → **S2C** `999\r\n`
6. **S2C** `821 27\r\n`
7. **C2S** `BL 1\r\nCN 1\r\n` — **bill credited** (strong signal)
8. **S2C** `700\r\n`, `777\r\n`

Later ($10-looking):

9. **S2C** `821 19\r\n`, `822 98\r\n`
10. **C2S** `BL 10\r\n` — face value hint ($10?)

Periodic credit notify (may be unrelated handpay context):

- **C2S** `CN 4\r\nHP 14\r\n` → **S2C** `701\r\n`

## Protocol decode (manual, from `bill_sniff_20260727_100634`)

After `BZ 1` + S2C `999`, the bill validator speaks **S2C ASCII lines** (`\r\n`).

| Path | When | S2C after `999` | ruleta C2S ack | Credits |
|------|------|-----------------|----------------|---------|
| **Simple** | Physical 12:07 | `821 27` only | `BL 1` / `CN 1` then S2C `700`/`777` | 200k |
| **Burst** | Physical 12:33 + proven inject | see formula below | `BL 10` | 200k |

**Do not use `credit` profile** (`821 XX||PHASE||700|||777`) — phase2 lines are **validator→game** on a real insert, not something we inject. That profile waits for `BL 1`/`CN 1` that never comes when locked or wrong framing.

### Burst formula (proven for code 98 / 200k)

```
821 18
821 19
822 {config_code}
823
821 {config_code - 81}
```

Example 2,000 COP (config **98**): `821 18|||821 19|||822 98|||823|||821 17` → **37 bytes** after `999`, single inject phase, then drain.

Derived 5,000 COP (config **99**):

```
821 18|||821 19|||822 99|||823|||821 18
```

Alternate end (Dallas simple code `config-71`): `…|||821 28` — try `-PayloadProfile captured5k-alt` if primary fails.

Simple-path Dallas code (physical 12:07 only): `821 (config_code - 71)` → 200k=`821 27`, 500k=`821 28`.

## Inject (proven / catalog)

Replication uses WinDivert on the live KeyCtrl session:

| Game | Port | Process | Splice mode |
|------|------|---------|-------------|
| Roulette | `:30300` | `ruleta` | `billinject` (wait `BZ 1` + `999`) |
| Slot | `:30800` | `OneHand` | `inject` (force burst; Slot has no BZ zone poll) |

`Invoke-BillInjectRouletteRemote.ps1` auto-detects which is running (`-GameKind Auto|Slot|Roulette`).

**List active denominations** (from `setup.plain.stations2.xml`, matches .90 BiOS bill table):

```powershell
.\lab\roulette\Invoke-BillInjectRouletteRemote.ps1 -ListDenominations
```

| COP note | Credits | Config code | Dallas after `999` |
|----------|---------|-------------|-------------------|
| 1,000 | 100,000 | 97 | `821 26` |
| 2,000 | **200,000** | 98 | **`821 27`** |
| 5,000 | **500,000** | 99 | **`821 28`** |
| 10,000 | 1,000,000 | 100 | `821 29` |
| … | … | … | `821 (code − 71)` |

**400,000 is not a valid note** — there is no 4,000 COP denomination. The next step above 200,000 is **500,000** (5,000 COP).

**Proven inject (200,000):**

```powershell
.\lab\roulette\Invoke-BillInjectRouletteRemote.ps1 -PayloadProfile captured -Credits 200000 -Send
```

Payload after `BZ 1` + `999`: `821 18|||821 19|||822 98|||823|||821 17`

**500,000 (5,000 COP, code 99) — inject-proven 2026-07-27:**

```powershell
.\lab\roulette\Invoke-BillInjectRouletteRemote.ps1 -Credits 500000 -ClearLockFirst -Send
```

Uses decoded burst `821 18|19 + 822 99 + 823 + 821 18` (37 B). If cabinet still locked, run `.\Invoke-ClearRouletteServiceLock.ps1` first.

Alternate end code: `-PayloadProfile captured5k-alt` → last line `821 28`.

Legacy one-shot (not recommended): `Invoke-BillInjectRouletteRemote.ps1 -BillCode 27 -PayloadProfile credit -Send`

## Capture artifacts

- `aft/captures/bill_sniff_20260727_100634/dallas_capture.log`
- `aft/captures/bill_sniff_20260727_095832/stage0-90-steady-*.txt` (WdSniff)
