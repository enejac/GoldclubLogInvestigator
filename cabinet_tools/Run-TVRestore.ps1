$ErrorActionPreference = 'Continue'
$log = 'C:\Windows\Temp\tv_restore_run.log'
$status = 'C:\Windows\Temp\tv_restore_status.txt'
Remove-Item $log, $status -Force -ErrorAction SilentlyContinue

$consoleUser = $null
$query = query user 2>$null
if ($query) {
    foreach ($line in ($query | Select-Object -Skip 1)) {
        if ($line -match 'console') {
            $consoleUser = ($line.Trim() -split '\s+', 2)[0]
            $consoleUser = $consoleUser.TrimStart('>')
            break
        }
    }
}
if (-not $consoleUser) { throw 'No interactive console user found' }

$taskName = 'GCI_TVRestore'
schtasks /Delete /TN $taskName /F 2>$null | Out-Null

$launchCmd = 'C:\Windows\Temp\run_tv_restore.cmd'
$cmdBody = "@echo off`r`nD:\TeamViewer_LoginBackup\restore_tv_login.cmd > C:\Windows\Temp\tv_restore_run.log 2>&1`r`n"
[System.IO.File]::WriteAllText($launchCmd, $cmdBody, [Text.Encoding]::ASCII)

$runUser = $consoleUser
$tr = 'C:\Windows\System32\cmd.exe /c C:\Windows\Temp\run_tv_restore.cmd'
$st = (Get-Date).AddMinutes(2).ToString('HH:mm')
$sd = (Get-Date).ToString('MM/dd/yyyy')

$null = cmd.exe /c "schtasks /Create /TN $taskName /TR `"$tr`" /SC ONCE /ST $st /SD $sd /RU $runUser /IT /F"
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed: $LASTEXITCODE for user $runUser" }

$null = schtasks /Run /TN $taskName
if ($LASTEXITCODE -ne 0) { throw "schtasks /Run failed: $LASTEXITCODE" }

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path $log) -and ((Get-Item $log).Length -gt 0)) {
        $tail = Get-Content $log -Raw -ErrorAction SilentlyContinue
        if ($tail -match 'RESTORE COMPLETE' -or $tail -match '\[ERROR\]') { break }
    }
    Start-Sleep -Milliseconds 500
}

schtasks /Delete /TN $taskName /F 2>$null | Out-Null
Remove-Item $launchCmd -Force -ErrorAction SilentlyContinue

"user=$consoleUser" | Out-File $status
if (Test-Path $log) { Get-Content $log | Add-Content $status } else { 'NO LOG OUTPUT' | Add-Content $status }
