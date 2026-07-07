<#
.SYNOPSIS
    Read-only multi-port WinDivert sniff of the EGM hardware loopback bus, with
    payload decoding. Use it to find which localhost port (and what bytes) a
    physical button press produces.

.DESCRIPTION
    Captures loopback TCP on a set of candidate ports (default: the CommCtrl
    hardware ports OneHand reads plus the hwsubsys aggregator), runs the official
    WinDivert netdump.exe in SNIFF mode (copies packets, never blocks/RSTs), then
    parses dump.txt and prints only packets that CARRY A PAYLOAD -- with
    timestamp, direction, port, and ASCII+hex -- so a button frame stands out
    against the idle heartbeat traffic.

    SNIFF cannot desync anything. The driver is removed when done.

.PARAMETER Ports
    Localhost TCP ports to watch. Default 30200,30700,30600,25071.

.PARAMETER Seconds
    Capture window. PRESS THE PHYSICAL BUTTON (several times) during this window.

.EXAMPLE
    .\Invoke-HwSniffRemote.ps1 -ComputerName 10.0.0.90 -Seconds 30 -Ports 30200,30700,30600,25071
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe',
    [int]    $Seconds      = 30,
    [int[]]  $Ports        = @(30200, 30700, 30600, 25071),
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64',
    [switch] $ShowHeartbeat,
    [switch] $ButtonsOnly,
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

$remoteDirUnc = "\\$ComputerName\c`$\Windows\Temp\wd"
$dumpUnc      = Join-Path $remoteDirUnc 'dump.txt'
$statusUnc    = Join-Path $remoteDirUnc 'recon_status.txt'

$portCond = ($Ports | ForEach-Object { "tcp.SrcPort == $_ or tcp.DstPort == $_" }) -join ' or '
$filter   = "loopback and tcp and ($portCond)"

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host ' EGM hardware bus sniff (read-only, multi-port)'           -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target  : \\{0}" -f $ComputerName) -ForegroundColor Gray
Write-Host (" Ports   : {0}" -f ($Ports -join ', ')) -ForegroundColor Gray
Write-Host (" Window  : {0}s  -- PRESS THE PHYSICAL BUTTON DURING THIS TIME" -f $Seconds) -ForegroundColor Yellow
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $remoteDirUnc)) { New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null }
Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $exe -Destination $remoteDirUnc -Force
Write-Host ' Staged WinDivert + netdump.' -ForegroundColor Green

$remote = @'
$ErrorActionPreference = "SilentlyContinue"
$wd  = "C:\Windows\Temp\wd"
$flt = "__FILTER__"
$argLine = '"' + $flt + '"'
Remove-Item "$wd\dump.txt","$wd\err.txt","$wd\recon_status.txt" -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$wd\netdump.exe" -ArgumentList $argLine -WorkingDirectory $wd `
    -RedirectStandardOutput "$wd\dump.txt" -RedirectStandardError "$wd\err.txt" -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 1500
$earlyExit = $false
try { $earlyExit = $p.HasExited } catch {}
if (-not $earlyExit) { Start-Sleep -Seconds __SECONDS__ }
try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}
Start-Sleep -Milliseconds 400
sc.exe stop WinDivert   | Out-Null
sc.exe delete WinDivert | Out-Null
$dumpLen = 0
try { $dumpLen = (Get-Item "$wd\dump.txt" -ErrorAction Stop).Length } catch {}
Set-Content "$wd\recon_status.txt" ("EARLY_EXIT=$earlyExit`r`nDUMP_BYTES=$dumpLen")
'@
$remote = $remote.Replace('__FILTER__', $filter).Replace('__SECONDS__', [string]$Seconds)
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))

Write-Host ' Loading WinDivert + sniffing...' -ForegroundColor Cyan
Write-Host '   >>> PRESS THE PHYSICAL SPIN BUTTON NOW (a few times, ~2s apart) <<<' -ForegroundColor Yellow
& $PsExecPath "\\$ComputerName" -accepteula -s powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc | Out-Null

Start-Sleep -Milliseconds 600
if (Test-Path -LiteralPath $statusUnc) {
    Write-Host ' --- status ---' -ForegroundColor Cyan
    Get-Content -LiteralPath $statusUnc | ForEach-Object { Write-Host "  $_" }
}

