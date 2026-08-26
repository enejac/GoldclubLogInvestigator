<#
.SYNOPSIS
    Read-only AFT/WAT audit parser for a GoldClub cabinet.

    Reads the AFT transaction-state XML files that GoldClub.Aurum.Services writes
    under the GCMessenger\SASControler1 state tree, de-duplicates them, decodes the
    SAS-encoded fields (asset number, BCD completion time, transaction-id chars),
    correlates them against the authoritative SlotLog "Cashless In" events, runs a
    set of robustness checks, and emits CSV/JSON/Markdown reports.

.DESCRIPTION
    IMPORTANT - what this script talks to:
      * It ONLY reads files. It never writes to the cabinet and never sends SAS.
      * It does NOT communicate with CommCtrl.exe or CommCtrlSAS.exe. Those are the
        serial<->TCP SAS bridge processes. The AFT credit result is persisted by
        GoldClub.Aurum.Services into the state XML this script parses. If you need
        live SAS wire visibility, that is a separate passive capture on the
        CommCtrlSAS loopback ports - not this report.

    Data source (default cabinet 10.0.0.90):
      \\<host>\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1
        GCC_*_aftCurrentSettings_v*.xml        - current AFT settings (registration)
        GCC_*_aftMostRecentTransaction_v*.xml  - most-recent transaction mirror
        History\GCC_*_aftTransactionHistory_i<idx>_v<ver>.xml - history slots

    SAS field decoding:
      assetNumber                  base64 -> 4 little-endian bytes -> int
      transactionCompletedDateTime base64 -> 7 BCD bytes -> yyyyMMddHHmmss
      transactionId/char[]         decimal byte values -> ASCII text

.PARAMETER ComputerName
    Cabinet host/IP. Default 10.0.0.90.

.PARAMETER StateRoot
    Override the SASControler1 state folder (UNC or local). Derived from
    ComputerName when not supplied.

.PARAMETER OutputDir
    Folder for the generated reports. Default: <script dir>\aft\report.

.PARAMETER SlotLogPath
    SlotLog file used for the cashless-in reconciliation. When omitted, the script
    picks \\<host>\c$\Goldclub\var\log\SlotLog\<latest>.log.

.PARAMETER AssetNumber
    Expected cabinet asset number for the asset-binding check. Default 777.

.EXAMPLE
    .\Convert-AftHistory.ps1
    Parses cabinet 10.0.0.90 and writes reports to .\aft\report.

.EXAMPLE
    .\Convert-AftHistory.ps1 -ComputerName 10.0.0.90 -Verbose
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $StateRoot,
    [string] $OutputDir,
    [string] $SlotLogPath,
    [int]    $AssetNumber = 777
)

$ErrorActionPreference = 'Stop'

if (-not $StateRoot) {
    $StateRoot = "\\$ComputerName\c`$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1"
}
if (-not $OutputDir) {
    $OutputDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\report'
}
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

