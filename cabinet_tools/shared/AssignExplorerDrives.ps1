#Requires -RunAsAdministrator
$Log = 'C:\Users\Ezbogar\GoldclubLogInvestigator\assign-letters.log'
$Img = 'C:\WIN_SYSTEMS\Images\Alegro_10.2_128GB_BIWIN.mrimg'
$Reflect = 'C:\Program Files\Macrium\Reflect\Reflect.exe'
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Log([string]$m) {
    $line = "[$(Get-Date -Format o)] $m"
    Write-Host $line
    [IO.File]::AppendAllText($Log, $line + [Environment]::NewLine, $utf8)
}

[IO.File]::WriteAllText($Log, '', $utf8)
Log '=== Assign E G P (elevated PS) ==='

$biwin = Get-Disk | Where-Object { $_.FriendlyName -match 'BIWIN' } | Select-Object -First 1
if (-not $biwin) { Log 'FAIL: BIWIN not found'; exit 1 }
$n = [int]$biwin.Number
Log "BIWIN disk $n"

$dp = @(
    "select disk $n", 'list partition',
    'select partition 1', 'assign letter=E',
    'select partition 2', 'assign letter=G',
    'list volume', 'exit'
)
$tmp = Join-Path $env:TEMP "assign-egp-$([guid]::NewGuid().ToString('N')).txt"
[IO.File]::WriteAllLines($tmp, $dp, $utf8)
cmd /c "diskpart /s `"$tmp`"" 2>&1 | ForEach-Object { Log "  $_" }
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

foreach ($letter in 'E','G') {
    if (Test-Path "${letter}:\") {
        Log "--- ${letter}: $((Get-Volume -DriveLetter $letter -EA SilentlyContinue).FileSystemLabel) ---"
        Get-ChildItem "${letter}:\" -EA SilentlyContinue | ForEach-Object { Log "  $($_.Name) $([math]::Round($_.Length/1GB,2)) GB" }
    } else { Log "FAIL: ${letter}: missing" }
}

if (-not (Test-Path 'P:\goldclub.vhd')) {
    Log 'Mounting 10.2 on P:'
    Start-Process -FilePath $Reflect -ArgumentList @("`"$Img`"", '-b', '-auto', '-drives', '*,P') -Wait -WindowStyle Hidden
    Start-Sleep 4
}
if (Test-Path 'P:\') {
    Log '--- P: ---'
    Get-ChildItem 'P:\' | ForEach-Object { Log "  $($_.Name) $([math]::Round($_.Length/1GB,2)) GB" }
} else { Log 'FAIL: P: missing' }

# Verify G: 10.2 payload if copy completed
if (Test-Path 'G:\goldclub.vhd') {
    $g = (Get-Item 'G:\goldclub.vhd').Length
    $p = if (Test-Path 'P:\goldclub.vhd') { (Get-Item 'P:\goldclub.vhd').Length } else { 0 }
    Log "G: goldclub.vhd $([math]::Round($g/1GB,2)) GB | P: $([math]::Round($p/1GB,2)) GB | match=$($g -eq $p -or $p -eq 0)"
}

Log '=== DONE ==='
exit 0
