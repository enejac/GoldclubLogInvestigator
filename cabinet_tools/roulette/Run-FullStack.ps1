<#
.SYNOPSIS
    Reverse of Kill-All: start full GoldClub/roulette stack + Godot UI (direct).

.DESCRIPTION
    Fast path (no HIH):
      1) Start GoldClub services in dependency order
         LogDaemon -> CommCtrl -> CommCtrl SAS -> HWSubsys, then the rest
      2) Launch nginx watcher (fire-and-forget)
      3) Start ruleta.exe if needed; wait for ruleta to spawn Godot (it owns the UI)
      4) Only if Godot never appears, start godot.exe once as fallback (--main-pack)
      5) Prune any extra Godot (frozen "Loading something..." splash)
      6) ALWAYS verify health (CommCtrl ports, HW connected, Godot not frozen,
         Godot/ruleta in console session - not Session 0)
         - "service Running" / "process exists" alone is NEVER success
         - If already-Running bootstrap is unhealthy, restart it in order once
      7) Do NOT resume HIH / do NOT start HIH.exe / do NOT start game-start

    WinRM / Session 0: ruleta and Godot MUST NOT be Start-Process'd from a
    non-interactive session (splash-frozen ~16MB, empty title). When this host
    session is not the interactive console, UI exes launch via Register-ScheduledTask
    LogonType Interactive as the autologon/console user (typically goldclub).

    Do NOT start Godot in parallel with ruleta - ruleta already launches the frontend
    from godot.xml. A second Start-Process races and leaves a stuck Winsystems splash.

    Why order matters for ticket printer: CommCtrl opens COM4->TCP 30400; HWSubsys
    loads drivers on 30400/30600/30700. Parallel Start() races HW before the gateway
    listens ("connection refused") and ruleta used to launch before settle -> tito0 lock.

    Backup of previous shell/HIH method: Run-FullStack.ps1.bak-shell-hih

.EXAMPLE
    .\Run-FullStack.ps1
#>
[CmdletBinding()]
param(
    [switch] $WhatIf,
    [switch] $AlreadyElevated,
    [string] $BinDir = 'C:\goldclub\bin',
    [string] $RuletaDir = 'C:\goldclub\ruleta',
    [string] $GodotXml = 'C:\goldclub\config\etc\application\ruleta\godot.xml',
    # Max wait for a kicked service to reach Running.
    [int] $ServiceWaitSec = 12,
    # Wait for ruleta-owned Godot before optional fallback Start-Process.
    [int] $GodotWaitSec = 12,
    # Below this WorkingSet after settle => treat as frozen splash (not real UI).
    [int] $GodotMinHealthyMb = 40,
    # Seconds Godot must be alive before freeze heuristics apply (loading needs time).
    [int] $GodotFreezeCheckAgeSec = 20
)

$ErrorActionPreference = 'Continue'
$script:ExitCode = 1
$script:RuletaCompatHold = $false
$script:RuletaCompatHoldReason = ''
$script:Error30Relocked = $false

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $p = New-Object Security.Principal.WindowsPrincipal($id)
        return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

if (-not $WhatIf -and -not $AlreadyElevated -and -not (Test-IsAdmin)) {
    $self = $MyInvocation.MyCommand.Path
    if (-not $self) { Write-Host 'ERROR: cannot elevate' -ForegroundColor Red; exit 1 }
    $helper = Join-Path $PSScriptRoot 'GoldClubElevate.ps1'
    if (Test-Path -LiteralPath $helper) { . $helper }
    $argList = @('-AlreadyElevated')
    if ($PSBoundParameters.ContainsKey('BinDir')) { $argList += @('-BinDir', $BinDir) }
    if ($PSBoundParameters.ContainsKey('RuletaDir')) { $argList += @('-RuletaDir', $RuletaDir) }
    if ($PSBoundParameters.ContainsKey('GodotXml')) { $argList += @('-GodotXml', $GodotXml) }
    if ($PSBoundParameters.ContainsKey('ServiceWaitSec')) { $argList += @('-ServiceWaitSec', "$ServiceWaitSec") }
    if ($PSBoundParameters.ContainsKey('GodotWaitSec')) { $argList += @('-GodotWaitSec', "$GodotWaitSec") }
    if ($PSBoundParameters.ContainsKey('GodotMinHealthyMb')) { $argList += @('-GodotMinHealthyMb', "$GodotMinHealthyMb") }
    if ($PSBoundParameters.ContainsKey('GodotFreezeCheckAgeSec')) { $argList += @('-GodotFreezeCheckAgeSec', "$GodotFreezeCheckAgeSec") }
    if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {
        exit (Invoke-GoldClubSelfElevate -ScriptPath $self -ArgumentList $argList)
    }
    Write-Host 'Not elevated - relaunching as Administrator...' -ForegroundColor Yellow
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $self) + $argList
    try {
        $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $psArgs -PassThru -Wait
        exit $(if ($null -ne $p) { $p.ExitCode } else { 1 })
    } catch {
        Write-Host ("ERROR: elevation failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
}

function Get-AutoLogonUser {
    try {
        $wl = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
        if ($wl.DefaultUserName) { return [string]$wl.DefaultUserName }
    } catch {}
    return 'goldclub'
}

function Get-CurrentSessionId {
    try { return [int][System.Diagnostics.Process]::GetCurrentProcess().SessionId } catch { return 0 }
}

function Get-ActiveConsoleSessionId {
    try {
        $console = @(Get-Process -Name explorer -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -gt 0 } |
            Sort-Object SessionId)
        if ($console.Count -gt 0) { return [int]$console[0].SessionId }
    } catch {}
    try {
        if (-not ('WtsConsoleSession' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WtsConsoleSession {
  [DllImport("kernel32.dll")]
  public static extern uint WTSGetActiveConsoleSessionId();
}
'@ -ErrorAction Stop
        }
        $id = [WtsConsoleSession]::WTSGetActiveConsoleSessionId()
        if ($id -ne 0xFFFFFFFF -and $id -gt 0) { return [int]$id }
    } catch {}
    return 0
}

function Test-CanLaunchUiInCurrentSession {
    $mySess = Get-CurrentSessionId
    if ($mySess -le 0) { return $false }
    $console = Get-ActiveConsoleSessionId
    if ($console -gt 0 -and $mySess -ne $console) { return $false }
    return $true
}

function Test-ProcessOnConsoleSession {
    param([System.Diagnostics.Process] $Proc)
    if (-not $Proc) {
        return [pscustomobject]@{ Ok = $false; Reason = 'no process' }
    }
    $sess = -1
    try { $sess = [int]$Proc.SessionId } catch {}
    $console = Get-ActiveConsoleSessionId
    if ($sess -eq 0) {
        return [pscustomobject]@{
            Ok     = $false
            Reason = ("pid={0} in Session 0 (no desktop; WinRM/non-interactive launch)" -f $Proc.Id)
        }
    }
    if ($console -gt 0 -and $sess -ne $console) {
        return [pscustomobject]@{
            Ok     = $false
            Reason = ("pid={0} Session={1} != console Session={2}" -f $Proc.Id, $sess, $console)
        }
    }
    return [pscustomobject]@{
        Ok     = $true
        Reason = ("pid={0} Session={1} (console={2})" -f $Proc.Id, $sess, $console)
    }
}

function Start-UiProcessInConsole {
    param(
        [Parameter(Mandatory = $true)][string] $FilePath,
        [string] $WorkingDirectory,
        [string[]] $ArgumentList = @(),
        [string] $Label = 'UI'
    )
    if (-not $WorkingDirectory) {
        $WorkingDirectory = Split-Path -Parent $FilePath
    }
    $mySess = Get-CurrentSessionId
    $console = Get-ActiveConsoleSessionId
    $user = Get-AutoLogonUser

    if (Test-CanLaunchUiInCurrentSession) {
        Write-Host ("  launch mode: same-session (this Session {0}; console={1}) for {2}" -f $mySess, $console, $Label) -ForegroundColor DarkGray
        if ($WhatIf) {
            Write-Host ("  WhatIf: Start-Process {0}" -f $FilePath) -ForegroundColor Yellow
            return 'same-session'
        }
        if ($ArgumentList.Count -gt 0) {
            Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory
        } else {
            Start-Process -FilePath $FilePath -WorkingDirectory $WorkingDirectory
        }
        return 'same-session'
    }

    Write-Host ("  launch mode: interactive-task as {0} (this Session {1}; console={2}) for {3}" -f $user, $mySess, $console, $Label) -ForegroundColor Cyan
    if ($WhatIf) {
        Write-Host ("  WhatIf: Register-ScheduledTask Interactive -> {0}" -f $FilePath) -ForegroundColor Yellow
        return 'interactive-task'
    }
    if ($console -le 0) {
        Write-Host '  ERROR: no interactive console session (explorer) - cannot host Godot UI' -ForegroundColor Red
        return 'failed'
    }

    $taskName = 'GoldClub-FullStack-UI-Once'
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $argStr = ''
    if ($ArgumentList.Count -gt 0) {
        $argStr = (@($ArgumentList | ForEach-Object {
            $a = [string]$_
            if ($a -match '[\s"]') { '"{0}"' -f ($a -replace '"', '\"') } else { $a }
        })) -join ' '
    }
    if ($argStr) {
        $action = New-ScheduledTaskAction -Execute $FilePath -Argument $argStr -WorkingDirectory $WorkingDirectory
    } else {
        $action = New-ScheduledTaskAction -Execute $FilePath -WorkingDirectory $WorkingDirectory
    }
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -MultipleInstances IgnoreNew
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 2
    } catch {
        Write-Host ("  ERROR: interactive-task launch failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        return 'failed'
    } finally {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    return 'interactive-task'
}

function Stop-WrongSessionUiProcesses {
    param([switch] $WhatIf)
    $console = Get-ActiveConsoleSessionId
    $stopped = 0
    foreach ($name in @('godot', 'Godot_v4', 'ruleta', 'Ruleta')) {
        foreach ($p in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
            $bad = ($p.SessionId -eq 0) -or ($console -gt 0 -and $p.SessionId -ne $console)
            if (-not $bad) { continue }
            if ($WhatIf) {
                Write-Host ("  WhatIf: Stop-Process {0} pid={1} Session={2}" -f $p.Name, $p.Id, $p.SessionId) -ForegroundColor Yellow
                $stopped++
                continue
            }
            try {
                Stop-Process -Id $p.Id -Force -ErrorAction Stop
                Write-Host ("  stopped wrong-session {0} pid={1} Session={2}" -f $p.Name, $p.Id, $p.SessionId) -ForegroundColor DarkYellow
                $stopped++
            } catch {
                Write-Host ("  WARN: could not stop {0} pid={1}: {2}" -f $p.Name, $p.Id, $_.Exception.Message) -ForegroundColor Yellow
            }
        }
    }
    if ($stopped -gt 0 -and -not $WhatIf) { Start-Sleep -Milliseconds 500 }
    return $stopped
}

function Get-GodotUiProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -and ($_.Name -like 'godot*' -or $_.Name -ieq 'Godot_v4.exe')
    } | ForEach-Object {
        try { Get-Process -Id $_.ProcessId -ErrorAction Stop } catch { $null }
    } | Where-Object { $_ } | Sort-Object Id -Unique)
}

