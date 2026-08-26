# Push D:\ConfigScanner\software_versions\Ruleta_v10.2.0.876_build40119
# onto C:\goldclub\ruleta. Keeps live licence XML and licence.dll.
# Deletes HeapDataDateTime.dat + Password.dat so LLAVE enter is not stale.
[CmdletBinding()]
param(
    [switch] $AlreadyElevated,
    [switch] $SkipLaunch
)

$ErrorActionPreference = 'Continue'
$srcRoot = 'D:\ConfigScanner\software_versions\Ruleta_v10.2.0.876_build40119'
$dstRoot = 'C:\goldclub\ruleta'
$log = 'D:\usb_scripts\roulette\push-102-876.log'

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

$files = @(
    'godot\RouletteGui.pck',
    'godot\.mono\assemblies\RouletteWebApiModels.dll',
    'godot\.mono\assemblies\RouletteGui2.dll',
    'lib\RouletteWebApiModels.dll',
    'lib\GoldClub.ManagedRendererWebApiServer.dll',
    'lib\GoldClub.ManagedRendererWebApiProxy.dll',
    'Ruleta.exe',
    'BuildVersion.txt'
)

L '=== Push Ruleta 10.2.0.876 (keep live licence) ==='
L ("source={0}" -f $srcRoot)
L ("dest={0}" -f $dstRoot)
L ("whoami={0} admin={1}" -f (whoami), (Test-IsAdmin))

foreach ($rel in $files) {
    $p = Join-Path $srcRoot $rel
    if (-not (Test-Path -LiteralPath $p)) {
        L ("ERROR missing {0}" -f $p)
        exit 2
    }
}

$kill = Join-Path $PSScriptRoot 'Kill-All.ps1'
L 'Kill-All'
& $kill -AlreadyElevated
Start-Sleep -Seconds 2

foreach ($rel in $files) {
    $src = Join-Path $srcRoot $rel
    $dst = Join-Path $dstRoot $rel
    $parent = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    L ("copied {0}" -f $rel)
}

foreach ($name in @('HeapDataDateTime.dat', 'Password.dat')) {
    $p = Join-Path $dstRoot ("var\" + $name)
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        L ("deleted {0}" -f $p)
    } else {
        L ("heap already gone {0}" -f $p)
    }
}

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
L ("licence.dll exists={0}" -f (Test-Path -LiteralPath $dll))
if (Test-Path -LiteralPath $dll) {
    $dllLen = (Get-Item -LiteralPath $dll).Length
    L ("licence.dll size={0} (must stay 12288, not 0157 AxProtector)" -f $dllLen)
}

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
