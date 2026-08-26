# Maintenance task: remote share + test admin (runs via RunManteinanceTasks after JT25 Setup.exe)
$ErrorActionPreference = 'Continue'
Write-Host '[D__JT25] Enable remote share'

if (Test-Path -LiteralPath 'G:\') {
    cmd /c 'net share slot /delete /y' 2>&1 | ForEach-Object { Write-Host $_ }
    cmd /c 'net share slot=G:\ /grant:everyone,FULL' 2>&1 | ForEach-Object { Write-Host $_ }
} else {
    Write-Warning 'G:\ not found - share skipped'
}

$null = cmd /c 'net user test' 2>&1
if ($LASTEXITCODE -ne 0) {
    cmd /c 'net user test test /add' 2>&1 | ForEach-Object { Write-Host $_ }
}
cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { Write-Host $_ }
Write-Host '[D__JT25] share task done'