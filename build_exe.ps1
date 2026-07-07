<#
.SYNOPSIS
    Build the Log Investigator desktop UI into a single .exe.

.DESCRIPTION
    Wraps PyInstaller using LogInvestigator.spec. Output: dist\LogInvestigator.exe

    Profiles:
      (default)   lite  - smallest binary; AI + OpenCV excluded (graceful fallback).
      -BundleAi   ai    - lite + Google Gemini AI summary SDKs.
      -Full       full  - everything: AI SDKs + OpenCV/numpy (QR scan) + pywin32.

.PARAMETER Full
    Build the full-stack binary with every optional feature bundled.

.PARAMETER BundleAi
    Bundle only the optional Google Gemini SDKs (ignored if -Full is set).

.PARAMETER InstallDeps
    pip-install the runtime requirements before building (recommended for -Full).

.EXAMPLE
    ./build_exe.ps1                       # lite
    ./build_exe.ps1 -BundleAi             # + AI
    ./build_exe.ps1 -Full                 # full stack features
    ./build_exe.ps1 -Full -InstallDeps    # full + install deps first
#>
[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$BundleAi,
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Full)        { $profile = "full" }
elseif ($BundleAi) { $profile = "ai" }
else              { $profile = "lite" }
$env:LI_BUILD = $profile
$env:BUNDLE_AI = $null  # avoid back-compat ambiguity; LI_BUILD wins

Write-Host "Build profile: $profile" -ForegroundColor Cyan

# Ensure PyInstaller is available.
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not found - installing..." -ForegroundColor Yellow
    python -m pip install pyinstaller
}

if ($InstallDeps) {
    Write-Host "Installing runtime requirements..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
}

# Warn early if a full build is missing optional deps.
if ($profile -eq "full") {
    foreach ($mod in @("cv2", "numpy", "google.genai")) {
        python -c "import $mod" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARN: '$mod' not importable - that feature will be omitted. Run with -InstallDeps." -ForegroundColor Yellow
        }
    }
}

$exe = Join-Path $PSScriptRoot "dist\LogInvestigator.exe"
$before = $null
if (Test-Path $exe) {
    $before = (Get-Item $exe).LastWriteTimeUtc
}

python -m PyInstaller --noconfirm --clean LogInvestigator.spec
$pyiExit = $LASTEXITCODE

if ($pyiExit -ne 0) {
    Write-Host ""
    Write-Host "PyInstaller failed (exit $pyiExit)." -ForegroundColor Red
    if (Test-Path $exe) {
        Write-Host "If you see 'Access is denied' above, close LogInvestigator.exe (or any app locking dist\LogInvestigator.exe) and rebuild." -ForegroundColor Yellow
    }
    exit $pyiExit
}

if (-not (Test-Path $exe)) {
    Write-Host "Build finished but exe not found at $exe" -ForegroundColor Red
    exit 1
}

$after = (Get-Item $exe).LastWriteTimeUtc
if ($before -and $after -le $before) {
    Write-Host ""
    Write-Host "WARN: exe timestamp did not change - output may be stale. Close any running LogInvestigator.exe and rebuild." -ForegroundColor Yellow
}

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host ("Build complete ({0}): {1} ({2} MB)" -f $profile, $exe, $sizeMb) -ForegroundColor Green
