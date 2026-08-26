#requires -Version 5.1
<#
.SYNOPSIS
    Wake the CommCtrlSAS <-> Aurum SAS bridge on a lab cabinet.
.EXAMPLE
    .\Invoke-WakeSasBridge.ps1 -IP 10.0.0.90
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [switch] $ClearPendingAft,
    [switch] $SkipClearPendingAft,
    [switch] $WaitForWat,
    [switch] $SkipWaitForWat,
    [int]    $WaitSec = 90,
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [pscredential] $Credential
)

$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabAccess.ps1')
$labRemote = Join-Path $PSScriptRoot 'LabRemoteTransport.ps1'
if (Test-Path -LiteralPath $labRemote) {
    . $labRemote
}
if (-not $Credential) { $Credential = Get-LabCredential }

$doClearPending = $ClearPendingAft -or (-not $SkipClearPendingAft)
$doWaitForWat = $WaitForWat -or (-not $SkipWaitForWat)

$gatewaySvc = 'GoldClub Serial Communication Gateway SAS'
$aurumSvc   = 'GoldClub.Aurum.Services'

Write-Host "=== Wake SAS bridge on $ComputerName ===" -ForegroundColor Cyan

$wakeScriptBlock = {
    param($GatewaySvc, $AurumSvc, $DoClearPending)
    $out = New-Object System.Collections.Generic.List[string]

    Stop-Process -Name WdPollInject, WdInject, CommCtrlSAS -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Restart-Service $GatewaySvc -Force
    Start-Sleep -Seconds 12
    $out.Add("Restarted: $GatewaySvc")

    if ($DoClearPending) {
        $stateDir = 'C:\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1'
        if (Test-Path $stateDir) {
            $bak = Join-Path $stateDir ('_pending_bak_' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            New-Item -ItemType Directory -Path $bak -Force | Out-Null
            Get-ChildItem $stateDir -Filter '*aftPendingTransaction*' -ErrorAction SilentlyContinue | ForEach-Object {
                Move-Item $_.FullName -Destination $bak -Force
                $out.Add("Moved pending: $($_.Name)")
            }
        }
    }

    Restart-Service $AurumSvc -Force
    Start-Sleep -Seconds 25
    $out.Add("Restarted: $AurumSvc")

    $tcp = Get-NetTCPConnection -LocalPort 31150 -ErrorAction SilentlyContinue |
        Select-Object State, RemotePort
    [pscustomobject]@{ Log = $out; Tcp31150 = $tcp }
}

$result = $null
$transport = 'WinRM'
$psExecTimeout = if ($ComputerName -eq '10.0.0.171') { 180 } else { 120 }

$canWinRm = $false
if (Get-Command Test-LabWinRmReachable -ErrorAction SilentlyContinue) {
    $canWinRm = (Test-LabWinRmReachable -Computer $ComputerName) -and
        (Test-LabTrustedHostConfigured -Computer $ComputerName)
}

if ($canWinRm) {
    try {
        $result = Invoke-Command -ComputerName $ComputerName -Credential $Credential -ScriptBlock $wakeScriptBlock `
            -ArgumentList $gatewaySvc, $aurumSvc, [bool]$doClearPending -ErrorAction Stop
    }
    catch {
        Write-Host "[!] WinRM wake failed: $($_.Exception.Message)" -ForegroundColor Yellow
        $result = $null
    }
}

if (-not $result) {
    if (-not ($PsExecPath -and (Test-Path -LiteralPath $PsExecPath))) {
        throw "Wake failed: WinRM unavailable and PsExec not found at $PsExecPath"
    }
    $transport = 'PsExec'
    Write-Host "[*] Retrying wake via PsExec (-s, timeout ${psExecTimeout}s)..." -ForegroundColor Cyan
    $gwEsc = $gatewaySvc -replace "'", "''"
    $auEsc = $aurumSvc -replace "'", "''"
    $clearFlag = if ($doClearPending) { '$true' } else { '$false' }
    $psBody = @"
`$GatewaySvc = '$gwEsc'
`$AurumSvc = '$auEsc'
`$DoClearPending = $clearFlag
`$out = New-Object System.Collections.Generic.List[string]
Stop-Process -Name WdPollInject, WdInject, CommCtrlSAS -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Restart-Service `$GatewaySvc -Force
Start-Sleep -Seconds 12
`$out.Add("Restarted: `$GatewaySvc")
if (`$DoClearPending) {
    `$stateDir = 'C:\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1'
    if (Test-Path `$stateDir) {
        `$bak = Join-Path `$stateDir ('_pending_bak_' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Path `$bak -Force | Out-Null
        Get-ChildItem `$stateDir -Filter '*aftPendingTransaction*' -ErrorAction SilentlyContinue | ForEach-Object {
            Move-Item `$_.FullName -Destination `$bak -Force
            `$out.Add("Moved pending: `$(`$_.Name)")
        }
    }
}
Restart-Service `$AurumSvc -Force
Start-Sleep -Seconds 25
`$out.Add("Restarted: `$AurumSvc")
`$tcp = Get-NetTCPConnection -LocalPort 31150 -ErrorAction SilentlyContinue | Select-Object State, RemotePort
foreach (`$line in `$out) { Write-Output ('LOG=' + `$line) }
foreach (`$t in `$tcp) { Write-Output ('TCP=' + `$t.State + ':' + `$t.RemotePort) }
"@
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($psBody))
    $psArgs = @("\\$ComputerName", '-accepteula') + @(Get-LabPsExecArgs) + @('-s', '-n', ([string]$psExecTimeout),
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $enc)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = (& $PsExecPath @psArgs 2>&1 | Out-String)
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    $logLines = New-Object System.Collections.Generic.List[string]
    $tcpRows = @()
    foreach ($ln in ($raw -split "`r?`n")) {
        $ln = $ln.Trim()
        if ($ln -match '^LOG=(.+)$') { $logLines.Add($Matches[1].Trim()) | Out-Null }
        elseif ($ln -match '^TCP=(.+)$') { $tcpRows += $Matches[1].Trim() }
    }
    if ($logLines.Count -eq 0) {
        throw "PsExec wake produced no output. Tail: $(($raw -split "`r?`n" | Select-Object -Last 4) -join '; ')"
    }
    $result = [pscustomobject]@{ Log = $logLines; Tcp31150 = $tcpRows }
}

Write-Host "[+] Wake transport: $transport" -ForegroundColor DarkGray
foreach ($line in $result.Log) {
    Write-Host "[+] $line" -ForegroundColor Green
}
if ($result.Tcp31150) {
    $result.Tcp31150 | Format-Table -AutoSize
}

if ($doWaitForWat) {
    $logPath = "\\$ComputerName\c`$\Goldclub\var\log\GoldClub.Aurum.Services\$((Get-Date).ToString('yyyy-MM-dd')).log"
    $deadline = (Get-Date).AddSeconds([math]::Max(10, $WaitSec))
    Write-Host "[*] Waiting up to ${WaitSec}s for WAT2AFT UP..." -ForegroundColor Cyan
    do {
        if (Test-Path -LiteralPath $logPath) {
            $tail = Get-Content -LiteralPath $logPath -Tail 30 -ErrorAction SilentlyContinue
            if ($tail -match 'WAT2AFT UP') {
                Write-Host '[+] WAT2AFT UP confirmed.' -ForegroundColor Green
                ($tail | Select-String 'WAT2AFT UP' | Select-Object -Last 1).Line
                break
            }
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
}

Write-Host ''
Write-Host 'Next: .\Send-TestAft1000.ps1 -Send' -ForegroundColor DarkGray
