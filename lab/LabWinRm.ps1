<#
.SYNOPSIS
    Shared WinRM helper for lab cabinet scripts (replaces PsExec for remote UI).

.DESCRIPTION
    Dot-source from Open_*_Remote / Surgical / Push-Credits style tools.
    If WinRM is down, Ensure-LabWinRm auto-enables it via PsExec (SYSTEM).
    Prefer D:\_ENABLE_WINRM.bat when present, else Enable-PSRemoting with
    -SkipNetworkProfileCheck (Public profile on GoldClub cabinets).
#>
Set-StrictMode -Version Latest

$script:LabWinRmUser = 'GOLD-CLUB\test'
$script:LabWinRmPass = 'test'

function Get-LabWinRmUserForHost {
    param([string] $ComputerName = '')
    $h = "$ComputerName".Trim()
    switch ($h) {
        '10.0.0.111' { return '10.0.0.111\test' }
        default { return $script:LabWinRmUser }
    }
}

function Get-LabWinRmCredential {
    param(
        [string] $ComputerName = '10.0.0.90',
        [string] $User,
        [string] $Password = $script:LabWinRmPass
    )
    if (-not $User) { $User = Get-LabWinRmUserForHost -ComputerName $ComputerName }
    $sec = ConvertTo-SecureString $Password -AsPlainText -Force
    return [PSCredential]::new($User, $sec)
}

function Get-LabWinRmPsExecPath {
    $candidates = @(
        (Join-Path $PSScriptRoot '..\PsExec.exe'),
        (Join-Path $PSScriptRoot '..\..\PsExec.exe'),
        'C:\Tools\PSTools\PsExec.exe',
        (Join-Path $PSScriptRoot 'PsExec.exe')
    )
    foreach ($c in $candidates) {
        try {
            $full = [System.IO.Path]::GetFullPath($c)
        } catch {
            continue
        }
        if (Test-Path -LiteralPath $full) { return $full }
    }
    return $null
}

