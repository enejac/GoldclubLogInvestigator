<#
.SYNOPSIS
    Remote roulette Dallas inject (Send-DallasKey style). Leaves slot WinDivert tools untouched.

.DESCRIPTION
    Roulette difference vs slot:
      - No OneHand; ruleta/godot online
      - Gateway STATUS usually shows ZERO :30800 subscribers during game
      - Slot WinDivert splice therefore returns NO_TARGET
      - This path: stop non-SAS CommCtrl, stub :30800, wait for client, publish ROM, restore CommCtrl

.EXAMPLE
    .\Invoke-SendDallasKeyRouletteRemote.ps1 -ComputerName 10.0.0.90
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [ValidatePattern('^[0-9A-Fa-f]{16}$')]
    [string] $Rom = '01D68A721B000019',
    [ValidateSet('insert','eject','roundtrip')]
    [string] $Action = 'roundtrip',
    [int] $WaitForClientSeconds = 45,
    [switch] $SkipBiosStart,
    [switch] $WhatIf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$localStub = Join-Path $here 'Send-DallasKeyRoulette.ps1'
if (-not (Test-Path -LiteralPath $localStub)) { throw "Missing $localStub" }

if (-not (Get-Command Invoke-LabWinRmCommand -ErrorAction SilentlyContinue)) {
    $labAccess = Join-Path (Split-Path (Split-Path $here -Parent) -Parent) 'LabAccess.ps1'
    if (-not (Test-Path -LiteralPath $labAccess)) {
        $labAccess = 'C:\Users\Ezbogar\GoldclubLogInvestigator\LabAccess.ps1'
    }
    . $labAccess
}
if (-not (Get-Command Invoke-LabWinRmCommand -ErrorAction SilentlyContinue)) {
    throw 'LabAccess.ps1 / Invoke-LabWinRmCommand not found'
}

$remoteDir = "\\$ComputerName\c`$\Windows\Temp\dallas_roulette"
$remoteStub = Join-Path $remoteDir 'Send-DallasKeyRoulette.ps1'
New-Item -ItemType Directory -Force -Path $remoteDir | Out-Null
Copy-Item -LiteralPath $localStub -Destination $remoteStub -Force
Write-Host ("Staged stub -> {0}" -f $remoteStub) -ForegroundColor Green

if ($WhatIf) {
    Write-Host 'WhatIf: would stop CommCtrl (non-SAS), listen :30800, inject, restore' -ForegroundColor Yellow
    return
}

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host ' Roulette Dallas inject (Send-DallasKey fork, WinRM)' -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target : {0}" -f $ComputerName) -ForegroundColor Gray
Write-Host (" ROM    : {0}  Action: {1}" -f $Rom, $Action) -ForegroundColor Gray
Write-Host ' Slot WinDivert scripts are NOT used/modified.' -ForegroundColor DarkGray
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

