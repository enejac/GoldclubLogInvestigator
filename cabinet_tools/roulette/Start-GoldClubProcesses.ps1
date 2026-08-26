<#
.SYNOPSIS
    Start roulette GoldClub game stack via HIH.exe (preferred).

.DESCRIPTION
    Ensures key GoldClub Windows services are Running (starts if Stopped),
    then launches C:\goldclub\bin\HIH.exe which relaunches the game via
    RUN_GAME → game-start. Does NOT start Bootstrap (slot START_ONEHAND style).

    Refuses to start HIH if platform\user\shell.ps1 is already running (that shell
    owns the HIH/game chain; a second HIH causes dual Godot). Use -Force to override,
    or Run-FullStack.bat to resume the shell instead.

    Fallback order if HIH.exe is missing: Start-Game.exe, then game-start.exe
    in the same bin folder.

.EXAMPLE
    .\Start-GoldClubProcesses.ps1
    .\Start-GoldClubProcesses.ps1 -WhatIf
    .\Start-GoldClubProcesses.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch] $WhatIf,
    [switch] $Force,
    [string] $BinDir = 'C:\goldclub\bin'
)

$ErrorActionPreference = 'Continue'

$gcSvcPath = Join-Path $PSScriptRoot 'GoldClubServices.ps1'
if (-not (Test-Path -LiteralPath $gcSvcPath)) {
    Write-Host ("ERROR: missing {0}" -f $gcSvcPath) -ForegroundColor Red
    exit 1
}
. $gcSvcPath
if (-not $script:GoldClubServiceNames -or @($script:GoldClubServiceNames).Count -lt 1) {
    Write-Host 'ERROR: GoldClubServices.ps1 did not load (UTF-16?). Re-copy UTF-8 file beside this script.' -ForegroundColor Red
    exit 1
}
# Same full service set as Kill-All / Run-FullStack (merged list).
$serviceNames = @($script:GoldClubServiceNames)

Write-Host 'Starting GoldClub / roulette processes ...' -ForegroundColor Cyan
Write-Host ("Bin: {0}" -f $BinDir) -ForegroundColor DarkGray
Write-Host ''

function Ensure-GcService {
    param([string] $Name)
    $svc = Resolve-GoldClubService -NameOrDisplay $Name
    if (-not $svc) {
        Write-Host ("  service missing (skip): {0}" -f $Name) -ForegroundColor DarkYellow
        return
    }
    if ($svc.Status -eq 'Running') {
        Write-Host ("  service already Running: {0}" -f $Name) -ForegroundColor Green
        return
    }
    if ($WhatIf) {
        Write-Host ("  WhatIf: would Start-Service {0} (was {1})" -f $Name, $svc.Status) -ForegroundColor Yellow
        return
    }
    try {
        Write-Host ("  starting service: {0} (was {1}) ..." -f $svc.Name, $svc.Status) -ForegroundColor Cyan
        Start-Service -InputObject $svc -ErrorAction Stop
        $svc.Refresh()
        Write-Host ("  service now {0}: {1}" -f $svc.Status, $svc.Name) -ForegroundColor Green
    } catch {
        Write-Host ("  FAILED Start-Service {0}: {1}" -f $svc.Name, $_.Exception.Message) -ForegroundColor Red
    }
}

$shells = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '^(powershell|pwsh)\.exe$' -and
    $_.CommandLine -and
    ($_.CommandLine -match '(?i)[\\/]platform[\\/]user[\\/]shell\.ps1') -and
    ($_.CommandLine -notmatch '(?i)Open-AdminShell')
})
if ($shells.Count -gt 0 -and -not $Force) {
    Write-Host ("platform\user\shell.ps1 already running (pid={0})." -f (($shells | ForEach-Object ProcessId) -join ',')) -ForegroundColor Yellow
    Write-Host 'Not starting a second HIH/game-start (causes dual Godot).' -ForegroundColor Yellow
    Write-Host 'Use Run-FullStack.bat to resume the shell, or re-run with -Force.' -ForegroundColor DarkGray
    exit 2
}
if ($shells.Count -gt 0 -and $Force) {
    Write-Host 'WARNING: -Force with live shell.ps1 — dual Godot risk.' -ForegroundColor Yellow
}

Write-Host 'Checking GoldClub services ...' -ForegroundColor Cyan
foreach ($sn in $serviceNames) {
    Ensure-GcService -Name $sn
}
Write-Host ''

$hih = Get-Process -Name 'HIH' -ErrorAction SilentlyContinue
if ($hih) {
    $hih |
        Format-Table Id, ProcessName, @{ N = 'MB'; E = { [math]::Round($_.WorkingSet64 / 1MB, 1) } } -AutoSize
    Write-Host 'HIH.exe is already running — not starting another instance.' -ForegroundColor Yellow
    exit 0
}

$candidates = @(
    (Join-Path $BinDir 'HIH.exe'),
    (Join-Path $BinDir 'Start-Game.exe'),
    (Join-Path $BinDir 'game-start.exe')
)

$exe = $null
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) {
        $exe = $c
        break
    }
}

if (-not $exe) {
    Write-Host ("ERROR: none of HIH.exe / Start-Game.exe / game-start.exe found under {0}" -f $BinDir) -ForegroundColor Red
    exit 1
}

$prefer = Join-Path $BinDir 'HIH.exe'
if ($exe -ine $prefer) {
    Write-Host ("HIH.exe not found; falling back to: {0}" -f $exe) -ForegroundColor Yellow
}

if ($WhatIf) {
    Write-Host ("WhatIf: would Start-Process '{0}' WorkingDirectory='{1}'" -f $exe, $BinDir) -ForegroundColor Yellow
    exit 0
}

try {
    Write-Host ("Starting: {0}" -f $exe) -ForegroundColor Cyan
    Start-Process -FilePath $exe -WorkingDirectory $BinDir
    Start-Sleep -Milliseconds 800
    $started = Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($exe)) -ErrorAction SilentlyContinue
    if ($started) {
        Write-Host ("Started OK. PID(s): {0}" -f (($started | ForEach-Object Id) -join ', ')) -ForegroundColor Green
    } else {
        Write-Host 'Start-Process returned; process not visible yet (may launch child then exit).' -ForegroundColor Yellow
    }
    exit 0
} catch {
    Write-Host ("FAILED to start {0}: {1}" -f $exe, $_.Exception.Message) -ForegroundColor Red
    exit 1
}
