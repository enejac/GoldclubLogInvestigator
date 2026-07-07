# Payload Injection Report - 10.0.0.90

**Date:** 2026-06-17 15:57:25
**Machine:** GST20664

## Summary

- **Tested:** 7 methods
- **Landed:** 0
- **Failed:** 7

## Results

| # | Method | Type | Amount | Txn | Status | SlotLog evidence |
|---|--------|------|--------|-----|--------|----------------|
| 1 | WinDivert promo $1000 | non-restricted | $1000 | 1256 | **NO_LAND** | - |
| 2 | WinDivert cashable $1000 | cashable | $1000 | 1257 | **NO_LAND** | - |
| 3 | WinDivert restricted $1000 | restricted | $1000 | 1258 | **NO_LAND** | - |
| 4 | Capture aft-txn48 promo | non-restricted | $1000 | 1259 | **NO_LAND** | - |
| 5 | Capture aft-txn50 cashable | cashable | $1000 | 1260 | **NO_LAND** | - |
| 6 | WinDivert promo $500 | non-restricted | $500 | 1261 | **NO_LAND** | - |
| 7 | WinDivert cashable $500 | cashable | $500 | 1262 | **NO_LAND** | - |

## Notes

- Only SAS 0x72 AFT (cashless) paths are injectable via WinDivert.
- Handpay / Key-On Credit are game-internal (no SAS traffic).
- Coin / Bill / Voucher were not observed on this cabinet.

## Failed methods
- **WinDivert promo $1000**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 100000, -TransactionNumber, 1256, -nr. Run with -Help for usage.
- **WinDivert cashable $1000**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 100000, -TransactionNumber, 1257, -c. Run with -Help for usage.
- **WinDivert restricted $1000**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 100000, -TransactionNumber, 1258, -r. Run with -Help for usage.
- **Capture aft-txn48 promo**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 100000, -TransactionNumber, 1259, -nr. Run with -Help for usage.
- **Capture aft-txn50 cashable**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 100000, -TransactionNumber, 1260, -c. Run with -Help for usage.
- **WinDivert promo $500**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 50000, -TransactionNumber, 1261, -nr. Run with -Help for usage.
- **WinDivert cashable $500**: exit=0 err=Unexpected argument(s): -Send, -ComputerName, 10.0.0.90, -AssetNumber, -Amount, 50000, -TransactionNumber, 1262, -c. Run with -Help for usage.