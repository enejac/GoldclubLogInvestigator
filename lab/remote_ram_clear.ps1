# Soft RAM-clear on a lab cabinet (newest START_ONEHAND / LogDaemonRamClear).
#
# Uploads cabinet_tools\slot\START_ONEHAND.ps1 (late stamp after cabinet ONLINE
# for SAS soft meters 0x7A), then launches it on the console session via WinRM.
# Optional -UseUsbBat falls back to D:\.START_ONEHAND.bat on the cabinet USB.
param(
    [string] $TargetIP = '10.0.0.90',
    [string] $LocalPs1 = '',
    [switch] $UseUsbBat,
    [string] $UsbBatPath = 'D:\.START_ONEHAND.bat'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\LabWinRm.ps1"

function Resolve-StartOneHandPs1 {
    param([string] $Explicit)
    if ($Explicit -and (Test-Path -LiteralPath $Explicit)) {
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $candidates = @(
        (Join-Path $PSScriptRoot 'scripts\slot\START_ONEHAND.ps1'),
        (Join-Path $PSScriptRoot '..\..\GoldclubLogInvestigator\cabinet_tools\slot\START_ONEHAND.ps1'),
        'C:\Users\Ezbogar\GoldclubLogInvestigator\cabinet_tools\slot\START_ONEHAND.ps1'
    )
    $dir = $PSScriptRoot
    for ($i = 0; $i -lt 6 -and $dir; $i++) {
        $candidates += (Join-Path $dir 'cabinet_tools\slot\START_ONEHAND.ps1')
        $candidates += (Join-Path $dir 'GoldclubLogInvestigator\cabinet_tools\slot\START_ONEHAND.ps1')
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    foreach ($c in $candidates) {
        try {
            if ($c -and (Test-Path -LiteralPath $c)) {
                return (Resolve-Path -LiteralPath $c).Path
            }
        } catch { }
    }
    return $null
}

Write-Host "=== Remote soft RAM Clear (START_ONEHAND / LogDaemonRamClear) ===" -ForegroundColor Cyan
Write-Host "[*] Target: $TargetIP" -ForegroundColor Cyan

if (-not (Test-Connection -ComputerName $TargetIP -Count 1 -Quiet)) {
    Write-Host "[-] Cabinet offline" -ForegroundColor Red
    exit 1
}
try {
    Ensure-LabWinRm -ComputerName $TargetIP | Out-Null
} catch {
    Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($UseUsbBat) {
    Write-Host "[*] Launching USB bat on console: $UsbBatPath" -ForegroundColor Cyan
    try {
        $out = Start-LabInteractiveRemote -ComputerName $TargetIP -FilePath 'cmd.exe' `
            -Arguments "/c `"$UsbBatPath`"" -AppWindowStyle Hidden
        Write-Host "[+] $out" -ForegroundColor Green
        Write-Host "[*] Fire-and-forget — watch cabinet for services + OneHand + late LogDaemonRamClear." -ForegroundColor DarkCyan
        exit 0
    } catch {
        Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

$local = Resolve-StartOneHandPs1 -Explicit $LocalPs1
if (-not $local) {
    Write-Host "[-] Could not find START_ONEHAND.ps1 (pass -LocalPs1 or keep scripts\slot\ next to this file)." -ForegroundColor Red
    Write-Host "    Tip: -UseUsbBat to launch $UsbBatPath instead." -ForegroundColor DarkYellow
    exit 1
}

# Sanity: newest soft path must late-stamp after cabinet ONLINE.
$probe = Get-Content -LiteralPath $local -Raw -ErrorAction Stop
foreach ($needle in @('Wait-CabinetDeviceOnline', 'LogDaemonRamClear', 'Stop-LogDaemonUnclean')) {
    if ($probe -notmatch [regex]::Escape($needle)) {
        Write-Host "[-] Local script looks stale (missing $needle): $local" -ForegroundColor Red
        exit 1
    }
}

$remotePs1 = 'C:\Windows\Temp\START_ONEHAND.ps1'
$unc = "\\$TargetIP\c`$\Windows\Temp\START_ONEHAND.ps1"
Write-Host "[*] Uploading newest START_ONEHAND.ps1 -> $unc" -ForegroundColor Cyan
Write-Host "    from $local" -ForegroundColor DarkGray
try {
    Copy-Item -LiteralPath $local -Destination $unc -Force
} catch {
    Write-Host "[-] Upload failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    Try: cmdkey /add:$TargetIP /user:GOLD-CLUB\test /pass:test" -ForegroundColor DarkYellow
    exit 1
}

Write-Host "[*] Launching on console session (hidden window; OneHand still starts normally)..." -ForegroundColor Cyan
try {
    $out = Start-LabInteractiveRemote -ComputerName $TargetIP `
        -FilePath 'powershell.exe' `
        -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$remotePs1`"" `
        -AppWindowStyle Hidden
    Write-Host "[+] $out" -ForegroundColor Green
    Write-Host "[*] Soft RAM clear started. Expect late LogDaemonRamClear after cabinet ONLINE (SAS 0x7A)." -ForegroundColor DarkCyan
} catch {
    Write-Host "[-] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}