<#
.SYNOPSIS
    Fetch official llama.cpp Windows CPU Release DLLs for AI Helper packaging.

.DESCRIPTION
    PyPI llama-cpp-python win_amd64 wheels for some Python versions ship Debug CRT
    DLLs (MSVCP140D / ucrtbased). Cabinets without Visual Studio cannot load them.
    This script downloads ggml-org/llama.cpp CPU-x64 Release binaries into
    vendor\llama_cpp_lib\ for LogInvestigator.spec / build_exe.ps1 -BundleLocalAi.
#>
[CmdletBinding()]
param(
    [string]$Tag = "b10042",
    [string]$DestDir = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DestDir) {
    $DestDir = Join-Path $repoRoot "vendor\llama_cpp_lib"
}

$stamp = Join-Path $DestDir "SOURCE.txt"
$need = @("llama.dll", "ggml.dll", "ggml-base.dll", "libomp140.x86_64.dll")
if ((Test-Path $stamp) -and (($need | ForEach-Object { Test-Path (Join-Path $DestDir $_) }) -notcontains $false)) {
    $existing = (Get-Content $stamp -Raw -ErrorAction SilentlyContinue).Trim()
    if ($existing -like "*$Tag*") {
        Write-Host "Release llama DLLs already present ($existing)" -ForegroundColor Green
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
$cache = Join-Path $repoRoot "vendor\llama_cpp_release"
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$zipName = "llama-$Tag-bin-win-cpu-x64.zip"
$zip = Join-Path $cache $zipName
$url = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag/$zipName"

if (-not (Test-Path $zip)) {
    Write-Host "Downloading $url ..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
}

$extract = Join-Path $cache "extract-$Tag"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $extract -Force

foreach ($name in @("llama.dll", "ggml.dll", "ggml-base.dll", "mtmd.dll", "libomp140.x86_64.dll")) {
    $src = Join-Path $extract $name
    if (-not (Test-Path $src)) { throw "Missing $name in $zipName" }
    Copy-Item $src (Join-Path $DestDir $name) -Force
}
Get-ChildItem (Join-Path $extract "ggml-cpu-*.dll") | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $DestDir $_.Name) -Force
}
Copy-Item (Join-Path $extract "ggml-cpu-x64.dll") (Join-Path $DestDir "ggml-cpu.dll") -Force
"$Tag cpu-x64 Release CRT" | Set-Content (Join-Path $DestDir "SOURCE.txt") -Encoding ascii
Write-Host "Wrote Release DLLs to $DestDir" -ForegroundColor Green
