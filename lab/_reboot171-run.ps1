#requires -Version 5.1
$ErrorActionPreference = 'Continue'
$Repo = 'c:\Users\Ezbogar\GoldclubLogInvestigator'
$IP = '10.0.0.171'
. "$Repo\LabAccess.ps1"
Initialize-LabSmbCredential -IP $IP -ErrorAction SilentlyContinue | Out-Null
$psAuth = Get-LabPsExecArgs
$PsExecPath = 'C:\Tools\PSTools\PsExec.exe'
if (-not (Test-Path $PsExecPath)) { $PsExecPath = 'C:\Sysinternals\PsExec.exe' }

function Test-SmbUp {
    param([string]$TargetIp)
    return Test-Path "\\$TargetIp\c$\Goldclub" -ErrorAction SilentlyContinue
}

function Get-RemoteLastBoot {
    $out = & $PsExecPath "\\$IP" -accepteula @psAuth -s -n 180 powershell.exe -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')" 2>&1
    $line = ($out | Where-Object { $_ -match '^\d{4}-\d{2}-\d{2}T' } | Select-Object -Last 1)
    if (-not $line) { $line = ($out | Where-Object { $_ -match '\d{4}-\d{2}-\d{2}' } | Select-Object -Last 1) }
    return [string]$line
}

Write-Host "=== STEP 1: PRE-REBOOT LastBootUpTime ===" -ForegroundColor Cyan
$preBoot = Get-RemoteLastBoot
Write-Host "PRE: $preBoot"

Write-Host "=== STEP 2: FORCE REBOOT shutdown /r /t 0 /f ===" -ForegroundColor Cyan
$rebootOut = & $PsExecPath "\\$IP" -accepteula @psAuth -s -n 60 cmd.exe /c "shutdown.exe /r /t 0 /f" 2>&1
Write-Host ($rebootOut | Out-String)

Write-Host "=== STEP 3: POLL SMB (max 20 min) ===" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(20)
$sawDown = $false
$upAt = $null
while ((Get-Date) -lt $deadline) {
    $up = Test-SmbUp -TargetIp $IP
    $ts = Get-Date -Format 'HH:mm:ss'
    if (-not $up) { $sawDown = $true; Write-Host "$ts SMB DOWN" }
    elseif ($sawDown) { $upAt = Get-Date; Write-Host "$ts SMB UP (after down)"; break }
    else { Write-Host "$ts still up (waiting for down...)" }
    Start-Sleep -Seconds 10
}
if (-not $sawDown) { Write-Host "WARNING: never saw SMB down" -ForegroundColor Yellow }
if (-not $upAt) {
    Write-Host "Waiting for SMB to return..."
    while ((Get-Date) -lt $deadline) {
        if (Test-SmbUp -TargetIp $IP) { $upAt = Get-Date; Write-Host "$(Get-Date -Format HH:mm:ss) SMB UP"; break }
        Start-Sleep -Seconds 15
    }
}

if (-not $upAt) { Write-Host "FATAL: cabinet did not return within 20 min"; exit 2 }

Write-Host "=== Settle 3 minutes ===" -ForegroundColor Cyan
Start-Sleep -Seconds 180

Write-Host "=== STEP 4: POST CHECKS ===" -ForegroundColor Cyan
$postBoot = Get-RemoteLastBoot
Write-Host "POST: $postBoot"
$rebootConfirmed = $false
try {
    if ($preBoot -and $postBoot) {
        $preDt = [DateTimeOffset]::Parse($preBoot.Trim())
        $postDt = [DateTimeOffset]::Parse($postBoot.Trim())
        $rebootConfirmed = $postDt -gt $preDt
    }
} catch { Write-Host "Parse error: $_" }

Write-Host "REBOOT CONFIRMED: $rebootConfirmed (pre=$preBoot post=$postBoot)" -ForegroundColor $(if ($rebootConfirmed) {'Green'} else {'Red'})

# CommCtrlSAS log tail
$logRoot = "\\$IP\c$\Goldclub\var\log\CommCtrlSAS"
$today = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $logRoot "$today.log"
$bridge = @{ COM11 = $false; CheckForMux = $false; Port31150 = $false; Samples = @() }
if (Test-Path $logFile) {
    $tail = Get-Content $logFile -Tail 400 -ErrorAction SilentlyContinue
    $bridge.COM11 = [bool]($tail | Select-String -Pattern 'COM11' -SimpleMatch)
    $bridge.CheckForMux = [bool]($tail | Select-String -Pattern 'CheckForMux')
    $bridge.Port31150 = [bool]($tail | Select-String -Pattern '31150')
    $bridge.Samples = @($tail | Select-String -Pattern 'COM11|CheckForMux|31150|31100' | Select-Object -Last 15 | ForEach-Object { $_.Line })
} else {
    Write-Host "Log not found: $logFile"
    $alt = Get-ChildItem $logRoot -Filter '*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($alt) {
        $logFile = $alt.FullName
        $tail = Get-Content $logFile -Tail 400
        $bridge.COM11 = [bool]($tail | Select-String -Pattern 'COM11' -SimpleMatch)
        $bridge.CheckForMux = [bool]($tail | Select-String -Pattern 'CheckForMux')
        $bridge.Port31150 = [bool]($tail | Select-String -Pattern '31150')
        $bridge.Samples = @($tail | Select-String -Pattern 'COM11|CheckForMux|31150|31100' | Select-Object -Last 15 | ForEach-Object { $_.Line })
    }
}

Write-Host "=== CommCtrlSAS bridge flags ==="
Write-Host "COM11: $($bridge.COM11)  CheckForMux: $($bridge.CheckForMux)  31150: $($bridge.Port31150)"
$bridge.Samples | ForEach-Object { Write-Host "  $_" }

$wakeRan = $false
if ($bridge.COM11 -and -not $bridge.Port31150) {
    Write-Host "=== Running Invoke-WakeSasBridge (partial bridge) ===" -ForegroundColor Cyan
    & "$Repo\lab\Invoke-WakeSasBridge.ps1" -ComputerName $IP -SkipClearPendingAft
    $wakeRan = $true
    Start-Sleep -Seconds 30
    if (Test-Path $logFile) {
        $tail2 = Get-Content $logFile -Tail 200
        $bridge.CheckForMux = [bool]($tail2 | Select-String -Pattern 'CheckForMux')
        $bridge.Port31150 = [bool]($tail2 | Select-String -Pattern '31150')
        Write-Host "After wake - CheckForMux: $($bridge.CheckForMux) 31150: $($bridge.Port31150)"
    }
}

Write-Host "=== Capture-CabinetSasState -Label after ===" -ForegroundColor Cyan
& "$Repo\lab\Capture-CabinetSasState.ps1" -IP $IP -Label after

Write-Host "=== SUMMARY ===" -ForegroundColor Cyan
Write-Host "RebootConfirmed: $rebootConfirmed"
Write-Host "WakeSasBridgeRan: $wakeRan"
Write-Host "Bridge: COM11=$($bridge.COM11) CheckForMux=$($bridge.CheckForMux) 31150=$($bridge.Port31150)"
