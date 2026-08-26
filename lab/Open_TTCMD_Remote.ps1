# Launch Total Commander on the cabinet console via WinRM (hidden orchestrator).
param(
    [string] $TargetIP = '10.0.0.90',
    [string] $ExePath = 'D:\totalcmd\TOTALCMD.EXE'
)

. "$PSScriptRoot\LabWinRm.ps1"

Write-Host "=== Remote Total Commander (WinRM) ===" -ForegroundColor Cyan
Ensure-LabWinRm -ComputerName $TargetIP | Out-Null

try {
    $out = Start-LabInteractiveRemote -ComputerName $TargetIP -FilePath $ExePath -AppWindowStyle Normal
    Write-Host "[+] $out" -ForegroundColor Green
} catch {
    Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
