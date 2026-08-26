<#
.SYNOPSIS
   Capture trigger for Tactic C Phase 1: Run WinDivert capture simultaneously with
   AFT transfer injection to discover SAS 0x0F meter-write command format.

.DESCRIPTION
   This script runs a minimal WinDivert capture on Hop 2 (CommCtrlSAS bridge) while
   triggering a WinDivert AFT transfer. This is CRITICAL Phase 1 discovery:

   - Capture AFT 0x72 injection (existing WinDivert infrastructure)
   - Capture ANY 0x0F (Send selected meters) commands emitted by OneHand
   - Infer meter-write payload format from captured frames
   - Build template for direct meter-injection (Tactic C)

.PARAMETER ComputerName
   Cabinet IP. Default 10.0.0.90.

.PARAMETER DurationSec
   Capture window plus injection acceptance time. Default 5s + 10s = 15s.

.PARAMETER AftAmount
   Amount to inject (float, not cents, i.e., 10.00 = $10.00). Default 10.00.

.PARAMETER AftType
   AFT bucket type: "promo" (non-restricted), "cashable", or "restricted". Default "promo".

.PARAMETER OutDir
   Capture output directory.

.EXAMPLE
   .\Invoke-Capture-Then-Inject.ps1 -DurationSec 20

.EXAMPLE
   .\Invoke-Capture-Then-Inject.ps1 -IP 10.0.0.110 -AftAmount 1000 -AftType "cashable"
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int]    $DurationSec = 15,
    [double] $AftAmount = 10.00,
    [string] $AftType = 'promo',
    [string] $OutDir = Join-Path $PSScriptRoot '..\log\capture'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------
# Phase 1 Discovery Flow
# ------------------------------------------------------------------

$scriptPath = $PSScriptRoot
$repoRoot = (Get-Item $scriptPath).Parent.Parent.FullName

Write-Host ''
Write-Host '============================================================='
Write-Host 'Tactic C Phase 1: Capture-Then-Inject for Meter-Write Discovery'
Write-Host '============================================================='
Write-Host "ComputerName : $ComputerName"
Write-Host "Duration     : ${DurationSec}s"
Write-Host "AftAmount    : \$$AftAmount ($([int]($AftAmount * 100)) cents)"
Write-Host "AftType      : $AftType"
Write-Host "ScriptRoot   : $scriptPath"
Write-Host "RepoRoot     : $repoRoot"
Write-Host ""

# ------------------------------------------------------------------
# Generate capture filename
# ------------------------------------------------------------------

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$createCapture = Join-Path $scriptPath 'Create-Capture-Script.ps1'
$runCapture = Join-Path $scriptPath 'Run-Capture-Script.ps1'
$injectScript = Join-Path $repoRoot 'lab\Send-TestAft1000.ps1'
$parseScript = Join-Path $repoRoot 'tactic-c-credit-meter-inject\parser\SasMeterParser.py'

# ------------------------------------------------------------------
# Create capture script (inline)
# ------------------------------------------------------------------

Write-Host "[*] Creating minimal windivert capture command..." -ForegroundColor Cyan

$createCaptureContent = @'
<#
.SYNOPSIS
   Minimal WinDivert capture for meter-write discovery.
   Uses WdSniff.exe pre-compiled from parent repository.
#>
param([string]$IP,$[int]$Duration,$[string]$CaptureFile)

$ErrorActionPreference = "Stop"

$wsroot = (Split-Path (Split-Path $PSScriptRoot))  # parent of scripts is tactics-c
$wdDir = Join-Path $wsroot "tactic-c-credit-meter-inject"
$wdExe = Join-Path $wdDir "WdSniff.exe"
$dump = Join-Path $wdDir $CaptureFile

Write-Host "[*] WinDivert capture script created"
Write-Host "[*] Duration: ${Duration}s"
Write-Host "[*] Capturing ports: 31100,31150"
Write-Host ""

# Minimal minimal capture - just sniff for SAS frames
$cmd = "$wdExe ${Duration} 31100,31150 > $dump"
Write-Host "> Running: $cmd"
(& $cmd)  # as SYSTEM via bulk drive

Write-Host ""
Write-Host "[+] Capture complete: $dump"
'@

$createCaptureContent | Out-File $createCapture -Encoding utf8 -NoNewline

# ------------------------------------------------------------------
# Inject AFT transfer
# ------------------------------------------------------------------

Write-Host "[*] Starting AFT injection..." -ForegroundColor Cyan
Write-Host "[*] Command: \$PSCommandPath -Send ...\n"

# Run inject BEFORE capture if needed (capture ends "after injector")
$injectCmd = """$injectScript"" -Send -IP $ComputerName -Amount $($AftAmount * 100) -nr -DryRun"
Write-Host "> Running: $injectCmd"

# Show instructions to user
Write-Host ""
Write-Host "============================================================="
Write-Host "MANUAL INSTRUCTIONS"
Write-Host "============================================================="
Write-Host ""
Write-Host "1. You should now see AFT transfer being injected via WinDivert."
Write-Host ""
Write-Host "2. Once AFT transfer completes, OneHand will emit meter update."
Write-Host "   Look for in SlotLog:"
Write-Host "      - \`[GM2AU_aurumExecute]\`"
Write-Host "      - \`Cashless In: \$$([int]($AftAmount * 100))\`"
Write-Host "      - \`Aurum $AftType credit state increased to\`"
Write-Host ""
Write-Host "3. After 5 seconds, the capture will be saved."
Write-Host ""
Write-Host "4. Parse captures:"
Write-Host "   cd $repoRoot"
Write-Host "   python tactic-c-credit-meter-inject/parser/SasMeterParser.py `\$pwd/aft/captures/meter-write-*.txt"
Write-Host ""
Write-Host "5. Discovered meter-write template will report SAS 0x0F (Send selected meters)"
Write-Host "   and expected byte format for Phase 2 implementation."
Write-Host ""

# ------------------------------------------------------------------
# Display summary
# ------------------------------------------------------------------

Write-Host "============================================================="
Write-Host "SCRIPT SUMMARY"
Write-Host "============================================================="
Write-Host "This script generates minimal capture + triggers AFT inject."
Write-Host "It does NOT run the capture or inject (user must execute)."
Write-Host ""
Write-Host "To complete Phase 1 discovery:"
Write-Host "  1. Run: & PowerShell $createCapture -IP $ComputerName -Duration $DurationSec -CaptureFile capture-metric.txt"
Write-Host "  2. Run AFT: `path\to\parent-dir\Send-TestAft1000.ps1 -Send -IP $ComputerName -Amount $($AftAmount * 100) -nr"
Write-Host "  3. Use: python my-parser.py capture-metric.txt"
Write-Host ""
Write-Host "To parse afterwards:"
Write-Host "  cd $repoRoot"
Write-Host "  cdd tactic-c-credit-meter-inject/parser"
Write-Host "  python SasMeterParser.py ../log/capture/capture-metric.txt"
Write-Host ""

Write-Host "[+] Phase 1 discovery script created: $createCapture"
Write-Host "[+] AFT inject script: $injectScript"
Write-Host "[+] Parser: $parseScript"
Write-Host ""
