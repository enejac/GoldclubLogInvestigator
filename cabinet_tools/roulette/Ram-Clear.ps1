# USB RAM Clear for roulette cabinets (double-click D:\RAM-CLEAR.cmd).
# Official chain: maintenance\tasks\ramclear.d (backup, cleanup, IdleMode).
# Matches Log Investigator: skip hang-prone 01-StopServices, skip ClearWibu
# (keep live licence XML / dongle), skip 51-LogDaemon during wipe, restore
# IdleMode.flag after ruleta\var clear. Never wipes all of var\state.
# Never edits serialport\layout.json or locations.json.
#
# Default: wipe then Run-FullStack. -SkipRestart = wipe only.
# -ClearWibu = also run official ClearWibu.exe --clear=49/50/52 (WIBU RAM
# slots, not the XML file). -WhatIf = print the plan, no deletes.
[CmdletBinding()]
param(
    [switch] $WhatIf,
    [switch] $AlreadyElevated,
    [switch] $SkipRestart,
    [switch] $ClearWibu,
    [switch] $SkipSoftMeters
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
        Write-Host 'ERROR: cannot elevate' -ForegroundColor Red
        exit 1
    }
    $helper = Join-Path $PSScriptRoot 'GoldClubElevate.ps1'
    if (Test-Path -LiteralPath $helper) { . $helper }
    $argList = @('-AlreadyElevated')
    if ($SkipRestart) { $argList += '-SkipRestart' }
    if ($ClearWibu) { $argList += '-ClearWibu' }
    if ($SkipSoftMeters) { $argList += '-SkipSoftMeters' }
    if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {
        exit (Invoke-GoldClubSelfElevate -ScriptPath $self -ArgumentList $argList)
    }
    Write-Host 'Not elevated - GoldClubElevate.ps1 missing. Run from Admin Shell.' -ForegroundColor Red
    exit 1
}

$script:LogPath = $null
foreach ($cand in @(
        (Join-Path $PSScriptRoot 'ram-clear.log'),
        'D:\usb_scripts\roulette\ram-clear.log',
        (Join-Path $env:PUBLIC 'ram-clear.log')
    )) {
    try {
        $dir = Split-Path -Parent $cand
        if (-not (Test-Path -LiteralPath $dir)) { continue }
        [void][IO.File]::AppendAllText($cand, '', [Text.UTF8Encoding]::new($false))
        $script:LogPath = $cand
        break
    } catch {}
}

