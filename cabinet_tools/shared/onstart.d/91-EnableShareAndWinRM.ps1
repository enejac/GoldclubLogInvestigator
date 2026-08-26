# onstart.d task (SYSTEM / session 0) - slot+USB share, WinRM, RustDesk LAN
# firewall ports, and start TeamViewerPortable\TeamViewer.exe + Total Commander
# from USB in the *logged-on user* session.
#
# Why GUI apps failed before:
#   onstart runs as SYSTEM (session 0). Start-Process there leaves orphans that
#   vanish at auto-logon. TeamViewer also refuses SYSTEM ("GUI running in system
#   account") and exits unless started as the console user (goldclub).
#
# Fix: register AtLogOn scheduled tasks as the auto-logon user for TeamViewer /
# Total Commander. Startup .cmd is only for the elevated PowerShell trigger -
# do NOT also launch TV/TC from Startup.
# Never Start-Now restore_tv_login_roulette.cmd: it taskkill /F TeamViewer.exe
# then xcopy+reg import. Use D:\TeamViewerPortable\TeamViewer.exe instead.
#
# Drop into (either or both):
#   C:\platform\system\init\onstart\91-EnableShareAndWinRM.ps1
#   C:\goldclub\platform\system\init\onstart.d\91-EnableShareAndWinRM.ps1
#
# GoldClub may invoke onstart and onstart.d (same files on .111) and will
# re-enter this script when the stack restarts. Do not delete/recreate shares
# or Start-ScheduledTask on every entry: that disconnects SMB, taskkills
# TeamViewer (restore_tv_login), and spawns extra Admin Shell windows.
#
# No USB stick: stock roulette boot - no elevated PowerShell, Startup launcher,
# TeamViewer, or Total Commander (and remove leftovers from a prior USB boot).
# Silent share / WinRM / firewall still run. Status log is next to this script.

$ErrorActionPreference = 'Continue'

# Prefer the folder that holds this script (onstart / onstart.d). Fallback if
# invoked in a way that leaves $PSScriptRoot empty.
$scriptDir = $PSScriptRoot
if (-not $scriptDir) {
    try { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path } catch { $scriptDir = $null }
}
if (-not $scriptDir) { $scriptDir = 'C:\goldclub\var\log' }

$logFile = Join-Path $scriptDir '91-EnableShareAndWinRM.log'
$statusFile = Join-Path $scriptDir '91-EnableShareAndWinRM-status.txt'
# Mirror under GoldClub var\log when that tree exists (operators already look there).
$legacyLogDir = 'C:\goldclub\var\log'
$legacyLogFile = Join-Path $legacyLogDir 'onstart-share-winrm.log'

# Per-step results for the status file: name -> @{ Ok = bool; Detail = string }
$script:StepResults = [ordered]@{}

function Set-StepResult {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$Ok,
        [string]$Detail = ''
    )
    $script:StepResults[$Name] = @{ Ok = $Ok; Detail = $Detail }
}

function Write-StatusSummary {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(('=== 91-EnableShareAndWinRM status {0} ===' -f (Get-Date -Format o)))
    $lines.Add(('computer={0} whoami={1}' -f $env:COMPUTERNAME, (whoami)))
    $lines.Add(('scriptDir={0}' -f $scriptDir))
    $okN = 0; $failN = 0; $skipN = 0
    foreach ($key in $script:StepResults.Keys) {
        $r = $script:StepResults[$key]
        $tag = if ($r.Ok) { 'OK' } else { 'FAIL' }
        if ($r.Detail -match '(?i)^SKIP') { $tag = 'SKIP'; $skipN++ }
        elseif ($r.Ok) { $okN++ } else { $failN++ }
        $detail = if ($r.Detail) { ' - {0}' -f $r.Detail } else { '' }
        $lines.Add(('{0,-28} {1}{2}' -f $key, $tag, $detail))
    }
    $lines.Add(('--- totals: OK={0} FAIL={1} SKIP={2} ---' -f $okN, $failN, $skipN))
    $text = ($lines -join "`r`n") + "`r`n"
    try {
        [System.IO.File]::WriteAllText($statusFile, $text, [System.Text.UTF8Encoding]::new($false))
        Write-TaskLog ("status summary: {0}" -f $statusFile)
    } catch {
        Write-TaskLog ("WARN status summary write failed: {0}" -f $_.Exception.Message)
    }
}

function Write-TaskLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Write-Host $line
    foreach ($path in @($logFile, $legacyLogFile)) {
        try {
            $dir = Split-Path -Parent $path
            if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            }
            Add-Content -LiteralPath $path -Value $line -Encoding ASCII
        } catch {}
    }
}

$script:ShareMutex = $null
$script:RepeatSkipHeavy = $false

function Enter-91Mutex {
    try {
        $script:ShareMutex = New-Object System.Threading.Mutex($false, 'Global\GoldClub91EnableShareAndWinRM')
        if (-not $script:ShareMutex.WaitOne(0)) {
            Write-TaskLog 'already running - skip this instance (stops share/TV/admin-shell loops)'
            try { $script:ShareMutex.Dispose() } catch {}
            $script:ShareMutex = $null
            return $false
        }
        return $true
    } catch {
        Write-TaskLog ("WARN mutex: {0} - continuing" -f $_.Exception.Message)
        return $true
    }
}

function Exit-91Mutex {
    if ($null -eq $script:ShareMutex) { return }
    try { [void]$script:ShareMutex.ReleaseMutex() } catch {}
    try { $script:ShareMutex.Dispose() } catch {}
    $script:ShareMutex = $null
}

function Write-91SuccessStamp {
    try {
        $dir = 'C:\goldclub\var\state\onstart-share-winrm'
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Set-Content -LiteralPath (Join-Path $dir 'last-success.txt') -Value (Get-Date -Format o) -Encoding ASCII
    } catch {}
}

