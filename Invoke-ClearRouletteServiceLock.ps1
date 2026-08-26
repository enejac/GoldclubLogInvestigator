<#
.SYNOPSIS
    Clear roulette FUERA DE SERVICIO lock (forwards to lab script).
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [int] $KeyboardWaitSec = 90,
    [int] $GateTimeoutSec = 60
)
& (Join-Path $PSScriptRoot 'lab\roulette\Clear-RouletteServiceLock.ps1') @PSBoundParameters
