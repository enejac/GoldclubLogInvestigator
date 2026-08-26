# Deploy Log Investigator + cabinet scripts to a USB folder (portable QA).
param(
    [string]$Dest = "H:\ConfigScanner",
    [string]$Exe = "LogInvestigator.exe"
)

$ErrorActionPreference = "Stop"
# Never Set-Location onto Dest: a PowerShell cwd on \\server\USB_Remote keeps the
# share open and Explorer reports "Folder In Use" after deploy.
$script:DeployStartLocation = (Get-Location).Path
$root = $PSScriptRoot
$srcExe = Join-Path $root $Exe
if (-not (Test-Path -LiteralPath $srcExe)) {
    $srcExe = Join-Path $root "dist\LogInvestigator.exe"
}
if (-not (Test-Path -LiteralPath $srcExe)) {
    Write-Host "Build the exe first: .\build_exe.ps1" -ForegroundColor Yellow
    exit 1
}

function Copy-DeployFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (-not (Test-Path -LiteralPath $Source)) { return }
    $srcPath = (Resolve-Path -LiteralPath $Source).Path
    $tgtPath = [System.IO.Path]::GetFullPath($Target)
    if ($srcPath -ieq $tgtPath) { return }
    $tgtDir = Split-Path $tgtPath -Parent
    if ($tgtDir) {
        [void][System.IO.Directory]::CreateDirectory($tgtDir)
    }
    # Scripts: always UTF-8 no BOM (never Copy-Item — preserves UTF-16 corruption).
    $ext = [System.IO.Path]::GetExtension($srcPath).ToLowerInvariant()
    if ($ext -in @('.ps1', '.bat', '.cmd', '.txt', '.md', '.json', '.xml', '.yml', '.yaml', '.vbs')) {
        $bytes = [System.IO.File]::ReadAllBytes($srcPath)
        $text = if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
            [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
        } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
            [System.Text.Encoding]::BigEndianUnicode.GetString($bytes, 2, $bytes.Length - 2)
        } elseif ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
        } elseif ($bytes -contains [byte]0) {
            [System.Text.Encoding]::Unicode.GetString($bytes)
        } else {
            [System.Text.Encoding]::UTF8.GetString($bytes)
        }
        [System.IO.File]::WriteAllText($tgtPath, $text, [System.Text.UTF8Encoding]::new($false))
        return
    }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
}

function Copy-Tree {
    param([string]$SourceDir, [string]$TargetDir)
    if (-not (Test-Path -LiteralPath $SourceDir)) { return }
    if (-not (Test-Path -LiteralPath $TargetDir)) {
        New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
    }
    # Materialize the listing: a live enumerator over a UNC folder holds the share.
    $children = @(Get-ChildItem -LiteralPath $SourceDir -Force)
    foreach ($item in $children) {
        $tgt = Join-Path $TargetDir $item.Name
        if ($item.PSIsContainer) {
            Copy-Tree -SourceDir $item.FullName -TargetDir $tgt
        } else {
            Copy-DeployFile -Source $item.FullName -Target $tgt
        }
    }
}

