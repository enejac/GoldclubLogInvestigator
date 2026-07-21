<#
.SYNOPSIS
    Recover a cabinet after a failed Dallas MITM run (especially -PauseBootstrap).

.DESCRIPTION
    Kills Bootstrap processes stuck in Session 0 (started by PsExec -s), verifies
    AxiomtekHardware.xml is on port 30800, and optionally reboots so the real
    autostart chain brings Bootstrap + game back in Session 1.

.EXAMPLE
    .\Recover-EgmCabinet.ps1 -ComputerName 10.0.0.90
    .\Recover-EgmCabinet.ps1 -ComputerName 10.0.0.90 -Reboot
#>
[CmdletBinding()]
param(
    [string] $ComputerName = '10.0.0.90',
    [string] $PsExecPath   = 'C:\Tools\PSTools\PsExec.exe',
    [string] $ConfigUnc    = '\\10.0.0.90\c$\Goldclub\slot\hwdrivers\AxiomtekHardware.xml',
    [switch] $Reboot,
    [int]    $RebootDelaySeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$recoverScript = @'
$ErrorActionPreference = 'SilentlyContinue'
Write-Output "=== RECOVER START ==="
$killed = @()
foreach ($b in @(Get-Process -Name Bootstrap -ErrorAction SilentlyContinue)) {
    if ($b.SessionId -eq 0) {
        try { Stop-Process -Id $b.Id -Force; $killed += $b.Id } catch { }
    }
}
if ($killed.Count) { Write-Output ("KILLED Session-0 Bootstrap PIDs: " + ($killed -join ', ')) }
else { Write-Output 'No Session-0 Bootstrap to kill.' }
Get-Process Bootstrap,BiOS2,OneHand,game-start -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Output ('{0} pid={1} session={2}' -f $_.ProcessName, $_.Id, $_.SessionId) }
'@
if ($Reboot) {
    $recoverScript += @"

Write-Output 'REBOOT scheduled in $RebootDelaySeconds s...'
shutdown.exe /r /t $RebootDelaySeconds /c 'Goldclub cabinet recovery reboot' /f
"@
}

$remotePath = "\\$ComputerName\c`$\Windows\Temp\Recover-EgmCabinet.ps1"
$recoverScript | Set-Content -LiteralPath $remotePath -Encoding UTF8 -Force

Write-Host ''
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host ' EGM cabinet recovery' -ForegroundColor Cyan
Write-Host '==========================================================' -ForegroundColor Cyan
Write-Host (" Target : \\{0}" -f $ComputerName) -ForegroundColor Gray
Write-Host (" Reboot : {0}" -f $(if ($Reboot) { "yes (${RebootDelaySeconds}s delay)" } else { 'no (kill zombies only)' })) -ForegroundColor Gray

# Config safety on share (same byte replace as guard)
if (Test-Path -LiteralPath $ConfigUnc) {
    $bytes = [System.IO.File]::ReadAllBytes($ConfigUnc)
    $needle = [System.Text.Encoding]::ASCII.GetBytes('<port>40800</port>')
    $repl   = [System.Text.Encoding]::ASCII.GetBytes('<port>30800</port>')
    $idx = -1
    $limit = $bytes.Length - $needle.Length
    for ($i = 0; $i -le $limit; $i++) {
        $match = $true
        for ($j = 0; $j -lt $needle.Length; $j++) {
            if ($bytes[$i + $j] -ne $needle[$j]) { $match = $false; break }
        }
        if ($match) { $idx = $i; break }
    }
    if ($idx -ge 0) {
        $tailStart = $idx + $needle.Length
        $tailLen   = $bytes.Length - $tailStart
        $out = New-Object byte[] ($idx + $repl.Length + $tailLen)
        [Array]::Copy($bytes, 0, $out, 0, $idx)
        [Array]::Copy($repl, 0, $out, $idx, $repl.Length)
        [Array]::Copy($bytes, $tailStart, $out, ($idx + $repl.Length), $tailLen)
        [System.IO.File]::WriteAllBytes($ConfigUnc, $out)
        Write-Host ' Config   : reverted 40800 -> 30800 on share.' -ForegroundColor Green
    } else {
        Write-Host ' Config   : already 30800 (or no 40800 tag).' -ForegroundColor Green
    }
}

& $PsExecPath @("\\$ComputerName", '-accepteula', '-s', 'powershell.exe',
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'C:\Windows\Temp\Recover-EgmCabinet.ps1')
Write-Host ''
Write-Host ' Recovery command sent.' -ForegroundColor Green
if (-not $Reboot) {
    Write-Host ' If the game/BIOS UI is still blank, re-run with -Reboot for a clean autostart.' -ForegroundColor Yellow
}
