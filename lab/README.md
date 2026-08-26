# Lab scripts

PowerShell helpers for owned GoldClub lab cabinets (SMB/WinRM via `Initialize-LabAccess.ps1`).

| Area | Path |
|------|------|
| Slot AFT inject | `Invoke-WinDivertAft.ps1`, `Send-TestAft1000.ps1` — see [`../aft/README.md`](../aft/README.md) |
| Roulette Dallas / AFT | [`roulette/README.md`](roulette/README.md) |
| Bridge wake | `Invoke-WakeSasBridge.ps1` |
| History / verify | `Convert-AftHistory.ps1`, `Invoke-AftTransferTest.ps1` |

Do not use these against production cabinets.