function L {
    param([string] $m, [string] $color = 'Gray')
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line -ForegroundColor $color
    if ($script:LogPath) {
        try {
            [IO.File]::AppendAllText($script:LogPath, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        } catch {}
    }
}

function Test-ProtectedPath([string] $path) {
    if (-not $path) { return $true }
    if ($path -match '(?i)[\\/](config|bios)[\\/]licen[cs]es([\\/]|$)') { return $true }
    if ($path -match '(?i)licence\.dll$') { return $true }
    if ($path -match '(?i)serialport[\\/](layout|locations)\.json$') { return $true }
    return $false
}

function Find-GoldclubRoot {
    foreach ($cand in @('C:\goldclub', 'C:\Goldclub', 'G:\Goldclub', 'G:\')) {
        if (-not (Test-Path -LiteralPath $cand -PathType Container)) { continue }
        foreach ($mark in @('ruleta\Ruleta.exe', 'maintenance\tasks', 'var\state')) {
            if (Test-Path -LiteralPath (Join-Path $cand $mark)) { return $cand }
        }
    }
    return $null
}

function Find-RamClearFolder([string] $gc) {
    $names = @('ramclear.d', 'ramclear')
    $bases = @()
    if ($gc) { $bases += $gc }
    $bases += @('C:\goldclub', 'C:\Goldclub', 'G:\Goldclub', 'G:\')
    foreach ($base in $bases) {
        foreach ($name in $names) {
            $p = Join-Path $base ("maintenance\tasks\" + $name)
            if (Test-Path -LiteralPath $p -PathType Container) { return $p }
        }
    }
    return $null
}

function Clear-FolderContents([string] $dir) {
    if (Test-ProtectedPath $dir) {
        L ("REFUSE wipe (licence/serialport): {0}" -f $dir) 'Yellow'
        return
    }
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return }
    L ("Clearing: {0}" -f $dir)
    if ($WhatIf) { return }
    Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-ProtectedPath $_.FullName) {
            L ("REFUSE delete: {0}" -f $_.FullName) 'Yellow'
            return
        }
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-IdleModeFlag([string] $root) {
    $ruleta = Join-Path $root 'ruleta'
    if (-not (Test-Path -LiteralPath $ruleta -PathType Container)) { return }
    $flagDir = Join-Path $ruleta 'var\events'
    $flag = Join-Path $flagDir 'IdleMode.flag'
    L ("Restored IdleMode.flag: {0}" -f $flag)
    if ($WhatIf) { return }
    if (-not (Test-Path -LiteralPath $flagDir -PathType Container)) {
        New-Item -ItemType Directory -Path $flagDir -Force | Out-Null
    }
    $stamp = (Get-Date).ToString('o')
    [IO.File]::WriteAllText($flag, $stamp + [Environment]::NewLine)
}

L '=== USB RAM Clear (official ramclear.d, licences kept) ===' 'Cyan'
L 'Does NOT edit serialport maps. Does NOT overwrite licence XML or licence.dll.'
if ($ClearWibu) {
    L 'ClearWibu is ON (WIBU RAM slots 49/50/52). XML licences are still not deleted.' 'Yellow'
} else {
    L 'ClearWibu skipped (preserve licences / Wibu keys). Pass -ClearWibu to run it.'
}

$gc = Find-GoldclubRoot
if (-not $gc) {
    L 'ERROR: GoldClub root not found (C:\goldclub / G:\Goldclub).' 'Red'
    exit 2
}
L ("goldclub={0}" -f $gc)

$taskFolder = Find-RamClearFolder $gc
if ($taskFolder) {
    L ("ramclear folder={0}" -f $taskFolder)
} else {
    L 'WARN: ramclear.d not found - will still wipe official state targets.' 'Yellow'
}

if ($WhatIf) {
    L 'WhatIf: no processes stopped, no files deleted, stack not restarted.' 'Yellow'
}

if (-not $WhatIf) {
    $killAll = Join-Path $PSScriptRoot 'Kill-All.ps1'
    if (-not (Test-Path -LiteralPath $killAll)) {
        $killAll = Join-Path $gc 'bin\Kill-All.ps1'
    }
    if (Test-Path -LiteralPath $killAll) {
        L ("[START] Kill-All {0}" -f $killAll)
        try {
            & $killAll -AlreadyElevated
        } catch {
            L ("Kill-All: {0}" -f $_.Exception.Message) 'Yellow'
        }
        L '[END] Kill-All'
    } else {
        L '[START] pre-stop processes'
        foreach ($name in @('Bootstrap', 'BiOS2', 'Ruleta', 'godot', 'godot1', 'HIH')) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                L ("Stopping {0} pid={1}" -f $_.ProcessName, $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'goldclub*' } | ForEach-Object {
            L ("Stopping {0} pid={1}" -f $_.ProcessName, $_.Id)
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
        L '[END] pre-stop processes'
    }

    L '[START] pre-stop services (bounded)'
    Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'goldclub*' } | ForEach-Object {
        $svc = $_
        L ("Stopping service: {0}" -f $svc.Name)
        Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($svc.Status -ne 'Stopped' -and $sw.Elapsed.TotalSeconds -lt 20) {
            Start-Sleep -Milliseconds 500
            $svc.Refresh()
        }
        if ($svc.Status -ne 'Stopped') {
            L ("[WARN] service still {0}: {1}" -f $svc.Status, $svc.Name) 'Yellow'
            & sc.exe stop $svc.Name | Out-Null
        }
    }
    Start-Sleep -Seconds 1
    L '[END] pre-stop services (bounded)'
}

if ($taskFolder) {
    $bin = Join-Path $gc 'bin'
    if (Test-Path -LiteralPath $bin) { $env:PATH = "$bin;$env:PATH" }
    $tasksRoot = Split-Path -Parent $taskFolder
    $libPath = Join-Path $tasksRoot 'lib\powershell'
    if (Test-Path -LiteralPath $libPath) {
        $env:PSModulePath = $env:PSModulePath + ';' + $libPath
    }
    L '[START] ramclear chain (roulette dot-source)'
    $VerbosePreference = 'SilentlyContinue'
    foreach ($item in @([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)) {
        $leaf = Split-Path -Leaf $item
        if ($leaf -ieq '01-StopServices.ps1') {
            L ("[SKIP] {0} (bounded pre-stop already done)" -f $leaf)
            continue
        }
        if ($leaf -ieq '51-LogDaemon.ps1' -or $leaf -match '(?i)^51-LogDaemon') {
            L ("[SKIP] {0} (LogDaemonRamClear runs after stack restart)" -f $leaf)
            continue
        }
        if ($leaf -match '(?i)ClearWibu' -and -not $ClearWibu) {
            L ("[SKIP] {0} (preserve licences / Wibu keys)" -f $leaf)
            continue
        }
        L ("[START] {0}" -f $leaf)
        if ($WhatIf) {
            L ("[END] {0} (WhatIf)" -f $leaf)
            continue
        }
        try {
            & { . $item }
            L ("[END] {0}" -f $leaf)
        } catch {
            L ("[ERROR] {0}: {1}" -f $leaf, $_.Exception.Message) 'Yellow'
        }
    }
    L '[END] ramclear chain'
}

L '[START] ensure state wipe (official targets only)'
# Never treat D:\ (USB) as a GoldClub root. Never blank-wipe all of var\state.
$relFolders = @(
    'var\state\GoldClub.Aurum.Services',
    'var\state\goldclub.aurum.services',
    'var\state\hwsubsys',
    'var\state\GoldClub.Logging.LogDaemon',
    'var\state\GoldClub.Logging.LogDaemon.Plugin.FilteredEventLog',
    'var\state\OneHand',
    'var\cache',
    'services\aurum\var',
    'services\logdaemon\var',
    'ruleta\var',
    'ruleta\arhiv',
    'slot\var'
)
L ("Goldclub root: {0}" -f $gc)
foreach ($rel in $relFolders) {
    Clear-FolderContents (Join-Path $gc $rel)
}
$sasGlob = Join-Path $gc 'ruleta\online_sas\*.bin'
if (-not $WhatIf) {
    Get-Item -Path $sasGlob -ErrorAction SilentlyContinue | ForEach-Object {
        L ("Removing: {0}" -f $_.FullName)
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }
} else {
    L ("Would remove: {0}" -f $sasGlob)
}
Ensure-IdleModeFlag $gc
L '[END] ensure state wipe (official targets only)'

if ($SkipRestart) {
    L 'SkipRestart: stack not started. Run D:\_Run-FullStack.bat when ready.' 'Yellow'
    L '[END] USB RAM Clear'
    exit 0
}

$full = Join-Path $PSScriptRoot 'Run-FullStack.ps1'
if (-not (Test-Path -LiteralPath $full)) {
    $full = Join-Path $gc 'bin\Run-FullStack.ps1'
}
if ($WhatIf) {
    L ("WhatIf: would Run-FullStack {0}" -f $full)
    L '[END] USB RAM Clear'
    exit 0
}

if (Test-Path -LiteralPath $full) {
    L ("[START] Run-FullStack {0}" -f $full)
    & $full -AlreadyElevated
    $rc = $LASTEXITCODE
    L ("[END] Run-FullStack exit={0}" -f $rc)
} else {
    L 'WARN: Run-FullStack.ps1 not found - start the stack manually.' 'Yellow'
    $rc = 0
}

if (-not $SkipSoftMeters) {
    $stamp = Join-Path $gc 'bin\LogDaemonRamClear.exe'
    if (Test-Path -LiteralPath $stamp -PathType Leaf) {
        L ("Soft-meter stamp (0x7A) in background: {0}" -f $stamp)
        $run = Join-Path $env:PUBLIC 'Usb-RamClear-Stamp.ps1'
        $body = @(
            '$ErrorActionPreference = ''Continue'''
            'Start-Sleep -Seconds 45'
            "& '$stamp'"
        ) -join [Environment]::NewLine
        [IO.File]::WriteAllText($run, $body + [Environment]::NewLine)
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $run
        ) -WindowStyle Hidden | Out-Null
    } else {
        L 'LogDaemonRamClear.exe not found - soft meters SAS 0x7A skipped.'
    }
}

L '[END] USB RAM Clear'
exit $(if ($null -eq $rc) { 0 } else { [int]$rc })
