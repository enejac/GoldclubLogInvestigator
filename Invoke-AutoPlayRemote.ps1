<#
.SYNOPSIS
    Remote launcher for the AutoPlay keystroke harness on a OneHand EGM.

.DESCRIPTION
    Stages AutoPlay.exe to the cabinet, launches it inside the interactive
    console session (where the Unity game renders) via PsExec, and verifies that
    spins actually registered by diffing the OneHand GameData log before/after.

    AutoPlay presses ordinary game buttons (Spin / Bet+ / Bet- / MaxBet) by
    sending real keyboard input to the foreground window. It performs no memory
    writes and no value injection -- it is a volume generator for math/RTP QA.

.EXAMPLE
    # Phase A: prove a single spin lands (game must be on screen, credits loaded)
    .\Invoke-AutoPlayRemote.ps1 -ComputerName 10.0.0.90 -Action spin -Count 1 -Verify

.EXAMPLE
    # Set bet to step 5 then spin 50 times, detached, self-paced
    .\Invoke-AutoPlayRemote.ps1 -ComputerName 10.0.0.90 -Seq "betminus*9,betplus*4,spin" -Count 50 -DelayMs 1800 -Detach
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [ValidateSet('spin','betplus','betminus','maxbet','collect','gameselect')]
    [string] $Action = 'spin',
    [int]    $Count = 1,
    [int]    $DelayMs = 1500,
    [int]    $PreDelayMs = 1500,
    [int]    $HoldMs = 60,
    [ValidateSet('vk','scan')]
    [string] $KeyMode = 'scan',
    [ValidateSet('enter','p','w')]
    [string] $SpinKey = 'enter',
    [int]    $FastStopMs = 0,
    [string] $Seq = '',
    [int]    $Session = 1,
    [string] $TargetProcess = 'OneHand',
    [string] $TargetClass = '',
    [string] $TargetTitle = '',
    [switch] $ListWindows,
    [switch] $NoFocus,
    [switch] $DryRun,
    [switch] $Detach,
    [switch] $Verify,
    [switch] $Rebuild,
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [string] $RemoteStageDir = 'C:\Windows\Temp',
    [string] $RemoteCsc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
)

$ErrorActionPreference = 'Stop'

