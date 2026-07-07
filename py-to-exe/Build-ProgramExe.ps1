<#
.SYNOPSIS
    Turn program.py (or any .py file) into a standalone .exe on Windows.

.DESCRIPTION
    One script for a PC that may not have Python yet:
      1. Finds Python, or installs it with winget (optional)
      2. Creates a local virtual environment (does not touch system Python packages)
      3. Installs PyInstaller and builds a single-file .exe

    Output:  dist\<Name>.exe

.PARAMETER Program
    Python source file to build. Default: program.py in this folder.

.PARAMETER Name
    Base name of the .exe (no .exe suffix). Default: stem of Program (e.g. program).

.PARAMETER InstallPython
    If Python is missing, run: winget install Python.Python.3.12
    Requires winget (Windows 10/11 with App Installer). You may need to reopen PowerShell after install.

.PARAMETER NoOneFile
    Build a folder dist\<Name>\ with dependencies instead of one big .exe (faster startup, more files).

.EXAMPLE
    .\Build-ProgramExe.ps1

.EXAMPLE
    .\Build-ProgramExe.ps1 -Program .\my_tool.py -Name MyTool

.EXAMPLE
    .\Build-ProgramExe.ps1 -InstallPython
#>
[CmdletBinding()]
param(
    [string] $Program = '',
    [string] $Name = '',
    [switch] $InstallPython,
    [switch] $NoOneFile
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not $Program) { $Program = Join-Path $PSScriptRoot 'program.py' }

if (-not (Test-Path -LiteralPath $Program)) {
    throw "Program not found: $Program"
}
if (-not $Name) {
    $Name = [System.IO.Path]::GetFileNameWithoutExtension($Program)
}

function Test-PythonAvailable {
    param([string] $Command)
    if ($Command -eq 'py -3') {
        & py -3 --version 2>&1 | Out-Null
    }
    else {
        & $Command --version 2>&1 | Out-Null
    }
    return ($LASTEXITCODE -eq 0)
}

function Find-PythonCommand {
    foreach ($cmd in @('py -3', 'python', 'python3')) {
        if (Test-PythonAvailable -Command $cmd) { return $cmd }
    }
    return $null
}

function Invoke-Python {
    param([string[]] $PyArgs)
    if ($script:PythonCmd -eq 'py -3') {
        & py -3 @PyArgs
    }
    else {
        & $script:PythonCmd @PyArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed (exit $LASTEXITCODE): $($script:PythonCmd) $($PyArgs -join ' ')"
    }
}

Write-Host ''
Write-Host '=== Build Python program to .exe ===' -ForegroundColor Cyan
Write-Host "Source : $Program"
Write-Host "Output : dist\$Name.exe"
Write-Host ''

$script:PythonCmd = Find-PythonCommand
if (-not $script:PythonCmd) {
    if (-not $InstallPython) {
        Write-Host 'Python was not found on this PC.' -ForegroundColor Yellow
        Write-Host ''
        Write-Host 'Option A — install automatically (needs winget):' -ForegroundColor White
        Write-Host '  .\Build-ProgramExe.ps1 -InstallPython' -ForegroundColor Green
        Write-Host ''
        Write-Host 'Option B — install manually:' -ForegroundColor White
        Write-Host '  1. Open https://www.python.org/downloads/windows/' -ForegroundColor DarkGray
        Write-Host '  2. Download Python 3.12+ and run the installer' -ForegroundColor DarkGray
        Write-Host '  3. CHECK "Add python.exe to PATH" on the first screen' -ForegroundColor DarkGray
        Write-Host '  4. Close and reopen PowerShell, then run this script again' -ForegroundColor DarkGray
        exit 1
    }

    Write-Host '[*] Installing Python 3.12 via winget (one-time)...' -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw 'winget install failed. Install Python manually from python.org (see README.md).'
    }
    Write-Host '[+] Python installed. If the next step still fails, close PowerShell, open a new window, and run this script again.' -ForegroundColor Green
    $script:PythonCmd = Find-PythonCommand
    if (-not $script:PythonCmd) {
        throw 'Python still not found after install. Open a NEW PowerShell window and retry.'
    }
}

Write-Host "[+] Using Python: $script:PythonCmd" -ForegroundColor Green
Invoke-Python @('-c', 'import sys; print(sys.executable); print(sys.version)')

$venv = Join-Path $PSScriptRoot '.build-venv'
if (-not (Test-Path -LiteralPath $venv)) {
    Write-Host '[*] Creating virtual environment (.build-venv)...' -ForegroundColor DarkGray
    Invoke-Python @('-m', 'venv', $venv)
}

$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "venv python missing: $venvPython"
}

Write-Host '[*] Installing / upgrading PyInstaller in venv...' -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { throw 'pip install pyinstaller failed' }

$distDir = Join-Path $PSScriptRoot 'dist'
$buildDir = Join-Path $PSScriptRoot 'build'
$pyiArgs = @(
    '--noconfirm',
    '--clean',
    '--name', $Name,
    '--distpath', $distDir,
    '--workpath', $buildDir,
    '--specpath', $PSScriptRoot
)
if (-not $NoOneFile) {
    $pyiArgs += '--onefile'
}
$pyiArgs += $Program

Write-Host '[*] Running PyInstaller (may take 1–3 minutes)...' -ForegroundColor Cyan
& $venvPython -m PyInstaller @pyiArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit $LASTEXITCODE). See errors above."
}

if ($NoOneFile) {
    $outFolder = Join-Path $distDir $Name
    $outExe = Join-Path $outFolder "$Name.exe"
    if (-not (Test-Path -LiteralPath $outExe)) {
        throw "Expected exe not found: $outExe"
    }
    Write-Host ''
    Write-Host "[+] Done: $outExe" -ForegroundColor Green
    Write-Host "    (folder build — copy the whole dist\$Name folder to run on another PC)" -ForegroundColor DarkGray
}
else {
    $outExe = Join-Path $distDir "$Name.exe"
    if (-not (Test-Path -LiteralPath $outExe)) {
        throw "Expected exe not found: $outExe"
    }
    $sizeMb = [math]::Round((Get-Item -LiteralPath $outExe).Length / 1MB, 1)
    Write-Host ''
    Write-Host "[+] Done: $outExe  ($sizeMb MB)" -ForegroundColor Green
    Write-Host '    Double-click it, or run from PowerShell:' -ForegroundColor DarkGray
    Write-Host "    .\dist\$Name.exe" -ForegroundColor DarkGray
}

Write-Host ''
