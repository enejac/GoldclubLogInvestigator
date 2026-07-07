<#
.SYNOPSIS
    Lab AFT test harness: YOU trigger the transfer on the IGT SAS tester; this script
    watches the cabinet and verifies the result (read-only).

.DESCRIPTION
    Mimics the documented IGT-tester workflow without sending credits itself:

      1. Snapshot current AFT state + SlotLog cashless-in baseline
      2. Print tester steps (COM11, amount, asset)
      3. Wait until a NEW transfer appears in AFT XML and/or SlotLog
      4. Run Convert-AftHistory.ps1 and compare before/after
      5. Emit PASS/FAIL verification for the detected transfer

    The script never writes to the cabinet and never injects SAS/WAT traffic.
    Use the legitimate IGT SAS tester on the MUX/SAS port (COM11 on the lab EGM).

.PARAMETER ComputerName
    Cabinet host. Default 10.0.0.90.

.PARAMETER ExpectedAmountCents
    Expected cashable amount in cents (default 100000 = $1,000).

.PARAMETER TimeoutSec
    Seconds to wait for a new transfer after you start the tester action.

.PARAMETER AssetNumber
    Expected cabinet asset number (default 777).

.PARAMETER OutputRoot
    Folder for before/after audit reports. Default: <repo>\aft-test

.PARAMETER BaselineOnly
    Snapshot + before audit only (no wait). Use to confirm cabinet reachability.

.PARAMETER AllowUnregistered
    Continue even when current AFT settings report GAMING_MACHINE_NOT_REGISTERED.
    Default behavior is to fail fast because host->EGM AFT transfers will not persist
    when the EGM is not registered/ready.

.PARAMETER Bonus
    Validate an AFT BONUS / WAT transfer instead of a standard registered cashable
    transfer. Bonus awards are accepted by the EGM while unregistered, so this mode
    implies -AllowUnregistered, accepts PARTIAL_TRANSFER_SUCCESSFUL, and compares the
    TOTAL obtained amount (cashable + restricted + non-restricted) against the expected
    amount rather than the cashable bucket only.

.PARAMETER AfterBaselineScript
    Optional script path invoked immediately after the before snapshot and before the
    wait loop. Use for automated triggers (e.g. Send-TestAft1000.ps1 -Send / the WinDivert
    in-stream injection) so the baseline is captured before the transfer is sent.

.PARAMETER DirectPost
    Skip the manual IGT tester instructions. Implies an automated after-baseline action
    is in use (typically -AfterBaselineScript).

.EXAMPLE
    .\Invoke-AftTransferTest.ps1
    .\Invoke-AftTransferTest.ps1 -ExpectedAmountCents 100000 -TimeoutSec 300
    .\Invoke-AftTransferTest.ps1 -BaselineOnly
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [int]    $ExpectedAmountCents = 100000,
    [int]    $TimeoutSec = 180,
    [int]    $AssetNumber = 777,
    [string] $OutputRoot,
    [switch] $BaselineOnly,
    [switch] $AllowUnregistered,
    [switch] $Bonus,
    [string] $AfterBaselineScript,
    [switch] $DirectPost
)

# Bonus/WAT awards are accepted while unregistered; treat as allowed automatically.
if ($Bonus) { $AllowUnregistered = $true }

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
if (-not $OutputRoot) { $OutputRoot = Join-Path $here 'aft-test' }
$convert = Join-Path $here 'Convert-AftHistory.ps1'
if (-not (Test-Path -LiteralPath $convert)) { throw "Missing $convert" }

$StateRoot = "\\$ComputerName\c`$\Goldclub\var\state\goldclub.aurum.services\GCMessenger\SASControler1"
$SlotLogDir = "\\$ComputerName\c`$\Goldclub\var\log\SlotLog"
$AurumLogDir = "\\$ComputerName\c`$\Goldclub\var\log\GoldClub.Aurum.Services"

