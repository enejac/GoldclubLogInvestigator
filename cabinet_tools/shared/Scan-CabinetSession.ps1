# Scan GoldClub / Windows session / privilege state (no admin required).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\Scan-CabinetSession.ps1
# Or:    .\Scan-CabinetSession.bat
$ErrorActionPreference = 'Continue'
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$LogPath = Join-Path $PSScriptRoot 'cabinet-session-scan.log'
$lines = New-Object System.Collections.Generic.List[string]

function Out-Scan([string]$Message) {
    $line = $Message
    [void]$lines.Add($line)
    Write-Host $line
}

function Section([string]$Title) {
    Out-Scan ''
    Out-Scan ("=== {0} ===" -f $Title)
}

Out-Scan ("[{0}] Cabinet session scan start" -f (Get-Date -Format o))
Out-Scan ("Computer={0} User={1}\{2}" -f $env:COMPUTERNAME, $env:USERDOMAIN, $env:USERNAME)
Out-Scan ("Log={0}" -f $LogPath)

Section '1) Windows privilege (this shell)'
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = [Security.Principal.WindowsPrincipal]::new($id)
    $isAdmin = $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Out-Scan ("IsInRole(Administrator) = {0}" -f $isAdmin)
    Out-Scan ("Identity = {0}" -f $id.Name)
    Out-Scan ("AuthType = {0}" -f $id.AuthenticationType)
    Out-Scan ("Groups (sample) = {0}" -f (($id.Groups | ForEach-Object { try { $_.Translate([Security.Principal.NTAccount]).Value } catch { $_.Value } } | Select-Object -First 12) -join '; '))
} catch {
    Out-Scan ("privilege check failed: {0}" -f $_.Exception.Message)
}
$netSession = cmd /c 'net session >nul 2>&1 & echo %ERRORLEVEL%'
Out-Scan ("net session ERRORLEVEL (0=admin) = {0}" -f ($netSession | Select-Object -Last 1))

Section '2) Logged-on sessions (quser / query session)'
try {
    $quser = cmd /c 'quser 2>&1'
    if ($quser) { $quser | ForEach-Object { Out-Scan $_ } } else { Out-Scan '(quser empty)' }
} catch { Out-Scan ("quser failed: {0}" -f $_.Exception.Message) }
try {
    $qs = cmd /c 'query session 2>&1'
    if ($qs) { $qs | ForEach-Object { Out-Scan $_ } }
} catch { Out-Scan ("query session failed: {0}" -f $_.Exception.Message) }

Section '3) GoldClub / roulette processes (owner + session)'
$exactNames = @(
    'HIH.exe','hih.exe',
    'Ruleta.exe','ruleta.exe','OneHand.exe','game-start.exe',
    'Bootstrap.exe','BiOS2.exe','Setup.exe','CommCtrl.exe',
    'LogDaemon.exe','bootstrap.exe'
)
$procs = @()
try {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $n = $_.Name
        if (-not $n) { return $false }
        foreach ($want in $exactNames) {
            if ($n -ieq $want) { return $true }
        }
        if ($n -like 'goldclub*' -or $n -like '*Aurum*' -or $n -like '*SASControl*' -or $n -like '*CommCtrl*') {
            return $true
        }
        $path = [string]$_.ExecutablePath
        if ($path -and ($path -match '(?i)[\\/](goldclub|ruleta)[\\/]')) { return $true }
        return $false
    }
} catch {
    Out-Scan ("Get-CimInstance Win32_Process failed: {0}" -f $_.Exception.Message)
}

if (-not $procs -or $procs.Count -eq 0) {
    Out-Scan 'No matching GoldClub/roulette process names found.'
} else {
    Out-Scan ('{0,-8} {1,-22} {2,-8} {3,-28} {4}' -f 'PID','Name','Sess','Owner','ExecutablePath')
    foreach ($pr in ($procs | Sort-Object Name, ProcessId)) {
        $owner = '?'
        try {
            $o = Invoke-CimMethod -InputObject $pr -MethodName GetOwner -ErrorAction SilentlyContinue
            if ($o -and $o.ReturnValue -eq 0) {
                $owner = ('{0}\{1}' -f $o.Domain, $o.User)
            } elseif ($o) {
                $owner = ('GetOwner rc={0}' -f $o.ReturnValue)
            }
        } catch {
            $owner = 'n/a'
        }
        $path = if ($pr.ExecutablePath) { $pr.ExecutablePath } else { '' }
        Out-Scan ('{0,-8} {1,-22} {2,-8} {3,-28} {4}' -f $pr.ProcessId, $pr.Name, $pr.SessionId, $owner, $path)
    }
}