function Get-NamedProcesses {
    param([string[]] $Names)
    $all = @()
    foreach ($n in $Names) {
        $all += @(Get-Process -Name $n -ErrorAction SilentlyContinue)
    }
    @($all | Sort-Object Id -Unique)
}

function Get-GodotProcessDetails {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -and ($_.Name -like 'godot*' -or $_.Name -ieq 'Godot_v4.exe')
    } | ForEach-Object {
        $parentName = $null
        try {
            $par = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $_.ParentProcessId) -ErrorAction SilentlyContinue
            if ($par) { $parentName = $par.Name }
        } catch {}
        [pscustomobject]@{
            ProcessId       = [int]$_.ProcessId
            ParentProcessId = [int]$_.ParentProcessId
            ParentName      = $parentName
            CommandLine     = [string]$_.CommandLine
            WorkingSet      = try { (Get-Process -Id $_.ProcessId -ErrorAction Stop).WorkingSet64 } catch { 0 }
        }
    })
}

function Get-GodotUiOnConsole {
    $console = Get-ActiveConsoleSessionId
    @(Get-GodotUiProcesses | Where-Object {
        $_.SessionId -gt 0 -and ($console -le 0 -or $_.SessionId -eq $console)
    })
}

function Wait-GodotUi {
    param(
        [int] $TimeoutSec,
        [switch] $ConsoleOnly
    )
    if ($TimeoutSec -le 0) {
        if ($ConsoleOnly) { return @(Get-GodotUiOnConsole) }
        return @(Get-GodotUiProcesses)
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $gods = if ($ConsoleOnly) { @(Get-GodotUiOnConsole) } else { @(Get-GodotUiProcesses) }
        if ($gods.Count -gt 0) { return $gods }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    if ($ConsoleOnly) { return @(Get-GodotUiOnConsole) }
    return @(Get-GodotUiProcesses)
}

function Select-KeepGodotPid {
    param([object[]] $Details)
    if (-not $Details -or $Details.Count -eq 0) { return $null }
    $byRuleta = @($Details | Where-Object {
        $_.ParentName -and ($_.ParentName -ieq 'ruleta.exe' -or $_.ParentName -ieq 'Ruleta.exe')
    } | Sort-Object WorkingSet -Descending)
    if ($byRuleta.Count -gt 0) { return $byRuleta[0].ProcessId }
    $byPack = @($Details | Where-Object {
        $_.CommandLine -and ($_.CommandLine -match 'main-pack|RouletteGui\.pck')
    } | Sort-Object WorkingSet -Descending)
    if ($byPack.Count -gt 0) { return $byPack[0].ProcessId }
    return (@($Details | Sort-Object WorkingSet -Descending)[0].ProcessId)
}

function Remove-ExtraGodotProcesses {
    param([switch] $WhatIf)
    $details = @(Get-GodotProcessDetails)
    if ($details.Count -le 1) { return @(Get-GodotUiProcesses) }
    $keep = Select-KeepGodotPid -Details $details
    $extras = @($details | Where-Object { $_.ProcessId -ne $keep })
    Write-Host ("  {0} Godot - keep pid={1} (prefer ruleta child / largest), stop {2}" -f `
        $details.Count, $keep, (($extras | ForEach-Object ProcessId) -join ',')) -ForegroundColor Yellow
    foreach ($x in $extras) {
        if ($WhatIf) {
            Write-Host ("  WhatIf: Stop-Process godot pid={0}" -f $x.ProcessId) -ForegroundColor Yellow
            continue
        }
        try {
            Stop-Process -Id $x.ProcessId -Force -ErrorAction Stop
            Write-Host ("  stopped extra Godot pid={0} (WS~{1:N0} MB)" -f $x.ProcessId, ($x.WorkingSet / 1MB)) -ForegroundColor DarkYellow
        } catch {
            Write-Host ("  WARN: could not stop Godot pid={0}: {1}" -f $x.ProcessId, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    Start-Sleep -Milliseconds 300
    return @(Get-GodotUiProcesses)
}

function Get-GodotLaunchInfo {
    param([string] $XmlPath, [string] $RuletaRoot)
    $info = [ordered]@{
        AppExe   = 'C:\goldclub\apps\godot\3.3.2.0-gc\godot.exe'
        AppDir   = 'C:\goldclub\apps\godot\3.3.2.0-gc'
        MainPack = (Join-Path $RuletaRoot 'godot\RouletteGui.pck')
        Players  = 1
        Position = '0,0'
        Skin     = 'new'
    }
    if (-not (Test-Path -LiteralPath $XmlPath)) { return [pscustomobject]$info }
    try {
        [xml]$doc = Get-Content -LiteralPath $XmlPath -Raw -ErrorAction Stop
        $nsm = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
        $nsm.AddNamespace('c', 'config')
        $appPath = $doc.SelectSingleNode('//c:apppath', $nsm)
        $appName = $doc.SelectSingleNode('//c:appname', $nsm)
        $resPath = $doc.SelectSingleNode('//c:respath', $nsm)
        $resName = $doc.SelectSingleNode('//c:resname', $nsm)
        $w1pos = $doc.SelectSingleNode('//c:window1/c:position', $nsm)
        if ($appPath -and $appName) {
            $info.AppDir = $appPath.InnerText.Trim().TrimEnd('\', '/')
            $info.AppExe = Join-Path $info.AppDir $appName.InnerText.Trim()
        }
        if ($resPath -and $resName) {
            $info.MainPack = Join-Path ($resPath.InnerText.Trim().TrimEnd('\', '/')) $resName.InnerText.Trim()
        }
        if ($w1pos -and $w1pos.InnerText) {
            $info.Position = $w1pos.InnerText.Trim()
        }
    } catch {
        Write-Host ("  WARN: godot.xml parse failed: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
    return [pscustomobject]$info
}

# CommCtrl: TCP = 30000 + COM# -> ticket 30400/COM4, switch 30600/COM6, lights 30700/COM7
$script:CommCtrlListenPorts = @(30400, 30600, 30700)
# HW must hold Established sessions to switch+lights; ticket (30400) required when tito is configured.
$script:HwAlwaysConnectedPorts = @(30600, 30700)

function Get-ListeningLocalPorts {
    $set = New-Object 'System.Collections.Generic.HashSet[int]'
    try {
        foreach ($c in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
            [void]$set.Add([int]$c.LocalPort)
        }
    } catch {
        # Fallback: netstat parse if Get-NetTCPConnection unavailable
        foreach ($line in @(netstat -an | Select-String 'LISTENING')) {
            if ($line -match ':(\d+)\s+.*LISTENING') {
                [void]$set.Add([int]$Matches[1])
            }
        }
    }
    return $set
}

function Get-EstablishedRemotePorts {
    $set = New-Object 'System.Collections.Generic.HashSet[int]'
    try {
        foreach ($c in @(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue)) {
            if ($c.RemoteAddress -eq '127.0.0.1' -or $c.RemoteAddress -eq '::1') {
                [void]$set.Add([int]$c.RemotePort)
            }
        }
    } catch {}
    return $set
}

function Test-TitoDriverConfigured {
    $cfg = 'C:\goldclub\config\etc\application\HW\driverssetup\configuration.xml'
    if (-not (Test-Path -LiteralPath $cfg)) { return $false }
    try {
        $raw = Get-Content -LiteralPath $cfg -Raw -ErrorAction Stop
        return [bool]($raw -match '(?i)aliasName>\s*tito\s*<|tcp://127\.0\.0\.1:30400')
    } catch { return $false }
}

function Get-LatestHwSubsysLog {
    $dir = 'C:\goldclub\var\log\HWSubsys'
    if (-not (Test-Path -LiteralPath $dir)) { return $null }
    Get-ChildItem -LiteralPath $dir -Filter '*.log' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Test-HwLogPortConnected {
    param([int] $Port, [int] $LookbackMin = 10)
    $log = Get-LatestHwSubsysLog
    if (-not $log) { return $null }
    $cutoff = (Get-Date).AddMinutes(-$LookbackMin)
    if ($log.LastWriteTime -lt $cutoff) { return $null }
    $patConn = ("Connected to:\s*127\.0\.0\.1:{0}\b" -f $Port)
    $patFail = ("Cannot connect to\s*127\.0\.0\.1:{0}\b" -f $Port)
    $lastConn = $null
    $lastFail = $null
    try {
        foreach ($m in @(Select-String -Path $log.FullName -Pattern $patConn -ErrorAction SilentlyContinue | Select-Object -Last 3)) {
            $lastConn = $m
        }
        foreach ($m in @(Select-String -Path $log.FullName -Pattern $patFail -ErrorAction SilentlyContinue | Select-Object -Last 3)) {
            $lastFail = $m
        }
    } catch { return $null }
    if (-not $lastConn) { return $false }
    if ($lastFail -and $lastFail.LineNumber -gt $lastConn.LineNumber) { return $false }
    return $true
}

function Test-GodotProcessHealthy {
    param(
        [System.Diagnostics.Process] $Proc,
        [int] $MinHealthyMb,
        [int] $FreezeCheckAgeSec
    )
    if (-not $Proc) {
        return [pscustomobject]@{ Ok = $false; Reason = 'no Godot process' }
    }
    $sessCheck = Test-ProcessOnConsoleSession -Proc $Proc
    if (-not $sessCheck.Ok) {
        return [pscustomobject]@{ Ok = $false; Reason = ("Godot {0}" -f $sessCheck.Reason) }
    }
    $ageSec = 0
    try { $ageSec = ((Get-Date) - $Proc.StartTime).TotalSeconds } catch {}
    $mb = [math]::Round($Proc.WorkingSet64 / 1MB, 1)
    $title = ''
    try { $title = [string]$Proc.MainWindowTitle } catch {}
    $responding = $true
    try { $responding = [bool]$Proc.Responding } catch {}
    $sess = -1
    try { $sess = [int]$Proc.SessionId } catch {}

    if (-not $responding) {
        return [pscustomobject]@{ Ok = $false; Reason = ("Godot pid={0} not Responding (WS={1}MB title='{2}')" -f $Proc.Id, $mb, $title) }
    }
    if ($title -match '(?i)loading\s+something|winsystems') {
        return [pscustomobject]@{ Ok = $false; Reason = ("Godot pid={0} frozen splash title='{1}' (WS={2}MB)" -f $Proc.Id, $title, $mb) }
    }
    if ($ageSec -ge $FreezeCheckAgeSec -and $mb -lt $MinHealthyMb) {
        return [pscustomobject]@{
            Ok     = $false
            Reason = ("Godot pid={0} looks frozen/splash-only (WS={1}MB < {2}MB after {3:N0}s, title='{4}')" -f `
                $Proc.Id, $mb, $MinHealthyMb, $ageSec, $title)
        }
    }
    return [pscustomobject]@{
        Ok     = $true
        Reason = ("Godot pid={0} Session={1} WS={2}MB age={3:N0}s title='{4}'" -f $Proc.Id, $sess, $mb, $ageSec, $title)
    }
}