function Write-Step { param($m) Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[-] $m" -ForegroundColor Red }

function ConvertFrom-AssetNumber {
    param([string] $Base64)
    if ([string]::IsNullOrWhiteSpace($Base64)) { return $null }
    try {
        $bytes = [Convert]::FromBase64String($Base64)
        if ($bytes.Length -lt 4) { $bytes = $bytes + (,[byte]0) * (4 - $bytes.Length) }
        return [BitConverter]::ToUInt32($bytes, 0)
    } catch { return $null }
}

function Get-LatestSlotLogPath {
    if (-not (Test-Path -LiteralPath $SlotLogDir)) { return $null }
    Get-ChildItem -LiteralPath $SlotLogDir -Filter '*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}

function Get-LatestAurumLogPath {
    if (-not (Test-Path -LiteralPath $AurumLogDir)) { return $null }
    Get-ChildItem -LiteralPath $AurumLogDir -Filter '*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}

function Get-SlotLogCashlessTail {
    param(
        [string] $Path,
        [int]    $FromByteOffset = 0
    )
    $out = @()
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $out }
    $rx = [regex]'^(?<ts>\S+).*OneHand\.AurumEGM - Cashless In:\s*\$(?<amt>[\d,]+\.\d{2})'
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($FromByteOffset -gt 0 -and $FromByteOffset -lt $fs.Length) {
            $fs.Seek($FromByteOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
        }
        $reader = New-Object System.IO.StreamReader($fs)
        while ($null -ne ($line = $reader.ReadLine())) {
            $m = $rx.Match($line)
            if ($m.Success) {
                $amtText = $m.Groups['amt'].Value
                $cents = [int64]([double]($amtText -replace ',', '') * 100)
                $out += [pscustomobject]@{
                    Timestamp   = $m.Groups['ts'].Value
                    AmountCents = $cents
                    AmountText  = $amtText
                    Line        = $line.Trim()
                }
            }
        }
        return ,@($out, $fs.Length)
    } finally { $fs.Dispose() }
}

function Get-AurumAftTail {
    param(
        [string] $Path,
        [int]    $FromByteOffset = 0
    )
    $events = @()
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return ,@($events, 0) }
    $rxException = [regex]'^(?<ts>\S+).*AFT EXCEPTION ISSUED:\s*(?<code>\d+)'
    $rxCashout = [regex]'^(?<ts>\S+).*CASHOUT BUTTON PRESSED'
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
        if ($FromByteOffset -gt 0 -and $FromByteOffset -lt $fs.Length) {
            $fs.Seek($FromByteOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
        }
        $reader = New-Object System.IO.StreamReader($fs)
        while ($null -ne ($line = $reader.ReadLine())) {
            $lineText = $line.Trim()
            $m = $rxException.Match($lineText)
            if ($m.Success) {
                $events += [pscustomobject]@{
                    Kind      = 'AFT_EXCEPTION'
                    Timestamp = $m.Groups['ts'].Value
                    Code      = $m.Groups['code'].Value
                    Line      = $lineText
                }
                continue
            }
            $m = $rxCashout.Match($lineText)
            if ($m.Success) {
                $events += [pscustomobject]@{
                    Kind      = 'CASHOUT_BUTTON'
                    Timestamp = $m.Groups['ts'].Value
                    Code      = $null
                    Line      = $lineText
                }
            }
        }
        return ,@($events, $fs.Length)
    } finally { $fs.Dispose() }
}

function Read-AftCurrentSettings {
    if (-not (Test-Path -LiteralPath $StateRoot)) { return $null }
    $f = Get-ChildItem -LiteralPath $StateRoot -Filter '*_aftCurrentSettings_v*.xml' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { return $null }
    try {
        [xml]$x = Get-Content -LiteralPath $f.FullName -Raw
        $s = $x.CurrentAftSettingsSerializer.currentAftSettings
        [pscustomobject]@{
            FilePath                       = $f.FullName
            FileTimeUtc                    = $f.LastWriteTimeUtc
            RegistrationStatus             = [string]$s.registrationStatus
            TransferAllowedOnlyIfLocked    = [string]$s.transferFlags.transferAllowedOnlyIfLocked
            CashoutToHostControl           = [string]$s.transferFlags.cashoutToHostControl
            CashoutToHostCurrentlyEnabled  = [string]$s.transferFlags.cashoutToHostCurrentlyEnabled
        }
    } catch { return $null }
}

function Read-AftMostRecent {
    if (-not (Test-Path -LiteralPath $StateRoot)) { return $null }
    $f = Get-ChildItem -LiteralPath $StateRoot -Filter '*_aftMostRecentTransaction_v*.xml' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $f) { return $null }
    try {
        [xml]$x = Get-Content -LiteralPath $f.FullName -Raw
        $t = $x.DocumentElement.aftTransaction
        if (-not $t -or -not $t.transferStatus) { return $null }
        $aurum = $t.aurumTransactions.AurumTransaction
        if ($aurum -is [Array]) { $aurum = $aurum[0] }
        $req = $aurum.requestTransfer
        $com = $aurum.commitTransfer
        $obtCash    = [int64]$t.obtainedCashableAmount
        $obtRestr   = [int64]$t.obtainedRestrictedAmount
        $obtNonRes  = [int64]$t.obtainedNonRestrictedAmount
        [pscustomobject]@{
            FilePath              = $f.FullName
            FileTimeUtc           = $f.LastWriteTimeUtc
            InstanceId            = [string]$t.instanceId
            TransferStatus        = [string]$t.transferStatus
            TransferType          = [string]$t.transferType
            ObtainedCashable      = $obtCash
            ObtainedRestricted    = $obtRestr
            ObtainedNonRestricted = $obtNonRes
            ObtainedTotal         = ($obtCash + $obtRestr + $obtNonRes)
            AssetNumber           = (ConvertFrom-AssetNumber ([string]$t.assetNumber))
            CreditType            = if ($req) { [string]$req.creditType } else { $null }
            HostRequestId         = if ($req) { [string]$req.hostRequestId } else { $null }
            RequestTxnId          = if ($req) { [string]$req.transactionId } else { $null }
            CommitTxnId           = if ($com) { [string]$com.transactionId } else { $null }
            CommitException       = if ($com) { [string]$com.transferException } else { $null }
            WatAccount            = if ($req) { [string]$req.accountId } else { $null }
        }
    } catch { return $null }
}

