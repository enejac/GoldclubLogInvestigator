#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Mount 10.2 goldclub.vhd then copy software (admin only).

.DESCRIPTION
    Use only when drives are NOT pre-mounted. On UAC-blocked PCs use
    Fetch-Roulette102Software.bat instead (pre-mounted R: + G:).
#>
param(
    [string] $SourceVhd,
    [string] $DestGoldclubRoot,
    [string] $MountLetter = 'R',
    [switch] $IncludePlatform,
    [switch] $WhatIf,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$ToolRoot = $PSScriptRoot
$LogPath = Join-Path $ToolRoot 'fetch-roulette102-software.log'
$Utf8 = [System.Text.UTF8Encoding]::new($false)

if (-not $SourceVhd) { $SourceVhd = 'P:\goldclub.vhd' }
if (-not $DestGoldclubRoot) { $DestGoldclubRoot = 'G:\' }

function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
}

function Get-FreeLetter([string]$Preferred) {
    if ($Preferred -and -not (Get-Volume -DriveLetter $Preferred -EA SilentlyContinue)) { return $Preferred }
    foreach ($l in 'R','S','T','U','V','W','X') {
        if (-not (Get-Volume -DriveLetter $l -EA SilentlyContinue)) { return $l }
    }
    throw 'No free drive letter'
}

Write-Log '=== ADMIN: mount VHD then copy ==='
if (-not (Test-Path -LiteralPath $SourceVhd)) {
    throw "Source VHD not found: $SourceVhd (Macrium Browse 10.2 GCDATA0 on P: first)"
}

$letter = Get-FreeLetter $MountLetter
Write-Log "Mount $SourceVhd -> ${letter}:\"
$mounted = $false
try {
    if (-not $WhatIf) {
        $disk = Mount-VHD -Path $SourceVhd -Passthru -NoDriveLetter
        $part = Get-Partition -DiskNumber $disk.DiskNumber |
            Where-Object { $_.Type -ne 'Reserved' } |
            Sort-Object Size -Descending | Select-Object -First 1
        if (-not $part) { throw 'No partition in VHD' }
        if (-not $part.DriveLetter) {
            Set-Partition -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -NewDriveLetter $letter
            Start-Sleep -Seconds 2
        }
        else { $letter = [string]$part.DriveLetter }
        $mounted = $true
    }

    $main = Join-Path $ToolRoot 'Fetch-Roulette102Software.ps1'
    $args = @(
        '-SourceGoldclubRoot', "${letter}:\",
        '-DestGoldclubRoot', $DestGoldclubRoot
    )
    if ($IncludePlatform) { $args += '-IncludePlatform' }
    if ($WhatIf) { $args += '-WhatIf' }
    if ($Force) { $args += '-Force' }

    & $main @args
    exit $LASTEXITCODE
}
finally {
    if ($mounted) {
        Write-Log 'Dismount source VHD'
        Dismount-VHD -Path $SourceVhd -ErrorAction SilentlyContinue
    }
}
