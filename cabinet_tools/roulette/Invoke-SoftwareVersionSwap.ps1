<#
.SYNOPSIS
    Surgical roulette software version swap (7 Frontend/Middleware/Backend files).

.DESCRIPTION
    Optional local kill + copy + Run-FullStack. Prefer the LogInvestigator GUI
    hybrid path (WinRM Kill-All / SMB copy / WinRM Run-FullStack) for large files.

.EXAMPLE
    .\Invoke-SoftwareVersionSwap.ps1 -SourceRuletaRoot D:\staging\10.2\ruleta -WhatIf
.EXAMPLE
    .\Invoke-SoftwareVersionSwap.ps1 -SourceRuletaRoot D:\staging\10.1\ruleta -AlreadyElevated
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $SourceRuletaRoot,
    [string] $DestRuletaRoot = 'C:\goldclub\ruleta',
    [switch] $SkipKill,
    [switch] $SkipLaunch,
    [switch] $WhatIf,
    [switch] $AlreadyElevated
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:SoftwareVersionFiles = @(
    'godot\RouletteGui.pck',
    'godot\.mono\assemblies\RouletteWebApiModels.dll',
    'godot\.mono\assemblies\RouletteGui2.dll',
    'lib\RouletteWebApiModels.dll',
    'lib\GoldClub.ManagedRendererWebApiServer.dll',
    'lib\GoldClub.ManagedRendererWebApiProxy.dll',
    'Ruleta.exe'
)
# Same WIBU dongle; pair the CodeMeter runtime with the exe when the pack has it.
$script:OptionalSoftwareVersionFiles = @(
    'licence.dll',
    'libeay32.dll',
    'ssleay32.dll'
)

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

if (-not $WhatIf -and -not $AlreadyElevated -and -not (Test-IsAdmin)) {
    $self = $MyInvocation.MyCommand.Path
    $argList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $self, '-AlreadyElevated',
        '-SourceRuletaRoot', $SourceRuletaRoot,
        '-DestRuletaRoot', $DestRuletaRoot
    )
    if ($SkipKill) { $argList += '-SkipKill' }
    if ($SkipLaunch) { $argList += '-SkipLaunch' }
    if ($WhatIf) { $argList += '-WhatIf' }
    $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList -PassThru -Wait
    exit $(if ($null -ne $p) { $p.ExitCode } else { 1 })
}

function Write-SwapLog([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
}

$srcRoot = (Resolve-Path -LiteralPath $SourceRuletaRoot).Path.TrimEnd('\')
$dstRoot = $DestRuletaRoot.TrimEnd('\')

Write-SwapLog ("SoftwareVersionSwap source={0}" -f $srcRoot)
Write-SwapLog ("SoftwareVersionSwap dest={0}" -f $dstRoot)

$missing = @()
foreach ($rel in $script:SoftwareVersionFiles) {
    $p = Join-Path $srcRoot $rel
    if (-not (Test-Path -LiteralPath $p)) { $missing += $rel }
}
if ($missing.Count -gt 0) {
    Write-SwapLog ('FAIL: missing source files: {0}' -f ($missing -join ', '))
    exit 2
}

$here = $PSScriptRoot
$killPs1 = Join-Path $here 'Kill-All.ps1'
$runPs1 = Join-Path $here 'Run-FullStack.ps1'

if (-not $SkipKill) {
    if (-not (Test-Path -LiteralPath $killPs1)) {
        Write-SwapLog "FAIL: Kill-All.ps1 not found next to this script"
        exit 3
    }
    if ($WhatIf) {
        Write-SwapLog 'WhatIf: would run Kill-All.ps1 -AlreadyElevated'
    } else {
        Write-SwapLog 'Running Kill-All.ps1 ...'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $killPs1 -AlreadyElevated
        if ($LASTEXITCODE -ne 0) {
            Write-SwapLog ("WARN: Kill-All exit={0} (continuing if game is down)" -f $LASTEXITCODE)
        }
    }
} else {
    Write-SwapLog 'SkipKill: not running Kill-All'
}

function Get-PeMachine([string]$Path) {
    $fs = [IO.File]::OpenRead($Path)
    try {
        $hdr = New-Object byte[] 64
        if ($fs.Read($hdr, 0, 64) -lt 64) { return $null }
        if ($hdr[0] -ne 0x4D -or $hdr[1] -ne 0x5A) { return $null }
        $eLfanew = [BitConverter]::ToInt32($hdr, 0x3C)
        $fs.Position = $eLfanew
        $pe = New-Object byte[] 6
        if ($fs.Read($pe, 0, 6) -lt 6) { return $null }
        if ($pe[0] -ne 0x50 -or $pe[1] -ne 0x45) { return $null }
        return [BitConverter]::ToUInt16($pe, 4)
    } finally {
        $fs.Dispose()
    }
}

$copyList = @($script:SoftwareVersionFiles)
foreach ($rel in $script:OptionalSoftwareVersionFiles) {
    $opt = Join-Path $srcRoot $rel
    if (-not (Test-Path -LiteralPath $opt)) { continue }
    if ($rel -match 'eay32\.dll$') {
        try { $machine = Get-PeMachine $opt } catch { $machine = $null }
        if ($machine -ne 0x8664) {
            Write-SwapLog ("skip optional {0} (not 64-bit PE, machine=0x{1:X})" -f $rel, $(if ($null -eq $machine) { 0 } else { $machine }))
            continue
        }
    }
    $copyList += $rel
    Write-SwapLog ("including optional {0}" -f $rel)
}

foreach ($rel in $copyList) {
    $src = Join-Path $srcRoot $rel
    $dst = Join-Path $dstRoot $rel
    $parent = Split-Path -Parent $dst
    if ($WhatIf) {
        Write-SwapLog ("WhatIf: copy {0} -> {1}" -f $rel, $dst)
        continue
    }
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $srcHash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
    if ($srcHash -ne $dstHash) {
        Write-SwapLog ("FAIL: hash mismatch after copy: {0}" -f $rel)
        exit 4
    }
    Write-SwapLog ("OK copy {0} sha256={1}" -f $rel, $srcHash.Substring(0, 12))
}

if (-not $SkipLaunch) {
    if (-not (Test-Path -LiteralPath $runPs1)) {
        Write-SwapLog "FAIL: Run-FullStack.ps1 not found next to this script"
        exit 5
    }
    if ($WhatIf) {
        Write-SwapLog 'WhatIf: would run Run-FullStack.ps1 -AlreadyElevated'
    } else {
        Write-SwapLog 'Running Run-FullStack.ps1 ...'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runPs1 -AlreadyElevated
        if ($LASTEXITCODE -ne 0) {
            Write-SwapLog ("FAIL: Run-FullStack exit={0}" -f $LASTEXITCODE)
            exit 6
        }
    }
} else {
    Write-SwapLog 'SkipLaunch: not running Run-FullStack'
}

Write-SwapLog 'DONE OK'
exit 0