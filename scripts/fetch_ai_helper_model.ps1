<#
.SYNOPSIS
    Download Qwen3-4B Q4_K_M GGUF for the offline AI Helper.

.DESCRIPTION
    Saves to <Repo>\models\Qwen3-4B-Q4_K_M.gguf (gitignored).
    Uses Hugging Face resolve URL for Qwen/Qwen3-4B-GGUF.

.PARAMETER DestDir
    Directory for the GGUF (default: repo models\).

.EXAMPLE
    ./scripts/fetch_ai_helper_model.ps1
#>
[CmdletBinding()]
param(
    [string]$DestDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $DestDir) {
    $DestDir = Join-Path $root "models"
}
if (-not (Test-Path -LiteralPath $DestDir)) {
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
}

$outFile = Join-Path $DestDir "Qwen3-4B-Q4_K_M.gguf"
$url = "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"

if (Test-Path -LiteralPath $outFile) {
    $sizeMb = [math]::Round((Get-Item $outFile).Length / 1MB, 1)
    Write-Host "Already present: $outFile ($sizeMb MB)" -ForegroundColor Green
    exit 0
}

Write-Host "Downloading Qwen3-4B Q4_K_M (~2.5 GB)…" -ForegroundColor Cyan
Write-Host "  $url"
Write-Host "  -> $outFile"

$tmp = "$outFile.tmp"
try {
    # Prefer curl.exe (Windows 10+) for progress; fall back to Invoke-WebRequest.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -L --fail --retry 3 -o $tmp $url
        if ($LASTEXITCODE -ne 0) { throw "curl failed with exit $LASTEXITCODE" }
    } else {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    }
    Move-Item -LiteralPath $tmp -Destination $outFile -Force
} catch {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    throw
}

$sizeMb = [math]::Round((Get-Item $outFile).Length / 1MB, 1)
Write-Host "Done: $outFile ($sizeMb MB)" -ForegroundColor Green
Write-Host "Also install: python -m pip install -r requirements-ai-helper.txt"
Write-Host "Deploy copies models\ when present: .\deploy_usb.ps1 -Dest H:\ConfigScanner"
