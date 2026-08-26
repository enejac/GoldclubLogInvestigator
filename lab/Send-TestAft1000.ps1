<#
.SYNOPSIS
    Send a $1,000 on-demand AFT/WAT promo test transaction to the lab EGM.

.DESCRIPTION
    Thin convenience wrapper around Invoke-WinDivertAft.ps1. It triggers the proven
    WinDivert in-stream injection method: a raw SAS 0x72 AFT "transfer funds" command
    ($1,000 promo, asset 777) is 0x1B-framed and injected, via WinDivert, INTO the
    EXISTING CommCtrlSAS:31150 -> Aurum:<ephemeral> loopback TCP flow at the correct
    sequence number. Aurum reads it as the next in-order bytes on its established SAS
    session and commits the transfer. No IGT SAS tester is required.

    This is NOT a new TCP socket to the bridge (that is never merged into the live
    SAS stream), nor a .NET-remoting WAT requestTransfer, nor the legacy setBonusAward
    jackpot path. See aft/RUNBOOK.md for the full method, the live-injection
    evidence, and the dead-ends that do not work.

    Expected cabinet evidence after -Send (all checked by Invoke-WinDivertAft.ps1):
      - sasmsgr qGMID1:0172... full-hex match (per-txn id + CRC unique), compared in UTC
      - SlotLog: Cashless In: $1,000.00 and Aurum promo credit state increased to 100000
      - OneHand TRANSACTION EVENTS: Transfer IN $1,000.00(promo:0)
      - OneHand GM2AU: Withdraw successful GCC_ST_20664_01 ... 100000(promo:0)

    Default mode is -DryRun (injects nothing). Use -Send to actually inject the packet.

.EXAMPLE
    .\Send-TestAft1000.ps1

.EXAMPLE
    .\Send-TestAft1000.ps1 -Send

.EXAMPLE
    # Target a different cabinet by IP (-IP is an alias of -ComputerName)
    .\Send-TestAft1000.ps1 -Send -IP 10.0.0.110

.EXAMPLE
    # Custom amount (raw credits / base units) into the non-restricted (promo) field.
    .\Send-TestAft1000.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -nr

.EXAMPLE
    # Custom amount into the cashable field instead (-c). Use -r for restricted.
    .\Send-TestAft1000.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -c
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

    # Optional injector tuning, forwarded to Invoke-WinDivertAft.ps1 only when supplied:
    #   -ObserveMs  : how long WdInject waits for ANY outbound 31150 segment (default 8000).
    #   -AckGraceMs : ACK-anchor grace window for an idle-but-established link (default 600;
    #                 0 disables ACK-anchoring / legacy payload-only behavior).
    [int]    $ObserveMs = 8000,
    [int]    $AckGraceMs = 600,

    # Transfer-type selection (default none = non-restricted/promo). Forwarded to
    # Invoke-WinDivertAft.ps1, which enforces "only one of -c / -r / -nr".
    [Alias('c')]  [switch] $Cashable,
    [Alias('r')]  [switch] $Restricted,
    [Alias('nr')] [switch] $NonRestricted,

    # Optional cabinet admin credential for the faster WinRM transport (NTLM by IP).
    [pscredential] $Credential,

    [string] $SasComPort = 'COM4',
    [switch] $NoAutoSasPoll,
    [ValidateSet('Auto', 'WinDivert', 'Com', 'None')]
    [string] $SasPollMode,
    [switch] $NoAutoWake,
    [switch] $NoAutoBootstrap,

    # Self-healing remediation cycles before abort (default 4).
    [int]    $MaxRemediationCycles = 4,

    # Maintenance: safely remove the WinDivert driver service on the cabinet, then exit.
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

