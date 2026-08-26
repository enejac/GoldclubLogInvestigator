# One-shot: register GoldClub-ElevateOnce on GRT330106 when onstart did not (or EWF wiped it).
# Run once from GoldClub Admin Shell or Total Commander on the cabinet (elevated).
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$elevDir = 'D:\usb_scripts\roulette\_elevate'
if (-not (Test-Path -LiteralPath $elevDir)) {
    New-Item -ItemType Directory -Path $elevDir -Force | Out-Null
}
$runner = Join-Path $elevDir 'GoldClubElevate-run.cmd'
if (-not (Test-Path -LiteralPath $runner)) {
    [System.IO.File]::WriteAllText(
        $runner,
        "@echo off`r`nrem placeholder until GoldClubElevate.ps1 runs`r`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}
$taskName = 'GoldClub-ElevateOnce'
# cmd.exe so a missing task does not throw under $ErrorActionPreference Stop
cmd.exe /c "schtasks /Delete /TN `"$taskName`" /F >nul 2>nul"
$createOut = cmd.exe /c "schtasks /Create /TN `"$taskName`" /SC ONCE /ST 00:00 /SD 01/01/2099 /RL HIGHEST /RU SYSTEM /F /TR `"$runner`""
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: schtasks /Create failed: $createOut" -ForegroundColor Red
    exit 1
}
Write-Host "Registered $taskName -> $runner" -ForegroundColor Green
Write-Host 'Remote Config Scanner restore can now Kill-All over WinRM.' -ForegroundColor Cyan
exit 0