Section '4) Same session as you?'
$myPid = $PID
$mySess = $null
try {
    $me = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $myPid) -ErrorAction SilentlyContinue
    $mySess = $me.SessionId
    Out-Scan ("This PowerShell PID={0} SessionId={1}" -f $myPid, $mySess)
} catch {
    Out-Scan ("Could not read own SessionId: {0}" -f $_.Exception.Message)
}
if ($procs -and $mySess -ne $null) {
    $other = @($procs | Where-Object { $_.SessionId -ne $mySess })
    $same = @($procs | Where-Object { $_.SessionId -eq $mySess })
    Out-Scan ("Processes in YOUR session ({0}): {1}" -f $mySess, $same.Count)
    Out-Scan ("Processes in OTHER sessions: {0}" -f $other.Count)
    if ($other.Count -gt 0) {
        Out-Scan 'YES - GoldClub/roulette appears in another session (or Session 0 / services).'
        $other | Group-Object SessionId | ForEach-Object {
            Out-Scan ("  Session {0}: {1}" -f $_.Name, (($_.Group | ForEach-Object Name) -join ', '))
        }
    } else {
        Out-Scan 'No GoldClub process found outside your session (may still run as different user in same session).'
    }
}

Section '5) GoldClub services'
try {
    Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'goldclub*' -or $_.DisplayName -like '*GoldClub*' -or $_.DisplayName -like '*Aurum*' } |
        Sort-Object Name |
        ForEach-Object {
            Out-Scan ('{0,-40} {1,-12} {2}' -f $_.Name, $_.Status, $_.StartType)
        }
} catch {
    Out-Scan ("Get-Service failed: {0}" -f $_.Exception.Message)
}
try {
    Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'goldclub*' -or $_.DisplayName -like '*GoldClub*' } |
        ForEach-Object {
            Out-Scan ('Service {0} StartName={1} State={2} Path={3}' -f $_.Name, $_.StartName, $_.State, $_.PathName)
        }
} catch {}

Section '6) SMB share / test user (read-only checks)'
try {
    $shares = cmd /c 'net share 2>&1'
    $shares | ForEach-Object { Out-Scan $_ }
} catch {
    Out-Scan ("net share failed: {0}" -f $_.Exception.Message)
}
$slot = cmd /c 'net share slot 2>&1'
Out-Scan '--- net share slot ---'
$slot | ForEach-Object { Out-Scan $_ }
$testUser = cmd /c 'net user test 2>&1'
Out-Scan '--- net user test ---'
$testUser | Select-Object -First 20 | ForEach-Object { Out-Scan $_ }

Section '7) Write filter (UWF/EWF) if visible'
try {
    $uwf = Get-Command uwfmgr.exe -ErrorAction SilentlyContinue
    if ($uwf) {
        cmd /c 'uwfmgr.exe get-config 2>&1' | Select-Object -First 40 | ForEach-Object { Out-Scan $_ }
    } else {
        Out-Scan 'uwfmgr.exe not in PATH'
    }
} catch {
    Out-Scan ("uwf check: {0}" -f $_.Exception.Message)
}
try {
    $ewf = Get-Service ewfsvc -ErrorAction SilentlyContinue
    if ($ewf) { Out-Scan ("ewfsvc Status={0}" -f $ewf.Status) }
} catch {}

Section '8) Common GoldClub paths'
foreach ($p in @(
    'C:\goldclub','C:\Goldclub','D:\goldclub','D:\Goldclub','G:\goldclub','G:\Goldclub',
    'D:\ruleta','C:\goldclub\bin\Setup.exe','C:\Goldclub\bin\Setup.exe',
    'C:\goldclub\bin\RunManteinanceTasks.1.ps1','C:\Goldclub\platform\user\init\onlogon.ps1',
    'G:\','D:\'
)) {
    $exists = Test-Path -LiteralPath $p
    Out-Scan ("{0,-55} exists={1}" -f $p, $exists)
}

Section '9) Verdict'
$admin = $false
try {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch {}
if (-not $admin) {
    Out-Scan 'You are NOT a Windows Administrator in this shell.'
    Out-Scan '_share.bat will keep failing with System error 5 until an elevated process creates the share.'
} else {
    Out-Scan 'This shell IS elevated — _share.bat should work if G: exists and UWF is off.'
}
Out-Scan 'GoldClub superadmin (JT25/Setup.exe) is APPLICATION admin, not Windows admin.'
Out-Scan 'If Ruleta/Bootstrap show SessionId=0 or Owner=NT AUTHORITY\SYSTEM, they run outside your desktop session.'
Out-Scan 'If Owner is another DOMAIN\user with different SessionId, the game is on another interactive session.'

Out-Scan ''
Out-Scan ("[{0}] DONE" -f (Get-Date -Format o))
try {
    [IO.File]::WriteAllText($LogPath, ($lines -join [Environment]::NewLine) + [Environment]::NewLine, $Utf8)
    Write-Host ""
    Write-Host ("Wrote {0}" -f $LogPath)
} catch {
    Write-Host ("WARN: could not write log: {0}" -f $_.Exception.Message)
}