<#
.SYNOPSIS
    PHASE 1 (read-only) of the TCP-forge research: deploy WinDivert to the EGM
    and SNIFF the CommCtrl<->consumer loopback flow on :30800 using the official
    netdump.exe (WINDIVERT_FLAG_SNIFF - copies packets, never blocks).

.DESCRIPTION
    Purpose:
      1. Confirm the WinDivert kernel driver actually LOADS on the cabinet
         (Secure Boot / driver signing gate) - in the safest possible mode.
      2. Capture the real TCP SeqNum/AckNum/flags + payload of the live
         CommCtrl->consumer connection, including a physical Dallas tap, so the
         Phase-2 splicer can inject WITHOUT desyncing the connection.

    SNIFF mode cannot RST or desync anything; it only observes. WinDivert
    auto-removes its driver when netdump exits (default flags), so nothing
    permanent is loaded afterward. Files staged to C:\Windows\Temp\wd are temp.

.PARAMETER ComputerName
    EGM host. Default 10.0.0.90 (lab cabinet).

.PARAMETER Seconds
    How long to sniff. Tap the physical Dallas key during this window. Default 40.

.PARAMETER Port
    Hardware/Dallas TCP port. Default 30800.

.PARAMETER WinDivertDir
    Local folder with x64 WinDivert.dll, WinDivert64.sys, netdump.exe.

.EXAMPLE
    cd C:\Tools\PSTools
    & 'C:\Users\Ezbogar\GoldclubLogInvestigator\Invoke-DallasSniffRemote.ps1' -ComputerName 10.0.0.90 -Seconds 40
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe',
    [int]    $Seconds      = 40,
    [int]    $Port         = 30800,
    [string] $WinDivertDir = 'C:\Tools\WinDivert\extracted\WinDivert-2.2.2-A\x64'
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
$errUnc       = Join-Path $remoteDirUnc 'err.txt'
$statusUnc    = Join-Path $remoteDirUnc 'recon_status.txt'

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host ' Dallas TCP sniff (Phase 1, read-only WinDivert SNIFF)'    -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target  : \\{0}" -f $ComputerName) -ForegroundColor Gray
Write-Host (" Filter  : loopback tcp port {0}" -f $Port) -ForegroundColor Gray
Write-Host (" Window  : {0}s  (TAP THE PHYSICAL KEY during this time)" -f $Seconds) -ForegroundColor Yellow
Write-Host '----------------------------------------------------------' -ForegroundColor Cyan

# Stage WinDivert + netdump to the EGM temp dir.
if (-not (Test-Path -LiteralPath $remoteDirUnc)) { New-Item -ItemType Directory -Force -Path $remoteDirUnc | Out-Null }
Copy-Item -LiteralPath $dll -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $sys -Destination $remoteDirUnc -Force
Copy-Item -LiteralPath $exe -Destination $remoteDirUnc -Force
Write-Host ' Staged WinDivert.dll + WinDivert64.sys + netdump.exe.' -ForegroundColor Green

# Remote runner: launch netdump (sniff), wait, stop, record status.
$remote = @'
$ErrorActionPreference = "SilentlyContinue"
$wd  = "C:\Windows\Temp\wd"
$flt = "loopback and tcp and (tcp.SrcPort == __PORT__ or tcp.DstPort == __PORT__)"
$argLine = '"' + $flt + '"'   # one quoted arg so netdump sees argc==2
Remove-Item "$wd\dump.txt","$wd\err.txt","$wd\recon_status.txt" -Force -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$wd\netdump.exe" -ArgumentList $argLine -WorkingDirectory $wd `
    -RedirectStandardOutput "$wd\dump.txt" -RedirectStandardError "$wd\err.txt" -PassThru -WindowStyle Hidden
