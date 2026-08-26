#Requires -RunAsAdministrator
# Quick repair: local test/test + LanmanServer + SMB/WinRM firewall + remote-admin token.
# Does NOT start TeamViewer / Total Commander / elevated shell.
$ErrorActionPreference = 'Continue'
function W([string]$m) { Write-Host $m; try { Add-Content -LiteralPath (Join-Path $PSScriptRoot '91-EnableShareAndWinRM.log') -Value ("[{0}] FIX: {1}" -f (Get-Date -Format o), $m) -Encoding ASCII } catch {} }

W '=== FIX-SHARE-NOW start ==='
cmd /c 'net user test test /add' 2>&1 | ForEach-Object { W "  $_" }
cmd /c 'net user test test' 2>&1 | ForEach-Object { W "  $_" }
cmd /c 'net user test /active:yes' 2>&1 | ForEach-Object { W "  $_" }
cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { W "  $_" }

try {
    Set-Service LanmanServer -StartupType Automatic -ErrorAction SilentlyContinue
    Start-Service LanmanServer -ErrorAction SilentlyContinue
    W ("LanmanServer: {0}" -f (Get-Service LanmanServer).Status)
} catch { W ("WARN LanmanServer: {0}" -f $_.Exception.Message) }

$uac = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
New-Item -Path $uac -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -LiteralPath $uac -Name 'LocalAccountTokenFilterPolicy' -Value 1 -Type DWord -Force
Set-ItemProperty -LiteralPath $uac -Name 'ConsentPromptBehaviorAdmin' -Value 0 -Type DWord -Force
W 'LocalAccountTokenFilterPolicy=1 ConsentPromptBehaviorAdmin=0'

foreach ($g in @('File And Printer Sharing', 'Windows Remote Management')) {
    Get-NetFirewallRule -DisplayGroup $g -ErrorAction SilentlyContinue |
        Set-NetFirewallRule -Profile Any -Enabled True -ErrorAction SilentlyContinue
    Enable-NetFirewallRule -DisplayGroup $g -ErrorAction SilentlyContinue | Out-Null
    W ("Firewall: {0} => Any/Enabled" -f $g)
}

Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.NetworkCategory -ne 'Private') {
        Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue
        W ("Network if{0} -> Private" -f $_.InterfaceIndex)
    }
}

# Re-assert shares (same as onstart)
if (Test-Path 'C:\goldclub') {
    cmd /c 'net share slot /delete /y' 2>&1 | Out-Null
    cmd /c 'net share slot=C:\goldclub /grant:everyone,FULL' 2>&1 | ForEach-Object { W "  $_" }
}
foreach ($letter in @('D','E','F','G','H')) {
    $root = "${letter}:\"
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $tv = Join-Path $root 'TeamViewerPortable\TeamViewer.exe'
    $tc = Join-Path $root 'totalcmd\TOTALCMD64.EXE'
    if (-not ((Test-Path $tv) -or (Test-Path $tc))) { continue }
    cmd /c "net share USB_Remote /delete /y" 2>&1 | Out-Null
    cmd /c "net share USB_Remote=${letter}:\ /grant:everyone,FULL" 2>&1 | ForEach-Object { W "  $_" }
    cmd /c "net share USB /delete /y" 2>&1 | Out-Null
    cmd /c "net share USB=${letter}:\ /grant:everyone,FULL" 2>&1 | ForEach-Object { W "  $_" }
    $cfg = "${letter}:\ConfigScanner"
    if (Test-Path $cfg) {
        cmd /c 'net share ConfigScanner /delete /y' 2>&1 | Out-Null
        cmd /c "net share ConfigScanner=$cfg /grant:everyone,FULL" 2>&1 | ForEach-Object { W "  $_" }
    }
    break
}

try {
    Enable-PSRemoting -Force -SkipNetworkProfileCheck
    Set-Service WinRM -StartupType Automatic
    Start-Service WinRM
    W 'WinRM: enabled'
} catch { W ("WARN WinRM: {0}" -f $_.Exception.Message) }

W '=== FIX-SHARE-NOW done ==='
W 'From lab PC:  net use \\THIS_IP\slot /user:THIS_IP\test test'
