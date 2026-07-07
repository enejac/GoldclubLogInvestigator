$ErrorActionPreference = 'Continue'
$SlotLog = '\\10.0.0.90\c$\Goldclub\var\log\SlotLog\2026-06-17.log'
$lastLines = (Get-Content $SlotLog).Count
Write-Host "Monitor starting at line $lastLines"
for ($i = 1; $i -le 120; $i++) {
    Start-Sleep -Seconds 5
    $cur = (Get-Content $SlotLog).Count
    $new = $cur - $lastLines
    if ($new -gt 0) {
        $lastLines = $cur
        $tail = Get-Content $SlotLog -Tail $new
        $m = $tail | Select-String 'Coin In:|Cashless In:|Bill In:|Handpay|Voucher|credit state'
        if ($m) {
            Write-Host "EVENT at poll $i"
            $m | ForEach-Object { Write-Host "  $_" }
        }
    }
    if ($i % 10 -eq 0) { Write-Host "Poll $i/120" }
}
