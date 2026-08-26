<#
.SYNOPSIS
    Root wrapper — forwards to lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1
.EXAMPLE
    .\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90
.EXAMPLE
    .\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90 -PassThru
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [ValidateSet('passthru', 'capture', 'inject', 'heal', 'billinject')]
    [string] $Mode = 'inject',
    [ValidateSet('insert', 'eject', 'roundtrip')]
    [string] $Action = 'insert',
    [int] $RunSeconds = 40,
    [int] $InjectAfterMs = 0,
    [int] $InitialDelta = 0,
    [int] $DrainReserveSec = 35,
    [string] $Rom = '700|||701|||01D68A721B000019',
    [string] $EjectRom = '700|||701|||655',
    [int] $ServerPort = 30300,
    [string] $TargetProcess = 'ruleta',
    [int] $HitTimeoutSec = 4,
    [int] $GateTimeoutSec = 60,
    [int] $HandpayClearTimeoutSec = 4,
    [int] $KeyboardRecoverSec = 8,
    [switch] $WaitForDrain,
    [switch] $WaitForDallas,
    [switch] $SkipGate,
    [switch] $Fast,
    [switch] $PassThru,
    [string] $WinDivertDir = 'C:\Tools\WinDivert\x64',
    [string] $ExePath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$target = Join-Path $PSScriptRoot 'lab\roulette\Invoke-DallasSpliceRouletteRemote.ps1'
if (-not (Test-Path -LiteralPath $target)) {
    throw "Missing roulette orchestrator: $target"
}

& $target @PSBoundParameters
