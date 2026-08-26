<#
.SYNOPSIS
    Fast WinRM-native KeyCtrl Dallas splice (roulette :30300/ruleta or Slot :30800/OneHand).

.DESCRIPTION
    Default (inject/insert): inject 700/701/ROM, wait for a fresh KEY=admin IN
    (menu login). Drain finishes in the background so WinDivert does not hold
    the keyboard link for tens of seconds (that flashes KEYBOARD TIMEOUT and
    drops the service menu).

    Guest handpay is cleared by the same admin insert. Keyboard disconnect clears
    the menu even when no KEY=OUT is logged — do not treat stale admin IN as
    UiReady. Use -Action roundtrip only when you need an explicit eject first.

.EXAMPLE
    .\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90
.EXAMPLE
    .\Invoke-DallasSpliceRouletteRemote.ps1 -ComputerName 10.0.0.90 -Action roundtrip
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [ValidateSet('passthru', 'capture', 'inject', 'heal', 'billinject')]
    [string] $Mode = 'inject',
    [ValidateSet('insert', 'eject', 'roundtrip')]
    [string] $Action = 'insert',
    [int] $RunSeconds = 40,
    [int] $InjectAfterMs = 0,
    [int] $InitialDelta = 0,
    [int] $DrainReserveSec = 35,
    # Insert context (700/701) + ROM. ROM-only often yields Injected=True but no KEY=admin.
    [string] $Rom = '700|||701|||01D68A721B000019',
    [string] $EjectRom = '700|||701|||655',
    [int] $ServerPort = 30300,
    [string] $TargetProcess = 'ruleta',
    # Seconds to wait for KEY=admin IN after inject (keep short — handpay clear often needs a 2nd run).
    [int] $HitTimeoutSec = 4,
    [int] $GateTimeoutSec = 60,
    # After inject during handpay, wait for HANDPAY CANCEL / unlock (not another KEY wait).
    [int] $HandpayClearTimeoutSec = 4,
    # After KEY=admin IN, wait for keyboard reconnect (divert flash).
    [int] $KeyboardRecoverSec = 8,
    [switch] $WaitForDrain,
    [switch] $WaitForDallas,
    [switch] $SkipGate,
    # Insert-only; same as default Action=insert (kept for callers).
    [switch] $Fast,
    # Emit full Status/Log + return the remote object (old noisy output).
    [switch] $PassThru,
    [string] $WinDivertDir = 'C:\Tools\WinDivert\x64',
    [string] $ExePath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $ExePath) { $ExePath = Join-Path $repo 'probes\DallasSpliceRoulette.exe' }

$labAccess = Join-Path $repo 'LabAccess.ps1'
if (-not (Test-Path $labAccess)) { $labAccess = 'C:\Users\Ezbogar\GoldclubLogInvestigator\LabAccess.ps1' }
. $labAccess

$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
foreach ($f in @($dll, $sys, $ExePath)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}

# Default: wait for fresh KEY=admin IN; background-drain (avoid long KEYBOARD TIMEOUT).
$effectiveWaitDrain = [bool]$WaitForDrain
$effectiveWaitDallas = [bool]$WaitForDallas
$effectiveAction = $Action
if ($Mode -eq 'inject' -and -not $Fast) {
    if (-not $PSBoundParameters.ContainsKey('WaitForDallas')) { $effectiveWaitDallas = $true }
    # Only wait full drain when caller asks (-WaitForDrain). Long divert = menu drop.
    if (-not $PSBoundParameters.ContainsKey('WaitForDrain')) { $effectiveWaitDrain = $false }
}
if ($Mode -ne 'inject') { $effectiveAction = 'insert' }
if ($Mode -eq 'billinject') { $effectiveWaitDallas = $false; $effectiveWaitDrain = $false; $Fast = $true }
if ($Fast -and $Mode -eq 'inject') { $effectiveAction = 'insert' }

$swAll = [Diagnostics.Stopwatch]::StartNew()
$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\wd_roulette"
New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null

function Copy-IfChanged([string]$Src, [string]$DestDir) {
    $name = Split-Path $Src -Leaf
    $dest = Join-Path $DestDir $name
    if (Test-Path $dest) {
        $a = Get-Item -LiteralPath $Src
        $b = Get-Item -LiteralPath $dest
        if ($a.Length -eq $b.Length -and $a.LastWriteTimeUtc -le $b.LastWriteTimeUtc.AddSeconds(1)) { return $false }
    }
    Copy-Item -LiteralPath $Src -Destination $dest -Force
    return $true
}

$copied = @()
foreach ($f in @($dll, $sys, $ExePath)) {
    if (Copy-IfChanged $f $remoteDirUnc) { $copied += (Split-Path $f -Leaf) }
}
if ($copied.Count) { Write-Host ("Staged ({0}): {1}" -f $copied.Count, ($copied -join ', ')) -ForegroundColor Green }
else { Write-Host 'Stage OK (cached)' -ForegroundColor DarkGray }

