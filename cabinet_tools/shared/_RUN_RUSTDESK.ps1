#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $UsbRoot = $PSScriptRoot,
    [string] $RelativeDir = 'RustDesk-LAN-x64',
    [string] $LocalDir = 'D:\RustDesk-LAN-x64',
    [string] $DefaultEgmIp = '10.0.0.90',
    [string] $LabUser = 'GOLD-CLUB\test',
    [string] $LabPassword = 'test'
)

$ErrorActionPreference = 'Stop'

function Get-UncHost([string] $Path) {
    if ($Path -match '^\\\\([^\\]+)\\') { return $Matches[1] }
    return $null
}

function Test-IsLocalHostName([string] $Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { return $false }
    $n = $Name.Trim().TrimEnd('.')
    if ($n -eq '.' -or $n -eq 'localhost' -or $n -eq '127.0.0.1') { return $true }
    if ($n -eq $env:COMPUTERNAME) { return $true }
    try {
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -and $_.IPAddress -notlike '127.*' } |
            Select-Object -ExpandProperty IPAddress)
        if ($ips -contains $n) { return $true }
    } catch {}
    return $false
}

function Get-AutoLogonUser {
    try {
        $wl = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
        if ($wl.DefaultUserName) { return [string]$wl.DefaultUserName }
    } catch {}
    return 'goldclub'
}

function Copy-RustDeskConfig([string] $RdDir, [string] $UserName) {
    $src = Join-Path $RdDir 'config'
    if (-not (Test-Path -LiteralPath $src)) { return }
    $dest = Join-Path $env:APPDATA 'RustDesk\config'
    if ($UserName) {
        $dest = "C:\Users\$UserName\AppData\Roaming\RustDesk\config"
    }
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    Copy-Item -Path (Join-Path $src '*') -Destination $dest -Force -Recurse -ErrorAction SilentlyContinue
}

function Get-InteractiveRustDesk {
    @(Get-Process -Name rustdesk -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
}

function Start-RustDeskInConsole {
    param(
        [Parameter(Mandatory = $true)][string] $RdDir,
        [string] $Label = 'EGM'
    )
    $exe = Join-Path $RdDir 'rustdesk.exe'
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "rustdesk.exe not found: $exe"
    }

    $user = Get-AutoLogonUser
    Copy-RustDeskConfig -RdDir $RdDir -UserName $user

    Get-Process -Name rustdesk -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -eq 0 } |
        Stop-Process -Force -ErrorAction SilentlyContinue

    $ui = Get-InteractiveRustDesk
    if ($ui.Count -gt 0) {
        Write-Host ("[OK] [{0}] RustDesk already running (pid={1} session={2})" -f $Label, ($ui.Id -join ','), ($ui.SessionId -join ',')) -ForegroundColor Green
        return
    }

    $sessionId = 0
    try { $sessionId = [int][System.Diagnostics.Process]::GetCurrentProcess().SessionId } catch {}

    if ($sessionId -gt 0) {
        Start-Process -FilePath $exe -WorkingDirectory $RdDir -WindowStyle Minimized
        Start-Sleep -Seconds 2
        $after = Get-InteractiveRustDesk
        if ($after.Count -gt 0) {
            Write-Host ("[OK] [{0}] RustDesk started in this session (pid={1})" -f $Label, ($after.Id -join ',')) -ForegroundColor Green
            return
        }
        Write-Host ("[WARN] [{0}] Start-Process did not yield console process - falling back to scheduled task" -f $Label) -ForegroundColor Yellow
    } else {
        Write-Host ("[{0}] Non-interactive/session-0 - launching via scheduled task as {1}" -f $Label, $user) -ForegroundColor Cyan
    }

    $workDir = Split-Path -Parent $exe
    $taskName = 'GoldClub-RustDesk-LAN-Once'
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    $launchArgs = '/c start "" /min "{0}"' -f $exe
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launchArgs -WorkingDirectory $workDir
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    $after = Get-InteractiveRustDesk
    if ($after.Count -gt 0) {
        Write-Host ("[OK] [{0}] RustDesk started on console (pid={1} session={2} user={3})" -f $Label, ($after.Id -join ','), ($after.SessionId -join ','), $user) -ForegroundColor Green
        return
    }
    throw "[$Label] RustDesk did not start on console user=$user exe=$exe"
}

