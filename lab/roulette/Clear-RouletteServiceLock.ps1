<#
.SYNOPSIS
    Clear roulette "FUERA DE SERVICIO" / tilt lock via Dallas admin inject.

.DESCRIPTION
    Wrong bill-inject frames can trip the out-of-service overlay. This script:
      1. Restarts Serial Communication Gateway if keyboard is down (30300 path)
      2. Waits for keyboard reconnect in ruleta log
      3. Runs Dallas admin roundtrip (700/701/ROM eject+insert)

.EXAMPLE
    .\Clear-RouletteServiceLock.ps1 -ComputerName 10.0.0.90
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [int] $KeyboardWaitSec = 90,
    [int] $GateTimeoutSec = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repo 'LabAccess.ps1')

function Get-RuletaLogPathRemote {
    param([System.Management.Automation.Runspaces.PSSession] $Session)
    Invoke-Command -Session $Session -ScriptBlock {
        @(Get-ChildItem 'C:\goldclub\var\log\ruleta Roulette' -Filter '*.log' -EA SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName)[0]
    }
}

function Test-KeyboardDownRemote {
    param([System.Management.Automation.Runspaces.PSSession] $Session, [string] $LogPath)
    Invoke-Command -Session $Session -ScriptBlock {
        param($Path)
        if (-not $Path -or -not (Test-Path $Path)) { return $false }
        $tail = Get-Content -LiteralPath $Path -Tail 400 -EA SilentlyContinue
        $down = $false
        foreach ($line in $tail) {
            if ($line -match 'unlock of type lmt_keyboard_disconnected|keyboard reconnected') { $down = $false }
            elseif ($line -match 'lock of type lmt_keyboard_disconnected|keyboard disconnected|KB ping timeout') { $down = $true }
        }
        return $down
    } -ArgumentList $LogPath
}

Write-Host ''
Write-Host '=== Clear roulette service lock (Dallas admin) ===' -ForegroundColor Cyan
Write-Host (" Cabinet: {0}" -f $ComputerName)

$opt = New-PSSessionOption -OpenTimeout 4000 -OperationTimeout 120000 -CancelTimeout 4000
$session = New-PSSession -ComputerName $ComputerName -Credential (Get-LabCredential) `
    -Authentication Negotiate -SessionOption $opt -ErrorAction Stop

try {
    $logPath = Get-RuletaLogPathRemote -Session $session
    $kbDown = Test-KeyboardDownRemote -Session $session -LogPath $logPath
    Write-Host (" Keyboard down (log): {0}" -f $kbDown)

    if ($kbDown) {
        Write-Host ' Restarting Serial Communication Gateway to restore :30300 keyboard...' -ForegroundColor Yellow
        Invoke-Command -Session $session -ScriptBlock {
            Restart-Service 'GoldClub Serial Communication Gateway' -Force -EA Stop
            Start-Sleep -Seconds 5
        }
    }

    $deadline = (Get-Date).AddSeconds($KeyboardWaitSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        if (-not (Test-KeyboardDownRemote -Session $session -LogPath $logPath)) {
            Write-Host ' Keyboard reconnect seen in log.' -ForegroundColor Green
            break
        }
    }
    if ((Get-Date) -ge $deadline) {
        Write-Host ' Keyboard still down — continuing with Dallas inject anyway.' -ForegroundColor Yellow
    }
}
finally {
    Remove-PSSession $session -EA SilentlyContinue
}

$dallas = Join-Path $PSScriptRoot 'Invoke-DallasSpliceRouletteRemote.ps1'
$result = & $dallas -ComputerName $ComputerName -Action roundtrip -GateTimeoutSec $GateTimeoutSec

Write-Host ''
Write-Host (" DallasHit={0} UiReady={1} Injected={2} Blocker={3}" -f `
    $result.DallasHit, $result.UiReady, $result.Injected, $result.Blocker)

if ($result.UiReady) {
    Write-Host ' Service lock should be clear — dismiss any remaining menu on cabinet.' -ForegroundColor Green
}
elseif ($result.DallasHit) {
    Write-Host ' Admin key registered; check cabinet for service menu / unlock prompt.' -ForegroundColor Yellow
}
else {
    Write-Host ' Dallas did not register — wait ~60s and re-run this script.' -ForegroundColor Yellow
}

return $result
