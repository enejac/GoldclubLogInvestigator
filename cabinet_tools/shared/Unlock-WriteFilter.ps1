<#
.SYNOPSIS
    Detect write-filter lock (UWF/EWF) on a GoldClub cabinet and disable it.

.DESCRIPTION
    GoldClub cabinets use Unified Write Filter (UWF) via goldclub.filter.1.
    Older docs call this "EWF lock". When enabled, disk writes are volatile until
    UWF is disabled and the machine reboots.

    Detection order:
      1. GoldClub module (Show-UWFState / Disable-UWF)
      2. uwfmgr.exe (native Windows UWF)
      3. ewfmgr.exe (legacy Enhanced Write Filter)

    Disable-UWF schedules filter off after reboot (GoldClub maintenance pattern).

.PARAMETER GoldclubRoot
    GoldClub install root (default C:\goldclub).

.PARAMETER Reboot
    Reboot after scheduling disable (recommended so writes persist).

.PARAMETER RebootSeconds
    Delay before reboot when -Reboot is set.

.PARAMETER WhatIf
    Report only; do not disable or reboot.

.EXAMPLE
    .\Unlock-WriteFilter.ps1

.EXAMPLE
    .\Unlock-WriteFilter.ps1 -Reboot -RebootSeconds 30
#>
#Requires -RunAsAdministrator
param(
    [string] $GoldclubRoot = 'C:\goldclub',
    [switch] $Reboot,
    [int] $RebootSeconds = 30,
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'
$ToolRoot = $PSScriptRoot
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$LogCandidates = @(
    (Join-Path $ToolRoot 'unlock-writefilter.log'),
    (Join-Path $GoldclubRoot 'var\log\unlock-writefilter.log'),
    (Join-Path $env:TEMP 'unlock-writefilter.log')
)
$LogPath = $LogCandidates | Where-Object { Test-Path (Split-Path $_ -Parent) -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $LogPath) { $LogPath = $LogCandidates[-1] }

function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    try {
        [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
    }
    catch {
        Write-Host "WARN: could not write log: $($_.Exception.Message)"
    }
}

function Find-Tool {
    param([string[]]$Names)
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        foreach ($dir in @(
            "$env:WINDIR\System32",
            "$env:WINDIR\Sysnative",
            (Join-Path $GoldclubRoot 'bin')
        )) {
            $path = Join-Path $dir $name
            if (Test-Path -LiteralPath $path) { return $path }
        }
    }
    return $null
}

function Get-GoldclubUwfState {
    $module = Join-Path $GoldclubRoot 'bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
    if (-not (Test-Path -LiteralPath $module)) { return $null }
    try {
        Import-Module $module -Force -ErrorAction Stop
        $state = Show-UWFState
        return [pscustomobject]@{
            Backend = 'GoldClub-UWF'
            State = [string]$state
            Locked = ($state -eq 'enabled')
            Detail = "module=$module"
        }
    }
    catch {
        return [pscustomobject]@{
            Backend = 'GoldClub-UWF'
            State = 'error'
            Locked = $null
            Detail = $_.Exception.Message
        }
    }
}

function Get-NativeUwfState {
    $uwf = Find-Tool @('uwfmgr.exe', 'uwfmgr')
    if (-not $uwf) { return $null }
    try {
        $out = & $uwf filter get-config 2>&1 | Out-String
        $locked = ($out -match '(?i)Filter\s+State\s*:\s*ON') -or ($out -match '(?i)CurrentEnabled\s*:\s*True')
        $state = if ($locked) { 'enabled' } else { 'disabled' }
        return [pscustomobject]@{
            Backend = 'uwfmgr'
            State = $state
            Locked = $locked
            Detail = $out.Trim()
        }
    }
    catch {
        return [pscustomobject]@{
            Backend = 'uwfmgr'
            State = 'error'
            Locked = $null
            Detail = $_.Exception.Message
        }
    }
}

function Get-LegacyEwfState {
    $ewf = Find-Tool @('ewfmgr.exe', 'ewfmgr')
    if (-not $ewf) { return $null }
    foreach ($vol in @('C:', 'D:', 'E:')) {
        try {
            $out = & $ewf $vol 2>&1 | Out-String
            if ($out -match '(?i)ENABLED|ACTIVE') {
                return [pscustomobject]@{
                    Backend = 'ewfmgr'
                    State = 'enabled'
                    Locked = $true
                    Detail = "${vol}: $($out.Trim())"
                }
            }
            if ($out -match '(?i)DISABLED|INACTIVE') {
                return [pscustomobject]@{
                    Backend = 'ewfmgr'
                    State = 'disabled'
                    Locked = $false
                    Detail = "${vol}: $($out.Trim())"
                }
            }
        }
        catch { continue }
    }
    return $null
}