function Get-StackHealthReport {
    param(
        [string[]] $CriticalServiceNames,
        [int] $GodotMinHealthyMb,
        [int] $GodotFreezeCheckAgeSec
    )
    $failures = New-Object System.Collections.Generic.List[string]
    $notes = New-Object System.Collections.Generic.List[string]

    foreach ($sn in $CriticalServiceNames) {
        $svc = Resolve-GoldClubService -NameOrDisplay $sn
        if (-not $svc) {
            [void]$failures.Add(("missing service: {0}" -f $sn))
            continue
        }
        try { $svc.Refresh() } catch {}
        if ($svc.Status -ne 'Running') {
            [void]$failures.Add(("service not Running ({0}): {1}" -f $svc.Status, $svc.Name))
        } else {
            [void]$notes.Add(("service OK: {0}" -f $svc.Name))
        }
    }

    $listen = Get-ListeningLocalPorts
    foreach ($port in $script:CommCtrlListenPorts) {
        if ($listen.Contains([int]$port)) {
            [void]$notes.Add(("listen OK: {0}" -f $port))
        } else {
            [void]$failures.Add(("CommCtrl port not listening: {0}" -f $port))
        }
    }

    $needHw = New-Object 'System.Collections.Generic.List[int]'
    foreach ($p in $script:HwAlwaysConnectedPorts) { [void]$needHw.Add([int]$p) }
    if (Test-TitoDriverConfigured) { [void]$needHw.Add(30400) }

    $estab = Get-EstablishedRemotePorts
    foreach ($port in $needHw) {
        $ok = $estab.Contains([int]$port)
        if (-not $ok) {
            $logOk = Test-HwLogPortConnected -Port $port
            if ($logOk -eq $true) { $ok = $true }
        }
        if ($ok) {
            [void]$notes.Add(("HW connected OK: {0}" -f $port))
        } else {
            [void]$failures.Add(("HW not connected to 127.0.0.1:{0}" -f $port))
        }
    }

    $ruleta = @(Get-NamedProcesses -Names @('ruleta', 'Ruleta'))
    $gods = @(Get-GodotUiProcesses)
    if ($script:RuletaCompatHold) {
        [void]$notes.Add('ruleta/Godot skipped (compat hold)')
        return [pscustomobject]@{
            Ok       = ($failures.Count -eq 0)
            Failures = @($failures)
            Notes    = @($notes)
            Ruleta   = $ruleta.Count
            Godot    = $gods.Count
            Held     = $true
        }
    }
    if ($ruleta.Count -lt 1) {
        [void]$failures.Add('ruleta.exe not running')
    } else {
        $rSess = Test-ProcessOnConsoleSession -Proc $ruleta[0]
        if ($rSess.Ok) {
            [void]$notes.Add(("ruleta OK: pid={0} Session={1}" -f $ruleta[0].Id, $ruleta[0].SessionId))
        } else {
            [void]$failures.Add(("ruleta {0}" -f $rSess.Reason))
        }
    }
    if ($gods.Count -lt 1) {
        if ($ruleta.Count -gt 0) {
            [void]$failures.Add('ruleta running but Godot UI missing')
        } else {
            [void]$failures.Add('Godot UI not running')
        }
    } elseif ($gods.Count -gt 1) {
        [void]$failures.Add(("multiple Godot processes ({0}) - likely frozen splash leftover" -f $gods.Count))
        foreach ($g in $gods) {
            $s = Test-ProcessOnConsoleSession -Proc $g
            if (-not $s.Ok) { [void]$failures.Add(("Godot {0}" -f $s.Reason)) }
        }
    } else {
        $gHealth = Test-GodotProcessHealthy -Proc $gods[0] -MinHealthyMb $GodotMinHealthyMb -FreezeCheckAgeSec $GodotFreezeCheckAgeSec
        if ($gHealth.Ok) {
            [void]$notes.Add($gHealth.Reason)
        } else {
            [void]$failures.Add($gHealth.Reason)
        }
    }

    return [pscustomobject]@{
        Ok       = ($failures.Count -eq 0)
        Failures = @($failures)
        Notes    = @($notes)
        Ruleta   = $ruleta.Count
        Godot    = $gods.Count
    }
}

