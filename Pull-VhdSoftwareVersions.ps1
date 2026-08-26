<#
.SYNOPSIS
    Mount goldclub.vhd and copy ruleta software into ConfigScanner software_versions.

.DESCRIPTION
    1. Attach E:\goldclub.vhd (or -VhdPath) read-only via Mount-GoldclubVhd.ps1
    2. Snapshot the 7 surgical binaries plus 64-bit libeay32/ssleay32
       into D:\ConfigScanner\software_versions\<Ruleta_v…>

    Requires elevation (diskpart). No internet. Destination needs ~50 MB free on D:.

.PARAMETER VhdPath
    Path to goldclub.vhd (default: auto-find, including E:\goldclub.vhd on GCDATA0).

.PARAMETER DestVersionsDir
    software_versions folder (default: D:\ConfigScanner\software_versions).

.PARAMETER MountLetter
    Preferred drive letter for mounted VHD interior (default R).

.PARAMETER DismountAfter
    Detach the VHD when finished (default: leave mounted for browsing).

.PARAMETER ForceNew
    Create a new _v2 pack even when the same version folder exists.

.EXAMPLE
    .\Pull-VhdSoftwareVersions.bat

.EXAMPLE
    .\Pull-VhdSoftwareVersions.ps1 -VhdPath E:\goldclub.vhd -DestVersionsDir D:\ConfigScanner\software_versions
#>
#Requires -RunAsAdministrator
param(
    [string] $VhdPath = '',
    [string] $DestVersionsDir = 'D:\ConfigScanner\software_versions',
    [string] $MountLetter = 'R',
    [switch] $DismountAfter,
    [switch] $ForceNew
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$LogPath = Join-Path $RepoRoot 'pull-vhd-software-versions.log'

function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
}

[IO.File]::WriteAllText($LogPath, '', $Utf8)
Write-Log '=== Pull-VhdSoftwareVersions ==='

if (-not $VhdPath) {
    foreach ($c in @('E:\goldclub.vhd', 'P:\goldclub.vhd', 'E:\Goldclub.vhd')) {
        if (Test-Path -LiteralPath $c) { $VhdPath = $c; break }
    }
}
if (-not $VhdPath -or -not (Test-Path -LiteralPath $VhdPath)) {
    throw "goldclub.vhd not found. Pass -VhdPath (e.g. E:\goldclub.vhd on GCDATA0 browse volume)."
}
Write-Log "VHD: $VhdPath"
Write-Log "Dest: $DestVersionsDir"

$destRoot = Split-Path -Parent $DestVersionsDir
if (-not (Test-Path -LiteralPath $destRoot)) {
    Write-Log "Creating $destRoot"
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
}
New-Item -ItemType Directory -Force -Path $DestVersionsDir | Out-Null

$freeMb = [math]::Round((Get-PSDrive -Name ($destRoot.Substring(0, 1)) -ErrorAction SilentlyContinue).Free / 1MB)
Write-Log "Free space on ${destRoot}: ~${freeMb} MB"
if ($freeMb -lt 40) {
    Write-Log "WARN: less than 40 MB free — pack needs ~20–50 MB; copy may fail"
}

$mountScript = Join-Path $RepoRoot 'Mount-GoldclubVhd.ps1'
if (-not (Test-Path -LiteralPath $mountScript)) {
    throw "Missing $mountScript"
}

$mountArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', $mountScript,
    '-VhdPath', $VhdPath,
    '-MountLetter', $MountLetter
)
Write-Log "Mounting VHD..."
& powershell.exe @mountArgs
if ($LASTEXITCODE -ne 0) {
    throw "Mount-GoldclubVhd failed (exit $LASTEXITCODE). See mount-goldclub-vhd.log"
}

$letter = $MountLetter
foreach ($vol in Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter }) {
    $l = [string]$vol.DriveLetter
    $root = "${l}:\"
    if (-not (Test-Path $root)) { continue }
    foreach ($m in @('ruleta\Ruleta.exe', 'goldclub\ruleta\Ruleta.exe')) {
        if (Test-Path -LiteralPath (Join-Path $root $m)) {
            $letter = $l
            break
        }
    }
}

$ruletaCandidates = @(
    "${letter}:\ruleta",
    "${letter}:\goldclub\ruleta",
    "${letter}:\Goldclub\ruleta"
)
$ruletaRoot = $null
foreach ($c in $ruletaCandidates) {
    if (Test-Path -LiteralPath (Join-Path $c 'Ruleta.exe')) {
        $ruletaRoot = $c
        break
    }
}
if (-not $ruletaRoot) {
    throw "Mounted ${letter}:\ but ruleta\Ruleta.exe not found. Open Disk Management or Explorer on the new drive letter."
}
Write-Log "Ruleta source: $ruletaRoot"

$pyScript = @"
import sys
from pathlib import Path
repo = Path(r'$RepoRoot')
sys.path.insert(0, str(repo))
from network.software_version_swap import snapshot_live_ruleta_package

dest = Path(r'$DestVersionsDir')
install = dest.parent

def progress(msg):
    print(msg, flush=True)

result = snapshot_live_ruleta_package(
    Path(r'$ruletaRoot'),
    install_root=install,
    progress=progress,
    force_new=$($ForceNew.IsPresent),
)
if result is None:
    sys.exit(2)
print('PACKAGE=' + str(result.path))
print('SKIPPED=' + str(result.skipped_existing))
sys.exit(0)
"@

$pyTmp = Join-Path $env:TEMP ("pull-sv-{0}.py" -f [guid]::NewGuid().ToString('N'))
[IO.File]::WriteAllText($pyTmp, $pyScript, $Utf8)
try {
    Write-Log 'Packaging software_versions (surgical binaries + 64-bit OpenSSL)...'
    $pyOut = & py -3 $pyTmp 2>&1 | ForEach-Object { "$_" }
    foreach ($line in $pyOut) { Write-Log "  $line" }
    if ($LASTEXITCODE -eq 2) {
        throw 'Ruleta tree incomplete (missing required binaries). See log.'
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python packaging failed (exit $LASTEXITCODE)"
    }
    $pkgLine = $pyOut | Where-Object { $_ -match '^PACKAGE=' } | Select-Object -Last 1
    if ($pkgLine) {
        Write-Log "Done: $($pkgLine.Substring(8))"
    }
}
finally {
    Remove-Item -LiteralPath $pyTmp -Force -ErrorAction SilentlyContinue
}

if ($DismountAfter) {
    Write-Log 'Dismounting VHD...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $mountScript -VhdPath $VhdPath -Dismount
}

Write-Log '=== DONE ==='
Write-Log "Browse packs: $DestVersionsDir"
exit 0
