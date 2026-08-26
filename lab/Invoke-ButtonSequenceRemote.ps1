<#
.SYNOPSIS
    Inject a TIMED SEQUENCE of physical-button presses into the live OneHand
    button bus (CommCtrl :30800) on the EGM, via the WinDivert splicer
    ButtonInject.exe. Self-heals the connection (drain to delta=0) so there is
    no RST / 'unexpected stop' reboot.

.DESCRIPTION
    Each press is injected server->client as the identical "<code>\r\n" frame a
    real mechanical press produces (verified by capture). Button names are
    resolved to codes from ButtonMap.json.

    -Steps is an array of tokens "Name" or "Name@gapAfterMs". gapAfterMs is the
    delay AFTER that press before the next one (defaults to -DefaultGapMs). The
    first press happens at -FirstDelayMs.

.EXAMPLE
    # 10x Spin (4s apart), Help, Help (close), Collect:
    $steps = @(1..10 | ForEach-Object { 'Spin@4000' }) + @('Help@2500','Help@2500','Collect@2500')
    & .\Invoke-ButtonSequenceRemote.ps1 -ComputerName 10.0.0.90 -Steps $steps
#>
[CmdletBinding()]
param(
    [string]   $ComputerName    = '10.0.0.90',
    [string]   $PsExecPath      = 'C:\Tools\PSTools\PsExec.exe',
    [string[]] $Steps,
    [int]      $FirstDelayMs    = 2000,
    [int]      $DefaultGapMs    = 3000,
    # Drain can only swallow real poll-response bytes (~1 small segment/sec), so
    # reserve generously: ~1s per injected byte. 13 presses x5B = 65B -> ~25s.
    [int]      $DrainReserveSec = 25,
    [string]   $TargetProcess   = 'OneHand',
    [string]   $WinDivertDir    = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string]   $ExePath         = '',
    [string]   $ButtonMapPath   = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path $PSScriptRoot -Parent
if (-not $ExePath) { $ExePath = Join-Path $RepoRoot 'probes\ButtonInject.exe' }
if (-not $ButtonMapPath) { $ButtonMapPath = Join-Path $RepoRoot 'ButtonMap.json' }

$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
foreach ($f in @($PsExecPath, $dll, $sys, $ExePath, $ButtonMapPath)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}

# Default sequence = the requested 10x Spin, Help, Help (close), Collect.
if (-not $Steps -or $Steps.Count -eq 0) {
    $Steps = @(1..10 | ForEach-Object { 'Spin@4000' }) + @('Help@2500', 'Help@2500', 'Collect@2500')
}

# name -> code map from ButtonMap.json
$map = @{}
$bm = Get-Content -Raw -LiteralPath $ButtonMapPath | ConvertFrom-Json
foreach ($b in $bm.buttons) { $map[$b.name.ToLowerInvariant()] = [int]$b.code }

# Build the schedule (code@absMs) and a human-readable plan.
$schedItems = New-Object System.Collections.Generic.List[string]
$plan       = New-Object System.Collections.Generic.List[string]
$t = $FirstDelayMs
foreach ($step in $Steps) {
    $name = $step; $gap = $DefaultGapMs
    if ($step -match '^(.*?)@(\d+)$') { $name = $Matches[1]; $gap = [int]$Matches[2] }
    $key = $name.Trim().ToLowerInvariant()
    if (-not $map.ContainsKey($key)) { throw "Unknown button name '$name'. Known: $($map.Keys -join ', ')" }
    $code = $map[$key]
    $schedItems.Add(("{0}@{1}" -f $code, $t))
    $plan.Add(("  t={0,6}ms  {1} (code {2})" -f $t, $name, $code))
    $t += $gap
}
$lastMs    = $t - ($DefaultGapMs)   # approx end of last action's gap
$scheduleSpec = ($schedItems -join ',')
$runSeconds   = [int][Math]::Ceiling($lastMs / 1000.0) + $DrainReserveSec + 3

