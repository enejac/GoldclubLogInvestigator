# Deploy Log Investigator Config Scanner to a lab EGM cabinet (SMB + WinRM mirror to D:).
param(
    [string]$ComputerName = "10.0.0.90",
    [string]$RemoteFolder = "ConfigScanner",
    [string]$LocalDrive = "D",
    [string]$Exe = "LogInvestigator.exe",
    [switch]$SkipMirror
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$srcExe = Join-Path $root $Exe
if (-not (Test-Path -LiteralPath $srcExe)) {
    $srcExe = Join-Path $root "dist\LogInvestigator.exe"
}

if (-not (Test-Path -LiteralPath $srcExe)) {
    Write-Host "Build the exe first: .\build_exe.ps1" -ForegroundColor Yellow
    exit 1
}

$initScript = Join-Path $root "Initialize-LabAccess.ps1"
if (Test-Path -LiteralPath $initScript) {
    & $initScript -Ip @($ComputerName) | Out-Null
} else {
    cmdkey /add:$ComputerName /user:GOLD-CLUB\test /pass:test | Out-Null
}

. (Join-Path $root "LabAccess.ps1")

function Deploy-ToUncRoot {
    param(
        [string]$UncRoot,
        [string]$ReadmePath = ""
    )
    if (-not (Test-Path -LiteralPath $UncRoot)) {
        New-Item -ItemType Directory -Path $UncRoot -Force | Out-Null
    }
    foreach ($subdir in @("snapshots", "reports", "templates")) {
        $path = Join-Path $UncRoot $subdir
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }
    Copy-Item -LiteralPath $srcExe -Destination (Join-Path $UncRoot "LogInvestigator.exe") -Force
    if ($ReadmePath -and (Test-Path -LiteralPath $ReadmePath)) {
        Copy-Item -LiteralPath $ReadmePath -Destination (Join-Path $UncRoot "README.txt") -Force
    }
}

# Stage on C: (always reachable via admin share).
$cUnc = "\\$ComputerName\c$\$RemoteFolder"
$cabinetRoot = "C:\$RemoteFolder"
$readmePath = Join-Path $cUnc "README.txt"
$readmeLines = @(
    "Log Investigator - Config SHA1 Scanner (EGM)",
    "",
    "Run: $cabinetRoot\LogInvestigator.exe",
    "Open the Config Scanner tab.",
    "",
    "  Auto-detect repo  - G: or C:\Goldclub\slot on cabinet",
    "  Scan now          - snapshot config tagged with build version",
    "  Set baseline      - rename snapshot to slot_baseline / roulette_baseline_...",
    "",
    "Data folder: $cabinetRoot\ (snapshots, reports, config.json)"
)
if (-not $SkipMirror) {
    $readmeLines[2] = "Run: ${LocalDrive}:\$RemoteFolder\LogInvestigator.exe"
    $readmeLines[9] = "Data folder: ${LocalDrive}:\$RemoteFolder\ (snapshots, reports, config.json)"
}
if (-not (Test-Path -LiteralPath $cUnc)) {
    New-Item -ItemType Directory -Path $cUnc -Force | Out-Null
}
Set-Content -LiteralPath $readmePath -Value $readmeLines -Encoding UTF8
Deploy-ToUncRoot -UncRoot $cUnc

# Mirror to D:\ on the cabinet (D$ SMB when exposed, else WinRM copy from C:\ stage).
$localRoot = "${LocalDrive}:\$RemoteFolder"
$pushedLocal = $false
$mirrorMethod = ""

if (-not $SkipMirror) {
    $dUnc = "\\$ComputerName\${LocalDrive}`$\$RemoteFolder"
    try {
        $driveRoot = "\\$ComputerName\${LocalDrive}`$"
        if (Test-Path -LiteralPath $driveRoot -ErrorAction Stop) {
            Deploy-ToUncRoot -UncRoot $dUnc -ReadmePath $readmePath
            $pushedLocal = $true
            $mirrorMethod = "SMB ($dUnc)"
        }
    }
    catch {
        Write-Host "[*] D`$ share not reachable over SMB; mirroring via WinRM..." -ForegroundColor DarkGray
    }

    if (-not $pushedLocal) {
        $mirrorScript = {
            param($DriveLetter, $Folder)
            $drive = "${DriveLetter}:"
            if (-not (Test-Path $drive)) { return "NO_DRIVE" }
            $destRoot = Join-Path $drive $Folder
            $stage = Join-Path "C:\" $Folder
            New-Item -ItemType Directory -Path $destRoot,
                (Join-Path $destRoot "snapshots"),
                (Join-Path $destRoot "reports"),
                (Join-Path $destRoot "templates") -Force | Out-Null
            Copy-Item (Join-Path $stage "LogInvestigator.exe") (Join-Path $destRoot "LogInvestigator.exe") -Force
            Copy-Item (Join-Path $stage "README.txt") (Join-Path $destRoot "README.txt") -Force -ErrorAction SilentlyContinue
            return "OK"
        }
        try {
            $mirrorResult = Invoke-LabWinRmCommand -ComputerName $ComputerName `
                -ScriptBlock $mirrorScript -ArgumentList @($LocalDrive, $RemoteFolder)
            if ($mirrorResult -eq "OK") {
                $pushedLocal = $true
                $mirrorMethod = "WinRM"
            }
            elseif ($mirrorResult -eq "NO_DRIVE") {
                Write-Host "Drive ${LocalDrive}: not present on $ComputerName - left copy on C:\$RemoteFolder only." -ForegroundColor Yellow
            }
            else {
                Write-Host "WinRM push to ${localRoot} returned unexpected result: $mirrorResult" -ForegroundColor Yellow
            }
        }
        catch {
            Write-Host "WinRM push to ${localRoot} failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "Deployed to cabinet $ComputerName"
Write-Host "  Staged:  \\$ComputerName\c$\$RemoteFolder\LogInvestigator.exe"
if ($SkipMirror) {
    Write-Host "  Run on EGM: $cabinetRoot\LogInvestigator.exe"
}
elseif ($pushedLocal) {
    Write-Host "  Mirrored: ${localRoot}\LogInvestigator.exe ($mirrorMethod)"
    Write-Host "  Run on EGM: ${localRoot}\LogInvestigator.exe"
}
else {
    Write-Host "  Run on EGM: $cabinetRoot\LogInvestigator.exe (or copy to ${localRoot})"
}
