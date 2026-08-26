<#
.SYNOPSIS
    Mount goldclub.vhd (Macrium GCDATA0 browse) for Explorer browsing.

.DESCRIPTION
    Uses diskpart (no Hyper-V Mount-VHD required). Attaches read-only by default
    because Macrium Browse volumes are usually write-protected.

.PARAMETER VhdPath
    Full path to goldclub.vhd (default: auto-find on P:\ or any drive).

.PARAMETER MountLetter
    Preferred drive letter (default R). Falls back to next free letter if taken.

.PARAMETER ReadWrite
    Attach writable (fails on Macrium read-only browse -- use only on a copied VHD).

.PARAMETER Dismount
    Detach the VHD instead of mounting.

.PARAMETER WhatIf
    Show planned actions only.

.EXAMPLE
    .\Mount-GoldclubVhd.ps1

.EXAMPLE
    .\Mount-GoldclubVhd.ps1 -VhdPath P:\goldclub.vhd -MountLetter G

.EXAMPLE
    .\Mount-GoldclubVhd.ps1 -Dismount
#>
#Requires -RunAsAdministrator
param(
    [string] $VhdPath,
    [string] $MountLetter = 'R',
    [switch] $ReadWrite,
    [switch] $Dismount,
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$LogPath = Join-Path $RepoRoot 'mount-goldclub-vhd.log'
$Utf8 = [System.Text.UTF8Encoding]::new($false)

function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
}

function Invoke-DiskPart([string[]]$Commands) {
    $tmp = Join-Path $env:TEMP ("mount-gc-{0}.txt" -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $Utf8)
        Write-Log ('diskpart: ' + ($Commands -join '; '))
        $out = cmd /c "diskpart /s `"$tmp`"" 2>&1 | ForEach-Object { "$_" }
        foreach ($line in $out) { Write-Log "  $line" }
        return ($out -join "`n")
    }
    finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Find-GoldclubVhd {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($VhdPath) { $candidates.Add($VhdPath) }
    foreach ($letter in @('P', 'O', 'N', 'M', 'L', 'K', 'J', 'I', 'H', 'G', 'F', 'E', 'D')) {
        $candidates.Add("${letter}:\goldclub.vhd")
        $candidates.Add("${letter}:\Goldclub.vhd")
        $candidates.Add("${letter}:\GOLDCLUB.VHD")
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            return (Resolve-Path -LiteralPath $c).Path
        }
    }
    return $null
}

function Get-FreeLetter([string]$Preferred) {
    $pref = ($Preferred -replace '[:\\]', '').ToUpperInvariant()
    if ($pref -and -not (Get-Volume -DriveLetter $pref -ErrorAction SilentlyContinue)) {
        return $pref
    }
    foreach ($l in 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'G') {
        if (-not (Get-Volume -DriveLetter $l -ErrorAction SilentlyContinue)) { return $l }
    }
    throw 'No free drive letter available'
}

function Test-GoldclubMounted([string]$Letter) {
    $root = "${Letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { return $false }
    foreach ($m in @('ruleta', 'config', 'apps', 'bin')) {
        if (Test-Path -LiteralPath (Join-Path $root $m)) { return $true }
    }
    $vol = Get-Volume -DriveLetter $Letter -ErrorAction SilentlyContinue
    if ($vol -and $vol.FileSystemLabel -match 'GOLDCLUB') { return $true }
    return $false
}

function Find-AlreadyMountedLetter {
    foreach ($vol in Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter }) {
        $letter = [string]$vol.DriveLetter
        if (Test-GoldclubMounted $letter) { return $letter }
    }
    return $null
}

function Get-VolumesSnapshot {
    $map = @{}
    Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object {
        $map[[string]$_.DriveLetter] = $true
    }
    return $map
}

[IO.File]::WriteAllText($LogPath, '', $Utf8)
Write-Log '=== Mount-GoldclubVhd ==='

$vhd = Find-GoldclubVhd
if (-not $vhd) {
    throw 'goldclub.vhd not found. Browse Macrium GCDATA0 first (e.g. P:\goldclub.vhd) or pass -VhdPath.'
}
Write-Log "VHD: $vhd"