function Resolve-ScriptDir {
    if ($PSScriptRoot) { return $PSScriptRoot }
    return (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

# PsExec always writes its connection banner to stderr; under EAP=Stop that is
# treated as a terminating NativeCommandError. Run it with relaxed error action
# and surface output as plain host lines.
function Invoke-PsExec {
    param([string[]] $PsArgs, [string] $Tag = 'psexec')
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $PsExecPath @PsArgs 2>&1 | ForEach-Object {
            $s = "$_"
            if ($s -and $s -notmatch 'RemoteException') { Write-Host "  [$Tag] $s" }
        }
    } finally {
        $ErrorActionPreference = $old
    }
}

# NOTE: We never compile AutoPlay.exe on the workstation -- an EDR (CrowdStrike)
# quarantines freshly built SendInput tools. Instead we ship the harmless C#
# source to the cabinet and compile it there (the EGM has csc and no EDR).
$scriptDir = Resolve-ScriptDir
$localSrc  = Join-Path $scriptDir 'AutoPlay.cs'
if (-not (Test-Path $localSrc)) {
    throw "AutoPlay.cs not found next to this script ($localSrc)."
}
if (-not (Test-Path $PsExecPath)) {
    throw "PsExec not found at $PsExecPath"
}

$remoteSrc    = Join-Path $RemoteStageDir 'AutoPlay.cs'
$remoteExe    = Join-Path $RemoteStageDir 'AutoPlay.exe'
$remoteLog    = Join-Path $RemoteStageDir 'autoplay.log'
$adminSrc     = "\\$ComputerName\" + ($remoteSrc -replace ':','$')
$adminExe     = "\\$ComputerName\" + ($remoteExe -replace ':','$')
$adminLog     = "\\$ComputerName\" + ($remoteLog -replace ':','$')
$gameDataDir  = "\\$ComputerName\c`$\Goldclub\var\log\OneHand GameData"

function Get-NewestGameDataLog {
    if (-not (Test-Path $gameDataDir)) { return $null }
    Get-ChildItem -Path $gameDataDir -Filter '*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

function Get-SpinSnapshot {
    $log = Get-NewestGameDataLog
    if (-not $log) { return [pscustomobject]@{ File=$null; Count=0; Last='' } }
    $lines = Get-Content -LiteralPath $log.FullName -ErrorAction SilentlyContinue
    $game  = $lines | Where-Object { $_ -match '\]\s+G:' }
    $last  = if ($game) { $game[-1] } else { '' }
    [pscustomobject]@{ File=$log.FullName; Count=($game | Measure-Object).Count; Last=$last }
}

Write-Host "=== AutoPlay remote launch ===" -ForegroundColor Cyan
Write-Host ("Target      : {0} (session {1})" -f $ComputerName, $Session)

# 1) Stage source + compile on the cabinet (if needed)
Write-Host "Staging     : $localSrc -> $adminSrc"
Copy-Item -LiteralPath $localSrc -Destination $adminSrc -Force
Remove-Item -LiteralPath $adminLog -Force -ErrorAction SilentlyContinue

if ($Rebuild -or -not (Test-Path $adminExe)) {
    Write-Host "Compiling   : csc on $ComputerName -> $remoteExe" -ForegroundColor Yellow
    Remove-Item -LiteralPath $adminExe -Force -ErrorAction SilentlyContinue
    $cscArgs = @("\\$ComputerName", '-accepteula', '-nobanner', '-s', $RemoteCsc,
                 '/nologo', '/platform:x64', '/target:winexe', "/out:$remoteExe", $remoteSrc)
    Invoke-PsExec -PsArgs $cscArgs -Tag 'csc'
    Start-Sleep -Seconds 2
    if (-not (Test-Path $adminExe)) {
        throw "Remote compile failed: $adminExe was not produced."
    }
    Write-Host ("Compiled    : {0} bytes" -f (Get-Item -LiteralPath $adminExe).Length) -ForegroundColor Green
} else {
    Write-Host "Reusing     : existing $remoteExe (use -Rebuild to force)"
}

# 2) Before snapshot
$before = $null
if ($Verify) {
    $before = Get-SpinSnapshot
    Write-Host ("Before      : {0} game records in {1}" -f $before.Count, (Split-Path -Leaf ($before.File)))
}

# 3) Build remote argument list
$exeArgs = @()
if ($ListWindows) {
    $exeArgs += '--list-windows'
} elseif ($Seq) {
    $exeArgs += @('--seq', $Seq)
    if ($Count -gt 1) {
        # repeat the whole seq Count times by chaining waits between iterations
        $repeated = (1..$Count | ForEach-Object { $Seq }) -join (',wait{0},' -f $DelayMs)
        $exeArgs = @('--seq', $repeated)
    }
} else {
    $exeArgs += @('--action', $Action, '--count', $Count, '--delay-ms', $DelayMs)
}
$exeArgs += @('--pre-delay-ms', $PreDelayMs, '--hold-ms', $HoldMs, '--key-mode', $KeyMode, '--spin-key', $SpinKey, '--fast-stop-ms', $FastStopMs, '--log', $remoteLog)
if ($TargetProcess) { $exeArgs += @('--target-process', $TargetProcess) }
if ($TargetClass) { $exeArgs += @('--target-class', $TargetClass) }
if ($TargetTitle) { $exeArgs += @('--target-title', $TargetTitle) }
if ($NoFocus) { $exeArgs += '--no-focus' }
if ($DryRun)  { $exeArgs += '--dry-run' }

# 4) Launch via PsExec inside the interactive session
$psArgs = @("\\$ComputerName", '-accepteula', '-nobanner', '-s', '-i', $Session)
if ($Detach) { $psArgs += '-d' }
$psArgs += $remoteExe
$psArgs += $exeArgs

Write-Host ("Command     : AutoPlay.exe {0}" -f ($exeArgs -join ' '))
Write-Host "Launching   : PsExec -s -i $Session ..." -ForegroundColor Yellow
Invoke-PsExec -PsArgs $psArgs -Tag 'psexec'

# 5) After snapshot / verification
if ($Verify) {
    $expected = if ($ListWindows) { 0 } elseif ($Seq) { ($Seq.Split(',') | Where-Object { $_ -match '(?i)spin' }).Count * [Math]::Max(1,$Count) } else { if ($Action -eq 'spin') { $Count } else { 0 } }
    $settle = [Math]::Max(3, [int]($PreDelayMs/1000) + 3)
    Write-Host "Settling    : ${settle}s for log flush..."
    Start-Sleep -Seconds $settle
    $after = Get-SpinSnapshot
    $delta = $after.Count - $before.Count
    Write-Host ("After       : {0} game records (delta = {1}, expected ~{2})" -f $after.Count, $delta, $expected) -ForegroundColor Green
    if ($after.Last -and $after.Last -ne $before.Last) {
        Write-Host "Last spin   : $($after.Last)"
    }
    if ($delta -le 0) {
        Write-Host "WARNING: no new spins detected. Check that the game is on screen, foreground, and has credits." -ForegroundColor Red
    }
}

# 6) Pull back the harness log
if (Test-Path $adminLog) {
    Write-Host "--- AutoPlay log (remote) ---" -ForegroundColor Cyan
    Get-Content -LiteralPath $adminLog | ForEach-Object { Write-Host "  $_" }
}

Write-Host "=== done ===" -ForegroundColor Cyan
