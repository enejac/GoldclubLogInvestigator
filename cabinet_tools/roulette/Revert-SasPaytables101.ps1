# Let GoldClub re-sign DeviceManager from the remapped 10.1 combo.
# Moves live 10.2-signed DeviceManagerData aside (does NOT rewrite the MAC).
# Does not touch licences or serialport layout/locations.
# When Service Control Manager is denied (WinRM Session 0), re-runs itself
# as an interactive goldclub task on the console.
param([switch]$Force, [switch]$AlreadyInteractive)
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
$log = 'C:\goldclub\var\log\revert-sas-101.log'
$exitMarker = 'C:\goldclub\var\log\revert-sas-101.exit'
$hold = 'C:\goldclub\var\state\ruleta-compat-hold.json'
$lic = 'C:\goldclub\config\Licences\37A55022DCBEF351AE27471D181B1EF5.xml'
$dll = 'C:\goldclub\ruleta\licence.dll'

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try { [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, $utf8) } catch {}
}

function Write-Hold {
    $dir = Split-Path -Parent $hold
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $body = @{
        holdStart = $true
        liveRuleta = '10.1'
        signedSasPaytableIds = @('paytable_elite_double_zero', 'paytable_premium_double_zero')
        reason = 'Ruleta 10.1 cannot load signed SAS paytable paytable_elite_double_zero, paytable_premium_double_zero (PutRemoteThemeAndCombo bad conversion). Writable combo/Aurum names were remapped; DeviceManager MAC is not rewritten.'
        bypass = 'Automatic bypass: swap Ruleta to 10.2 (same WIBU) so the exe matches SAS, or have GoldClub re-sign DeviceManager with paytable_double_zero. Do not unsigned-patch the MAC.'
    } | ConvertTo-Json
    [IO.File]::WriteAllText($hold, $body, $utf8)
    L 'wrote ruleta-compat-hold'
}

function Sha1([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA1).Hash
}

