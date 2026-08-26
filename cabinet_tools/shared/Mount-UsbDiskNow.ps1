<#
.SYNOPSIS
    Ultimate BIWIN USB disk bring-to-life suite (mount + debug + EFI browse).

.DESCRIPTION
    One-shot admin workflow:
      - USB/PnP diagnostics and recovery (UAS fallback optional)
      - VDS refresh, diskpart rescan, BIWIN online/letter assignment
      - Assign G: (GCDATA0 data) and E: (GCEFISYS EFI) when missing
      - Mirror EFI to G:\GCEFISYS for normal-user Explorer browsing
      - Desktop shortcuts + open browse copy

.PARAMETER ForceUsbMassStorage
    Set UAS->BOT fallback (DeviceHackFlags) for flaky USB bridges.

.PARAMETER UsbOnly
    Limit generic letter assignment to USB bus disks only.

.PARAMETER EfiOnly
    Skip mount/recovery; only refresh G:\GCEFISYS mirror from E:.

.PARAMETER MountOnly
    Skip EFI mirror/shortcuts (hardware mount only).

.PARAMETER SkipExplorer
    Do not open G:\GCEFISYS in Explorer at the end.

.PARAMETER Recovery
    Aggressive USB recovery (PnP reset, UAS fallback, ghost E: cleanup).
    OFF by default - can destabilize BIWIN and break EGM boot. Use only when
    the disk is not visible at all.

.PARAMETER AssignEfiDriveLetter
    Assign E: to BIWIN GCEFISYS (admin-only). Default is G:\GCEFISYS folder mount.

.PARAMETER EfiBrowseMount
    Mount EFI at G:\GCEFISYS for Explorer browsing (workstation only).
    Skipped in safe mode to avoid altering ESP access paths before EGM boot.

.EXAMPLE
    .\_MOUNT_USB_SAFE.bat

.EXAMPLE
    .\_MOUNT_USB_DISK_NOW.bat -Recovery -ForceUsbMassStorage
#>
param(
    [switch] $ForceUsbMassStorage,
    [switch] $UsbOnly,
    [switch] $EfiOnly,
    [switch] $MountOnly,
    [switch] $SkipExplorer,
    [switch] $Recovery,
    [switch] $AssignEfiDriveLetter,
    [switch] $EfiBrowseMount
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$LogFile = Join-Path $RepoRoot 'logs\mount-usb-disk-now.log'
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Log([string] $Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, $utf8)
}

function Stop-StaleDiskPart {
    Get-Process diskpart -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Get-ChildItem (Join-Path $env:TEMP 'mount-dp-*.txt') -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Invoke-DiskPart([string[]] $Commands) {
    Stop-StaleDiskPart
    $tmp = Join-Path $env:TEMP ('mount-dp-{0}.txt' -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $utf8)
        Log ('diskpart: ' + ($Commands -join '; '))
        $out = cmd /c "diskpart /s `"$tmp`"" 2>&1
        $text = ($out | ForEach-Object { "$_" }) -join "`n"
        foreach ($line in $out) {
            $trim = "$line".Trim()
            if ($trim) { Log "  $trim" }
        }
        return ,@($text, ($LASTEXITCODE -eq 0 -and $text -notmatch 'fatal device hardware error|encountered an error'))
    }
    finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Vds {
    $svc = Get-Service vds -ErrorAction SilentlyContinue
    if (-not $svc) { return }
    if ($svc.Status -ne 'Running') {
        Start-Service vds
        Log 'Started vds'
    }
    else {
        Restart-Service vds -Force -ErrorAction SilentlyContinue
        Log 'Restarted vds'
    }
}

function Write-UsbDiagnostics {
    Log '--- USB / disk diagnostics ---'
    Get-PnpDevice -Class DiskDrive -ErrorAction SilentlyContinue |
        Where-Object { $_.FriendlyName -notmatch 'Virtual' } |
        ForEach-Object {
            $problem = if ($_.Problem) { " problem=$($_.Problem)" } else { '' }
            Log ("PnP disk: status={0}{1} name={2}" -f $_.Status, $problem, $_.FriendlyName)
        }
    Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue |
        Where-Object { $_.Model -notmatch 'Virtual' } |
        ForEach-Object {
            $sizeGb = if ($_.Size) { [math]::Round($_.Size / 1GB, 1) } else { 0 }
            Log ("WMI disk index={0} sizeGB={1} model={2} status={3}" -f $_.Index, $sizeGb, $_.Model, $_.Status)
            if (-not $_.Size) {
                Log "WARN WMI sees '$($_.Model)' but size=0 (enclosure/drive not responding)"
            }
        }
    Get-PnpDevice -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FriendlyName -match 'Mass Storage|UAS|Descriptor Request|USB Serial|Flash Drive|BIWIN' -or
            $_.InstanceId -match 'USBSTOR|USB\\VID|152D'
        } |
        ForEach-Object {
            $problem = if ($_.Problem) { " problem=$($_.Problem)" } else { '' }
            Log ("PnP usb: status={0}{1} class={2} name={3}" -f $_.Status, $problem, $_.Class, $_.FriendlyName)
        }
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName      = 'System'
            ProviderName = 'disk'
            Id           = 154
            StartTime    = (Get-Date).AddHours(-2)
        } -MaxEvents 3 -ErrorAction Stop
        foreach ($event in $events) {
            Log ("WARN recent disk hardware error: {0}" -f ($event.Message -replace '\s+', ' '))
        }
    }
    catch {
        Log 'No recent disk Event ID 154 hardware errors in System log'
    }
}