function Write-Step { param($m) Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "[!] $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# SAS field decoders
# ---------------------------------------------------------------------------
function ConvertFrom-AssetNumber {
    param([string] $Base64)
    if ([string]::IsNullOrWhiteSpace($Base64)) { return $null }
    try {
        $bytes = [Convert]::FromBase64String($Base64)
        if ($bytes.Length -lt 4) { $bytes = $bytes + (,[byte]0) * (4 - $bytes.Length) }
        return [BitConverter]::ToUInt32($bytes, 0)   # little-endian
    } catch { return $null }
}

function ConvertTo-HexString {
    param([string] $Base64)
    if ([string]::IsNullOrWhiteSpace($Base64)) { return $null }
    try { return (([Convert]::FromBase64String($Base64)) | ForEach-Object { $_.ToString('x2') }) -join '' }
    catch { return $null }
}

function ConvertFrom-BcdDateTime {
    # transactionCompletedDateTime is base64 of 7 BCD bytes: YY YY MM DD HH mm ss
    param([string] $Base64)
    if ([string]::IsNullOrWhiteSpace($Base64)) { return @{ Bcd = $null; Text = $null } }
    try {
        $bytes = [Convert]::FromBase64String($Base64)
        $bcd = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
        if ($bcd.Length -ge 14) {
            $y = $bcd.Substring(0,4); $mo = $bcd.Substring(4,2); $d = $bcd.Substring(6,2)
            $h = $bcd.Substring(8,2); $mi = $bcd.Substring(10,2); $s = $bcd.Substring(12,2)
            return @{ Bcd = $bcd.Substring(0,14); Text = "$y-$mo-$d $h`:$mi`:$s" }
        }
        return @{ Bcd = $bcd; Text = $null }
    } catch { return @{ Bcd = $null; Text = $null } }
}

function ConvertFrom-TransactionIdChars {
    param($TransactionIdNode)
    if ($null -eq $TransactionIdNode) { return $null }
    $chars = @($TransactionIdNode.char)
    if ($chars.Count -eq 0) { return $null }
    $sb = New-Object System.Text.StringBuilder
    foreach ($c in $chars) {
        $val = 0
        if ([int]::TryParse([string]$c, [ref]$val) -and $val -gt 0) {
            [void]$sb.Append([char]$val)
        }
    }
    return $sb.ToString().Trim([char]0, ' ')
}

function To-Dollars {
    param($Cents)
    if ($null -eq $Cents) { return $null }
    # Invariant culture so output is always "8100.00", not locale-formatted "8.100,00".
    return ([double]($Cents / 100.0)).ToString('F2', [System.Globalization.CultureInfo]::InvariantCulture)
}

# ---------------------------------------------------------------------------
# Transaction XML parser
# ---------------------------------------------------------------------------
function ConvertFrom-AftTransactionFile {
    param([System.IO.FileInfo] $File)

    try { [xml]$xml = Get-Content -LiteralPath $File.FullName -Raw } catch { return $null }
    # Most-recent mirrors use root <AftTransactionSerializer>; history slots use
    # <AftTransactionCarrier>. Empty history placeholders have no <aftTransaction>.
    $t = $xml.DocumentElement.SelectSingleNode('./*[local-name()="aftTransaction"]')
    if ($null -eq $t -or [string]::IsNullOrWhiteSpace([string]$t.transferStatus)) { return $null }

    $name = $File.Name
    $historyIndex = $null; $version = $null
    if ($name -match 'aftTransactionHistory_i(\d+)_v(\d+)') { $historyIndex = [int]$Matches[1]; $version = [int]$Matches[2] }
    elseif ($name -match '_v(\d+)\.xml$') { $version = [int]$Matches[1] }

    $completed = ConvertFrom-BcdDateTime ([string]$t.transactionCompletedDateTime)
    $txnText   = ConvertFrom-TransactionIdChars $t.transactionId

    $reqCash = [int64]$t.requestedCashableAmount
    $obtCash = [int64]$t.obtainedCashableAmount
    $totReq  = [int64]$t.requestedCashableAmount + [int64]$t.requestedRestrictedAmount + [int64]$t.requestedNonRestrictedAmount
    $totObt  = [int64]$t.obtainedCashableAmount  + [int64]$t.obtainedRestrictedAmount  + [int64]$t.obtainedNonRestrictedAmount

    # WAT details (first AurumTransaction, when present)
    $wat = $null
    $aurum = $null
    $aurumTransactions = $t.SelectSingleNode('./*[local-name()="aurumTransactions"]')
    if ($aurumTransactions) {
        $aurum = $aurumTransactions.SelectSingleNode('./*[local-name()="AurumTransaction"]')
    }
    if ($aurum -is [Array]) { $aurum = $aurum[0] }
    $aurumType = if ($aurum) { [string]$aurum.type } else { $null }
    if (-not $aurumType -and $aurum) { $aurumType = [string]$aurum.GetAttribute('xsi:type') }

    $req = if ($aurum) { $aurum.SelectSingleNode('./*[local-name()="requestTransfer"]') } else { $null }
    $com = if ($aurum) { $aurum.SelectSingleNode('./*[local-name()="commitTransfer"]') } else { $null }
    $acct = if ($aurum) { $aurum.SelectSingleNode('./*[local-name()="watTransactionDetails"]/*[local-name()="WatTransactionDetails"]/*[local-name()="account"]') } else { $null }

    [pscustomobject]@{
        SourceFile                     = $name
        HistoryIndex                   = $historyIndex
        Version                        = $version
        InstanceId                     = [string]$t.instanceId
        TransferStatus                 = [string]$t.transferStatus
        ReceiptStatus                  = [string]$t.receiptStatus
        TransferType                   = [string]$t.transferType
        RequestedCashable              = $reqCash
        RequestedRestricted            = [int64]$t.requestedRestrictedAmount
        RequestedNonRestricted         = [int64]$t.requestedNonRestrictedAmount
        ObtainedCashable               = $obtCash
        ObtainedRestricted             = [int64]$t.obtainedRestrictedAmount
        ObtainedNonRestricted          = [int64]$t.obtainedNonRestrictedAmount
        TotalRequested                 = $totReq
        TotalObtained                  = $totObt
        RequestedCashableDollars       = (To-Dollars $reqCash)
        ObtainedCashableDollars        = (To-Dollars $obtCash)
        AssetNumberBase64              = [string]$t.assetNumber
        AssetNumberHex                 = (ConvertTo-HexString ([string]$t.assetNumber))
        AssetNumber                    = (ConvertFrom-AssetNumber ([string]$t.assetNumber))
        TransactionIdText              = $txnText
        CompletedBcd                   = $completed.Bcd
        CompletedAtBcd                 = $completed.Text
        RequestTextMessage             = [string]$t.requestTextMessage
        CumulativeCashableMeter        = [int64]$t.comulativeCashableAmountMeter
        CumulativeCashableMeterDollars = (To-Dollars ([int64]$t.comulativeCashableAmountMeter))
        PartialTransferAllowed         = [string]$t.partialTransferAllowed
        Reported                       = [string]$t.aftTransactionReported
        Finished                       = [string]$t.aftTransactionFinished
        IsCashOutTransaction           = [string]$t.isCashOutTransaction
        TransactionInvalidated         = [string]$t.transactionInvalidated
        AurumTransactionType           = $aurumType
        TransferInitiatedTime          = if ($aurum) { [string]$aurum.transferInitiatedTime } else { $null }
        CommitTransferDateTime         = if ($com) { [string]$com.transferDateTime } else { $null }
        AccountId                      = if ($req) { [string]$req.accountId } else { $null }
        WatAmount                      = if ($req) { [string]$req.watAmount } else { $null }
        CreditType                     = if ($req) { [string]$req.creditType } else { $null }
        PoolId                         = if ($req) { [string]$req.poolId } else { $null }
        TransferAction                 = if ($req) { [string]$req.transferAction } else { $null }
        HostRequestId                  = if ($req) { [string]$req.hostRequestId } else { $null }
        RequestTransactionId           = if ($req) { [string]$req.transactionId } else { $null }
        CommitTransactionId            = if ($com) { [string]$com.transactionId } else { $null }
        CommitTransferAmount           = if ($com) { [string]$com.transferAmount } else { $null }
        CommitTransferException        = if ($com) { [string]$com.transferException } else { $null }
        AccountWithdrawOk              = if ($acct) { [string]$acct.withdrawOk } else { $null }
        AccountDepositOk               = if ($acct) { [string]$acct.depositOk } else { $null }
        AccountBalance                 = if ($acct) { [string]$acct.Balance } else { $null }
    }
}

# ---------------------------------------------------------------------------
# Current AFT settings
# ---------------------------------------------------------------------------
function Get-CurrentAftSettings {
    param([string] $Root)
    $candidates = Get-ChildItem -LiteralPath $Root -Filter '*_aftCurrentSettings_v*.xml' -ErrorAction SilentlyContinue
    if (-not $candidates) { return $null }
    $best = $null; $bestVer = -1
    foreach ($f in $candidates) {
        try { [xml]$x = Get-Content -LiteralPath $f.FullName -Raw } catch { continue }
        $ver = [int]($x.CurrentAftSettingsSerializer.version)
        if ($ver -ge $bestVer) { $bestVer = $ver; $best = @{ File = $f; Xml = $x } }
    }
    if (-not $best) { return $null }
    $s = $best.Xml.CurrentAftSettingsSerializer.currentAftSettings
    [pscustomobject]@{
        Path                         = $best.File.FullName
        Exists                       = $true
        RegistrationStatus           = [string]$s.registrationStatus
        RegistrationKey              = [string]$s.registrationKey
        TransferAllowedOnlyIfLocked  = [string]$s.transferFlags.transferAllowedOnlyIfLocked
        CashoutToHostControl         = [string]$s.transferFlags.cashoutToHostControl
        CashoutToHostCurrentlyEnabled= [string]$s.transferFlags.cashoutToHostCurrentlyEnabled
    }
}

# ---------------------------------------------------------------------------
# SlotLog cashless-in events
# ---------------------------------------------------------------------------
function Get-SlotLogCashlessIn {
    param([string] $Path)
    $result = New-Object System.Collections.Generic.List[object]
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $result }
    $rx = [regex]'^(?<ts>\S+).*OneHand\.AurumEGM - Cashless In:\s*\$(?<amt>[\d,]+\.\d{2})'
    # Open with shared read/write: the live LogDaemon keeps this file open for writing.
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        $reader = New-Object System.IO.StreamReader($fs)
        while ($null -ne ($line = $reader.ReadLine())) {
            $m = $rx.Match($line)
            if ($m.Success) {
                $amtText = $m.Groups['amt'].Value
                $cents = [int64]([double]($amtText -replace ',','') * 100)
                $result.Add([pscustomobject]@{
                    Timestamp   = $m.Groups['ts'].Value
                    AmountText  = $amtText
                    AmountCents = $cents
                    Line        = $line.Trim()
                })
            }
        }
    } finally { $fs.Dispose() }
    return $result
}

