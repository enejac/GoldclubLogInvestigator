<#
.SYNOPSIS
    Root forwarder: roulette AFT inject (see lab\roulette\Invoke-WinDivertAftRoulette.ps1).
#>
[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]] $RemainingArgs,
    [Alias('IP')][string] $ComputerName = '10.0.0.90',
    [switch] $Send,
    [switch] $DryRun,
    [switch] $Help,
    [int64] $Amount = 0,
    [int64] $AmountCents = 100000,
    [Alias('c')][switch] $Cashable,
    [Alias('r')][switch] $Restricted,
    [Alias('nr')][switch] $NonRestricted,
    [switch] $NoAutoWake,
    [switch] $NoAutoBootstrap,
    [switch] $NoAutoSasPoll,
    [switch] $SkipAurumReadyWait,
    [int] $MaxRemediationCycles = 4,
    [switch] $RemoveDriver,
    [pscredential] $Credential
)
$target = Join-Path $PSScriptRoot 'lab\roulette\Invoke-WinDivertAftRoulette.ps1'
if (-not (Test-Path -LiteralPath $target)) { throw "Missing $target" }
$fwd = @{
    ComputerName         = $ComputerName
    Amount               = $Amount
    AmountCents          = $AmountCents
    MaxRemediationCycles = $MaxRemediationCycles
}
if ($Help) { $fwd.Help = $true }
if ($Send) { $fwd.Send = $true }
if ($DryRun) { $fwd.DryRun = $true }
if ($Cashable) { $fwd.Cashable = $true }
if ($Restricted) { $fwd.Restricted = $true }
if ($NonRestricted) { $fwd.NonRestricted = $true }
if ($NoAutoWake) { $fwd.NoAutoWake = $true }
if ($NoAutoBootstrap) { $fwd.NoAutoBootstrap = $true }
if ($NoAutoSasPoll) { $fwd.NoAutoSasPoll = $true }
if ($SkipAurumReadyWait) { $fwd.SkipAurumReadyWait = $true }
if ($RemoveDriver) { $fwd.RemoveDriver = $true }
if ($Credential) { $fwd.Credential = $Credential }
& $target @fwd @RemainingArgs
exit $LASTEXITCODE