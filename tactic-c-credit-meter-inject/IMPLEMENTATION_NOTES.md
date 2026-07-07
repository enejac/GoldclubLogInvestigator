# Tactic C Implementation Notes

**Location:** `C:\Users\Ezbogar\GoldclubLogInvestigator\tactic-c-credit-meter-inject\`

## Current Status: Phase 1 - Meter-Write Format Discovery

**Started:** 2026-06-17 10:44 UTC

## Files Created

### Root ----------------------------------------------------------------------
- `README.md` — Complete implementation plan, architecture diagram, phase checklist
- `IMPLEMENTATION_NOTES.md` — This file (emergent notes during development)

### Scripts ----------------------------------------------------------------------
- `scripts/run-capture-for-meter-write.ps1` — Memory query script for discovering
  OneHand.exe RAM location. NOT a full WinDivert capture, but useful for
  understanding meter write mechanics.

### Parser ----------------------------------------------------------------------
- `parser/SasMeterParser.py` — WinDivert capture parser for SAS frames.
  Identifies SAS commands (0x72 AFT, 0x0F meter-write) and extracts payload data.

## What Was Discovered

### SAS Meter-Write Command Discovery

Based on analysis of existing code:

1. **Meter-write command exists in SAS protocol:**
   - From `decode_stage0.py`: `0x0F: "Send selected meters (single)"`
   - This command is used by OneHand to update game meters after WAT commit

2. **Meter code -> Value map (from `gui/view_model.py`):**
   ```python
   # SAS meter codes tracked by OneHand:
   "0001": ["Total Coin Out Credits / Win"]
   "0010": ["Credits"]
   "0017": ["TotalTransferToEGM"]  # AFT/WAT transfer bucket
   "001C": ["TotalMachinePaidPaytableWin"]
   "0020": ["Total bill meters (cancelled credits)"]

   # WAT bucket keys (for bonus/promo/cashable):
   "wat_cashableInAmt"
   "wat_nonCashInAmt"
   "wat_promoInAmt"
   ```

3. **Direct meter write approach:**
   - The meter is an internal memory value in OneHand.exe
   - Changes trigger SlotLog lines: `Aurum {bucket} credit state increased to <cents>`
   - Meter value is visible in SlotLog AFTER credit is applied
   - Based on test data (`aft/test-runs/*.json`), meter values ARE preserved

## What Still Needs Discovery

### CRITICAL: Meter Write Frame Format

**Status:** NOT DONE (Phase 1 incomplete)

The exact byte format for SAS 0x0F commands is NOT yet known:

**What we know:**
- Command code: `0x0F` (from decode_stage0.py)
- Direction: S2C (CommCtrlSAS → Aurum)
- Likely uses 0x1B framing (same as 0x72 AFT)
- Includes meter code + encoded value + CRC

**What we DON'T know:**
```xml
<scenario>
  Expected format:
    1. Direction byte (from WdSniff): 0x01 or 0x1B?
    2. SAS frame header: <cmd> (0x0F) <length>
    3. Meter code: <hex> (e.g., "0x001C" for TotalMachinePaidPaytableWin)
    4. Meter value: <encoded> (binary data, not BCD)
    5. CRC: CRC-16/KERMIT (poly 0x1021, reflected 0x8408)

  Unknown fields (need capture discovery):
    - Skip indicator bytes?
    - Meter value encoding (BCD vs binary)?
    - Order of fields in payload?
    - \[REQUIRED] exact byte layout

  Without this, cannot construct valid meter-write frame.
</scenario>
```

## Next Steps

### Priority 1: Complete Capture with Trigger (IMMEDIATE)

**Goal:** Capture SAS frames DURING AFT transfer to see what OneHand does

**Approach:**
```powershell
# 1. Run WinDivert capture while AFT transfer occurs
cd C:\Users\Ezbogar\GoldclubLogInvestigator
.\Invoke-AurumTrafficCapture.ps1 -IP 10.0.0.90 -DurationSec 10 -Ports 31100,31150
# Run this in parallel with manual AFT injection:
.\Send-TestAft1000.ps1 -Send -IP 10.0.0.90

# 2. Parse captures using new parser:
python C:\Users\Ezbogar\GoldclubLogInvestigator\tactic-c-credit-meter-inject\parser\SasMeterParser.py
C:\Users\Ezbogar\GoldclubLogInvestigator\aft\captures\stage0-90-steady-20260616-141116.txt

# 3. Extract 0x0F frames and document format:
#    SasMeterParser.py will output:
#      - Bridge prefix: 1B
#      - Command: 0x0F
#      - Length: X
#      - Meter code: XX
#      - Encoded value: XX
#      - CRC: XX
```

**Script you'll need to create:**
```powershell
# Still need: triggers for manual/captured AFT injection
# 1. Use existing Send-TestAft1000.ps1 for inject
# 2. Or create: Invoke-Capture-Then-Inject.ps1
```

### Priority 2: Build Meter-Write Template

**Based on captured 0x0F frames:**

```python
# meter-write-template.json (auto-generated)
{
  "bridge_prefix": 0x1B,
  "command": 0x0F,
  "length": Integer,
  "meter_code": Dictionary -> HexValue,
  "value_encoding": "BCD" | "binary",
  "crc": "CRC-16/KERMIT (poly 0x1021, reverse 0x8408)"
}
```

### Priority 3: Implement WdMeterInject.cs

**Framework from existing WdInject.cs:**

```csharp
// WdMeterInject.cs (core injector)
public static void ConstructMeterWriteFrame(
    int amountCents,
    string bucketType,  // "promo" | "cashable" | "restricted"
    out byte[] frame,
    out int crc
) {
    // 1. Map bucketType to meter code
    // 2. Create meter-write payload
    // 3. Build 0x1B + SAS 0x0F + payload + CRC
    // 4. Return frame + CRC
}

// Inject into existing flow
public static void InjectMeterWriteFrame(
    byte[] payload,
    long anchorSeq,
    int ephemPort  // Discovered live from capture
) {
    // Reuse WdInject anchor point logic
    // Modify payload bytes
    // Retransport via WinDivertSend
}
```

## Blockers (Current)

1. **No 0x0F frames in existing captures** — Existing captures only have alternating 80/81 polls and occasional 0x72 AFT frames
2. **Need capture during AFT transfer** — Meter writes happen inside handling code, not during AFT response
3. **OneHand not instrumented** — We haven't seen where OneHand actually writes the meters yet

## Risk Mitigation Path

**Fallback if Phase 1 fails:**
- Analyze OneHand.exe DLL for meter write references
- Use Process Monitor / Trace Monitor to see what DLLs OneHand loads during credit apply
- Examine C:\Users\Ezbogar\GoldclubLogInvestigator\gui\*.py for any OneHand COM interfaces

**Alternative bypass:**
- Engage OneHand directly via COM (if `GoldClub.Aurum.Services` exposes it)
- Use .NET remoting directly to GM2AU endpoint (go around Aurum by targeting game client)

## Resources Referenced

- `../Nazisoft Command Reference.md` — Original SAS command mapping
- `../aft/RUNBOOK.md` — WinDivert injection patterns (anchor point discovery)
- `../aft/investigations/alt-credit-paths.md` — Why .NET remoting failed
- `../aft/hops/hop4-onehand-to-slotlog.md` — Where meter bump occurs
- `../gui/view_model.py` lines 1880-1914 — SAS meter mappings
- `../network/health_monitor.py` lines 60-89 — WMIC remote access pattern

## Completed Tactic C Prerequisites

[✓] Architecture analysis complete
[✓] Mapped one HAND meter library structure
[✓] Identified existing VMIC remote access utilities
[✓] Created parser for SAS frame discovery
[✓] Created capture script framework
[✓] Established working directory structure

[ ] **METER-WRITE FORMAT DISCOVERY** (Phase 1 BLOCKER)
[ ] **Meter-write template** (Phase 2 DELIVERABLE)
[ ] **WdMeterInject.cs implementation** (Phase 3 BLOCKER)
[ ] **Meter injector script** (Phase 2 BLOCKER)
[ ] **Verification framework** (Phase 4 DELIVERABLE)

## Questions for the User

**Immediate (before investing more):**
1. Do we have access to run AFT transfer on .90 during capture?
2. Can we instrument OneHand.exe to log its meter write operations?
3. Is there a WAT manager API we can call for testing?
4. Priority: discovery first, then implementation?

**Trade-off:**
- **Time-efficient:** Instrument OneHand + run capture → discover format → inject
- **Rule-following:** Perfect capture of existing code → document template → build injector

**Recommendation:**
Start with Phase 1 capture WITHOUT code changes, because:
1. Instrumentation requires code changes that may not work
2. Existing code already has meter infrastructure in place
3. Capture can run in parallel with other activity
4. Template will be discovered objectively, not hypothesized

**However**, if capture overview shows 0x0F frames ARE present, move immediately to implementation.

---

*Last updated:* 2026-06-17 10:45 UTC
