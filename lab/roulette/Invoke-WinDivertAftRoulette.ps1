<#
.SYNOPSIS
    Roulette AFT inject — WinDivert 0x72 into CommCtrlSAS:30550 (MUX) -> Aurum.

.DESCRIPTION
    Thin wrapper around lab\Invoke-WinDivertAft.ps1 with roulette defaults:
      -CabinetProfile Roulette
      -AutoBridgePort   (ClientsSet WakeUpPort first, usually 30550)
      -SasPollMode WinDivert

    Reads ClientsSet.xml for SASAddress + WakeUpPort so the 0x72 goes on the
    same channel the IGT tester uses. Works with the tester on (organic 80/81)
    or off (pollaft simulates polls). Cashout exception 66 is not treated as busy.

    Slot cabinets keep using lab\Send-TestAft1000.ps1 / Invoke-WinDivertAft.ps1 (31150).

    Prerequisite: Gateway SAS must have brought up MUX (historically COM5 @921600) so
    :30550 is listening and Aurum is ESTABLISHED. If only :40000 is up, cold-boot the
    cabinet or restore the USB serial MUX — service restart alone often stays on :40000.

.EXAMPLE
    .\Invoke-WinDivertAftRoulette.ps1 -Send

.EXAMPLE
    # Match successful IGT tester cashable transfer on .90
    .\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -c 1000000

.EXAMPLE
    .\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -Amount 100000 -nr -NoAutoWake
#>
[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [Alias('h')]
    [switch] $Help,

    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int64]  $AmountCents = 100000,
    [int64]  $Amount = 0,
    [int]    $AssetNumber = 777,
    [int]    $TransactionNumber = 0,
    [byte]   $SasAddress = 0,
    [int]    $ObserveMs = 8000,
    [int]    $AckGraceMs = 600,

    [Alias('c')]  [switch] $Cashable,
    [Alias('r')]  [switch] $Restricted,
    [Alias('nr')] [switch] $NonRestricted,

    [pscredential] $Credential,
    [ValidateSet('Auto', 'WinDivert', 'Com', 'None')]
    [string] $SasPollMode = 'WinDivert',
    [switch] $NoAutoWake,
    [switch] $NoAutoBootstrap,
    [switch] $NoAutoSasPoll,
    [switch] $SkipAurumReadyWait,
    [int]    $MaxRemediationCycles = 4,
    [switch] $RemoveDriver,

    [Parameter(ParameterSetName = 'DryRun')]
    [switch] $DryRun,
    [Parameter(ParameterSetName = 'Send', Mandatory = $true)]
    [switch] $Send,

    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]] $RemainingArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Help -or ($RemainingArgs -contains '/?') -or ($RemainingArgs -contains '-?')) {
    Write-Host @"
Invoke-WinDivertAftRoulette.ps1 — roulette AFT (CommCtrlSAS WakeUpPort / MUX path).

  .\Invoke-WinDivertAftRoulette.ps1 -Send
  .\Invoke-WinDivertAftRoulette.ps1 -Send -IP 10.0.0.90 -c 1000000
  .\Invoke-WinDivertAftRoulette.ps1 -Send -nr 100000 -NoAutoWake

Channel comes from ClientsSet.xml (SASAddress + WakeUpPort, usually 1 / 30550).
Works with IGT tester on or off. See lab\roulette\README.md
"@
    exit 0
}

$inject = Join-Path (Split-Path $PSScriptRoot -Parent) 'Invoke-WinDivertAft.ps1'
if (-not (Test-Path -LiteralPath $inject)) {
    throw "Missing $inject"
}

$fwd = @{
    ComputerName         = $ComputerName
    AmountCents          = $AmountCents
    Amount               = $Amount
    AssetNumber          = $AssetNumber
    TransactionNumber    = $TransactionNumber
    ObserveMs            = $ObserveMs
    AckGraceMs           = $AckGraceMs
    CabinetProfile       = 'Roulette'
    AutoBridgePort       = $true
    SasPollMode          = $SasPollMode
    MaxRemediationCycles = $MaxRemediationCycles
}
if ($SasAddress -gt 0) { $fwd.SasAddress = $SasAddress }
if ($Credential) { $fwd.Credential = $Credential }
if ($Cashable) { $fwd.Cashable = $true }
if ($Restricted) { $fwd.Restricted = $true }
if ($NonRestricted) { $fwd.NonRestricted = $true }
if ($NoAutoWake) { $fwd.NoAutoWake = $true }
if ($NoAutoBootstrap) { $fwd.NoAutoBootstrap = $true }
if ($NoAutoSasPoll) { $fwd.NoAutoSasPoll = $true }
if ($SkipAurumReadyWait) { $fwd.SkipAurumReadyWait = $true }
if ($RemoveDriver) { $fwd.RemoveDriver = $true }
if ($Send) { $fwd.Send = $true }
elseif ($DryRun) { $fwd.DryRun = $true }

Write-Host ''
Write-Host '=== Roulette AFT (ClientsSet channel / AutoBridgePort / profile Roulette) ===' -ForegroundColor White
& $inject @fwd
exit $LASTEXITCODE
