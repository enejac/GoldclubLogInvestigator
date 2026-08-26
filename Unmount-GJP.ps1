#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Unmount Macrium browse volumes G: (goldclub.vhd), J: (system.vhd), P: (GCDATA0).
#>
$ErrorActionPreference = 'Continue'
$Utf8 = [Text.UTF8Encoding]::new($false)
$LogPath = Join-Path $PSScriptRoot '_tmp_logs\unmount-gjp.log'
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
}
function Invoke-DiskPart([string[]]$Commands) {
    $tmp = Join-Path $env:TEMP ("unmount-gjp-{0}.txt" -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $Utf8)
        $out = cmd /c "diskpart /s `"$tmp`"" 2>&1 | ForEach-Object { "$_" }
        foreach ($line in $out) { Write-Log "  $line" }
        return ($out -join "`n")
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

[IO.File]::WriteAllText($LogPath, '', $Utf8)
Write-Log '=== Unmount G: J: P: ==='

# Detach nested VHDs while P: still visible
foreach ($vhd in @(
    'P:\goldclub.vhd', 'P:\system.vhd',
    'P:\Goldclub.vhd', 'P:\SYSTEM.vhd',
    'G:\goldclub.vhd', 'J:\system.vhd'
)) {
    if (Test-Path -LiteralPath $vhd) {
        Write-Log "detach vdisk $vhd"
        [void](Invoke-DiskPart @("select vdisk file=`"$vhd`"", 'detach vdisk'))
    }
}

Start-Sleep -Seconds 1

foreach ($letter in 'G', 'J', 'P') {
    $part = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
    if ($part) {
        Write-Log "Remove-PartitionAccessPath ${letter}:"
        try {
            Remove-PartitionAccessPath -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -AccessPath "${letter}:\" -ErrorAction Stop
            Write-Log '  ok'
        } catch {
            Write-Log "  fail: $($_.Exception.Message)"
        }
    }
    $mv = cmd /c "mountvol ${letter}: /d" 2>&1 | Out-String
    Write-Log ("mountvol {0}: /d => {1}" -f $letter, $mv.Trim())
}

# Macrium browse parent may still hold P: — try volume dismount by unique id
foreach ($letter in 'G', 'J', 'P') {
    if (Test-Path "${letter}:\") {
        Write-Log "${letter}: still present after detach"
    } else {
        Write-Log "${letter}: gone"
    }
}

Write-Log ("Final: G={0} J={1} P={2}" -f (Test-Path 'G:\'), (Test-Path 'J:\'), (Test-Path 'P:\'))
Write-Log '=== DONE ==='
if ((Test-Path 'G:\') -or (Test-Path 'J:\') -or (Test-Path 'P:\')) {
    Write-Log 'NOTE: If P: remains, close Macrium Reflect Browse / Image Guardian and re-run, or eject GCDATA0 from Macrium UI.'
    exit 2
}
exit 0