function Get-AftSnapshot {
    param([int] $SlotLogByteOffset = 0)
    $slotPath = Get-LatestSlotLogPath
    $parsed = Get-SlotLogCashlessTail -Path $slotPath -FromByteOffset $SlotLogByteOffset
    $newEvents = @($parsed[0])
    $fileLen = $parsed[1]
    $aurumPath = Get-LatestAurumLogPath
    $aurumParsed = Get-AurumAftTail -Path $aurumPath
    $aurumEvents = @($aurumParsed[0])
    $aurumLen = $aurumParsed[1]
    $most = Read-AftMostRecent
    [pscustomobject]@{
        TakenAtUtc           = (Get-Date).ToUniversalTime()
        SlotLogPath          = $slotPath
        SlotLogByteLength    = $fileLen
        CashlessCount        = $newEvents.Count
        LastCashless         = if ($newEvents.Count) { $newEvents[-1] } else { $null }
        AurumLogPath         = $aurumPath
        AurumLogByteLength   = $aurumLen
        AurumAftEventCount   = $aurumEvents.Count
        LastAurumAftEvent    = if ($aurumEvents.Count) { $aurumEvents[-1] } else { $null }
        MostRecent           = $most
        CurrentSettings      = Read-AftCurrentSettings
    }
}

function Collect-KnownIds {
    $hosts = New-Object System.Collections.Generic.HashSet[string]
    $insts = New-Object System.Collections.Generic.HashSet[string]
    $dirs = @($StateRoot)
    $hist = Join-Path $StateRoot 'History'
    if (Test-Path -LiteralPath $hist) { $dirs += $hist }
    foreach ($dir in $dirs) {
        Get-ChildItem -LiteralPath $dir -Filter '*aft*Transaction*.xml' -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                [xml]$x = Get-Content -LiteralPath $_.FullName -Raw
                $t = $x.DocumentElement.aftTransaction
                if (-not $t) { return }
                $iid = [string]$t.instanceId
                if ($iid) { [void]$insts.Add($iid) }
                $aurum = $t.aurumTransactions.AurumTransaction
                if ($aurum -is [Array]) { $aurum = $aurum[0] }
                $hr = [string]$aurum.requestTransfer.hostRequestId
                if ($hr) { [void]$hosts.Add($hr) }
            } catch {}
        }
    }
    ,@($hosts), @($insts)
}

