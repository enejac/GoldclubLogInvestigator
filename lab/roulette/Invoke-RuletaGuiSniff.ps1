<#
.SYNOPSIS
    READ-ONLY WinDivert sniff of Godot <-> ruleta middleware HTTP (:8090).

.DESCRIPTION
    Compiles and runs probes\HttpGuiSniff.cs on the cabinet (SNIFF | RECV_ONLY).
    Captures PUT /api/action and interesting GET /api/data responses used to map
    Layout1 (futura_doublezero square cloth). Does not inject or divert traffic.

.EXAMPLE
    .\lab\roulette\Invoke-RuletaGuiSniff.ps1 -Seconds 40

.EXAMPLE
    .\lab\roulette\Invoke-RuletaGuiSniff.ps1 -IP 10.0.0.90 -Seconds 60 -Ports 8090,8083
#>
[CmdletBinding()]
param(
    [Alias('IP')]
    [string] $ComputerName = '10.0.0.90',
    [int]    $Seconds = 45,
    [int[]]  $Ports = @(8090),
    [string] $WinDivertDir = 'C:\Tools\WinDivert\x64',
    [string] $OutDir = '',
    [pscredential] $Credential
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $PSScriptRoot) { throw 'PSScriptRoot is empty; run as a .ps1 file.' }
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot 'probes\HttpGuiSniff.cs'))) {
    # lab\roulette -> repo root is two parents; tolerate invoke from lab\
    $alt = Split-Path -Parent $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $alt 'probes\HttpGuiSniff.cs')) { $RepoRoot = $alt }
}
if (-not $OutDir) {
    $OutDir = Join-Path $RepoRoot '_tmp_logs\gui-sniff'
}
$labAccess = Join-Path $RepoRoot 'LabAccess.ps1'
$labRemote = Join-Path $RepoRoot 'lab\LabRemoteTransport.ps1'
if (-not (Test-Path -LiteralPath $labAccess)) { throw "Missing $labAccess" }
if (-not (Test-Path -LiteralPath $labRemote)) { throw "Missing $labRemote" }
. $labAccess
. $labRemote

if (-not $Credential) { $Credential = Get-LabCredential }
Initialize-LabSmbCredential -Ip @($ComputerName) | Out-Null
$labCtx = Initialize-LabRemoteContext -ComputerName $ComputerName -Credential $Credential
$Credential = $labCtx.Credential

$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$csSrc = Join-Path $RepoRoot 'probes\HttpGuiSniff.cs'
foreach ($f in @($dll, $sys, $csSrc)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}
if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}

$portsCsv = ($Ports -join ',')
$totalMs = [int]($Seconds * 1000)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dumpName = "gui8090-$($ComputerName.Split('.')[-1])-$stamp.txt"
$dumpLocal = Join-Path $OutDir $dumpName

Write-Host ''
Write-Host '=== Ruleta GUI HTTP sniff (READ-ONLY, :8090) ===' -ForegroundColor White
Write-Host "Cabinet : $ComputerName  |  Window: ${Seconds}s  |  Ports: $portsCsv"
Write-Host "Output  : $dumpLocal" -ForegroundColor DarkGray

