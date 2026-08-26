# Cabinet-only: drop Ruleta trial tokens that survive RAM clear.
# Clock rollback is not enough if RouletteActivate.dat / FinanceStamps
# still hold the 10.2 SUCCEEDED + expire from 20 Aug.
# Never writes G:\ or goldclub.vhd. Does not touch licence XML/dll.
[CmdletBinding()]
param(
    [switch] $AlreadyElevated,
    [switch] $SkipLaunch
)

$ErrorActionPreference = 'Continue'
$dstRoot = 'C:\goldclub\ruleta'
$log = 'D:\usb_scripts\roulette\clear-trial-persistent.log'
$bakRoot = 'D:\ConfigScanner\backup-persistent-trial-20260820'

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
    if ($SkipLaunch) { $argList += '-SkipLaunch' }
    if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {
        exit (Invoke-GoldClubSelfElevate -ScriptPath $self -ArgumentList $argList)
    }
    L 'ERROR: GoldClubElevate.ps1 missing'
    exit 1
}

L '=== Clear trial persistent (C: only, keep licence) ==='
L ("whoami={0} admin={1} now={2}" -f (whoami), (Test-IsAdmin), (Get-Date))

foreach ($letter in @('G', 'E')) {
    $vhd = '{0}:\goldclub.vhd' -f $letter
    if (Test-Path -LiteralPath $vhd) {
        L ("mounted VHD present {0} - will not write it" -f $vhd)
    }
}

L 'Kill Ruleta/godot'
foreach ($im in @('Ruleta.exe', 'godot.exe', 'Godot_v4.exe')) {
    & cmd.exe /c ("taskkill /F /T /IM {0}" -f $im) | Out-Null
    L ("taskkill /IM {0} exit={1}" -f $im, $LASTEXITCODE)
}
Start-Sleep -Seconds 2

if (-not (Test-Path -LiteralPath $bakRoot)) {
    New-Item -ItemType Directory -Path $bakRoot -Force | Out-Null
}
$persist = Join-Path $dstRoot 'persistent'
$names = @('RouletteActivate.dat', 'RouletteStop.flag', 'HeapDataFinanceStamps.dat')
foreach ($name in $names) {
    $p = Join-Path $persist $name
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $bakRoot $name) -Force
        Remove-Item -LiteralPath $p -Force
        L ("backed up + deleted {0}" -f $p)
    } else {
        L ("already gone {0}" -f $p)
    }
}

$pwd = Join-Path $dstRoot 'var\Password.dat'
if (Test-Path -LiteralPath $pwd) {
    Copy-Item -LiteralPath $pwd -Destination (Join-Path $bakRoot 'Password.dat') -Force
    Remove-Item -LiteralPath $pwd -Force
    L ("backed up + deleted {0}" -f $pwd)
}

$xml = 'C:\goldclub\config\licences\37A55022DCBEF351AE27471D181B1EF5.xml'
$dll = Join-Path $dstRoot 'licence.dll'
L ("licence xml exists={0}" -f (Test-Path -LiteralPath $xml))
L ("licence.dll exists={0} size={1}" -f (Test-Path -LiteralPath $dll), $(if (Test-Path -LiteralPath $dll) { (Get-Item -LiteralPath $dll).Length } else { 0 }))

if ($SkipLaunch) {
    L 'SkipLaunch'
    exit 0
}

$exe = Join-Path $dstRoot 'Ruleta.exe'
L ("start {0}" -f $exe)
$p = Start-Process -FilePath $exe -WorkingDirectory $dstRoot -PassThru
if ($null -eq $p) {
    L 'ERROR: Start-Process returned nothing'
    exit 3
}
L ("started pid={0}" -f $p.Id)
Start-Sleep -Seconds 8
$up = @(Get-Process -Name Ruleta, ruleta -ErrorAction SilentlyContinue)
L ("ruleta processes: {0}" -f (($up | ForEach-Object { '{0}:{1}' -f $_.Name, $_.Id }) -join ', '))
if ($up.Count -eq 0) {
    L 'ERROR: Ruleta did not stay up'
    exit 4
}
exit 0
