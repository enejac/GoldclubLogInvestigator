<#
.SYNOPSIS
   Trigger AFT transfer on cabinet to collect response traffic during meter-update.

.DESCRIPTION
   Runs AFT transfer injection, collects essentially the moment when OneHand
   processes the credit and potentially emits meter-write commands.
#>
param(
    [Parameter(Mandatory=$true)]
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',

    [int] $AmountCents = 100000,
    
    [string] $InstallDrive = 'C:\Windows\Temp\auruntap',

    [switch] $TestOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Write-Host ''
Write-Host "AFT Inject Trigger"
Write-Host "=================="
Write-Host "Cabinet     : $ComputerName"
Write-Host "Amount      : \$$([int]($AmountCents / 100)).00"
Write-Host ""

$parentProject = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$sendScript = Join-Path $parentProject "lab\Send-TestAft1000.ps1"

if (-not (Test-Path $sendScript)) {
    Write-Host "[!] Send-TestAft1000.ps1 not found at: $sendScript"
    exit 1
}

Write-Host "[+] Found inject script: $sendScript"
Write-Host ""

# Show what will happen:
Write-Host "EXECUTE this in parent directory:"
Write-Host "> cd C:\Users\Ezbogar\GoldclubLogInvestigator"
Write-Host "> .\Send-TestAft1000.ps1 -Send -IP $ComputerName -Amount $AmountCents -nr"
Write-Host ""

if ($TestOnly) {
    Write-Host "Run complete capture and then come back with results."
    Write-Host ""
    Write-Host "Netdump instructions:"
    Write-Host "1. Run: Invoke-AurumTrafficCapture.ps1 -IP $ComputerName -Ports 31100,31150"
    Write-Host "2. Execute above AFT script as SYSTEM"
    Write-Host "3. Parse results: python tactic-c-credit-meter-inject/parser/SasMeterParser.py <file>"
} else {
    Write-Host "[+] Ready to Run"
    Write-Host "[+] (Auto-run disabled for manual control)"
}
