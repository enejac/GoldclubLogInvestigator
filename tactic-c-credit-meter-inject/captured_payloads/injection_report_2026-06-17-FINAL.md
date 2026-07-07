# Payload Injection Report - 10.0.0.90 (GST20664)

**Date:** 2026-06-17 16:00-16:07 (cabinet local +01:00)
**Cabinet:** 10.0.0.90 / GST20664
**Path:** WinDivert Hop 2 (CommCtrlSAS:31150 -> Aurum TCP)

## Summary

- **Tested:** 7 methods
- **Landed:** 7 / 7
- **SAS ingest:** 7 / 7
- **Inject errors:** 0

Note: injection_report_2026-06-17-155920.md showed 0/7 due to Test-CreditSince scope bug (fixed). WinDivert credit polling and SlotLog tail confirm all seven landed.

## Results

| # | Method | Type | Amount | Txn | Status | SlotLog evidence |
|---|--------|------|--------|-----|--------|------------------|
| 1 | WinDivert promo $1000 | non-restricted | $1000 | 1260 | **LANDED** | Cashless In $1000 / promo 100000 @ 16:00:02 |
| 2 | WinDivert cashable $1000 | cashable | $1000 | 1261 | **LANDED** | Cashless In $1000 / cashable 100000 @ 16:01:14 |
| 3 | WinDivert restricted $1000 | restricted | $1000 | 1262 | **LANDED** | Cashless In $1000 / nonCashPool 100000 @ 16:02:25 |
| 4 | Capture aft-txn48 promo | non-restricted | $1000 | 1263 | **LANDED** | Cashless In $1000 / promo 200000 @ 16:03:36 |
| 5 | Capture aft-txn50 cashable | cashable | $1000 | 1264 | **LANDED** | Cashless In $1000 / cashable 200000 @ 16:04:47 |
| 6 | WinDivert promo $500 | non-restricted | $500 | 1265 | **LANDED** | Cashless In $500 / promo 250000 @ 16:06:00 |
| 7 | WinDivert cashable $500 | cashable | $500 | 1266 | **LANDED** | Cashless In $500 / cashable 250000 @ 16:07:10 |

## Non-viable (not tested)

- Handpay, Key-On Credit: game-internal, no SAS 0x72
- Coin / Bill / Voucher: not observed on this cabinet

## Conclusion

All seven SAS 0x72 AFT cashless payloads landed via WinDivert Hop 2. Promo, cashable, and restricted routing all work. Capture templates aft-txn48 and aft-txn50 match fresh builds.