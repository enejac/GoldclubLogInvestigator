<#
.SYNOPSIS
    Root forwarder: synthetic bill accept on KeyCtrl (roulette :30300 or Slot :30800).
#>
[CmdletBinding(DefaultParameterSetName = 'DryRun')]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]] $RemainingArgs,
    [Alias('IP')][string] $ComputerName = '10.0.0.90',
    [int] $BillCode = 27,
    [ValidateSet('minimal', 'credit', 'captured', 'captured2', 'escrow', 'legacy')][string] $PayloadProfile = 'captured',
    [int] $Credits = 200000,
    [int] $RepeatCount = 1,
    [int] $RunSeconds = 20,
    [ValidateSet('Auto', 'Roulette', 'Slot')]
    [string] $GameKind = 'Auto',
    [switch] $Send,
    [switch] $DryRun,
    [switch] $ClearLockFirst,
    [switch] $Help
)
$target = Join-Path $PSScriptRoot 'lab\roulette\Invoke-BillInjectRouletteRemote.ps1'
if (-not (Test-Path -LiteralPath $target)) { throw "Missing $target" }
$fwd = @{
    ComputerName   = $ComputerName
    BillCode       = $BillCode
    PayloadProfile = $PayloadProfile
    Credits        = $Credits
    RepeatCount    = $RepeatCount
    RunSeconds     = $RunSeconds
    GameKind       = $GameKind
}
if ($Help) { $fwd.Help = $true }
if ($Send) { $fwd.Send = $true }
if ($DryRun) { $fwd.DryRun = $true }
if ($ClearLockFirst) { $fwd.ClearLockFirst = $true }
if ($RemainingArgs) { & $target @fwd @RemainingArgs } else { & $target @fwd }
exit $LASTEXITCODE