function Release-DeployShareHandles {
    # Drop cwd / enumerator refs so SMB can release USB_Remote after copy.
    try {
        if ($script:DeployStartLocation -and (Test-Path -LiteralPath $script:DeployStartLocation)) {
            Set-Location -LiteralPath $script:DeployStartLocation
        } else {
            Set-Location -LiteralPath $env:USERPROFILE
        }
    } catch {
        Set-Location -LiteralPath $env:TEMP
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if (-not (Test-Path -LiteralPath $Dest)) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
}

# Remove obsolete / superseded deploy junk (old names + one-off Macrium helpers)
$remove = @(
    "Scan-Config.bat", "Scan-Config.cmd", "Scan-Config.ps1",
    "Compare-Scans.bat", "Compare-Scans.cmd", "Compare-Scans.ps1",
    "Compare-ToLatest.bat", "Compare-ToLatest.cmd", "Compare-ToLatest.ps1",
    "Sync-ToUsb.bat", "_RunScript.bat", "_Launch.ps1",
    "MIGRATED.txt", "README.md",
    "Invoke-RuletaSuperadminSetup.ps1", "Invoke-RuletaSuperadminSetup.bat", "JTB.BAT",
    "D__.START_ONEHAND.bat", "D__CopyOnlogon.bat", "D__onlogon.ps1",
    "D__RustDesk_RUN_RUSTDESK.bat", "D___RUN_RUSTDESK.bat",
    "D__HWDrivers_ST3_copy_hwdrivers.bat", "D__slot1onlogon.ps1", "D___LogFiles_run.bat",
    "Enable-UsbDiskForMacrium.ps1", "Grant-BiwinEAccess.ps1",
    "_ENABLE_DISK_FOR_MACRIUM.bat", "_GRANT_E_ACCESS.bat",
    "_OPEN_E_ADMIN.bat", "_OPEN_GCEFISYS.bat",
    "cabinet-session-scan.log", "JT25.log", "onlogon-run.log",
    "LogInvestigator.log"
)
foreach ($name in $remove) {
    $item = Join-Path $Dest $name
    if (Test-Path -LiteralPath $item) {
        try {
            Remove-Item -LiteralPath $item -Force -Recurse -ErrorAction Stop
            Write-Host "Removed: $name"
        } catch {
            Write-Host "Skip remove (in use): $name" -ForegroundColor Yellow
        }
    }
}
$libDir = Join-Path $Dest "lib"
if (Test-Path -LiteralPath $libDir) {
    Remove-Item -LiteralPath $libDir -Recurse -Force
    Write-Host "Removed: lib\"
}

# Wipe flat files left under Dest\scripts (keep only roulette|slot|shared buckets)
$destScriptsPre = Join-Path $Dest "scripts"
if (Test-Path -LiteralPath $destScriptsPre) {
    Get-ChildItem -LiteralPath $destScriptsPre -Force | ForEach-Object {
        if ($_.Name -notin @("roulette", "slot", "shared")) {
            Remove-Item -LiteralPath $_.FullName -Force -Recurse
            Write-Host "Removed flat scripts\$($_.Name)"
        }
    }
}

$destExe = Join-Path $Dest "LogInvestigator.exe"
Copy-DeployFile -Source $srcExe -Target $destExe
Write-Host "Exe: $destExe"

# Offline AI Helper GGUF (optional; multi-GB — only if present in repo models\)
$srcModels = Join-Path $root "models"
$destModels = Join-Path $Dest "models"
if (Test-Path -LiteralPath $srcModels) {
    $ggufs = @(Get-ChildItem -LiteralPath $srcModels -Filter "*.gguf" -File -ErrorAction SilentlyContinue)
    if ($ggufs.Count -gt 0) {
        Copy-Tree -SourceDir $srcModels -TargetDir $destModels
        Write-Host "Copied: models\ ($($ggufs.Count) GGUF)"
    }
}

# Portable Chromium for HTML reports (optional; skip user-data)
$srcBrowserChrome = Join-Path $root "config-scanner\browser\chrome-win64\chrome.exe"
if (Test-Path -LiteralPath $srcBrowserChrome) {
    $srcChromeDir = Join-Path $root "config-scanner\browser\chrome-win64"
    $destChromeDir = Join-Path $Dest "browser\chrome-win64"
    if (Test-Path -LiteralPath $destChromeDir) {
        Remove-Item -LiteralPath $destChromeDir -Recurse -Force
    }
    Copy-Tree -SourceDir $srcChromeDir -TargetDir $destChromeDir
    $srcBrowserReadme = Join-Path $root "config-scanner\browser\README.md"
    Copy-DeployFile -Source $srcBrowserReadme -Target (Join-Path $Dest "browser\README.md")
    Write-Host "Copied: browser\chrome-win64\"
}

$toolsRoot = Join-Path $root "cabinet_tools"

# ConfigScanner root: app + data only. Canonical scripts live on the stick as usb_scripts\.
$keepRootFiles = @("LogInvestigator.exe", "README.txt", "config.json", "baseline.json")
$keepRootDirs = @("snapshots", "reports", "templates", "browser", "D__JT25", "models", "cabinet_tools", "software_versions", "_tmp_logs")
$legacyScripts = Join-Path $Dest "scripts"
if (Test-Path -LiteralPath $legacyScripts) {
    Remove-Item -LiteralPath $legacyScripts -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Removed leftover ConfigScanner\scripts\"
}

# Drop leftover flat scripts at Dest root (keep data dirs + app files only)
Get-ChildItem -LiteralPath $Dest -Force | ForEach-Object {
    if ($_.PSIsContainer) {
        if ($_.Name -notin $keepRootDirs) { return }
    } else {
        if ($_.Name -notin $keepRootFiles) {
            Remove-Item -LiteralPath $_.FullName -Force
            Write-Host "Removed ConfigScanner root: $($_.Name)"
        }
    }
}
$djt = Join-Path (Join-Path $toolsRoot "roulette") "D__JT25"
if (Test-Path -LiteralPath $djt) {
    $tgt = Join-Path $Dest "D__JT25"
    if (Test-Path -LiteralPath $tgt) { Remove-Item $tgt -Recurse -Force }
    Copy-Item -LiteralPath $djt -Destination $tgt -Recurse -Force
    Write-Host "Copied: D__JT25\"
}

$readme = @"
Log Investigator - Config SHA1 Scanner (USB)

Double-click LogInvestigator.exe and open Config Scanner (Ctrl+Shift+C).
AI Helper (Ctrl+Shift+A): offline config/log search; optional GGUF in models\.

Scripts live on the USB stick root as usb_scripts\ (not ConfigScanner\scripts):
  usb_scripts\roulette\   Kill-All, Run-FullStack, Fix-SerialPortLocations, JT25
  usb_scripts\slot\       START_ONEHAND, slot1onlogon
  usb_scripts\shared\     Scan-CabinetSession, _share, Unlock-WriteFilter, onlogon

USB stick root keeps thin .bat shortcuts only (cabinet_tools\usb_root\) that call into
usb_scripts\... — do not put .ps1 next to those shortcuts.

Data: snapshots\, reports\, browser\ (portable Chromium), config.json, models\ (optional offline LLM)
"@
[System.IO.File]::WriteAllText((Join-Path $Dest "README.txt"), ($readme -replace "`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))

# When Dest is <usb>\ConfigScanner, also update stick root shortcuts + usb_scripts mirror.
$usbRoot = Split-Path $Dest -Parent
$isLocalDrive = ($Dest -match '^[A-Za-z]:\\')
$isUncConfigScanner = ($Dest -match '(?i)[\\/]ConfigScanner$')
if ($usbRoot -and (Test-Path -LiteralPath $usbRoot) -and ($isLocalDrive -or $isUncConfigScanner)) {
    $usbScriptsMirror = Join-Path $usbRoot "usb_scripts"
    if (-not (Test-Path -LiteralPath $usbScriptsMirror)) {
        New-Item -ItemType Directory -Path $usbScriptsMirror -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $usbScriptsMirror -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -notin @("roulette", "slot", "shared")) {
            Remove-Item -LiteralPath $_.FullName -Force -Recurse
            Write-Host "Removed usb_scripts\$($_.Name)"
        }
    }
    foreach ($bucket in @("roulette", "slot", "shared")) {
        $src = Join-Path $toolsRoot $bucket
        $tgt = Join-Path $usbScriptsMirror $bucket
        if (Test-Path -LiteralPath $tgt) { Remove-Item $tgt -Recurse -Force }
        Copy-Tree -SourceDir $src -TargetDir $tgt
        Write-Host "Mirrored: usb_scripts\$bucket\"
    }

    $usbRootSrc = Join-Path $toolsRoot "usb_root"
    if (Test-Path -LiteralPath $usbRootSrc) {
        Get-ChildItem -LiteralPath $usbRootSrc -File -Filter "*.bat" | ForEach-Object {
            Copy-DeployFile -Source $_.FullName -Target (Join-Path $usbRoot $_.Name)
        }
        Write-Host "Updated USB root .bat shortcuts -> usb_scripts\..."
    }

    # Remove companion .ps1 that used to sit next to root bats (now under usb_scripts)
    $rootPs1Gone = @(
        "Kill-All.ps1", "Kill-GoldClubProcesses.ps1", "GoldClubServices.ps1",
        "Run-FullStack.ps1", "Start-GoldClubProcesses.ps1",
        "onlogon.ps1", "slot1onlogon.ps1", "Scan-CabinetSession.ps1",
        "_RUN_RUSTDESK.ps1", "Rename-CabinetHostname.ps1", "SetSerial_GST20664.ps1"
    )
    foreach ($name in $rootPs1Gone) {
        $p = Join-Path $usbRoot $name
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Force
            Write-Host "Removed USB root: $name"
        }
    }
}

Release-DeployShareHandles

Write-Host ""
Write-Host "Deployed: $destExe"
Write-Host "Scripts:  $destScripts\roulette|slot|shared"
Write-Host "Launch LogInvestigator.exe -> Config Scanner tab."
