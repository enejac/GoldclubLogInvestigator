<#
.SYNOPSIS
    Align serialport locations.json with layout.json (Leds on COM7, not legacy COM5 label).

.DESCRIPTION
    Serial Port Config UI labels IO ranges from locations.json. Stale map had:
      "io:238 - 23F" : "COM5"
    while layout.json / ruleta.json assign that UART to Leds as Windows COM7, and
    Online MUX-SAS to USB COM5. That makes the UI look like "Leds on COM5".

    This script sets io:238-23F -> COM7 to match layout, backs up the old file,
    and prints a short before/after + cross-check.

.EXAMPLE
    .\Fix-SerialPortLocations.ps1
#>
[CmdletBinding()]
param(
    [string] $GoldclubRoot = 'C:\goldclub',
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'

$serialDir = Join-Path $GoldclubRoot 'config\etc\application\system\hardware\serialport'
$locPath = Join-Path $serialDir 'locations.json'
$layoutPath = Join-Path $serialDir 'layout.json'

if (-not (Test-Path -LiteralPath $locPath)) {
    throw "Missing $locPath"
}

Write-Host 'Fix serialport locations.json (io:238-23F COM5 -> COM7 for Leds)' -ForegroundColor Cyan
Write-Host "  $locPath"

$before = Get-Content -LiteralPath $locPath -Raw
Write-Host ''
Write-Host '--- before ---' -ForegroundColor DarkGray
Write-Host $before

if ($before -match '"io:238 - 23F"\s*:\s*"COM7"') {
    Write-Host 'Already patched (io:238-23F -> COM7). Nothing to do.' -ForegroundColor Green
    exit 0
}

$after = @'
{
	"io:3F8 - 3FF" : "COM1",
	"io:2F8 - 2FF" : "COM2",
	"io:3E8 - 3EF" : "COM3",
	"io:2E8 - 2EF" : "COM4",
	"io:238 - 23F" : "COM7",
	"io:228 - 22F" : "COM6"
}
'@

if ($WhatIf) {
    Write-Host ''
    Write-Host '--- WhatIf after ---' -ForegroundColor Yellow
    Write-Host $after.Trim()
    exit 0
}

$bak = $locPath + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
Copy-Item -LiteralPath $locPath -Destination $bak -Force
# UTF8 no BOM preferred for GoldClub JSON; .NET UTF8Encoding(false)
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($locPath, $after.Trim() + "`r`n", $utf8)

Write-Host ''
Write-Host "Backup: $bak" -ForegroundColor DarkGray
Write-Host '--- after ---' -ForegroundColor Green
Write-Host (Get-Content -LiteralPath $locPath -Raw)

if (Test-Path -LiteralPath $layoutPath) {
    Write-Host '--- layout.json cross-check (expect Leds on io:238 / ComNumber 7) ---' -ForegroundColor DarkGray
    $layout = Get-Content -LiteralPath $layoutPath -Raw | ConvertFrom-Json
    $io238 = $layout.'io:238 - 23F'
    if ($io238) {
        Write-Host ("  io:238-23F => ComNumber={0} Name={1}" -f $io238.ComNumber, $io238.Name)
    }
    $mux = $layout.PSObject.Properties | Where-Object { $_.Value.Name -eq 'Online MUX-SAS' } | Select-Object -First 1
    if ($mux) {
        Write-Host ("  Online MUX-SAS => ComNumber={0} ({1})" -f $mux.Value.ComNumber, $mux.Name)
    }
}

Write-Host ''
Write-Host 'Done. Re-open Serial Port Config (or restart HW Subsystem) to refresh the UI.' -ForegroundColor Green
Write-Host 'Online MUX-SAS stays on USB COM5; Leds label is now COM7 for io:238-23F.' -ForegroundColor DarkGray
exit 0