function Show-TestAft1000Help {
    $runbook = Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\RUNBOOK.md'
    $readme = Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\README.md'
    Write-Host @"
Send-TestAft1000.ps1 — thin wrapper around Invoke-WinDivertAft.ps1 (`$1,000 promo AFT default).

PREREQUISITES
  Lab cabinet reachable (default 10.0.0.90). AutoWake restarts bridge if wedged.
  See aft/PROVEN-INJECT-PROCEDURE.md

MODES
  Default = DryRun. Pass -Send for live injection ($1,000 promo AFT).

SIMPLEST LIVE RUN
  .\Send-TestAft1000.ps1 -Send
  Send-TestAft1000.cmd -Send          (same; no ".\" required in PowerShell/cmd)

TARGET / AMOUNT / TRANSFER TYPE
  -IP <addr>                  cabinet (default 10.0.0.90)
  -Amount <int>                explicit amount (also: unnamed int after -IP or -c/-r/-nr)
  -AmountCents <int>           legacy alias
  -nr [<amount>] / -c / -r     transfer type (default promo); only one

EXAMPLES (see $runbook)
  .\Send-TestAft1000.ps1 -Help
  .\Send-TestAft1000.ps1 -Send -IP 10.0.0.90 -c 1000000
  .\Send-TestAft1000.ps1 -Send -IP 10.0.0.90 -nr
  .\Send-TestAft1000.ps1 -Send -IP 10.0.0.90 1000000 -c
  .\Send-TestAft1000.ps1 -Send -IP 10.0.0.110 -Amount 1000000 -c

DOCS
  $runbook
  $readme
  Full parameter list: .\Invoke-WinDivertAft.ps1 -Help
"@
}

if ($Help -or $RemainingArgs -contains '/?' -or $RemainingArgs -contains '-?') {
    Show-TestAft1000Help
    exit 0
}

# Optional positional amount: one unnamed positive int (after -IP and/or after -c/-r/-nr).
$trailingArgs = @($RemainingArgs) | Where-Object { $_ -ne $null -and [string]$_ -ne '' }
$positionalAmount = $null
$unexpectedTrailing = New-Object System.Collections.Generic.List[object]
foreach ($arg in $trailingArgs) {
    $text = [string]$arg
    if ($null -eq $positionalAmount -and $text -match '^\d+$') {
        $parsed = [int64]$text
        if ($parsed -gt 0) {
            $positionalAmount = $parsed
            continue
        }
    }
    $unexpectedTrailing.Add($arg) | Out-Null
}
if ($unexpectedTrailing.Count -gt 0) {
    throw "Unexpected argument(s): $($unexpectedTrailing -join ', '). Run with -Help for usage."
}
if ($null -ne $positionalAmount) {
    if ($PSBoundParameters.ContainsKey('Amount') -and $Amount -gt 0 -and $Amount -ne $positionalAmount) {
        throw "Conflicting amount: -Amount $Amount vs positional $positionalAmount. Run with -Help for usage."
    }
    if (-not $PSBoundParameters.ContainsKey('Amount') -or $Amount -le 0) {
        $Amount = $positionalAmount
    }
}

$here = $PSScriptRoot
$injectScript = Join-Path $here 'Invoke-WinDivertAft.ps1'
if (-not (Test-Path -LiteralPath $injectScript)) {
    throw "Missing $injectScript"
}

if ($RemoveDriver) {
    & $injectScript -ComputerName $ComputerName -RemoveDriver
    exit $LASTEXITCODE
}

Write-Host ''
Write-Host '=== Send on-demand AFT/WAT promo test transaction ===' -ForegroundColor White
$effectiveAmount = if ($Amount -gt 0) { $Amount } else { $AmountCents }
$transferType = if ($Cashable) { 'cashable' } elseif ($Restricted) { 'restricted' } else { 'non-restricted' }
Write-Host "Cabinet: $ComputerName  |  Asset: $AssetNumber  |  Transfer type: $transferType  |  Amount: $effectiveAmount (raw SAS units; = `$$([double]$effectiveAmount / 100.0) if 1 unit = 1 cent)"
Write-Host 'Path   : WinDivert inject (0x72 + 0x1B) -> live 31150 stream -> Aurum/OneHand'
Write-Host 'Note   : This is not legacy setBonusAward / jackpot mode.'
Write-Host ''

$invokeArgs = @{
    ComputerName      = $ComputerName
    AmountCents       = $AmountCents
    AssetNumber       = $AssetNumber
    TransactionNumber = $TransactionNumber
}
if ($Amount -gt 0) { $invokeArgs.Amount = $Amount }
# Forward injector tuning only when the caller actually supplied it, so the orchestrator's
# own defaults stay authoritative otherwise.
if ($PSBoundParameters.ContainsKey('ObserveMs'))  { $invokeArgs.ObserveMs = $ObserveMs }
if ($PSBoundParameters.ContainsKey('AckGraceMs')) { $invokeArgs.AckGraceMs = $AckGraceMs }
if ($Cashable)      { $invokeArgs.Cashable = $true }
if ($Restricted)    { $invokeArgs.Restricted = $true }
if ($NonRestricted) { $invokeArgs.NonRestricted = $true }
if ($Credential)    { $invokeArgs.Credential = $Credential }
if ($PSBoundParameters.ContainsKey('SasComPort')) { $invokeArgs.SasComPort = $SasComPort }
if ($NoAutoSasPoll) { $invokeArgs.NoAutoSasPoll = $true }
if ($NoAutoWake) { $invokeArgs.NoAutoWake = $true }
if ($NoAutoBootstrap) { $invokeArgs.NoAutoBootstrap = $true }
$invokeArgs.MaxRemediationCycles = $MaxRemediationCycles
if ($PSBoundParameters.ContainsKey('SasPollMode')) {
    $invokeArgs.SasPollMode = $SasPollMode
}
else {
    $invokeArgs.SasPollMode = 'WinDivert'
}

if ($PSCmdlet.ParameterSetName -eq 'Send') {
    $invokeArgs.Send = $true
}
else {
    $invokeArgs.DryRun = $true
}

& $injectScript @invokeArgs
exit $LASTEXITCODE