# ===========================================================================
# MAIN
# ===========================================================================
Write-Step "State root : $StateRoot"
if (-not (Test-Path -LiteralPath $StateRoot)) { throw "State root not reachable: $StateRoot" }

$historyDir = Join-Path $StateRoot 'History'
$files = New-Object System.Collections.Generic.List[System.IO.FileInfo]
Get-ChildItem -LiteralPath $StateRoot -Filter '*_aftMostRecentTransaction_v*.xml' -ErrorAction SilentlyContinue | ForEach-Object { $files.Add($_) }
if (Test-Path -LiteralPath $historyDir) {
    Get-ChildItem -LiteralPath $historyDir -Filter '*_aftTransactionHistory_i*_v*.xml' -ErrorAction SilentlyContinue | ForEach-Object { $files.Add($_) }
}
Write-Step "Candidate transaction files: $($files.Count)"

$raw = New-Object System.Collections.Generic.List[object]
foreach ($f in $files) {
    $parsed = ConvertFrom-AftTransactionFile $f
    if ($parsed) { $raw.Add($parsed) }
}
Write-Step "Parsed transaction rows (non-empty): $($raw.Count)"

# De-duplicate by InstanceId; prefer the row that carries a HistoryIndex, then newest commit time.
$dedup = $raw |
    Group-Object InstanceId |
    ForEach-Object {
        $_.Group | Sort-Object @{ Expression = { $null -ne $_.HistoryIndex }; Descending = $true },
                                @{ Expression = { $_.CommitTransferDateTime }; Descending = $true } |
        Select-Object -First 1
    } |
    Sort-Object { if ($_.CommitTransferDateTime) { [datetime]$_.CommitTransferDateTime } else { [datetime]::MinValue } }

