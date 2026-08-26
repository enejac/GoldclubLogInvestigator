<#
.SYNOPSIS
   Forever loop: inject credits to cabinet, verify in remote logs, retry until success.
#>
[CmdletBinding()]
param(
    [string] $CabinetIP = '10.0.0.90',
    [int]    $InitialAmountCents = 100000,
    [int]    $DelayAfterInjectSec = 15,
    [int]    $DelayBetweenRoundsSec = 10
)

$ErrorActionPreference = 'Continue'

$repoRoot = 'C:\Users\Ezbogar\GoldclubLogInvestigator'
$injectScript = Join-Path $repoRoot 'lab\Send-TestAft1000.ps1'
$remoteLogPath = "\\$CabinetIP\c$\Goldclub\var\log\SlotLog"
$round = 0
$successCount = 0
$failCount = 0
$amountCents = $InitialAmountCents

Write-Host ''
Write-Host '============================================================='
Write-Host 'Forever Credit Injection Loop'
Write-Host '============================================================='
Write-Host "Cabinet     : $CabinetIP"
Write-Host "Amount      : $([int]($amountCents / 100)).00"
Write-Host "Delay       : ${DelayAfterInjectSec}s after inject, ${DelayBetweenRoundsSec}s between rounds"
Write-Host ''

while ($true) {
    $round++
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host ''
    Write-Host "=== ROUND $round | $timestamp | Amount: $([int]($amountCents / 100)).00 ===" -ForegroundColor Cyan

    Write-Host "[*] Injecting credit transfer..." -ForegroundColor Yellow

    $injectOutput = & $injectScript -Send -IP $CabinetIP -Amount $amountCents 2>&1
    $injectOutput | ForEach-Object { Write-Host $_ }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] Inject returned exit code $LASTEXITCODE" -ForegroundColor Red
        $failCount++
    } else {
        Write-Host "[+] Inject command completed" -ForegroundColor Green
    }

    Write-Host "[*] Waiting ${DelayAfterInjectSec}s for credit to process..." -ForegroundColor Yellow
    Start-Sleep -Seconds $DelayAfterInjectSec

    Write-Host "[*] Checking remote SlotLog for Cashless In evidence..." -ForegroundColor Yellow
    $today = Get-Date -Format 'yyyy-MM-dd'
    $todayLog = Join-Path $remoteLogPath "$today.log"

    if (-not (Test-Path $todayLog)) {
        Write-Host "[!] Remote log not found: $todayLog" -ForegroundColor Red
        $failCount++
        Start-Sleep -Seconds $DelayBetweenRoundsSec
        continue
    }

    $searchStart = (Get-Date).AddMinutes(-3)
    $found = $false
    $matchedLines = @()

    try {
        $cashlessLines = Select-String -Path $todayLog -Pattern "Cashless In:" -ErrorAction SilentlyContinue
        foreach ($line in $cashlessLines) {
            $lineText = $line.Line
            if ($lineText -match '^(\S+)') {
                $tsStr = $matches[1]
                try {
                    $ts = [datetime]::Parse($tsStr.Replace('+01:00','').Replace('+00:00',''))
                    if ($ts -ge $searchStart) {
                        $matchedLines += $lineText
                        $found = $true
                    }
                }
                catch {
                    $matchedLines += $lineText
                    $found = $true
                }
            }
        }
    }
    catch {
        Write-Host "[!] Error reading remote log: $_" -ForegroundColor Red
    }

    if ($found) {
        $successCount++
        Write-Host "[+] SUCCESS! Credit landed in SlotLog!" -ForegroundColor Green
        foreach ($ml in $matchedLines) {
            Write-Host "    $ml" -ForegroundColor Green
        }
    }
    else {
        $failCount++
        Write-Host "[!] NOT FOUND - No Cashless In in recent logs" -ForegroundColor Red
        try {
            $lastLines = Get-Content $todayLog -Tail 3 -ErrorAction SilentlyContinue
            foreach ($ll in $lastLines) {
                Write-Host "    $ll" -ForegroundColor DarkGray
            }
        }
        catch { }
    }

    if (-not $found) {
        $amountCents = if ($amountCents -eq 100000) { 50000 } elseif ($amountCents -eq 50000) { 200000 } else { 100000 }
        Write-Host "[*] Next round amount: $([int]($amountCents / 100)).00" -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host "=== SUMMARY | Rounds: $round | Success: $successCount | Failed: $failCount ===" -ForegroundColor White
    Write-Host "[*] Waiting ${DelayBetweenRoundsSec}s before next round..." -ForegroundColor Yellow
    Start-Sleep -Seconds $DelayBetweenRoundsSec
}