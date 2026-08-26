<#
.SYNOPSIS
    Prepare BIWIN USB disk for Macrium full-disk restore (empty GPT, no partitions).
#>
#Requires -RunAsAdministrator
param([switch]$WhatIf, [switch]$Force)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$RepoLog = Join-Path $repoRoot 'logs\format-biwin-restore.log'
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($RepoLog, $line + [Environment]::NewLine, $utf8)
}

function Invoke-DiskPartScript([string[]]$Commands) {
    $tmp = Join-Path $env:TEMP ("format-biwin-{0}.txt" -f [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllLines($tmp, ($Commands + 'exit'), $utf8)
        Log ('diskpart: ' + ($Commands -join '; '))
        cmd /c "diskpart /s `"$tmp`"" 2>&1 | ForEach-Object { Log "  $_" }
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

[IO.File]::WriteAllText($RepoLog, '', $utf8)
Log '=== Prepare BIWIN for Macrium (empty GPT) ==='
Update-HostStorageCache -ErrorAction SilentlyContinue | Out-Null
Invoke-DiskPartScript @('rescan')
Start-Sleep -Seconds 2

$biwin = Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'BIWIN' } | Select-Object -First 1
if (-not $biwin) { Log 'FAIL: BIWIN not visible'; exit 1 }
$num = [int]$biwin.Number
if ($biwin.FriendlyName -match 'Samsung|KINGSTON') { Log 'ABORT: not BIWIN'; exit 2 }
$sizeGb = [math]::Round($biwin.Size / 1GB, 1)
Log "Target: Disk $num — $($biwin.FriendlyName) — $sizeGb GB"
if ($WhatIf) { exit 0 }
Write-Host "WILL ERASE Disk $num BIWIN ($sizeGb GB)" -ForegroundColor Red
if (-not $Force -and (Read-Host 'Type YES') -ne 'YES') { exit 0 }
Invoke-DiskPartScript @("select disk $num", 'detail disk', 'online disk noerr', 'attributes disk clear readonly noerr', 'clean', 'convert gpt', 'list disk', 'list partition')
Log 'Done — empty GPT, no partitions. Macrium whole-disk restore next.'
exit 0
