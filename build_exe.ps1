<#
.SYNOPSIS
    Build the Log Investigator desktop UI into a single .exe.

.DESCRIPTION
    Wraps PyInstaller using LogInvestigator.spec. Output: dist\LogInvestigator.exe
    and a copy at repo root LogInvestigator.exe

    Profiles:
      (default)   lite  - smallest binary; AI + OpenCV excluded (graceful fallback).
      -BundleAi   ai    - lite + Google Gemini AI summary SDKs.
      -BundleLocalAi local_ai - lite + llama-cpp-python for offline AI Helper.
      -Full       full  - everything: AI SDKs + OpenCV/numpy (QR scan) + pywin32
                          (+ local AI if installed).

.PARAMETER Full
    Build the full-stack binary with every optional feature bundled.

.PARAMETER BundleAi
    Bundle only the optional Google Gemini SDKs (ignored if -Full is set).

.PARAMETER BundleLocalAi
    Bundle llama-cpp-python for the offline AI Helper (GGUF still external in models\).

.PARAMETER InstallDeps
    pip-install the runtime requirements before building (recommended for -Full).

.PARAMETER SasVerify
    Build the separate SAS Verify Meters exe instead (see build_sas_verify_exe.ps1).

.EXAMPLE
    ./build_exe.ps1                       # lite
    ./build_exe.ps1 -BundleAi             # + cloud AI SDKs
    ./build_exe.ps1 -BundleLocalAi -InstallDeps  # + llama-cpp
    ./build_exe.ps1 -Full                 # full stack features
    ./build_exe.ps1 -Full -InstallDeps    # full + install deps first
    ./build_exe.ps1 -SasVerify            # standalone SasVerifyMeters.exe
#>
[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$BundleAi,
    [switch]$BundleLocalAi,
    [switch]$InstallDeps,
    [switch]$SasVerify
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvScripts = Join-Path $PSScriptRoot ".venv\Scripts"
if (Test-Path -LiteralPath (Join-Path $venvScripts "python.exe")) {
    $env:Path = "$venvScripts;$env:Path"
    Write-Host "Using repo venv: $venvScripts\python.exe" -ForegroundColor DarkGray
}

function Test-PythonModule {
    param([Parameter(Mandatory)][string]$Module)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -c "import $Module" 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    return $ok
}

if ($SasVerify) {
    & (Join-Path $PSScriptRoot "build_sas_verify_exe.ps1") -InstallDeps:$InstallDeps
    exit $LASTEXITCODE
}

if ($Full)             { $profile = "full" }
elseif ($BundleLocalAi -and $BundleAi) { $profile = "full" }  # both optional stacks
elseif ($BundleLocalAi) { $profile = "local_ai" }
elseif ($BundleAi)      { $profile = "ai" }
else                   { $profile = "lite" }
$env:LI_BUILD = $profile
$env:BUNDLE_AI = $null  # avoid back-compat ambiguity; LI_BUILD wins

Write-Host "Build profile: $profile" -ForegroundColor Cyan

function Test-InternetReachable {
    param([int]$TimeoutMs = 2500)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('1.1.1.1', 443, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($ok) { try { $client.EndConnect($iar) } catch { $ok = $false } }
        $client.Close()
        return [bool]$ok
    } catch {
        return $false
    }
}

function Invoke-OfflineSafePipInstall {
    param(
        [Parameter(Mandatory)][string[]] $PipArguments
    )
    if (-not (Test-InternetReachable)) {
        Write-Host "WARN: No internet - skipping pip ($($PipArguments -join ' ')). Using packages already on this PC." -ForegroundColor Yellow
        return $false
    }
    python @PipArguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARN: pip failed (exit $LASTEXITCODE). Build continues with installed packages only." -ForegroundColor Yellow
        return $false
    }
    return $true
}

# Ensure PyInstaller is available.
if (-not (Test-PythonModule "PyInstaller")) {
    Write-Host "PyInstaller not found - attempting install..." -ForegroundColor Yellow
    if (-not (Invoke-OfflineSafePipInstall -PipArguments @('-m', 'pip', 'install', 'pyinstaller'))) {
        Write-Host "ERROR: PyInstaller is required and could not be installed offline." -ForegroundColor Red
        Write-Host "Install once while online: python -m pip install pyinstaller" -ForegroundColor Yellow
        exit 1
    }
}

if ($InstallDeps) {
    if (Test-InternetReachable) {
        Write-Host "Installing runtime requirements..." -ForegroundColor Yellow
        python -m pip install -r requirements.txt
        if ($profile -in @("local_ai", "full")) {
            Write-Host "Installing local AI Helper requirements..." -ForegroundColor Yellow
            python -m pip install -r requirements-ai-helper.txt
        }
    } else {
        Write-Host "WARN: -InstallDeps skipped - no internet. Using packages already installed on this PC." -ForegroundColor Yellow
    }
}

# Warn early if a full/local_ai build is missing optional deps.
if ($profile -eq "full") {
    foreach ($mod in @("cv2", "numpy", "google.genai")) {
        if (-not (Test-PythonModule $mod)) {
            Write-Host "WARN: '$mod' not importable - that feature will be omitted. Run with -InstallDeps while online." -ForegroundColor Yellow
        }
    }
}
if ($profile -in @("local_ai", "full")) {
    if (-not (Test-PythonModule "llama_cpp")) {
        Write-Host "WARN: llama_cpp not importable - AI Helper stays search-only. Run with -BundleLocalAi -InstallDeps." -ForegroundColor Yellow
    }
    # PyPI win wheels may ship Debug CRT (*D.dll); fetch official Release DLLs for packaging.
    $fetchDlls = Join-Path $PSScriptRoot "scripts\fetch_llama_cpp_release_dlls.ps1"
    if (Test-Path $fetchDlls) {
        if (Test-InternetReachable) {
            Write-Host "Ensuring Release llama.cpp DLLs in vendor\llama_cpp_lib ..." -ForegroundColor Yellow
            & $fetchDlls
            if ($LASTEXITCODE -ne 0) {
                Write-Host "WARN: could not fetch Release llama DLLs - using cached vendor\llama_cpp_lib if present." -ForegroundColor Yellow
            }
        } else {
            $vendorDll = Join-Path $PSScriptRoot "vendor\llama_cpp_lib\llama.dll"
            if (Test-Path $vendorDll) {
                Write-Host "Offline: using cached llama DLLs in vendor\llama_cpp_lib (no download)." -ForegroundColor DarkGray
            } else {
                Write-Host "WARN: offline and vendor\llama_cpp_lib missing - local AI may be search-only in this build." -ForegroundColor Yellow
            }
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
$rootExe = Join-Path $PSScriptRoot "LogInvestigator.exe"
Copy-Item -LiteralPath $exe -Destination $rootExe -Force

# Guard: LogInvestigator must not ship Dallas splice / WinDivert AFT inject scripts.
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
Write-Host "Inject-script guard: OK (no Dallas/WinDivert AFT markers in exe)" -ForegroundColor DarkGray

Write-Host ""
Write-Host ("Build complete ({0}): {1} ({2} MB)" -f $profile, $exe, $sizeMb) -ForegroundColor Green
Write-Host ("Root copy: {0} ({1} MB)" -f $rootExe, $sizeMb) -ForegroundColor Green
