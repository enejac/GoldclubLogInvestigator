<#
.SYNOPSIS
    Stage 1-3 driver for WdRespond.exe: a table-driven SAS-slave responder on the
    CommCtrlSAS loopback bus. Loads a VERBATIM poll->response table (built by
    build_sas_response_table.py from a Stage 0 capture), stages + compiles WdRespond
    on the cabinet, and runs it in the requested mode.

.DESCRIPTION
    Forks the transport / compile-cache / safe-teardown pattern of
    Invoke-Stage0SasSniff.ps1, but runs WdRespond.exe instead of WdSniff.exe.

    The poll->response map handed to WdRespond is read ENTIRELY from the response-table
    JSON. This script invents no bytes: for each table entry it forwards the
    poll_unframed_hex key and the single most-frequent verbatim response (or an empty
    value for a poll that had no observed reply). If the table has no usable response,
    gp/enum runs are refused (matches WdRespond's own refusal).

    Modes (mirror aft/investigations/171-landing-plan.md and WdRespond):
      * passthru : observe only -- forwards every packet unchanged, logs what it WOULD
                   answer. Sends nothing. Use this for Stage 1 (prove framing/direction,
                   and that the .171 poll keys actually appear on the wire).
      * gp       : answer general polls (single-byte 80/81) only -- Stage 2.
      * enum     : answer all table polls (general + long polls) -- Stage 3.

    SAFETY: passthru emits nothing. gp/enum inject only verbatim table replies onto the
    31100 reply connection. No SAS frame is synthesized; an unknown poll -> silence.
    Never per-run sc stop/delete WinDivert (use -RemoveDriver for the safe teardown).

.EXAMPLE
    .\Invoke-SasResponder.ps1 -IP 10.0.0.171 -Mode passthru -Seconds 25

.EXAMPLE
    .\Invoke-SasResponder.ps1 -IP 10.0.0.171 -Mode gp -Seconds 90 -Table .\aft\captures\sas-response-table-steady.json

.EXAMPLE
    .\Invoke-SasResponder.ps1 -IP 10.0.0.171 -RemoveDriver
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.171',
    [ValidateSet('passthru', 'gp', 'enum')]
    [string] $Mode = 'passthru',
    [int]    $Seconds = 25,
    [int]    $DrainReserveSeconds = 3,
    [int]    $PollPort = 31150,
    [int]    $ReplyPort = 31100,
    [string] $Table = (Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\captures\sas-response-table-steady.json'),
    [string] $PsExecPath = 'C:\Tools\PSTools\PsExec.exe',
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string] $OutDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'aft\captures'),
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
    # PsExec writes its banner to stderr; relax EAP so that does not terminate strict mode.
    $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $result = (& $PsExecPath "\\$ComputerName" -accepteula @($script:PsExecAuthArgs) -s -n 30 powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc 2>$null) | Out-String
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
$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$csSrc = Join-Path $RepoRoot 'probes\WdRespond.cs'
foreach ($f in @($PsExecPath, $dll, $sys, $csSrc, $Table)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

# ------------------------------------------------ build the VERBATIM map from the table ----
$tableDoc = Get-Content -LiteralPath $Table -Raw | ConvertFrom-Json
if (-not $tableDoc.entries) { throw "Response table has no 'entries': $Table" }

$mapParts = New-Object System.Collections.Generic.List[string]
$answerableCount = 0
foreach ($e in $tableDoc.entries) {
    $key = ([string]$e.poll_unframed_hex).ToUpperInvariant()
    if (-not $key) { continue }
    $resp = ''
    if ($e.responses -and @($e.responses).Count -gt 0) {
        # responses are emitted most-frequent-first by build_sas_response_table.py.
        $resp = ([string](@($e.responses)[0].hex)).ToUpperInvariant()
        if ($resp) { $answerableCount++ }
    }
    $mapParts.Add("$key=$resp")
}
$mapArg = ($mapParts -join ',')

if ($mapParts.Count -eq 0) { throw "Response table produced an empty map: $Table" }
if ($Mode -ne 'passthru' -and $answerableCount -eq 0) {
    throw "Mode '$Mode' needs at least one verbatim response in the table, but '$Table' has none. Re-capture Stage 0 or use -Mode passthru."
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logName = "respond-$($ComputerName.Split('.')[-1])-$Mode-$stamp.txt"
$logLocal = Join-Path $OutDir $logName

Write-Host ''
Write-Host '=== SAS table-driven responder (WdRespond) ===' -ForegroundColor White
Write-Host "Cabinet : $ComputerName  |  Mode: $Mode  |  Window: ${Seconds}s  |  pollPort: $PollPort  replyPort: $ReplyPort"
Write-Host "Table   : $Table"
Write-Host "Map     : $mapArg" -ForegroundColor DarkGray
Write-Host "Output  : $logLocal" -ForegroundColor DarkGray
if ($Mode -eq 'passthru') {
    Write-Host '[*] passthru: forwards every packet unchanged and only LOGS what it would answer. Emits nothing.' -ForegroundColor Cyan
}
else {
    Write-Host "[*] ${Mode}: will inject ONLY verbatim table replies onto the $ReplyPort reply connection. Unknown polls stay silent." -ForegroundColor Yellow
}

# ------------------------------------------------------- content-hashed compile cache ----
$srcHash = (Get-FileHash -LiteralPath $csSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$remoteBaseDirUnc = "\\$ComputerName\c`$\Windows\Temp\aurumtap"
$remoteBinName = "respondbin-$srcHash"
$remoteDirUnc = Join-Path $remoteBaseDirUnc $remoteBinName
$remoteRunPath = "C:\Windows\Temp\aurumtap\$remoteBinName"
$exeUnc = Join-Path $remoteDirUnc 'WdRespond.exe'
$remoteLog = "$remoteRunPath\$logName"
$logUnc = Join-Path $remoteDirUnc $logName
$outUnc = Join-Path $remoteDirUnc 'respond_run.txt'

if (Test-Path -LiteralPath $exeUnc) {
    Write-Host "[*] Reusing cached WdRespond.exe on cabinet ($remoteBinName); skipping stage + compile." -ForegroundColor Cyan
}
else {
    New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
    Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force
    Write-Host "[*] Cache miss ($remoteBinName): staged WinDivert.dll, WinDivert64.sys, WdRespond.cs (compiles WdRespond.exe once)." -ForegroundColor Cyan
}

# ------------------------------------------------------------------- remote run ----
$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\WdRespond.exe"
$log = "__LOG__"
$out = "$wd\respond_run.txt"
Remove-Item $log,$out -Force -ErrorAction SilentlyContinue
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\WdRespond.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & "$exe" __MODE__ __SECONDS__ "__MAP__" __DRAIN__ __POLLPORT__ __REPLYPORT__ 1> $log 2>&1
            "EXITCODE=$LASTEXITCODE" | Out-File -FilePath $out -Encoding utf8 -Append
        }
        finally { $ErrorActionPreference = $prevEap }
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
    Replace('__MAP__', $mapArg).
    Replace('__DRAIN__', [string]$DrainReserveSeconds).
    Replace('__POLLPORT__', [string]$PollPort).
    Replace('__REPLYPORT__', [string]$ReplyPort)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))

