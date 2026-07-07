<#
.SYNOPSIS
    Stage 0 (recon): READ-ONLY, direction-labeled WinDivert sniff of the CommCtrlSAS
    loopback SAS bus on the WORKING reference cabinet (default 10.0.0.90).

.DESCRIPTION
    Forks the read-only capture pattern of Invoke-AurumTrafficCapture.ps1 but:
      * Uses a dedicated SNIFF-mode sniffer (WdSniff.exe, compiled from WdSniff.cs)
        instead of netdump.exe, so every packet is logged TIME-ORDERED with a
        DIRECTION label (S2C = CommCtrlSAS->Aurum host poll; C2S = Aurum->CommCtrlSAS
        slave response), src/dst port, TCP seq/ack/flags, payload length, and FULL
        payload hex (0x1B bridge framing intact).
      * Opens WinDivert in SNIFF | RECV_ONLY mode -> it is a passive copy of the bus
        and physically cannot inject/divert/drop, so the live SAS bus is never disrupted.
      * NEVER does a per-run `sc stop/delete WinDivert` (that wedges the driver into
        STOP_PENDING; see aft/RUNBOOK.md). The driver is left registered as
        demand-start; use -RemoveDriver for the SAFE teardown (delete only when STOPPED).
      * WinRM first (5985), PsExec-as-SYSTEM fallback (see LabRemoteTransport.ps1).

    Two capture modes:
      * steady  : just sniff the running poll cycle for -Seconds.
      * bringup : start the sniffer, wait -PreRestartSeconds, then Restart-Service
                  -Force GoldClub.Aurum.Services on the cabinet (the ONLY authorized
                  state change), and keep sniffing to record the reconnect + EGM
                  enumeration handshake. Confirms .90 returns online afterwards.

    READ-ONLY guardrails: no SAS frame injection, no AFT, no config edits, no reboot,
    no touching the SAS gateway services or 10.0.0.171.

.EXAMPLE
    .\Invoke-Stage0SasSniff.ps1 -Mode steady -Seconds 45

.EXAMPLE
    .\Invoke-Stage0SasSniff.ps1 -Mode bringup -Seconds 100 -PreRestartSeconds 12

.EXAMPLE
    .\Invoke-Stage0SasSniff.ps1 -RemoveDriver
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [ValidateSet('steady', 'bringup')]
    [string] $Mode = 'steady',
    [int]    $Seconds = 45,
    [int]    $PreRestartSeconds = 12,
    [int[]]  $Ports = @(31100, 31101, 31150),
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string] $OutDir = (Join-Path $PSScriptRoot 'aft\captures'),
    [string] $AurumServiceName = 'GoldClub.Aurum.Services',
    [pscredential] $Credential,
    [switch] $RemoveDriver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PsExecAuthArgs = @()
$labRemoteTransportPath = Join-Path $PSScriptRoot 'LabRemoteTransport.ps1'
if (-not (Test-Path -LiteralPath $labRemoteTransportPath)) { throw "Missing $labRemoteTransportPath" }
. $labRemoteTransportPath
$labCtx = Initialize-LabRemoteContext -ComputerName $ComputerName -Credential $Credential
$Credential = $labCtx.Credential
$script:PsExecAuthArgs = @($labCtx.PsExecAuthArgs)

# ------------------------------------------------------------------ safe teardown ----
if ($RemoveDriver) {
    if (-not (Test-Path -LiteralPath $PsExecPath)) {
        throw "PsExec not found at $PsExecPath (needed for -RemoveDriver)."
    }
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
    $result = (& $PsExecPath "\\$ComputerName" -accepteula @($script:PsExecAuthArgs) -s -n 30 powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc 2>&1) | Out-String
    switch -Regex ($result) {
        'WINDIVERT_DELETED' { Write-Host "[+] WinDivert service stopped and deleted cleanly." -ForegroundColor Green }
        'WINDIVERT_NOT_INSTALLED' { Write-Host "[*] WinDivert service is not installed (nothing to remove)." -ForegroundColor Green }
        'WINDIVERT_STOP_PENDING_REBOOT_REQUIRED' { Write-Warning "WinDivert is STOP_PENDING (referenced kernel driver). NOT deleting (would mark-for-deletion). Reboot $ComputerName to clear it." }
        'WINDIVERT_NOT_STOPPED_SKIP_DELETE' { Write-Warning "WinDivert did not reach STOPPED in time; skipped delete to avoid a wedge. Retry shortly or reboot $ComputerName." }
        default { Write-Warning "Teardown result unclear:`n$result" }
    }
    return
}