if ($Dismount) {
    if ($WhatIf) {
        Write-Log "WhatIf: would detach $vhd"
        exit 0
    }
    $out = Invoke-DiskPart @(
        "select vdisk file=`"$vhd`"",
        'detach vdisk'
    )
    if ($out -match '(?i)successfully|detached|is not attached') {
        Write-Log '=== DONE (detached) ==='
        exit 0
    }
    throw 'Detach failed -- see log'
}

$existing = Find-AlreadyMountedLetter
if ($existing) {
    Write-Log "Already mounted as ${existing}:\ -- open Explorer there"
    Write-Log '=== DONE (already mounted) ==='
    exit 0
}

$letter = Get-FreeLetter $MountLetter
$attachMode = if ($ReadWrite) { 'attach vdisk' } else { 'attach vdisk readonly' }
Write-Log "Target letter: ${letter}:  mode: $attachMode"

if ($WhatIf) {
    Write-Log 'WhatIf: would attach and assign letter'
    exit 0
}

$before = Get-VolumesSnapshot
$attachOut = Invoke-DiskPart @(
    "select vdisk file=`"$vhd`"",
    $attachMode
)

if ($attachOut -match '(?i)write protected' -and $ReadWrite) {
    Write-Log 'Write attach failed (write protected). Retrying readonly...'
    $attachOut = Invoke-DiskPart @(
        "select vdisk file=`"$vhd`"",
        'attach vdisk readonly'
    )
}

if ($attachOut -match '(?i)write protected' -and -not $ReadWrite) {
    # Some hosts still fail first readonly attempt; retry once
    Start-Sleep -Seconds 2
    $attachOut = Invoke-DiskPart @(
        "select vdisk file=`"$vhd`"",
        'attach vdisk readonly'
    )
}

if ($attachOut -match '(?i)error|failed' -and $attachOut -notmatch '(?i)successfully attached') {
    # "successfully" check
    if ($attachOut -notmatch '(?i)successfully attached') {
        throw "Attach failed. If media is write-protected, keep default readonly (do not use -ReadWrite). Log: $LogPath"
    }
}

Start-Sleep -Seconds 2
Update-HostStorageCache -ErrorAction SilentlyContinue | Out-Null

# Prefer volume that appeared after attach, or label GOLDCLUB
$newLetters = @()
Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object {
    $l = [string]$_.DriveLetter
    if (-not $before.ContainsKey($l)) { $newLetters += $l }
}

$assigned = $null
if ($newLetters.Count -eq 1) {
    $assigned = $newLetters[0]
    Write-Log "Windows assigned ${assigned}: automatically"
}
elseif ($newLetters.Count -gt 1) {
    foreach ($l in $newLetters) {
        if (Test-GoldclubMounted $l) { $assigned = $l; break }
    }
    if (-not $assigned) { $assigned = $newLetters[0] }
}

if (-not $assigned) {
    # Find GOLDCLUB volume without letter or with wrong letter via diskpart list
    $listOut = Invoke-DiskPart @('list volume')
    Write-Log 'Assigning letter via diskpart...'
    Invoke-DiskPart @(
        "select vdisk file=`"$vhd`"",
        'list partition'
    ) | Out-Null

    # Attach may have given a disk number -- use Get-Disk for Offline/new VHDs
    $vhdDisk = Get-Disk -ErrorAction SilentlyContinue | Where-Object {
        $_.FriendlyName -match 'Virtual|Msft|VHD' -or $_.Location -match 'goldclub'
    } | Sort-Object Number -Descending | Select-Object -First 1

    if (-not $vhdDisk) {
        # Broader: largest partition disk that appeared recently without letter
        $parts = Get-Partition -ErrorAction SilentlyContinue | Where-Object {
            -not $_.DriveLetter -and $_.Type -ne 'Reserved' -and $_.Size -gt 1GB
        } | Sort-Object Size -Descending | Select-Object -First 1
        if ($parts) {
            Set-Partition -DiskNumber $parts.DiskNumber -PartitionNumber $parts.PartitionNumber -NewDriveLetter $letter
            $assigned = $letter
            Write-Log "Assigned ${letter}: to disk $($parts.DiskNumber) part $($parts.PartitionNumber)"
        }
    }
    else {
        $part = Get-Partition -DiskNumber $vhdDisk.Number -ErrorAction SilentlyContinue |
            Where-Object { $_.Type -ne 'Reserved' } |
            Sort-Object Size -Descending |
            Select-Object -First 1
        if ($part) {
            if ($part.DriveLetter) {
                $assigned = [string]$part.DriveLetter
            }
            else {
                Set-Partition -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -NewDriveLetter $letter
                $assigned = $letter
            }
            Write-Log "Assigned ${assigned}: from VHD disk $($vhdDisk.Number)"
        }
    }
}

Start-Sleep -Seconds 1
if (-not $assigned) {
    $assigned = Find-AlreadyMountedLetter
}

if (-not $assigned -or -not (Test-Path "${assigned}:\")) {
    throw "VHD attached but no drive letter found. Check Disk Management. Log: $LogPath"
}

if (-not (Test-GoldclubMounted $assigned)) {
    Write-Log "WARN: ${assigned}:\ mounted but goldclub markers not seen yet -- open Explorer anyway"
}

Write-Log "Mounted: ${assigned}:\"
Write-Log "Browse: ${assigned}:\ruleta   ${assigned}:\config"
try {
    Start-Process explorer.exe "${assigned}:\"
} catch { }

Write-Log '=== DONE ==='
Write-Log "Dismount later: .\Mount-GoldclubVhd.bat -Dismount"
exit 0