function Test-SharePathEqual {
    param([string]$Left, [string]$Right)
    if ([string]::IsNullOrWhiteSpace($Left) -or [string]::IsNullOrWhiteSpace($Right)) { return $false }
    $a = ($Left.TrimEnd('\') + '\').ToUpperInvariant()
    $b = ($Right.TrimEnd('\') + '\').ToUpperInvariant()
    return ($a -eq $b)
}

function Get-NetSharePath {
    param([Parameter(Mandatory = $true)][string]$Name)
    try {
        $s = Get-SmbShare -Name $Name -ErrorAction Stop
        if ($s) { return [string]$s.Path }
    } catch {}
    $out = @(cmd /c "net share $Name" 2>&1 | ForEach-Object { "$_" })
    foreach ($line in $out) {
        if ($line -match '(?i)^\s*Path\s+(.+?)\s*$') {
            return $Matches[1].Trim()
        }
    }
    return $null
}

function Ensure-NetShare {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $got = Get-NetSharePath -Name $Name
    if ($got -and (Test-SharePathEqual -Left $got -Right $Path)) {
        Write-TaskLog ("share {0} already {1} - leave it" -f $Name, $got)
        return $true
    }
    if ($got) {
        Write-TaskLog ("share {0} was {1} - recreate as {2}" -f $Name, $got, $Path)
        cmd /c "net share $Name /delete /y" 2>&1 | ForEach-Object { Write-TaskLog "  $_" }
    } else {
        Write-TaskLog ("Sharing {0} as {1}" -f $Path, $Name)
    }
    cmd /c "net share $Name=$Path /grant:everyone,FULL" 2>&1 | ForEach-Object { Write-TaskLog "  $_" }
    return ($LASTEXITCODE -eq 0)
}

function Test-GoldClubAdminShellRunning {
    try {
        $hit = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.SessionId -gt 0 -and $_.CommandLine -and
                $_.CommandLine -match 'Open-AdminShell\.ps1'
            })
        if ($hit.Count -gt 0) { return $true }
    } catch {}
    $byTitle = @(Get-Process powershell, pwsh -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -gt 0 -and $_.MainWindowTitle -eq 'GoldClub Admin Shell' })
    return ($byTitle.Count -gt 0)
}

function Get-AutoLogonUser {
    try {
        $wl = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
        if ($wl.DefaultUserName) { return [string]$wl.DefaultUserName }
    } catch {}
    return 'goldclub'
}

function Ensure-LabShareAccess {
    # Make \\cabinet\slot (and C$) reachable from the lab PC.
    # Common failures: stale local "test" password (script used to set it only on create),
    # Public profile blocking FPS/WinRM, LanmanServer stopped, UAC remote-token filter.
    param([switch]$Light)
    if ($Light) {
        Write-TaskLog 'Ensure lab share access (light): Server + token filter only (skip password/firewall rescan)'
    } else {
        Write-TaskLog 'Ensure lab share access (test user + Server + firewall + token filter)'
        # Always force local lab account password (EWF / prior image may leave another password)
        cmd /c 'net user test test /add' 2>&1 | ForEach-Object { Write-TaskLog "  net user add: $_" }
        cmd /c 'net user test test' 2>&1 | ForEach-Object { Write-TaskLog "  net user setpass: $_" }
        cmd /c 'net user test /active:yes' 2>&1 | ForEach-Object { Write-TaskLog "  net user active: $_" }
        cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { Write-TaskLog "  net localgroup: $_" }
        cmd /c 'net localgroup "Remote Management Users" test /add' 2>&1 | ForEach-Object { Write-TaskLog "  winrm group: $_" }
        cmd /c 'net localgroup "Remote Management Users" goldclub /add' 2>&1 | ForEach-Object { Write-TaskLog "  winrm group goldclub: $_" }
    }

    try {
        $svc = Get-Service -Name 'LanmanServer' -ErrorAction Stop
        if ($svc.StartType -eq 'Disabled') {
            Set-Service -Name 'LanmanServer' -StartupType Automatic
            Write-TaskLog 'LanmanServer: StartupType -> Automatic'
        }
        if ($svc.Status -ne 'Running') {
            Start-Service -Name 'LanmanServer' -ErrorAction Stop
            Write-TaskLog 'LanmanServer: started'
        } else {
            Write-TaskLog 'LanmanServer: Running'
        }
    } catch {
        Write-TaskLog ("WARN LanmanServer: {0}" -f $_.Exception.Message)
    }

    # Remote local-admin (IP\test) gets a filtered token unless this is 1 -> C$/admin shares fail
    try {
        $uacKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
        New-Item -Path $uacKey -Force -ErrorAction SilentlyContinue | Out-Null
        Set-ItemProperty -LiteralPath $uacKey -Name 'LocalAccountTokenFilterPolicy' -Value 1 -Type DWord -Force
        Write-TaskLog 'UAC: LocalAccountTokenFilterPolicy=1 (remote local admin full token)'
    } catch {
        Write-TaskLog ("WARN LocalAccountTokenFilterPolicy: {0}" -f $_.Exception.Message)
    }

    if ($Light) {
        Set-StepResult -Name 'LabShareAccess' -Ok $true -Detail 'SKIP heavy: recent success'
        return
    }

    # Public profile is normal on these cabinets - force FPS + WinRM on Any profile
    foreach ($group in @('File And Printer Sharing', 'Windows Remote Management', 'Windows Remote Management (HTTP-In)')) {
        try {
            Get-NetFirewallRule -DisplayGroup $group -ErrorAction SilentlyContinue |
                Set-NetFirewallRule -Profile Any -Enabled True -ErrorAction SilentlyContinue
            Enable-NetFirewallRule -DisplayGroup $group -ErrorAction SilentlyContinue | Out-Null
            Write-TaskLog ("Firewall: {0} enabled (Profile=Any)" -f $group)
        } catch {
            Write-TaskLog ("WARN firewall group {0}: {1}" -f $group, $_.Exception.Message)
        }
    }
    # Named SMB-In rules sometimes sit outside the display group on older images
    foreach ($name in @('FPS-SMB-In-TCP', 'FPS-SMB-In-TCP-NoScope', 'FPS-SMB445-In-TCP')) {
        try {
            Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue |
                Set-NetFirewallRule -Profile Any -Enabled True -Action Allow -ErrorAction SilentlyContinue
        } catch {}
    }
    try {
        Get-NetFirewallRule -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'SMB|File and Printer|Remote Management|WinRM' } |
            Set-NetFirewallRule -Profile Any -Enabled True -ErrorAction SilentlyContinue
    } catch {}

    try {
        Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.NetworkCategory -ne 'Private') {
                Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue
                Write-TaskLog ("NetworkProfile: if{0} -> Private (was {1})" -f $_.InterfaceIndex, $_.NetworkCategory)
            } else {
                Write-TaskLog ("NetworkProfile: if{0} already Private" -f $_.InterfaceIndex)
            }
        }
    } catch {
        Write-TaskLog ("WARN NetworkProfile: {0}" -f $_.Exception.Message)
    }

    Set-StepResult -Name 'LabShareAccess' -Ok $true -Detail 'test/test + Server + FPS/WinRM Any + TokenFilter=1'
}

function Ensure-LogonUserIsAdmin {
    # EWF/UWF often reverts SAM - re-add every boot BEFORE launching the admin shell.
    # Mid-session group add does not upgrade an already-open non-elevated PowerShell.
    param([Parameter(Mandatory = $true)][string]$UserName)
    if (-not $UserName) { return $false }
    Write-TaskLog ("Ensure admin: net localgroup administrators {0} /add" -f $UserName)
    cmd /c "net localgroup administrators $UserName /add" 2>&1 | ForEach-Object { Write-TaskLog "  $_" }
    $members = @(cmd /c 'net localgroup administrators' 2>&1)
    $memberText = ($members -join "`n")
    $ok = ($memberText -match ("(?im)^\s*{0}\s*$" -f [regex]::Escape($UserName)))
    if (-not $ok) {
        # Domain-style listing: COMPUTER\user or DOMAIN\user
        $ok = ($memberText -match ("(?im)\\{0}\s*$" -f [regex]::Escape($UserName)))
    }
    Write-TaskLog ("Ensure admin: {0} in Administrators = {1}" -f $UserName, $ok)
    # Silent elevation for lab (RunAs / RunLevel Highest without UAC click)
    try {
        $uacKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
        New-Item -Path $uacKey -Force -ErrorAction SilentlyContinue | Out-Null
        Set-ItemProperty -LiteralPath $uacKey -Name 'ConsentPromptBehaviorAdmin' -Value 0 -Type DWord -Force
        Write-TaskLog 'UAC: ConsentPromptBehaviorAdmin=0 (elevate without prompting)'
    } catch {
        Write-TaskLog ("WARN UAC ConsentPromptBehaviorAdmin: {0}" -f $_.Exception.Message)
    }
    Ensure-UacElevationServices
    Register-GoldClubElevateOnceTask
    return $ok
}

function Ensure-UacElevationServices {
    # GoldClub images Disable Secondary Logon / Appinfo. RunAs then fails
    # (Kill-All from unelevated Total Commander). 91 runs as SYSTEM and can start them.
    foreach ($name in @('Appinfo', 'seclogon')) {
        $cfg = cmd.exe /c "sc.exe config $name start= demand" 2>&1 | Out-String
        $st = cmd.exe /c "sc.exe start $name" 2>&1 | Out-String
        $status = 'missing'
        try { $status = [string](Get-Service -Name $name -ErrorAction Stop).Status } catch {}
        Write-TaskLog (
            "UAC service {0}: demand + start status={1} cfg={2} start={3}" -f
            $name, $status, $cfg.Trim(), $st.Trim()
        )
        if ($status -ne 'Running') {
            Write-TaskLog ("WARN UAC service {0} is not Running - USB scripts cannot RunAs" -f $name)
        }
    }
}

