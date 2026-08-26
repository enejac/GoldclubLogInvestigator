#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $UsbRoot = $PSScriptRoot,
    [int] $DelaySeconds = 0,
    [string] $DefaultEgmIp = '10.0.0.90',
    [string] $LabUser = 'GOLD-CLUB\test',
    [string] $LabPassword = 'test'
)

$ErrorActionPreference = 'Stop'

function Get-UncHost([string] $Path) {
    if ($Path -match '^\\\\([^\\]+)\\') { return $Matches[1] }
    return $null
}

function Test-IsLocalHostName([string] $Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    $n = $Name.Trim().TrimEnd('.')
    if ($n -eq '.' -or $n -eq 'localhost' -or $n -eq '127.0.0.1') { return $true }
    if ($n -eq $env:COMPUTERNAME) { return $true }
    try {
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -and $_.IPAddress -notlike '127.*' } |
            Select-Object -ExpandProperty IPAddress)
        if ($ips -contains $n) { return $true }
    } catch {}
    return $false
}

function Test-IsEgmLocal {
    if (Test-Path -LiteralPath 'C:\Goldclub') { return $true }
    if ($env:COMPUTERNAME -match '^(GRT|GST)\d') { return $true }
    return $false
}

function Restart-EgmLocal {
    param([int] $Seconds = 0)
    $msg = 'GoldClub USB _REBOOT'
    Write-Host ("[EGM] Rebooting this machine in {0}s..." -f $Seconds) -ForegroundColor Yellow
    & shutdown.exe /r /t $Seconds /f /c $msg
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        throw "shutdown.exe failed exit=$LASTEXITCODE"
    }
    Write-Host '[OK] reboot scheduled' -ForegroundColor Green
}

function Restart-EgmRemote {
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [int] $Seconds = 0
    )
    Write-Host ("[PC] Rebooting EGM {0} via WinRM (delay {1}s)..." -f $ComputerName, $Seconds) -ForegroundColor Cyan
    cmdkey /add:$ComputerName /user:$LabUser /pass:$LabPassword | Out-Null
    $cred = New-Object System.Management.Automation.PSCredential (
        $LabUser,
        (ConvertTo-SecureString $LabPassword -AsPlainText -Force)
    )
    try {
        $th = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction SilentlyContinue).Value
        if ($th -notmatch [regex]::Escape($ComputerName) -and $th -ne '*') {
            $new = if ([string]::IsNullOrWhiteSpace($th)) { $ComputerName } else { "$th,$ComputerName" }
            Set-Item WSMan:\localhost\Client\TrustedHosts -Value $new -Force -ErrorAction SilentlyContinue
        }
    } catch {}

    $result = Invoke-Command -ComputerName $ComputerName -Credential $cred -Authentication Negotiate -ScriptBlock {
        param([int] $Delay, [string] $Comment)
        $out = & shutdown.exe /r /t $Delay /f /c $Comment 2>&1
        [PSCustomObject]@{
            Host = $env:COMPUTERNAME
            Exit = [int]$LASTEXITCODE
            Out  = ($out | Out-String).Trim()
        }
    } -ArgumentList $Seconds, 'GoldClub USB _REBOOT (remote)'

    if ($result.Exit -ne 0) {
        throw ("Remote shutdown failed on {0}: exit={1} {2}" -f $result.Host, $result.Exit, $result.Out)
    }
    Write-Host ("[OK] reboot scheduled on {0}" -f $result.Host) -ForegroundColor Green
}

# --- main ---
$root = if ($UsbRoot) { $UsbRoot.TrimEnd('\') } else { $PSScriptRoot }
$uncHost = Get-UncHost $root
if (-not $uncHost) { $uncHost = Get-UncHost $PSScriptRoot }
if (-not $uncHost) { $uncHost = Get-UncHost $MyInvocation.MyCommand.Path }

if ($uncHost -and (Test-IsLocalHostName $uncHost)) {
    Write-Host ("[EGM] UNC {0} is this machine - local reboot" -f $uncHost) -ForegroundColor Cyan
    Restart-EgmLocal -Seconds $DelaySeconds
    exit 0
}

if ($uncHost) {
    Restart-EgmRemote -ComputerName $uncHost -Seconds $DelaySeconds
    exit 0
}

if (Test-IsEgmLocal) {
    Restart-EgmLocal -Seconds $DelaySeconds
    exit 0
}

Write-Host ("[WARN] No UNC host detected - rebooting default EGM {0}" -f $DefaultEgmIp) -ForegroundColor Yellow
Restart-EgmRemote -ComputerName $DefaultEgmIp -Seconds $DelaySeconds