Start-Sleep -Milliseconds 1500
$earlyExit = $false
try { $earlyExit = $p.HasExited } catch {}
if (-not $earlyExit) { Start-Sleep -Seconds __SECONDS__ }
try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch {}
Start-Sleep -Milliseconds 400
# netdump is force-killed, so WinDivert's own uninstall never runs -> remove the driver here.
sc.exe stop WinDivert   | Out-Null
sc.exe delete WinDivert | Out-Null
$dumpLen = 0; $errTxt = ""
try { $dumpLen = (Get-Item "$wd\dump.txt" -ErrorAction Stop).Length } catch {}
try { $errTxt  = (Get-Content "$wd\err.txt" -Raw -ErrorAction Stop) } catch {}
$pkts = 0
try { $pkts = (Select-String -Path "$wd\dump.txt" -Pattern "^Packet \[" -ErrorAction Stop | Measure-Object).Count } catch {}
$lines = @()
$lines += "EARLY_EXIT=$earlyExit"
$lines += "DUMP_BYTES=$dumpLen"
$lines += "PACKETS=$pkts"
$lines += "STDERR=$errTxt"
Set-Content "$wd\recon_status.txt" ($lines -join [Environment]::NewLine)
'@
$remote = $remote.Replace('__PORT__', [string]$Port).Replace('__SECONDS__', [string]$Seconds)

$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote))

Write-Host ' Loading WinDivert + sniffing on the EGM...' -ForegroundColor Cyan
Write-Host '   >>> TAP THE PHYSICAL DALLAS KEY NOW (a couple of times) <<<' -ForegroundColor Yellow
& $PsExecPath "\\$ComputerName" -accepteula -s powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc | Out-Null

Start-Sleep -Milliseconds 600

Write-Host ''
Write-Host ' --- Recon status ---' -ForegroundColor Cyan
if (Test-Path -LiteralPath $statusUnc) {
    $status = Get-Content -LiteralPath $statusUnc -Raw
    Write-Host $status
    if ($status -match 'EARLY_EXIT=True') {
        Write-Host ''
        Write-Host ' WinDivert exited immediately -> driver likely FAILED to load on the cabinet.' -ForegroundColor Red
        Write-Host ' (Secure Boot / driver signing may be blocking it.) See STDERR above.' -ForegroundColor Red
        return
    }
} else {
    Write-Host ' No status file - the remote runner may not have executed.' -ForegroundColor Red
    return
}

if (Test-Path -LiteralPath $errUnc) {
    $e = Get-Content -LiteralPath $errUnc -Raw
    if ($e) { $e = $e.Trim() }
    if ($e) { Write-Host (' STDERR: ' + $e) -ForegroundColor Yellow }
}

Write-Host ''
Write-Host (' --- TCP frames on :{0} (SeqNum/AckNum/flags) ---' -f $Port) -ForegroundColor Cyan
if (Test-Path -LiteralPath $dumpUnc) {
    $raw = Get-Content -LiteralPath $dumpUnc -Raw
    # Show each packet block's TCP line + any ASCII payload that looks like data.
    $tcpLines = [regex]::Matches($raw, 'TCP \[[^\]]*\]')
    if ($tcpLines.Count -eq 0) {
        Write-Host ' No TCP frames captured (connection idle? try a longer window / tap during it).' -ForegroundColor Yellow
    } else {
        $i = 0
        foreach ($m in $tcpLines) {
            $i++
            if ($i -gt 60) { Write-Host ('   ... (+{0} more frames)' -f ($tcpLines.Count - 60)) -ForegroundColor DarkGray; break }
            Write-Host ('   ' + $m.Value) -ForegroundColor Gray
        }
    }
    # Flag any frame whose payload carries the Dallas ROM.
    if ($raw -match '01D68A721B000019' -or $raw -match '0\.1\.D\.6\.8') {
        Write-Host ''
        Write-Host ' >>> Dallas ROM bytes appear in a captured payload - this is the inject template. <<<' -ForegroundColor Green
    }
    Write-Host ''
    Write-Host (' Full capture on EGM: {0}' -f $dumpUnc) -ForegroundColor DarkGray
} else {
    Write-Host ' No dump.txt produced.' -ForegroundColor Red
}