function Reset-PnpDevice {
    param([string] $InstanceId, [string] $Label)
    if (-not $InstanceId) { return }
    try {
        Log "Reset PnP: $Label"
        Disable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
        Start-Sleep -Seconds 2
        Enable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
        Start-Sleep -Seconds 2
    }
    catch {
        Log "Reset failed for ${Label}: $($_.Exception.Message)"
    }
}

function Invoke-UsbRecoveryPass {
    param([switch] $ApplyUasFallback)
    $targets = @(
        'USB Attached SCSI (UAS) Mass Storage Device',
        'USB Mass Storage Device',
        'BIWIN SS D SCSI Disk Device',
        'Samsung Flash Drive USB Device'
    )
    foreach ($name in $targets) {
        Get-PnpDevice -ErrorAction SilentlyContinue |
            Where-Object { $_.FriendlyName -eq $name } |
            ForEach-Object { Reset-PnpDevice -InstanceId $_.InstanceId -Label $_.FriendlyName }
    }
    if ($ApplyUasFallback) {
        Get-PnpDevice -Class SCSIAdapter -ErrorAction SilentlyContinue |
            Where-Object { $_.FriendlyName -match 'UAS' } |
            ForEach-Object {
                if ($_.InstanceId -match 'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})') {
                    $vid = $Matches[1].ToLower()
                    $devicePid = $Matches[2].ToLower()
                    $key = "HKLM:\SYSTEM\CurrentControlSet\Control\usbstor\${vid}_${devicePid}"
                    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
                    Set-ItemProperty -Path $key -Name 'DeviceHackFlags' -Value 1 -Type DWord
                    Log "Set UAS->BOT fallback DeviceHackFlags=1 at $key (replug USB if still size=0)"
                    Reset-PnpDevice -InstanceId $_.InstanceId -Label $_.FriendlyName
                }
            }
    }
}

function Find-BiwinDiskNumber {
    $byCmdlet = Get-Disk -ErrorAction SilentlyContinue |
        Where-Object { $_.FriendlyName -match 'BIWIN' } |
        Select-Object -First 1
    if ($byCmdlet) {
        Log ("BIWIN found via Get-Disk: disk $($byCmdlet.Number)")
        return [int]$byCmdlet.Number
    }
    $wmi = Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue |
        Where-Object { $_.Model -match 'BIWIN' -and $_.Index -match '^\d+$' } |
        Select-Object -First 1
    if ($wmi) {
        Log ("BIWIN found via WMI: disk $($wmi.Index)")
        return [int]$wmi.Index
    }
    # One diskpart list disk — not N separate diskpart processes.
    $result = Invoke-DiskPart @('list disk')
    foreach ($line in ($result[0] -split "`n")) {
        if ($line -match 'Disk\s+(\d+)\s+.+(BIWIN)') {
            Log ("BIWIN found via diskpart list: disk $($Matches[1])")
            return [int]$Matches[1]
        }
    }
    return $null
}

function Get-DetailFlags([string] $DetailText) {
    [pscustomobject]@{
        Online        = $DetailText -match 'Status\s*:\s*Online'
        Offline       = $DetailText -match 'Status\s*:\s*Offline'
        NoPartitions  = $DetailText -match 'no partitions'
        ZeroId        = $DetailText -match 'Disk ID:\s*0{8}'
        HardwareError = $DetailText -match 'fatal device hardware error|encountered an error'
    }
}