function Test-NewTransfer {
    param($Before, $After)
    $reasons = @()
    $mrB = $Before.MostRecent
    $mrA = $After.MostRecent
    $newHost = $false
    $newInstance = $false
    $newSlot = $false
    $newAftException = $false

    if ($mrA -and $mrB) {
        if ($mrA.HostRequestId -and $mrA.HostRequestId -ne $mrB.HostRequestId) { $newHost = $true }
        if ($mrA.InstanceId -and $mrA.InstanceId -ne $mrB.InstanceId) { $newInstance = $true }
        if ($mrA.FileTimeUtc -gt $mrB.FileTimeUtc) { $reasons += 'mostRecent file updated' }
    } elseif ($mrA -and -not $mrB) {
        $newHost = $true
    }

    if ($After.CashlessCount -gt $Before.CashlessCount) { $newSlot = $true }
    if ($Before.LastCashless -and $After.LastCashless) {
        if ($After.LastCashless.Timestamp -ne $Before.LastCashless.Timestamp) { $newSlot = $true }
    } elseif ($After.LastCashless -and -not $Before.LastCashless) {
        $newSlot = $true
    }

    if ($After.AurumAftEventCount -gt $Before.AurumAftEventCount) { $newAftException = $true }
    if ($Before.LastAurumAftEvent -and $After.LastAurumAftEvent) {
        if ($After.LastAurumAftEvent.Timestamp -ne $Before.LastAurumAftEvent.Timestamp -or
            $After.LastAurumAftEvent.Line -ne $Before.LastAurumAftEvent.Line) { $newAftException = $true }
    } elseif ($After.LastAurumAftEvent -and -not $Before.LastAurumAftEvent) {
        $newAftException = $true
    }

    $detected = $newHost -or $newInstance -or $newSlot
    [pscustomobject]@{
        Detected      = $detected
        NewHostId     = $newHost
        NewInstanceId = $newInstance
        NewSlotLog    = $newSlot
        NewAftException = $newAftException
        Reasons       = $reasons
        AfterMostRecent = $mrA
        AfterSlotLast   = $After.LastCashless
        AfterAurumEvent = $After.LastAurumAftEvent
    }
}

function Invoke-AftAudit {
    param([string] $OutDir)
    if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
    & $convert -ComputerName $ComputerName -OutputDir $OutDir | Out-Null
}