function Copy-And-Read([string]$path) {
    $tmp = Join-Path $env:TEMP ('dm-' + [guid]::NewGuid().ToString('N'))
    Copy-Item -LiteralPath $path -Destination $tmp -Force
    try { return [IO.File]::ReadAllBytes($tmp) } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Has102([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    try {
        $raw = Copy-And-Read $path
        $t = [Text.Encoding]::GetEncoding(1252).GetString($raw)
        return [bool]($t.Contains('paytable_elite_double_zero') -or $t.Contains('paytable_premium_double_zero'))
    } catch {
        L ('Has102 read fail {0}: {1} (treat as still 10.2)' -f $path, $_.Exception.Message)
        return $true
    }
}

function FileOk([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    return ((Get-Item -LiteralPath $path).Length -gt 2000)
}

function Test-CanControlServices {
    try {
        $null = Get-Service -Name 'GoldClub.Aurum.Services' -ErrorAction Stop
        return $true
    } catch { return $false }
}

function Write-ExitMarker([int]$code) {
    try { [IO.File]::WriteAllText($exitMarker, [string]$code, $utf8) } catch {}
}

$sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
if (-not $AlreadyInteractive -and ($sessionId -eq 0 -or -not (Test-CanControlServices))) {
    L ('Session={0} SCM={1} - relaunching as interactive goldclub task' -f $sessionId, (Test-CanControlServices))
    $self = $MyInvocation.MyCommand.Path
    $taskName = 'GoldClub-RevertSas101'
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $exitMarker) { Remove-Item -LiteralPath $exitMarker -Force }
    $arg = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -AlreadyInteractive' -f $self
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
    $user = 'goldclub'
    try {
        $wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
        if ($wl.DefaultUserName) { $user = [string]$wl.DefaultUserName }
    } catch {}
    $prin = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $prin -Settings $set -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $deadline = (Get-Date).AddSeconds(100)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $exitMarker) {
            $code = [int](Get-Content -LiteralPath $exitMarker -ErrorAction SilentlyContinue | Select-Object -First 1)
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
            L ('interactive task exit={0}' -f $code)
            exit $code
        }
        Start-Sleep -Seconds 2
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    L 'interactive task timed out'
    Write-Hold
    Write-ExitMarker 3
    exit 3
}

$dirs = @(
    'C:\goldclub\ruleta\var\SASControler1',
    'C:\goldclub\ruleta\var\gm2au',
    'C:\goldclub\ruleta\var\RouletteAurumHost',
    'C:\goldclub\services\aurum\var\MeterHost'
)
$names = @('DeviceManagerData.xml_1', 'DeviceManagerData.xml_2')
$sas1 = 'C:\goldclub\ruleta\var\SASControler1\DeviceManagerData.xml_1'

L ('start whoami={0} interactive={1}' -f (whoami), [bool]$AlreadyInteractive)
$licBefore = Sha1 $lic
$dllBefore = Sha1 $dll
L ('licence {0} dll {1}' -f $licBefore, $dllBefore)

foreach ($im in @('Ruleta.exe', 'godot.exe', 'Godot_v4.exe', 'HIH.exe', 'game-start.exe')) {
    & cmd.exe /c ('taskkill /F /T /IM {0} 1>nul 2>nul' -f $im) | Out-Null
}

$svcStops = @('GoldClub.Aurum.Services', 'GoldClub Serial Communication Gateway SAS')
foreach ($n in $svcStops) {
    $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
    if (-not $svc) { L ('no service {0}' -f $n); continue }
    try {
        Stop-Service -InputObject $svc -Force -ErrorAction Stop
        L ('stopped {0}' -f $svc.Name)
    } catch {
        L ('stop fail {0}: {1}' -f $n, $_.Exception.Message)
    }
}
Start-Sleep -Seconds 3

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$holdDir = Join-Path 'C:\goldclub\var\state' ('sas-101-quarantine-' + $stamp)
New-Item -ItemType Directory -Path $holdDir -Force | Out-Null
$moved = 0
$movedSas1 = $false
foreach ($dir in $dirs) {
    foreach ($name in $names) {
        $p = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $p)) { continue }
        attrib.exe -R $p 2>$null | Out-Null
        $dest = Join-Path $holdDir (($dir.Split('\')[-1]) + '_' + $name)
        try {
            Move-Item -LiteralPath $p -Destination $dest -Force -ErrorAction Stop
            if (-not ((Test-Path -LiteralPath $dest) -and ((Get-Item -LiteralPath $dest).Length -gt 500))) {
                throw 'move produced empty dest'
            }
            L ('quarantine {0} ({1} bytes)' -f $dest, (Get-Item -LiteralPath $dest).Length)
            $moved++
            if ($p -eq $sas1) { $movedSas1 = $true }
        } catch {
            L ('move fail {0}: {1}' -f $p, $_.Exception.Message)
        }
    }
}
L ('quarantined {0} file(s) sas1={1} dir={2}' -f $moved, $movedSas1, $holdDir)

if (-not $movedSas1) {
    L 'ABORT: could not move SAS DeviceManager xml_1 (service still holding the file)'
    Write-Hold
    Write-ExitMarker 2
    exit 2
}

foreach ($n in @($svcStops[1], $svcStops[0])) {
    $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
    if (-not $svc) { continue }
    try {
        Start-Service -InputObject $svc -ErrorAction Stop
        L ('started {0}' -f $svc.Name)
    } catch {
        L ('start fail {0}: {1}' -f $n, $_.Exception.Message)
    }
}

function Restore-Quarantine {
    foreach ($dir in $dirs) {
        foreach ($name in $names) {
            $src = Join-Path $holdDir (($dir.Split('\')[-1]) + '_' + $name)
            $dst = Join-Path $dir $name
            if (-not (Test-Path -LiteralPath $src)) { continue }
            if ((Get-Item -LiteralPath $src).Length -lt 500) { continue }
            try {
                Copy-Item -LiteralPath $src -Destination $dst -Force
                L ('restored quarantine {0}' -f $dst)
            } catch {
                L ('restore fail {0}: {1}' -f $dst, $_.Exception.Message)
            }
        }
    }
}

$deadline = (Get-Date).AddSeconds(50)
$ok = $false
while ((Get-Date) -lt $deadline) {
    if ((FileOk $sas1) -and -not (Has102 $sas1)) { $ok = $true; break }
    if ((FileOk $sas1) -and (Has102 $sas1)) { break }
    Start-Sleep -Seconds 2
}

if (-not $ok) {
    L ('regen failed: exists={0} len={1} has102={2}' -f (Test-Path -LiteralPath $sas1), ($(if (Test-Path -LiteralPath $sas1) { (Get-Item -LiteralPath $sas1).Length } else { 0 })), (Has102 $sas1))
    foreach ($n in $svcStops) {
        $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
        if (-not $svc) { continue }
        try {
            Stop-Service -InputObject $svc -Force -ErrorAction Stop
            L ('stopped for restore {0}' -f $svc.Name)
        } catch {
            L ('stop-for-restore fail {0}: {1}' -f $n, $_.Exception.Message)
        }
    }
    Start-Sleep -Seconds 2
    Restore-Quarantine
    foreach ($n in @($svcStops[1], $svcStops[0])) {
        $svc = Get-Service | Where-Object { $_.Name -eq $n -or $_.DisplayName -eq $n } | Select-Object -First 1
        if (-not $svc) { continue }
        try {
            Start-Service -InputObject $svc -ErrorAction Stop
            L ('started after restore {0}' -f $svc.Name)
        } catch {
            L ('start-after-restore fail {0}: {1}' -f $n, $_.Exception.Message)
        }
    }
    Write-Hold
    if ((Sha1 $lic) -ne $licBefore -or (Sha1 $dll) -ne $dllBefore) {
        L 'FAIL licence changed'
        Write-ExitMarker 4
        exit 4
    }
    Write-ExitMarker 2
    exit 2
}

L ('regen ok sas1={0} bytes no 10.2 paytable names' -f (Get-Item -LiteralPath $sas1).Length)
if (Test-Path -LiteralPath $hold) { Remove-Item -LiteralPath $hold -Force }
L 'cleared ruleta-compat-hold'
if ((Sha1 $lic) -ne $licBefore -or (Sha1 $dll) -ne $dllBefore) {
    L 'FAIL licence changed'
    Write-Hold
    Write-ExitMarker 4
    exit 4
}
Write-ExitMarker 0
L 'done OK - run Run-FullStack from the console'
exit 0
