#Requires -RunAsAdministrator
# Relaunch roulette after 10.1 binaries were already copied. Do not touch licences.
$ErrorActionPreference = 'Continue'
$log = 'D:\ConfigScanner\relaunch-10.1.log'
function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try {
        [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    } catch {}
}
L ("relaunch start whoami={0}" -f (whoami))
$kill = 'D:\ConfigScanner\scripts\roulette\Kill-All.ps1'
$run = 'D:\ConfigScanner\scripts\roulette\Run-FullStack.ps1'
if (-not (Test-Path -LiteralPath $kill)) { L "missing $kill"; exit 2 }
if (-not (Test-Path -LiteralPath $run)) { L "missing $run"; exit 3 }
L 'Kill-All'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $kill -AlreadyElevated
L ("Kill-All exit={0}" -f $LASTEXITCODE)
Start-Sleep -Seconds 2
L 'Run-FullStack'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $run -AlreadyElevated
L ("Run-FullStack exit={0}" -f $LASTEXITCODE)
L 'relaunch done (licences not touched)'
exit 0