function Format-Dollars([int64]$Cents) {
    return ([double]$Cents / 100.0).ToString('F2', [System.Globalization.CultureInfo]::InvariantCulture)
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
$stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$beforeDir = Join-Path $OutputRoot "$stamp-before"
$afterDir  = Join-Path $OutputRoot "$stamp-after"
if (-not (Test-Path -LiteralPath $OutputRoot)) { New-Item -ItemType Directory -Path $OutputRoot | Out-Null }

Write-Host ''
Write-Host '=== AFT transfer test (IGT tester + verify) ===' -ForegroundColor White
$expectedBucket = if ($Bonus) { 'total bonus/WAT award' } else { 'cashable' }
Write-Host "Cabinet: $ComputerName  |  Expected: $(Format-Dollars $ExpectedAmountCents) USD $expectedBucket  |  Asset: $AssetNumber"
Write-Host ''

if (-not (Test-Path -LiteralPath $StateRoot)) {
    throw "Cannot reach AFT state root: $StateRoot"
}

Write-Step 'Baseline snapshot + audit (before)...'
$before = Get-AftSnapshot
Invoke-AftAudit -OutDir $beforeDir
Write-Ok "Before report: $beforeDir"

if ($before.MostRecent) {
    $mr0 = $before.MostRecent
    Write-Host ''
    Write-Host '--- Current most-recent AFT (baseline) ---' -ForegroundColor White
    Write-Host "  Status:        $($mr0.TransferStatus)"
    Write-Host "  Obtained:      $(Format-Dollars $mr0.ObtainedCashable) USD"
    Write-Host "  hostRequestId: $($mr0.HostRequestId)"
    Write-Host "  instanceId:    $($mr0.InstanceId)"
}
if ($before.LastCashless) {
    Write-Host "  Last SlotLog:  `$$($before.LastCashless.AmountText) @ $($before.LastCashless.Timestamp)"
}
Write-Host "  Cashless events in today's SlotLog: $($before.CashlessCount)"
if ($before.CurrentSettings) {
    Write-Host "  AFT registration: $($before.CurrentSettings.RegistrationStatus)"
    Write-Host "  CashoutToHost enabled: $($before.CurrentSettings.CashoutToHostCurrentlyEnabled)"
}

if ($BaselineOnly) {
    Write-Ok 'BaselineOnly complete - run without -BaselineOnly when ready to test on COM11.'
    exit 0
}

$isRegistered = $before.CurrentSettings -and $before.CurrentSettings.RegistrationStatus -eq 'GAMING_MACHINE_REGISTERED'
if ($Bonus -and -not $isRegistered) {
    Write-Warn 'BONUS mode: cabinet is unregistered, which is expected - AFT bonus/WAT awards do not require registration.'
}
elseif (-not $isRegistered -and -not $AllowUnregistered) {
    Write-Fail "AFT is not registered/ready: $($before.CurrentSettings.RegistrationStatus)."
    Write-Warn 'No host->EGM AFT transfer is expected to persist until the cabinet is registered with the host/tester.'
    Write-Warn 'Fix tester/host registration first, or rerun with -Bonus (bonus award) or -AllowUnregistered.'
    exit 3
}

$dollars = Format-Dollars $ExpectedAmountCents
$transferLabel = if ($Bonus) { "Host -> EGM AFT BONUS / WAT transfer (no registration required)" }
                 else        { "Host -> EGM cashable AFT / WAT transfer" }
Write-Host ''
if ($DirectPost -or $AfterBaselineScript) {
    Write-Host '--- Automated direct post (after baseline) ---' -ForegroundColor Yellow
    Write-Host "  Action:   $transferLabel for `$$dollars"
    Write-Host "  Asset:    $AssetNumber (GCC_ST_20664_01)"
    if ($Bonus) { Write-Host '  Mode:     BONUS (accepts PARTIAL_TRANSFER_SUCCESSFUL; validates total obtained)' }
    Write-Host "  Timeout:  ${TimeoutSec}s"
    Write-Host ''
    if ($AfterBaselineScript) {
        Write-Step "Running after-baseline script: $AfterBaselineScript"
        & $AfterBaselineScript
        if ($LASTEXITCODE -ne 0) {
            throw "After-baseline script failed (exit $LASTEXITCODE)."
        }
        Write-Ok 'After-baseline script completed.'
    }
}
else {
    Write-Host '--- Perform transfer on IGT SAS tester NOW ---' -ForegroundColor Yellow
    Write-Host '  Port:     COM11 (MUX/SAS - not COM4 ticker)'
    Write-Host "  Action:   $transferLabel for `$$dollars"
    Write-Host "  Asset:    $AssetNumber (GCC_ST_20664_01)"
    if ($Bonus) { Write-Host '  Mode:     BONUS (accepts PARTIAL_TRANSFER_SUCCESSFUL; validates total obtained)' }
    if ($Bonus) { Write-Host '  Tester:   Use Host -> EGM bonus/award transfer, not EGM cashout / Cashout button.' }
    Write-Host "  Timeout:  ${TimeoutSec}s"
    Write-Host ''
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$detection = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $afterSnap = Get-AftSnapshot
    $detection = Test-NewTransfer -Before $before -After $afterSnap
    if ($detection.Detected) { break }
    if ($detection.NewAftException) { break }
    Write-Host ('  waiting... {0:N0}s left' -f (($deadline - (Get-Date)).TotalSeconds)) -ForegroundColor DarkGray
}

if ($detection -and $detection.NewAftException -and -not $detection.Detected) {
    Write-Fail 'AFT request was rejected before a transaction was persisted.'
    if ($detection.AfterAurumEvent) {
        Write-Warn "Aurum logged $($detection.AfterAurumEvent.Kind): code=$($detection.AfterAurumEvent.Code) @ $($detection.AfterAurumEvent.Timestamp)"
        Write-Warn $detection.AfterAurumEvent.Line
    }
    if ($Bonus) {
        Write-Warn 'The cabinet log shows CASHOUT BUTTON PRESSED before exception 66. That is the EGM->host cashout path, not the unregistered bonus award path.'
        Write-Warn 'On the IGT tester select Host -> EGM AFT BONUS / WAT award (AFT_BONUS), asset 777, amount 1000.00. Do not use Cashout / EGM-to-host.'
    }
    else {
        Write-Warn 'For standard cashable AFT, register the EGM first. For the unregistered lab path, rerun with -Bonus and send an AFT_BONUS award from the tester.'
    }
    exit 4
}

if (-not $detection -or -not $detection.Detected) {
    Write-Fail "No new AFT transfer detected within ${TimeoutSec}s."
    if ($detection -and $detection.NewAftException -and $detection.AfterAurumEvent) {
        Write-Warn "Aurum logged $($detection.AfterAurumEvent.Kind): code=$($detection.AfterAurumEvent.Code) @ $($detection.AfterAurumEvent.Timestamp)"
        Write-Warn $detection.AfterAurumEvent.Line
    }
    Write-Warn 'Did you trigger the transfer on COM11? Check tester link and EGM readiness.'
    exit 2
}

Write-Ok 'New transfer activity detected.'
if ($detection.NewHostId)     { Write-Ok "  New hostRequestId: $($detection.AfterMostRecent.HostRequestId)" }
if ($detection.NewInstanceId) { Write-Ok "  New instanceId:    $($detection.AfterMostRecent.InstanceId)" }
if ($detection.NewSlotLog -and $detection.AfterSlotLast) {
    Write-Ok "  SlotLog Cashless In: `$$($detection.AfterSlotLast.AmountText) @ $($detection.AfterSlotLast.Timestamp)"
}

Write-Step 'Post-transfer audit (after)...'
Invoke-AftAudit -OutDir $afterDir
Write-Ok "After report: $afterDir"

$mr = $detection.AfterMostRecent
$checks = @()

if ($mr) {
    if ($Bonus) {
        $okStatuses = @('FULL_TRANSFER_SUCCESSFUL', 'PARTIAL_TRANSFER_SUCCESSFUL')
        $checks += [pscustomobject]@{
            Check = 'AFT_STATUS'
            Pass  = ($okStatuses -contains $mr.TransferStatus)
            Detail = "Status=$($mr.TransferStatus) (bonus: full/partial accepted)"
        }
        $obtained = $mr.ObtainedTotal
        $checks += [pscustomobject]@{
            Check = 'AFT_AMOUNT'
            Pass  = ($obtained -eq $ExpectedAmountCents)
            Detail = "ObtainedTotal=$(Format-Dollars $obtained) (cash=$(Format-Dollars $mr.ObtainedCashable) restr=$(Format-Dollars $mr.ObtainedRestricted) nonRestr=$(Format-Dollars $mr.ObtainedNonRestricted)) expected=$(Format-Dollars $ExpectedAmountCents) creditType=$($mr.CreditType)"
        }
    }
    else {
        $checks += [pscustomobject]@{
            Check = 'AFT_STATUS'
            Pass  = ($mr.TransferStatus -eq 'FULL_TRANSFER_SUCCESSFUL')
            Detail = "Status=$($mr.TransferStatus)"
        }
        $checks += [pscustomobject]@{
            Check = 'AFT_AMOUNT'
            Pass  = ($mr.ObtainedCashable -eq $ExpectedAmountCents)
            Detail = "Obtained=$(Format-Dollars $mr.ObtainedCashable) expected=$(Format-Dollars $ExpectedAmountCents)"
        }
    }
    $checks += [pscustomobject]@{
        Check = 'AFT_ASSET'
        Pass  = ($mr.AssetNumber -eq $AssetNumber)
        Detail = "Asset=$($mr.AssetNumber) expected=$AssetNumber"
    }
    $checks += [pscustomobject]@{
        Check = 'AFT_COMMIT_EXCEPTION'
        Pass  = ($mr.CommitException -eq '0')
        Detail = "CommitException=$($mr.CommitException)"
    }
}

if ($detection.AfterSlotLast) {
    $checks += [pscustomobject]@{
        Check = 'SLOTLOG_CASHLESS_IN'
        Pass  = ($detection.AfterSlotLast.AmountCents -eq $ExpectedAmountCents)
        Detail = "SlotLog `$$($detection.AfterSlotLast.AmountText)"
    }
}

$checksPath = Join-Path $afterDir 'aft-transfer-test-checks.json'
$checks | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $checksPath -Encoding UTF8

Write-Host ''
Write-Host '--- Verification ---' -ForegroundColor White
foreach ($c in $checks) {
    if ($c.Pass) { Write-Ok "$($c.Check): PASS ($($c.Detail))" }
    else         { Write-Fail "$($c.Check): FAIL ($($c.Detail))" }
}

# Surface automated reconciliation from Convert-AftHistory
$autoChecks = Join-Path $afterDir 'aft-checks.json'
if (Test-Path -LiteralPath $autoChecks) {
    Write-Host ''
    Write-Host '--- Convert-AftHistory checks ---' -ForegroundColor White
    $ac = Get-Content -LiteralPath $autoChecks -Raw | ConvertFrom-Json
    foreach ($row in $ac) {
        $color = switch ($row.Status) { 'PASS' { 'Green' } 'WARN' { 'Yellow' } default { 'Red' } }
        Write-Host "  [$($row.Status)] $($row.Id): $($row.Message)" -ForegroundColor $color
    }
}

$allPass = ($checks.Count -gt 0) -and -not ($checks | Where-Object { -not $_.Pass })
Write-Host ''
if ($allPass) {
    Write-Ok 'OVERALL: PASS - tester transfer matches expected amount and cabinet evidence.'
    exit 0
}
Write-Fail 'OVERALL: FAIL - transfer detected but one or more checks did not match.'
exit 1