# ------------------------------------------------------------------ preconditions ----
$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$csSrc = Join-Path $PSScriptRoot 'WdSniff.cs'
foreach ($f in @($PsExecPath, $dll, $sys, $csSrc)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}
if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

$portsCsv = ($Ports -join ',')
$totalMs = [int]($Seconds * 1000)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dumpName = "stage0-$($ComputerName.Split('.')[-1])-$Mode-$stamp.txt"
$dumpLocal = Join-Path $OutDir $dumpName

Write-Host ''
Write-Host '=== Stage 0 SAS bus sniff (READ-ONLY, SNIFF mode) ===' -ForegroundColor White
Write-Host "Cabinet : $ComputerName   |  Mode: $Mode   |  Window: ${Seconds}s   |  Ports: $portsCsv"
Write-Host "Output  : $dumpLocal" -ForegroundColor DarkGray

# ------------------------------------------------------- content-hashed compile cache ----
$srcHash = (Get-FileHash -LiteralPath $csSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$remoteBaseDirUnc = "\\$ComputerName\c`$\Windows\Temp\aurumtap"
$remoteBinName = "sniffbin-$srcHash"
$remoteDirUnc = Join-Path $remoteBaseDirUnc $remoteBinName
$remoteRunPath = "C:\Windows\Temp\aurumtap\$remoteBinName"
$exeUnc = Join-Path $remoteDirUnc 'WdSniff.exe'
$remoteDump = "$remoteRunPath\$dumpName"
$dumpUnc = Join-Path $remoteDirUnc $dumpName
$outUnc = Join-Path $remoteDirUnc 'sniff_run.txt'

if (Test-Path -LiteralPath $exeUnc) {
    Write-Host "[*] Reusing cached WdSniff.exe on cabinet ($remoteBinName); skipping stage + compile." -ForegroundColor Cyan
}
else {
    New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
    Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force
    Write-Host "[*] Cache miss ($remoteBinName): staged WinDivert.dll, WinDivert64.sys, WdSniff.cs (compiles WdSniff.exe once)." -ForegroundColor Cyan
}

# ------------------------------------------------------------------- remote sniff run ----
# Compiles WdSniff.exe only on a cache miss, then runs it for $totalMs with stdout -> dump.
# NO per-run sc stop/delete of WinDivert (driver auto-unloads on clean handle close).
$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\WdSniff.exe"
$dump = "__DUMP__"
$out = "$wd\sniff_run.txt"
Remove-Item $dump,$out -Force -ErrorAction SilentlyContinue
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\WdSniff.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & "$exe" __MS__ __PORTS__ 1> $dump 2> $out
            "EXITCODE=$LASTEXITCODE" | Out-File -FilePath $out -Encoding utf8 -Append
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
}
catch {
    "REMOTE_EXCEPTION: $_" | Out-File -FilePath $out -Encoding utf8 -Append
}
'@
$remoteScript = $remoteTemplate.Replace('__REMOTE_WD__', $remoteRunPath).Replace('__DUMP__', $remoteDump).Replace('__MS__', [string]$totalMs).Replace('__PORTS__', $portsCsv)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))