if ($PassThru -or $VerbosePreference -ne 'SilentlyContinue') {
    Write-Host ("Roulette DallasSplice  mode={0} action={1}  :{2} -> {3}" -f $Mode, $effectiveAction, $ServerPort, $TargetProcess) -ForegroundColor Cyan
}

$th = ''
try { $th = [string](Get-Item WSMan:\localhost\Client\TrustedHosts -EA Stop).Value } catch {}
if ($th -ne '*' -and ($th -split ',' | ForEach-Object { $_.Trim() }) -notcontains $ComputerName) {
    $null = Initialize-LabWinRmTrustedHosts -Ip @($ComputerName)
}

$timeoutMs = [Math]::Max(120000, ($GateTimeoutSec + $HitTimeoutSec + $HandpayClearTimeoutSec + $KeyboardRecoverSec + $RunSeconds + 90) * 1000)
if ($effectiveAction -eq 'roundtrip') {
    $timeoutMs = [Math]::Max($timeoutMs, ($GateTimeoutSec + $HandpayClearTimeoutSec + $KeyboardRecoverSec + 2 * $RunSeconds + 120) * 1000)
}

$opt = New-PSSessionOption -OpenTimeout 4000 -OperationTimeout $timeoutMs -CancelTimeout 4000
$session = New-PSSession -ComputerName $ComputerName -Credential (Get-LabCredential -ComputerName $ComputerName) `
    -Authentication Default -SessionOption $opt -ErrorAction Stop

try {
    $result = Invoke-Command -Session $session -ScriptBlock {
        param($Mode, $RunSeconds, $InjectAfterMs, $Rom, $EjectRom, $InitD, $DrainR, $ServerPort, $ForceName, $HitTimeoutSec, $GateTimeoutSec, $HandpayClearTimeoutSec, $KeyboardRecoverSec, $WaitForDrain, $WaitForDallas, $SkipGate, $Action)

        $ErrorActionPreference = 'Continue'
        $wd = 'C:\Windows\Temp\wd_roulette'
        $exe = Join-Path $wd 'DallasSpliceRoulette.exe'
        $status = Join-Path $wd 'splice_status.txt'
        $log = Join-Path $wd 'splice.log'
        $err = Join-Path $wd 'splice.err'
        $live = Join-Path $wd 'splice_live.log'
        $flag = Join-Path $wd 'inject.flag'
        $hitFlag = Join-Path $wd 'dallas_hit.flag'
        Remove-Item $log, $err, $hitFlag, $flag, $live -Force -EA SilentlyContinue

        function Get-RuletaLogPath {
            Get-ChildItem 'C:\goldclub\var\log\ruleta Roulette' -Filter '*.log' -EA SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
        }

        function Read-RuletaTail([string]$Path, [int]$MaxBytes = 120000) {
            if (-not $Path -or -not (Test-Path $Path)) { return '' }
            $fs = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                $len = $fs.Length
                $start = [Math]::Max(0L, $len - [int64]$MaxBytes)
                $fs.Position = $start
                return (New-Object IO.StreamReader($fs)).ReadToEnd()
            } finally { $fs.Dispose() }
        }

        function Get-RuletaGateState([string]$Path) {
            $tail = Read-RuletaTail $Path
            $handpayLocked = $false
            $kbDown = $false
            $adminIn = $false
            foreach ($line in ($tail -split "`r?`n")) {
                # Match unlock BEFORE lock — "unlock of type lmt_..." contains "lock of type lmt_...".
                if ($line -match 'unlock of type lmt_handpay_processing') { $handpayLocked = $false }
                elseif ($line -match 'lock of type lmt_handpay_processing') { $handpayLocked = $true }
                elseif ($line -match 'HANDPAY CANCEL|Handpay saved') { $handpayLocked = $false }
                elseif ($line -match 'HANDPAY REQUEST|Handpay request of') { $handpayLocked = $true }
                # KEYBOARD TIMEOUT / disconnect drops the service menu even without KEY=OUT.
                if ($line -match 'keyboard reconnected|unlock of type lmt_keyboard_disconnected') { $kbDown = $false }
                elseif ($line -match 'WARN .* keyboard disconnected|lock of type lmt_keyboard_disconnected|KB ping timeout') {
                    $kbDown = $true
                    $adminIn = $false
                }
                if ($line -match 'KEY = admin.*EVENT = IN') { $adminIn = $true }
                elseif ($line -match 'KEY = admin.*EVENT = OUT') { $adminIn = $false }
            }
            # HANDPAY_LOCK is soft: admin Dallas insert cancels/clears it.
            # KEYBOARD_DOWN is hard: splice cannot reconnect the keyboard.
            $soft = @()
            $hard = @()
            if ($handpayLocked) { $soft += 'HANDPAY_LOCK' }
            if ($kbDown) { $hard += 'KEYBOARD_DOWN' }
            $blockers = @($soft + $hard)
            [pscustomobject]@{
                HandpayLocked = $handpayLocked
                KeyboardDown  = $kbDown
                AdminKeyIn    = $adminIn
                SoftBlockers  = $soft
                HardBlockers  = $hard
                Blockers      = $blockers
                BlockerText   = ($blockers -join ',')
            }
        }

        function Clear-SpliceLeftovers {
            $had = [bool](Get-Process -Name 'DallasSpliceRoulette' -EA SilentlyContinue)
            Get-Process -Name 'DallasSpliceRoulette' -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
            Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -EA SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine -match 'drain_watchdog\.ps1' } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
            if ($had) {
                sc.exe stop WinDivert 2>$null | Out-Null
                sc.exe delete WinDivert 2>$null | Out-Null
                Start-Sleep -Milliseconds 800
            }
        }

        function Get-RuletaTarget([int]$Port, [string]$ForceName) {
            $allRuleta = @(Get-Process -Name 'ruleta' -EA SilentlyContinue | Sort-Object StartTime)
            if ($allRuleta.Count -gt 1) {
                foreach ($r in $allRuleta[0..($allRuleta.Count - 2)]) {
                    Get-CimInstance Win32_Process -Filter "Name='godot.exe'" -EA SilentlyContinue | Where-Object {
                        $_.ParentProcessId -eq $r.Id -or ($_.CommandLine -and $_.CommandLine -match ("--pid={0}" -f $r.Id))
                    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }
                    Stop-Process -Id $r.Id -Force -EA SilentlyContinue
                }
                Start-Sleep -Milliseconds 500
            }

            $procMap = @{}
            $startMap = @{}
            $names = @($ForceName, 'ruleta', 'OneHand') | Where-Object { $_ } | Select-Object -Unique
            Get-Process -Name $names -EA SilentlyContinue | ForEach-Object {
                $procMap[[string]$_.Id] = $_.ProcessName
                $startMap[[string]$_.Id] = $_.StartTime
            }
            $rows = @()
            foreach ($line in (netstat -ano -p tcp)) {
                if ($line -notmatch 'ESTABLISHED') { continue }
                if ($line -notmatch [regex]::Escape(":$Port")) { continue }
                $parts = ($line -split '\s+' | Where-Object { $_ })
                if ($parts.Count -lt 5) { continue }
                $local = $parts[1]; $remote = $parts[2]; $pidStr = $parts[4]
                if ($remote -notmatch ":$Port$") { continue }
                if (-not $procMap.ContainsKey($pidStr)) { continue }
                $lp = 0
                if ($local -match ':(\d+)$') { $lp = [int]$Matches[1] }
                $st = if ($startMap.ContainsKey($pidStr)) { $startMap[$pidStr] } else { [datetime]::MinValue }
                $rows += [pscustomobject]@{ LocalPort = $lp; PID = [int]$pidStr; Name = $procMap[$pidStr]; StartTime = $st }
            }
            $prefer = @()
            if ($ForceName) { $prefer += $ForceName }
            $prefer += @('OneHand', 'ruleta')
            $target = $null
            foreach ($name in ($prefer | Select-Object -Unique)) {
                $target = $rows | Where-Object { $_.Name -ieq $name } |
                    Sort-Object StartTime -Descending | Select-Object -First 1
                if ($target) { break }
            }
            if (-not $target) { $target = $rows | Sort-Object StartTime -Descending | Select-Object -First 1 }
            [pscustomobject]@{ Target = $target; Rows = $rows }
        }

        function Invoke-OneSplice {
            param(
                [string]$Payload,
                [int]$Ephem,
                [string]$SpliceMode,
                [bool]$WaitDrain,
                [bool]$WaitDallasHit,
                [string]$ExpectEvent,
                [long]$LogOffset
            )
            Remove-Item $log, $err, $flag, $live, $hitFlag -Force -EA SilentlyContinue
            # Quote payload so lines like "821 27|||807" stay a single argv token for the exe.
            $quotedPayload = if ($Payload -match '\s') { "`"$Payload`"" } else { $Payload }
            $argList = @("$Ephem", $SpliceMode, "$RunSeconds", "$InjectAfterMs", $quotedPayload, "$InitD", "$DrainR", "$ServerPort")
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $null = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $wd `
                -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -WindowStyle Hidden

            function Read-NewEvents {
                if (-not $rf -or -not (Test-Path $rf)) { return @() }
                $fs = [IO.File]::Open($rf, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                try {
                    if ($fs.Length -le $LogOffset) { return @() }
                    $fs.Position = $LogOffset
                    $tail = (New-Object IO.StreamReader($fs)).ReadToEnd()
                    return @([regex]::Matches($tail, 'KEY = admin[^\r\n]*EVENT = (?:IN|OUT)[^\r\n]*') | ForEach-Object { $_.Value })
                } finally { $fs.Dispose() }
            }
            function Test-InjectedLocal {
                if (Test-Path -LiteralPath $flag) { return $true }
                if (-not (Test-Path -LiteralPath $live)) { return $false }
                try {
                    $fs = [IO.File]::Open($live, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                    try { return [bool]((New-Object IO.StreamReader($fs)).ReadToEnd() -match 'INJECTED ROM') }
                    finally { $fs.Dispose() }
                } catch { return $false }
            }

            $dallasHit = $false
            $injected = $false
            $newDallas = @()
            $fast = ($SpliceMode -eq 'inject') -and (-not $WaitDrain)
            # Fast+KEY: short wait for admin IN. Fast bill/force inject (no KEY wait): stay up for
            # RunSeconds so idle KeyCtrl can deliver an S2C template before we give up.
            if ($fast -and $WaitDallasHit) {
                $deadline = (Get-Date).AddSeconds([Math]::Min(10, $HitTimeoutSec))
            } elseif ($fast) {
                $deadline = (Get-Date).AddSeconds([Math]::Max(8, $RunSeconds) + 2)
            } else {
                $deadline = (Get-Date).AddSeconds($RunSeconds + 8)
            }

            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 40
                if (-not $injected) { $injected = Test-InjectedLocal }
                if ($WaitDallasHit -or -not $fast) {
                    $ev = Read-NewEvents
                    if ($ev.Count -gt 0) {
                        if ($ExpectEvent) {
                            $match = @($ev | Where-Object { $_ -match ("EVENT = $ExpectEvent") })
                            if ($match.Count -gt 0) { $newDallas = $match; $dallasHit = $true }
                        } else {
                            $newDallas = $ev; $dallasHit = $true
                        }
                        if ($dallasHit) { Set-Content $hitFlag ($newDallas -join [Environment]::NewLine) }
                    }
                }
                if ($fast -and $injected) {
                    if ($WaitDallasHit -and -not $dallasHit) {
                        $extra = (Get-Date).AddSeconds([Math]::Max(2, $HitTimeoutSec))
                        while ((Get-Date) -lt $extra -and -not $dallasHit) {
                            Start-Sleep -Milliseconds 60
                            $ev = Read-NewEvents
                            if ($ExpectEvent) { $ev = @($ev | Where-Object { $_ -match ("EVENT = $ExpectEvent") }) }
                            if ($ev.Count -gt 0) {
                                $newDallas = $ev
                                $dallasHit = $true
                                Set-Content $hitFlag ($newDallas -join [Environment]::NewLine)
                            }
                        }
                    } else {
                        $extra = (Get-Date).AddMilliseconds(500)
                        while ((Get-Date) -lt $extra -and -not $dallasHit) {
                            Start-Sleep -Milliseconds 40
                            $ev = Read-NewEvents
                            if ($ExpectEvent) { $ev = @($ev | Where-Object { $_ -match ("EVENT = $ExpectEvent") }) }
                            if ($ev.Count -gt 0) { $newDallas = $ev; $dallasHit = $true }
                        }
                    }
                    break
                }
                if (-not $fast -and -not (Get-Process -Name 'DallasSpliceRoulette' -EA SilentlyContinue)) { break }
                # Force-inject path: keep waiting for INJECTED ROM; do not early-out on silence.
                if ($fast -and -not $WaitDallasHit -and -not (Get-Process -Name 'DallasSpliceRoulette' -EA SilentlyContinue)) { break }
            }

            $backgroundDrain = $false
            if ($fast -and (Get-Process -Name 'DallasSpliceRoulette' -EA SilentlyContinue)) {
                $backgroundDrain = $true
                $cleanupPath = Join-Path $wd 'drain_watchdog.ps1'
                $timeout = $RunSeconds + 30
                @(
                    "`$ErrorActionPreference='SilentlyContinue'"
                    "Wait-Process -Name DallasSpliceRoulette -Timeout $timeout -EA SilentlyContinue"
                    'Get-Process DallasSpliceRoulette -EA SilentlyContinue | Stop-Process -Force'
                    'Start-Sleep -Milliseconds 300'
                    'sc.exe stop WinDivert | Out-Null'
                    'sc.exe delete WinDivert | Out-Null'
                ) -join "`r`n" | Set-Content $cleanupPath -Encoding ASCII
                Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $cleanupPath) -WindowStyle Hidden | Out-Null
            } elseif (-not $fast) {
                $d2 = (Get-Date).AddSeconds([Math]::Max(1, $RunSeconds + 5 - [int]$sw.Elapsed.TotalSeconds))
                while ((Get-Date) -lt $d2 -and (Get-Process DallasSpliceRoulette -EA SilentlyContinue)) { Start-Sleep -Milliseconds 300 }
                Get-Process DallasSpliceRoulette -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
                sc.exe stop WinDivert 2>$null | Out-Null
                sc.exe delete WinDivert 2>$null | Out-Null
            }

            if (-not $dallasHit) {
                $ev = Read-NewEvents
                if ($ExpectEvent) { $ev = @($ev | Where-Object { $_ -match ("EVENT = $ExpectEvent") }) }
                $newDallas = $ev
                $dallasHit = $newDallas.Count -gt 0
            }
            if (-not $injected) { $injected = Test-InjectedLocal }

            $logText = ''
            if (Test-Path $live) {
                try {
                    $fs = [IO.File]::Open($live, 'Open', 'Read', 'ReadWrite')
                    $logText = (New-Object IO.StreamReader($fs)).ReadToEnd()
                    $fs.Dispose()
                } catch { $logText = Get-Content $live -Raw -EA SilentlyContinue }
            } elseif (Test-Path $log) { $logText = Get-Content $log -Raw -EA SilentlyContinue }
            $errText = if (Test-Path $err) { Get-Content $err -Raw -EA SilentlyContinue } else { '' }

            [pscustomobject]@{
                DallasHit       = $dallasHit
                NewDallas       = ($newDallas -join "`n")
                Injected        = $injected
                BackgroundDrain = $backgroundDrain
                ElapsedMs       = [int]$sw.ElapsedMilliseconds
                Log             = $logText
                Err             = $errText
            }
        }

        $rf = Get-RuletaLogPath
        $statusLines = New-Object System.Collections.Generic.List[string]
        Clear-SpliceLeftovers

        $sel = Get-RuletaTarget -Port $ServerPort -ForceName $ForceName
        $target = $sel.Target
        $rows = $sel.Rows
        [void]$statusLines.Add('CONSUMERS:')
        foreach ($r in ($rows | Sort-Object StartTime -Descending)) {
            [void]$statusLines.Add(('  port={0} pid={1} name={2} start={3:HH:mm:ss}' -f $r.LocalPort, $r.PID, $r.Name, $r.StartTime))
        }
        if (-not $target) {
            [void]$statusLines.Add('NO_TARGET')
            Set-Content $status ($statusLines -join [Environment]::NewLine)
            return [pscustomobject]@{
                Status = ($statusLines -join [Environment]::NewLine); Log = ''; Err = 'NO_TARGET'
                DallasHit = $false; NewDallas = ''; Ephem = 0; ElapsedMs = 0
                Injected = $false; BackgroundDrain = $false; IsAdmin = $false
                Action = $Action; Blocker = 'NO_TARGET'; UiReady = $false; Ejected = $false
            }
        }

        $ephem = [int]$target.LocalPort
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        [void]$statusLines.Add(('TARGET: port={0} pid={1} name={2} (newest)' -f $target.LocalPort, $target.PID, $target.Name))
        $waitDrain = [bool]($WaitForDrain -eq $true -or "$WaitForDrain" -eq 'True' -or "$WaitForDrain" -eq '1')
        $waitDallas = [bool]($WaitForDallas -eq $true -or "$WaitForDallas" -eq 'True' -or "$WaitForDallas" -eq '1')
        $doSkipGate = [bool]($SkipGate -eq $true -or "$SkipGate" -eq 'True' -or "$SkipGate" -eq '1')
        [void]$statusLines.Add(('MODE={0} ACTION={1} SERVER={2} WAIT_DRAIN={3}' -f $Mode, $Action, $ServerPort, [int]$waitDrain))
        [void]$statusLines.Add(('IS_ADMIN: {0}' -f $isAdmin))
        if (-not $isAdmin) { throw 'WinRM identity is not admin' }

        $gate = Get-RuletaGateState $rf
        [void]$statusLines.Add(('GATE handpay={0} kbDown={1} adminIn={2} soft={3} hard={4}' -f `
            $gate.HandpayLocked, $gate.KeyboardDown, $gate.AdminKeyIn,
            (($gate.SoftBlockers) -join ','), (($gate.HardBlockers) -join ',')))
        if ($gate.HandpayLocked -and $Mode -eq 'inject') {
            [void]$statusLines.Add('HANDPAY_CLEAR_VIA_DALLAS: inject will cancel/clear guest handpay')
        }

        # Hard gate only (keyboard down). Handpay is cleared by the inject itself.
        if ($Mode -eq 'inject' -and -not $doSkipGate -and $gate.HardBlockers.Count -gt 0) {
            [void]$statusLines.Add(('GATE_WAIT upTo={0}s for {1}' -f $GateTimeoutSec, (($gate.HardBlockers) -join ',')))
            Set-Content $status ($statusLines -join [Environment]::NewLine)
            $deadline = (Get-Date).AddSeconds($GateTimeoutSec)
            while ((Get-Date) -lt $deadline) {
                Start-Sleep -Milliseconds 500
                $gate = Get-RuletaGateState $rf
                if ($gate.HardBlockers.Count -eq 0) { break }
            }
            [void]$statusLines.Add(('GATE_AFTER handpay={0} kbDown={1} hard={2}' -f `
                $gate.HandpayLocked, $gate.KeyboardDown, (($gate.HardBlockers) -join ',')))
            if ($gate.HardBlockers.Count -gt 0) {
                Set-Content $status ($statusLines -join [Environment]::NewLine)
                return [pscustomobject]@{
                    Status = ($statusLines -join [Environment]::NewLine); Log = ''
                    Err = 'BLOCKED: ' + (($gate.HardBlockers) -join ',')
                    DallasHit = $false; NewDallas = ''; Ephem = $ephem; ElapsedMs = 0
                    Injected = $false; BackgroundDrain = $false; IsAdmin = $isAdmin
                    Action = $Action; Blocker = (($gate.HardBlockers) -join ','); UiReady = $false; Ejected = $false
                }
            }
        }

        $swTotal = [Diagnostics.Stopwatch]::StartNew()
        $ejected = $false
        $handpayCleared = $false
        $handpayAtStart = [bool]$gate.HandpayLocked
        $phaseLog = ''
        $last = $null

        function Test-HandpayClearedSince([string]$Path, [long]$Offset) {
            if (-not $Path -or -not (Test-Path $Path)) { return $false }
            $fs = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                if ($fs.Length -le $Offset) { return $false }
                $fs.Position = $Offset
                $chunk = (New-Object IO.StreamReader($fs)).ReadToEnd()
                return [bool]($chunk -match 'HANDPAY CANCEL|unlock of type lmt_handpay_processing|Handpay saved')
            } finally { $fs.Dispose() }
        }

        function Read-AdminKeyEventsSince([string]$Path, [long]$Offset, [string]$ExpectEvent) {
            if (-not $Path -or -not (Test-Path $Path)) { return @() }
            $fs = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            try {
                if ($fs.Length -le $Offset) { return @() }
                $fs.Position = $Offset
                $tail = (New-Object IO.StreamReader($fs)).ReadToEnd()
                $all = @([regex]::Matches($tail, 'KEY = admin[^\r\n]*EVENT = (?:IN|OUT)[^\r\n]*') | ForEach-Object { $_.Value })
                if ($ExpectEvent) { return @($all | Where-Object { $_ -match ("EVENT = $ExpectEvent") }) }
                return $all
            } finally { $fs.Dispose() }
        }

        function Invoke-EjectPhase([bool]$WaitOut) {
            $offE = if ($rf -and (Test-Path $rf)) { (Get-Item $rf).Length } else { 0L }
            Clear-SpliceLeftovers
            $selE = Get-RuletaTarget -Port $ServerPort -ForceName $ForceName
            if (-not $selE.Target) { return $null }
            $epE = [int]$selE.Target.LocalPort
            $ej = Invoke-OneSplice -Payload $EjectRom -Ephem $epE -SpliceMode 'inject' `
                -WaitDrain $false -WaitDallasHit $WaitOut -ExpectEvent 'OUT' -LogOffset $offE
            Start-Sleep -Milliseconds 700
            Clear-SpliceLeftovers
            $selE = Get-RuletaTarget -Port $ServerPort -ForceName $ForceName
            if ($selE.Target) { $epE = [int]$selE.Target.LocalPort }
            return [pscustomobject]@{ Result = $ej; Ephem = $epE }
        }

        # Default Action=insert: exactly ONE splice. No auto-retry / no eject+insert.
        # -Action roundtrip: one eject then one insert (explicit only).
        if ($Mode -eq 'inject' -and $Action -eq 'roundtrip' -and -not $handpayAtStart) {
            [void]$statusLines.Add('PHASE=eject')
            Set-Content $status ($statusLines -join [Environment]::NewLine)
            $ejPack = Invoke-EjectPhase -WaitOut $false
            if (-not $ejPack) {
                return [pscustomobject]@{
                    Status = 'NO_TARGET_AFTER_GATE'; Log = ''; Err = 'NO_TARGET'; DallasHit = $false
                    NewDallas = ''; Ephem = 0; ElapsedMs = [int]$swTotal.ElapsedMilliseconds
                    Injected = $false; BackgroundDrain = $false; IsAdmin = $isAdmin
                    Action = $Action; Blocker = 'NO_TARGET'; UiReady = $false; Ejected = $false
                    HandpayCleared = $false
                }
            }
            $last = $ejPack.Result
            $ephem = [int]$ejPack.Ephem
            $ejected = $last.Injected
            $phaseLog = $last.Log
            [void]$statusLines.Add(('EJECT injected={0} hitOut={1} ms={2}' -f $last.Injected, $last.DallasHit, $last.ElapsedMs))
        } elseif ($Mode -eq 'inject' -and $handpayAtStart) {
            [void]$statusLines.Add('PHASE=single_insert (handpay - one splice clears + login)')
        } elseif ($Mode -eq 'inject' -and $gate.AdminKeyIn) {
            [void]$statusLines.Add('PHASE=single_insert (adminIn log stale OK - one splice only)')
        }

        $payload = $Rom
        $expect = 'IN'
        if ($Mode -eq 'inject' -and $Action -eq 'eject') {
            $payload = $EjectRom
            $expect = 'OUT'
        }

        $off = if ($rf -and (Test-Path $rf)) { (Get-Item $rf).Length } else { 0L }
        if ($Mode -ne 'inject') {
            [void]$statusLines.Add(('PHASE={0}' -f $Mode))
            Set-Content $status ($statusLines -join [Environment]::NewLine)
            $last = Invoke-OneSplice -Payload $payload -Ephem $ephem -SpliceMode $Mode `
                -WaitDrain $waitDrain -WaitDallasHit $false -ExpectEvent '' -LogOffset $off
            $phaseLog = $last.Log
        } else {
            [void]$statusLines.Add('PHASE=insert')
            Set-Content $status ($statusLines -join [Environment]::NewLine)
            $last = Invoke-OneSplice -Payload $payload -Ephem $ephem -SpliceMode 'inject' `
                -WaitDrain $waitDrain -WaitDallasHit $waitDallas -ExpectEvent $expect -LogOffset $off
            if ($phaseLog) { $phaseLog = $phaseLog + "`n--- insert ---`n" + $last.Log } else { $phaseLog = $last.Log }
        }

        # Post-check: KEY wait already happened in Invoke-OneSplice. Only linger for handpay unlock
        # (or a short log-tail grace). Do not stack another full HitTimeout wait.
        $gateAfter = Get-RuletaGateState $rf
        if ($handpayAtStart -and (Test-HandpayClearedSince $rf $off -or -not $gateAfter.HandpayLocked)) {
            $handpayCleared = $true
            [void]$statusLines.Add('HANDPAY_CLEARED=True')
        }
        $uiReady = $false
        $blocker = ''
        $hit = [bool]$last.DallasHit
        $newDallas = [string]$last.NewDallas
        if ($Mode -eq 'inject' -and $Action -ne 'eject') {
            $needHandpayWait = $last.Injected -and $handpayAtStart -and -not $handpayCleared -and $gateAfter.HandpayLocked
            $needKeyGrace = $last.Injected -and -not $hit
            if ($needHandpayWait -or $needKeyGrace) {
                $waitSec = if ($needHandpayWait) { [Math]::Max(1, $HandpayClearTimeoutSec) } else { 1 }
                [void]$statusLines.Add(('POST_WAIT upTo={0}s handpay={1} keyGrace={2}' -f $waitSec, [int]$needHandpayWait, [int]$needKeyGrace))
                Set-Content $status ($statusLines -join [Environment]::NewLine)
                $deadline = (Get-Date).AddSeconds($waitSec)
                while ((Get-Date) -lt $deadline) {
                    Start-Sleep -Milliseconds 200
                    $gateAfter = Get-RuletaGateState $rf
                    if ($handpayAtStart -and -not $gateAfter.HandpayLocked) { $handpayCleared = $true }
                    if (-not $hit) {
                        $ev = Read-AdminKeyEventsSince $rf $off 'IN'
                        if ($ev.Count -gt 0) {
                            $hit = $true
                            $newDallas = $ev[-1]
                            [void]$statusLines.Add('KEY_ADMIN_IN delayed')
                        }
                    }
                    if ($hit -and (-not $handpayAtStart -or $handpayCleared -or -not $gateAfter.HandpayLocked)) { break }
                    if ($handpayCleared -and -not $needHandpayWait) { break }
                    if ($handpayCleared -and -not $hit) { break }
                }
            } else {
                $gateAfter = Get-RuletaGateState $rf
            }

            if ($hit -and $KeyboardRecoverSec -gt 0) {
                [void]$statusLines.Add(('KB_WAIT reconnect upTo={0}s' -f $KeyboardRecoverSec))
                $kbDeadline = (Get-Date).AddSeconds($KeyboardRecoverSec)
                do {
                    $gateAfter = Get-RuletaGateState $rf
                    if (-not $gateAfter.KeyboardDown) { break }
                    Start-Sleep -Milliseconds 200
                } while ((Get-Date) -lt $kbDeadline)
            } else {
                $gateAfter = Get-RuletaGateState $rf
            }

            if ($hit -and -not $gateAfter.HandpayLocked -and -not $gateAfter.KeyboardDown) {
                $uiReady = $true
            } elseif ($hit -and $gateAfter.HandpayLocked) {
                $blocker = 'HANDPAY_LOCK_AFTER_HIT'
                $hit = $false
            } elseif ($hit -and $gateAfter.KeyboardDown) {
                $blocker = 'KEYBOARD_DOWN_AFTER_HIT'
                $hit = $false
            } elseif (-not $hit -and $last.Injected -and $handpayCleared -and -not $gateAfter.HandpayLocked) {
                $blocker = 'HANDPAY_CLEARED_NO_KEY'
            } elseif (-not $hit -and $last.Injected) {
                $blocker = 'NO_KEY_EVENT'
            }
        } elseif ($Mode -eq 'inject' -and $Action -eq 'eject') {
            $uiReady = $hit
        }

        [void]$statusLines.Add(('EXIT hit={0} injected={1} uiReady={2} blocker={3} handpayCleared={4} ms={5}' -f `
            $hit, $last.Injected, $uiReady, $blocker, $handpayCleared, $swTotal.ElapsedMilliseconds))
        Set-Content $status ($statusLines -join [Environment]::NewLine)

        [pscustomobject]@{
            Status          = Get-Content $status -Raw
            Log             = $phaseLog
            Err             = $last.Err
            DallasHit       = $hit
            NewDallas       = $newDallas
            Ephem           = $ephem
            Injected        = [bool]$last.Injected
            BackgroundDrain = [bool]$last.BackgroundDrain
            ElapsedMs       = [int]$swTotal.ElapsedMilliseconds
            IsAdmin         = $isAdmin
            Action          = $Action
            Blocker         = $blocker
            UiReady         = $uiReady
            Ejected         = $ejected
            HandpayCleared  = $handpayCleared
        }
    } -ArgumentList $Mode, $RunSeconds, $InjectAfterMs, $Rom, $EjectRom, $InitialDelta, $DrainReserveSec, $ServerPort, $TargetProcess, $HitTimeoutSec, $GateTimeoutSec, $HandpayClearTimeoutSec, $KeyboardRecoverSec, ([int]$effectiveWaitDrain), ([int]$effectiveWaitDallas), ([int][bool]$SkipGate), $effectiveAction
}
finally {
    if ($session) { Remove-PSSession $session -EA SilentlyContinue }
}

