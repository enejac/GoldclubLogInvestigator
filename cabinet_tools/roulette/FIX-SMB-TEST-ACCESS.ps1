#Requires -RunAsAdministrator
# Run ON the cabinet (GoldClub Admin Shell). Fixes:
#   - C$ Access denied for test/test  -> LocalAccountTokenFilterPolicy=1 + local/domain test in Administrators
#   - slot reparse 4392               -> share a live GoldClub path (G:\ or resolved target), not a dead junction
# Does not touch licences or serialport layout/locations.

$ErrorActionPreference = 'Continue'
$logDir = 'D:\usb_scripts\roulette'
if (-not (Test-Path -LiteralPath $logDir)) { $logDir = 'C:\goldclub\var\log' }
$logFile = Join-Path $logDir 'FIX-SMB-TEST-ACCESS.log'

function W([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try {
        $d = Split-Path -Parent $logFile
        if ($d -and -not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
        Add-Content -LiteralPath $logFile -Value $line -Encoding ASCII
    } catch {}
}

function Test-DirReadable([string]$Path) {
    if (-not $Path) { return $false }
    try {
        $null = Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop | Select-Object -First 1
        return $true
    } catch {
        try {
            $null = [System.IO.Directory]::GetFileSystemEntries($Path)
            return $true
        } catch {
            return $false
        }
    }
}

function Test-LooksLikeGoldclub([string]$Path) {
    if (-not $Path) { return $false }
    foreach ($rel in @('ruleta', 'bin\Ruleta.exe', 'platform', 'var\log')) {
        if (Test-Path -LiteralPath (Join-Path $Path $rel)) { return $true }
    }
    return $false
}

W '=== FIX-SMB-TEST-ACCESS start ==='
W ("whoami={0} computer={1}" -f (whoami), $env:COMPUTERNAME)

cmd /c 'net user test test /add' 2>&1 | ForEach-Object { W "  net user add: $_" }
cmd /c 'net user test test' 2>&1 | ForEach-Object { W "  net user setpass: $_" }
cmd /c 'net user test /active:yes' 2>&1 | ForEach-Object { W "  net user active: $_" }
cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { W "  local test admin: $_" }
# This cabinet is GRT330106 (workgroup). GOLD-CLUB\test does not exist here.
cmd /c 'net localgroup administrators GOLD-CLUB\test /add' 2>&1 | ForEach-Object { W "  domain test admin: $_" }
W 'Lab PC must use 10.0.0.111\test or GRT330106\test - not GOLD-CLUB\test'

$uac = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
New-Item -Path $uac -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -LiteralPath $uac -Name 'LocalAccountTokenFilterPolicy' -Value 1 -Type DWord -Force
Set-ItemProperty -LiteralPath $uac -Name 'ConsentPromptBehaviorAdmin' -Value 0 -Type DWord -Force
$tf = (Get-ItemProperty -LiteralPath $uac -Name 'LocalAccountTokenFilterPolicy' -ErrorAction SilentlyContinue).LocalAccountTokenFilterPolicy
W ("LocalAccountTokenFilterPolicy={0} (need 1 for C$ as local test)" -f $tf)

try {
    Set-Service LanmanServer -StartupType Automatic -ErrorAction SilentlyContinue
    Start-Service LanmanServer -ErrorAction SilentlyContinue
    W ("LanmanServer: {0}" -f (Get-Service LanmanServer).Status)
} catch {
    W ("WARN LanmanServer: {0}" -f $_.Exception.Message)
}

$slotPath = $null
$candidates = @('G:\', 'G:\goldclub', 'C:\goldclub')
foreach ($c in $candidates) {
    W ("probe {0} readable={1} goldclubish={2}" -f $c, (Test-DirReadable $c), (Test-LooksLikeGoldclub $c))
    if ((Test-DirReadable $c) -and (Test-LooksLikeGoldclub $c)) {
        $slotPath = $c
        break
    }
}
if (-not $slotPath) {
    foreach ($c in $candidates) {
        if (Test-DirReadable $c) { $slotPath = $c; break }
    }
}

if ($slotPath) {
    W ("Sharing live path {0} as slot (skip broken C:\goldclub junction)" -f $slotPath)
    cmd /c 'net share slot /delete /y' 2>&1 | ForEach-Object { W "  $_" }
    if ($slotPath -match '^[A-Za-z]:\\$') {
        cmd /c "net share slot=$slotPath /grant:everyone,FULL" 2>&1 | ForEach-Object { W "  $_" }
    } else {
        cmd /c "net share slot=$slotPath /grant:everyone,FULL" 2>&1 | ForEach-Object { W "  $_" }
    }
} else {
    W 'WARN: no readable GoldClub path - slot share left as-is'
}

W '=== FIX-SMB-TEST-ACCESS done ==='
W 'On lab PC: close the C$ prompt, then run D:\FIX-OPEN-USB.cmd (or \\10.0.0.111\USB_Remote\FIX-OPEN-USB.cmd)'
W 'Open \\10.0.0.111\USB_Remote and \\10.0.0.111\slot - do not open C$'