$srcHash = (Get-FileHash -LiteralPath $csSrc -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$remoteBaseDirUnc = "\\$ComputerName\c`$\Windows\Temp\investigator_guisniff"
$remoteBinName = "httpguisniff-$srcHash"
$remoteDirUnc = Join-Path $remoteBaseDirUnc $remoteBinName
$remoteRunPath = "C:\Windows\Temp\investigator_guisniff\$remoteBinName"
$exeUnc = Join-Path $remoteDirUnc 'HttpGuiSniff.exe'
$remoteDump = "$remoteRunPath\$dumpName"
$dumpUnc = Join-Path $remoteDirUnc $dumpName
$outUnc = Join-Path $remoteDirUnc 'sniff_run.txt'

if (Test-Path -LiteralPath $exeUnc) {
    Write-Host "[*] Reusing cached HttpGuiSniff.exe ($remoteBinName)" -ForegroundColor Cyan
}
else {
    New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
    Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
    Copy-Item -LiteralPath $csSrc -Destination $remoteDirUnc -Force
    Write-Host "[*] Staged HttpGuiSniff sources + WinDivert ($remoteBinName)" -ForegroundColor Cyan
}

$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$wd  = "__REMOTE_WD__"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$exe = "$wd\HttpGuiSniff.exe"
$dump = "__DUMP__"
$out = "$wd\sniff_run.txt"
Remove-Item $dump,$out -Force -ErrorAction SilentlyContinue
try {
    if (-not (Test-Path $exe)) {
        & $csc /nologo /platform:x64 /optimize+ /out:"$exe" "$wd\HttpGuiSniff.cs" 2>&1 | Out-File -FilePath $out -Encoding utf8 -Append
    }
    if (-not (Test-Path $exe)) {
        "COMPILE_FAILED" | Out-File -FilePath $out -Encoding utf8 -Append
    } else {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # UTF-8 dump (PowerShell `>` redirection is UTF-16 and breaks Python parsers).
            $p = Start-Process -FilePath $exe -ArgumentList @('__MS__','__PORTS__') -WorkingDirectory $wd `
                -RedirectStandardOutput $dump -RedirectStandardError $out -NoNewWindow -PassThru -Wait
            "EXITCODE=$($p.ExitCode)" | Out-File -FilePath $out -Encoding utf8 -Append
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

$winRmTimeoutMs = ([Math]::Max($Seconds + 45, 90)) * 1000
$psExecTimeoutSec = [Math]::Max($Seconds + 30, 90)
$psExecPath = 'C:\Tools\PSTools\PsExec.exe'
$transportPlan = Get-LabRemoteTransportPlan -ComputerName $ComputerName -Credential $Credential `
    -CredentialFromLab:$labCtx.CredentialFromLab
$sniffLog = Join-Path $env:TEMP ("ruleta_gui_sniff_{0}.log" -f $stamp)

Write-Host "[*] Starting remote HTTP GUI sniff for ${Seconds}s ..." -ForegroundColor Cyan
$used = Invoke-LabRemoteEncodedWithFallback -TransportOrder $transportPlan.TransportOrder `
    -Computer $ComputerName -Enc $enc -LogPath $sniffLog -Credential $Credential `
    -PsExecPath $psExecPath -PsExecAuthArgs $labCtx.PsExecAuthArgs `
    -WinRmOperationTimeoutMs $winRmTimeoutMs -PsExecTimeoutSec $psExecTimeoutSec
if (-not $used) { throw 'No remote transport succeeded for HttpGuiSniff.' }
Write-Host "[+] Sniff finished via $used." -ForegroundColor Green

if (Test-Path -LiteralPath $outUnc) {
    $runlog = Get-Content -LiteralPath $outUnc -Raw
    if ($runlog) {
        Write-Host '--- remote run log ---' -ForegroundColor DarkGray
        Write-Host $runlog.Trim()
    }
}
if (-not (Test-Path -LiteralPath $dumpUnc)) {
    Write-Host "[!] No dump at $dumpUnc" -ForegroundColor Red
    exit 1
}
Copy-Item -LiteralPath $dumpUnc -Destination $dumpLocal -Force
$len = (Get-Item -LiteralPath $dumpLocal).Length
$http = 0
try { $http = (Select-String -LiteralPath $dumpLocal -Pattern '^HTTP ' | Measure-Object).Count } catch {}
Write-Host ''
Write-Host ("[+] Capture complete: {0} HTTP messages, {1:N0} bytes" -f $http, $len) -ForegroundColor Green
Write-Host "    $dumpLocal" -ForegroundColor DarkGray
Write-Output $dumpLocal