function Enable-LabWinRmTrustedHost {
    param([Parameter(Mandatory)][string] $ComputerName)
    try {
        $cur = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
        $entries = @($cur -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($entries -contains '*' -or $entries -contains $ComputerName) { return $true }
        Set-Item WSMan:\localhost\Client\TrustedHosts -Value (($entries + $ComputerName) -join ',') -Force
        return $true
    } catch {
        Write-Warning "Could not add $ComputerName to TrustedHosts (run elevated): $($_.Exception.Message)"
        return $false
    }
}

function Test-LabWinRm {
    param([Parameter(Mandatory)][string] $ComputerName)
    try {
        Test-WSMan -ComputerName $ComputerName -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-LabWinRmPort {
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [int] $TimeoutMs = 2500
    )
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($ComputerName, 5985, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $ok) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($client) { try { $client.Close() } catch { } }
    }
}

function Initialize-LabWinRmSmbCredential {
    param([Parameter(Mandatory)][string] $ComputerName)
    $user = Get-LabWinRmUserForHost -ComputerName $ComputerName
    cmdkey /add:$ComputerName /user:$user /pass:$script:LabWinRmPass 2>&1 | Out-Null
}

function Invoke-LabWinRmEnableRemote {
    <#
    Run the cabinet-side enable via PsExec as SYSTEM (synchronous).
    Uses -EncodedCommand so multi-line enable script survives PsExec.
    #>
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [string] $PsExecPath
    )
    if (-not $PsExecPath) { $PsExecPath = Get-LabWinRmPsExecPath }
    if (-not $PsExecPath) {
        throw "PsExec.exe not found (looked under C:\Tools\PSTools and next to LabWinRm.ps1)."
    }

    Initialize-LabWinRmSmbCredential -ComputerName $ComputerName

    $enableBody = @'
$ErrorActionPreference = "Continue"
$did = $false
foreach ($bat in @("D:\_ENABLE_WINRM.bat", "D:\Enable-WinRM.bat", "C:\_ENABLE_WINRM.bat")) {
    if (Test-Path -LiteralPath $bat) { & cmd.exe /c $bat; $did = $true; break }
}
foreach ($ps1 in @("D:\Enable-WinRM.ps1", "C:\Windows\Temp\Enable-WinRM.ps1", "C:\Windows\Temp\lab_enable_winrm_full.ps1")) {
    if (-not $did -and (Test-Path -LiteralPath $ps1)) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -NoElevate
        $did = $true
        break
    }
}
if (-not $did) {
    Enable-PSRemoting -Force -SkipNetworkProfileCheck
    Set-Service WinRM -StartupType Automatic
    Start-Service WinRM -ErrorAction SilentlyContinue
}
$svc = Get-Service WinRM -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne "Running") { Start-Service WinRM -ErrorAction SilentlyContinue }
'@

    $localEnable = @(
        (Join-Path $PSScriptRoot 'Enable-WinRM.ps1'),
        (Join-Path $PSScriptRoot '..\cabinet_tools\shared\Enable-WinRM.ps1'),
        (Join-Path $PSScriptRoot '..\..\cabinet_tools\shared\Enable-WinRM.ps1'),
        'C:\Users\Ezbogar\GoldclubLogInvestigator\cabinet_tools\shared\Enable-WinRM.ps1'
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($localEnable) {
        foreach ($unc in @(
            "\\$ComputerName\c$\Windows\Temp\Enable-WinRM.ps1",
            "\\$ComputerName\c$\Windows\Temp\lab_enable_winrm_full.ps1",
            "\\$ComputerName\USB_Remote\Enable-WinRM.ps1",
            "\\$ComputerName\USB\Enable-WinRM.ps1"
        )) {
            try {
                $dir = Split-Path -Parent $unc
                if (-not (Test-Path -LiteralPath $dir)) {
                    New-Item -ItemType Directory -Force -Path $dir | Out-Null
                }
                Copy-Item -LiteralPath $localEnable -Destination $unc -Force -ErrorAction Stop
                break
            } catch {
                Write-Warning "Could not stage Enable-WinRM.ps1 ($unc): $($_.Exception.Message)"
            }
        }
    }

    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($enableBody))
    $auth = @('-u', (Get-LabWinRmUserForHost -ComputerName $ComputerName), '-p', $script:LabWinRmPass)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $PsExecPath "\\$ComputerName" -accepteula @auth -s -n 120 `
            powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc 2>&1
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Ensure-LabWinRm {
    <#
    Return $true when WinRM answers. If not, auto-enable via PsExec and wait.
    Throws when enable is impossible or still down after TimeoutSec.
    #>
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [int] $TimeoutSec = 90,
        [switch] $Quiet
    )
    Enable-LabWinRmTrustedHost -ComputerName $ComputerName | Out-Null
    if (Test-LabWinRm -ComputerName $ComputerName) { return $true }

    if (-not $Quiet) {
        Write-Host "[*] WinRM not ready on $ComputerName - auto-enabling via PsExec..." -ForegroundColor Yellow
    }

    try {
        $null = Invoke-LabWinRmEnableRemote -ComputerName $ComputerName
    } catch {
        throw "WinRM not ready on $ComputerName and auto-enable failed: $($_.Exception.Message)"
    }

    $deadline = (Get-Date).AddSeconds([Math]::Max(15, $TimeoutSec))
    while ((Get-Date) -lt $deadline) {
        if (Test-LabWinRmPort -ComputerName $ComputerName -TimeoutMs 1500) {
            if (Test-LabWinRm -ComputerName $ComputerName) {
                if (-not $Quiet) {
                    Write-Host "[+] WinRM ready on $ComputerName" -ForegroundColor Green
                }
                return $true
            }
        }
        Start-Sleep -Seconds 2
    }

    throw "WinRM still not ready on $ComputerName after auto-enable. Check firewall / UWF / reboot, or run D:\_ENABLE_WINRM.bat on the cabinet."
}

function Invoke-LabWinRm {
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][scriptblock] $ScriptBlock,
        [object[]] $ArgumentList = @(),
        [pscredential] $Credential,
        [int] $TimeoutMs = 120000
    )
    if (-not $Credential) { $Credential = Get-LabWinRmCredential -ComputerName $ComputerName }
    Ensure-LabWinRm -ComputerName $ComputerName | Out-Null
    $opt = New-PSSessionOption -OperationTimeout $TimeoutMs -OpenTimeout 30000
    Invoke-Command -ComputerName $ComputerName -Credential $Credential -Authentication Negotiate `
        -SessionOption $opt -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList
}

