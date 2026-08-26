# One-shot repair for GRT330106 / 10.0.0.111.
# Combo GR0072 mapping, MeterHost DATA TAMPERED, TeamViewer portable, WinRM ACL, relaunch stack.
# Does not touch licences or serialport layout/locations.
#Requires -RunAsAdministrator
param([switch]$Force)
$ErrorActionPreference = 'Continue'
$utf8 = [Text.UTF8Encoding]::new($false)
$log = 'C:\goldclub\var\log\fix-111-now.log'
$done = 'C:\goldclub\bin\fix-111-now.done'

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    foreach ($p in @($log, 'D:\FIX-111-NOW.log')) {
        try {
            $d = Split-Path -Parent $p
            if ($d -and -not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
            [IO.File]::AppendAllText($p, $line + [Environment]::NewLine, $utf8)
        } catch {}
    }
}

L ('start whoami={0} computer={1}' -f (whoami), $env:COMPUTERNAME)
if ((-not $Force) -and (Test-Path -LiteralPath $done)) {
    L 'done flag present - skip (use -Force to re-run)'
    exit 0
}

cmd /c 'net localgroup "Remote Management Users" test /add' 2>&1 | ForEach-Object { L "winrm group: $_" }
cmd /c 'net localgroup "Remote Management Users" goldclub /add' 2>&1 | ForEach-Object { L "winrm group goldclub: $_" }
try {
    Enable-PSRemoting -Force -SkipNetworkProfileCheck | Out-Null
    L 'Enable-PSRemoting ok'
} catch {
    L ('WARN Enable-PSRemoting: {0}' -f $_.Exception.Message)
}

try {
    Unregister-ScheduledTask -TaskName 'GoldClub-TeamViewer-USB' -Confirm:$false -ErrorAction SilentlyContinue
    L 'removed GoldClub-TeamViewer-USB (restore taskkills TV)'
} catch {}

