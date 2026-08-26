# PHASE 1 DISCOVERY - COMPLETE WORKFLOW

## STEP 1: BUILD WDSNIFF.EXE
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator
csc /platform:x64 /optimize+ /out:probes/WdSniff.exe probes/WdSniff.cs
```

## STEP 2: RUN CAPTURE
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator\tactic-c-credit-meter-inject

# Prepare capture directory
Window-File "capture"

# Run capture (10 seconds):
.\tactic-c-credit-meter-inject\WdSniff.exe 10000 31100,31150 > .\capture\01-begin.txt

# Wait for capture to complete (10 seconds)
Start-Sleep -Seconds 10
```

## STEP 3: TRIGGER AFT INJECT
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator

# Prepare admin credentials if needed:
cmdkey /generic:10.0.0.90 /user:GOLD-CLUB\test /pass:test

# Trigger AFT transfer:
.\lab\Send-TestAft1000.ps1 -Send -IP 10.0.0.90 -Amount 100000 -nr
```

## STEP 4: COMPLETE CAPTURE
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator\tactic-c-credit-meter-inject

# Stop capture:
.\tactic-c-credit-meter-inject\WdSniff.exe 0 > .\capture\02-end.txt

# Verify files:
Get-ChildItem .\capture\
Measure-Object -InputObject (Get-Content ".\capture\01-begin.txt") -Line
```

## STEP 5: MERGE CAPTURE FILES
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator\tactic-c-credit-meter-inject\capture

# Merge begin + end for full timeline:
Get-Content 01-begin.txt, 02-end.txt | Out-File capture-full.txt

# Show samples:
Select-String -Path capture-full.txt -Pattern "^Packet"
```

## STEP 6: PARSE CAPTURE
```powershell
cd C:\Users\Ezbogar\GoldclubLogInvestigator

# Use targeted WdSniff.exe output from parent directory:
python tactic-c-credit-meter-inject/parser/SasMeterParser.py \
  .\aft\captures\stage0-90-steady-20260616-141116.txt

# Or if WdSniff compiled:
python tactic-c-credit-meter-inject/parser/SasMeterParser.py \
  .\tactic-c-credit-meter-inject\capture\capture-full.txt
```

## STEP 7: ANALYSIS RESULTS

### If SAS 0x0F Found:
```
✓ Discover bridge prefix
✓ Identify meter code
✓ Determine value encoding
✓ Extract CRC algorithm
✓ Build meter-write template
```

### If SAS 0x0F Accepts:
```
![Phase 1 Discovery Results](files/phase1-results.jpg)
```