function Invoke-LabWinRmEncoded {
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][string] $EncodedCommand,
        [pscredential] $Credential,
        [int] $TimeoutMs = 120000
    )
    Invoke-LabWinRm -ComputerName $ComputerName -Credential $Credential -TimeoutMs $TimeoutMs -ScriptBlock {
        param($Enc)
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $Enc) `
            -WindowStyle Hidden -Wait
    } -ArgumentList $EncodedCommand
}

function Start-LabInteractiveRemote {
    <#
    Launch a program in the physical console session without flashing
    a PowerShell/CMD window on the cabinet. The orchestrator runs hidden;
    the target app uses -AppWindowStyle (Normal, Hidden, Minimized).
    #>
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][string] $FilePath,
        [string] $Arguments = '',
        [ValidateSet('Normal', 'Hidden', 'Minimized', 'Maximized')]
        [string] $AppWindowStyle = 'Normal',
        [pscredential] $Credential
    )
    Invoke-LabWinRm -ComputerName $ComputerName -Credential $Credential -ScriptBlock {
        param($Path, $ArgsLine, $WinStyle)
        $sessionUser = (Get-CimInstance -ClassName Win32_ComputerSystem).UserName
        if ([string]::IsNullOrWhiteSpace($sessionUser)) {
            throw 'No user is logged into the physical console.'
        }
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "Executable not found on cabinet: $Path"
        }

        $id = [guid]::NewGuid().ToString('N').Substring(0, 10)
        $launcher = Join-Path $env:TEMP "labwinrm_launch_$id.ps1"
        $cleanup = Join-Path $env:TEMP "labwinrm_launch_$id.cleanup.cmd"

        # Use -FilePath (not -LiteralPath): some cabinet PowerShell builds reject
        # Start-Process -LiteralPath entirely ("parameter cannot be found").
        $workDir = Split-Path -Parent $Path
        if ([string]::IsNullOrWhiteSpace($ArgsLine)) {
            $launchBody = @"
Start-Process -FilePath '$($Path.Replace("'", "''"))' -WorkingDirectory '$($workDir.Replace("'", "''"))' -WindowStyle $WinStyle
"@
        } else {
            $launchBody = @"
Start-Process -FilePath '$($Path.Replace("'", "''"))' -WorkingDirectory '$($workDir.Replace("'", "''"))' -ArgumentList '$($ArgsLine.Replace("'", "''"))' -WindowStyle $WinStyle
"@
        }
        Set-Content -LiteralPath $launcher -Value $launchBody -Encoding ASCII
        Set-Content -LiteralPath $cleanup -Value "@echo off`r`ndel /f /q `"$launcher`" `"$cleanup`" 2>nul" -Encoding ASCII

        $taskName = "LabWinRm_$id"
        $psArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs
        $principal = New-ScheduledTaskPrincipal -UserId $sessionUser -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 1
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false | Out-Null
        Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"$cleanup`"" -WindowStyle Hidden | Out-Null

        "Started $Path as $sessionUser (orchestrator hidden, app window=$WinStyle)"
    } -ArgumentList $FilePath, $Arguments, $AppWindowStyle
}
