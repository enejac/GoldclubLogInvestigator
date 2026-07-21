<#
.SYNOPSIS
    Option-D probe driver for WdPollInject.exe: a WinDivert loopback HOST-POLL
    injector that ORIGINATES the verbatim 0x1B-framed general polls 1B81 / 1B80 into
    the live CommCtrlSAS:31150 -> Aurum:<ephemeral> TCP stream on a cabinet, to test
    whether injecting host polls brings Aurum's SAS slave session online.

.DESCRIPTION
    Mirror image of Invoke-SasResponder.ps1 (which drove the slave RESPONDER WdRespond).
    This drives the host POLLER WdPollInject. It forks the same transport / compile-cache
    / safe-teardown scaffolding:
      * WinRM first (5985), PsExec-as-SYSTEM fallback (see LabRemoteTransport.ps1).
      * Content-hashed remote compile cache under C:\Windows\Temp\aurumtap.
      * -RemoveDriver safe teardown (stop+delete only when actually STOPPED; never while
        STOP_PENDING; never a per-run sc delete that wedges the driver).

    The live Aurum ephemeral port for the 31150 connection is DISCOVERED REMOTELY in the
    same SYSTEM session immediately before the exe launches (the loopback connection
    churns / reconnects, so the ephemeral can change; discovering it in-session avoids a
    stale value). If no 31150 connection is ESTABLISHED at launch, the run is skipped and
    reported (nothing is injected).

    Modes (staged):
      * passthru : divert + forward every packet unchanged (delta stays 0). Proves the
                   splice is safe before any inject. Stage 1.
      * poll     : additionally inject the alternating 1B81/1B80 cadence server->client
                   at -IntervalMs. Stage 2.

    MONEY-FREE: this tool only ever emits the verbatim general polls 1B81 / 1B80. It sends
    no AFT / 0x72 / long poll and synthesizes no other SAS byte. WdPollInject itself
    refuses any poll frame other than 1B81 / 1B80.

.EXAMPLE
    .\Invoke-LoopbackPollInject.ps1 -IP 10.0.0.171 -Mode passthru -Seconds 15

.EXAMPLE
    .\Invoke-LoopbackPollInject.ps1 -IP 10.0.0.171 -Mode poll -Seconds 40 -IntervalMs 200

.EXAMPLE
    .\Invoke-LoopbackPollInject.ps1 -IP 10.0.0.171 -RemoveDriver
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.171',
    [ValidateSet('passthru', 'poll', 'pollaft')]
    [string] $Mode = 'passthru',
    [int]    $Seconds = 15,
    [int]    $IntervalMs = 200,
    [int]    $DrainReserveSeconds = 3,
    [int]    $ServerPort = 31150,
    [int]    $EphemWaitSec = 90,
    [ValidateSet('1B81,1B80', '1B80,1B81', '1B81', '1B80')]
    [string] $PollFrames = '1B81,1B80',
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string] $OutDir = (Join-Path $PSScriptRoot 'aft\captures'),
    [pscredential] $Credential,
    [switch] $RemoveDriver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-WinDivertDir {
    param([string] $Preferred)
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $candidates += @(
        'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
        'C:\Tools\WinDivert\x64'
    )
    foreach ($dir in ($candidates | Select-Object -Unique)) {
        $dll = Join-Path $dir 'WinDivert.dll'
        $sys = Join-Path $dir 'WinDivert64.sys'
        if ((Test-Path -LiteralPath $dll) -and (Test-Path -LiteralPath $sys)) {
            return $dir
        }
    }
    throw @(
        'WinDivert x64 not found. Expected WinDivert.dll + WinDivert64.sys under one of:',
        '  C:\Tools\WinDivert\x64',
        '  C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
        'Pass -WinDivertDir <folder>.'
    ) -join "`n"
}

$script:PsExecAuthArgs = @()
$labRemoteTransportPath = Join-Path $PSScriptRoot 'LabRemoteTransport.ps1'
if (-not (Test-Path -LiteralPath $labRemoteTransportPath)) { throw "Missing $labRemoteTransportPath" }
. $labRemoteTransportPath
$labCtx = Initialize-LabRemoteContext -ComputerName $ComputerName -Credential $Credential
$Credential = $labCtx.Credential
$script:PsExecAuthArgs = @($labCtx.PsExecAuthArgs)

