# Runs RouletteHitboxScan.exe in the interactive console session via schtasks /IT.
param(
    [Parameter(Mandatory)][string]$ExePath,
    [Parameter(Mandatory)][string]$ArgLine,
    [Parameter(Mandatory)][string]$OutPath,
    [Parameter(Mandatory)][string]$ErrPath,
    [int]$WaitSec = 100
)

$ErrorActionPreference = 'Stop'
Remove-Item -LiteralPath $OutPath, $ErrPath -Force -ErrorAction SilentlyContinue

$consoleUser = $null
$query = query user 2>$null
if ($query) {
    foreach ($line in ($query | Select-Object -Skip 1)) {
        if ($line -match 'console') {
            $consoleUser = ($line.Trim() -split '\s+', 2)[0]
            break
        }
    }
}
if (-not $consoleUser) { throw 'No interactive console user found (query user)' }

$workDir = Split-Path -Parent $ExePath
$launchPs1 = Join-Path $workDir 'launch_hitbox_hidden.ps1'

function Escape-SingleQuoted([string]$s) { return $s.Replace("'", "''") }
$exeEsc = Escape-SingleQuoted $ExePath
$argEsc = Escape-SingleQuoted $ArgLine
$outEsc = Escape-SingleQuoted $OutPath
$errEsc = Escape-SingleQuoted $ErrPath

$psBody = @"
`$psi = New-Object System.Diagnostics.ProcessStartInfo
`$psi.FileName = '$exeEsc'
`$psi.Arguments = '$argEsc'
`$psi.UseShellExecute = `$false
`$psi.CreateNoWindow = `$true
`$psi.RedirectStandardOutput = `$true
`$psi.RedirectStandardError = `$true
`$psi.WorkingDirectory = '$(Escape-SingleQuoted $workDir)'
`$p = [Diagnostics.Process]::Start(`$psi)
`$stdout = `$p.StandardOutput.ReadToEnd()
`$stderr = `$p.StandardError.ReadToEnd()
`$p.WaitForExit()
[System.IO.File]::WriteAllText((Join-Path '$workDir' 'hitbox_stdout.txt'), `$stdout)
if (`$stderr) { [System.IO.File]::WriteAllText('$errEsc', `$stderr) }
exit `$p.ExitCode
"@
[System.IO.File]::WriteAllText($launchPs1, $psBody, (New-Object System.Text.UTF8Encoding $false))

$taskName = 'GCI_Hitbox_' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$tr = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launchPs1`""
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$st = (Get-Date).AddMinutes(2).ToString('HH:mm', $inv)

# /SD is read in the cabinet's date order, so naming it in MM/dd/yyyy fails on a
# dd/MM cabinet after the 12th. ONCE starts today anyway; retry with the local
# pattern only if this build demands a date.
$null = schtasks /Create /TN $taskName /TR $tr /SC ONCE /ST $st /RU $consoleUser /IT /F
if ($LASTEXITCODE -ne 0) {
    $sd = (Get-Date).ToString((Get-Culture).DateTimeFormat.ShortDatePattern)
    $null = schtasks /Create /TN $taskName /TR $tr /SC ONCE /ST $st /SD $sd /RU $consoleUser /IT /F
    if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed: $LASTEXITCODE user=$consoleUser sd=$sd" }
}
$null = schtasks /Run /TN $taskName
if ($LASTEXITCODE -ne 0) { throw "schtasks /Run failed: $LASTEXITCODE" }

$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $OutPath) -and ((Get-Item -LiteralPath $OutPath).Length -gt 80)) {
        Start-Sleep -Milliseconds 600
        break
    }
    Start-Sleep -Milliseconds 400
}

schtasks /Delete /TN $taskName /F 2>$null | Out-Null
Remove-Item -LiteralPath $launchPs1 -Force -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $OutPath)) {
    $errTail = ''
    if (Test-Path -LiteralPath $ErrPath) { $errTail = Get-Content -LiteralPath $ErrPath -Raw -EA SilentlyContinue }
    throw "Hitbox scan did not finish within ${WaitSec}s (user=$consoleUser). stderr=$errTail"
}
Write-Output "ok user=$consoleUser out=$OutPath"