function Register-GoldClubElevateOnceTask {
    # Pre-create the SYSTEM runner that GoldClubElevate.ps1 /Runs.
    # Unelevated goldclub cannot schtasks /Create /RU SYSTEM (Access is denied).
    $elevDir = 'D:\usb_scripts\roulette\_elevate'
    if (-not (Test-Path -LiteralPath $elevDir)) {
        New-Item -ItemType Directory -Path $elevDir -Force | Out-Null
    }
    $runner = Join-Path $elevDir 'GoldClubElevate-run.cmd'
    if (-not (Test-Path -LiteralPath $runner)) {
        [System.IO.File]::WriteAllText(
            $runner,
            "@echo off`r`nrem written by GoldClubElevate.ps1 before schtasks /Run`r`n",
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    $taskName = 'GoldClub-ElevateOnce'
    $createOut = cmd.exe /c "schtasks /Create /TN `"$taskName`" /SC ONCE /ST 00:00 /SD 01/01/2099 /RL HIGHEST /RU SYSTEM /F /TR `"$runner`""
    if ($LASTEXITCODE -ne 0) {
        Write-TaskLog ("WARN GoldClub-ElevateOnce register: {0}" -f $createOut)
        return
    }
    Write-TaskLog ("GoldClub-ElevateOnce: SYSTEM on-demand task -> {0}" -f $runner)
}

function Stop-GoldClubAdminShells {
    # Drop early AtLogOn shells that started before goldclub was in Administrators.
    try { Stop-ScheduledTask -TaskName 'GoldClub-ElevatedPowerShell' -ErrorAction SilentlyContinue } catch {}
    try {
        Get-Process powershell, pwsh -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -gt 0 -and $_.MainWindowTitle -eq 'GoldClub Admin Shell' } |
            ForEach-Object {
                Write-TaskLog ("ElevatedPowerShell: stopping non-fresh shell pid={0}" -f $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
    } catch {}
}

function Disable-TeamViewerPortablePrinter([string]$TvExe) {
    # Portable TV always StartService(Spooler) even with remote printing off.
    # START_TYPE 4 (Disabled) -> MessageBox 1058. Ticket is FutureLogic COM, so
    # do not set Automatic and do not start the service here; demand-start lets TV
    # open the service without the dialog. Hide the XPS/VPN infs so those
    # drivers are not installed. Leave VirtualMonitor infs (three displays).
    try {
        $qc = sc.exe qc spooler 2>&1 | Out-String
        if ($qc -match 'DISABLED') {
            cmd /c 'sc.exe config spooler start= demand' 2>&1 | ForEach-Object { Write-TaskLog "  spooler: $_" }
            Write-TaskLog 'TeamViewer: Print Spooler start= demand (was Disabled; not started)'
        }
    } catch {
        Write-TaskLog ("WARN spooler demand-start: {0}" -f $_.Exception.Message)
    }
    if (-not $TvExe) { return }
    $dir = Split-Path -Parent $TvExe
    $printer = Join-Path $dir 'Printer'
    $disabled = Join-Path $dir 'Printer.disabled'
    try {
        if (Test-Path -LiteralPath $printer) {
            if (Test-Path -LiteralPath $disabled) {
                Remove-Item -LiteralPath $disabled -Recurse -Force -ErrorAction SilentlyContinue
            }
            Rename-Item -LiteralPath $printer -NewName 'Printer.disabled'
            Write-TaskLog 'TeamViewer: moved Printer to Printer.disabled (skip XPS driver install)'
        }
    } catch {
        Write-TaskLog ("WARN TeamViewer Printer rename: {0}" -f $_.Exception.Message)
    }
    $vpnInf = Join-Path $dir 'x64\TeamViewerVPN.inf'
    $vpnOff = Join-Path $dir 'x64\TeamViewerVPN.inf.disabled'
    try {
        if (Test-Path -LiteralPath $vpnInf) {
            if (Test-Path -LiteralPath $vpnOff) {
                Remove-Item -LiteralPath $vpnOff -Force -ErrorAction SilentlyContinue
            }
            Rename-Item -LiteralPath $vpnInf -NewName 'TeamViewerVPN.inf.disabled'
            Write-TaskLog 'TeamViewer: renamed TeamViewerVPN.inf (EGM has no TAP device)'
        }
    } catch {
        Write-TaskLog ("WARN TeamViewer VPN inf rename: {0}" -f $_.Exception.Message)
    }
}

function Find-UsbRoot {
    # Only treat a drive as the lab USB if expected tools are present.
    # Do NOT fall back to a bare D:\ - that would share an empty volume and
    # confuse elevated-shell WorkingDirectory when the stick is absent.
    foreach ($letter in @('D', 'E', 'F', 'G', 'H')) {
        $root = "${letter}:\"
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $tv = Join-Path $root 'TeamViewerPortable\TeamViewer.exe'
        $restore = Join-Path $root 'TeamViewer_LoginBackup\restore_tv_login_roulette.cmd'
        $tc = Join-Path $root 'totalcmd\TOTALCMD64.EXE'
        $tc32 = Join-Path $root 'totalcmd\TOTALCMD.EXE'
        if ((Test-Path -LiteralPath $tv) -or (Test-Path -LiteralPath $restore) -or (Test-Path -LiteralPath $tc) -or (Test-Path -LiteralPath $tc32)) {
            return $root.TrimEnd('\')
        }
    }
    return $null
}

function Test-RestoreTvLoginRouletteCmd {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    # Cmd uses %~dp0 as backup SRC - require roulette reg (or TeamViewer data) beside it
    $dir = Split-Path -Parent $Path
    $reg = Join-Path $dir 'TeamViewer_HKCU_Roulette.reg'
    $tvData = Join-Path $dir 'TeamViewer'
    return ((Test-Path -LiteralPath $reg) -or (Test-Path -LiteralPath $tvData))
}

function Find-RestoreTvLoginRouletteCmd {
    param([string]$UsbRoot)
    $name = 'restore_tv_login_roulette.cmd'
    $candidates = New-Object System.Collections.Generic.List[string]

    if ($PSScriptRoot) {
        $candidates.Add((Join-Path $PSScriptRoot $name))
        $candidates.Add((Join-Path $PSScriptRoot "TeamViewer_LoginBackup\$name"))
        $candidates.Add((Join-Path $PSScriptRoot "..\TeamViewer_LoginBackup\$name"))
        $candidates.Add((Join-Path $PSScriptRoot "..\..\TeamViewer_LoginBackup\$name"))
        $candidates.Add((Join-Path $PSScriptRoot "..\..\..\TeamViewer_LoginBackup\$name"))
    }

    if ($UsbRoot) {
        $candidates.Add((Join-Path $UsbRoot "TeamViewer_LoginBackup\$name"))
    }

    foreach ($letter in @('D', 'E', 'F', 'G', 'H')) {
        $candidates.Add("${letter}:\TeamViewer_LoginBackup\$name")
        $candidates.Add("${letter}:\usb_scripts\roulette\$name")
        $candidates.Add("${letter}:\usb_scripts\shared\TeamViewer_LoginBackup\$name")
        $candidates.Add("${letter}:\ConfigScanner\scripts\roulette\$name")
        $candidates.Add("${letter}:\ConfigScanner\scripts\shared\TeamViewer_LoginBackup\$name")
    }

    foreach ($fixed in @(
            "C:\goldclub\TeamViewer_LoginBackup\$name",
            "C:\Goldclub\TeamViewer_LoginBackup\$name",
            "C:\goldclub\platform\system\init\onstart.d\$name",
            "D:\usb_scripts\roulette\$name",
            "D:\ConfigScanner\scripts\roulette\$name"
        )) {
        $candidates.Add($fixed)
    }

    $seen = @{}
    foreach ($c in $candidates) {
        try {
            $full = [System.IO.Path]::GetFullPath($c)
        } catch {
            continue
        }
        if ($seen.ContainsKey($full)) { continue }
        $seen[$full] = $true
        if (Test-RestoreTvLoginRouletteCmd -Path $full) {
            return $full
        }
    }
    return $null
}

function Clear-UsbDesktopHelpers {
    # Stock boot: tear down lab GUI helpers left from a previous USB session.
    $taskNames = @(
        'GoldClub-ElevatedPowerShell',
        'GoldClub-TeamViewer-USB',
        'GoldClub-TeamViewer-Start',
        'GoldClub-TotalCommander-USB'
    )
    foreach ($name in $taskNames) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
            Write-TaskLog ("stock boot: removed task {0}" -f $name)
        } catch {
            Write-TaskLog ("WARN remove task {0}: {1}" -f $name, $_.Exception.Message)
        }
    }
    $user = Get-AutoLogonUser
    if ($user) {
        $startupCmd = "C:\Users\$user\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\GoldClub-USB-DesktopTools.cmd"
        if (Test-Path -LiteralPath $startupCmd) {
            try {
                Remove-Item -LiteralPath $startupCmd -Force -ErrorAction Stop
                Write-TaskLog ("stock boot: removed Startup launcher {0}" -f $startupCmd)
            } catch {
                Write-TaskLog ("WARN remove Startup launcher: {0}" -f $_.Exception.Message)
            }
        }
    }
}

function Install-UserStartupLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$UserName,
        [string[]]$ExePaths = @(),
        [switch]$IncludeElevatedPowerShell
    )
    $startup = "C:\Users\$UserName\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    try {
        if (-not (Test-Path -LiteralPath $startup)) {
            New-Item -ItemType Directory -Path $startup -Force | Out-Null
        }
        $cmdPath = Join-Path $startup 'GoldClub-USB-DesktopTools.cmd'
        $lines = @(
            '@echo off',
            'rem Installed by 91-EnableShareAndWinRM.ps1 - elevated PS only (TV/TC via AtLogOn tasks)'
        )
        if ($IncludeElevatedPowerShell) {
            # Triggers the Highest-runlevel AtLogOn task (keeps one admin PowerShell open)
            $lines += 'schtasks /Run /TN "GoldClub-ElevatedPowerShell" >nul 2>&1'
        }
        foreach ($exe in $ExePaths) {
            if (-not (Test-Path -LiteralPath $exe)) { continue }
            # /min - keep logon desktop clear (TV / Total Commander / etc.)
            # .cmd/.bat: wrap with cmd /c so the console closes when the script ends
            # (bare start on a .cmd can leave a window if the batch does not exit cleanly).
            if ($exe -match '\.(cmd|bat)$') {
                $lines += ('start /min "" cmd.exe /c ""{0}""' -f $exe)
            } else {
                $lines += ('start /min "" "{0}"' -f $exe)
            }
        }
        Set-Content -LiteralPath $cmdPath -Value ($lines -join "`r`n") -Encoding ASCII
        Write-TaskLog ("Startup launcher: {0}" -f $cmdPath)
        return $true
    } catch {
        Write-TaskLog ("WARN Startup launcher: {0}" -f $_.Exception.Message)
        return $false
    }
}

