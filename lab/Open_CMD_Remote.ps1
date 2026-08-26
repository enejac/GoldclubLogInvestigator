# Launch elevated CMD on the cabinet console via WinRM (hidden orchestrator).
param([string] $TargetIP = '10.0.0.90')

. "$PSScriptRoot\LabWinRm.ps1"

Write-Host "=== Remote Session 1 CMD (WinRM) ===" -ForegroundColor Cyan
if (-not (Test-Connection -ComputerName $TargetIP -Count 1 -Quiet)) {
    Write-Host "[-] Cabinet offline" -ForegroundColor Red
    exit 1
}
try {
    Ensure-LabWinRm -ComputerName $TargetIP | Out-Null
} catch {
    Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

try {
    $out = Start-LabInteractiveRemote -ComputerName $TargetIP -FilePath 'cmd.exe' -AppWindowStyle Normal
    Write-Host "[+] $out" -ForegroundColor Green
} catch {
    Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