$ok = $false
if ($result.PSObject.Properties.Name -contains 'UiReady') { $ok = [bool]$result.UiReady }
else { $ok = [bool]$result.DallasHit }
$hpCleared = $false
if ($result.PSObject.Properties.Name -contains 'HandpayCleared') { $hpCleared = [bool]$result.HandpayCleared }
$color = if ($ok) { 'Green' } elseif ($hpCleared -or $result.Injected) { 'Yellow' } else { 'Red' }

# One-line verdict (default). -PassThru / -Verbose restores the old dump.
$note = ''
if ($ok -and $hpCleared) { $note = ' handpay cleared + menu' }
elseif ("$($result.Blocker)" -eq 'HANDPAY_CLEARED_NO_KEY') { $note = ' handpay cleared — re-run for menu' }
elseif ("$($result.Blocker)" -eq 'HANDPAY_LOCK_AFTER_HIT') { $note = ' handpay still locked' }
elseif ("$($result.Blocker)" -match 'KEYBOARD_DOWN') { $note = ' keyboard down — retry' }
elseif ($result.Injected -and -not $result.DallasHit) { $note = ' no KEY=admin' }

Write-Host ('Dallas {0}/{1}ms  inject={2} hit={3} ui={4} handpayCleared={5} blocker={6}{7}' -f `
    $swAll.ElapsedMilliseconds, $result.ElapsedMs,
    [int][bool]$result.Injected, [int][bool]$result.DallasHit, [int][bool]$ok,
    [int]$hpCleared, ("$($result.Blocker)" -replace '^$', '-'), $note) -ForegroundColor $color

if ($PassThru -or $VerbosePreference -ne 'SilentlyContinue') {
    Write-Host $result.Status
    if ($result.Log) {
        ($result.Log -split "\r?\n" | Where-Object { $_ } | Select-Object -Last 12) | ForEach-Object { Write-Host $_ }
    }
    if ($result.NewDallas) { Write-Host $result.NewDallas -ForegroundColor Green }
}

if ($PassThru) { $result }
