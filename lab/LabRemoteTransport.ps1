<#
.SYNOPSIS
    Shared remote execution transport: WinRM first, PsExec (-s) fallback.

.DESCRIPTION
    Dot-source from lab cabinet orchestration scripts. Requires LabAccess.ps1
    in the same directory for fleet credentials and PsExec auth args.

    WinRM (port 5985) is ~2-5s per run; PsExec is ~20-45s but runs as SYSTEM
    and is the proven fallback when WinDivert needs an elevated token.
#>

if (-not (Get-Command Get-LabCredential -ErrorAction SilentlyContinue)) {
    $labAccessPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'LabAccess.ps1'
    if (Test-Path -LiteralPath $labAccessPath) {
        . $labAccessPath
    }
}

$script:LabFleetIps = @('10.0.0.83', '10.0.0.90', '10.0.0.100', '10.0.0.110', '10.0.0.111', '10.0.0.112', '10.0.0.171')

function Initialize-LabRemoteContext {
    param(
        [string] $ComputerName,
        [pscredential] $Credential
    )
    $psExecAuthArgs = @()
    $credentialFromLab = $false
    if ($ComputerName -in $script:LabFleetIps) {
        if (Get-Command Get-LabPsExecArgs -ErrorAction SilentlyContinue) {
            $psExecAuthArgs = @(Get-LabPsExecArgs -ComputerName $ComputerName)
        }
        if (-not $Credential -and (Get-Command Get-LabCredential -ErrorAction SilentlyContinue)) {
            $Credential = Get-LabCredential -ComputerName $ComputerName
            $credentialFromLab = $true
        }
    }
    return [PSCustomObject]@{
        Credential        = $Credential
        CredentialFromLab = $credentialFromLab
        PsExecAuthArgs    = $psExecAuthArgs
    }
}

function Test-LabWinRmReachable {
    param(
        [string] $Computer,
        [int]    $TimeoutMs = 2500
    )
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Computer, 5985, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $ok) { return $false }
        $client.EndConnect($iar)
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($client) { try { $client.Close() } catch { } }
    }
}

