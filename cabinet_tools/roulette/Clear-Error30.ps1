# Clear Ruleta ERROR 30 leftovers: leftover USB/wrong-WIBU XML AND the
# persistent trial tokens that official RAM clear does not wipe.
# Never overwrites live licence XML or licence.dll. No UAC RunAs.
#
# Default: wipe XML + persistent + restart Ruleta (expect ERROR 99, new id).
# -Auto: do not kill Ruleta. If the LLAVE keypad is up (TRIAL DISPLAYED),
# type D:\usb_scripts\roulette\error30.password (or RULETA_ERROR30_PASSWORD).
# After a successful accept a dated 10.2 beta re-locks in ~12s
# (Trial expired / STOPDIALOG with no buttons) unless persistent was
# cleared and the clock is before the build lock. -Auto will not loop that.
[CmdletBinding()]
param(
    [switch] $Auto
)

$ErrorActionPreference = 'Continue'
$log = 'D:\ConfigScanner\clear-error30.log'
$ruletaDir = 'C:\goldclub\ruleta'
$ruletaExe = Join-Path $ruletaDir 'Ruleta.exe'

function L([string]$m) {
    $line = '[{0}] {1}' -f (Get-Date -Format o), $m
    Write-Host $line
    try {
        [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    } catch {}
}

function Test-GcAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $prin = New-Object Security.Principal.WindowsPrincipal($id)
        return $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Remove-TrialPersistent {
    $bak = 'D:\ConfigScanner\backup-persistent-trial-clear30'
    $persist = Join-Path $ruletaDir 'persistent'
    $varDir = Join-Path $ruletaDir 'var'
    if (-not (Test-Path -LiteralPath $bak)) {
        New-Item -ItemType Directory -Path $bak -Force | Out-Null
    }
    foreach ($name in @('RouletteActivate.dat', 'RouletteStop.flag', 'HeapDataFinanceStamps.dat')) {
        $p = Join-Path $persist $name
        if (Test-Path -LiteralPath $p) {
            Copy-Item -LiteralPath $p -Destination (Join-Path $bak $name) -Force
            Remove-Item -LiteralPath $p -Force
            L ('backed up + deleted {0}' -f $p)
        }
    }
    foreach ($name in @('Password.dat', 'HeapDataDateTime.dat')) {
        $p = Join-Path $varDir $name
        if (Test-Path -LiteralPath $p) {
            Copy-Item -LiteralPath $p -Destination (Join-Path $bak $name) -Force
            Remove-Item -LiteralPath $p -Force
            L ('backed up + deleted {0}' -f $p)
        }
    }
}

function Remove-Error30LeftoverXml {
    $leftover = @(
        'C:\goldclub\config\licences\6051106A90E561F9F08CDEEBB5C3F6E2.xml',
        'C:\goldclub\bios\licences\6051106A90E561F9F08CDEEBB5C3F6E2.xml',
        'D:\GoldClub\Licenses\6051106A90E561F9F08CDEEBB5C3F6E2.xml',
        'D:\GoldClub\Licences\6051106A90E561F9F08CDEEBB5C3F6E2.xml'
    )
    foreach ($p in $leftover) {
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
            L ('removed leftover {0}' -f $p)
        }
    }
}

function Get-RuletaRouletteLogPath {
    $dir = 'C:\goldclub\var\log\ruleta Roulette'
    $today = Join-Path $dir ((Get-Date).ToString('yyyy-MM-dd') + '.log')
    if (Test-Path -LiteralPath $today) { return $today }
    if (-not (Test-Path -LiteralPath $dir)) { return $null }
    $hit = Get-ChildItem -LiteralPath $dir -Filter '*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

function Get-Error30State {
    $path = Get-RuletaRouletteLogPath
    if (-not $path) { return 'none' }
    $tail = ''
    try {
        $tail = [IO.File]::ReadAllText($path)
        if ($tail.Length -gt 80000) { $tail = $tail.Substring($tail.Length - 80000) }
    } catch { return 'none' }
    $disp = $tail.LastIndexOf('type="DISPLAYED"')
    $ok = $tail.LastIndexOf('type="SUCCEEDED"')
    $expired = $tail.LastIndexOf('Trial expired')
    $err30 = $tail.LastIndexOf('ERR: number on screen: 30')
    $stop = $tail.LastIndexOf('No buttons enabled in stop dialog')
    if ($disp -lt 0 -and $ok -lt 0 -and $expired -lt 0) { return 'none' }
    $lockAt = [Math]::Max($expired, [Math]::Max($err30, $stop))
    # Newest event wins. A fresh DISPLAYED after restart must not inherit
    # yesterday's Trial expired / STOPDIALOG from the same daily log.
    if ($disp -ge 0 -and $disp -gt $ok -and $disp -gt $lockAt) { return 'displayed' }
    if ($ok -ge 0 -and $ok -ge $disp -and $ok -ge $lockAt) { return 'accepted' }
    if ($lockAt -gt $ok -and $lockAt -gt $disp) { return 're-locked' }
    return 'none'
}

function Get-Error30Password {
    if ($env:RULETA_ERROR30_PASSWORD) { return [string]$env:RULETA_ERROR30_PASSWORD }
    foreach ($p in @(
        'D:\usb_scripts\roulette\error30.password',
        'C:\goldclub\var\state\error30.password'
    )) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $t = [IO.File]::ReadAllText($p).Trim()
            if ($t) { return $t }
        } catch {}
    }
    return ''
}

