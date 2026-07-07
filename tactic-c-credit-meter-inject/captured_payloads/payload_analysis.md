# SAS Payload Analysis - Captured Payment Data

Cabinet `10.0.0.90` (`GST20664`), captured 2026-06-17 from `qGMID1:` sasmsgr lines.

## Injectable method: Cashless In (SAS 0x72 AFT)

Only cashless AFT transfers produce SAS traffic suitable for WinDivert injection.

### Frame layout (74 bytes total)

| Offset | Field | Notes |
|-------:|-------|-------|
| 0 | Address | `0x01` |
| 1 | Command | `0x72` AFT transfer funds |
| 2 | Length | Data byte count (typically `0x45` = 69) |
| 3 | Transfer code | `0x00` = in-house to EGM |
| 4 | Transaction index | `0x00` new transfer |
| 5 | Transfer type | `0x00` in captures (routing via BCD field) |
| 6-10 | Cashable amount | BCD cents, 5 bytes |
| 11-15 | Restricted amount | BCD cents |
| 16-20 | Non-restricted amount | BCD cents (promo path) |
| 21 | Transfer flags | `0x00` |
| 22-25 | Asset number | LE uint32 (`777` = `09 03 00 00`) |
| 26-45 | Registration key | 20 zero bytes (unregistered) |
| 46 | Txn ID length | Includes leading `0x00` + ASCII |
| 47+ | Transaction ID | e.g. `est Transaction48` |
| .. | Expiration | `05 30 20 20` |
| .. | Pool ID | `0C 00` |
| .. | Receipt len | `00` |
| end | CRC-16/KERMIT | Little-endian on wire |

### CRC

**CRC-16/KERMIT** (poly `0x8408`, init `0`) over addr+cmd+len+data. Same as `Get-SasCrc16` in `Invoke-WinDivertAft.ps1`. Verified against all 10 captured templates.

### Captured templates (`captured_templates.json`)

| ID | Type | Amount | Txn |
|----|------|--------|-----|
| aft-txn48 | non-restricted | $1,000 | 48 |
| aft-txn49 | non-restricted | $1,000 | 49 |
| aft-txn50 | cashable | $10,000 | 50 |
| aft-txn51-55 | cashable | $10,000 | 51-55 |
| aft-txn56 | cashable | $1,000,000 | 56 |
| aft-txn57 | non-restricted | $1,000 | 57 |

### Non-injectable methods (no SAS traffic)

- **Handpay** — game-internal (`OneHand.GameControl`)
- **Key-On Credit** — menu credit management, no SAS
- **Coin/Bill/Voucher** — not observed on this cabinet today

## Builder / injection scripts

| File | Purpose |
|------|---------|
| `Build-CapturedAftPayload.ps1` | Parse captures, build 0x72 frames, CRC, 0x1B bridge prefix |
| `captured_templates.json` | 10 verified live captures |
| `Invoke-ReplayCapturedAft.ps1` | Build from template + inject via `Invoke-WinDivertAft.ps1` |
| `monitor.ps1` | Tail SlotLog for credit events |

### Usage

```powershell
# Dry-run: rebuild txn48 capture exactly
.\Invoke-ReplayCapturedAft.ps1 -TemplateId aft-txn48 -DryRun

# Build $500 cashable from cashable template baseline
.\Invoke-ReplayCapturedAft.ps1 -TemplateId aft-txn50 -AmountCents 50000 -TransferType cashable -TransactionNumber 99 -DryRun

# Live inject (requires SAS host + WinDivert)
.\Invoke-ReplayCapturedAft.ps1 -TemplateId aft-txn48 -Send
```