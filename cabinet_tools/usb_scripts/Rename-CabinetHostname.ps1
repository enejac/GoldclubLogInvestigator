#Requires -RunAsAdministrator
param([string] $TargetMachineName = 'GST20664')
$ErrorActionPreference = 'Stop'
$LogFile = 'C:\goldclub\var\log\rename_hostname.log'
function Write-Log([string] $m) {
    $l = "[$(Get-Date -Format o)] $m"
    Write-Host $l
    $dir = Split-Path $LogFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $l | Add-Content $LogFile -Encoding ASCII
}
Import-Module 'C:\goldclub\bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
Write-Log "=== Rename hostname -> $TargetMachineName ==="
if ($env:COMPUTERNAME -eq $TargetMachineName) { Write-Log 'Already correct'; exit 0 }
$state = Show-UWFState
Write-Log "UWF current session: $state"
if ($state -eq 'enabled') {
    Disable-UWF | Out-String | ForEach-Object { Write-Log $_.Trim() }
    Write-Log 'Step 1/2: UWF disable scheduled. Rebooting in 15s (CheckUWF must be bypassed).'
    shutdown /r /t 15 /f /c "Hostname rename step 1: disable UWF"
    exit 0
}
Write-Log 'Step 2/2: UWF disabled session - GoldClub pattern Enable-UWF then Rename-Restart'
Enable-UWF | Out-String | ForEach-Object { Write-Log $_.Trim() }
Write-Log "Rename-Computer $($env:COMPUTERNAME) -> $TargetMachineName"
Rename-Computer -NewName $TargetMachineName -Force -Restart
