<#
.SYNOPSIS
    Kill-All — master stop for roulette game stack (merged Kill-GoldClubProcesses + Kill-ActiveGame).

.DESCRIPTION
    1) Suspend platform\user\shell.ps1 (keep alive; do not kill)
    2) Stop all GoldClub Windows services (unless -GameOnly)
    3) Kill relaunchers FIRST: HIH / game-start / Start-Game / ruleta
    4) Optional light helpers (bios/bootstrap/OneHand) unless -GameOnly
    5) Kill godot LAST + re-sweep (godot relaunches fast while relaunchers live)
    6) Stop nginx watcher / nginx.exe (unless -GameOnly)

    Does NOT kill Open-AdminShell or shell.ps1 itself.
    Use -GameOnly to leave services + nginx running (UI-only stop).

.EXAMPLE
    .\Kill-All.ps1
    .\Kill-All.ps1 -GameOnly
#>
[CmdletBinding()]
param(
    [switch] $WhatIf,
    [switch] $AlreadyElevated,
    [switch] $GameOnly
)

$ErrorActionPreference = 'Continue'

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

if (-not $WhatIf -and -not $AlreadyElevated -and -not (Test-IsAdmin)) {
    $self = $MyInvocation.MyCommand.Path
    if (-not $self) {
        Write-Host 'ERROR: not Administrator and cannot resolve script path for elevation.' -ForegroundColor Red
        exit 1
    }
    $helper = Join-Path $PSScriptRoot 'GoldClubElevate.ps1'
    if (Test-Path -LiteralPath $helper) { . $helper }
    $argList = @('-AlreadyElevated')
    if ($GameOnly) { $argList += '-GameOnly' }
    if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {
        exit (Invoke-GoldClubSelfElevate -ScriptPath $self -ArgumentList $argList)
    }
    Write-Host 'Not elevated - relaunching as Administrator (UAC)...' -ForegroundColor Yellow
    $psArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $self, '-AlreadyElevated'
    )
    if ($GameOnly) { $psArgs += '-GameOnly' }
    try {
        $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $psArgs -PassThru -Wait
        exit $(if ($null -ne $p) { $p.ExitCode } else { 1 })
    } catch {
        Write-Host ("ERROR: elevation failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host 'Secondary Logon/Appinfo may be disabled. Copy GoldClubElevate.ps1 next to this script or run from an admin/SYSTEM shell with -AlreadyElevated.' -ForegroundColor Yellow
        exit 1
    }
}

if (-not ('NativeMethods.ProcHold' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace NativeMethods {
  public static class ProcHold {
    [DllImport("ntdll.dll")] public static extern int NtSuspendProcess(IntPtr ProcessHandle);
    [DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr ProcessHandle);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr h);
    public const uint PROCESS_SUSPEND_RESUME = 0x0800;
  }
}
"@
}

function Get-PlatformShellProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^(powershell|pwsh)\.exe$' -and
        $_.CommandLine -and
        ($_.CommandLine -match '(?i)[\\/]platform[\\/]user[\\/]shell\.ps1') -and
        ($_.CommandLine -notmatch '(?i)Open-AdminShell') -and
        ($_.ProcessId -ne $PID)
    })
}

function Suspend-PlatformShell {
    $shells = @(Get-PlatformShellProcesses)
    if ($shells.Count -eq 0) {
        Write-Host 'platform\user\shell.ps1: not running (nothing to suspend)' -ForegroundColor DarkGray
        return
    }
    foreach ($s in $shells) {
        if ($WhatIf) {
            Write-Host ("WhatIf: would SUSPEND shell.ps1 pid={0}" -f $s.ProcessId) -ForegroundColor Yellow
            continue
        }
        $h = [NativeMethods.ProcHold]::OpenProcess([NativeMethods.ProcHold]::PROCESS_SUSPEND_RESUME, $false, [int]$s.ProcessId)
        if ($h -eq [IntPtr]::Zero) {
            Write-Host ("WARN: cannot open shell.ps1 pid={0} for suspend" -f $s.ProcessId) -ForegroundColor Yellow
            continue
        }
        try {
            for ($i = 0; $i -lt 4; $i++) {
                [void][NativeMethods.ProcHold]::NtSuspendProcess($h)
            }
            Write-Host ("  SUSPENDED platform shell.ps1 pid={0} (x4; not killed; Run-FullStack resumes)" -f $s.ProcessId) -ForegroundColor Cyan
        } finally {
            [void][NativeMethods.ProcHold]::CloseHandle($h)
        }
    }
}

