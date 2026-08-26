# Persistent supervisor for roulette two-week soak (restarts on crash until STOP/deadline).
param(
  [string]$Ip = "10.0.0.90",
  [string]$Until = "2026-08-17T07:30:00",
  [string]$OutRoot = ""
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
if (-not $OutRoot) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $OutRoot = Join-Path $Root ("automation_runs\{0}_{1}_two_week_soak" -f $stamp, $Ip)
}
New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$ptrDir = Join-Path $Root "_tmp_logs"
New-Item -ItemType Directory -Force -Path $ptrDir | Out-Null
[System.IO.File]::WriteAllText(
  (Join-Path $ptrDir "two_week_soak_active.txt"),
  ($OutRoot + [Environment]::NewLine),
  (New-Object System.Text.UTF8Encoding $false)
)
$supervisorLog = Join-Path $OutRoot "supervisor.log"
function Write-Sup([string]$msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -LiteralPath $supervisorLog -Value $line -Encoding utf8
  Write-Host $line
}
Write-Sup ("supervisor start out={0} until={1}" -f $OutRoot, $Until)
while ($true) {
  if (Test-Path (Join-Path $OutRoot "STOP")) { Write-Sup "STOP flag"; break }
  try {
    $deadline = [datetime]::ParseExact($Until, "yyyy-MM-ddTHH:mm:ss", $null)
    if ((Get-Date) -ge $deadline) { Write-Sup "deadline reached"; break }
  } catch {
    try {
      $deadline = [datetime]::Parse($Until)
      if ((Get-Date) -ge $deadline) { Write-Sup "deadline reached"; break }
    } catch {}
  }
  Write-Sup "launching python soak"
  $argList = @(
    "-u", "-m", "automation.roulette_two_week_soak",
    "--ip", $Ip,
    "--until", $Until,
    "--out-root", $OutRoot,
    "--layouts", "layout1,layout2",
    "--profile", "fast",
    "--kind", "slot",
    "--slot-rounds", "80",
    "--fast",
    "--aggressive"
  )
  $stdout = Join-Path $OutRoot "stdout.log"
  $stderr = Join-Path $OutRoot "stderr.log"
  $p = Start-Process -FilePath "python" -ArgumentList $argList -WorkingDirectory $Root -PassThru -NoNewWindow `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  Wait-Process -Id $p.Id
  $code = $p.ExitCode
  Write-Sup ("python exited code={0}" -f $code)
  if ($code -eq 0) { Write-Sup "clean exit; supervisor done"; break }
  if (Test-Path (Join-Path $OutRoot "STOP")) { break }
  try {
    $deadline = [datetime]::Parse($Until)
    if ((Get-Date) -ge $deadline) { break }
  } catch {}
  Write-Sup "crash restart in 30s"
  Start-Sleep -Seconds 30
}
Write-Sup "supervisor exit"