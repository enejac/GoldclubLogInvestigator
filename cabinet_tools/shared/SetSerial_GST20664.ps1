#Requires -RunAsAdministrator
param(
    [string] $TargetMachineName = 'GST20664',
    [string] $TargetSerial = '20664',
    [switch] $WhatIf,
    [switch] $SkipHostnameRename,
    [switch] $ScanOnly
)

$ErrorActionPreference = 'Stop'
$ProductKind = 'ST'
$TargetEgmId = "GCC_${ProductKind}_${TargetSerial}_01"
$GoldclubRoot = 'C:\goldclub'
$LogFile = Join-Path $GoldclubRoot 'var\log\set_serial_GST20664.log'

function Write-Log([string] $Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $line | Add-Content -Path $LogFile -Encoding ASCII
}

function Get-CurrentIdentity {
    $conf = Join-Path $GoldclubRoot 'var\state\maintenance\ProductSerialNumber.conf'
    $oldSerial = $null
    $oldMachine = $null
    if (Test-Path $conf) {
        foreach ($line in Get-Content $conf) {
            if ($line -match '^\s*ProductSerialNumber:\s*(.+)\s*$') { $oldSerial = $Matches[1].Trim() }
            if ($line -match '^\s*MachineName:\s*(.+)\s*$') { $oldMachine = $Matches[1].Trim() }
        }
    }
    if (-not $oldSerial -and $oldMachine -match '^GST(\d+)$') { $oldSerial = $Matches[1] }
    if (-not $oldMachine -and $oldSerial) { $oldMachine = "GST$oldSerial" }
    return [PSCustomObject]@{
        Serial = $oldSerial
        MachineName = $oldMachine
        EgmId = if ($oldSerial) { "GCC_${ProductKind}_${oldSerial}_01" } else { $null }
    }
}

function Test-TextFile([string] $Path) {
    if (-not (Test-Path $Path -PathType Leaf)) { return $false }
    $ext = [IO.Path]::GetExtension($Path).ToLowerInvariant()
    $skipExt = @('.exe','.dll','.png','.jpg','.jpeg','.gif','.zip','.7z','.pdf','.pdb','.woff','.ttf','.eot','.mp3','.mp4','.avi','.wav')
    if ($skipExt -contains $ext) { return $false }
    try { if ((Get-Item $Path).Length -gt 20MB) { return $false } } catch { return $false }
    return $true
}

function Get-ReplacementPairs([object] $Old) {
    $pairs = New-Object System.Collections.Generic.List[object]
    if ($Old.MachineName -and $Old.MachineName -ne $TargetMachineName) {
        [void]$pairs.Add([PSCustomObject]@{ Old = $Old.MachineName; New = $TargetMachineName })
    }
    if ($Old.Serial -and $Old.Serial -ne $TargetSerial) {
        [void]$pairs.Add([PSCustomObject]@{ Old = $Old.Serial; New = $TargetSerial })
    }
    if ($Old.EgmId -and $Old.EgmId -ne $TargetEgmId) {
        [void]$pairs.Add([PSCustomObject]@{ Old = $Old.EgmId; New = $TargetEgmId })
    }
    if ($pairs.Count -eq 0) {
        [void]$pairs.Add([PSCustomObject]@{ Old = 'GST19737'; New = $TargetMachineName })
        [void]$pairs.Add([PSCustomObject]@{ Old = '19737'; New = $TargetSerial })
        [void]$pairs.Add([PSCustomObject]@{ Old = 'GCC_ST_19737_01'; New = $TargetEgmId })
    }
    return $pairs
}

