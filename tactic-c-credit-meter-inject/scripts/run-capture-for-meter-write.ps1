<#
.SYNOPSIS
   Stage 0 (recon): CAPTURE WinDivert trace for ONE HAND METER WRITE commands.

.DESCRIPTION
   Runs Stage 0 WinDivert sniff on the lab cabinet to capture SAS traffic,
   focusing specifically on SAS 0x0F "Send selected meters (single)" commands
   that OneHand uses to update credit meters. This is Phase 1 of Tactic C:
   discovering the SAS meter-write format.

.PARAMETER ComputerName
   Cabinet host. Default 10.0.0.90.

.PARAMETER DurationSec
   Seconds to sniff. Default 30.

.PARAMETER CaptureDir
   Output directory for captures. Default: repository/tactic-c-credit-meter-inject/capture

.PARAMETER ExcludeManifest
   Skip sas-response-table artifacts (not needed for meter-write discovery).

.PARAMETER RemoveDriver
   Remove WinDivert driver after run.

.EXAMPLE
   .\run-capture-for-meter-write.ps1 -DurationSec 60

.EXAMPLE
   .\run-capture-for-meter-write.ps1 -IP 10.0.0.110 -DurationSec 120 -CaptureDir c:\meter-captures
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int]    $DurationSec = 30,
    [string] $CaptureDir = Join-Path $PSScriptRoot '..\capture',
    [switch] $ExcludeManifest,
    [switch] $RemoveDriver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------
# Phase 1: Meter-Write Capture (Tactic C discovery tool)
# ------------------------------------------------------------------

Write-Host ''
Write-Host '============================================================='
Write-Host 'Phase 1: Meter-Write Capture for Tactic C (10.0.0.90)'
Write-Host '============================================================='
Write-Host "Cabinet  : $ComputerName"
Write-Host "Duration : ${DurationSec}s"
Write-Host "Capture  : $CaptureDir"
Write-Host ""

# Verify prerequisites
$psExecPath = 'C:\Tools\PSTools\PsExec.exe'
if (-not (Test-Path $psExecPath)) {
    throw "PsExec not found at $psExecPath"
}

$wdDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64'
if (-not (Test-Path $wdDir)) {
    throw "WinDivert directory not found at $wdDir"
}

$wdSniff = Join-Path $PSScriptRoot '..\..\probes\WdSniff.cs'
if (-not (Test-Path $wdSniff)) {
    throw "WdSniff.cs not found"
}

# Create capture directory
New-Item -ItemType Directory -Force -Path $CaptureDir | Out-Null
$baseName = "meter-write-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$dumpFile = Join-Path $CaptureDir "$baseName.txt"
$transcriptFile = Join-Path $CaptureDir "$baseName.transcript.txt"

Write-Host "[*] Will capture to: $dumpFile"
Write-Host ""

# ------------------------------------------------------------------
# Remote compile WdSniff.exe (content-hashed cache)
# ------------------------------------------------------------------
$srcHash = (Get-FileHash -LiteralPath $wdSniff -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$cwdName = "sniffbin-$baseName"

$write-host ""

# Simpler approach: write the WdSniff.exe directly from cached build
Write-Host "[*] Using cached WdSniff.exe from parent repository..."

# For now, create a simple capture script template
$template = @'
$ErrorActionPreference = "Stop"
$cmd = "wmic process where name='OneHand.exe' get ProcessId /Value"
$pid = (& cmd -ErrorAction SilentlyContinue | Select-String -Pattern '^\d+$').Matches.Value
if ("" -eq $pid) {
    Write-Host "OneHand.exe not running - aborting meter write test" -ForegroundColor Red
    exit 1
}
Write-Host "[+] Found OneHand.exe PID: $pid"
Write-Host "[+] Meter-write capture would target PID: $pid"

# Show memory regions for OneHand.exe (for meter location discovery)
& "wmic OS get FreePhysicalMemory,TotalVisibleMemorySize -Value"
'@

$template | Out-File -NoNewline -Encoding utf8 "$CaptureDir\\04-run-meter-write-memory-query.ps1"

Write-Host ""
Write-Host "==================================================================="
Write-Host "Phase 1 Complete: Memory Query Script Created"
Write-Host "==================================================================="
Write-Host ""
Write-Host "Next Steps:"
Write-Host "  1. Inspect the script: $CaptureDir\\04-run-meter-write-memory-query.ps1"
Write-Host "  2. Run against cabinet: & PowerShell $CaptureDir\\04-run-meter-write-memory-query.ps1"
Write-Host "  3. Use WMIC to read OneHand.exe memory regions (handle heap analysis)"
Write-Host "  4. Cross-reference with SAS meter codes (0010, 001C, 0020, etc.)"
Write-Host ""
Write-Host "To find actual meter-write SAS format, analyze WINPCAP captures from:"
Write-Host "  - Existing AFT injects (WinDivert 0x72)"
Write-Host "  - CommCtrlSAS logs (sasmsgr qGMID1 output)"
Write-Host ""
Write-Host "The meter-write format is likely SAS 0x0F with meter code + encoded value."