function Test-ProtectedProcess {
    param($CimProcess)
    if ($CimProcess.ProcessId -eq $PID) { return $true }
    $cmd = [string]$CimProcess.CommandLine
    if ($cmd -match '(?i)Open-AdminShell\.ps1') { return $true }
    if ($cmd -match '(?i)GoldClub Admin Shell') { return $true }
    if ($cmd -match '(?i)[\\/]platform[\\/]user[\\/]shell\.ps1') { return $true }
    if ($CimProcess.Name -match '^(powershell|pwsh)\.exe$' -and $cmd -match '(?i)Kill-All|Run-FullStack|Start-GoldClubProcesses') { return $true }
    return $false
}

# Explicit allow-lists only (no goldclub path / Aurum / CommCtrl globs — those are service hosts).
$script:RelauncherNames = @(
    'HIH.exe', 'hih.exe',
    'game-start.exe', 'Start-Game.exe',
    'Ruleta.exe', 'ruleta.exe'
)
$script:HelperNames = @(
    'OneHand.exe',
    'Bootstrap.exe', 'bootstrap.exe',
    'BiOS2.exe', 'BiOS.exe', 'bios.exe'
)
$script:GodotNames = @(
    'godot.exe', 'Godot_v4.exe'
)

function Get-KillPhase {
    param([string] $Name)
    $n = [string]$Name
    foreach ($want in $script:RelauncherNames) {
        if ($n -ieq $want) { return 1 }
    }
    if ($n -like 'Godot*' -or ($script:GodotNames -contains $n) -or ($script:GodotNames | Where-Object { $_ -ieq $n })) {
        return 3
    }
    if ($n -like 'godot*') { return 3 }
    foreach ($want in $script:HelperNames) {
        if ($n -ieq $want) { return 2 }
    }
    return 0
}

function Test-IsTargetProcess {
    param(
        $CimProcess,
        [int[]] $Phases
    )
    if (Test-ProtectedProcess -CimProcess $CimProcess) { return $false }
    $phase = Get-KillPhase -Name $CimProcess.Name
    if ($phase -eq 0) { return $false }
    return ($Phases -contains $phase)
}