function Send-Error30Password {
    $pw = Get-Error30Password
    if (-not $pw) {
        L 'no error30.password / RULETA_ERROR30_PASSWORD - type the LLAVE password by hand'
        return $false
    }
    $proc = @(Get-Process -Name Ruleta, ruleta -ErrorAction SilentlyContinue) | Select-Object -First 1
    if (-not $proc) {
        L 'no Ruleta process to type into'
        return $false
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $sig = @'
using System;
using System.Runtime.InteropServices;
public static class GcFg {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
}
'@
        Add-Type -TypeDefinition $sig -ErrorAction SilentlyContinue
        if ($proc.MainWindowHandle -ne [IntPtr]::Zero) {
            [GcFg]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
            [GcFg]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        }
        Start-Sleep -Milliseconds 400
        $escaped = ($pw -replace '([+\^%~{}\[\]])', '{$1}')
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        L 'typed LLAVE password into Ruleta'
        return $true
    } catch {
        L ('SendKeys fail: {0}' -f $_.Exception.Message)
        return $false
    }
}

function Invoke-Error30AutoClear {
    $st = Get-Error30State
    L ('error30 state={0}' -f $st)
    if ($st -eq 'accepted') {
        L 'licence / password already accepted; ERROR 30 is not up'
        return 0
    }
    if ($st -eq 're-locked') {
        L 'ERROR 30 re-locked after accept (Trial expired / STOPDIALOG no buttons)'
        L 'that is the 10.2.0.0 beta date lock - leftover XML will not clear it'
        L 'need licensed 10.2.0.827; password dismiss lasts ~12s then no-button dialog'
        return 5
    }
    if ($st -ne 'displayed') {
        L 'ERROR 30 keypad not showing - nothing to type'
        return 0
    }
    if (-not (Send-Error30Password)) { return 6 }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $st = Get-Error30State
        L ('error30 state={0}' -f $st)
        if ($st -eq 'accepted') { return 0 }
        if ($st -eq 're-locked') { return 5 }
    }
    return 6
}

L ('clear-error30 start whoami={0} IsAdmin={1} Auto={2}' -f (whoami), (Test-GcAdmin), [bool]$Auto)
L 'live licence XML / licence.dll will not be overwritten'
Remove-Error30LeftoverXml
if (-not $Auto) {
    Remove-TrialPersistent
} else {
    L 'Auto: leave persistent trial files (keypad type only)'
}

if ($Auto) {
    $code = Invoke-Error30AutoClear
    L ('auto done exit={0}' -f $code)
    exit $code
}

if (-not (Test-Path -LiteralPath $ruletaExe)) {
    L ("ERROR: missing {0}" -f $ruletaExe)
    exit 2
}

foreach ($im in @('Ruleta.exe', 'godot.exe', 'Godot_v4.exe', 'HIH.exe', 'game-start.exe', 'Start-Game.exe')) {
    & cmd.exe /c ("taskkill /F /T /IM {0}" -f $im) | Out-Null
    L ("taskkill /IM {0} exit={1}" -f $im, $LASTEXITCODE)
}

$names = @('Ruleta', 'godot', 'Godot_v4', 'HIH', 'game-start', 'Start-Game')
foreach ($n in $names) {
    $procs = @(Get-Process -Name $n -ErrorAction SilentlyContinue)
    foreach ($p in $procs) {
        L ("kill {0} pid={1}" -f $p.Name, $p.Id)
        try {
            Stop-Process -Id $p.Id -Force -ErrorAction Stop
            L ("  stopped pid={0}" -f $p.Id)
        } catch {
            L ("  Stop-Process failed: {0}" -f $_.Exception.Message)
            & cmd.exe /c ("taskkill /F /T /PID {0}" -f $p.Id) | Out-Null
            L ("  taskkill exit={0}" -f $LASTEXITCODE)
        }
    }
}

Start-Sleep -Seconds 2
$left = @(Get-Process -Name Ruleta, ruleta, godot -ErrorAction SilentlyContinue)
if ($left.Count -gt 0) {
    L ("WARN still alive: {0}" -f (($left | ForEach-Object { '{0}:{1}' -f $_.Name, $_.Id }) -join ', '))
} else {
    L 'Ruleta/godot gone'
}

L ("start {0}" -f $ruletaExe)
$p = Start-Process -FilePath $ruletaExe -WorkingDirectory $ruletaDir -PassThru
if ($null -eq $p) {
    L 'ERROR: Start-Process returned nothing'
    exit 3
}
L ("started pid={0}" -f $p.Id)
Start-Sleep -Seconds 8
$up = @(Get-Process -Name Ruleta, ruleta -ErrorAction SilentlyContinue)
L ("ruleta processes: {0}" -f (($up | ForEach-Object { '{0}:{1}' -f $_.Name, $_.Id }) -join ', '))
if ($up.Count -eq 0) {
    L 'ERROR: Ruleta did not stay up'
    exit 4
}
$code = Invoke-Error30AutoClear
L ('clear-error30 done autoExit={0}' -f $code)
exit 0
