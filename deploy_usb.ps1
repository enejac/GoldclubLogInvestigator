# Deploy Log Investigator to a USB folder (portable QA).
param(
    [string]$Dest = "H:\ConfigScanner",
    [string]$Exe = "dist\LogInvestigator.exe"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$srcExe = Join-Path $root $Exe

if (-not (Test-Path -LiteralPath $srcExe)) {
    Write-Host "Build the exe first: .\build_exe.ps1" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path -LiteralPath $Dest)) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
}

$remove = @(
    "Scan-Config.bat", "Scan-Config.cmd", "Scan-Config.ps1",
    "Compare-Scans.bat", "Compare-Scans.cmd", "Compare-Scans.ps1",
    "Compare-ToLatest.bat", "Compare-ToLatest.cmd", "Compare-ToLatest.ps1",
    "Sync-ToUsb.bat", "_RunScript.bat", "_Launch.ps1",
    "MIGRATED.txt", "README.md"
)
foreach ($name in $remove) {
    $item = Join-Path $Dest $name
    if (Test-Path -LiteralPath $item) {
        Remove-Item -LiteralPath $item -Force
        Write-Host "Removed: $name"
    }
}
$libDir = Join-Path $Dest "lib"
if (Test-Path -LiteralPath $libDir) {
    Remove-Item -LiteralPath $libDir -Recurse -Force
    Write-Host "Removed: lib\"
}

$destExe = Join-Path $Dest "LogInvestigator.exe"
Copy-Item -LiteralPath $srcExe -Destination $destExe -Force

$readme = @"
Log Investigator - Config SHA1 Scanner (USB)

Double-click LogInvestigator.exe and open the Config Scanner tab.

  Scan now           - snapshot D:\config\ tagged with build version
  Compare latest two - HTML diff report
  Open snapshots / reports folder - Explorer shortcuts

Data in this folder: snapshots\, reports\, config.json
"@
Set-Content -LiteralPath (Join-Path $Dest "README.txt") -Value $readme -Encoding UTF8

Write-Host ""
Write-Host "Deployed: $destExe"
Write-Host "Launch LogInvestigator.exe -> Config Scanner tab."
