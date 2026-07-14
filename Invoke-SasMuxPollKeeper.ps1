<#
.SYNOPSIS
    Probe host COM4 and run the built-in SAS 80/81 poll keeper for MUX cabinets (.171).
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.171',
    [string] $SasComPort = 'COM4',
    [int]    $WarmupSec = 8,
    [int]    $WatchSec = 20,
    [switch] $ProbeOnly,
    [switch] $KeepRunning,
    [switch] $NoAutoWake,
    [switch] $NoAutoBootstrap
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\LabAccess.ps1"
. "$PSScriptRoot\LabRemoteTransport.ps1"
. "$PSScriptRoot\LabSasPollDiagnostics.ps1"
$script:LabCredential = Get-LabCredential
$script:LabPsExecArgs = @(Get-LabPsExecArgs)
$psExecPath = 'C:\Tools\PSTools\PsExec.exe'
$bootstrap = Initialize-CabinetInjectPrerequisites -Computer $ComputerName -Credential $script:LabCredential `
    -PsExecPath $psExecPath -PsExecAuthArgs $script:LabPsExecArgs -NoAutoBootstrap:$NoAutoBootstrap
$script:LabPsExecArgs = @($bootstrap.AuthArgs)
$script:InjectBootstrapReport = $bootstrap
function Get-MuxKeeperScript {
    $candidates = @(
        (Join-Path $PSScriptRoot 'scripts\sas_poll_keeper.py'),
        (Join-Path $PSScriptRoot 'scripts\sas_poll_keeper_standalone.py')
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}
function Get-MuxSasmsgrLogPath {
    param([string] $Computer, [datetime] $Date = (Get-Date))
    $dateStr = $Date.ToString('yyyy-MM-dd')
    $subs = @('GoldClub.Aurum.Services sasmsgr of SASControler1', 'GoldClub.Aurum.Services SASControler1')
    foreach ($sub in $subs) {
        $path = "\\$Computer\c`$\Goldclub\var\log\$sub\$dateStr.log"
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return "\\$Computer\c`$\Goldclub\var\log\$($subs[0])\$dateStr.log"
}
function Test-MuxCabinetPollsRecent {
    param([string] $Computer, [int] $WithinSeconds = 15)
    $logPath = Get-MuxSasmsgrLogPath -Computer $Computer
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $cutoff = (Get-Date).ToUniversalTime().AddSeconds(-[math]::Abs($WithinSeconds))
    foreach ($line in (Get-Content -LiteralPath $logPath -Tail 80 -ErrorAction SilentlyContinue)) {
        if ($line -notmatch 'qGMID1:8[01]\s*$') { continue }
        if ($line -notmatch '^(\S+)') { continue }
        try {
            $ts = [datetime]::Parse($Matches[1])
            if ($ts.Kind -eq [DateTimeKind]::Unspecified) { $ts = [datetime]::SpecifyKind($ts, [DateTimeKind]::Local).ToUniversalTime() }
            elseif ($ts.Kind -eq [DateTimeKind]::Local) { $ts = $ts.ToUniversalTime() }
            if ($ts -ge $cutoff) { return $true }
        } catch { continue }
    }
    return $false
}
function Invoke-MuxComProbe {
    param([string] $Port)
    $keeper = Get-MuxKeeperScript
    if (-not $keeper) { return @{ Ok = $false; Message = 'Missing scripts/sas_poll_keeper.py' } }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { return @{ Ok = $false; Message = 'python not found on PATH' } }
    $out = (& $py.Source $keeper '--probe' $Port 2>&1 | Out-String).Trim()
    return @{ Ok = ($LASTEXITCODE -eq 0); Message = $(if ($out) { $out } else { "probe exit $LASTEXITCODE" }) }
}
function Start-MuxPollKeeperProcess {
    param([string] $Port, [int] $WarmupSec)
    $keeper = Get-MuxKeeperScript
    $py = Get-Command python -ErrorAction SilentlyContinue
    $args = @($keeper, $Port, '--interval-ms', '200', '--warmup-s', ([string]([math]::Max(0.5, $WarmupSec))))
    return Start-Process -FilePath $py.Source -ArgumentList $args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
}
Write-Host ''
if ($VerbosePreference -eq 'Continue') {
    Write-Host '=== MUX COM4 SAS poll keeper ===' -ForegroundColor White
    Write-Host "Cabinet : $ComputerName  |  Host port: $SasComPort  |  Wire: raw 80/81 @19200"
    Write-Host 'Chain   : host COM4 -> MUX upstream -> cabinet COM11 -> CommCtrlSAS' -ForegroundColor DarkGray
    Write-Host ''
}
$autoWakeAttempted = $false
$autoWakeFailed = $false
if (-not $NoAutoWake) {
    $wakeResult = Invoke-CabinetSasPollAutoWakeIfNeeded -Computer $ComputerName -Credential $script:LabCredential `
        -PsExecAuthArgs $script:LabPsExecArgs
    if ($wakeResult.Attempted) {
        $autoWakeAttempted = $true
        $autoWakeFailed = -not $wakeResult.ServicesUp
        if (Test-MuxCabinetPollsRecent -Computer $ComputerName -WithinSeconds 12) {
            Write-Host '[+] Cabinet sasmsgr shows recent qGMID1:80/81 after SAS bridge wake.' -ForegroundColor Green
            exit 0
        }
    }
}
$probe = Invoke-MuxComProbe -Port $SasComPort
if ($probe.Ok) { Write-Host "[+] $($probe.Message)" -ForegroundColor Green }
else {
    $diag = Get-CabinetSasPollFailureDiagnosis -Computer $ComputerName -Credential $script:LabCredential `
        -PsExecAuthArgs $script:LabPsExecArgs -Port $SasComPort `
        -ComProbeResult @{ Ok = $false; Message = $probe.Message } `
        -AutoWakeAttempted:$autoWakeAttempted -AutoWakeFailed:$autoWakeFailed
    Write-CabinetSasPollAbort -Diagnosis $diag -Context 'mux_keeper'
    exit 1
}
if ($ProbeOnly) { Write-Host '[+] Probe-only OK.' -ForegroundColor Green; exit 0 }
if (Test-MuxCabinetPollsRecent -Computer $ComputerName -WithinSeconds 12) {
    Write-Host '[+] Cabinet sasmsgr already shows recent qGMID1:80/81.' -ForegroundColor Green
    exit 0
}
Write-Host "[*] Starting poll keeper on $SasComPort..." -ForegroundColor Cyan
$proc = $null
try {
    $proc = Start-MuxPollKeeperProcess -Port $SasComPort -WarmupSec $WarmupSec
    $deadline = (Get-Date).AddSeconds([math]::Max(1, $WatchSec))
    $ok = $false
    do {
        if (Test-MuxCabinetPollsRecent -Computer $ComputerName -WithinSeconds 8) { $ok = $true; break }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    if ($ok) {
        Write-Host '[+] Cabinet sasmsgr shows live 80/81 - MUX path working.' -ForegroundColor Green
        if ($KeepRunning) { while (-not $proc.HasExited) { Start-Sleep -Seconds 1 } }
        exit 0
    }
    $diag = Get-CabinetSasPollFailureDiagnosis -Computer $ComputerName -Credential $script:LabCredential `
        -PsExecAuthArgs $script:LabPsExecArgs -Port $SasComPort `
        -ComProbeResult @{ Ok = $true; Message = $probe.Message } -PollKeeperStarted -PollWaitSec $WatchSec `
        -AutoWakeAttempted:$autoWakeAttempted -AutoWakeFailed:$autoWakeFailed
    Write-CabinetSasPollAbort -Diagnosis $diag -Context 'mux_keeper'
    exit 2
}
finally {
    if ($proc -and -not $proc.HasExited -and -not $KeepRunning) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