$dedup = @($dedup)
$successful = @($dedup | Where-Object { $_.TransferStatus -like 'FULL_TRANSFER_SUCCESSFUL' })
$totalObtainedCashable = ($successful | Measure-Object -Property ObtainedCashable -Sum).Sum
if (-not $totalObtainedCashable) { $totalObtainedCashable = 0 }

$settings = Get-CurrentAftSettings -Root $StateRoot

# SlotLog selection
if (-not $SlotLogPath) {
    $slotLogDir = "\\$ComputerName\c`$\Goldclub\var\log\SlotLog"
    if (Test-Path -LiteralPath $slotLogDir) {
        $latest = Get-ChildItem -LiteralPath $slotLogDir -Filter '*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) { $SlotLogPath = $latest.FullName }
    }
}
$cashlessIn = Get-SlotLogCashlessIn -Path $SlotLogPath
$slotLogDate = $null
if ($SlotLogPath -and $SlotLogPath -match '(\d{4}-\d{2}-\d{2})\.log$') { $slotLogDate = $Matches[1] }

# ---------------------------------------------------------------------------
# Robustness checks
# ---------------------------------------------------------------------------
$checks = New-Object System.Collections.Generic.List[object]

# 1. Reconcile successful AFT (for the SlotLog date) against SlotLog cashless-in.
$aftForDate = $successful
if ($slotLogDate) { $aftForDate = @($successful | Where-Object { $_.CompletedAtBcd -like "$slotLogDate*" }) }
$aftSum = ($aftForDate | Measure-Object -Property ObtainedCashable -Sum).Sum; if (-not $aftSum) { $aftSum = 0 }
$matchEvents = @($cashlessIn | Where-Object { $_.AmountCents -gt 0 })
$slSum = ($matchEvents | Measure-Object -Property AmountCents -Sum).Sum; if (-not $slSum) { $slSum = 0 }
$reconStatus = if ($aftForDate.Count -eq $matchEvents.Count -and $aftSum -eq $slSum) { 'PASS' } else { 'WARN' }
$checks.Add([pscustomobject]@{
    Id = 'AFT_SLOTLOG_RECONCILIATION'; Severity = 'High'; Status = $reconStatus
    Message = "Successful AFT transfers vs SlotLog Cashless In events: AFT count/sum=$($aftForDate.Count)/$aftSum, SlotLog relevant count/sum=$($matchEvents.Count)/$slSum."
    Details = [pscustomobject]@{
        AftSuccessfulCount = $aftForDate.Count; AftObtainedCashable = $aftSum
        ComparedSlotLogDate = $slotLogDate
        SlotLogCashlessInMatchingAmountCount = $matchEvents.Count
        SlotLogCashlessInMatchingAmountSum = $slSum
        SlotLogPath = $SlotLogPath
    }
})

