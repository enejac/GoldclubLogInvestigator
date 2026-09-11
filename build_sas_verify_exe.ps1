<#
.SYNOPSIS
    Build the standalone SAS Verify Meters tool into a single .exe.

.DESCRIPTION
    Wraps PyInstaller using SasVerifyMeters.spec.
    Output: SasVerifyMeters.exe at the GoldclubLogInvestigator repo root.

.PARAMETER InstallDeps
    pip-install the runtime requirements before building.

.EXAMPLE
    ./build_sas_verify_exe.ps1
    ./build_sas_verify_exe.ps1 -InstallDeps
#>
[CmdletBinding()]
param(
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvScripts = Join-Path $PSScriptRoot ".venv\Scripts"
if (Test-Path -LiteralPath (Join-Path $venvScripts "python.exe")) {
    $env:Path = "$venvScripts;$env:Path"
}

Write-Host "Building SasVerifyMeters.exe (standalone SAS verify) -> repo root" -ForegroundColor Cyan

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not found - installing..." -ForegroundColor Yellow
    python -m pip install pyinstaller
}

if ($InstallDeps) {
    Write-Host "Installing runtime requirements..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
}

$exe = Join-Path $PSScriptRoot "SasVerifyMeters.exe"
$before = $null
if (Test-Path $exe) {
    $before = (Get-Item $exe).LastWriteTimeUtc
}

# PyInstaller logs its INFO stream to stderr. Under $ErrorActionPreference =
# "Stop" PowerShell promotes the first such line to a terminating
# NativeCommandError, so the build died on the banner while the exe built fine.
# Judge the run by its exit code instead.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    python -m PyInstaller --noconfirm --clean --distpath . SasVerifyMeters.spec 2>&1 |
        ForEach-Object { Write-Host $_ }
    $pyiExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}

if ($pyiExit -ne 0) {
    Write-Host ""
    Write-Host "PyInstaller failed (exit $pyiExit)." -ForegroundColor Red
    if (Test-Path $exe) {
        Write-Host "If you see 'Access is denied' above, close SasVerifyMeters.exe and rebuild." -ForegroundColor Yellow
    }
    exit $pyiExit
}

if (-not (Test-Path $exe)) {
    # Spec also sets distpath="."; older PyInstaller may still drop under dist/.
    $fallback = Join-Path $PSScriptRoot "dist\SasVerifyMeters.exe"
    if (Test-Path $fallback) {
        Copy-Item -LiteralPath $fallback -Destination $exe -Force
        Write-Host "Moved dist\SasVerifyMeters.exe -> repo root" -ForegroundColor DarkGray
    }
}

if (-not (Test-Path $exe)) {
    Write-Host "Build finished but exe not found at $exe" -ForegroundColor Red
    exit 1
}

$after = (Get-Item $exe).LastWriteTimeUtc
if ($before -and $after -le $before) {
    Write-Host ""
    Write-Host "WARN: exe timestamp did not change - output may be stale. Close any running SasVerifyMeters.exe and rebuild." -ForegroundColor Yellow
}

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

$forbidden = @(
    'DallasSplice',
    'Invoke-Dallas',
    'Invoke-WinDivertAft',
    'Send-TestAft1000',
    'WdInject',
    'WdPollInject',
    'WinDivert64.sys',
    'tactic-c-credit-meter-inject'
)
$bytes = [System.IO.File]::ReadAllBytes($exe)
$ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
$hits = @($forbidden | Where-Object { $ascii.Contains($_) })
if ($hits.Count -gt 0) {
    Write-Host ""
    Write-Host ("REFUSING release: exe contains forbidden inject markers: {0}" -f ($hits -join ', ')) -ForegroundColor Red
    exit 2
}
Write-Host "Inject-script guard: OK" -ForegroundColor DarkGray

Write-Host ""
Write-Host ("Build complete: {0} ({1} MB)" -f $exe, $sizeMb) -ForegroundColor Green