if (-not (Test-Path -LiteralPath $dumpUnc)) { Write-Host ' No dump produced.' -ForegroundColor Red; return }

if ($Raw) {
    # Binary protocol (e.g. SAS): skip ASCII frame parsing. Just report size and
    # leave the full hex dump on the EGM for targeted decoding.
    $len = (Get-Item -LiteralPath $dumpUnc).Length
    $pkts = 0
    try { $pkts = (Select-String -LiteralPath $dumpUnc -Pattern '^Packet \[' | Measure-Object).Count } catch {}
    Write-Host ''
    Write-Host (' RAW capture: {0} packets, {1:N0} bytes' -f $pkts, $len) -ForegroundColor Green
    Write-Host (' Full hex dump on EGM: {0}' -f $dumpUnc) -ForegroundColor DarkGray
    return
}

# Parse packets, keep only those with a payload, decode ASCII + hex.
$lines = Get-Content -LiteralPath $dumpUnc
$ts = ''; $src = ''; $dst = ''; $payHex = ''
$known = @('ON', 'POWER ON', '')   # heartbeat payloads to suppress unless -ShowHeartbeat
$hits = New-Object System.Collections.Generic.List[string]

function Flush {
    param($ts,$src,$dst,$payHex)
    if (-not $payHex) { return }
    $bytes = for ($i = 0; $i -lt $payHex.Length; $i += 2) { [Convert]::ToByte($payHex.Substring($i,2),16) }
    $ascii = -join ($bytes | ForEach-Object { if ($_ -ge 32 -and $_ -lt 127) { [char]$_ } else { '.' } })
    $clean = ($ascii -replace '[^\x20-\x7E]','').Trim()
    if ($script:ButtonsOnly) {
        # A real button press is an UNSOLICITED server->client push of a bare
        # button code. Server ports are low (<40000); client ports are ephemeral.
        # Button codes sit in 200-499; poll/lamp-ack status codes are 6xx/7xx/8xx.
        $isServerPush = $false
        try { $isServerPush = ([int]$src -lt 40000) } catch {}
        if ($isServerPush -and $clean -match '^\d{2,3}$' -and [int]$clean -ge 200 -and [int]$clean -le 499) {
            $script:hits.Add(("[{0,10}]  CODE {1}   ({2}->{3})" -f $ts,$clean,$src,$dst))
        }
        return
    }
    if (-not $script:ShowHeartbeat -and ($known -contains $clean)) { return }
    $script:hits.Add(("[{0}] {1}->{2}  ascii='{3}'  hex={4}" -f $ts,$src,$dst,$ascii,$payHex))
}

# Within each packet the indented hex rows are: IP header (20B), TCP header
# (20B), then payload row(s). Skip the first two hex rows; the rest is payload.
$hexRow = 0
foreach ($ln in $lines) {
    if ($ln -match '^Packet \[Timestamp=([0-9.]+)') {
        Flush $ts $src $dst $payHex
        $ts = $Matches[1]; $src=''; $dst=''; $payHex=''; $hexRow = 0
        continue
    }
    if ($ln -match 'TCP \[SrcPort=(\d+) DstPort=(\d+)') { $src=$Matches[1]; $dst=$Matches[2]; continue }
    if ($ln -match '^\s+([0-9A-Fa-f]{2,})\s*$') {
        $h = $Matches[1]
        if ($h.Length % 2 -eq 0) {
            $hexRow++
            if ($hexRow -gt 2) { $payHex += $h }   # rows 1-2 are IP+TCP headers
        }
    }
}
Flush $ts $src $dst $payHex

Write-Host ''
Write-Host (' --- payload frames (heartbeat {0}) ---' -f ($(if($ShowHeartbeat){'shown'}else{'hidden'}))) -ForegroundColor Cyan
if ($hits.Count -eq 0) {
    Write-Host '  (none) - no non-heartbeat payloads captured. Press during the window, or try other ports.' -ForegroundColor Yellow
} else {
    $hits | ForEach-Object { Write-Host "  $_" -ForegroundColor Green }
}
Write-Host ''
Write-Host (' Full capture on EGM: {0}' -f $dumpUnc) -ForegroundColor DarkGray
