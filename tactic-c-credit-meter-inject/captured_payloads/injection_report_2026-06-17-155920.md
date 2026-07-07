# Payload Injection Report - 10.0.0.90

**Date:** 2026-06-17 16:07:43
**Machine:** GST20664

## Summary

- **Tested:** 7 methods
- **Landed:** 7
- **Failed:** 0

> Corrected after Test-CreditSince scope bug fix. See injection_report_2026-06-17-FINAL.md.

## Results

| # | Method | Type | Amount | Txn | Status | SlotLog evidence |
|---|--------|------|--------|-----|--------|----------------|
| 1 | WinDivert promo $1000 | non-restricted | $1000 | 1260 | **LANDED** | Cashless In $1000 @ 16:00:02 |
| 2 | WinDivert cashable $1000 | cashable | $1000 | 1261 | **LANDED** | Cashless In $1000 @ 16:01:14 |
| 3 | WinDivert restricted $1000 | restricted | $1000 | 1262 | **LANDED** | Cashless In $1000 @ 16:02:25 |
| 4 | Capture aft-txn48 promo | non-restricted | $1000 | 1263 | **LANDED** | Cashless In $1000 @ 16:03:36 |
| 5 | Capture aft-txn50 cashable | cashable | $1000 | 1264 | **LANDED** | Cashless In $1000 @ 16:04:47 |
| 6 | WinDivert promo $500 | non-restricted | $500 | 1265 | **LANDED** | Cashless In $500 @ 16:06:00 |
| 7 | WinDivert cashable $500 | cashable | $500 | 1266 | **LANDED** | Cashless In $500 @ 16:07:10 |

## Notes

- Only SAS 0x72 AFT (cashless) paths are injectable via WinDivert.
- Handpay / Key-On Credit are game-internal (no SAS traffic).
- Coin / Bill / Voucher were not observed on this cabinet.