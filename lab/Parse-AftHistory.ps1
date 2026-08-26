<#
.SYNOPSIS
    Read-only parser for Goldclub/Aurum AFT transaction state.

.DESCRIPTION
    Parses the cabinet's AFT transaction XML files and exports a deduplicated
    transfer ledger as CSV, JSON, and Markdown. It reads:

      - aftMostRecentTransaction_v*.xml
      - History/*_aftTransactionHistory_i*_v*.xml
      - aftCurrentSettings_v*.xml

    The script never writes to the cabinet. Outputs are written locally under
    -OutDir.
#>
[CmdletBinding()]
param(
    [string] $SourceRoot = '\\10.0.0.90\c$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1',
    [string] $OutDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\report')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NodeText {
    param($Node, [string] $Path)
    $n = $Node.SelectSingleNode($Path)
    if ($null -eq $n) { return $null }
    return $n.InnerText
}

function Get-AttrLocal {
    param($Node, [string] $Name)
    if ($null -eq $Node -or $null -eq $Node.Attributes) { return $null }
    foreach ($a in $Node.Attributes) {
        if ($a.LocalName -eq $Name) { return $a.Value }
    }
    return $null
}

function To-Int64OrNull {
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $n = 0L
    if ([Int64]::TryParse($Value, [ref]$n)) { return $n }
    return $null
}

function Decode-Base64Bytes {
    param([string] $Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return @() }
    try { return [Convert]::FromBase64String($Value) } catch { return @() }
}

function Decode-AssetNumber {
    param([string] $Value)
    $bytes = Decode-Base64Bytes $Value
    if ($bytes.Count -eq 0) { return $null }
    if ($bytes.Count -ge 4) { return [BitConverter]::ToUInt32($bytes, 0) }
    $n = 0
    for ($i = 0; $i -lt $bytes.Count; $i++) { $n += $bytes[$i] -shl (8 * $i) }
    return $n
}

function Decode-BcdDateTime {
    param([string] $Value)
    $bytes = Decode-Base64Bytes $Value
    if ($bytes.Count -lt 7) { return $null }
    $digits = foreach ($b in $bytes[0..6]) { '{0:X2}' -f $b }
    $text = ($digits -join '')
    # Observed format: YY YY MM DD HH mm ss, e.g. 20 26 06 05 11 00 53.
    return '{0}{1}-{2}-{3}T{4}:{5}:{6}' -f $digits[0], $digits[1], $digits[2], $digits[3], $digits[4], $digits[5], $digits[6]
}

function Decode-CharList {
    param($Node)
    if ($null -eq $Node) { return $null }
    $chars = New-Object System.Collections.Generic.List[char]
    foreach ($c in $Node.SelectNodes('./char')) {
        $n = 0
        if ([Int32]::TryParse($c.InnerText, [ref]$n) -and $n -gt 0) {
            $chars.Add([char]$n)
        }
    }
    return -join $chars
}

function Format-Usd {
    param([double] $Value)
    return $Value.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture)
}

function Parse-AftFile {
    param([string] $Path)
    [xml] $xml = Get-Content -LiteralPath $Path -Raw
    $tx = $xml.SelectSingleNode('//*[local-name()="aftTransaction"]')
    if ($null -eq $tx) { return $null }

    $aurum = $tx.SelectSingleNode('.//*[local-name()="AurumTransaction"]')
    $request = $aurum.SelectSingleNode('.//*[local-name()="requestTransfer"]')
    $authorize = $aurum.SelectSingleNode('.//*[local-name()="authorizeTransfer"]')
    $commit = $aurum.SelectSingleNode('.//*[local-name()="commitTransfer"]')
    $account = $aurum.SelectSingleNode('.//*[local-name()="account"]')

    $instanceId = Get-NodeText $tx './instanceId'
    if ([string]::IsNullOrWhiteSpace($instanceId)) { return $null }

    $completedRaw = Get-NodeText $tx './transactionCompletedDateTime'
    $assetRaw = Get-NodeText $tx './assetNumber'
    $requestedCashable = To-Int64OrNull (Get-NodeText $tx './requestedCashableAmount')
    $obtainedCashable = To-Int64OrNull (Get-NodeText $tx './obtainedCashableAmount')

    $initiatedTime = Get-NodeText $aurum './transferInitiatedTime'
    $commitTime = Get-AttrLocal $commit 'transferDateTime'
    $completedTime = Decode-BcdDateTime $completedRaw
    if (-not $completedTime) { $completedTime = $commitTime }
    if (-not $completedTime) { $completedTime = $initiatedTime }

    [pscustomobject]@{
        InstanceId = $instanceId
        SourceFile = $Path
        Status = Get-NodeText $tx './transferStatus'
        ReceiptStatus = Get-NodeText $tx './receiptStatus'
        TransferType = Get-NodeText $tx './transferType'
        IsCashOut = [string](Get-NodeText $tx './isCashOutTransaction')
        RequestedCashable = $requestedCashable
        RequestedRestricted = To-Int64OrNull (Get-NodeText $tx './requestedRestrictedAmount')
        RequestedNonRestricted = To-Int64OrNull (Get-NodeText $tx './requestedNonRestrictedAmount')
        ObtainedCashable = $obtainedCashable
        ObtainedRestricted = To-Int64OrNull (Get-NodeText $tx './obtainedRestrictedAmount')
        ObtainedNonRestricted = To-Int64OrNull (Get-NodeText $tx './obtainedNonRestrictedAmount')
        AmountDollars = if ($obtainedCashable -ne $null) { [Math]::Round($obtainedCashable / 100.0, 2) } else { $null }
        AssetNumber = Decode-AssetNumber $assetRaw
        AssetNumberBase64 = $assetRaw
        TransactionId = Decode-CharList ($tx.SelectSingleNode('./transactionId'))
        CompletedTime = $completedTime
        InitiatedTime = $initiatedTime
        RequestTextMessage = Get-NodeText $tx './requestTextMessage'
        Reported = [string](Get-NodeText $tx './aftTransactionReported')
        Finished = [string](Get-NodeText $tx './aftTransactionFinished')
        Invalidated = [string](Get-NodeText $tx './transactionInvalidated')
        CumulativeCashableMeter = To-Int64OrNull (Get-NodeText $tx './comulativeCashableAmountMeter')
        CumulativeRestrictedMeter = To-Int64OrNull (Get-NodeText $tx './comulativeRestrictedAmountMeter')
        CumulativeNonRestrictedMeter = To-Int64OrNull (Get-NodeText $tx './comulativeNonRestrictedAmountMeter')
        AurumType = Get-AttrLocal $aurum 'type'
        AccountId = Get-AttrLocal $request 'accountId'
        HostRequestId = Get-AttrLocal $request 'hostRequestId'
        WatTransactionId = Get-AttrLocal $request 'transactionId'
        CommitTransferAmount = To-Int64OrNull (Get-AttrLocal $commit 'transferAmount')
        CommitTransferException = Get-AttrLocal $commit 'transferException'
        CommitTransferTime = $commitTime
        CreditType = Get-AttrLocal $account 'creditType'
        WithdrawOk = Get-AttrLocal $account 'withdrawOk'
        DepositOk = Get-AttrLocal $account 'depositOk'
    }
}

function Write-MarkdownLedger {
    param($Rows, [string] $Path)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# AFT Transaction Ledger')
    $lines.Add('')
    $lines.Add("Generated: $(Get-Date -Format s)")
    $lines.Add("Source: ``$SourceRoot``")
    $lines.Add('')
    $lines.Add("Transactions parsed: $($Rows.Count)")
    if ($Rows.Count -gt 0) {
        $totalCashable = ($Rows | Measure-Object -Property ObtainedCashable -Sum).Sum
        $successes = @($Rows | Where-Object { $_.Status -eq 'FULL_TRANSFER_SUCCESSFUL' }).Count
        $lines.Add("Successful transfers: $successes")
        $lines.Add(('Total obtained cashable: {0} cents (${1})' -f $totalCashable, (Format-Usd ($totalCashable / 100.0))))
        $lines.Add('')
        $lines.Add('| Completed | Status | Type | Cashable | TransactionId | Asset | Source |')
        $lines.Add('|---|---|---|---:|---|---:|---|')
        foreach ($r in $Rows | Sort-Object CompletedTime) {
            $source = Split-Path -Leaf $r.SourceFile
            $lines.Add(('| {0} | {1} | {2} | {3} | {4} | {5} | {6} |' -f $r.CompletedTime, $r.Status, $r.TransferType, (Format-Usd ($r.ObtainedCashable / 100.0)), $r.TransactionId, $r.AssetNumber, $source))
        }
    }
    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $SourceRoot)) { throw "SourceRoot not found: $SourceRoot" }
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$files = New-Object System.Collections.Generic.List[string]
$patterns = @(
    '*_aftMostRecentTransaction_v*.xml',
    'GCC_ST_20664_01_aftMostRecentTransaction_v*.xml',
    'History\*_aftTransactionHistory_i*_v*.xml'
)
foreach ($pattern in $patterns) {
    Get-ChildItem -LiteralPath $SourceRoot -Filter $pattern -File -ErrorAction SilentlyContinue | ForEach-Object { $files.Add($_.FullName) }
}
Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'History') -Filter '*_aftTransactionHistory_i*_v*.xml' -File -ErrorAction SilentlyContinue | ForEach-Object { $files.Add($_.FullName) }

$rows = foreach ($file in ($files | Sort-Object -Unique)) {
    try { Parse-AftFile $file } catch { Write-Warning "Failed to parse $file`: $_" }
}

$deduped = $rows |
    Where-Object { $null -ne $_ } |
    Group-Object InstanceId |
    ForEach-Object {
        $_.Group |
            Sort-Object @{ Expression = { if ($_.SourceFile -match '_v2\.xml$') { 0 } else { 1 } } }, SourceFile |
            Select-Object -First 1
    } |
    Sort-Object CompletedTime, InstanceId

$currentSettingsPath = Join-Path $SourceRoot 'GCC_ST_20664_01_aftCurrentSettings_v2.xml'
$settings = $null
if (Test-Path -LiteralPath $currentSettingsPath) {
    [xml] $settingsXml = Get-Content -LiteralPath $currentSettingsPath -Raw
    $current = $settingsXml.SelectSingleNode('//*[local-name()="currentAftSettings"]')
    if ($current) {
        $settings = [pscustomobject]@{
            RegistrationStatus = Get-NodeText $current './registrationStatus'
            RegistrationKeyBase64 = Get-NodeText $current './registrationKey'
            TransferAllowedOnlyIfLocked = Get-NodeText $current './transferFlags/transferAllowedOnlyIfLocked'
            CashoutToHostControl = Get-NodeText $current './transferFlags/cashoutToHostControl'
            CashoutToHostCurrentlyEnabled = Get-NodeText $current './transferFlags/cashoutToHostCurrentlyEnabled'
            SourceFile = $currentSettingsPath
        }
    }
}

$csvPath = Join-Path $OutDir 'aft-transactions.csv'
$jsonPath = Join-Path $OutDir 'aft-transactions.json'
$mdPath = Join-Path $OutDir 'aft-transactions.md'
$settingsPath = Join-Path $OutDir 'aft-current-settings.json'

$deduped | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8
$deduped | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8
Write-MarkdownLedger -Rows $deduped -Path $mdPath
if ($settings) { $settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $settingsPath -Encoding UTF8 }

[pscustomobject]@{
    Transactions = @($deduped).Count
    Csv = $csvPath
    Json = $jsonPath
    Markdown = $mdPath
    Settings = if ($settings) { $settingsPath } else { $null }
    RegistrationStatus = if ($settings) { $settings.RegistrationStatus } else { $null }
}