# 2. Duplicate hostRequestId
$dupHost = @($dedup | Where-Object { $_.HostRequestId } | Group-Object HostRequestId | Where-Object { $_.Count -gt 1 })
$checks.Add([pscustomobject]@{
    Id = 'DUPLICATE_HOST_REQUEST_ID'; Severity = 'Critical'; Status = ($(if ($dupHost.Count -eq 0) {'PASS'} else {'FAIL'}))
    Message = ($(if ($dupHost.Count -eq 0) { 'No duplicate hostRequestId values found in de-duplicated AFT history.' } else { "Found $($dupHost.Count) duplicated hostRequestId value(s)." }))
    Details = @($dupHost | ForEach-Object { [pscustomobject]@{ HostRequestId = $_.Name; Count = $_.Count } })
})

# 3. Duplicate WAT transaction id
$dupWat = @($dedup | Where-Object { $_.CommitTransactionId } | Group-Object CommitTransactionId | Where-Object { $_.Count -gt 1 })
$checks.Add([pscustomobject]@{
    Id = 'DUPLICATE_WAT_TRANSACTION_ID'; Severity = 'Critical'; Status = ($(if ($dupWat.Count -eq 0) {'PASS'} else {'FAIL'}))
    Message = ($(if ($dupWat.Count -eq 0) { 'No duplicate WAT transaction IDs found in de-duplicated AFT history.' } else { "Found $($dupWat.Count) duplicated WAT transaction ID(s)." }))
    Details = @($dupWat | ForEach-Object { [pscustomobject]@{ TransactionId = $_.Name; Count = $_.Count } })
})