function Start-RustDeskOnEgmRemote {
    param(
        [Parameter(Mandatory = $true)][string] $ComputerName,
        [Parameter(Mandatory = $true)][string] $RdDir
    )
    $exe = Join-Path $RdDir 'rustdesk.exe'
    Write-Host ("[REMOTE] EGM={0} exe={1}" -f $ComputerName, $exe) -ForegroundColor Cyan

    cmdkey /add:$ComputerName /user:$LabUser /pass:$LabPassword | Out-Null

    $cred = New-Object System.Management.Automation.PSCredential (
        $LabUser,
        (ConvertTo-SecureString $LabPassword -AsPlainText -Force)
    )

    try {
        $th = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction SilentlyContinue).Value
        if ($th -notmatch [regex]::Escape($ComputerName) -and $th -ne '*') {
            $new = if ([string]::IsNullOrWhiteSpace($th)) { $ComputerName } else { "$th,$ComputerName" }
            Set-Item WSMan:\localhost\Client\TrustedHosts -Value $new -Force -ErrorAction SilentlyContinue
        }
    } catch {}

    $result = Invoke-Command -ComputerName $ComputerName -Credential $cred -Authentication Negotiate -ScriptBlock {
        param([string] $ExePath, [string] $ConfigDir)
        $user = 'goldclub'
        try {
            $wl = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
            if ($wl.DefaultUserName) { $user = [string]$wl.DefaultUserName }
        } catch {}
        if (-not (Test-Path -LiteralPath $ExePath)) { return "ERROR: missing $ExePath on EGM" }
        if (Test-Path -LiteralPath $ConfigDir) {
            $dest = "C:\Users\$user\AppData\Roaming\RustDesk\config"
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
            Copy-Item -Path (Join-Path $ConfigDir '*') -Destination $dest -Force -Recurse -ErrorAction SilentlyContinue
        }
        Get-Process -Name rustdesk -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq 0 } |
            Stop-Process -Force -ErrorAction SilentlyContinue
        $ui = @(Get-Process -Name rustdesk -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
        if ($ui.Count -gt 0) {
            return ("ALREADY pid={0} session={1} user={2}" -f ($ui.Id -join ','), ($ui.SessionId -join ','), $user)
        }
        $workDir = Split-Path -Parent $ExePath
        $taskName = 'GoldClub-RustDesk-LAN-Once'
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        $launchArgs = '/c start "" /min "{0}"' -f $ExePath
        $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $launchArgs -WorkingDirectory $workDir
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
            -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 3
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        $after = @(Get-Process -Name rustdesk -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -gt 0 })
        if ($after.Count -gt 0) {
            return ("STARTED pid={0} session={1} user={2}" -f ($after.Id -join ','), ($after.SessionId -join ','), $user)
        }
        return "STARTED_TASK but no process yet (check goldclub desktop) user=$user"
    } -ArgumentList $exe, (Join-Path $RdDir 'config')

    Write-Host ("[OK] {0}" -f $result) -ForegroundColor Green
}

# --- main ---
$root = $UsbRoot.TrimEnd('\')
$uncHost = Get-UncHost $root
$localCandidate = Join-Path $root $RelativeDir
$egmDir = $LocalDir

$rdDir = $null
if (Test-Path -LiteralPath (Join-Path $localCandidate 'rustdesk.exe')) {
    $rdDir = $localCandidate
} elseif (Test-Path -LiteralPath (Join-Path $egmDir 'rustdesk.exe')) {
    $rdDir = $egmDir
}

# On EGM via UNC (\\10.0.0.90\USB_Remote\...) or plain D:\ path -> console start
if ($uncHost -and (Test-IsLocalHostName $uncHost)) {
    Write-Host ("[EGM] UNC {0} is this machine - starting on console" -f $uncHost) -ForegroundColor Cyan
    if (-not $rdDir) { $rdDir = $egmDir }
    Start-RustDeskInConsole -RdDir $rdDir -Label 'EGM'
    exit 0
}

# From another PC via UNC -> WinRM into EGM console
if ($uncHost) {
    Start-RustDeskOnEgmRemote -ComputerName $uncHost -RdDir $egmDir
    exit 0
}

# Local drive letter on EGM (D:\_RUN_RUSTDESK.bat)
if ($rdDir) {
    Start-RustDeskInConsole -RdDir $rdDir -Label 'EGM'
    exit 0
}

Write-Host ("[WARN] Local rustdesk.exe missing - trying remote start on {0}" -f $DefaultEgmIp) -ForegroundColor Yellow
Start-RustDeskOnEgmRemote -ComputerName $DefaultEgmIp -RdDir $egmDir