# ------------------------------------------------------------------ safe teardown ----
if ($RemoveDriver) {
    if (-not (Test-Path -LiteralPath $PsExecPath)) { throw "PsExec not found at $PsExecPath (needed for -RemoveDriver)." }
    Write-Host "[*] Safe WinDivert teardown on $ComputerName ..." -ForegroundColor Cyan
    $teardown = @'
$ErrorActionPreference = "Continue"
$q = (& sc.exe query WinDivert 2>&1) -join "`n"
if ($q -match "1060") { "WINDIVERT_NOT_INSTALLED"; return }
if ($q -match "STOP_PENDING") { "WINDIVERT_STOP_PENDING_REBOOT_REQUIRED"; return }
& sc.exe stop WinDivert | Out-Null
for ($i = 0; $i -lt 24; $i++) {
    $s = (& sc.exe query WinDivert 2>&1) -join "`n"
    if ($s -match "1060" -or $s -match "STOPPED" -or $s -match "STOP_PENDING") { break }
    Start-Sleep -Milliseconds 250
}
$s = (& sc.exe query WinDivert 2>&1) -join "`n"
if ($s -match "1060") { "WINDIVERT_NOT_INSTALLED" }
elseif ($s -match "STOP_PENDING") { "WINDIVERT_STOP_PENDING_REBOOT_REQUIRED" }
elseif ($s -match "STOPPED") { & sc.exe delete WinDivert | Out-Null; "WINDIVERT_DELETED" }
else { "WINDIVERT_NOT_STOPPED_SKIP_DELETE" }
'@
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($teardown))
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $result = (& $PsExecPath "\\$ComputerName" -accepteula @script:PsExecAuthArgs -s -n 30 powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc 2>$null) | Out-String
    $ErrorActionPreference = $prevEap
    switch -Regex ($result) {
        'WINDIVERT_DELETED' { Write-Host "[+] WinDivert service stopped and deleted cleanly." -ForegroundColor Green }
        'WINDIVERT_NOT_INSTALLED' { Write-Host "[*] WinDivert service is not installed (nothing to remove)." -ForegroundColor Green }
        'WINDIVERT_STOP_PENDING_REBOOT_REQUIRED' { Write-Warning "WinDivert is STOP_PENDING (referenced kernel driver). NOT deleting. Reboot $ComputerName to clear it." }
        'WINDIVERT_NOT_STOPPED_SKIP_DELETE' { Write-Warning "WinDivert did not reach STOPPED in time; skipped delete to avoid a wedge." }
        default { Write-Warning "Teardown result unclear:`n$result" }
    }
    return
}