function Get-FltmcHint {
    try {
        $out = fltmc filters 2>&1 | Out-String
        if ($out -match 'UWF|FBWF|EWF') {
            return $out.Trim()
        }
    }
    catch { }
    return $null
}

function Disable-GoldclubUwf {
    $module = Join-Path $GoldclubRoot 'bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
    Import-Module $module -Force
    $result = Disable-UWF | Out-String
    return $result.Trim()
}

function Disable-NativeUwf {
    $uwf = Find-Tool @('uwfmgr.exe', 'uwfmgr')
    if (-not $uwf) { throw 'uwfmgr not found' }
    $lines = @()
    $lines += (& $uwf filter disable 2>&1 | Out-String).Trim()
    $lines += (& $uwf servicing disable 2>&1 | Out-String).Trim()
    return ($lines -join '; ')
}

function Disable-LegacyEwf {
    $ewf = Find-Tool @('ewfmgr.exe', 'ewfmgr')
    if (-not $ewf) { throw 'ewfmgr not found' }
    $lines = @()
    foreach ($vol in @('C:', 'D:', 'E:')) {
        $lines += (& $ewf $vol /disable 2>&1 | Out-String).Trim()
    }
    return ($lines -join '; ')
}

[IO.File]::AppendAllText($LogPath, '', $Utf8) | Out-Null
Write-Log '=== Write filter check (EWF/UWF) ==='
Write-Log "Computer: $env:COMPUTERNAME"
Write-Log "Goldclub: $GoldclubRoot"
Write-Log "Log: $LogPath"

$status = Get-GoldclubUwfState
if (-not $status) {
    $status = Get-NativeUwfState
}
if (-not $status) {
    $status = Get-LegacyEwfState
}

if (-not $status) {
    $hint = Get-FltmcHint
    if ($hint) {
        Write-Log "WARN: filter drivers present but state unknown. fltmc:`n$hint"
    }
    Write-Log 'No UWF/EWF tooling found. Cabinet may have no write filter or tools are missing.'
    Write-Log '=== DONE (nothing to unlock) ==='
    exit 2
}

Write-Log ("Backend: {0}" -f $status.Backend)
Write-Log ("State:   {0}" -f $status.State)
Write-Log ("Locked:  {0}" -f $status.Locked)
if ($status.Detail) {
    foreach ($line in ($status.Detail -split "`n")) {
        if ($line.Trim()) { Write-Log "  $($line.Trim())" }
    }
}

if ($status.State -eq 'error') {
    throw "Could not read write-filter state ($($status.Backend)): $($status.Detail)"
}

if (-not $status.Locked) {
    Write-Log 'Write filter already disabled/unlocked in this session.'
    Write-Log 'Disk writes should persist. No reboot required.'
    Write-Log '=== DONE (already unlocked) ==='
    exit 0
}

Write-Log 'Write filter is LOCKED (enabled). Attempting disable...'

if ($WhatIf) {
    Write-Log "WhatIf: would disable $($status.Backend)"
    if ($Reboot) { Write-Log "WhatIf: would reboot in $RebootSeconds seconds" }
    Write-Log '=== DONE (WhatIf) ==='
    exit 0
}

$disableMsg = switch ($status.Backend) {
    'GoldClub-UWF' { Disable-GoldclubUwf }
    'uwfmgr' { Disable-NativeUwf }
    'ewfmgr' { Disable-LegacyEwf }
    default { throw "Unknown backend: $($status.Backend)" }
}
Write-Log "Disable result: $disableMsg"

if ($status.Backend -eq 'GoldClub-UWF') {
    Write-Log 'GoldClub UWF: disable takes effect after reboot (maintenance session).'
}

if ($Reboot) {
    $msg = 'Write filter disable scheduled - rebooting for maintenance session'
    Write-Log "Rebooting in $RebootSeconds seconds..."
    shutdown /r /t $RebootSeconds /f /c $msg
    Write-Log '=== DONE (reboot scheduled) ==='
    exit 10
}

Write-Log 'Disable scheduled. Reboot manually to enter unlocked maintenance session.'
Write-Log 'Run again with -Reboot to reboot automatically.'
Write-Log '=== DONE (disable scheduled, reboot needed) ==='
exit 10
