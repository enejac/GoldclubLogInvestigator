<#
.SYNOPSIS
   Execute Phase 1 Discovery: WinDivert capture + Analyzer on cabinet.
   MUST be run with admin access (as SYSTEM) on the workstation to target cabinet.
.DESCRIPTION
   Executes WinDivert capture on the lap cabinet (10.0.0.90), collects SAS frames,
   then sends results to parser. This is the critical Phase 1 step to discover
   SAS 0x0F meter-write format.
#>
param(
    [Parameter(Mandatory=$true)]
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',

    [int] $DurationSec = 10,

    [string] $OutDir = Join-Path (Split-Path $PSScriptRoot) '..\logs\capture',

    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',

    [string] $WinDivertDll = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64\WinDivert.dll',

    [switch] $RemoveDriver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------
# Prerequisites
# ------------------------------------------------------------------

$repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$wdSniff = Join-Path $repoRoot 'WdSniff.cs'
$wdNetdump = Join-Path $repRoot 'WdNetdump.exe'  # Need to check if this exists

if (-not (Test-Path $PsExecPath)) {
    throw "PsExec not found: $PsExecPath"
}

if (-not (Test-Path $WinDivertDll)) {
    Write-Host "[⚠] WinDivert.dll not found." -ForegroundColor Yellow
    Write-Host "    Capture may not work without this driver."
}

# ------------------------------------------------------------------
# Script
# ------------------------------------------------------------------

DumpFile = "capture-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"

Write-Host ''
Write-Host "Phase 1 Discovery Execution"
Write-Host "============================"
Write-Host "Target: $ComputerName"
Write-Host "Duration: ${DurationSec}s"
Write-Host "Capture output: $DumpFile"
Write-Host ""

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

# Because WinDivert requires SYSTEM on target cabinet,
# we need to run capture via PsExec-as-SYSTEM
RemoteScript = @'
$ErrorActionPreference = "Stop"

# Build capture script template
$wdDir = "C:\Windows\Temp\meter-write-stage"
$wdExe = "$wdDir\WdSniff.exe"
$outFile = "$wdDir\capture.txt"

# Ensure destination exists
New-Item -ItemType Directory -Force -Path $wdDir | Out-Null

# Run capture for 5 seconds (ensured from caller)
$time = 5000
$ports = "31100,31150"

Write-Host "[Phase1] Starting WinDivert capture..."
Write-Host "[Phase1] Port filter: $ports"
Write-Host "[Phase1] Duration: $($time / 1000)s"
Write-Host "[Phase1] Output: $outFile"

# Execute WdSniff
& "$wdExe" $time $ports | Out-File "$outFile" -Encoding utf8

Write-Host "[Phase1] Capture complete."
Write-Host "[Phase1] File size: $((Get-Item $outFile -ErrorAction SilentlyContinue).Length) bytes"

# Cleanup
$cwdPath = $pwd.Path
Remove-Item $wdDir -Recurse -Force -ErrorAction SilentlyContinue
'@

$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($RemoteScript))

Write-Host "[*] Starting remote capture (as SYSTEM via PsExec)..." -ForegroundColor Cyan

Start-Process -FilePath $PsExecPath -ArgumentList @(
    "\\$ComputerName", '-accepteula', '-s', 'powershell.exe', '-NoProfile',
    '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $enc
) -Wait -WindowStyle Normal

Write-Host "[+] Remote capture of $ComputerName completed"
Write-Host "[+] Results: CONST LOG" 
