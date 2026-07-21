<#
.SYNOPSIS
    Capture Aurum remoting / SAS bridge TCP traffic on the lab cabinet.

.DESCRIPTION
    Stages WinDivert netdump.exe on the cabinet and captures TCP packets for the
    selected ports. This is for observing what traffic is actually sent to Aurum;
    it does not wait for an AFT transfer to commit.

    Useful ports:
      50010 - GM2AU remoting
      50011 - SASControler1 remoting
      31100/31150 - CommCtrlSAS loopback bridge

.EXAMPLE
    .\Invoke-AurumTrafficCapture.ps1 -Seconds 20 -Ports 50010,50011

.EXAMPLE
    .\Invoke-AurumTrafficCapture.ps1 -Seconds 30 -Ports 50010,50011,31100,31150 `
      -TriggerCommand '.\Send-TestAft1000.ps1 -Send'
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe',
    [int]    $Seconds      = 20,
    [int[]]  $Ports        = @(50010, 50011),
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [string] $TriggerCommand,
    [switch] $Raw
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$dll = Join-Path $WinDivertDir 'WinDivert.dll'
$sys = Join-Path $WinDivertDir 'WinDivert64.sys'
$exe = Join-Path $WinDivertDir 'netdump.exe'
foreach ($f in @($PsExecPath, $dll, $sys, $exe)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "Required file not found: $f" }
}

$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\aurumtap"
$dumpUnc      = Join-Path $remoteDirUnc 'dump.txt'
$errUnc       = Join-Path $remoteDirUnc 'err.txt'
$statusUnc    = Join-Path $remoteDirUnc 'capture_status.txt'

$portCond = ($Ports | ForEach-Object { "tcp.SrcPort == $_ or tcp.DstPort == $_" }) -join ' or '
$filter = "tcp and ($portCond) and tcp.PayloadLength > 0"

Write-Host ''
Write-Host '=== Aurum TCP capture (read-only) ===' -ForegroundColor White
Write-Host "Cabinet: $ComputerName  |  Ports: $($Ports -join ', ')  |  Window: ${Seconds}s"
Write-Host "Filter : $filter" -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $remoteDirUnc)) {
    New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null
}
Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $exe -Destination $remoteDirUnc -Force

$remote = @'
$ErrorActionPreference = "SilentlyContinue"
$wd  = "C:\Windows\Temp\aurumtap"
$flt = "__FILTER__"
$argLine = '"' + $flt + '"'
Remove-Item "$wd\dump.txt","$wd\err.txt","$wd\capture_status.txt" -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$wd\netdump.exe" -ArgumentList $argLine -WorkingDirectory $wd `
    -RedirectStandardOutput "$wd\dump.txt" -RedirectStandardError "$wd\err.txt" -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 1500
$earlyExit = $false
try { $earlyExit = $p.HasExited } catch {}
Set-Content "$wd\capture_status.txt" ("STARTED={0}`r`nPID={1}`r`nFILTER={2}" -f (-not $earlyExit), $p.Id, $flt)
if (-not $earlyExit) { Start-Sleep -Seconds __SECONDS__ }
try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}
Start-Sleep -Milliseconds 400
sc.exe stop WinDivert   | Out-Null
sc.exe delete WinDivert | Out-Null
$dumpLen = 0
try { $dumpLen = (Get-Item "$wd\dump.txt" -ErrorAction Stop).Length } catch {}
Add-Content "$wd\capture_status.txt" ("DUMP_BYTES={0}" -f $dumpLen)
'@
$remote = $remote.Replace('__FILTER__', $filter).Replace('__SECONDS__', [string]$Seconds)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))

Write-Host '[*] Starting remote capture...' -ForegroundColor Cyan
$capture = Start-Process -FilePath $PsExecPath -ArgumentList @("\\$ComputerName", '-accepteula', '-s', 'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $enc) -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 3

if ($TriggerCommand) {
    Write-Host "[*] Running trigger: $TriggerCommand" -ForegroundColor Cyan
    $trigger = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $TriggerCommand) -PassThru -NoNewWindow
    $trigger.WaitForExit()
    Write-Host "[+] Trigger exited: $($trigger.ExitCode)" -ForegroundColor Green
}
else {
    Write-Host '[*] Capture is running. Trigger tester or direct remoting now.' -ForegroundColor Yellow
}

$capture.WaitForExit()

if (Test-Path -LiteralPath $statusUnc) {
    Write-Host '--- capture status ---' -ForegroundColor Cyan
    Get-Content -LiteralPath $statusUnc | ForEach-Object { Write-Host $_ }
}
if (Test-Path -LiteralPath $errUnc) {
    $err = Get-Content -LiteralPath $errUnc -Raw
    if ($err) {
        Write-Host '--- capture stderr ---' -ForegroundColor Yellow
        Write-Host $err
    }
}

if (-not (Test-Path -LiteralPath $dumpUnc)) {
    Write-Host 'No dump produced.' -ForegroundColor Red
    exit 1
}

$len = (Get-Item -LiteralPath $dumpUnc).Length
$pkts = 0
try { $pkts = (Select-String -LiteralPath $dumpUnc -Pattern '^Packet \[' | Measure-Object).Count } catch {}
Write-Host ''
Write-Host ("[+] Capture complete: {0} packets, {1:N0} bytes" -f $pkts, $len) -ForegroundColor Green
Write-Host "    $dumpUnc" -ForegroundColor DarkGray

if ($Raw) { exit 0 }

$lines = Get-Content -LiteralPath $dumpUnc
$shown = 0
$ts = ''; $src = ''; $dst = ''; $payHex = ''; $hexRow = 0

function Flush-Packet {
    param([string] $Ts, [string] $Src, [string] $Dst, [string] $PayHex)
    if (-not $PayHex -or $script:shown -ge 20) { return }
    $bytes = for ($i = 0; $i -lt $PayHex.Length; $i += 2) { [Convert]::ToByte($PayHex.Substring($i, 2), 16) }
    $ascii = -join ($bytes | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
    if ($ascii.Length -gt 120) { $ascii = $ascii.Substring(0, 120) + '...' }
    Write-Host ("[{0}] {1}->{2} ascii='{3}'" -f $Ts, $Src, $Dst, $ascii)
    $script:shown++
}

foreach ($ln in $lines) {
    if ($ln -match '^Packet \[Timestamp=([0-9.]+)') {
        Flush-Packet $ts $src $dst $payHex
        $ts = $Matches[1]; $src = ''; $dst = ''; $payHex = ''; $hexRow = 0
        continue
    }
    if ($ln -match 'TCP \[SrcPort=(\d+) DstPort=(\d+)') {
        $src = $Matches[1]; $dst = $Matches[2]; continue
    }
    if ($ln -match '^\s+([0-9A-Fa-f]{2,})\s*$') {
        $h = $Matches[1]
        if ($h.Length % 2 -eq 0) {
            $hexRow++
            if ($hexRow -gt 2) { $payHex += $h }
        }
    }
}
Flush-Packet $ts $src $dst $payHex