function Test-ProcessGone {
    param([int] $ProcessId)
    return -not [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-OneProcess {
    param(
        [Parameter(Mandatory = $true)][int] $ProcessId,
        [Parameter(Mandatory = $true)][string] $Name
    )
    # Race: child often exits when parent (game-start/ruleta) dies first.
    if (Test-ProcessGone -ProcessId $ProcessId) { return 'gone' }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        return 'stopped'
    } catch {
        if ($_.Exception.Message -match '(?i)cannot find a process|not found') { return 'gone' }
        if (Test-ProcessGone -ProcessId $ProcessId) { return 'gone' }
        # Avoid PowerShell NativeCommandError noise from taskkill stderr
        & cmd.exe /c "taskkill /F /T /PID $ProcessId 1>nul 2>nul" | Out-Null
        if ($LASTEXITCODE -eq 0) { return 'stopped' }
        if (Test-ProcessGone -ProcessId $ProcessId) { return 'gone' }
        throw ("stop failed PID {0} ({1}): {2}" -f $ProcessId, $Name, $_.Exception.Message)
    }
}

function Stop-TargetProcesses {
    param(
        [string] $Label,
        [int[]] $Phases
    )
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            Test-IsTargetProcess -CimProcess $_ -Phases $Phases
        })
    } catch {
        Write-Host ("Get-CimInstance failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        return @{ Killed = 0; Failed = 1 }
    }

    if ($procs.Count -eq 0) {
        Write-Host ("  {0}: none" -f $Label) -ForegroundColor DarkGray
        return @{ Killed = 0; Failed = 0 }
    }

    $procs = @($procs | Sort-Object { Get-KillPhase -Name $_.Name }, Name, ProcessId)
    Write-Host ("  {0}: {1} process(es)" -f $Label, $procs.Count) -ForegroundColor Cyan
    $procs | Format-Table @(
        @{ N = 'PID'; E = { $_.ProcessId } },
        @{ N = 'Phase'; E = { Get-KillPhase -Name $_.Name } },
        @{ N = 'Name'; E = { $_.Name } },
        @{ N = 'Path'; E = { $_.ExecutablePath } }
    ) -AutoSize

    if ($WhatIf) {
        Write-Host '  WhatIf: no processes stopped.' -ForegroundColor Yellow
        return @{ Killed = 0; Failed = 0 }
    }

    $killed = 0
    $failed = 0
    foreach ($pr in $procs) {
        try {
            $how = Stop-OneProcess -ProcessId $pr.ProcessId -Name $pr.Name
            $killed++
            if ($how -eq 'gone') {
                Write-Host ("  already gone PID {0} ({1}) phase={2}" -f $pr.ProcessId, $pr.Name, (Get-KillPhase -Name $pr.Name)) -ForegroundColor DarkGray
            } else {
                Write-Host ("  stopped PID {0} ({1}) phase={2}" -f $pr.ProcessId, $pr.Name, (Get-KillPhase -Name $pr.Name)) -ForegroundColor Green
            }
        } catch {
            # Final race check after exception
            if (Test-ProcessGone -ProcessId $pr.ProcessId) {
                $killed++
                Write-Host ("  already gone PID {0} ({1}) phase={2}" -f $pr.ProcessId, $pr.Name, (Get-KillPhase -Name $pr.Name)) -ForegroundColor DarkGray
            } else {
                $failed++
                Write-Host ("  FAILED PID {0} ({1}): {2}" -f $pr.ProcessId, $pr.Name, $_.Exception.Message) -ForegroundColor Red
            }
        }
    }
    return @{ Killed = $killed; Failed = $failed }
}

function Get-GodotLeft {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -and ($_.Name -like 'godot*' -or $_.Name -ieq 'Godot_v4.exe')
    })
}

function Get-RelauncherLeft {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $n = $_.Name
        foreach ($want in $script:RelauncherNames) {
            if ($n -ieq $want) { return $true }
        }
        return $false
    })
}

$gcSvcPath = Join-Path $PSScriptRoot 'GoldClubServices.ps1'
if (-not (Test-Path -LiteralPath $gcSvcPath)) {
    Write-Host ("ERROR: missing {0}" -f $gcSvcPath) -ForegroundColor Red
    exit 1
}
. $gcSvcPath
if (-not $script:GoldClubServiceNames -or @($script:GoldClubServiceNames).Count -lt 1) {
    Write-Host 'ERROR: GoldClubServices.ps1 did not load service names (file may be UTF-16). Re-copy UTF-8 GoldClubServices.ps1.' -ForegroundColor Red
    exit 1
}

function Stop-OneGoldClubService {
    param($Svc)
    if (-not $Svc) { return 'missing' }
    $key = $Svc.Name
    if ($Svc.Status -eq 'Stopped') { return 'already' }
    if ($WhatIf) {
        Write-Host ("  WhatIf: Stop-Service {0} (was {1})" -f $key, $Svc.Status) -ForegroundColor Yellow
        return 'whatif'
    }
    try {
        Write-Host ("  stopping: {0} / {1} (was {2}) ..." -f $Svc.Name, $Svc.DisplayName, $Svc.Status) -ForegroundColor Cyan
        Stop-Service -InputObject $Svc -Force -ErrorAction Stop
    } catch {
        & cmd.exe /c "sc.exe stop `"$key`" 1>nul 2>nul" | Out-Null
    }
    Start-Sleep -Milliseconds 500
    $again = Get-Service -Name $key -ErrorAction SilentlyContinue
    if ($again -and $again.Status -eq 'Stopped') {
        Write-Host ("  Stopped: {0}" -f $key) -ForegroundColor Green
        return 'stopped'
    }
    # Last resort: kill service process then sc stop
    try {
        $cim = Get-CimInstance Win32_Service -Filter ("Name='{0}'" -f ($key -replace "'", "''")) -ErrorAction SilentlyContinue
        if ($cim -and $cim.ProcessId -gt 0) {
            & cmd.exe /c "taskkill /F /PID $($cim.ProcessId) 1>nul 2>nul" | Out-Null
            Start-Sleep -Milliseconds 400
            & cmd.exe /c "sc.exe stop `"$key`" 1>nul 2>nul" | Out-Null
            Start-Sleep -Milliseconds 400
        }
    } catch { }
    $again = Get-Service -Name $key -ErrorAction SilentlyContinue
    if ($again -and $again.Status -eq 'Stopped') {
        Write-Host ("  Stopped (kill+sc): {0}" -f $key) -ForegroundColor Green
        return 'stopped'
    }
    $st = if ($again) { $again.Status } else { 'Missing' }
    Write-Host ("  FAILED still {0}: {1}" -f $st, $key) -ForegroundColor Red
    return 'failed'
}