function Test-LabTrustedHostConfigured {
    param([string] $Computer)
    try {
        $cur = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
    }
    catch {
        return $false
    }
    $entries = @($cur -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    return ($entries -contains '*' -or $entries -contains $Computer)
}

function Add-LabTrustedHostIfNeeded {
    param(
        [string] $Computer,
        [switch] $Quiet
    )
    if (Test-LabTrustedHostConfigured -Computer $Computer) { return $true }
    try {
        $cur = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
    }
    catch {
        return $false
    }
    $entries = @($cur -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    try {
        $new = if ($entries.Count) { ($entries + $Computer) -join ',' } else { $Computer }
        Set-Item WSMan:\localhost\Client\TrustedHosts -Value $new -Force -ErrorAction Stop
        return $true
    }
    catch {
        if (-not $Quiet) {
            Write-Host "[!] Could not add $Computer to client TrustedHosts (need admin): $_" -ForegroundColor Yellow
        }
        return $false
    }
}

function Start-LabRemoteWinRmEnableAsync {
    param(
        [string]   $Computer,
        [string]   $PsExecPath,
        [string[]] $PsExecAuthArgs = @(),
        [switch]   $Wait
    )
    # Prefer the shared LabAccess ensure (stages Enable-WinRM.ps1, waits for 5985).
    if ($Wait -and (Get-Command Ensure-LabWinRmReady -ErrorAction SilentlyContinue)) {
        try {
            Ensure-LabWinRmReady -ComputerName $Computer -Quiet | Out-Null
            return $true
        } catch {
            $log = Join-Path $env:TEMP ("lab_enablewinrm_{0}.log" -f $Computer)
            "ENSURE_WINRM_FAILED: $_" | Out-File -FilePath $log -Encoding utf8 -Append
            return $false
        }
    }
    $log = Join-Path $env:TEMP ("lab_enablewinrm_{0}.log" -f $Computer)
    $remoteCmd = 'Enable-PSRemoting -Force -SkipNetworkProfileCheck; Set-Service WinRM -StartupType Automatic; Start-Service WinRM -ErrorAction SilentlyContinue'
    if ($Wait) {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $PsExecPath "\\$Computer" -accepteula @($PsExecAuthArgs) -s -n 120 `
                powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $remoteCmd 2>&1 | Out-Null
        } catch {
            "SYNC_ENABLE_FAILED: $_" | Out-File -FilePath $log -Encoding utf8 -Append
            return $false
        } finally {
            $ErrorActionPreference = $prevEap
        }
        return (Test-LabWinRmReachable -Computer $Computer)
    }
    $psArgs = @("\\$Computer", '-accepteula') + @($PsExecAuthArgs) + @('-s', '-d', '-n', '60',
        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $remoteCmd)
    try {
        Start-Process -FilePath $PsExecPath -ArgumentList $psArgs -WindowStyle Hidden -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        "ASYNC_ENABLE_LAUNCH_FAILED: $_" | Out-File -FilePath $log -Encoding utf8 -Append
        return $false
    }
}

function Invoke-LabRemoteEncoded {
    param(
        [ValidateSet('psexec', 'winrm')]
        [string] $Transport,
        [string] $Computer,
        [string] $Enc,
        [string] $LogPath,
        [pscredential] $Credential,
        [string]   $PsExecPath,
        [string[]] $PsExecAuthArgs = @(),
        [int]      $PsExecTimeoutSec = 60,
        [int]      $WinRmOperationTimeoutMs = 120000
    )
    if ($Transport -eq 'psexec') {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $PsExecPath "\\$Computer" -accepteula @($PsExecAuthArgs) -s -n $PsExecTimeoutSec `
                powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $Enc *> $LogPath
            return $true
        }
        catch {
            try { "PSEXEC_TRANSPORT_FAILED: $_" | Out-File -FilePath $LogPath -Encoding utf8 -Append } catch { }
            return $false
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
    else {
        try {
            if (-not $Credential) {
                throw 'WINRM_TRANSPORT_FAILED: no credential (required for WinRM by IP).'
            }
            $sessionOption = New-PSSessionOption -OperationTimeout $WinRmOperationTimeoutMs -OpenTimeout 15000
            $icParams = @{
                ComputerName   = $Computer
                Credential     = $Credential
                Authentication = 'Negotiate'
                SessionOption  = $sessionOption
                ErrorAction    = 'Stop'
                ArgumentList   = $Enc
                ScriptBlock    = {
                    param($e)
                    & powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $e 2>&1
                }
            }
            Invoke-Command @icParams *> $LogPath
            return $true
        }
        catch {
            $msg = "WINRM_TRANSPORT_FAILED: $_"
            try { $msg | Out-File -FilePath $LogPath -Encoding utf8 -Append } catch { }
            Write-Host "[!] $msg" -ForegroundColor Yellow
            return $false
        }
    }
}

function Get-LabRemoteTransportPlan {
    param(
        [string] $ComputerName,
        [pscredential] $Credential,
        [switch] $CredentialFromLab
    )
    $useWinRm = $false
    $scheduleEnable = $false

    Write-Host "[*] Transport: probing WinRM (port 5985) on $ComputerName..." -ForegroundColor DarkGray
    if (Test-LabWinRmReachable -Computer $ComputerName) {
        if ($Credential) {
            $trusted = Add-LabTrustedHostIfNeeded -Computer $ComputerName
            if ($trusted) {
                $useWinRm = $true
                $credNote = if ($CredentialFromLab) { ' (lab GOLD-CLUB\test, auto)' } else { '' }
                Write-Host "[+] WinRM reachable on ${ComputerName}: WinRM first$credNote, PsExec fallback enabled." -ForegroundColor Green
            }
            else {
                Write-Host "[!] WinRM reachable but TrustedHosts not configured; using PsExec only." -ForegroundColor Yellow
            }
        }
        else {
            Write-Host "[*] WinRM reachable on $ComputerName but no credential; using PsExec only." -ForegroundColor DarkGray
        }
    }
    else {
        $scheduleEnable = $true
        Write-Host "[*] WinRM not reachable on $ComputerName; using PsExec (will enable WinRM in background for next run)." -ForegroundColor DarkGray
    }

    $order = if ($useWinRm) { @('winrm', 'psexec') } else { @('psexec') }
    return [PSCustomObject]@{
        TransportOrder      = $order
        ScheduleWinRmEnable = $scheduleEnable
        WinRmPreferredThisRun = $useWinRm
    }
}

function Invoke-LabRemoteEncodedWithFallback {
    param(
        [string[]] $TransportOrder,
        [string]   $Computer,
        [string]   $Enc,
        [string]   $LogPath,
        [pscredential] $Credential,
        [string]   $PsExecPath,
        [string[]] $PsExecAuthArgs = @(),
        [int]      $PsExecTimeoutSec = 60,
        [int]      $WinRmOperationTimeoutMs = 120000
    )
    foreach ($t in $TransportOrder) {
        Write-Host "[*] Transport '$t': launching remote command..." -ForegroundColor DarkGray
        $ok = Invoke-LabRemoteEncoded -Transport $t -Computer $Computer -Enc $Enc -LogPath $LogPath `
            -Credential $Credential -PsExecPath $PsExecPath -PsExecAuthArgs $PsExecAuthArgs `
            -PsExecTimeoutSec $PsExecTimeoutSec -WinRmOperationTimeoutMs $WinRmOperationTimeoutMs
        if ($ok) {
            Write-Host "[*] Transport '$t' finished." -ForegroundColor DarkGray
            return $t
        }
        Write-Host "[!] Transport '$t' failed; trying next..." -ForegroundColor Yellow
    }
    return $null
}
