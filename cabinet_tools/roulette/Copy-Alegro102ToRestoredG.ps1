<#
.SYNOPSIS
    Mount 10.2 GCDATA0 browse image + BIWIN G:, copy 10.2 VHD payload over restored 10.1 on G:.
#>
#Requires -RunAsAdministrator
param(
    [string] $Image10_2 = 'C:\WIN_SYSTEMS\Images\Alegro_10.2_128GB_BIWIN.mrimg',
    [string] $BrowseLetter = 'P',
    [string] $TargetLetter = 'G'
)

$ErrorActionPreference = 'Stop'
$LogPath = 'C:\Users\Ezbogar\GoldclubLogInvestigator\copy-alegro102-to-g.log'
$Reflect = 'C:\Program Files\Macrium\Reflect\Reflect.exe'
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $utf8)
}

function Invoke-DiskPart([string[]]$Commands) {
    $tmp = Join-Path $env:TEMP ("copy102-{0}.txt" -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $utf8)
        Log ('diskpart: ' + ($Commands -join '; '))
        cmd /c "diskpart /s `"$tmp`"" 2>&1 | ForEach-Object { Log "  $_" }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Get-PartitionVolumeLabel($Partition) {
    $vol = Get-Volume -Partition $Partition -ErrorAction SilentlyContinue
    if ($vol) { return $vol.FileSystemLabel }
    return $null
}

function Reset-UsbMassStorage {
    $targets = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        ($_.FriendlyName -match 'UAS|USB Attached SCSI|BIWIN|Mass Storage') -or
        ($_.InstanceId -match 'USBSTOR|UAS')
    }
    foreach ($d in $targets) {
        Log "PnP $($d.Status)/$($d.Problem): $($d.Class) $($d.FriendlyName) [$($d.InstanceId)]"
        if ($d.Status -ne 'OK') {
            pnputil /enable-device $d.InstanceId 2>&1 | ForEach-Object { Log "  pnputil: $_" }
            try {
                Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
                Start-Sleep -Seconds 3
                Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
                Log '  PnP cycle OK'
            } catch { Log "  PnP cycle: $($_.Exception.Message)" }
        }
    }
    # UAS held-for-eject often needs a second rescan after a pause
    Start-Sleep -Seconds 5
    Restart-Service vds -Force -ErrorAction SilentlyContinue
    Update-HostStorageCache -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 3
    Invoke-DiskPart @('rescan')
    Start-Sleep -Seconds 3
    Invoke-DiskPart @('list disk')
}

function Clear-Letter([string]$Letter) {
    $part = Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -eq $Letter } | Select-Object -First 1
    if ($part) {
        Log "Removing ${Letter}: from disk $($part.DiskNumber) part $($part.PartitionNumber)"
        Set-Partition -DiskNumber $part.DiskNumber -PartitionNumber $part.PartitionNumber -RemoveDriveLetter -ErrorAction SilentlyContinue
    }
}

function Mount-10_2Browse {
    if (Test-Path "${BrowseLetter}:\goldclub.vhd") {
        Log "${BrowseLetter}: already has 10.2 payload"
        return
    }
    if (-not (Test-Path $Image10_2)) {
        throw "10.2 image not found: $Image10_2"
    }
    Clear-Letter -Letter $BrowseLetter
    Log "Mounting 10.2 GCDATA0 browse on ${BrowseLetter}:"
    $args = @("`"$Image10_2`"", '-b', '-auto', '-drives', "*,$BrowseLetter")
    Log ('Reflect: ' + ($args -join ' '))
    $p = Start-Process -FilePath $Reflect -ArgumentList $args -Wait -PassThru -WindowStyle Hidden
    Log "Reflect mount exit $($p.ExitCode)"
    Start-Sleep -Seconds 3
    if (-not (Test-Path "${BrowseLetter}:\goldclub.vhd")) {
        throw "${BrowseLetter}:\goldclub.vhd not found after mount"
    }
}