# 4. Non-incrementing WAT transaction id (in commit-time order)
$ordered = @($dedup | Where-Object { $_.CommitTransactionId } )
$nonInc = $false; $prev = $null
foreach ($r in $ordered) {
    $cur = 0; [void][int]::TryParse([string]$r.CommitTransactionId, [ref]$cur)
    if ($null -ne $prev -and $cur -le $prev) { $nonInc = $true }
    $prev = $cur
}
$checks.Add([pscustomobject]@{
    Id = 'NON_INCREMENTING_WAT_TRANSACTION_ID'; Severity = 'High'; Status = ($(if (-not $nonInc) {'PASS'} else {'FAIL'}))
    Message = ($(if (-not $nonInc) { 'WAT transaction IDs are strictly increasing in commit-time order.' } else { 'WAT transaction IDs are NOT strictly increasing in commit-time order.' }))
    Details = @()
})

# 5. Success while unregistered
if ($settings -and $settings.RegistrationStatus -eq 'GAMING_MACHINE_NOT_REGISTERED' -and $successful.Count -gt 0) {
    $checks.Add([pscustomobject]@{
        Id = 'SUCCESS_WHILE_UNREGISTERED'; Severity = 'High'; Status = 'WARN'
        Message = "Current settings report GAMING_MACHINE_NOT_REGISTERED with $($successful.Count) successful transfer(s) in history."
        Details = [pscustomobject]@{ RegistrationStatus = $settings.RegistrationStatus; RegistrationKey = $settings.RegistrationKey; SuccessfulTransferCount = $successful.Count }
    })
} else {
    $checks.Add([pscustomobject]@{
        Id = 'SUCCESS_WHILE_UNREGISTERED'; Severity = 'High'; Status = 'PASS'
        Message = 'Registration state is consistent with transfer activity (or machine is registered).'
        Details = [pscustomobject]@{ RegistrationStatus = ($(if ($settings) { $settings.RegistrationStatus } else { 'UNKNOWN' })); SuccessfulTransferCount = $successful.Count }
    })
}

$checkCounts = [pscustomobject]@{
    Pass = @($checks | Where-Object { $_.Status -eq 'PASS' }).Count
    Warn = @($checks | Where-Object { $_.Status -eq 'WARN' }).Count
    Fail = @($checks | Where-Object { $_.Status -eq 'FAIL' }).Count
}

# ---------------------------------------------------------------------------
# Emit outputs
# ---------------------------------------------------------------------------
$txnCsv     = Join-Path $OutputDir 'aft-transactions.csv'
$txnJson    = Join-Path $OutputDir 'aft-transactions.json'
$rawCsv     = Join-Path $OutputDir 'aft-transactions-raw.csv'
$checksJson = Join-Path $OutputDir 'aft-checks.json'
$checksCsv  = Join-Path $OutputDir 'aft-checks.csv'
$slotCsv    = Join-Path $OutputDir 'slotlog-cashless-in.csv'
$settsJson  = Join-Path $OutputDir 'aft-current-settings.json'
$summaryJson= Join-Path $OutputDir 'aft-summary.json'
$txnMd      = Join-Path $OutputDir 'aft-transactions.md'

$dedup | Export-Csv -LiteralPath $txnCsv -NoTypeInformation -Encoding UTF8
$dedup | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $txnJson -Encoding UTF8
$raw   | Export-Csv -LiteralPath $rawCsv -NoTypeInformation -Encoding UTF8
$checks | Select-Object Id,Severity,Status,Message | Export-Csv -LiteralPath $checksCsv -NoTypeInformation -Encoding UTF8
$checks | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $checksJson -Encoding UTF8
$cashlessIn | Export-Csv -LiteralPath $slotCsv -NoTypeInformation -Encoding UTF8

