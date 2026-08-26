<#
.SYNOPSIS
    Hybrid Alegro disk: 10.1 GCEFISYS (boot) + 10.2 GCDATA0 payload from mounted browse image.

.DESCRIPTION
    Prerequisites:
      - 10.2 GCDATA0 already mounted (e.g. P: from Macrium Browse Image).
      - Run elevated. BIWIN USB must be connected.

    Steps: enable BIWIN -> empty GPT + EFI/GCDATA0 partitions -> copy 10.1 EFI -> copy 10.2 VHDs from source drive.
#>
#Requires -RunAsAdministrator
param(
    [string] $Source10_2Drive = 'P',
    [string] $Image10_1 = 'C:\WIN_SYSTEMS\Images\Alegro 10.1 128GB BIWIN.mrimg',
    [string] $EfiSourceLetter = 'O',
    [switch] $SkipBiwinPrep,
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'
$RepoRoot = 'C:\Users\Ezbogar\GoldclubLogInvestigator'
$LogPath = Join-Path $RepoRoot 'apply-alegro102-hybrid.log'
$utf8 = [System.Text.UTF8Encoding]::new($false)
$Reflect = 'C:\Program Files\Macrium\Reflect\Reflect.exe'

function Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $utf8)
}

function Invoke-DiskPartScript([string[]]$Commands) {
    $tmp = Join-Path $env:TEMP ("hybrid-{0}.txt" -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $utf8)
        Log ('diskpart: ' + ($Commands -join '; '))
        $out = cmd /c "diskpart /s `"$tmp`"" 2>&1
        $out | ForEach-Object { Log "  $_" }
        return ($out -join "`n")
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Enable-BiwinDisk {
    $pnp = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'VEN_BIWIN' }
    foreach ($d in $pnp) {
        Log "PnP $($d.Status): $($d.FriendlyName)"
        if ($d.Status -ne 'OK') {
            pnputil /enable-device $d.InstanceId 2>&1 | ForEach-Object { Log "  pnputil: $_" }
            try {
                Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
                Start-Sleep -Seconds 2
                Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
            } catch { Log "PnP cycle: $($_.Exception.Message)" }
        }
    }
    Restart-Service vds -Force -ErrorAction SilentlyContinue
    Update-HostStorageCache -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 4
    Invoke-DiskPartScript @('rescan')
}

function Get-BiwinDisk {
    Get-Disk -ErrorAction SilentlyContinue | Where-Object {
        $_.FriendlyName -match 'BIWIN' -and $_.FriendlyName -notmatch 'Samsung|KINGSTON'
    } | Select-Object -First 1
}

function Clear-LetterIfForeign([string]$Letter) {
    $part = Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -eq $Letter } | Select-Object -First 1
    if (-not $part) { return }
    $disk = Get-Disk -Number $part.DiskNumber -ErrorAction SilentlyContinue
    if ($disk -and $disk.FriendlyName -match 'BIWIN') { return }
    Log "Clearing foreign ${Letter}: on disk $($part.DiskNumber)"
    if (-not $WhatIf) {
        Set-Partition -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -RemoveDriveLetter -ErrorAction SilentlyContinue
    }
}

[IO.File]::WriteAllText($LogPath, '', $utf8)
Log '=== Alegro 10.1 EFI + 10.2 GCDATA0 hybrid restore ==='

$srcRoot = "${Source10_2Drive}:\"
if (-not (Test-Path (Join-Path $srcRoot 'goldclub.vhd'))) {
    Log "FAIL: ${Source10_2Drive}:\goldclub.vhd not found (mount 10.2 GCDATA0 in Macrium Browse first)"
    exit 1
}
if (-not (Test-Path $Image10_1)) {
    Log "FAIL: 10.1 image missing: $Image10_1"
    exit 2
}

Enable-BiwinDisk
$biwin = Get-BiwinDisk
if (-not $biwin) {
    Log 'FAIL: BIWIN not visible — replug USB 2.0 port and rerun'
    exit 3
}
$diskNum = [int]$biwin.Number
Log ("BIWIN disk {0}: {1} ({2} GB)" -f $diskNum, $biwin.FriendlyName, [math]::Round($biwin.Size / 1GB, 1))

