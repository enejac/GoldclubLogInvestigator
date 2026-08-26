# Runs InputAgent.exe in the interactive console session via WinRM + schtasks /IT.
param(
    [Parameter(Mandatory)][string]$ExePath,
    [Parameter(Mandatory)][string]$ScriptPath,
    [string]$FocusProcess = 'OneHand',
    [Parameter(Mandatory)][string]$OutPath,
    [Parameter(Mandatory)][string]$ErrPath,
    [int]$WaitSec = 30
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
$launchPs1 = Join-Path $workDir 'launch_input_hidden.ps1'

function Escape-SingleQuoted([string]$s) {
    return $s.Replace("'", "''")
}

$exeEsc = Escape-SingleQuoted $ExePath
$scriptEsc = Escape-SingleQuoted $ScriptPath
$outEsc = Escape-SingleQuoted $OutPath
$errEsc = Escape-SingleQuoted $ErrPath
$focusEsc = Escape-SingleQuoted $FocusProcess

$psBody = @"
`$psi = New-Object System.Diagnostics.ProcessStartInfo
`$psi.FileName = '$exeEsc'
`$psi.Arguments = '--scriptPath "' + '$scriptEsc' + '" --focusProcess ' + '$focusEsc'
`$psi.UseShellExecute = `$false
`$psi.CreateNoWindow = `$true
`$psi.RedirectStandardOutput = `$true
`$psi.RedirectStandardError = `$true
`$p = [Diagnostics.Process]::Start(`$psi)
`$stdout = `$p.StandardOutput.ReadToEnd()
`$stderr = `$p.StandardError.ReadToEnd()
`$p.WaitForExit()
[System.IO.File]::WriteAllText('$outEsc', `$stdout)
if (`$stderr) { [System.IO.File]::WriteAllText('$errEsc', `$stderr) }
exit `$p.ExitCode
"@
[System.IO.File]::WriteAllText($launchPs1, $psBody, (New-Object System.Text.UTF8Encoding $false))

$taskName = 'GCI_InputAgent_' + [guid]::NewGuid().ToString('N').Substring(0, 10)
$tr = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launchPs1`""
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$st = (Get-Date).AddMinutes(2).ToString('HH:mm', $inv)

# schtasks /SD locale note: omit /SD by default. Swallow /ST-earlier WARNING on stderr
# so WinRM does not abort the click as NativeCommandError.
function Invoke-SchtasksQuiet([string[]]$SchArgs) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & schtasks @SchArgs 2>&1 | Where-Object {
            $_ -isnot [System.Management.Automation.ErrorRecord] -or
            $_.ToString() -notmatch 'WARNING:\s*Task may not run'
        }
    } finally {
        $ErrorActionPreference = $prev
    }
    return $LASTEXITCODE
}
$code = Invoke-SchtasksQuiet @('/Create','/TN',$taskName,'/TR',$tr,'/SC','ONCE','/ST',$st,'/RU',$consoleUser,'/IT','/F')
if ($code -ne 0) {
    $sd = (Get-Date).ToString((Get-Culture).DateTimeFormat.ShortDatePattern)
    $code = Invoke-SchtasksQuiet @('/Create','/TN',$taskName,'/TR',$tr,'/SC','ONCE','/ST',$st,'/SD',$sd,'/RU',$consoleUser,'/IT','/F')
    if ($code -ne 0) { throw "schtasks /Create failed: $code for user $consoleUser sd=$sd st=$st" }
}

$code = Invoke-SchtasksQuiet @('/Run','/TN',$taskName)
if ($code -ne 0) { throw "schtasks /Run failed: $code" }

$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $OutPath) -and ((Get-Item -LiteralPath $OutPath).Length -gt 0)) {
        $tail = Get-Content -LiteralPath $OutPath -Raw -ErrorAction SilentlyContinue
        if ($tail -and $tail -match '"type"\s*:\s*"done"') { break }
    }
    Start-Sleep -Milliseconds 400
}

schtasks /Delete /TN $taskName /F 2>$null | Out-Null
Remove-Item -LiteralPath $launchPs1 -Force -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $OutPath)) {
    $errTail = ''
    if (Test-Path -LiteralPath $ErrPath) { $errTail = Get-Content -LiteralPath $ErrPath -Raw -ErrorAction SilentlyContinue }
    throw "InputAgent did not finish within ${WaitSec}s (user=$consoleUser). stderr=$errTail"
}

if (Test-Path -LiteralPath $ErrPath) {
    $err = Get-Content -LiteralPath $ErrPath -Raw -ErrorAction SilentlyContinue
    if ($err -and $err.Trim()) { throw $err.Trim() }
}

Write-Output "ok user=$consoleUser"