$winRmTimeoutMs = ([Math]::Max($Seconds + 30, 60)) * 1000
$psExecTimeoutSec = [Math]::Max($Seconds + 15, 60)
$transportPlan = Get-LabRemoteTransportPlan -ComputerName $ComputerName -Credential $Credential `
    -CredentialFromLab:$labCtx.CredentialFromLab
$sniffLog = Join-Path $env:TEMP ("stage0_sniff_{0}.log" -f $stamp)

Write-Host "[*] Starting remote SNIFF for ${Seconds}s (WinRM first, PsExec fallback)..." -ForegroundColor Cyan
$sniffJob = Start-Job -ScriptBlock {
    param($TransportPath, $Order, $Comp, $EncArg, $Log, $Cred, $PsExec, $PsAuth, $WinMs, $PsSec)
    . $TransportPath
    Invoke-LabRemoteEncodedWithFallback -TransportOrder $Order -Computer $Comp -Enc $EncArg `
        -LogPath $Log -Credential $Cred -PsExecPath $PsExec -PsExecAuthArgs $PsAuth `
        -WinRmOperationTimeoutMs $WinMs -PsExecTimeoutSec $PsSec
} -ArgumentList @(
    $labRemoteTransportPath,
    @($transportPlan.TransportOrder),
    $ComputerName,
    $enc,
    $sniffLog,
    $Credential,
    $PsExecPath,
    @($script:PsExecAuthArgs),
    $winRmTimeoutMs,
    $psExecTimeoutSec
)

Start-Sleep -Seconds 5

if ($Mode -eq 'bringup') {
    $wait = [Math]::Max(0, $PreRestartSeconds - 5)
    if ($wait -gt 0) { Start-Sleep -Seconds $wait }
    Write-Host "[*] Restarting ONLY '$AurumServiceName' on $ComputerName (authorized state change) to capture bring-up..." -ForegroundColor Yellow
    $restartCmd = "Restart-Service -Name '$AurumServiceName' -Force; Start-Sleep -Seconds 1; (Get-Service '$AurumServiceName').Status"
    $rEnc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($restartCmd))
    $restartLog = Join-Path $env:TEMP ("stage0_restart_{0}.log" -f $stamp)
    $restartTransport = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportPlan.TransportOrder `
        -Computer $ComputerName -Enc $rEnc -LogPath $restartLog -Credential $Credential `
        -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -PsExecTimeoutSec 60
    $restartOut = if (Test-Path $restartLog) { (Get-Content -LiteralPath $restartLog -Raw) } else { '' }
    Write-Host "[+] Restart issued via $restartTransport. Service status reported: $($restartOut.Trim() -split "`n" | Select-Object -Last 1)" -ForegroundColor Green
}
else {
    Write-Host '[*] Steady-state capture running (no service action).' -ForegroundColor Cyan
}

Write-Host '[*] Waiting for sniff window to complete...' -ForegroundColor Cyan
$usedSniffTransport = Receive-Job -Job $sniffJob -Wait -AutoRemoveJob
if (-not $usedSniffTransport) {
    throw 'No remote transport succeeded for sniff (WinRM and PsExec both failed).'
}
Write-Host "[+] Sniff finished via $usedSniffTransport." -ForegroundColor Green
if ($transportPlan.ScheduleWinRmEnable) {
    Start-LabRemoteWinRmEnableAsync -Computer $ComputerName -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs | Out-Null
}

# ------------------------------------------------------------------- retrieve dump ----
if (Test-Path -LiteralPath $outUnc) {
    $runlog = Get-Content -LiteralPath $outUnc -Raw
    if ($runlog) {
        Write-Host '--- remote run log ---' -ForegroundColor DarkGray
        Write-Host $runlog.Trim()
    }
}
if (-not (Test-Path -LiteralPath $dumpUnc)) {
    Write-Host "[!] No dump produced at $dumpUnc" -ForegroundColor Red
    exit 1
}
Copy-Item -LiteralPath $dumpUnc -Destination $dumpLocal -Force
$len = (Get-Item -LiteralPath $dumpLocal).Length
$pkts = 0
try { $pkts = (Select-String -LiteralPath $dumpLocal -Pattern '^PKT ' | Measure-Object).Count } catch {}
Write-Host ''
Write-Host ("[+] Capture complete: {0} packets, {1:N0} bytes" -f $pkts, $len) -ForegroundColor Green
Write-Host "    $dumpLocal" -ForegroundColor DarkGray