$winRmTimeoutMs = ([Math]::Max($Seconds + $DrainReserveSeconds + 30, 60)) * 1000
$transportPlan = Get-LabRemoteTransportPlan -ComputerName $ComputerName -Credential $Credential `
    -CredentialFromLab:$labCtx.CredentialFromLab
$transportLog = Join-Path $env:TEMP ("wdrespond_{0}.log" -f $stamp)
$usedTransport = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportPlan.TransportOrder `
    -Computer $ComputerName -Enc $enc -LogPath $transportLog -Credential $Credential `
    -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs `
    -WinRmOperationTimeoutMs $winRmTimeoutMs -PsExecTimeoutSec ([Math]::Max($Seconds + $DrainReserveSeconds + 15, 60))
if (-not $usedTransport) {
    throw 'No remote transport succeeded (WinRM and PsExec both failed).'
}
if ($transportPlan.ScheduleWinRmEnable) {
    Start-LabRemoteWinRmEnableAsync -Computer $ComputerName -PsExecPath $PsExecPath -PsExecAuthArgs $script:PsExecAuthArgs | Out-Null
}
Write-Host "[*] Remote WdRespond finished (via $usedTransport)." -ForegroundColor Cyan

# ------------------------------------------------------------------- retrieve log ----
if (Test-Path -LiteralPath $outUnc) {
    $runlog = Get-Content -LiteralPath $outUnc -Raw
    if ($runlog) { Write-Host '--- remote run log ---' -ForegroundColor DarkGray; Write-Host $runlog.Trim() }
}
if (-not (Test-Path -LiteralPath $logUnc)) {
    Write-Host "[!] No responder log produced at $logUnc" -ForegroundColor Red
    exit 1
}
Copy-Item -LiteralPath $logUnc -Destination $logLocal -Force
Write-Host ''
Write-Host "[+] Responder log saved: $logLocal" -ForegroundColor Green

# ------------------------------------------------------- quick summary of the log ----
$lines = Get-Content -LiteralPath $logLocal
$open = ($lines | Select-String -SimpleMatch 'OPEN ok' | Measure-Object).Count
$pollWould = ($lines | Select-String -SimpleMatch 'WOULD-ANSWER' | Measure-Object).Count
$unknown = ($lines | Select-String -SimpleMatch 'UNKNOWN_POLL' | Measure-Object).Count
$answered = ($lines | Select-String -SimpleMatch 'ANSWER key=' | Measure-Object).Count
$doneLine = ($lines | Select-String -SimpleMatch 'DONE mode=' | Select-Object -Last 1).Line
Write-Host ''
Write-Host '--- responder summary ---' -ForegroundColor White
Write-Host ("  OPEN ok           : {0}" -f $open)
Write-Host ("  host polls (would): {0}" -f $pollWould)
Write-Host ("  UNKNOWN_POLL      : {0}" -f $unknown)
Write-Host ("  answered (inject) : {0}" -f $answered)
if ($doneLine) { Write-Host ("  {0}" -f $doneLine.Trim()) }

Write-Host ''
Write-Host "[i] Safe driver teardown (optional): .\Invoke-SasResponder.ps1 -IP $ComputerName -RemoveDriver" -ForegroundColor DarkGray
