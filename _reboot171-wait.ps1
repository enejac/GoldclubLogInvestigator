$IP = '10.0.0.171'
$deadline = (Get-Date).AddMinutes(30)
while ((Get-Date) -lt $deadline) {
  $ping = Test-Connection -ComputerName $IP -Count 1 -Quiet -ErrorAction SilentlyContinue
  $smb = Test-Path "\\$IP\c$\Goldclub" -ErrorAction SilentlyContinue
  Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ping=$ping smb=$smb"
  if ($smb) { exit 0 }
  Start-Sleep -Seconds 30
}
exit 1
