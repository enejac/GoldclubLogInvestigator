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
    & "$Repo\Invoke-WakeSasBridge.ps1" -ComputerName $IP -SkipClearPendingAft
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
& "$Repo\Capture-CabinetSasState.ps1" -IP $IP -Label after

Write-Host "=== SUMMARY ===" -ForegroundColor Cyan
Write-Host "RebootConfirmed: $rebootConfirmed"
Write-Host "WakeSasBridgeRan: $wakeRan"
Write-Host "Bridge: COM11=$($bridge.COM11) CheckForMux=$($bridge.CheckForMux) 31150=$($bridge.Port31150)"