function Write-StackHealthReport {
    param($Report)
    foreach ($n in @($Report.Notes)) {
        Write-Host ("  {0}" -f $n) -ForegroundColor DarkGray
    }
    foreach ($f in @($Report.Failures)) {
        Write-Host ("  FAIL: {0}" -f $f) -ForegroundColor Red
    }
}

Write-Host 'Starting GoldClub FULL STACK + Godot (direct, no HIH) ...' -ForegroundColor Cyan
Write-Host ("Elevated: {0}  User: {1}\{2}" -f (Test-IsAdmin), $env:USERDOMAIN, $env:USERNAME) -ForegroundColor DarkGray
Write-Host ("Bin: {0}" -f $BinDir) -ForegroundColor DarkGray
$script:ThisSessionId = Get-CurrentSessionId
$script:ConsoleSessionId = Get-ActiveConsoleSessionId
$script:ConsoleUser = Get-AutoLogonUser
Write-Host ("Session: this={0} console={1} user={2} (UI needs console desktop)" -f `
    $script:ThisSessionId, $script:ConsoleSessionId, $script:ConsoleUser) -ForegroundColor DarkGray
if (-not (Test-CanLaunchUiInCurrentSession)) {
    Write-Host 'NOTE: non-interactive / Session 0 host - ruleta/Godot will use interactive-task launch.' -ForegroundColor Yellow
}
Write-Host ''

$gcSvcPath = Join-Path $PSScriptRoot 'GoldClubServices.ps1'
if (-not (Test-Path -LiteralPath $gcSvcPath)) {
    Write-Host ("ERROR: missing {0}" -f $gcSvcPath) -ForegroundColor Red
    exit 1
}
. $gcSvcPath
if (-not $script:GoldClubServiceNames -or @($script:GoldClubServiceNames).Count -lt 1) {
    Write-Host 'ERROR: GoldClubServices.ps1 did not load (UTF-16?). Re-copy UTF-8 file beside this script.' -ForegroundColor Red
    exit 1
}
$serviceNames = @($script:GoldClubServiceNames)

# Printer/HW path depends on gateway listening before HWSubsys attaches drivers.
$script:HwBootstrapServiceNames = @(
    'GoldClub.Logging.LogDaemon',
    'GoldClub Serial Communication Gateway',
    'GoldClub Serial Communication Gateway SAS',
    'GoldClub Hardware Subsystem'
)

function Start-OneGcService {
    param(
        [Parameter(Mandatory = $true)][string] $NameOrDisplay,
        [System.Collections.Generic.List[object]] $Pending
    )
    $svc = Resolve-GoldClubService -NameOrDisplay $NameOrDisplay
    if (-not $svc) {
        Write-Host ("  missing skip: {0}" -f $NameOrDisplay) -ForegroundColor DarkYellow
        return $null
    }
    if ($svc.Status -eq 'Running') {
        Write-Host ("  status Running (verify later): {0}" -f $svc.Name) -ForegroundColor DarkGray
        return $svc
    }
    if ($WhatIf) {
        Write-Host ("  WhatIf: Start {0}" -f $svc.Name) -ForegroundColor Yellow
        return $svc
    }
    try {
        $svc.Start()
        if ($Pending) { [void]$Pending.Add($svc) }
        Write-Host ("  kick Start: {0}" -f $svc.Name) -ForegroundColor Cyan
    } catch {
        try { $svc.Refresh() } catch {}
        if ($svc.Status -eq 'StartPending' -or $svc.Status -eq 'Running') {
            if ($Pending) { [void]$Pending.Add($svc) }
            Write-Host ("  kick Start (pending): {0}" -f $svc.Name) -ForegroundColor Cyan
        } else {
            Write-Host ("  FAILED kick {0}: {1}" -f $svc.Name, $_.Exception.Message) -ForegroundColor Red
        }
    }
    return $svc
}

function Wait-GcServicesRunning {
    param(
        $Services,
        [int] $TimeoutSec,
        [string] $Label
    )
    # Accept List[object] / ServiceController[] / single service - never bind as [object[]]
    # (List[T] -> object[] throws "Argument types do not match" on some hosts).
    $want = @(
        foreach ($s in @($Services)) {
            if ($null -eq $s) { continue }
            if ($s -is [System.Collections.IEnumerable] -and -not ($s -is [string]) -and -not ($s.GetType().FullName -match 'ServiceController')) {
                foreach ($inner in $s) { if ($null -ne $inner) { $inner } }
            } else {
                $s
            }
        }
    )
    if ($WhatIf -or $want.Count -eq 0 -or $TimeoutSec -le 0) { return $true }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    do {
        $still = @()
        foreach ($s in $want) {
            try { $s.Refresh() } catch {}
            if ($s.Status -ne 'Running') { $still += $s }
        }
        if ($still.Count -eq 0) {
            Write-Host ("  {0}: all Running" -f $Label) -ForegroundColor Green
            return $true
        }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    foreach ($s in $want) {
        try { $s.Refresh() } catch {}
        if ($s.Status -eq 'Running') {
            Write-Host ("  now Running: {0}" -f $s.Name) -ForegroundColor Green
        } else {
            Write-Host ("  still {0}: {1}" -f $s.Status, $s.Name) -ForegroundColor Yellow
        }
    }
    return $false
}

function Stop-OneGcService {
    param([Parameter(Mandatory = $true)][string] $NameOrDisplay)
    $svc = Resolve-GoldClubService -NameOrDisplay $NameOrDisplay
    if (-not $svc) { return }
    try { $svc.Refresh() } catch {}
    if ($svc.Status -eq 'Stopped') {
        Write-Host ("  already Stopped: {0}" -f $svc.Name) -ForegroundColor DarkGray
        return
    }
    if ($WhatIf) {
        Write-Host ("  WhatIf: Stop {0}" -f $svc.Name) -ForegroundColor Yellow
        return
    }
    try {
        Write-Host ("  stop: {0}" -f $svc.Name) -ForegroundColor Yellow
        Stop-Service -InputObject $svc -Force -ErrorAction Stop
    } catch {
        try { $svc.Stop() } catch {
            Write-Host ("  WARN: stop {0}: {1}" -f $svc.Name, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    $deadline = (Get-Date).AddSeconds(20)
    do {
        try { $svc.Refresh() } catch {}
        if ($svc.Status -eq 'Stopped') { break }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
}

function Restart-HwBootstrapOrdered {
    Write-Host '  remediating: restart HW bootstrap in order ...' -ForegroundColor Yellow
    # Stop reverse dependency order (HW first), then start forward.
    $stopOrder = @(
        'GoldClub Hardware Subsystem',
        'GoldClub Serial Communication Gateway SAS',
        'GoldClub Serial Communication Gateway'
    )
    foreach ($sn in $stopOrder) { Stop-OneGcService -NameOrDisplay $sn }
    Start-Sleep -Seconds 2
    $started = New-Object System.Collections.Generic.List[object]
    foreach ($sn in $script:HwBootstrapServiceNames) {
        $svc = Start-OneGcService -NameOrDisplay $sn -Pending $null
        if ($svc) {
            [void]$started.Add($svc)
            if (-not $WhatIf -and $svc.Status -ne 'Running') {
                $null = Wait-GcServicesRunning -Services @($svc) -TimeoutSec ([Math]::Max(10, $ServiceWaitSec)) -Label $svc.Name
            }
        }
    }
    if (-not $WhatIf) {
        # ToArray() - wrapping List[object] in @() throws "Argument types do not match" on PS 5.1.
        $null = Wait-GcServicesRunning -Services $started.ToArray() -TimeoutSec ([Math]::Max(20, $ServiceWaitSec + 8)) -Label 'HW bootstrap after restart'
        Start-Sleep -Seconds 3
    }
    return $started.ToArray()
}

function Repair-FrozenGodotOnce {
    Write-Host '  remediating: stop frozen Godot and wait for ruleta respawn ...' -ForegroundColor Yellow
    $gods = @(Get-GodotUiProcesses)
    foreach ($g in $gods) {
        if ($WhatIf) {
            Write-Host ("  WhatIf: Stop-Process godot pid={0}" -f $g.Id) -ForegroundColor Yellow
            continue
        }
        try {
            Stop-Process -Id $g.Id -Force -ErrorAction Stop
            Write-Host ("  stopped Godot pid={0}" -f $g.Id) -ForegroundColor DarkYellow
        } catch {
            Write-Host ("  WARN: could not stop Godot pid={0}: {1}" -f $g.Id, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    if ($WhatIf) { return }
    $null = Wait-GodotUi -TimeoutSec ([Math]::Max(8, $GodotWaitSec)) -ConsoleOnly
    # Wait until freeze-check age so we do not false-fail a still-loading UI.
    $waitMore = [Math]::Max(8, $GodotFreezeCheckAgeSec + 2)
    Write-Host ("  waiting {0}s for Godot to leave splash / grow WorkingSet ..." -f $waitMore) -ForegroundColor DarkGray
    Start-Sleep -Seconds $waitMore
}

# --- 1) Services: ordered HW bootstrap, then the rest ---
Write-Host '1) GoldClub Windows services (ordered: LogDaemon -> CommCtrl -> SAS -> HWSubsys -> rest) ...' -ForegroundColor Cyan
$pending = New-Object System.Collections.Generic.List[object]
$bootstrapSvcs = New-Object System.Collections.Generic.List[object]
$already = 0
$kicked = 0

Write-Host '  1a) HW bootstrap (LogDaemon -> CommCtrl -> CommCtrl SAS -> HWSubsys) ...' -ForegroundColor DarkGray
foreach ($sn in $script:HwBootstrapServiceNames) {
    $before = Resolve-GoldClubService -NameOrDisplay $sn
    $wasRunning = ($before -and $before.Status -eq 'Running')
    $svc = Start-OneGcService -NameOrDisplay $sn -Pending $pending
    if ($svc) {
        [void]$bootstrapSvcs.Add($svc)
        if ($wasRunning) { $already++ } else { $kicked++ }
        # Wait for each bootstrap service before the next (printer TCP depends on gateway).
        if (-not $WhatIf -and $svc.Status -ne 'Running') {
            $null = Wait-GcServicesRunning -Services @($svc) -TimeoutSec ([Math]::Max(8, $ServiceWaitSec)) -Label $svc.Name
        }
    }
}
if (-not $WhatIf) {
    $null = Wait-GcServicesRunning -Services $bootstrapSvcs.ToArray() -TimeoutSec ([Math]::Max(15, $ServiceWaitSec)) -Label 'HW bootstrap'
}

Write-Host '  1b) remaining GoldClub services ...' -ForegroundColor DarkGray
$bootstrapSet = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($n in $script:HwBootstrapServiceNames) { [void]$bootstrapSet.Add($n) }
foreach ($sn in $serviceNames) {
    if ($bootstrapSet.Contains($sn)) { continue }
    $before = Resolve-GoldClubService -NameOrDisplay $sn
    $wasRunning = ($before -and $before.Status -eq 'Running')
    $svc = Start-OneGcService -NameOrDisplay $sn -Pending $pending
    if ($svc) {
        if ($wasRunning) { $already++ } else { $kicked++ }
    }
}
Write-Host ("  kicked={0} alreadyRunning={1} (Running alone is not success - health check follows)" -f $kicked, $already) -ForegroundColor DarkGray

# --- 2) nginx ---
Write-Host ''
Write-Host '2) nginx ...' -ForegroundColor Cyan
$nginxPs1 = Join-Path $BinDir 'Start-NgnixAndWatcher.ps1'
$nginxWatchers = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match '(?i)Start-NgnixAndWatcher')
})
if (Get-Process -Name 'nginx' -ErrorAction SilentlyContinue) {
    Write-Host '  nginx already running' -ForegroundColor Green
} elseif ($nginxWatchers.Count -gt 0) {
    Write-Host ("  nginx watcher already running pid={0}" -f ($nginxWatchers.ProcessId -join ',')) -ForegroundColor Green
} elseif ($WhatIf) {
    Write-Host '  WhatIf: would start nginx' -ForegroundColor Yellow
} elseif (Test-Path -LiteralPath $nginxPs1) {
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $nginxPs1, '-Silent') -WindowStyle Hidden
    Write-Host '  nginx watcher launched' -ForegroundColor Green
} else {
    Write-Host '  WARN: Start-NgnixAndWatcher.ps1 missing' -ForegroundColor Yellow
}

# --- 3) No HIH / leave shell suspended ---
Write-Host ''
Write-Host '3) HIH / shell ...' -ForegroundColor Cyan
Write-Host '  skip: do not start HIH.exe; leave shell.ps1 suspended (no HIH loop)' -ForegroundColor DarkGray
$hih = @(Get-NamedProcesses -Names @('HIH'))
if ($hih.Count -gt 0) {
    Write-Host ("  NOTE: HIH already running pid={0} (not started by this script)" -f (($hih | ForEach-Object Id) -join ',')) -ForegroundColor Yellow
}

# --- 4) ruleta owns Godot; ONLY after gateway+HW are up ---
Write-Host ''
Write-Host '4) Start ruleta (Godot via ruleta; after CommCtrl+HW) ...' -ForegroundColor Cyan
$goldclubRoot = Split-Path -Parent $BinDir
$compatHoldPath = Join-Path $goldclubRoot 'var\state\ruleta-compat-hold.json'
$script:RuletaCompatHold = $false
$script:RuletaCompatHoldReason = ''
if (Test-Path -LiteralPath $compatHoldPath) {
    $script:RuletaCompatHold = $true
    try {
        $holdObj = Get-Content -LiteralPath $compatHoldPath -Raw -ErrorAction Stop | ConvertFrom-Json
        $script:RuletaCompatHoldReason = ([string]$holdObj.reason) -replace '[^\x09\x0A\x0D\x20-\x7E]', '-'
    } catch {
        $script:RuletaCompatHoldReason = '10.1 exe vs 10.2 signed SAS paytable'
    }
    $pe = ''
    foreach ($n in @('Ruleta.exe', 'ruleta.exe')) {
        $p = Join-Path $RuletaDir $n
        if (Test-Path -LiteralPath $p) {
            try { $pe = [string][Diagnostics.FileVersionInfo]::GetVersionInfo($p).ProductVersion } catch {}
            break
        }
    }
    if ($pe -match '^10\.2(\.|$)') {
        Write-Host ("  HOLD ignored: Ruleta.exe is already {0} (hold was for 10.1 vs 10.2 SAS)" -f $pe) -ForegroundColor Yellow
        if ($pe -eq '10.2.0.0') {
            Write-Host '  WARN: PE 10.2.0.0 is the Downloads pack that previously silent-exited. Licensed 10.2.0.827 is still missing.' -ForegroundColor Yellow
        }
        try { Remove-Item -LiteralPath $compatHoldPath -Force -ErrorAction Stop } catch {}
        $script:RuletaCompatHold = $false
        $script:RuletaCompatHoldReason = ''
    } else {
        Write-Host '  HOLD: ruleta-compat-hold.json present - not launching Ruleta/Godot' -ForegroundColor Yellow
        if ($script:RuletaCompatHoldReason) {
            Write-Host ("  {0}" -f $script:RuletaCompatHoldReason) -ForegroundColor Yellow
        }
    }
}
if (-not $WhatIf) {
    $hwOk = Wait-GcServicesRunning -Services $bootstrapSvcs.ToArray() -TimeoutSec 5 -Label 'pre-ruleta HW check'
    if (-not $hwOk) {
        Write-Host '  WARN: CommCtrl/HWSubsys not all Running - ruleta may lock tito0 (TICKET NO ESTA LISTO)' -ForegroundColor Yellow
    }
}
$launch = Get-GodotLaunchInfo -XmlPath $GodotXml -RuletaRoot $RuletaDir
Write-Host ("  godot.exe: {0}" -f $launch.AppExe) -ForegroundColor DarkGray
Write-Host ("  main-pack: {0}" -f $launch.MainPack) -ForegroundColor DarkGray

$ruletaExe = $null
foreach ($cand in @(
        (Join-Path $RuletaDir 'ruleta.exe'),
        (Join-Path $RuletaDir 'Ruleta.exe')
    )) {
    if (Test-Path -LiteralPath $cand) { $ruletaExe = $cand; break }
}

# Drop Session-0 / wrong-session leftovers before start (keeps healthy console UI).
$cleared = Stop-WrongSessionUiProcesses -WhatIf:$WhatIf
if ($cleared -gt 0) {
    Write-Host ("  cleared {0} wrong-session ruleta/Godot process(es)" -f $cleared) -ForegroundColor Yellow
}

$ruletaProcs = @(Get-NamedProcesses -Names @('ruleta', 'Ruleta'))
$consoleSess = Get-ActiveConsoleSessionId
$ruletaOnConsole = @($ruletaProcs | Where-Object {
    $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
})
if ($ruletaOnConsole.Count -gt 0) {
    Write-Host ("  ruleta already running on console pid={0} Session={1} (will still require healthy Godot)" -f `
        (($ruletaOnConsole | ForEach-Object Id) -join ','),
        (($ruletaOnConsole | ForEach-Object SessionId) -join ',')) -ForegroundColor DarkGray
    $ruletaProcs = $ruletaOnConsole
} elseif ($script:RuletaCompatHold) {
    Write-Host '  skip: not starting ruleta.exe (compat hold)' -ForegroundColor DarkGray
} elseif (-not $ruletaExe) {
    Write-Host ("  ERROR: ruleta.exe not found under {0}" -f $RuletaDir) -ForegroundColor Red
} else {
    Write-Host ("  starting ruleta.exe: {0}" -f $ruletaExe) -ForegroundColor Cyan
    $mode = Start-UiProcessInConsole -FilePath $ruletaExe -WorkingDirectory $RuletaDir -Label 'ruleta.exe'
    if ($mode -eq 'failed') {
        Write-Host '  ERROR: could not start ruleta on console session' -ForegroundColor Red
    } elseif (-not $WhatIf) {
        Start-Sleep -Milliseconds 600
        $ruletaProcs = @(Get-NamedProcesses -Names @('ruleta', 'Ruleta') | Where-Object {
            $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
        })
        if ($ruletaProcs.Count -gt 0) {
            Write-Host ("  ruleta up pid={0} Session={1}" -f `
                (($ruletaProcs | ForEach-Object Id) -join ','),
                (($ruletaProcs | ForEach-Object SessionId) -join ',')) -ForegroundColor Green
        } else {
            Write-Host '  ERROR: ruleta not visible on console after launch' -ForegroundColor Red
        }
    }
}

if (-not $script:RuletaCompatHold -and $ruletaProcs.Count -gt 0) {
    $clear30 = Join-Path $PSScriptRoot 'Clear-Error30.ps1'
    if (-not (Test-Path -LiteralPath $clear30)) {
        $clear30 = 'D:\usb_scripts\roulette\Clear-Error30.ps1'
    }
    $hasPw = [bool]$env:RULETA_ERROR30_PASSWORD
    if (-not $hasPw) {
        foreach ($pwPath in @(
                'D:\usb_scripts\roulette\error30.password',
                'C:\goldclub\var\state\error30.password'
            )) {
            if (Test-Path -LiteralPath $pwPath) { $hasPw = $true; break }
        }
    }
    if ($hasPw -and (Test-Path -LiteralPath $clear30)) {
        Write-Host '  LLAVE auto-enter after ruleta start (ERROR 99/30) ...' -ForegroundColor DarkGray
        Start-Sleep -Seconds 10
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $clear30 -Auto
        Write-Host ("  Clear-Error30 -Auto exit={0}" -f $LASTEXITCODE) -ForegroundColor Cyan
    }
}

$gods = @()
if ($script:RuletaCompatHold) {
    Write-Host '  skip: Godot wait/fallback (compat hold)' -ForegroundColor DarkGray
} else {
    Write-Host ("  waiting up to {0}s for ruleta to spawn Godot on console ..." -f $GodotWaitSec) -ForegroundColor DarkGray
    $gods = @(Wait-GodotUi -TimeoutSec $GodotWaitSec -ConsoleOnly)
}
if ($script:RuletaCompatHold) {
    # held: do not spawn Godot
} elseif ($gods.Count -gt 0) {
    Write-Host ("  Godot from ruleta pid={0} Session={1}" -f `
        (($gods | ForEach-Object Id) -join ','),
        (($gods | ForEach-Object SessionId) -join ',')) -ForegroundColor Green
} elseif ($WhatIf) {
    Write-Host ("  WhatIf: fallback godot --main-pack {0}" -f $launch.MainPack) -ForegroundColor Yellow
} elseif (-not (Test-Path -LiteralPath $launch.AppExe)) {
    Write-Host ("  ERROR: missing {0}" -f $launch.AppExe) -ForegroundColor Red
} elseif (-not (Test-Path -LiteralPath $launch.MainPack)) {
    Write-Host ("  ERROR: missing main-pack {0}" -f $launch.MainPack) -ForegroundColor Red
} else {
    $null = Stop-WrongSessionUiProcesses
    $ruletaProcs = @(Get-NamedProcesses -Names @('ruleta', 'Ruleta') | Where-Object {
        $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
    })
    $args = [System.Collections.Generic.List[string]]::new()
    [void]$args.Add('--main-pack')
    [void]$args.Add($launch.MainPack)
    if ($ruletaProcs.Count -gt 0) {
        $pidRuleta = ($ruletaProcs | Sort-Object StartTime | Select-Object -First 1).Id
        [void]$args.Add(('--pid={0}' -f $pidRuleta))
    }
    [void]$args.Add('--verbose')
    [void]$args.Add(('--players={0}' -f $launch.Players))
    [void]$args.Add(('--windowposition={0}' -f $launch.Position))
    [void]$args.Add('--position')
    [void]$args.Add($launch.Position)
    [void]$args.Add(('--skin={0}' -f $launch.Skin))
    $argList = @($args)
    Write-Host '  fallback: starting godot.exe once (ruleta did not spawn UI) ...' -ForegroundColor Cyan
    $null = Start-UiProcessInConsole -FilePath $launch.AppExe -WorkingDirectory $launch.AppDir -ArgumentList $argList -Label 'godot.exe'
    $gods = @(Wait-GodotUi -TimeoutSec ([Math]::Max(5, [Math]::Min(8, $GodotWaitSec))) -ConsoleOnly)
    if ($gods.Count -gt 0) {
        Write-Host ("  Godot up pid={0} Session={1}" -f `
            (($gods | ForEach-Object Id) -join ','),
            (($gods | ForEach-Object SessionId) -join ',')) -ForegroundColor Green
    } else {
        Write-Host '  ERROR: Godot not visible on console after fallback start' -ForegroundColor Red
    }
}

Write-Host '  prune extra Godot (frozen splash) ...' -ForegroundColor DarkGray
$gods = @(Remove-ExtraGodotProcesses -WhatIf:$WhatIf)
if (-not $WhatIf -and $gods.Count -ge 1) {
    # Settle so freeze heuristics see real WorkingSet / title, not just process birth.
    $settle = [Math]::Max(5, [Math]::Min(12, $GodotFreezeCheckAgeSec))
    Write-Host ("  settling {0}s before Godot health probe ..." -f $settle) -ForegroundColor DarkGray
    Start-Sleep -Seconds $settle
}

# --- 5) Mandatory health verify (never "skip settle" into false OK) ---
Write-Host ''
Write-Host '5) Health verify (ports / HW connected / Godot not frozen) ...' -ForegroundColor Cyan
if ($WhatIf) {
    Write-Host '  WhatIf: skip live health probes' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Done (WhatIf).' -ForegroundColor Yellow
    exit 0
}

# Also wait briefly for any remaining kicked (non-bootstrap) services.
$restPending = @($pending | Where-Object {
    $n = $_.Name
    $d = $_.DisplayName
    -not (
        $n -match 'LogDaemon|Hardware Subsystem|Serial Communication' -or
        $d -match 'LogDaemon|Hardware Subsystem|Serial Communication'
    )
})
if ($restPending.Count -gt 0 -and $ServiceWaitSec -gt 0) {
    Write-Host '  waiting for remaining kicked services ...' -ForegroundColor DarkGray
    $null = Wait-GcServicesRunning -Services $restPending -TimeoutSec $ServiceWaitSec -Label 'remaining'
} else {
    Write-Host '  no remaining kicked services to wait on - running health probes' -ForegroundColor DarkGray
}

$report = Get-StackHealthReport -CriticalServiceNames $script:HwBootstrapServiceNames `
    -GodotMinHealthyMb $GodotMinHealthyMb -GodotFreezeCheckAgeSec $GodotFreezeCheckAgeSec
Write-StackHealthReport -Report $report

$hwOrPortFail = @($report.Failures | Where-Object {
    $_ -match 'port not listening|HW not connected|service not Running|missing service'
}).Count -gt 0
$godotFail = @($report.Failures | Where-Object {
    $_ -match 'Godot|frozen|splash|ruleta|Session 0|!= console Session'
}).Count -gt 0

if (-not $report.Ok -and $hwOrPortFail) {
    Write-Host ''
    Write-Host '  health FAIL on CommCtrl/HW - ordered restart of bootstrap ...' -ForegroundColor Yellow
    $bootstrapSvcs = New-Object System.Collections.Generic.List[object]
    foreach ($s in @(Restart-HwBootstrapOrdered)) { [void]$bootstrapSvcs.Add($s) }
    $report = Get-StackHealthReport -CriticalServiceNames $script:HwBootstrapServiceNames `
        -GodotMinHealthyMb $GodotMinHealthyMb -GodotFreezeCheckAgeSec $GodotFreezeCheckAgeSec
    Write-Host '  re-check after HW restart:' -ForegroundColor Cyan
    Write-StackHealthReport -Report $report
    $godotFail = @($report.Failures | Where-Object {
        $_ -match 'Godot|frozen|splash|ruleta|Session 0|!= console Session'
    }).Count -gt 0
}

if (-not $report.Ok -and $godotFail -and -not $script:RuletaCompatHold) {
    $sessionFail = @($report.Failures | Where-Object {
        $_ -match 'Session 0|!= console Session'
    }).Count -gt 0
    $frozenOrMissing = @($report.Failures | Where-Object {
        $_ -match 'frozen|splash|Godot UI missing|Godot UI not running|multiple Godot'
    }).Count -gt 0
    if ($sessionFail) {
        Write-Host ''
        Write-Host '  health FAIL: UI in wrong session - stop Session0 leftovers and relaunch on console ...' -ForegroundColor Yellow
        $null = Stop-WrongSessionUiProcesses
        if ($ruletaExe -and (Test-Path -LiteralPath $ruletaExe)) {
            Write-Host ("  restarting ruleta on console: {0}" -f $ruletaExe) -ForegroundColor Cyan
            $null = Start-UiProcessInConsole -FilePath $ruletaExe -WorkingDirectory $RuletaDir -Label 'ruleta.exe'
            $null = Wait-GodotUi -TimeoutSec ([Math]::Max(8, $GodotWaitSec)) -ConsoleOnly
            $settle = [Math]::Max(5, [Math]::Min(12, $GodotFreezeCheckAgeSec))
            Start-Sleep -Seconds $settle
        }
        $null = Remove-ExtraGodotProcesses
        $report = Get-StackHealthReport -CriticalServiceNames $script:HwBootstrapServiceNames `
            -GodotMinHealthyMb $GodotMinHealthyMb -GodotFreezeCheckAgeSec $GodotFreezeCheckAgeSec
        Write-Host '  re-check after session repair:' -ForegroundColor Cyan
        Write-StackHealthReport -Report $report
        $godotFail = @($report.Failures | Where-Object {
            $_ -match 'Godot|frozen|splash|ruleta|Session 0|!= console Session'
        }).Count -gt 0
        $frozenOrMissing = @($report.Failures | Where-Object {
            $_ -match 'frozen|splash|Godot UI missing|Godot UI not running|multiple Godot'
        }).Count -gt 0
    }
    if ($frozenOrMissing -and -not $report.Ok) {
        Write-Host ''
        Repair-FrozenGodotOnce
        $consoleSess = Get-ActiveConsoleSessionId
        $gods = @(Get-GodotUiProcesses | Where-Object {
            $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
        })
        $rpOk = @(Get-NamedProcesses -Names @('ruleta', 'Ruleta') | Where-Object {
            $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
        })
        if ($gods.Count -eq 0 -and $ruletaExe -and $rpOk.Count -gt 0) {
            $clear30 = Join-Path $PSScriptRoot 'Clear-Error30.ps1'
            if (-not (Test-Path -LiteralPath $clear30)) {
                $clear30 = 'D:\usb_scripts\roulette\Clear-Error30.ps1'
            }
            if (Test-Path -LiteralPath $clear30) {
                Write-Host '  ruleta up, Godot gone - ERROR 30 auto-clear (no fallback Godot on LLAVE) ...' -ForegroundColor Yellow
                $script:Error30Relocked = $true
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $clear30 -Auto
                Write-Host ("  Clear-Error30 -Auto exit={0}" -f $LASTEXITCODE) -ForegroundColor Cyan
                if ($LASTEXITCODE -eq 5) {
                    Write-Host '  ERROR 30 re-locked: 10.2.0.0 beta trial date, not leftover licence.' -ForegroundColor Yellow
                }
                $report = Get-StackHealthReport -CriticalServiceNames $script:HwBootstrapServiceNames `
                    -GodotMinHealthyMb $GodotMinHealthyMb -GodotFreezeCheckAgeSec $GodotFreezeCheckAgeSec
                Write-Host '  re-check after ERROR 30 auto-clear:' -ForegroundColor Cyan
                Write-StackHealthReport -Report $report
            }
        }
        $gods = @(Get-GodotUiProcesses | Where-Object {
            $_.SessionId -gt 0 -and ($consoleSess -le 0 -or $_.SessionId -eq $consoleSess)
        })
        if ($gods.Count -eq 0 -and $ruletaExe -and $rpOk.Count -gt 0 -and -not $report.Ok -and -not $script:Error30Relocked) {
            Write-Host '  ruleta still up but no Godot - fallback godot start ...' -ForegroundColor Yellow
            if ((Test-Path -LiteralPath $launch.AppExe) -and (Test-Path -LiteralPath $launch.MainPack)) {
                $args = [System.Collections.Generic.List[string]]::new()
                [void]$args.Add('--main-pack'); [void]$args.Add($launch.MainPack)
                [void]$args.Add(('--pid={0}' -f ($rpOk | Sort-Object StartTime | Select-Object -First 1).Id))
                [void]$args.Add('--verbose')
                [void]$args.Add(('--players={0}' -f $launch.Players))
                [void]$args.Add(('--windowposition={0}' -f $launch.Position))
                [void]$args.Add('--position'); [void]$args.Add($launch.Position)
                [void]$args.Add(('--skin={0}' -f $launch.Skin))
                $null = Start-UiProcessInConsole -FilePath $launch.AppExe -WorkingDirectory $launch.AppDir -ArgumentList @($args) -Label 'godot.exe'
                $null = Wait-GodotUi -TimeoutSec 8
                Start-Sleep -Seconds 3
            }
        }
        $null = Remove-ExtraGodotProcesses
        $report = Get-StackHealthReport -CriticalServiceNames $script:HwBootstrapServiceNames `
            -GodotMinHealthyMb $GodotMinHealthyMb -GodotFreezeCheckAgeSec $GodotFreezeCheckAgeSec
        Write-Host '  re-check after Godot repair:' -ForegroundColor Cyan
        Write-StackHealthReport -Report $report
    }
}

Write-Host ''
if ($report.Ok) {
    if ($script:RuletaCompatHold) {
        Write-Host 'Done. HW stack HEALTHY. Ruleta/Godot held (need licensed 10.2).' -ForegroundColor Yellow
        if ($script:RuletaCompatHoldReason) {
            Write-Host ("  hold: {0}" -f $script:RuletaCompatHoldReason) -ForegroundColor Yellow
        }
        Write-Host 'Hint: 10.1 dies on PutRemoteThemeAndCombo while signed SAS still has 10.2 paytable ids. Need matching licensed 10.2 (not trial 684) or GoldClub-signed DeviceManager with paytable_double_zero.' -ForegroundColor DarkGray
        exit 0
    }
    Write-Host ("Done. Stack HEALTHY (godot={0} ruleta={1}; CommCtrl ports + HW connected verified)." -f $report.Godot, $report.Ruleta) -ForegroundColor Green
    exit 0
}

Write-Host 'Done with ERRORS: stack NOT healthy (refusing false OK).' -ForegroundColor Red
foreach ($f in @($report.Failures)) {
    Write-Host ("  - {0}" -f $f) -ForegroundColor Red
}
if ($script:RuletaCompatHold) {
    Write-Host ("  - Ruleta start held: {0}" -f $script:RuletaCompatHoldReason) -ForegroundColor Yellow
    Write-Host 'Hint: 10.1 dies on PutRemoteThemeAndCombo while signed SAS still has 10.2 paytable ids. Need matching licensed 10.2 (not trial 684) or GoldClub-signed DeviceManager with paytable_double_zero.' -ForegroundColor Yellow
} elseif ($script:Error30Relocked) {
    Write-Host 'Hint: ERROR 30 is the 10.2.0.0 beta trial date lock (licence was accepted). Need licensed 10.2.0.827.' -ForegroundColor Yellow
} else {
    Write-Host 'Hint: fix HW/CommCtrl first, or Kill-All then re-run Run-FullStack.' -ForegroundColor Yellow
}
Write-Host 'Hint: from WinRM, this script auto-uses interactive-task for ruleta/Godot (Session 0 cannot host UI).' -ForegroundColor Yellow
exit 1