function Test-FileHasReplacements([string] $Path, $Pairs) {
    if (-not (Test-TextFile $Path)) { return $false }
    try { $text = [IO.File]::ReadAllText($Path) } catch { return $false }
    foreach ($p in $Pairs) {
        if ($text.IndexOf($p.Old, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

function Update-TextFile([string] $Path, $Pairs) {
    if (-not (Test-TextFile $Path)) { return $false }
    $updated = [IO.File]::ReadAllText($Path)
    $changed = $false
    foreach ($p in $Pairs) {
        if ($updated.IndexOf($p.Old, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
        $pattern = [Regex]::Escape($p.Old)
        $newText = [Regex]::Replace($updated, $pattern, $p.New, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($newText -ne $updated) { $updated = $newText; $changed = $true }
    }
    if (-not $changed) { return $false }
    if ($WhatIf -or $ScanOnly) { Write-Log "WOULD UPDATE: $Path"; return $true }
    [IO.File]::WriteAllText($Path, $updated, [Text.Encoding]::UTF8)
    Write-Log "UPDATED: $Path"
    return $true
}

function Update-ProductSerialConf {
    $path = Join-Path $GoldclubRoot 'var\state\maintenance\ProductSerialNumber.conf'
    $body = @(
        "ProductKind: $ProductKind"
        "ProductSerialNumber: $TargetSerial"
        'ProductPart: -'
        "MachineName: $TargetMachineName"
    ) -join "`r`n"
    if ($WhatIf -or $ScanOnly) { Write-Log "WOULD WRITE: $path"; return }
    $dir = Split-Path $path -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [IO.File]::WriteAllText($path, $body + "`r`n", [Text.Encoding]::ASCII)
    Write-Log "WROTE: $path"
}

function Update-MgConfigMachineId {
    $path = Join-Path $GoldclubRoot 'slot\themes\mgconfig.xml'
    if (-not (Test-Path $path)) { return }
    try {
        [xml]$doc = Get-Content -Path $path -Encoding UTF8
        $node = $doc.SelectSingleNode('/Multigamer/MachineID')
        if (-not $node) { return }
        if ($node.InnerText -eq $TargetMachineName) { return }
        if ($WhatIf -or $ScanOnly) { Write-Log "WOULD SET mgconfig MachineID -> $TargetMachineName"; return }
        $node.InnerText = $TargetMachineName
        $doc.Save($path)
        Write-Log "UPDATED mgconfig MachineID -> $TargetMachineName"
    } catch {
        Write-Log "WARN mgconfig: $($_.Exception.Message)"
    }
}

function Rename-EgmStateFiles([object] $Old) {
    if (-not $Old.EgmId -or $Old.EgmId -eq $TargetEgmId) { return }
    $gcRoot = Join-Path $GoldclubRoot 'var\state\GoldClub.Aurum.Services\GCMessenger'
    if (-not (Test-Path $gcRoot)) { return }
    Get-ChildItem -Path $gcRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*$($Old.EgmId)*" } |
        ForEach-Object {
            $newName = $_.Name.Replace($Old.EgmId, $TargetEgmId)
            if ($WhatIf -or $ScanOnly) { Write-Log "WOULD RENAME: $($_.FullName) -> $newName"; return }
            $dest = Join-Path $_.DirectoryName $newName
            if (Test-Path $dest) { Remove-Item $dest -Force }
            Rename-Item -LiteralPath $_.FullName -NewName $newName
            Write-Log "RENAMED: $($_.FullName) -> $newName"
        }
}

function Find-SerialHits($Pairs) {
    $roots = @(
        (Join-Path $GoldclubRoot 'var\state'),
        (Join-Path $GoldclubRoot 'Services\aurum\config'),
        (Join-Path $GoldclubRoot 'slot\themes')
    )
    $hits = @()
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
            if (Test-FileHasReplacements $_.FullName $Pairs) { $hits += $_.FullName }
        }
    }
    return $hits | Select-Object -Unique
}

function Try-DisableUwf {
    $filterModule = Join-Path $GoldclubRoot 'bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
    if (-not (Test-Path $filterModule)) { return $false }
    try {
        Import-Module $filterModule -ErrorAction Stop
        $state = Show-UWFState
        Write-Log "UWF state: $state"
        if ($state -eq 'enabled') {
            if ($WhatIf -or $ScanOnly) { Write-Log 'WOULD disable UWF before writing'; return $false }
            Disable-UWF | Out-Null
            Write-Log 'Disabled UWF for persistent writes'
            return $true
        }
    } catch { Write-Log "WARN UWF: $($_.Exception.Message)" }
    return $false
}

Write-Log "=== SetSerial_GST20664 start (WhatIf=$WhatIf ScanOnly=$ScanOnly) ==="
$OldIdentity = Get-CurrentIdentity
Write-Log ("Current: serial={0} machine={1} egm={2}" -f $OldIdentity.Serial, $OldIdentity.MachineName, $OldIdentity.EgmId)

$pairs = Get-ReplacementPairs $OldIdentity
$hitFiles = Find-SerialHits $pairs
Write-Log ("Found {0} matching file(s)" -f @($hitFiles).Count)
foreach ($f in $hitFiles) { Write-Log "  HIT: $f" }
if ($ScanOnly) { Write-Log 'ScanOnly complete.'; exit 0 }

$null = Try-DisableUwf
Update-ProductSerialConf
Update-MgConfigMachineId
foreach ($f in $hitFiles) { Update-TextFile $f $pairs }
Rename-EgmStateFiles $OldIdentity

if (-not $SkipHostnameRename -and $env:COMPUTERNAME -ne $TargetMachineName) {
    if ($WhatIf) {
        Write-Log "WOULD run Rename-CabinetHostname.ps1 for $TargetMachineName (two reboots; see script)"
    } else {
        $renameScript = Join-Path $GoldclubRoot 'var\state\maintenance\Rename-CabinetHostname.ps1'
        $usbRename = 'D:\Rename-CabinetHostname.ps1'
        if (-not (Test-Path $renameScript) -and (Test-Path $usbRename)) {
            Copy-Item -Force $usbRename $renameScript
        }
        if (Test-Path $renameScript) {
            Write-Log "Delegating hostname rename to $renameScript (GoldClub UWF two-step)"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $renameScript -TargetMachineName $TargetMachineName
        } else {
            Write-Log 'WARN Rename-CabinetHostname.ps1 not found; hostname not changed'
        }
    }
}

Write-Log '=== SetSerial_GST20664 finished ==='