function Ensure-BiwinG {
    $biwin = Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'BIWIN' } | Select-Object -First 1
    if (-not $biwin) { throw 'BIWIN not visible — replug USB 2.0 port and rerun' }
    $num = [int]$biwin.Number
    Log ("BIWIN disk $num — $($biwin.FriendlyName)")
    if ($biwin.OperationalStatus -ne 'Online') {
        Invoke-DiskPart @("select disk $num", 'online disk noerr', 'attributes disk clear readonly noerr')
        Set-Disk -Number $num -IsOffline $false -ErrorAction SilentlyContinue
    }

    $data = Get-Partition -DiskNumber $num -ErrorAction SilentlyContinue | Where-Object {
        (Get-PartitionVolumeLabel $_) -eq 'GCDATA0' -or ($_.Type -eq 'Basic' -and $_.Size -gt 50GB)
    } | Sort-Object Size -Descending | Select-Object -First 1

    if (-not $data) { throw "GCDATA0 partition not found on BIWIN disk $num" }
    Log ("GCDATA0 part $($data.PartitionNumber) size $([math]::Round($data.Size/1GB,1)) GB")

    $existing = Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -eq $TargetLetter } | Select-Object -First 1
    if ($existing -and ($existing.DiskNumber -ne $num -or $existing.PartitionNumber -ne $data.PartitionNumber)) {
        Clear-Letter -Letter $TargetLetter
    }
    if (-not $data.DriveLetter) {
        Set-Partition -DiskNumber $num -PartitionNumber $data.PartitionNumber -NewDriveLetter $TargetLetter
        Log "Assigned ${TargetLetter}: to BIWIN GCDATA0"
    } elseif ($data.DriveLetter -ne $TargetLetter) {
        Log "GCDATA0 already on $($data.DriveLetter): (expected ${TargetLetter}:)"
    }
    Start-Sleep -Seconds 2
    if (-not (Test-Path "${TargetLetter}:\")) { throw "${TargetLetter}:\ not reachable" }
}

function Copy-Payload {
    $src = "${BrowseLetter}:\"
    $dst = "${TargetLetter}:\"
    $files = @('goldclub.vhd', 'system.vhd', 'system.efi')
    foreach ($f in $files) {
        if (-not (Test-Path (Join-Path $src $f))) { throw "Missing source $f" }
    }
    $need = ($files | ForEach-Object { (Get-Item (Join-Path $src $_)).Length } | Measure-Object -Sum).Sum
    $free = (Get-Volume -DriveLetter $TargetLetter).SizeRemaining
    Log ("Copy ${BrowseLetter}: -> ${TargetLetter}: need $([math]::Round($need/1GB,1)) GB, free $([math]::Round($free/1GB,1)) GB")
    if ($free -lt $need) { throw 'Not enough free space on G:' }

    Log 'Starting robocopy (may take 30-90 min)...'
    & robocopy $src $dst goldclub.vhd system.vhd system.efi /R:2 /W:5 /NP /LOG+:$LogPath
    $rc = $LASTEXITCODE
    Log "robocopy exit $rc"
    if ($rc -ge 8) { throw "robocopy failed with exit $rc" }

    foreach ($f in $files) {
        $s = (Get-Item (Join-Path $src $f)).Length
        $t = (Get-Item (Join-Path $dst $f)).Length
        if ($s -ne $t) { throw "Size mismatch $f src=$s dst=$t" }
        Log ("Verified $f ($([math]::Round($t/1GB,2)) GB)")
    }
}

[IO.File]::WriteAllText($LogPath, '', $utf8)
Log '=== Mount 10.2 browse + copy to G: ==='
Reset-UsbMassStorage
Mount-10_2Browse
Ensure-BiwinG
Copy-Payload
Log '=== DONE — 10.2 VHDs copied to G:. Detach P: browse in Macrium when ready. ==='
exit 0
