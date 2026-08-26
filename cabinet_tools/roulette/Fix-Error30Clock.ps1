# Cabinet-only: stop time sync, set clock before the Development trial lock,
# wipe Ruleta heap clock/password, relaunch. Keeps live XML + licence.dll.
# Never writes G:\ or any goldclub.vhd (mounted Ventura disk is off limits).
[CmdletBinding()]
param(
    [switch] $AlreadyElevated,
    [switch] $SkipKill,
    [switch] $SkipLaunch,
    [string] $TargetLocal = '2026-08-10 12:00:00'
)

$ErrorActionPreference = 'Continue'
$dstRoot = 'C:\goldclub\ruleta'
$log = 'D:\usb_scripts\roulette\fix-error30-clock.log'
$parseLocal = $TargetLocal
if ($TargetLocal -match '^\d{4}-\d{2}-\d{2}$') {
    $parseLocal = $TargetLocal + ' 12:00:00'
}
$target = [datetime]::Parse($parseLocal, [Globalization.CultureInfo]::InvariantCulture)

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try {
        [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    } catch {}
}

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

if ($dstRoot -notmatch '^[Cc]:\\goldclub\\ruleta') {
    throw 'refusing dest that is not C:\goldclub\ruleta'
}

if (-not $AlreadyElevated -and -not (Test-IsAdmin)) {
    $self = $MyInvocation.MyCommand.Path
    $helper = Join-Path $PSScriptRoot 'GoldClubElevate.ps1'
    if (Test-Path -LiteralPath $helper) { . $helper }
    $argList = @('-AlreadyElevated')
    if ($SkipKill) { $argList += '-SkipKill' }
    if ($SkipLaunch) { $argList += '-SkipLaunch' }
    $argList += '-TargetLocal'
    $argList += $TargetLocal
    if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {
        exit (Invoke-GoldClubSelfElevate -ScriptPath $self -ArgumentList $argList)
    }
    L 'ERROR: GoldClubElevate.ps1 missing'
    exit 1
}

L '=== Fix ERROR 30 clock (cabinet C: only, keep licence) ==='
L ("whoami={0} admin={1}" -f (whoami), (Test-IsAdmin))
L ("dest={0}" -f $dstRoot)
L ("targetLocal={0}" -f $target)

foreach ($letter in @('G', 'E')) {
    $vhd = '{0}:\goldclub.vhd' -f $letter
    if (Test-Path -LiteralPath $vhd) {
        L ("mounted VHD present {0} - will not write it" -f $vhd)
    }
}

if (-not $SkipKill) {
    $kill = Join-Path $PSScriptRoot 'Kill-All.ps1'
    L 'Kill-All'
    & $kill -AlreadyElevated
    Start-Sleep -Seconds 2
} else {
    L 'SkipKill (caller stops stack separately)'
}

try {
    Stop-Service -Name w32time -Force -ErrorAction SilentlyContinue
    Set-Service -Name w32time -StartupType Disabled -ErrorAction SilentlyContinue
    L 'w32time stopped+disabled'
} catch {
    L ("w32time: {0}" -f $_.Exception.Message)
}

try {
    Set-Date -Date $target
    L ("Set-Date now={0}" -f (Get-Date))
} catch {
    L ("ERROR Set-Date: {0}" -f $_.Exception.Message)
    exit 3
}

foreach ($name in @('HeapDataDateTime.dat', 'Password.dat', 'HeapDataDynamicPaytable.dat')) {
    $p = Join-Path $dstRoot ('var\' + $name)
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        L ("deleted {0}" -f $p)
    } else {
        L ("heap already gone {0}" -f $p)
    }
}

# RAM clear only wipes ruleta\var. Trial accept/expiry lives in persistent\
# (junctioned from var\state\ruleta\persistent). RouletteActivate.dat is
# written on SUCCEEDED; HeapDataFinanceStamps.dat is a Unix stamp of the
# last expire. Leaving those after a clock rollback makes 10.1 treat the
# clock as tampered and show LLAVE anyway.
$persist = Join-Path $dstRoot 'persistent'
foreach ($name in @('RouletteActivate.dat', 'RouletteStop.flag', 'HeapDataFinanceStamps.dat')) {
    $p = Join-Path $persist $name
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        L ("deleted {0}" -f $p)
    } else {
        L ("persist already gone {0}" -f $p)
    }
}

$xml = 'C:\goldclub\config\licences\37A55022DCBEF351AE27471D181B1EF5.xml'
$dll = Join-Path $dstRoot 'licence.dll'
L ("licence xml exists={0}" -f (Test-Path -LiteralPath $xml))
L ("licence.dll exists={0} size={1}" -f (Test-Path -LiteralPath $dll), $(if (Test-Path -LiteralPath $dll) { (Get-Item -LiteralPath $dll).Length } else { 0 }))

if ($SkipLaunch) {
    L 'SkipLaunch'
    exit 0
}

$run = Join-Path $PSScriptRoot 'Run-FullStack.ps1'
L 'Run-FullStack'
& $run -AlreadyElevated
$code = $LASTEXITCODE
L ("Run-FullStack exit={0}" -f $code)
exit $(if ($null -eq $code) { 0 } else { [int]$code })