$result = Invoke-LabWinRmCommand -ComputerName $ComputerName -ScriptBlock {
    param($Rom, $Action, $WaitForClientSeconds, $SkipBiosStart)
    $ErrorActionPreference = 'Stop'
    $log = 'C:\Windows\Temp\dallas_roulette_orch.log'
    function OL($m) {
        $line = '[{0}] {1}' -f (Get-Date -Format o), $m
        Write-Host $line
        Add-Content -Path $log -Value $line -Encoding ASCII
    }

    $stub = 'C:\Windows\Temp\dallas_roulette\Send-DallasKeyRoulette.ps1'
    $status = 'C:\Windows\Temp\dallas_roulette_status.txt'
    $sendLog = 'C:\Windows\Temp\dallas_roulette_send.log'
    Remove-Item $status, $sendLog, $log -Force -ErrorAction SilentlyContinue

    # Non-SAS Dallas path: COM8 -> CommCtrl -> TCP :30800
    # Managed by XYNT wrapper service (killing CommCtrl alone leaves service "Running" with no listener).
    $gwSvc = Get-Service | Where-Object { $_.DisplayName -eq 'GoldClub Serial Communication Gateway' } |
        Select-Object -First 1
    if (-not $gwSvc) { throw 'Service "GoldClub Serial Communication Gateway" not found' }

    function Get-DallasListenPid {
        Get-NetTCPConnection -LocalPort 30800 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty OwningProcess
    }

    $owner = Get-DallasListenPid
    if (-not $owner) {
        OL 'Nothing on :30800 — restarting Serial Communication Gateway service...'
        Restart-Service -Name $gwSvc.Name -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        $owner = Get-DallasListenPid
        if (-not $owner) {
            # Orphan Start-Process fallback (service sometimes stuck)
            OL 'Service restart did not bind :30800 — starting CommCtrl.exe directly'
            Start-Process -FilePath 'C:\goldclub\bin\CommCtrl.exe' `
                -ArgumentList '\goldclub\config\etc\application\CommCtrl\CommControler.ini' `
                -WorkingDirectory 'C:\goldclub\bin' -WindowStyle Hidden | Out-Null
            Start-Sleep -Seconds 4
            $owner = Get-DallasListenPid
        }
        if (-not $owner) {
            throw 'Nothing listening on :30800 after restarting GoldClub Serial Communication Gateway'
        }
    }

    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $owner)
    OL ("30800 owner pid={0} cmd={1}" -f $owner, $proc.CommandLine)
    if ($proc.CommandLine -match 'CommCtrlSAS') {
        throw 'Refusing to stop CommCtrlSAS (SAS bridge). Unexpected owner of :30800.'
    }

    $exePath = $proc.ExecutablePath
    if (-not $exePath) { $exePath = 'C:\goldclub\bin\CommCtrl.exe' }
    $cmdLine = $proc.CommandLine
    $workDir = Split-Path $exePath -Parent
    $gwSvcName = $gwSvc.Name

    OL 'Stopping Serial Communication Gateway (frees :30800)...'
    Stop-Service -Name $gwSvcName -Force -ErrorAction SilentlyContinue
    # Ensure child is gone even if service stop left it
    if (Get-Process -Id $owner -ErrorAction SilentlyContinue) {
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if (Get-DallasListenPid) {
        throw ':30800 still in use after stopping gateway service'
    }
    OL ':30800 is free'

    # Start stub
    $arg = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $stub,
        '-Rom', $Rom,
        '-Action', $Action,
        '-WaitForClientSeconds', "$WaitForClientSeconds"
    )
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $arg -PassThru -WindowStyle Hidden
    OL ("Stub started pid={0}" -f $p.Id)
    Start-Sleep -Seconds 1

    if (-not $SkipBiosStart) {
        $bios = 'C:\goldclub\bin\BiOS.exe'
        if (Test-Path $bios) {
            OL 'Starting BiOS.exe to encourage :30800 subscribe'
            Start-Process -FilePath $bios -WorkingDirectory 'C:\goldclub\bin' -WindowStyle Hidden
        }
    }

    # Wait for stub completion
    $wait = $WaitForClientSeconds + 20
    try { Wait-Process -Id $p.Id -Timeout $wait } catch {}
    if (-not $p.HasExited) {
        OL 'Stub still running - stopping'
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    $stubExit = $p.ExitCode
    OL ("Stub exit={0}" -f $stubExit)
    if (Test-Path $status) { OL ('STATUS: ' + (Get-Content $status -Raw)) }
    if (Test-Path $sendLog) { OL ("--- send log ---`n" + (Get-Content $sendLog -Raw)) }

    # Restore via XYNT service (not orphan Start-Process)
    OL 'Restoring Serial Communication Gateway service...'
    try {
        Start-Service -Name $gwSvcName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        $listen = [bool](Get-DallasListenPid)
        if (-not $listen) {
            OL 'Service up but :30800 missing — fallback Start-Process CommCtrl'
            if ($cmdLine -match 'CommCtrl\.exe"?\s+(.+)$') {
                Start-Process -FilePath $exePath -ArgumentList $Matches[1].Trim() -WorkingDirectory $workDir -WindowStyle Hidden
            } else {
                Start-Process -FilePath $exePath -ArgumentList '\goldclub\config\etc\application\CommCtrl\CommControler.ini' -WorkingDirectory $workDir -WindowStyle Hidden
            }
            Start-Sleep -Seconds 3
            $listen = [bool](Get-DallasListenPid)
        }
        OL ("CommCtrl restored listen30800={0}" -f $listen)
    } catch {
        OL ("RESTORE FAIL: {0}" -f $_.Exception.Message)
    }

    [pscustomobject]@{
        StubExit = $stubExit
        Status = $(if (Test-Path $status) { Get-Content $status -Raw } else { '' })
        SendLog = $(if (Test-Path $sendLog) { Get-Content $sendLog -Raw } else { '' })
        Listen30800 = [bool](Get-DallasListenPid)
        GatewayService = (Get-Service -Name $gwSvcName).Status.ToString()
    }
} -ArgumentList $Rom, $Action, $WaitForClientSeconds, [bool]$SkipBiosStart

Write-Host ''
Write-Host '--- Remote result ---' -ForegroundColor Cyan
$result | Format-List *
Write-Host 'Done.' -ForegroundColor Green