# ------------------------------------------------------------------ preconditions ----
$WinDivertDir = Resolve-WinDivertDir -Preferred $WinDivertDir
$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$csSrc = Join-Path $PSScriptRoot 'WdPollInject.cs'
foreach ($f in @($PsExecPath, $dll, $sys, $csSrc)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

if ($Seconds -lt 1) { throw "-Seconds must be >= 1." }
if ($IntervalMs -lt 20) { throw "-IntervalMs must be >= 20 (do not poll faster than the observed ~200ms)." }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logName = "pollinject-$($ComputerName.Split('.')[-1])-$Mode-$stamp.txt"
$logLocal = Join-Path $OutDir $logName

Write-Host ''
Write-Host '=== Option-D loopback host-poll injector (WdPollInject) ===' -ForegroundColor White
Write-Host "Cabinet : $ComputerName  |  Mode: $Mode  |  Window: ${Seconds}s  |  serverPort: $ServerPort  |  ephemWait: ${EphemWaitSec}s"
Write-Host "Poll    : frames=$PollFrames intervalMs=$IntervalMs drainReserveSec=$DrainReserveSeconds"
Write-Host "Output  : $logLocal" -ForegroundColor DarkGray
if ($Mode -eq 'passthru') {
    Write-Host '[*] passthru: diverts the 31150 connection and forwards every packet unchanged. Injects nothing (delta stays 0).' -ForegroundColor Cyan
}
else {
    Write-Host "[*] poll: injects ONLY the verbatim general polls ($PollFrames) server->client at ${IntervalMs}ms. No AFT/money." -ForegroundColor Yellow
}

# ------------------------------------------------------- content-hashed compile cache ----
$srcHash = (Get-FileHash -LiteralPath $csSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$remoteBaseDirUnc = "\\$ComputerName\c`$\Windows\Temp\aurumtap"
$remoteBinName = "pollinjectbin-$srcHash"
$remoteDirUnc = Join-Path $remoteBaseDirUnc $remoteBinName
$remoteRunPath = "C:\Windows\Temp\aurumtap\$remoteBinName"
$exeUnc = Join-Path $remoteDirUnc 'WdPollInject.exe'
$remoteLog = "$remoteRunPath\$logName"
$logUnc = Join-Path $remoteDirUnc $logName
$outUnc = Join-Path $remoteDirUnc 'pollinject_run.txt'

if (Test-Path -LiteralPath $exeUnc) {
    Write-Host "[*] Reusing cached WdPollInject.exe on cabinet ($remoteBinName); skipping stage + compile." -ForegroundColor Cyan
}
else {
    New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
    Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force
    Write-Host "[*] Cache miss ($remoteBinName): staged WinDivert.dll, WinDivert64.sys, WdPollInject.cs (compiles WdPollInject.exe once)." -ForegroundColor Cyan
}

# ------------------------------------------------------------------- remote run ----
# The ephemeral is discovered in-session right before launch (the loopback conn churns).
$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\WdPollInject.exe"
$log = "__LOG__"
$out = "$wd\pollinject_run.txt"
Remove-Item $log,$out -Force -ErrorAction SilentlyContinue
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\WdPollInject.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        # Discover the live Aurum ephemeral for the server port, retrying because
        # the loopback connection reconnects.
        $ephem = 0
        $deadline = (Get-Date).AddSeconds(__EPHEMWAIT__)
        do {
            $c = Get-NetTCPConnection -LocalPort __SERVERPORT__ -State Established -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($c) { $ephem = [int]$c.RemotePort; break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ($ephem -eq 0) {
            "BRIDGE_STATE (no Established on __SERVERPORT__ after __EPHEMWAIT__s):" | Out-File -FilePath $out -Encoding utf8 -Append
            Get-Process CommCtrlSAS,GoldClub.Aurum.Services -ErrorAction SilentlyContinue |
                Select-Object Name, Id | Format-Table -AutoSize | Out-String |
                Out-File -FilePath $out -Encoding utf8 -Append
            Get-NetTCPConnection -LocalPort __SERVERPORT__ -ErrorAction SilentlyContinue |
                Select-Object LocalPort, RemotePort, State, OwningProcess |
                Format-Table -AutoSize | Out-String | Out-File -FilePath $out -Encoding utf8 -Append
            "NO_ESTABLISHED___SERVERPORT__" | Out-File -FilePath $out -Encoding utf8 -Append
        } else {
            "DISCOVERED_EPHEM=$ephem" | Out-File -FilePath $out -Encoding utf8 -Append
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & "$exe" __MODE__ $ephem __SECONDS__ __INTERVALMS__ __DRAIN__ "__FRAMES__" __SERVERPORT__ 1> $log 2>&1
                "EXITCODE=$LASTEXITCODE" | Out-File -FilePath $out -Encoding utf8 -Append
            }
            finally { $ErrorActionPreference = $prevEap }
        }
    }
}
catch {
    "REMOTE_EXCEPTION: $_" | Out-File -FilePath $out -Encoding utf8 -Append
}
'@
$remoteScript = $remoteTemplate.
    Replace('__REMOTE_WD__', $remoteRunPath).
    Replace('__LOG__', $remoteLog).
    Replace('__MODE__', $Mode).
    Replace('__SECONDS__', [string]$Seconds).
    Replace('__INTERVALMS__', [string]$IntervalMs).
    Replace('__DRAIN__', [string]$DrainReserveSeconds).
    Replace('__FRAMES__', $PollFrames).
    Replace('__SERVERPORT__', [string]$ServerPort).
    Replace('__EPHEMWAIT__', [string]$EphemWaitSec)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))

