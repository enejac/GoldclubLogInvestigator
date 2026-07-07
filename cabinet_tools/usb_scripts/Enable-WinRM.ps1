#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Enable WinRM on this GoldClub cabinet for fast Invoke-Command from the lab PC.

.DESCRIPTION
    Lab cabinets use a Public network profile; plain Enable-PSRemoting fails with
    WSManFault 2150859113. -SkipNetworkProfileCheck installs the subnet-scoped
    firewall rule required for port 5985 on 10.0.0.x.

    After running on the cabinet, from your workstation:
      . .\LabAccess.ps1
      Initialize-LabWinRmTrustedHosts -Ip @('10.0.0.90')
      Invoke-Command -ComputerName 10.0.0.90 -Credential (Get-LabCredential) -Authentication Negotiate -ScriptBlock { hostname }
#>
param(
    [switch] $WhatIf,
    [switch] $VerifyOnly
)

$ErrorActionPreference = 'Stop'
$LogFile = 'C:\goldclub\var\log\enable_winrm.log'

function Write-Log([string] $Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    $line | Add-Content -Path $LogFile -Encoding ASCII
}

function Try-DisableUwf {
    $filterModule = 'C:\goldclub\bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
    if (-not (Test-Path $filterModule)) { return $false }
    try {
        Import-Module $filterModule -ErrorAction Stop
        $state = Show-UWFState
        Write-Log "UWF state: $state"
        if ($state -eq 'enabled') {
            if ($WhatIf) { Write-Log 'WOULD disable UWF for persistent WinRM config'; return $false }
            Disable-UWF | Out-Null
            Write-Log 'Disabled UWF so WinRM settings persist across reboot'
            return $true
        }
    } catch { Write-Log "WARN UWF: $($_.Exception.Message)" }
    return $false
}

function Test-WinRmReady {
    $ok = $true
    try {
        Test-WSMan -ComputerName localhost -ErrorAction Stop | Out-Null
        Write-Log 'Test-WSMan localhost: OK'
    } catch {
        Write-Log "Test-WSMan localhost: FAIL ($($_.Exception.Message))"
        $ok = $false
    }
    $svc = Get-Service WinRM -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Log ("WinRM service: {0}, start={1}" -f $svc.Status, $svc.StartType)
        if ($svc.Status -ne 'Running') { $ok = $false }
    } else {
        Write-Log 'WinRM service: NOT FOUND'
        $ok = $false
    }
    $portOpen = $false
    try {
        $portOpen = [bool](Get-NetTCPConnection -LocalPort 5985 -State Listen -ErrorAction SilentlyContinue)
    } catch {
        $portOpen = [bool](netstat -an | Select-String ':5985\s+LISTENING')
    }
    Write-Log ("Port 5985 listening: {0}" -f $portOpen)
    if (-not $portOpen) { $ok = $false }
    return $ok
}

Write-Log '=== Enable-WinRM start ==='
Write-Log ("Computer: {0}" -f $env:COMPUTERNAME)

if ($VerifyOnly) {
    if (Test-WinRmReady) { Write-Log 'VerifyOnly: WinRM is ready'; exit 0 }
    Write-Log 'VerifyOnly: WinRM is NOT ready'
    exit 1
}

$null = Try-DisableUwf

if ($WhatIf) {
    Write-Log 'WOULD run: Enable-PSRemoting -Force -SkipNetworkProfileCheck'
    Write-Log 'WOULD run: Set-Service WinRM -StartupType Automatic; Start-Service WinRM'
    exit 0
}

Write-Log 'Running Enable-PSRemoting -Force -SkipNetworkProfileCheck...'
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Write-Log 'Enable-PSRemoting completed'

Write-Log 'Setting WinRM service to Automatic and starting...'
Set-Service WinRM -StartupType Automatic
Start-Service WinRM

Start-Sleep -Seconds 2

if (Test-WinRmReady) {
    Write-Log '=== Enable-WinRM finished: READY ==='
    Write-Log 'From lab PC: Invoke-Command -ComputerName THIS_IP -Credential (GOLD-CLUB\test) -Authentication Negotiate'
    exit 0
}

Write-Log '=== Enable-WinRM finished: STILL NOT READY (check firewall / reboot) ==='
exit 1