$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\wd"
$statusUnc    = Join-Path $remoteDirUnc 'btn_status.txt'
$logUnc       = Join-Path $remoteDirUnc 'btn.log'
$errUnc       = Join-Path $remoteDirUnc 'btn.err'

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host ' Button-sequence injection (WinDivert splice, self-heal)'  -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target host : \\{0}  (consumer: {1})" -f $ComputerName, $TargetProcess) -ForegroundColor Gray
Write-Host (" Presses     : {0}   RunSeconds: {1}   DrainReserve: {2}s" -f $schedItems.Count, $runSeconds, $DrainReserveSec) -ForegroundColor Gray
Write-Host ' Plan:' -ForegroundColor Gray
$plan | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray }
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $remoteDirUnc)) { New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null }
Copy-Item -LiteralPath $dll     -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $sys     -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $ExePath -Destination $remoteDirUnc -Force
Write-Host ' Staged WinDivert.dll + WinDivert64.sys + ButtonInject.exe.' -ForegroundColor Green

$remote = @'
$ErrorActionPreference = "SilentlyContinue"
$wd     = "C:\Windows\Temp\wd"
$runSec = __RUNSEC__
$drainR = __DRAINR__
$sched  = "__SCHED__"
$force  = "__FORCE__"
$prefer = @("OneHand","BiOS2","Bootstrap")

$conns = Get-NetTCPConnection -RemotePort 30800 -State Established
$rows = @()
foreach ($c in $conns) {
    $pname = ""
    try { $pname = (Get-Process -Id $c.OwningProcess).ProcessName } catch {}
    $rows += [pscustomobject]@{ LocalPort = $c.LocalPort; PID = $c.OwningProcess; Name = $pname }
}
$target = $null
if ($force) { $target = $rows | Where-Object { $_.Name -ieq $force } | Select-Object -First 1 }
if (-not $target) { foreach ($p in $prefer) { $m = $rows | Where-Object { $_.Name -ieq $p } | Select-Object -First 1; if ($m) { $target = $m; break } } }
if (-not $target) { $target = $rows | Select-Object -First 1 }

$lines = @("CONSUMERS:")
foreach ($r in $rows) { $lines += ("  port={0} pid={1} name={2}" -f $r.LocalPort, $r.PID, $r.Name) }
if (-not $target) { $lines += "NO_TARGET"; Set-Content "$wd\btn_status.txt" ($lines -join [Environment]::NewLine); return }
$ephem = $target.LocalPort
$lines += ("TARGET: port={0} pid={1} name={2}" -f $target.LocalPort, $target.PID, $target.Name)
$lines += ("RUNSEC={0} SCHED={1}" -f $runSec, $sched)
Set-Content "$wd\btn_status.txt" ($lines -join [Environment]::NewLine)

Remove-Item "$wd\btn.log","$wd\btn.err" -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$wd\ButtonInject.exe" `
    -ArgumentList @("$ephem", "$runSec", "$drainR", $sched) -WorkingDirectory $wd `
    -RedirectStandardOutput "$wd\btn.log" -RedirectStandardError "$wd\btn.err" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds ($runSec + 6)
try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}
Add-Content "$wd\btn_status.txt" ("EXITCODE={0}" -f $(try { $p.ExitCode } catch { "n/a" }))
Start-Sleep -Milliseconds 500
sc.exe stop WinDivert   | Out-Null
sc.exe delete WinDivert | Out-Null
'@
$remote = $remote.Replace('__RUNSEC__', [string]$runSeconds).Replace('__DRAINR__', [string]$DrainReserveSec).Replace('__SCHED__', $scheduleSpec).Replace('__FORCE__', $TargetProcess)

$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))

Write-Host (' Running injection on the EGM ({0}s window)...' -f $runSeconds) -ForegroundColor Cyan
$psTimeout = $runSeconds + 90
& $PsExecPath "\\$ComputerName" -accepteula -s -n $psTimeout powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc | Out-Null

Start-Sleep -Milliseconds 700

Write-Host ''
Write-Host ' --- Target / consumers ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $statusUnc) { Write-Host (Get-Content -LiteralPath $statusUnc -Raw) } else { Write-Host ' (no status file)' -ForegroundColor Red }

Write-Host ' --- Injection log ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $logUnc) {
    $log = Get-Content -LiteralPath $logUnc -Raw
    if ($log) { Write-Host $log } else { Write-Host ' (log empty)' -ForegroundColor Yellow }
} else { Write-Host ' (no log file)' -ForegroundColor Red }

if (Test-Path -LiteralPath $errUnc) {
    $e = Get-Content -LiteralPath $errUnc -Raw
    if ($e) { $e = $e.Trim() }
    if ($e) { Write-Host ' --- stderr ---' -ForegroundColor Yellow; Write-Host $e -ForegroundColor Yellow }
}

Write-Host ''
Write-Host ' Done.' -ForegroundColor Green
