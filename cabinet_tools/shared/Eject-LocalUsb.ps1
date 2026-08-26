#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
Write-Host "Stopping apps that may lock USB (G:)..."
Stop-Process -Name LogInvestigator -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "G:\*" } | ForEach-Object {
  Write-Host "  Stopping $($_.Name) ($($_.ProcessId))"
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "Removing drive letter G:..."
cmd /c "mountvol G: /D"
Write-Host "Taking USB disk 1 offline..."
"select disk 1","offline disk" | diskpart
Start-Sleep -Seconds 2
$d = Get-Disk -Number 1 -ErrorAction SilentlyContinue
if ($d -and $d.IsOffline) { Write-Host "SUCCESS: Disk 1 is offline. You can unplug the USB now." -ForegroundColor Green }
elseif (-not $d) { Write-Host "SUCCESS: Disk removed." -ForegroundColor Green }
else {
  Write-Host "Trying Shell eject..."
  $shell = New-Object -ComObject Shell.Application
  $shell.Namespace(17).ParseName("G:").InvokeVerb("Eject")
  Write-Host "If still blocked, reboot and eject before opening any apps." -ForegroundColor Yellow
}
Read-Host "Press Enter to close"