function Get-FirstFreeDriveLetter {
    foreach ($code in 67..90) {
        $letter = [char]$code
        if (-not (Get-Volume -DriveLetter $letter -ErrorAction SilentlyContinue)) { return $letter }
    }
    return $null
}

function Get-PartitionVolumeLabel($Partition) {
    if (-not $Partition) { return '' }
    $vol = Get-Volume -Partition $Partition -ErrorAction SilentlyContinue
    if ($vol) { return $vol.FileSystemLabel }
    return ''
}

function Get-BiwinEfiPartition([int] $DiskNumber) {
    $parts = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)
    if ($parts.Count -eq 0) { return $null }
    $byLabel = $parts | Where-Object { (Get-PartitionVolumeLabel $_) -eq 'GCEFISYS' } | Select-Object -First 1
    if ($byLabel) { return $byLabel }
    return $parts | Where-Object { $_.Type -eq 'System' -and $_.Size -lt 512MB } | Select-Object -First 1
}

function Get-BiwinDataPartition([int] $DiskNumber) {
    $parts = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)
    if ($parts.Count -eq 0) { return $null }
    $byLabel = $parts | Where-Object { (Get-PartitionVolumeLabel $_) -eq 'GCDATA0' } | Select-Object -First 1
    if ($byLabel) { return $byLabel }
    return $parts | Where-Object {
        $_.Size -gt 1GB -and $_.Type -notin @('Reserved', 'Recovery', 'System')
    } | Sort-Object Size -Descending | Select-Object -First 1
}

function Test-PartitionHasLetter($Partition, [char] $Letter) {
    return $Partition -and $Partition.DriveLetter -eq $Letter
}

