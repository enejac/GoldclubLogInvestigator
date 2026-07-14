# Deploy Log Investigator Config Scanner to a lab EGM cabinet (SMB + optional PsExec to D:).
param(
    [string]$ComputerName = "10.0.0.90",
    [string]$RemoteFolder = "ConfigScanner",
    [string]$LocalDrive = "D",
    [string]$Exe = "dist\LogInvestigator.exe",
    [string]$PsExecPath = "C:\Tools\PSTools\PsExec.exe",
    [switch]$SkipMirror
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$srcExe = Join-Path $root $Exe

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

function Deploy-ToUncRoot {
    param([string]$UncRoot)
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
}

# Stage on C: (always reachable via admin share).
$cUnc = "\\$ComputerName\c$\$RemoteFolder"
Deploy-ToUncRoot -UncRoot $cUnc

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
Set-Content -LiteralPath $readmePath -Value $readmeLines -Encoding UTF8

# Mirror to D:\ on the cabinet (D$ admin share is often not exposed over SMB).
$localRoot = "${LocalDrive}:\$RemoteFolder"
$pushedLocal = $false
if (-not $SkipMirror -and (Test-Path -LiteralPath $PsExecPath)) {
    . (Join-Path $root "LabAccess.ps1")
    $psAuth = Get-LabPsExecArgs
    $remotePs = @"
`$drive = '${LocalDrive}:'
`$root = Join-Path `$drive '$RemoteFolder'
`$stage = 'C:\$RemoteFolder'
if (-not (Test-Path `$drive)) { exit 42 }
New-Item -ItemType Directory -Path `$root, (Join-Path `$root 'snapshots'), (Join-Path `$root 'reports'), (Join-Path `$root 'templates') -Force | Out-Null
Copy-Item (Join-Path `$stage 'LogInvestigator.exe') (Join-Path `$root 'LogInvestigator.exe') -Force
Copy-Item (Join-Path `$stage 'README.txt') (Join-Path `$root 'README.txt') -Force -ErrorAction SilentlyContinue
"@
    & $PsExecPath "\\$ComputerName" -accepteula @psAuth -s -n 120 powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $remotePs
    if ($LASTEXITCODE -eq 0) {
        $pushedLocal = $true
    } elseif ($LASTEXITCODE -eq 42) {
        Write-Host "Drive ${LocalDrive}: not present on $ComputerName - left copy on C:\$RemoteFolder only." -ForegroundColor Yellow
    } else {
        Write-Host "PsExec push to ${localRoot} failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
    }
} elseif (-not $SkipMirror) {
    Write-Host "PsExec not found at $PsExecPath - skipped ${localRoot} push." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Deployed to cabinet $ComputerName"
Write-Host "  Staged:  \\$ComputerName\c$\$RemoteFolder\LogInvestigator.exe"
if ($SkipMirror) {
    Write-Host "  Run on EGM: $cabinetRoot\LogInvestigator.exe"
} elseif ($pushedLocal) {
    Write-Host "  Run on EGM: ${localRoot}\LogInvestigator.exe"
} else {
    Write-Host "  Run on EGM: $cabinetRoot\LogInvestigator.exe (or copy to ${localRoot})"
}