function Stop-GoldClubServices {
    Write-Host 'Stopping GoldClub Windows services ...' -ForegroundColor Cyan
    $stopped = 0
    $failed = 0
    $missing = 0
    $seen = @{}

    # Primary: every live GoldClub* service (Name or DisplayName) — do not rely on list alone.
    $all = @(Get-AllGoldClubServices)
    if ($all.Count -eq 0) {
        Write-Host '  WARN: Get-Service found zero GoldClub* services' -ForegroundColor Yellow
    }
    foreach ($svc in $all) {
        $seen[$svc.Name] = $true
        $how = Stop-OneGoldClubService -Svc $svc
        if ($how -eq 'stopped') { $stopped++ }
        elseif ($how -eq 'failed') { $failed++ }
    }

    # Also walk the known name list (covers DisplayName-only matches / not yet in Get-All).
    foreach ($name in $script:GoldClubServiceNames) {
        $svc = Resolve-GoldClubService -NameOrDisplay $name
        if (-not $svc) {
            $missing++
            Write-Host ("  missing skip: {0}" -f $name) -ForegroundColor DarkGray
            continue
        }
        if ($seen.ContainsKey($svc.Name)) { continue }
        $seen[$svc.Name] = $true
        $how = Stop-OneGoldClubService -Svc $svc
        if ($how -eq 'stopped') { $stopped++ }
        elseif ($how -eq 'failed') { $failed++ }
    }
    return @{ Stopped = $stopped; Failed = $failed; Missing = $missing }
}