$winRmTimeoutMs = ([Math]::Max($EphemWaitSec + $Seconds + $DrainReserveSeconds + 30, 90)) * 1000
$transportPlan = Get-LabRemoteTransportPlan -ComputerName $ComputerName -Credential $Credential `
    -CredentialFromLab:$labCtx.CredentialFromLab
$transportLog = Join-Path $env:TEMP ("wdpollinject_{0}.log" -f $stamp)
$usedTransport = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportPlan.TransportOrder `
    -Computer $ComputerName -Enc $enc -LogPath $transportLog -Credential $Credential `
    -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs `
    -WinRmOperationTimeoutMs $winRmTimeoutMs -PsExecTimeoutSec ([Math]::Max($EphemWaitSec + $Seconds + $DrainReserveSeconds + 15, 90))
if (-not $usedTransport) {
    throw 'No remote transport succeeded (WinRM and PsExec both failed).'
}
if ($transportPlan.ScheduleWinRmEnable) {
    Start-LabRemoteWinRmEnableAsync -Computer $ComputerName -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs | Out-Null
}
Write-Host "[*] Remote WdPollInject finished (via $usedTransport)." -ForegroundColor Cyan

# ------------------------------------------------------------------- retrieve log ----
if (Test-Path -LiteralPath $outUnc) {
    $runlog = Get-Content -LiteralPath $outUnc -Raw
    if ($runlog) { Write-Host '--- remote run log ---' -ForegroundColor DarkGray; Write-Host $runlog.Trim() }
}
if (-not (Test-Path -LiteralPath $logUnc)) {
    Write-Host "[!] No injector log produced at $logUnc (connection may not have been ESTABLISHED; see remote run log above)." -ForegroundColor Red
    if ($runlog -match 'NO_ESTABLISHED') {
        Write-Host '    CommCtrlSAS/Aurum processes may be running while bridge TCP is down (31150 not LISTENING/ESTABLISHED).' -ForegroundColor Yellow
        Write-Host '    Wait for comm unlock / natural reconnect — do not restart services from this tool.' -ForegroundColor Yellow
    }
    exit 1
}
Copy-Item -LiteralPath $logUnc -Destination $logLocal -Force
Write-Host ''
Write-Host "[+] Injector log saved: $logLocal" -ForegroundColor Green

# ------------------------------------------------------- quick summary of the log ----
$lines = Get-Content -LiteralPath $logLocal
function Get-LastMatchLine([object[]] $haystack, [string] $needle) {
    $m = $haystack | Select-String -SimpleMatch $needle | Select-Object -Last 1
    if ($m) { return $m.Line } else { return $null }
}
$open = ($lines | Select-String -SimpleMatch 'OPEN ok' | Measure-Object).Count
$injected = ($lines | Select-String -SimpleMatch 'INJECT #' | Measure-Object).Count
$doneLine = Get-LastMatchLine $lines 'DONE mode='
$warnLine = Get-LastMatchLine $lines 'WARNING: stopped with delta='
$syncLine = Get-LastMatchLine $lines 'left in sync'
Write-Host ''
Write-Host '--- injector summary ---' -ForegroundColor White
Write-Host ("  OPEN ok        : {0}" -f $open)
Write-Host ("  polls injected : {0}" -f $injected)
if ($doneLine) { Write-Host ("  {0}" -f $doneLine.Trim()) }
if ($syncLine) { Write-Host ("  {0}" -f $syncLine.Trim()) -ForegroundColor Green }
if ($warnLine) { Write-Host ("  {0}" -f $warnLine.Trim()) -ForegroundColor Yellow }

Write-Host ''
Write-Host "[i] Safe driver teardown (optional): .\Invoke-LoopbackPollInject.ps1 -IP $ComputerName -RemoveDriver" -ForegroundColor DarkGray
