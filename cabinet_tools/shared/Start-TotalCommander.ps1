# Find Total Commander on USB / disk and launch as SYSTEM on console session 1.
$ErrorActionPreference = 'Continue'

function Write-TcLog([string]$Message) {
    $logDir = 'C:\goldclub\var\log'
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $line = "[$(Get-Date -Format o)] $Message"
    Add-Content -LiteralPath (Join-Path $logDir 'totalcmd-boot.log') -Value $line -Encoding UTF8
}

function Find-TotalCommanderExe {
    if ($PSScriptRoot -and $PSScriptRoot -match 'usb_scripts') {
        $usbRoot = Split-Path -Parent $PSScriptRoot
        foreach ($name in @('TOTALCMD.EXE', 'TOTALCMD64.EXE')) {
            $path = Join-Path $usbRoot "totalcmd\$name"
            if (Test-Path -LiteralPath $path) { return $path }
        }
    }
    foreach ($psd in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        foreach ($name in @('TOTALCMD.EXE', 'TOTALCMD64.EXE')) {
            $usb = Join-Path $psd.Root "totalcmd\$name"
            if (Test-Path -LiteralPath $usb) { return $usb }
        }
    }
    $candidates = @(
        'D:\totalcmd\TOTALCMD.EXE',
        'C:\totalcmd\TOTALCMD.EXE',
        'E:\totalcmd\TOTALCMD.EXE'
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    foreach ($root in @('D:\', 'C:\', 'E:\', 'F:\', 'G:\', 'H:\')) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $hit = Get-ChildItem -LiteralPath $root -Filter 'TOTALCMD.EXE' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\totalcmd\\TOTALCMD\.EXE$' } |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Find-PsExec {
    foreach ($path in @(
        'C:\Tools\PSTools\PsExec.exe',
        'C:\Tools\PSTools\psexec.exe',
        'D:\Tools\PSTools\PsExec.exe',
        'C:\goldclub\bin\PsExec.exe'
    )) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}

function Register-TotalCommanderScheduledTask([string]$ExePath) {
    $workDir = Split-Path -Parent $ExePath
    $taskName = 'GoldClub-TotalCommander'
    $action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $workDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-TcLog "Registered scheduled task $taskName for $ExePath"
}

function Start-TotalCommanderAsSystem {
    param([string]$ExePath)
    $workDir = Split-Path -Parent $ExePath
    $psexec = Find-PsExec
    if ($psexec) {
        Write-TcLog "Launch via PsExec SYSTEM session 1: $ExePath"
        Start-Process -FilePath $psexec -ArgumentList @(
            '-accepteula', '-s', '-i', '1', '-d', $ExePath
        ) -WorkingDirectory $workDir -WindowStyle Hidden
        return $true
    }
    try {
        $taskName = 'GoldClub-TotalCommander'
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $existing) {
            Register-TotalCommanderScheduledTask -ExePath $ExePath
        }
        Start-ScheduledTask -TaskName $taskName
        Write-TcLog "Started scheduled task $taskName"
        return $true
    }
    catch {
        Write-TcLog "SYSTEM launch failed: $($_.Exception.Message); falling back to current user"
        Start-Process -FilePath $ExePath -WorkingDirectory $workDir
        return $false
    }
}

$tc = Find-TotalCommanderExe
if (-not $tc) {
    Write-TcLog 'TOTALCMD.EXE not found (USB totalcmd folder, D/C/E, removable drives)'
    return
}
Write-TcLog "Found Total Commander: $tc"
Start-TotalCommanderAsSystem -ExePath $tc | Out-Null