# --------------------------------------------------- quick direction/role summary ----
$s2c = 0; $c2s = 0
$s2cPorts = @{}; $c2sPorts = @{}
foreach ($ln in (Get-Content -LiteralPath $dumpLocal)) {
    if ($ln -match '^PKT .* dir=(\S+) sport=(\d+) dport=(\d+) .* len=(\d+) hex=(\S+)') {
        $dir = $Matches[1]; $sp = $Matches[2]; $dp = $Matches[3]; $plen = [int]$Matches[4]
        if ($dir -eq 'S2C' -and $plen -gt 0) { $s2c++; $s2cPorts["$sp->$dp"] = ($s2cPorts["$sp->$dp"] + 1) }
        elseif ($dir -eq 'C2S' -and $plen -gt 0) { $c2s++; $c2sPorts["$sp->$dp"] = ($c2sPorts["$sp->$dp"] + 1) }
    }
}
Write-Host ''
Write-Host '--- direction summary (payload-bearing packets) ---' -ForegroundColor White
Write-Host ("  S2C (CommCtrlSAS->Aurum, host poll)   : {0}" -f $s2c)
foreach ($k in ($s2cPorts.Keys | Sort-Object)) { Write-Host ("      {0}  x{1}" -f $k, $s2cPorts[$k]) -ForegroundColor DarkGray }
Write-Host ("  C2S (Aurum->CommCtrlSAS, slave resp)  : {0}" -f $c2s)
foreach ($k in ($c2sPorts.Keys | Sort-Object)) { Write-Host ("      {0}  x{1}" -f $k, $c2sPorts[$k]) -ForegroundColor DarkGray }

# --------------------------------------------------- post-capture online check ----
Write-Host ''
Write-Host '[*] Confirming cabinet SAS link is back online...' -ForegroundColor Cyan
$check = @'
$ErrorActionPreference="SilentlyContinue"
$o = New-Object System.Collections.Generic.List[string]
$est = Get-NetTCPConnection -LocalPort 31100,31101,31150 -State Established -ErrorAction SilentlyContinue
$o.Add("ESTABLISHED=" + (@($est).Count))
foreach($x in $est){ $o.Add(("  L={0}:{1} R={2}:{3}" -f $x.LocalAddress,$x.LocalPort,$x.RemoteAddress,$x.RemotePort)) }
$log = "C:\Goldclub\var\log\GoldClub.Aurum.Services sasmsgr of SASControler1\" + (Get-Date).ToString("yyyy-MM-dd") + ".log"
if (Test-Path $log) {
    $tail = Get-Content $log -Tail 6 -ErrorAction SilentlyContinue
    $o.Add("SASMSGR_TAIL:")
    foreach($t in $tail){ $o.Add("  " + $t) }
} else { $o.Add("SASMSGR_LOG_NOT_FOUND") }
$o -join "`r`n"
'@
$checkOut = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportPlan.TransportOrder `
    -Computer $ComputerName -Enc $cEnc -LogPath (Join-Path $env:TEMP ("stage0_check_{0}.log" -f $stamp)) `
    -Credential $Credential -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs -PsExecTimeoutSec 30
if ($checkOut) {
    $checkLog = Get-Content -LiteralPath (Join-Path $env:TEMP ("stage0_check_{0}.log" -f $stamp)) -Raw -ErrorAction SilentlyContinue
    if ($checkLog) { Write-Host $checkLog.Trim() }
}

Write-Host ''
Write-Host "[i] Dump saved: $dumpLocal" -ForegroundColor Green
Write-Host "[i] Decode it with:  python decode_stage0.py `"$dumpLocal`"" -ForegroundColor DarkGray
Write-Host "[i] Safe driver teardown (optional): .\Invoke-Stage0SasSniff.ps1 -RemoveDriver" -ForegroundColor DarkGray