function Stop-NginxStack {
    if ($WhatIf) {
        Write-Host '  WhatIf: stop nginx + Start-NgnixAndWatcher' -ForegroundColor Yellow
        return
    }
    $watchers = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and ($_.CommandLine -match '(?i)Start-NgnixAndWatcher') -and -not (Test-ProtectedProcess -CimProcess $_)
    })
    foreach ($w in $watchers) {
        try {
            Stop-Process -Id $w.ProcessId -Force -ErrorAction Stop
            Write-Host ("  stopped nginx watcher pid={0}" -f $w.ProcessId) -ForegroundColor Green
        } catch {
            Write-Host ("  WARN nginx watcher pid={0}: {1}" -f $w.ProcessId, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    $nginx = @(Get-Process -Name 'nginx' -ErrorAction SilentlyContinue)
    foreach ($n in $nginx) {
        try {
            Stop-Process -Id $n.Id -Force -ErrorAction Stop
            Write-Host ("  stopped nginx.exe pid={0}" -f $n.Id) -ForegroundColor Green
        } catch {
            Write-Host ("  WARN nginx pid={0}: {1}" -f $n.Id, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    if ($watchers.Count -eq 0 -and $nginx.Count -eq 0) {
        Write-Host '  nginx: none' -ForegroundColor DarkGray
    }
}

Write-Host 'Kill-All: stopping GoldClub services + roulette processes ...' -ForegroundColor Cyan
Write-Host ("Elevated: {0}  User: {1}\{2}" -f (Test-IsAdmin), $env:USERDOMAIN, $env:USERNAME) -ForegroundColor DarkGray
Write-Host 'Order: suspend shell -> STOP SERVICES -> relaunchers -> helpers -> godot -> nginx' -ForegroundColor DarkGray
if ($GameOnly) { Write-Host 'Mode: -GameOnly (keep services/nginx; skip helpers)' -ForegroundColor DarkGray }
Write-Host ''

Write-Host '0) Suspend platform\user\shell.ps1 bootstrap loop (keep alive, do not kill) ...' -ForegroundColor Cyan
Suspend-PlatformShell
Write-Host ''

$totalKilled = 0
$totalFailed = 0
$svcFailed = 0

if (-not $GameOnly) {
    Write-Host '1) Stop GoldClub Windows services ...' -ForegroundColor Cyan
    $svcResult = Stop-GoldClubServices
    $svcFailed = [int]$svcResult.Failed
    Write-Host ("  services: stopped={0} failed={1} missing={2}" -f $svcResult.Stopped, $svcResult.Failed, $svcResult.Missing) -ForegroundColor $(if ($svcFailed) { 'Yellow' } else { 'Green' })
    Write-Host ''
} else {
    Write-Host '1) services skipped (-GameOnly)' -ForegroundColor DarkGray
    Write-Host ''
}

Write-Host '2) Kill relaunchers FIRST (HIH / game-start / ruleta) ...' -ForegroundColor Cyan
$r = Stop-TargetProcesses -Label 'relaunchers' -Phases @(1)
$totalKilled += $r.Killed; $totalFailed += $r.Failed
Start-Sleep -Milliseconds 400
Write-Host ''

if (-not $GameOnly) {
    Write-Host '3) Kill light helpers (bios / bootstrap / OneHand) ...' -ForegroundColor Cyan
    $r = Stop-TargetProcesses -Label 'helpers' -Phases @(2)
    $totalKilled += $r.Killed; $totalFailed += $r.Failed
    Start-Sleep -Milliseconds 400
    Write-Host ''
} else {
    Write-Host '3) helpers skipped (-GameOnly)' -ForegroundColor DarkGray
    Write-Host ''
}

Write-Host '4) Kill godot LAST + re-sweep (persistent / fast respawn) ...' -ForegroundColor Cyan
for ($pass = 1; $pass -le 4; $pass++) {
    Write-Host ("  pass {0}/4 ..." -f $pass) -ForegroundColor DarkGray
    $r = Stop-TargetProcesses -Label ("relauncher-resweep-$pass") -Phases @(1)
    $totalKilled += $r.Killed; $totalFailed += $r.Failed
    $r = Stop-TargetProcesses -Label ("godot-$pass") -Phases @(3)
    $totalKilled += $r.Killed; $totalFailed += $r.Failed
    Start-Sleep -Milliseconds 600
    $leftGodot = @(Get-GodotLeft)
    $leftRel = @(Get-RelauncherLeft)
    if ($leftGodot.Count -eq 0 -and $leftRel.Count -eq 0) {
        Write-Host '  clear: no godot / relaunchers left' -ForegroundColor Green
        break
    }
    Write-Host ("  still alive: godot={0} relaunchers={1}" -f $leftGodot.Count, $leftRel.Count) -ForegroundColor Yellow
}

if (-not $GameOnly) {
    Write-Host ''
    Write-Host '5) Stop nginx watcher / nginx.exe ...' -ForegroundColor Cyan
    Stop-NginxStack
}

Write-Host ''
Write-Host ("Done. processes stopped={0} failed={1}  services_failed={2}" -f $totalKilled, $totalFailed, $svcFailed) -ForegroundColor $(if ($totalFailed -or $svcFailed) { 'Yellow' } else { 'Green' })
Write-Host 'platform\user\shell.ps1 remains SUSPENDED (not killed). Use Run-FullStack.bat to resume + start game + services.' -ForegroundColor Cyan
if ($GameOnly) {
    Write-Host 'Note: -GameOnly left GoldClub services running.' -ForegroundColor DarkGray
}
exit $(if ($totalFailed -or $svcFailed) { 1 } else { 0 })