if (-not $SkipBiwinPrep) {
    Log 'Preparing empty GPT + GCEFISYS + GCDATA0 on BIWIN'
    if ($WhatIf) { Log 'WhatIf: would clean disk and create partitions' }
    else {
        Invoke-DiskPartScript @(
            "select disk $diskNum",
            'detail disk',
            'online disk noerr',
            'attributes disk clear readonly noerr',
            'clean',
            'convert gpt',
            'create partition efi size=256',
            'format fs=fat32 label=GCEFISYS quick',
            'assign letter=E',
            'create partition primary',
            'format fs=ntfs label=GCDATA0 quick',
            'assign letter=G',
            'list partition'
        )
        Start-Sleep -Seconds 2
    }
}

Clear-LetterIfForeign -Letter $EfiSourceLetter
if (-not $WhatIf) {
    Log "Mounting 10.1 image EFI source on ${EfiSourceLetter}:"
    $mountArgs = @(
        "`"$Image10_1`"",
        '-b', '-auto',
        '-drives', "$EfiSourceLetter,*"
    )
    Log ("Reflect mount: {0}" -f ($mountArgs -join ' '))
    $p = Start-Process -FilePath $Reflect -ArgumentList $mountArgs -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { Log "WARN Reflect mount exit $($p.ExitCode)" }
    Start-Sleep -Seconds 3
}

$efiSrc = "${EfiSourceLetter}:\"
if (-not (Test-Path $efiSrc)) {
    Log "FAIL: 10.1 EFI mount ${EfiSourceLetter}: not found"
    exit 4
}

$efiDest = 'E:\'
$gDest = 'G:\'
foreach ($p in @($efiDest, $gDest)) {
    if (-not (Test-Path $p)) {
        Log "FAIL: target $p missing — BIWIN partition prep failed"
        exit 5
    }
}

$gFree = (Get-Volume G -ErrorAction SilentlyContinue).SizeRemaining
$needBytes = (Get-ChildItem $srcRoot -File | Measure-Object -Property Length -Sum).Sum
Log ("G: free {0} GB; need {1} GB from ${Source10_2Drive}:" -f [math]::Round($gFree / 1GB, 1), [math]::Round($needBytes / 1GB, 1))
if ($gFree -lt $needBytes) {
    Log 'FAIL: G: too small for 10.2 VHD payload'
    exit 6
}

if ($WhatIf) {
    Log 'WhatIf complete'
    exit 0
}

Log 'Copying 10.1 GCEFISYS -> E: (boot)'
& robocopy $efiSrc $efiDest /E /R:2 /W:2 /XJ /NFL /NDL /NP /LOG+:$LogPath
if ($LASTEXITCODE -ge 8) {
    Log "FAIL robocopy EFI exit $LASTEXITCODE"
    exit 7
}

Log "Copying 10.2 payload ${Source10_2Drive}: -> G: (goldclub.vhd, system.vhd, system.efi)"
& robocopy $srcRoot $gDest goldclub.vhd system.vhd system.efi /R:2 /W:5 /NP /LOG+:$LogPath
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    Log "FAIL robocopy payload exit $rc"
    exit 8
}

Log 'Verifying boot payload on G:'
$checks = @('goldclub.vhd', 'system.vhd', 'system.efi')
foreach ($name in $checks) {
    $f = Join-Path $gDest $name
    if (-not (Test-Path $f)) {
        Log "FAIL missing $name on G:"
        exit 9
    }
    $sz = (Get-Item $f).Length
    Log ("  OK {0} ({1} GB)" -f $name, [math]::Round($sz / 1GB, 2))
}

try {
    Start-Process -FilePath $Reflect -ArgumentList @("${EfiSourceLetter}:", '-u') -Wait -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
} catch { Log "Unmount ${EfiSourceLetter}: $($_.Exception.Message)" }

Log '=== DONE: BIWIN has 10.1 EFI + 10.2 VHDs. Detach Macrium 10.2 browse (P:) when finished. ==='
Log 'Run _CHECK_BIWIN_BOOT.bat or Mount-UsbDiskNow.ps1 -MountOnly to verify boot files.'
exit 0
