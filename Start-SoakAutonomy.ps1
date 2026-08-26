# Launch two-week soak supervisor + self-healing watchdog (no Cursor required).
param(
  [string]$Ip = "10.0.0.90",
  [string]$Until = "2026-08-17T07:30:00",
  [double]$WatchHours = 3
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutRoot = Join-Path $Root ("automation_runs\{0}_{1}_two_week_soak" -f $stamp, $Ip)
New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$ptr = Join-Path $Root "_tmp_logs\two_week_soak_active.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $ptr) | Out-Null
[System.IO.File]::WriteAllText($ptr, $OutRoot + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

Write-Host "Starting soak supervisor -> $OutRoot"
$sup = Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $Root "Start-RouletteTwoWeekSoak.ps1"),
  "-Ip",$Ip,"-Until",$Until,"-OutRoot",$OutRoot
) -WorkingDirectory $Root -WindowStyle Minimized -PassThru

Write-Host "Starting watchdog every ${WatchHours}h until $Until"
$wdLog = Join-Path $Root "_tmp_logs\soak_watchdog_stdout.log"
$wdErr = Join-Path $Root "_tmp_logs\soak_watchdog_stderr.log"
$wd = Start-Process -FilePath "python" -ArgumentList @(
  "-u","-m","automation.soak_watchdog",
  "--ip",$Ip,"--until",$Until,"--loop-hours","$WatchHours"
) -WorkingDirectory $Root -WindowStyle Minimized -PassThru `
  -RedirectStandardOutput $wdLog -RedirectStandardError $wdErr

@(
  "started_si=$(Get-Date -Format o)"
  "supervisor_pid=$($sup.Id)"
  "watchdog_pid=$($wd.Id)"
  "out_root=$OutRoot"
  "until=$Until"
) | Set-Content -LiteralPath (Join-Path $OutRoot "autonomy.jsonl") -Encoding utf8
Write-Host "supervisor_pid=$($sup.Id) watchdog_pid=$($wd.Id)"
Write-Host "Health log: $wdLog"
Write-Host "Stop soak:  New-Item $OutRoot\STOP"
Write-Host "Stop watch: New-Item $OutRoot\WATCHDOG_STOP  (or _tmp_logs path from active)"