function Remove-PartitionDriveLetterSafe($Partition) {
    if (-not $Partition -or -not $Partition.DriveLetter) { return $true }
    $letter = $Partition.DriveLetter
    try {
        Remove-PartitionAccessPath -DiskNumber $Partition.DiskNumber -PartitionNumber $Partition.PartitionNumber `
            -AccessPath "${letter}:\" -ErrorAction Stop
        Log "Removed ${letter}: from disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
        return $true
    }
    catch {
        try {
            Set-Partition -DiskNumber $Partition.DiskNumber -PartitionNumber $Partition.PartitionNumber -RemoveDriveLetter
            Log "Removed ${letter}: from disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber) (Set-Partition)"
            return $true
        }
        catch {
            Log "Remove ${letter}: failed disk $($Partition.DiskNumber) part $($Partition.PartitionNumber): $($_.Exception.Message)"
            return $false
        }
    }
}

function Clear-GhostDriveLetter([char] $Letter, [int] $DiskNumber = -1) {
    $letterStr = "${Letter}:"
    $targets = @(Get-Partition -DriveLetter $Letter -ErrorAction SilentlyContinue)
    if ($DiskNumber -ge 0) {
        $targets = @($targets | Where-Object { $_.DiskNumber -eq $DiskNumber })
    }
    foreach ($part in $targets) {
        Remove-PartitionDriveLetterSafe $part | Out-Null
    }
    $di = [System.IO.DriveInfo]::GetDrives() | Where-Object { $_.Name -eq $letterStr }
    if ($di) {
        Log "Clearing ${letterStr} mount point (IsReady=$($di.IsReady))"
        cmd /c "mountvol $letterStr /D" 2>&1 | ForEach-Object { if ("$_") { Log "  mountvol /D: $_" } }
    }
    if ($DiskNumber -ge 0) {
        $null = Invoke-DiskPart @("select disk $DiskNumber", 'list partition')
        $parts = @(Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue)
        foreach ($part in $parts) {
            if ($part.DriveLetter -eq $Letter) {
                $null = Invoke-DiskPart @(
                    "select disk $DiskNumber",
                    "select partition $($part.PartitionNumber)",
                    "remove letter=$Letter noerr"
                )
            }
        }
    }
    Start-Sleep -Milliseconds 500
}


function Clear-StaleDriveLetterReservation([char] $Letter, [int] $DiskNumber = -1) {
    $letterStr = "${Letter}:"
    $hasPartition = [bool](Get-Partition -DriveLetter $Letter -ErrorAction SilentlyContinue)
    $ready = $false
    try {
        $ready = ([System.IO.DriveInfo]::new($letterStr)).IsReady
    }
    catch {}
    if ($hasPartition -and $ready) { return }
    Clear-GhostDriveLetter $Letter $DiskNumber
    if ($Recovery) {
        $md = 'HKLM:\SYSTEM\MountedDevices'
        $prop = "\DosDevices\$letterStr"
        if ((Get-Item -LiteralPath $md).Property -contains $prop) {
            Remove-ItemProperty -LiteralPath $md -Name $prop -Force -ErrorAction SilentlyContinue
            Log "Removed stale MountedDevices entry $prop (ghost letter)"
        }
        cmd /c 'mountvol /R' 2>&1 | Out-Null
    }
}
function Test-BiwinBootReadiness([int] $DiskNumber) {
    $efiGuid = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
    $result = [ordered]@{
        Ok       = $true
        Disk     = $DiskNumber
        Checks   = @()
        Failures = @()
    }

    $disk = Get-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
    if (-not $disk) {
        $result.Ok = $false
        $result.Failures += 'BIWIN disk not found'
        return [pscustomobject]$result
    }
    if ($disk.IsOffline -or $disk.OperationalStatus -ne 'Online') {
        $result.Failures += "Disk offline or not Online (status=$($disk.OperationalStatus))"
        $result.Ok = $false
    }
    else {
        $result.Checks += 'Disk online'
    }

    $efi = Get-BiwinEfiPartition -DiskNumber $DiskNumber
    if (-not $efi) {
        $result.Failures += 'EFI partition (GCEFISYS) missing'
        $result.Ok = $false
    }
    else {
        if ($efi.GptType -eq $efiGuid -or $efi.Type -eq 'System') {
            $result.Checks += 'EFI partition type OK'
        }
        else {
            $result.Failures += "EFI partition wrong type: $($efi.Type) $($efi.GptType)"
            $result.Ok = $false
        }
        $src = Get-EfiVolumeSourcePath $efi
        if (-not $src) { $src = if ($efi.Guid) { "\\?\Volume{$($efi.Guid)}\" } else { $null } }
        if ($src) {
            $bootEfi = Join-Path $src 'EFI\Boot\bootx64.efi'
            $bcd = Join-Path $src 'EFI\Microsoft\Boot\BCD'
            if (Test-Path -LiteralPath $bootEfi) { $result.Checks += 'bootx64.efi present' }
            else { $result.Failures += 'bootx64.efi missing on ESP'; $result.Ok = $false }
            if (Test-Path -LiteralPath $bcd) { $result.Checks += 'BCD present' }
            else { $result.Failures += 'BCD missing on ESP'; $result.Ok = $false }
        }
        else {
            $result.Failures += 'EFI volume path not reachable (assign E: or use -EfiBrowseMount on workstation)'
            $result.Ok = $false
        }
    }

    $data = Get-BiwinDataPartition -DiskNumber $DiskNumber
    if (-not $data) {
        $result.Failures += 'Data partition (GCDATA0) missing'
        $result.Ok = $false
    }
    else {
        $letter = if ($data.DriveLetter) { "$($data.DriveLetter):" } else { $null }
        if ($letter) {
            $payload = @('goldclub.vhd', 'system.efi', 'system.vhd') | Where-Object {
                Test-Path -LiteralPath (Join-Path $letter $_)
            }
            if ($payload.Count -gt 0) {
                $result.Checks += "Data payload on ${letter}: $($payload -join ', ')"
            }
            else {
                $result.Failures += "No goldclub.vhd/system.efi on ${letter}"
                $result.Ok = $false
            }
        }
        else {
            $result.Failures += 'Data partition has no drive letter (assign G:)'
            $result.Ok = $false
        }
    }

    foreach ($c in $result.Checks) { Log "BOOT OK: $c" }
    foreach ($f in $result.Failures) { Log "BOOT FAIL: $f" }
    Log ("BOOT READINESS: {0}" -f $(if ($result.Ok) { 'PASS' } else { 'FAIL' }))
    return [pscustomobject]$result
}

function Set-PartitionDriveLetterSafe($Partition, [char] $Letter) {
    if (-not $Partition) { return $false }
    $diskNum = $Partition.DiskNumber
    Clear-GhostDriveLetter $Letter $diskNum
    $letterStr = "${Letter}:"
    if (Test-PartitionHasLetter $Partition $Letter) {
        Log "${letterStr} already on BIWIN disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
        return $true
    }
    $existing = Get-Partition -DriveLetter $Letter -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.DiskNumber -eq $Partition.DiskNumber -and $existing.PartitionNumber -eq $Partition.PartitionNumber) {
            Log "${letterStr} already on target BIWIN partition"
            return $true
        }
        Log "${letterStr} in use on disk $($existing.DiskNumber) partition $($existing.PartitionNumber) - clearing wrong assignment"
        Remove-PartitionDriveLetterSafe $existing | Out-Null
        Start-Sleep -Milliseconds 500
    }
    try {
        Set-Partition -DiskNumber $Partition.DiskNumber -PartitionNumber $Partition.PartitionNumber -NewDriveLetter $Letter -ErrorAction Stop
        Log "Assigned ${letterStr} to BIWIN disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
        return $true
    }
    catch {
        $msg = $_.Exception.Message
        if ($msg -match 'already in use|access path') {
            Log "Assign ${letterStr} retry after ghost clear: $msg"
            Clear-GhostDriveLetter $Letter $diskNum
            if ($Partition.Guid) {
                $volPath = "\\?\Volume{$($Partition.Guid)}\"
                cmd /c "mountvol $letterStr `"$volPath`"" 2>&1 | ForEach-Object { if ("$_") { Log "  mountvol assign: $_" } }
                Start-Sleep -Milliseconds 500
                if (Test-PartitionHasLetter $Partition $Letter) {
                    Log "Assigned ${letterStr} via mountvol to BIWIN EFI"
                    return $true
                }
            }
            try {
                Set-Partition -DiskNumber $Partition.DiskNumber -PartitionNumber $Partition.PartitionNumber `
                    -NewDriveLetter $Letter -ErrorAction Stop
                Log "Assigned ${letterStr} on retry to BIWIN disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
                return $true
            }
            catch {
                Log "Assign ${letterStr} skipped (access path still in use): $($_.Exception.Message)"
                return $false
            }
        }
        Log "Assign ${letterStr} failed: $msg"
        return $false
    }
}

function Write-BootReadinessSummary($Readiness) {
    if (-not $Readiness) { return }
    if ($Readiness.Ok) {
        Log 'RESULT: BOOT READY for EGM (EFI + data verified). Do not run -Recovery before cabinet boot.'
    }
    else {
        Log 'RESULT: BOOT NOT READY - fix failures above before booting EGM.'
    }
}

function Confirm-EfiPartitionType($Partition) {
    if (-not $Partition) { return }
    $efiGuid = '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
    if ($Partition.GptType -eq $efiGuid) {
        Log "EFI partition GptType OK (System GUID) on disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
    }
    elseif ($Partition.Type -eq 'System') {
        Log "EFI partition Type=System on disk $($Partition.DiskNumber) partition $($Partition.PartitionNumber)"
    }
    else {
        Log "WARN partition disk $($Partition.DiskNumber) part $($Partition.PartitionNumber) type=$($Partition.Type) - not modifying for boot safety"
    }
}

function Ensure-BiwinLetters([int] $DiskNumber) {
    $disk = Get-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
    if (-not $disk -or $disk.FriendlyName -notmatch 'BIWIN') {
        Log "Ensure-BiwinLetters skipped: disk $DiskNumber is not BIWIN"
        return
    }

    $efi = Get-BiwinEfiPartition -DiskNumber $DiskNumber
    $data = Get-BiwinDataPartition -DiskNumber $DiskNumber
    if ($efi) {
        Confirm-EfiPartitionType $efi
        if ($AssignEfiDriveLetter) {
            Set-PartitionDriveLetterSafe -Partition $efi -Letter E | Out-Null
        }
        else {
            Clear-StaleDriveLetterReservation E $DiskNumber | Out-Null
            Log 'E: not assigned (browse EFI at G:\GCEFISYS; use -AssignEfiDriveLetter for admin E:)'
        }
    }
    else {
        Log 'BIWIN EFI partition (GCEFISYS) not found'
    }
    if ($data) {
        $gOnBiwin = Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue |
            Where-Object { $_.DriveLetter -eq 'G' }
        if ($gOnBiwin) {
            Log 'G: already on BIWIN data partition'
        }
        elseif (Get-Partition -DriveLetter G -ErrorAction SilentlyContinue | Where-Object { $_.DiskNumber -ne $DiskNumber }) {
            Log 'G: letter used by another disk - not stealing for BIWIN'
        }
        elseif (-not (Get-Partition -DriveLetter G -ErrorAction SilentlyContinue)) {
            Set-PartitionDriveLetterSafe -Partition $data -Letter G | Out-Null
        }
        else {
            $alt = Get-FirstFreeDriveLetter
            if ($alt) {
                Set-PartitionDriveLetterSafe -Partition $data -Letter $alt | Out-Null
            }
            else {
                Log 'No free drive letter for BIWIN data partition'
            }
        }
    }
    else {
        Log 'BIWIN data partition (GCDATA0) not found'
    }
}


function Get-EfiVolumeSourcePath($Partition) {
    if (-not $Partition) { return $null }
    if (Test-Path -LiteralPath 'E:\') { return 'E:\' }
    foreach ($ap in @($Partition.AccessPaths)) {
        if (-not $ap) { continue }
        $norm = if ($ap.EndsWith('\')) { $ap } else { "$ap\" }
        if ($norm -match 'Volume\{' -and (Test-Path -LiteralPath $norm)) { return $norm }
    }
    if ($Partition.Guid) {
        $volPath = "\\?\Volume{$($Partition.Guid)}\"
        if (Test-Path -LiteralPath $volPath) { return $volPath }
    }
    return $null
}

function Ensure-EfiBrowseFolderMount($efiPart) {
    if (-not $efiPart) { return $false }
    $browse = 'G:\GCEFISYS'
    if (-not (Test-Path -LiteralPath 'G:\')) {
        Log 'G: not available - cannot mount G:\GCEFISYS'
        return $false
    }
    Clear-StaleDriveLetterReservation E $efiPart.DiskNumber | Out-Null
    $mounted = @($efiPart.AccessPaths) | Where-Object { $_ -like 'G:\GCEFISYS*' }
    if (-not $mounted) {
        New-Item -ItemType Directory -Path $browse -Force | Out-Null
        try {
            Add-PartitionAccessPath -DiskNumber $efiPart.DiskNumber -PartitionNumber $efiPart.PartitionNumber `
                -AccessPath $browse -ErrorAction Stop
            Log "Mounted BIWIN EFI at $browse (no E: letter needed)"
        }
        catch {
            Log "Folder mount failed: $($_.Exception.Message)"
            return $false
        }
    }
    else {
        Log "BIWIN EFI already available at $browse"
    }
    return (Test-Path -LiteralPath $browse)
}
function Publish-EfiBrowseMirror {
    param([int] $DiskNumber = 0)

    $efiPart = $null
    if ($DiskNumber -gt 0) {
        $efiPart = Get-BiwinEfiPartition -DiskNumber $DiskNumber
    }
    if (-not $efiPart) {
        Log 'BIWIN EFI partition not found - skip mirror (assign E: first or replug drive)'
        return $false
    }

    $mirror = 'G:\GCEFISYS'
    $efiPart = Get-Partition -DiskNumber $efiPart.DiskNumber -PartitionNumber $efiPart.PartitionNumber -ErrorAction SilentlyContinue
    if (Ensure-EfiBrowseFolderMount $efiPart) {
        Log 'Using direct folder mount at G:\GCEFISYS (preferred over E: or robocopy mirror)'
    }
    else {
        if ($AssignEfiDriveLetter) {
            Set-PartitionDriveLetterSafe -Partition $efiPart -Letter E | Out-Null
            Start-Sleep -Seconds 1
        }
        $src = Get-EfiVolumeSourcePath $efiPart
        if (-not $src) {
            Log 'EFI volume not reachable - skip EFI browse setup'
            return $false
        }
        if (Test-Path -LiteralPath $mirror) {
            $item = Get-Item -LiteralPath $mirror -Force -ErrorAction SilentlyContinue
            if ($item -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                Remove-Item -LiteralPath $mirror -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        New-Item -ItemType Directory -Path $mirror -Force | Out-Null
        Log "Mirroring $src -> $mirror (NTFS browse copy fallback)"
        $rc = Start-Process robocopy -ArgumentList @(
            $src, $mirror, '/E', '/COPY:DAT', '/DCOPY:T', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS'
        ) -Wait -PassThru -NoNewWindow
        Log "Robocopy exit=$($rc.ExitCode)"
    }

    $user = "$env:USERDOMAIN\$env:USERNAME"
    cmd /c "icacls `"$mirror`" /grant `"$user`":(OI)(CI)F /T /C" 2>&1 | ForEach-Object { Log $_ }

    $desktop = [Environment]::GetFolderPath('Desktop')
    $ws = New-Object -ComObject WScript.Shell
    $shortcutPairs = [System.Collections.Generic.List[object]]::new()
    [void]$shortcutPairs.Add([object]@('BIWIN EFI (browse)', $mirror))
    if (Test-Path -LiteralPath 'E:\') {
        [void]$shortcutPairs.Add([object]@('BIWIN EFI (admin E:)', 'E:\'))
    }
    foreach ($pair in $shortcutPairs) {
        $lnk = Join-Path $desktop ($pair[0] + '.lnk')
        $s = $ws.CreateShortcut($lnk)
        $s.TargetPath = $pair[1]
        $s.Description = 'BIWIN GCEFISYS EFI partition'
        $s.Save()
        Log "Shortcut: $lnk"
    }
    $staleAdmin = Join-Path $desktop 'BIWIN EFI (admin E:).lnk'
    if ((Test-Path -LiteralPath $staleAdmin) -and -not (Test-Path -LiteralPath 'E:\')) {
        Remove-Item -LiteralPath $staleAdmin -Force -ErrorAction SilentlyContinue
        Log "Removed stale shortcut: $staleAdmin"
    }

    if (-not $SkipExplorer) {
        Log 'Opening browse copy...'
        Start-Process explorer.exe $mirror
    }
    return $true
}

function Write-Summary {
    Log '--- Current disks ---'
    $disks = @(Get-Disk -ErrorAction SilentlyContinue)
    if ($disks.Count -eq 0) {
        Log '(none)'
    }
    else {
        $disks | Format-Table Number, FriendlyName, BusType, OperationalStatus, IsOffline, IsReadOnly,
            @{ N = 'SizeGB'; E = { if ($_.Size) { [math]::Round($_.Size / 1GB, 1) } else { 0 } } } -AutoSize |
            Out-String | ForEach-Object { Log $_.Trim() }
    }

    Log '--- Current volumes ---'
    $volumes = @(Get-Volume -ErrorAction SilentlyContinue | Where-Object DriveLetter)
    if ($volumes.Count -eq 0) {
        Log '(none with drive letters)'
    }
    else {
        $volumes | Format-Table DriveLetter, FileSystemLabel, FileSystem, DriveType,
            @{ N = 'SizeGB'; E = { [math]::Round($_.Size / 1GB, 1) } },
            @{ N = 'FreeGB'; E = { [math]::Round($_.SizeRemaining / 1GB, 1) } } -AutoSize |
            Out-String | ForEach-Object { Log $_.Trim() }
    }

    $biwin = Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'BIWIN' } | Select-Object -First 1
    if ($biwin -and (-not $biwin.Size -or $biwin.Size -lt 1GB)) {
        Log 'RESULT: BIWIN detected but capacity=0 - software cannot fix hardware I/O failure.'
        Log 'Try: unplug 30s, rear USB 2.0 port, powered hub, different cable, then rerun with -ForceUsbMassStorage'
        return 2
    }
    if ($biwin -and (Get-Partition -DiskNumber $biwin.Number -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -eq 'G' })) {
        $g = Get-Volume -DriveLetter G -ErrorAction SilentlyContinue
        $free = if ($g) { [math]::Round($g.SizeRemaining / 1GB, 1) } else { '?' }
        Log ("RESULT: OK - data on G: (GCDATA0), ~{0} GB free. Macrium -> G:\MacriumImages\" -f $free)
        Log 'RESULT: EFI browse copy at G:\GCEFISYS (E: is admin-only by Windows design)'
        return 0
    }
    if (-not $biwin) {
        Log 'RESULT: No BIWIN USB disk found. Plug in only the BIWIN HDD and rerun.'
        return 1
    }
    Log 'RESULT: BIWIN visible but G: not assigned - check partitions in Disk Management.'
    return 1
}

# --- main ---
[IO.File]::WriteAllText($LogFile, '', $utf8)
$mode = if ($Recovery) { 'RECOVERY (aggressive)' } else { 'SAFE (EGM boot preserved)' }
Log "=== USB Disk Bring-To-Life Suite (admin) - $mode ==="
Log ("Args: Recovery=$Recovery ForceUsbMassStorage=$ForceUsbMassStorage EfiOnly=$EfiOnly MountOnly=$MountOnly EfiBrowseMount=$EfiBrowseMount AssignEfiDriveLetter=$AssignEfiDriveLetter")

if (-not $Recovery -and ($ForceUsbMassStorage -or $UsbOnly)) {
    Log 'WARN: -ForceUsbMassStorage/-UsbOnly ignored in SAFE mode. Add -Recovery only if disk is not visible.'
}

if ($EfiOnly) {
    if (-not $EfiBrowseMount) {
        Log 'EfiOnly without -EfiBrowseMount: verifying boot only (no ESP mount changes)'
        $diskNum = Find-BiwinDiskNumber
        if (-not $diskNum) { $diskNum = 1 }
        $ready = Test-BiwinBootReadiness -DiskNumber $diskNum
        Write-BootReadinessSummary $ready
        exit $(if ($ready.Ok) { 0 } else { 1 })
    }
    $diskNum = Find-BiwinDiskNumber
    if (-not $diskNum) { $diskNum = 1 }
    $ok = Publish-EfiBrowseMirror -DiskNumber $diskNum
    exit $(if ($ok) { 0 } else { 1 })
}

Write-UsbDiagnostics
if ($Recovery) {
    Log 'Recovery mode: PnP reset and optional UAS fallback'
    Invoke-UsbRecoveryPass -ApplyUasFallback:$ForceUsbMassStorage
}
else {
    Log 'Safe mode: skipping PnP reset (preserves EGM boot reliability)'
}
Ensure-Vds
Update-HostStorageCache -ErrorAction SilentlyContinue | Out-Null

Invoke-DiskPart @('rescan') | Out-Null
Start-Sleep -Seconds 3

$diskNum = Find-BiwinDiskNumber
if (-not $diskNum) {
    Log 'BIWIN not found in diskpart - replug drive, wait 10s, rerun'
    Write-Summary | Out-Null
    exit 1
}
Log "BIWIN = disk $diskNum"

$detail = (Invoke-DiskPart @("select disk $diskNum", 'detail disk', 'list partition'))[0]
$flags = Get-DetailFlags $detail
if ($flags.NoPartitions -or $flags.ZeroId) {
    Log 'WARN: 0 B / no partition table visible'
}

$gd = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue
if ($gd) {
    Log ("Get-Disk: {0} GB Offline={1}" -f ([math]::Round($gd.Size / 1GB, 1)), $gd.IsOffline)
}

$skipToggle = ($flags.Online -or ($gd -and -not $gd.IsOffline)) -and ($flags.NoPartitions -or $flags.ZeroId -or ($gd -and $gd.Size -lt 1GB))
if ($skipToggle) {
    Log 'SKIP offline/online toggle (already online but broken - toggle causes fatal hardware error)'
}
elseif ($flags.Offline -or ($gd -and $gd.IsOffline)) {
    Log 'Disk offline - bringing online'
    try {
        Set-Disk -Number $diskNum -IsOffline $false -ErrorAction Stop
        Log 'Set-Disk online OK'
    }
    catch {
        Log "Set-Disk online failed: $($_.Exception.Message)"
        $null = Invoke-DiskPart @("select disk $diskNum", 'online disk noerr')
    }
}
else {
    $null = Invoke-DiskPart @("select disk $diskNum", 'online disk noerr', 'attributes disk clear readonly noerr')
}

$onlineResult = Invoke-DiskPart @(
    "select disk $diskNum", 'detail disk', 'online disk noerr',
    'attributes disk clear readonly noerr', 'rescan', 'list partition'
)
if (-not $onlineResult[1]) {
    Log 'HARDWARE ERROR: unplug 30s, different USB port/cable, powered hub'
    Write-Summary | Out-Null
    exit 2
}

if ($gd = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue) {
    if ($gd.IsReadOnly) {
        try {
            Set-Disk -Number $diskNum -IsReadOnly $false
            Log 'Cleared disk read-only'
        }
        catch { Log "Read-only clear failed: $($_.Exception.Message)" }
    }
}

if (-not $AssignEfiDriveLetter) { Clear-StaleDriveLetterReservation E $diskNum | Out-Null }
Ensure-BiwinLetters -DiskNumber $diskNum
Start-Sleep -Seconds 1

$bootBefore = Test-BiwinBootReadiness -DiskNumber $diskNum

if (-not $MountOnly -and ($EfiBrowseMount -or $Recovery)) {
    if ($EfiBrowseMount) {
        Publish-EfiBrowseMirror -DiskNumber $diskNum | Out-Null
    }
    else {
        Log 'Skipping G:\GCEFISYS mount in safe mode (use -EfiBrowseMount on workstation only)'
    }
}

$bootAfter = Test-BiwinBootReadiness -DiskNumber $diskNum
Write-BootReadinessSummary $bootAfter

$exitCode = Write-Summary
if (-not $bootAfter.Ok) { $exitCode = 3 }
Log "Log: $LogFile"
exit $exitCode