if ($settings) {
    [pscustomobject]@{
        RegistrationStatus            = $settings.RegistrationStatus
        RegistrationKeyBase64         = $settings.RegistrationKey
        TransferAllowedOnlyIfLocked   = $settings.TransferAllowedOnlyIfLocked
        CashoutToHostControl          = $settings.CashoutToHostControl
        CashoutToHostCurrentlyEnabled = $settings.CashoutToHostCurrentlyEnabled
        SourceFile                    = $settings.Path
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $settsJson -Encoding UTF8
}

$latest = $dedup | Select-Object -Last 1
$summary = [pscustomobject]@{
    StateRoot                    = $StateRoot
    OutputDir                    = $OutputDir
    GeneratedAt                  = (Get-Date).ToString('s')
    TransactionCount             = $dedup.Count
    RawRowCount                  = $raw.Count
    SuccessfulCount              = $successful.Count
    TotalObtainedCashable        = $totalObtainedCashable
    TotalObtainedCashableDollars = (To-Dollars $totalObtainedCashable)
    Latest                       = $latest
    CurrentSettings              = if ($settings) { [pscustomobject]@{
        Path = $settings.Path; Exists = $true
        RegistrationStatus = $settings.RegistrationStatus
        RegistrationKey = $settings.RegistrationKey
        TransferAllowedOnlyIfLocked = $settings.TransferAllowedOnlyIfLocked
    } } else { $null }
    CheckCounts                  = $checkCounts
    CsvPath                      = $txnCsv
    JsonPath                     = $txnJson
    RawCsvPath                   = $rawCsv
    ChecksJsonPath               = $checksJson
    ChecksCsvPath                = $checksCsv
    SlotCashlessCsvPath          = $slotCsv
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryJson -Encoding UTF8

# Markdown ledger
$md = New-Object System.Collections.Generic.List[string]
$md.Add('# AFT Transaction Ledger')
$md.Add('')
$md.Add("Generated: $((Get-Date).ToString('s'))")
$md.Add("Source: ``$StateRoot``")
$md.Add('')
$md.Add("Transactions parsed: $($dedup.Count)")
$md.Add("Successful transfers: $($successful.Count)")
$md.Add("Total obtained cashable: $totalObtainedCashable cents (`$$(To-Dollars $totalObtainedCashable))")
$md.Add('')
$md.Add('| Completed | Status | Type | Cashable | TransactionId | Asset | Source |')
$md.Add('|---|---|---|---:|---|---:|---|')
foreach ($r in $dedup) {
    $md.Add("| $($r.CompletedAtBcd) | $($r.TransferStatus) | $($r.TransferType) | $($r.ObtainedCashableDollars) | $($r.TransactionIdText) | $($r.AssetNumber) | $($r.SourceFile) |")
}
$md -join "`r`n" | Set-Content -LiteralPath $txnMd -Encoding UTF8

# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------
Write-Ok "Transactions (de-duplicated): $($dedup.Count)  |  Successful: $($successful.Count)  |  Obtained cashable: `$$(To-Dollars $totalObtainedCashable)"
if ($settings) { Write-Step "Registration: $($settings.RegistrationStatus)" }
Write-Step "Checks: PASS=$($checkCounts.Pass) WARN=$($checkCounts.Warn) FAIL=$($checkCounts.Fail)"
foreach ($c in $checks) {
    $color = switch ($c.Status) { 'PASS' { 'Green' } 'WARN' { 'Yellow' } 'FAIL' { 'Red' } default { 'Gray' } }
    Write-Host ("    [{0}] {1} ({2})" -f $c.Status, $c.Id, $c.Severity) -ForegroundColor $color
}
Write-Ok "Reports written to: $OutputDir"