$tv = 'D:\TeamViewerPortable\TeamViewer.exe'
$tvPrinter = 'D:\TeamViewerPortable\Printer'
$tvPrinterOff = 'D:\TeamViewerPortable\Printer.disabled'
if (Test-Path -LiteralPath $tvPrinter) {
    try {
        if (Test-Path -LiteralPath $tvPrinterOff) {
            Remove-Item -LiteralPath $tvPrinterOff -Recurse -Force -ErrorAction SilentlyContinue
        }
        Rename-Item -LiteralPath $tvPrinter -NewName 'Printer.disabled'
        L 'TeamViewer Printer moved to Printer.disabled (skip XPS driver install)'
    } catch {
        L ('WARN TV Printer rename: {0}' -f $_.Exception.Message)
    }
}
$tvVpn = 'D:\TeamViewerPortable\x64\TeamViewerVPN.inf'
$tvVpnOff = 'D:\TeamViewerPortable\x64\TeamViewerVPN.inf.disabled'
if (Test-Path -LiteralPath $tvVpn) {
    try {
        if (Test-Path -LiteralPath $tvVpnOff) {
            Remove-Item -LiteralPath $tvVpnOff -Force -ErrorAction SilentlyContinue
        }
        Rename-Item -LiteralPath $tvVpn -NewName 'TeamViewerVPN.inf.disabled'
        L 'TeamViewer VPN inf renamed (no TAP device on EGM)'
    } catch {
        L ('WARN TV VPN inf rename: {0}' -f $_.Exception.Message)
    }
}
try {
    $qc = sc.exe qc spooler 2>&1 | Out-String
    if ($qc -match 'DISABLED') {
        cmd /c 'sc.exe config spooler start= demand' | Out-Null
        L 'Print Spooler start= demand (TV StartService; not Automatic, not started)'
    }
} catch {
    L ('WARN spooler demand-start: {0}' -f $_.Exception.Message)
}
$tvLaunch = 'D:\START-TV-NOW.cmd'
if (-not (Test-Path -LiteralPath $tvLaunch)) { $tvLaunch = $tv }
$tvUp = @(Get-Process -Name TeamViewer -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
$tvHooks = @(Get-Process -Name tv_w32, tv_x64 -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
if ($tvUp.Count -gt 0 -and $tvHooks.Count -eq 0) {
    L ('TeamViewer zombie pid={0} (no tv_w32) - restart' -f ($tvUp.Id -join ','))
    $tvUp | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $tvUp = @()
}
if ($tvUp.Count -gt 0) {
    L ('TeamViewer already on console pid={0}' -f ($tvUp.Id -join ','))
} elseif (Test-Path -LiteralPath $tvLaunch) {
    try {
        $logon = 'goldclub'
        try {
            $wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
            if ($wl.DefaultUserName) { $logon = [string]$wl.DefaultUserName }
        } catch {}
        $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c start /min "" cmd.exe /c ""{0}""' -f $tvLaunch)
        $prin = New-ScheduledTaskPrincipal -UserId $logon -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName 'GoldClub-TeamViewer-Start' -Action $action -Principal $prin -Force | Out-Null
        Start-ScheduledTask -TaskName 'GoldClub-TeamViewer-Start'
        L ('started TeamViewer task as {0} -> {1}' -f $logon, $tvLaunch)
    } catch {
        L ('WARN TV task: {0}' -f $_.Exception.Message)
        try { Start-Process -FilePath $tvLaunch | Out-Null; L 'Start-Process START-TV-NOW' } catch { L ('WARN TV start: {0}' -f $_.Exception.Message) }
    }
} else {
    L 'WARN TeamViewerPortable missing'
}

$combo = 'C:\goldclub\ruleta\var\SASControler1\GCC_RT_330106_01_combo.dat'
$comboBak = $combo + '.bak-gr0072'
if (Test-Path -LiteralPath $comboBak) {
    Copy-Item -LiteralPath $comboBak -Destination $combo -Force
    L 'combo restored bak-gr0072 (GR0072 -> paytable_double_zero)'
} else {
    L 'WARN combo bak-gr0072 missing'
}

$kill = 'D:\usb_scripts\roulette\Kill-All.ps1'
$run = 'D:\usb_scripts\roulette\Run-FullStack.ps1'
if (Test-Path -LiteralPath $kill) {
    L 'Kill-All'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $kill -AlreadyElevated
    L ('Kill-All exit={0}' -f $LASTEXITCODE)
} else {
    L 'Kill-All missing - taskkill Ruleta/godot'
    foreach ($im in @('Ruleta.exe', 'godot.exe', 'Godot_v4.exe', 'HIH.exe', 'game-start.exe')) {
        & cmd.exe /c ('taskkill /F /T /IM {0} 1>nul 2>nul' -f $im) | Out-Null
    }
}

$fix = 'C:\goldclub\bin\Fix-CrashLoop.ps1'
if (Test-Path -LiteralPath $fix) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $fix -FilesOnly -Force
    L ('Fix-CrashLoop FilesOnly exit={0}' -f $LASTEXITCODE)
}

$mh = 'C:\goldclub\services\aurum\var\MeterHost'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$hold = Join-Path $mh ('lab-tampered-' + $stamp)
New-Item -ItemType Directory -Path $hold -Force | Out-Null
foreach ($n in @('DeviceManagerData.xml_1', 'DeviceManagerData.xml_2')) {
    $p = Join-Path $mh $n
    if (Test-Path -LiteralPath $p) {
        attrib.exe -R $p | Out-Null
        try {
            Move-Item -LiteralPath $p -Destination (Join-Path $hold $n) -Force
            L ('moved tampered {0}' -f $n)
        } catch {
            L ('WARN move {0}: {1}' -f $n, $_.Exception.Message)
        }
    }
}

Start-Sleep -Seconds 2
try {
    Start-Service -Name 'GoldClub.Aurum.Services' -ErrorAction Stop
    L 'started GoldClub.Aurum.Services'
} catch {
    L ('WARN Aurum start: {0}' -f $_.Exception.Message)
}

if (Test-Path -LiteralPath $run) {
    L 'Run-FullStack'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $run -AlreadyElevated
    L ('Run-FullStack exit={0}' -f $LASTEXITCODE)
} else {
    L 'WARN Run-FullStack missing'
}

try { [IO.File]::WriteAllText($done, (Get-Date -Format o), $utf8) } catch {}
Remove-Item -LiteralPath 'C:\goldclub\bin\fix-crashloop.done' -Force -ErrorAction SilentlyContinue
L 'done'
exit 0