function Register-ElevatedPowerShellSession {
    param(
        [Parameter(Mandatory = $true)][string]$UserName,
        [string]$UsbRoot
    )
    $taskName = 'GoldClub-ElevatedPowerShell'
    $helperDir = 'C:\goldclub\var\state\onstart-share-winrm'
    $helperPs1 = Join-Path $helperDir 'Open-AdminShell.ps1'
    # Prefer USB root when present; otherwise GoldClub so missing stick never
    # leaves the shell stuck on a dead D:\ path.
    $workDir = $null
    if ($UsbRoot -and (Test-Path -LiteralPath ($UsbRoot + '\'))) {
        $workDir = ($UsbRoot.TrimEnd('\') + '\')
    } elseif (Test-Path -LiteralPath 'C:\goldclub') {
        $workDir = 'C:\goldclub'
    }
    try {
        if (-not (Test-Path -LiteralPath $helperDir)) {
            New-Item -ItemType Directory -Path $helperDir -Force | Out-Null
        }
        $workDirLiteral = if ($workDir) { $workDir } else { '' }
        # Self-elevate once via RunAs if AtLogOn started us before Administrators
        # membership applied (common with EWF). ConsentPromptBehaviorAdmin=0 => silent.
        $helperBody = @"
`$Host.UI.RawUI.WindowTitle = 'GoldClub Admin Shell'
function Test-GcAdmin {
    try {
        `$id = [Security.Principal.WindowsIdentity]::GetCurrent()
        `$prin = New-Object Security.Principal.WindowsPrincipal(`$id)
        return `$prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return `$false }
}
if (-not (Test-GcAdmin)) {
    if (`$env:GC_ADMIN_SHELL_RELAUNCH -eq '1') {
        Write-Host 'ERROR: IsAdmin=False after relaunch - goldclub not in Administrators?' -ForegroundColor Red
    } else {
        Write-Host 'Not elevated - relaunching with RunAs (silent if ConsentPromptBehaviorAdmin=0)...' -ForegroundColor Yellow
        `$env:GC_ADMIN_SHELL_RELAUNCH = '1'
        `$self = `$MyInvocation.MyCommand.Path
        if (-not `$self) { `$self = 'C:\goldclub\var\state\onstart-share-winrm\Open-AdminShell.ps1' }
        try {
            Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File', `$self) | Out-Null
            exit 0
        } catch {
            Write-Host ('ERROR: RunAs failed: {0}' -f `$_.Exception.Message) -ForegroundColor Red
        }
    }
}
if (Test-GcAdmin) {
    try {
        `$qc = sc.exe qc spooler 2>&1 | Out-String
        if (`$qc -match 'DISABLED') {
            cmd.exe /c 'sc.exe config spooler start= demand' | Out-Null
            Write-Host 'Print Spooler start= demand (TeamViewer)'
        }
    } catch {}
    `$tv = @(Get-Process -Name TeamViewer -ErrorAction SilentlyContinue | Where-Object { `$_.SessionId -gt 0 })
    `$hk = @(Get-Process -Name tv_w32,tv_x64 -ErrorAction SilentlyContinue | Where-Object { `$_.SessionId -gt 0 })
    if ((`$tv.Count -eq 0) -or (`$hk.Count -eq 0)) {
        `$startTv = 'D:\START-TV-NOW.cmd'
        if (Test-Path -LiteralPath `$startTv) {
            Write-Host 'Starting TeamViewer (START-TV-NOW)'
            Start-Process -FilePath `$startTv | Out-Null
        }
    }
    if (Test-Path -LiteralPath 'D:\FIX-CRASH-NOW.flag') {
        Remove-Item -LiteralPath 'D:\FIX-CRASH-NOW.flag' -Force
        Write-Host 'FIX-CRASH-NOW: Fix-CrashLoop -Force'
        `$fix = 'C:\goldclub\bin\Fix-CrashLoop.ps1'
        if (-not (Test-Path -LiteralPath `$fix)) { `$fix = 'D:\usb_scripts\roulette\Fix-CrashLoop.ps1' }
        if (-not (Test-Path -LiteralPath `$fix)) { `$fix = 'D:\ConfigScanner\scripts\roulette\Fix-CrashLoop.ps1' }
        if (Test-Path -LiteralPath `$fix) {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$fix -Force
        }
    }
    if (Test-Path -LiteralPath 'D:\START-GAME-NOW.flag') {
        Remove-Item -LiteralPath 'D:\START-GAME-NOW.flag' -Force
        Write-Host 'START-GAME-NOW: launching Ruleta.exe'
        if (Test-Path -LiteralPath 'C:\goldclub\var\state\ruleta-compat-hold.json') {
            `$pe = ''
            try { `$pe = [string][Diagnostics.FileVersionInfo]::GetVersionInfo('C:\goldclub\ruleta\Ruleta.exe').ProductVersion } catch {}
            if (`$pe -match '^10\.2') {
                Write-Host ('START-GAME-NOW: hold ignored, Ruleta.exe is {0}' -f `$pe)
                Start-Process -FilePath 'C:\goldclub\ruleta\Ruleta.exe' -WorkingDirectory 'C:\goldclub\ruleta'
            } else {
                Write-Host 'START-GAME-NOW blocked: ruleta-compat-hold (10.1 vs 10.2 SAS paytable)'
            }
        } else {
            Start-Process -FilePath 'C:\goldclub\ruleta\Ruleta.exe' -WorkingDirectory 'C:\goldclub\ruleta'
        }
    }
    if (Test-Path -LiteralPath 'D:\REBOOT-NOW.flag') {
        Remove-Item -LiteralPath 'D:\REBOOT-NOW.flag' -Force
        shutdown.exe /r /t 5 /f
    }
}
`$workDir = '$workDirLiteral'
if (`$workDir -and (Test-Path -LiteralPath `$workDir)) {
    Set-Location -LiteralPath `$workDir
} else {
    Write-Host ('WARN: USB/work dir missing - staying in {0} (roulette unaffected)' -f (Get-Location)) -ForegroundColor Yellow
}
Write-Host 'GoldClub elevated PowerShell (from onstart/logon)' -ForegroundColor Cyan
Write-Host ('cwd={0}' -f (Get-Location))
Write-Host ('whoami={0}' -f (whoami))
Write-Host ('IsAdmin={0}' -f (Test-GcAdmin))
"@
        [System.IO.File]::WriteAllText($helperPs1, ($helperBody -replace "`n", "`r`n"), [System.Text.UTF8Encoding]::new($false))
        Write-TaskLog ("ElevatedPowerShell: helper {0}" -f $helperPs1)
    } catch {
        Write-TaskLog ("WARN ElevatedPowerShell helper: {0}" -f $_.Exception.Message)
        $helperPs1 = $null
    }

    if ($helperPs1 -and (Test-Path -LiteralPath $helperPs1)) {
        $psArgs = "-NoExit -NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File `"$helperPs1`""
    } else {
        $psArgs = "-NoExit -NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass"
    }

    if ($workDir) {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $workDir
        Write-TaskLog ("ElevatedPowerShell: WorkingDirectory {0}" -f $workDir)
    } else {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs
        Write-TaskLog 'ElevatedPowerShell: no WorkingDirectory (no USB / C:\goldclub)'
    }
    $principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
    # StopExisting: replace early AtLogOn instance that may have started before admin add
    try {
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -MultipleInstances StopExisting `
            -StartWhenAvailable
    } catch {
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
            -MultipleInstances IgnoreNew `
            -StartWhenAvailable
        Write-TaskLog 'ElevatedPowerShell: StopExisting unsupported - using IgnoreNew + manual stop'
    }
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName
    try {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $logonTrigger `
            -Principal $principal `
            -Settings $settings `
            -Force | Out-Null
        $rl = $null
        try { $rl = (Get-ScheduledTask -TaskName $taskName -ErrorAction Stop).Principal.RunLevel } catch {}
        Write-TaskLog ("ElevatedPowerShell: AtLogOn task {0} as {1} (RunLevel={2}, -NoExit)" -f $taskName, $UserName, $rl)
        if ($rl -and ($rl.ToString() -ne 'Highest')) {
            Write-TaskLog ("ERROR ElevatedPowerShell: expected RunLevel Highest, got {0}" -f $rl)
            return $false
        }
    } catch {
        Write-TaskLog ("ERROR ElevatedPowerShell register: {0}" -f $_.Exception.Message)
        return $false
    }

    if (Test-GoldClubAdminShellRunning) {
        Write-TaskLog 'ElevatedPowerShell: Open-AdminShell already on console - not restarting'
        return $true
    }
    if ($script:RepeatSkipHeavy) {
        Write-TaskLog 'ElevatedPowerShell: recent success - not Start-ScheduledTask'
        return $true
    }

    # Restart only when no helper is open (EWF + early AtLogOn may have left a non-admin shell)
    Stop-GoldClubAdminShells
    Start-Sleep -Seconds 1

    try {
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Write-TaskLog ("ElevatedPowerShell: Start-ScheduledTask {0} (post-admin)" -f $taskName)
    } catch {
        Write-TaskLog ("WARN ElevatedPowerShell start now failed: {0} (will open at next logon / Startup)" -f $_.Exception.Message)
    }
    return $true
}

function Register-UserLogonAppTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$ExePath,
        [Parameter(Mandatory = $true)][string]$UserName,
        [string]$Label,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [switch]$RunLevelHighest
    )
    $isCmdHost = ($ExePath -eq 'cmd.exe' -or $ExePath -match '[\\/]cmd\.exe$')
    if (-not $isCmdHost -and -not (Test-Path -LiteralPath $ExePath)) {
        Write-TaskLog ("WARN {0}: missing {1}" -f $Label, $ExePath)
        return $false
    }

    if ($WorkingDirectory) {
        $workDir = $WorkingDirectory
    } elseif ($isCmdHost) {
        $workDir = $env:SystemRoot
    } else {
        $workDir = Split-Path -Parent $ExePath
    }

    # Always launch minimized so TV / TC / restore cmd do not cover the game UI.
    # Wrap with cmd + start /min (scheduled-task actions have no WindowStyle).
    # For .cmd/.bat targets use `cmd /c` inside start so the window auto-closes.
    if ($isCmdHost) {
        if ($Arguments -match '(?i)^/c\s+"([^"]+)"\s*$') {
            $inner = $Matches[1]
            if ($inner -match '\.(cmd|bat)$') {
                $launchArgs = '/c start /min "" cmd.exe /c ""{0}""' -f $inner
            } else {
                $launchArgs = '/c start /min "" "{0}"' -f $inner
            }
        } elseif ($Arguments) {
            $launchArgs = '/c start /min cmd.exe {0}' -f $Arguments
        } else {
            $launchArgs = '/c start /min "" cmd.exe'
        }
        $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launchArgs -WorkingDirectory $workDir
    } elseif ($Arguments) {
        $launchArgs = '/c start /min "" "{0}" {1}' -f $ExePath, $Arguments
        $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launchArgs -WorkingDirectory $workDir
    } else {
        if ($ExePath -match '\.(cmd|bat)$') {
            $launchArgs = '/c start /min "" cmd.exe /c ""{0}""' -f $ExePath
        } else {
            $launchArgs = '/c start /min "" "{0}"' -f $ExePath
        }
        $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launchArgs -WorkingDirectory $workDir
    }
    # Must be the console user - TeamViewer exits if launched as SYSTEM
    if ($RunLevelHighest) {
        $principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive
    }
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable

    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName
    $targetDesc = if ($Arguments) { "{0} {1}" -f $ExePath, $Arguments } else { $ExePath }
    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $logonTrigger `
            -Principal $principal `
            -Settings $settings `
            -Force | Out-Null
        $rl = if ($RunLevelHighest) { 'Highest' } else { 'Limited' }
        Write-TaskLog ("{0}: AtLogOn task {1} as {2} RunLevel={3} -> {4}" -f $Label, $TaskName, $UserName, $rl, $targetDesc)
    } catch {
        Write-TaskLog ("ERROR {0}: register failed: {1}" -f $Label, $_.Exception.Message)
        return $false
    }

    # Cleanup old SYSTEM-based tasks from previous script versions
    foreach ($old in @("$TaskName-Delayed", "$TaskName-Once")) {
        Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
    }

    return $true
}

function Start-UserAppNow {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$ExePath,
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [string]$Label,
        [switch]$RestartExisting
    )
    # Session 0 copies are invisible / TeamViewer self-exits - do not treat as success
    $all = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
    $interactive = @($all | Where-Object { $_.SessionId -gt 0 })
    $session0 = @($all | Where-Object { $_.SessionId -eq 0 })
    if ($session0.Count -gt 0) {
        Write-TaskLog ("{0}: killing session-0 orphan(s) pid={1}" -f $Label, ($session0.Id -join ','))
        $session0 | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    if ($interactive.Count -gt 0) {
        $healthy = $true
        if ($ProcessName -eq 'TeamViewer') {
            $hooks = @(Get-Process -Name 'tv_w32','tv_x64' -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
            if ($hooks.Count -eq 0) {
                Write-TaskLog ("{0}: zombie on console (pid={1}, no tv_w32/tv_x64) - restart" -f $Label, ($interactive.Id -join ','))
                $interactive | Stop-Process -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                $healthy = $false
            }
        }
        if ($healthy -and $RestartExisting) {
            Write-TaskLog (
                "{0}: replacing console instance so it inherits RunLevel Highest (pid={1})" -f
                $Label, ($interactive.Id -join ',')
            )
            $interactive | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            $healthy = $false
        }
        if ($healthy) {
            Write-TaskLog ("{0}: already on console (pid={1} session={2})" -f $Label, ($interactive.Id -join ','), ($interactive.SessionId -join ','))
            return
        }
    }

    try {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-TaskLog ("{0}: Start-ScheduledTask {1} (user interactive)" -f $Label, $TaskName)
    } catch {
        Write-TaskLog ("WARN {0}: Start-ScheduledTask failed: {1}" -f $Label, $_.Exception.Message)
        Write-TaskLog ("{0}: will start at next logon via task + Startup folder" -f $Label)
    }
}

function Enable-RustDeskLanFirewall {
    <#
    .SYNOPSIS
      Ensure inbound allow for RustDesk LAN portable (TCP 21118 / UDP 21119 / TCP 21114).
      Cabinet default inbound is Block; without these rules, clients get
      "Failed to connect to <ip>:21118". Rules can also get Disabled after reboot -
      always recreate/enable here.
    #>
    $rules = @(
        @{
            Name        = 'RustDesk-LAN-TCP-Port'
            DisplayName = 'RustDesk-LAN-TCP-Port'
            Protocol    = 'TCP'
            LocalPort   = 21118
            Program     = $null
        },
        @{
            Name        = 'RustDesk-LAN-UDP'
            DisplayName = 'RustDesk-LAN-UDP'
            Protocol    = 'UDP'
            LocalPort   = 21119
            Program     = $null
        },
        @{
            Name        = 'RustDesk-LAN-HTTP'
            DisplayName = 'RustDesk-LAN-HTTP'
            Protocol    = 'TCP'
            LocalPort   = 21114
            Program     = $null
        }
    )

    # Optional program-scoped TCP rule when portable exe is on USB
    $rustExe = $null
    foreach ($letter in @('D', 'E', 'F', 'G', 'H')) {
        $candidate = "${letter}:\RustDesk-LAN-x64\rustdesk.exe"
        if (Test-Path -LiteralPath $candidate) {
            $rustExe = $candidate
            break
        }
    }
    if ($rustExe) {
        $rules += @{
            Name        = 'RustDesk-LAN-TCP'
            DisplayName = 'RustDesk-LAN-TCP'
            Protocol    = 'TCP'
            LocalPort   = 21118
            Program     = $rustExe
        }
    }

    foreach ($spec in $rules) {
        try {
            $existing = @(Get-NetFirewallRule -DisplayName $spec.DisplayName -ErrorAction SilentlyContinue)
            if ($existing.Count -gt 0) {
                Enable-NetFirewallRule -DisplayName $spec.DisplayName -ErrorAction SilentlyContinue | Out-Null
                Set-NetFirewallRule -DisplayName $spec.DisplayName -Enabled True -Profile Any -ErrorAction SilentlyContinue | Out-Null
                $progNote = if ($spec.Program) { " program=$($spec.Program)" } else { '' }
                Write-TaskLog ("Firewall: {0}/{1} ({2}) already present - enabled{3}" -f $spec.Protocol, $spec.LocalPort, $spec.DisplayName, $progNote)
                continue
            }

            $params = @{
                DisplayName = $spec.DisplayName
                Direction   = 'Inbound'
                Action      = 'Allow'
                Protocol    = $spec.Protocol
                LocalPort   = $spec.LocalPort
                Profile     = 'Any'
                Enabled     = 'True'
                ErrorAction = 'Stop'
            }
            if ($spec.Program) {
                $params['Program'] = $spec.Program
            }
            New-NetFirewallRule @params | Out-Null
            Enable-NetFirewallRule -DisplayName $spec.DisplayName -ErrorAction SilentlyContinue | Out-Null
            Set-NetFirewallRule -DisplayName $spec.DisplayName -Enabled True -ErrorAction SilentlyContinue | Out-Null
            $progNote = if ($spec.Program) { " program=$($spec.Program)" } else { '' }
            Write-TaskLog ("Firewall: allow {0}/{1} ({2}){3}" -f $spec.Protocol, $spec.LocalPort, $spec.DisplayName, $progNote)
        } catch {
            Write-TaskLog ("WARN firewall RustDesk {0}: {1}" -f $spec.DisplayName, $_.Exception.Message)
        }
    }
}

Write-TaskLog '=== 91-EnableShareAndWinRM start ==='
Write-TaskLog ("whoami={0} computer={1}" -f (whoami), $env:COMPUTERNAME)
Write-TaskLog ("scriptDir={0}" -f $scriptDir)
Write-TaskLog ("logFile={0}" -f $logFile)
Write-TaskLog ("statusFile={0}" -f $statusFile)

if (-not (Enter-91Mutex)) {
    Set-StepResult -Name 'SingleInstance' -Ok $true -Detail 'SKIP: already running'
    Write-StatusSummary
    return
}

try {
Set-StepResult -Name 'SingleInstance' -Ok $true -Detail 'acquired'

# Detect USB early - missing stick must not block roulette or fail the script.
$usbRoot = Find-UsbRoot
if ($usbRoot) {
    Write-TaskLog ("USB detected: {0}" -f $usbRoot)
    Set-StepResult -Name 'UsbDetect' -Ok $true -Detail $usbRoot
} else {
    Write-TaskLog 'USB not detected (no TeamViewerPortable / restore cmd / totalcmd on D:-H:)'
    Write-TaskLog 'Stock roulette boot: no extra programs (elevated PS / TV / TC / Startup)'
    Set-StepResult -Name 'UsbDetect' -Ok $true -Detail 'SKIP: no USB - stock boot'
}

# Marker so we can verify this script ran and survived reboot / EWF
$markerDir = 'C:\goldclub\var\state\onstart-share-winrm'
try {
    if (-not (Test-Path -LiteralPath $markerDir)) {
        New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
    }
    $stamp = Get-Date -Format o
    Set-Content -LiteralPath (Join-Path $markerDir 'last-run.txt') -Value $stamp -Encoding ASCII
    Write-TaskLog ("marker written: {0}" -f (Join-Path $markerDir 'last-run.txt'))
    Set-StepResult -Name 'Marker' -Ok $true -Detail (Join-Path $markerDir 'last-run.txt')
} catch {
    Write-TaskLog ("WARN marker: {0}" -f $_.Exception.Message)
    Set-StepResult -Name 'Marker' -Ok $false -Detail $_.Exception.Message
}

$script:RepeatSkipHeavy = $false
$successFile = Join-Path $markerDir 'last-success.txt'
if (Test-Path -LiteralPath $successFile) {
    try {
        $prev = [datetime]::Parse((Get-Content -LiteralPath $successFile -Raw).Trim())
        $ageMin = ((Get-Date) - $prev).TotalMinutes
        if ($ageMin -ge 0 -and $ageMin -lt 15) {
            $script:RepeatSkipHeavy = $true
            Write-TaskLog ("recent success {0:n1} min ago - skip share-delete, Enable-PSRemoting -Force, GUI restart, firewall rescan" -f $ageMin)
            Set-StepResult -Name 'Debounce' -Ok $true -Detail ('SKIP heavy: {0:n1} min' -f $ageMin)
        }
    } catch {
        Write-TaskLog ("WARN last-success parse: {0}" -f $_.Exception.Message)
    }
}
if (-not $script:RepeatSkipHeavy) {
    Set-StepResult -Name 'Debounce' -Ok $true -Detail 'full run'
}

# --- 1) SMB share: slot ---
# Prefer an already-working GoldClub share (G:\ from FIX-SMB) over recreating
# C:\goldclub, which on this cabinet is a junction and used to 4392 the share.
$existingSlot = Get-NetSharePath -Name 'slot'
$keepSlot = $false
if ($existingSlot) {
    foreach ($ok in @('C:\goldclub', 'G:\', 'G:\goldclub')) {
        if (Test-SharePathEqual -Left $existingSlot -Right $ok) { $keepSlot = $true; break }
    }
}
if ($keepSlot) {
    Write-TaskLog ("share slot already {0} - leave it" -f $existingSlot)
    Set-StepResult -Name 'ShareSlot' -Ok $true -Detail ("already {0}" -f $existingSlot)
} else {
    $shareName = 'slot'
    $sharePath = $null
    foreach ($candidate in @('C:\goldclub', 'G:\')) {
        if (Test-Path -LiteralPath $candidate) {
            $sharePath = $candidate
            break
        }
    }
    if ($sharePath) {
        $slotOk = Ensure-NetShare -Name $shareName -Path $sharePath
        Set-StepResult -Name 'ShareSlot' -Ok $slotOk -Detail ("{0} as {1}" -f $sharePath, $shareName)
    } else {
        Write-TaskLog 'WARN: C:\goldclub and G:\ missing - slot share skipped'
        Set-StepResult -Name 'ShareSlot' -Ok $true -Detail 'SKIP: C:\goldclub and G:\ missing'
    }
}

# --- 1b) USB stick share (USB_Remote primary; USB legacy) + ConfigScanner ---
if ($usbRoot -and (Test-Path -LiteralPath $usbRoot)) {
    # Prefer unquoted drive-root form: net share NAME=D:\  (quoted "D:\" breaks cmd)
    $letter = $null
    if ($usbRoot -match '^([A-Za-z]):') { $letter = $Matches[1].ToUpperInvariant() }
    if ($letter) {
        $usbPath = "${letter}:\"
        $usbShareOk = Ensure-NetShare -Name 'USB_Remote' -Path $usbPath
        $null = Ensure-NetShare -Name 'USB' -Path $usbPath
        $cfg = "${letter}:\ConfigScanner"
        if (Test-Path -LiteralPath $cfg) {
            $null = Ensure-NetShare -Name 'ConfigScanner' -Path $cfg
        } else {
            Write-TaskLog 'WARN: ConfigScanner folder missing - ConfigScanner share skipped'
        }
        Set-StepResult -Name 'ShareUsb' -Ok $usbShareOk -Detail $usbPath
    } else {
        Write-TaskLog ("WARN: could not parse USB letter from {0}" -f $usbRoot)
        Set-StepResult -Name 'ShareUsb' -Ok $false -Detail ("bad path {0}" -f $usbRoot)
    }
} else {
    Write-TaskLog 'SKIP: USB stick missing - USB_Remote / ConfigScanner shares not created (roulette unaffected)'
    Set-StepResult -Name 'ShareUsb' -Ok $true -Detail 'SKIP: no USB'
}

$null = cmd /c 'net user test' 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-TaskLog 'Creating local user test'
    cmd /c 'net user test test /add' 2>&1 | ForEach-Object { Write-TaskLog "  $_" }
}
if ($script:RepeatSkipHeavy) {
    Ensure-LabShareAccess -Light
} else {
    Ensure-LabShareAccess
}

# Auto-logon user must be admin BEFORE any GoldClub Admin Shell starts (EWF reverts SAM)
$ensureAdminUser = Get-AutoLogonUser
$null = Ensure-LogonUserIsAdmin -UserName $ensureAdminUser
Set-StepResult -Name 'LogonUserAdmin' -Ok $true -Detail $ensureAdminUser

if ($script:RepeatSkipHeavy) {
    Write-TaskLog 'Firewall: File And Printer Sharing - skip rescan (recent success)'
    Set-StepResult -Name 'FirewallShare' -Ok $true -Detail 'SKIP heavy: recent success'
    Set-StepResult -Name 'FirewallRustDesk' -Ok $true -Detail 'SKIP heavy: recent success'
} else {
    try {
        Enable-NetFirewallRule -DisplayGroup 'File And Printer Sharing' -ErrorAction SilentlyContinue | Out-Null
        Set-NetFirewallRule -DisplayGroup 'File And Printer Sharing' -Profile Any -Enabled True -ErrorAction SilentlyContinue | Out-Null
        Write-TaskLog 'Firewall: File And Printer Sharing enabled'
        Set-StepResult -Name 'FirewallShare' -Ok $true
    } catch {
        Write-TaskLog ("WARN firewall share: {0}" -f $_.Exception.Message)
        Set-StepResult -Name 'FirewallShare' -Ok $false -Detail $_.Exception.Message
    }

    # RustDesk LAN portable: direct TCP 21118 + discovery UDP 21119 (+ distribute HTTP 21114)
    # Safe with no USB - port rules still apply; program-scoped rule only if rustdesk.exe found.
    Enable-RustDeskLanFirewall
    Set-StepResult -Name 'FirewallRustDesk' -Ok $true -Detail 'rules applied (program rule only if USB rustdesk present)'
}

# --- 2) WinRM (remote script execution) ---
$listening = $false
try {
    $listening = [bool](Get-NetTCPConnection -LocalPort 5985 -State Listen -ErrorAction SilentlyContinue)
} catch {
    $listening = [bool](netstat -an | Select-String ':5985\s+LISTENING')
}

if ($listening) {
    Write-TaskLog 'WinRM already listening on 5985 - skip Enable-PSRemoting -Force'
    try { Set-Service WinRM -StartupType Automatic } catch {}
    Set-StepResult -Name 'WinRM' -Ok $true -Detail 'already listening'
} else {
    try {
        Write-TaskLog 'Enable-PSRemoting -Force -SkipNetworkProfileCheck'
        Enable-PSRemoting -Force -SkipNetworkProfileCheck
        Set-Service WinRM -StartupType Automatic
        Start-Service WinRM
        Write-TaskLog 'WinRM service started (Automatic)'
        Set-StepResult -Name 'WinRM' -Ok $true
    } catch {
        Write-TaskLog ("ERROR WinRM: {0}" -f $_.Exception.Message)
        Set-StepResult -Name 'WinRM' -Ok $false -Detail $_.Exception.Message
    }
}

try {
    Test-WSMan -ComputerName localhost -ErrorAction Stop | Out-Null
    Write-TaskLog 'Test-WSMan localhost: OK'
    Set-StepResult -Name 'WSManLocal' -Ok $true
} catch {
    Write-TaskLog ("Test-WSMan localhost: FAIL ({0})" -f $_.Exception.Message)
    Set-StepResult -Name 'WSManLocal' -Ok $false -Detail $_.Exception.Message
}

if (-not $listening) {
    try {
        $listening = [bool](Get-NetTCPConnection -LocalPort 5985 -State Listen -ErrorAction SilentlyContinue)
    } catch {
        $listening = [bool](netstat -an | Select-String ':5985\s+LISTENING')
    }
}
Write-TaskLog ("Port 5985 listening: {0}" -f $listening)
Set-StepResult -Name 'Port5985' -Ok $listening -Detail ('listening={0}' -f $listening)

# --- 3-4) Desktop helpers ONLY when USB is present ---
# No USB => stock roulette: do not open elevated PowerShell, Startup launcher,
# TeamViewer, or Total Commander (even if a restore .cmd exists on C:).
# Order: TeamViewer/TC FIRST, then elevated shell. On .111 the script was stopping
# right after Start-ScheduledTask for the admin shell, so TV never started.
if (-not $usbRoot) {
    Write-TaskLog 'SKIP ElevatedPowerShell / Startup / TeamViewer / TotalCommander - stock roulette boot'
    Clear-UsbDesktopHelpers
    Set-StepResult -Name 'ElevatedPowerShell' -Ok $true -Detail 'SKIP: no USB - stock boot'
    Set-StepResult -Name 'TeamViewer' -Ok $true -Detail 'SKIP: no USB - stock boot'
    Set-StepResult -Name 'TotalCommander' -Ok $true -Detail 'SKIP: no USB - stock boot'
    Write-TaskLog '=== 91-EnableShareAndWinRM done (stock boot, no USB helpers) ==='
    Write-91SuccessStamp
    Write-StatusSummary
    return
}

$logonUser = Get-AutoLogonUser
Write-TaskLog ("USB present - desktop tools for auto-logon user: {0}" -f $logonUser)
$null = Ensure-LogonUserIsAdmin -UserName $logonUser

$restoreTvCmd = Find-RestoreTvLoginRouletteCmd -UsbRoot $usbRoot
$tvExe = Join-Path $usbRoot 'TeamViewerPortable\TeamViewer.exe'
$tcExe = $null
$tc64 = Join-Path $usbRoot 'totalcmd\TOTALCMD64.EXE'
$tc32 = Join-Path $usbRoot 'totalcmd\TOTALCMD.EXE'
if (Test-Path -LiteralPath $tc64) { $tcExe = $tc64 }
elseif (Test-Path -LiteralPath $tc32) { $tcExe = $tc32 }

Write-TaskLog ("USB root for desktop tools: {0}" -f $usbRoot)

if (Test-Path -LiteralPath $tvExe) {
    Disable-TeamViewerPortablePrinter -TvExe $tvExe
    try {
        Unregister-ScheduledTask -TaskName 'GoldClub-TeamViewer-USB' -Confirm:$false -ErrorAction SilentlyContinue
        Write-TaskLog 'TeamViewer: removed GoldClub-TeamViewer-USB (restore taskkills TV on logon)'
    } catch {}
    $tvLaunch = Join-Path $usbRoot 'START-TV-NOW.cmd'
    if (-not (Test-Path -LiteralPath $tvLaunch)) { $tvLaunch = $tvExe }
    if (Register-UserLogonAppTask -TaskName 'GoldClub-TeamViewer-Start' -ExePath $tvLaunch -UserName $logonUser -Label 'TeamViewer' -RunLevelHighest) {
        if ($script:RepeatSkipHeavy) {
            Write-TaskLog 'TeamViewer: recent success - not Start-ScheduledTask'
        } else {
            Start-UserAppNow -TaskName 'GoldClub-TeamViewer-Start' -ExePath $tvLaunch -ProcessName 'TeamViewer' -Label 'TeamViewer'
        }
        Set-StepResult -Name 'TeamViewer' -Ok $true -Detail $tvLaunch
    } else {
        Set-StepResult -Name 'TeamViewer' -Ok $false -Detail 'register start task failed'
    }
} elseif ($restoreTvCmd) {
    Write-TaskLog 'TeamViewer: portable exe missing - not starting restore (taskkill)'
    Set-StepResult -Name 'TeamViewer' -Ok $true -Detail 'SKIP start-now: restore cmd would taskkill TV'
} else {
    Write-TaskLog 'WARN TeamViewer: no restore_tv_login_roulette.cmd and no TeamViewerPortable\TeamViewer.exe'
    Set-StepResult -Name 'TeamViewer' -Ok $false -Detail 'tools missing'
}

if ($tcExe) {
    $tcProc = if ($tcExe -match '64') { 'TOTALCMD64' } else { 'TOTALCMD' }
    if (Register-UserLogonAppTask -TaskName 'GoldClub-TotalCommander-USB' -ExePath $tcExe -UserName $logonUser -Label 'TotalCommander' -RunLevelHighest) {
        if ($script:RepeatSkipHeavy) {
            Write-TaskLog 'TotalCommander: recent success - restarting so RunLevel Highest applies'
        }
        Start-UserAppNow -TaskName 'GoldClub-TotalCommander-USB' -ExePath $tcExe -ProcessName $tcProc -Label 'TotalCommander' -RestartExisting
        Set-StepResult -Name 'TotalCommander' -Ok $true -Detail $tcExe
    } else {
        Set-StepResult -Name 'TotalCommander' -Ok $false -Detail 'register failed'
    }
} else {
    Write-TaskLog ("WARN TotalCommander: {0}\totalcmd\TOTALCMD(64).EXE missing" -f $usbRoot)
    Set-StepResult -Name 'TotalCommander' -Ok $false -Detail 'TOTALCMD missing on USB'
}

# Elevated shell LAST - must not block TV/TC if this step hangs or faults
Write-TaskLog 'ElevatedPowerShell: registering after TeamViewer/TC'
try {
    try { Install-UserStartupLauncher -UserName $logonUser -ExePaths @() -IncludeElevatedPowerShell | Out-Null } catch {
        Write-TaskLog ("WARN Startup launcher: {0}" -f $_.Exception.Message)
    }
    $elevOk = [bool](Register-ElevatedPowerShellSession -UserName $logonUser -UsbRoot $usbRoot)
    Set-StepResult -Name 'ElevatedPowerShell' -Ok $elevOk -Detail ("user={0}" -f $logonUser)
} catch {
    Write-TaskLog ("ERROR ElevatedPowerShell: {0}" -f $_.Exception.Message)
    Set-StepResult -Name 'ElevatedPowerShell' -Ok $false -Detail $_.Exception.Message
}

Write-TaskLog '=== 91-EnableShareAndWinRM done (USB helpers registered) ==='
Write-91SuccessStamp
Write-StatusSummary
} finally {
    Exit-91Mutex
}
