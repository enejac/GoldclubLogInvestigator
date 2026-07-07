# Tactic C: OneHand RAM/Direct Meter Injection

## Overview

Experimental approach to inject credit directly into OneHand game meters by targeting the SAS 0x0F meter-write command at Hop 2, bypassing the entire Aurum/WAT commit phase.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      HOP 1 (IGT Tester)                      │
│              SAS Serial Poll (COM11 / MUX)                     │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                     HOP 2 (Bridge)                            │
│             TCP 31150 ↔ CommCtrlSAS.exe                       │
│                                                               │
│  Current Tactic: WinDivert AFT 0x72                          │
│  NEW Tactic: WinDivert 0x0F (Meter Write)                     │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│              HOP 3 (Aurum WAT2AFT) [SKIPPED]                  │
│         requestTransfer → authorizeTransfer → commit        │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                HOP 4 (OneHand/EOS)                            │
│   Aurum promo credit state increased → METERS UPDATED!       │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                  HOP 5 (SlotLog)                              │
│             Cashless In: $X  (automatic)                      │
└─────────────────────────────────────────────────────────────┘
```

## Why This Works

1. **Skip the commit phase** - OneHand applies the meter value directly
2. **No NullReferenceException** - Bypass module completely
3. **Faster** - No WAT transaction processing (~100-200ms vs ~230ms)
4. **Cleaner** - Single hop injection, no AFT XML dependency

## Implementation Phases

### Phase 1: Capture (In Progress)
- [ ] Parse WinDivert captures for SAS 0x0F meter-write commands
- [ ] Reverse-engineer exact byte format (0x1B + SAS 0x0F + meterData + CRC)
- [ ] Document template for each bucket (promo/cashable/restricted)

### Phase 2: Code
- [ ] Implement `WdMeterInject.cs` (extend `WdInject.cs` patterns)
- [ ] Add anchor point discovery for meter-write sequence
- [ ] Implement meter-data encoding functions

### Phase 3: Scripts
- [ ] Create `Invoke-MeterAftTest.ps1` (orchestrator)
- [ ] Add `Invoke-WinDivertMeter.ps1` (meter injection)
- [ ] Add credential management (GOLD-CLUB\test)

### Phase 4: Verification
- [ ] Capture OneHand credit-state log lines
- [ ] Verify meter values in RAM snapshot
- [ ] Compare to base64 normalized merchie values
- [ ] Success criteria: `credit state increased` + verified meter

## Files

| Category | File | Purpose |
|----------|------|---------|
| Implementation | `WdMeterInject.cs` | WinDivert meter-write injector |
| Scripts | `Invoke-MeterAftTest.ps1` | Orchestrator (similar to Send-TestAft1000.ps1) |
| Scripts | `Invoke-WinDivertMeter.ps1` | Meter-frame builder + driver orchestration |
| Scripts | `Verify-MeterPing.ps1` | Verify meter update landed |
| Parser | `SasMeterParser.py` | Parse captures for meter-write discovery |
| Test | `test_meter_inject.py` | Unit tests for meter-frame construction |
| Config | `meter-write-template.json` | Reverse-engineered SAS format template |
| Docs | `IMPLEMENTATION_NOTES.md` | Development notes and lessons learned |

## Quick Start

```powershell
# Phase 1: Capture meter-write format
cd tactic-c-credit-meter-inject
.\scripts\run-capture-for-meter-write.ps1 -IP 10.0.0.90 -Duration 30

# Phase 2: Inject into memory (after template discovered)
.\scripts\Invoke-MeterAftTest.ps1 -Send -IP 10.0.0.90 -Amount 1000000 -nr
```

## Success Criteria

- `['] sasmsgr INGESTED the injected message:` (SAS 0x0F meter frame)
- `Cashless In: $X` in SlotLog
- `Aurum promo credit state increased to <cents>` in SlotLog
- RAM snapshot confirms numeric meter value updated
- No `transferException=0` failure in AFT XML

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Meter command format unknown | High | Block progress | Extensive capture analysis |
| OneHand evokes meters | Medium | Value not visible | Inject during active session |
| Non-idempotent (double-credit) | Low | Invalid state | Use transaction IDs + locks |

## References

- `../aft/RUNBOOK.md` — Proven WinDivert AFT injection (hop 2 anchor pattern)
- `../aft/hops/hop4-onehand-to-slotlog.md` — Meter bump location
- `../gui/view_model.py` — SAS meter mappings and techniques
- `../network/health_monitor.py` — WMIC remote access pattern
