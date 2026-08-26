<#
.SYNOPSIS
    Shared lab-cabinet credential helper. Dot-source this from any remote script
    to get the standard GoldClub lab credential and WinRM helpers.

.DESCRIPTION
    Usage (inside another script):

        . "$PSScriptRoot\LabAccess.ps1"
        $cred = Get-LabCredential
        Invoke-LabWinRmCommand -ComputerName $ip -ScriptBlock { hostname }

    The credential is GOLD-CLUB\test / test on domain cabinets. Workgroup
    cabinets (10.0.0.111 / GRT330106) use 10.0.0.111\test. SMB share access is
    handled separately by the persistent cmdkey mapping (see Initialize-LabAccess.ps1).
    Remote execution should use WinRM (Invoke-Command), not PsExec.

.NOTES
    Lab credential only (test/test). Not for production hosts.
#>

$script:LabUser = 'GOLD-CLUB\test'
$script:LabPass = 'test'

function Get-LabUserForHost {
    <# Domain cabinets: GOLD-CLUB\test. Workgroup GRT330106: IP\test. #>
    param([string] $ComputerName = '')
    $h = "$ComputerName".Trim()
    switch ($h) {
        '10.0.0.111' { return '10.0.0.111\test' }
        default { return $script:LabUser }
    }
}

function Get-LabCredential {
    <# Returns a [pscredential] for the cabinet (pass -ComputerName on workgroup hosts). #>
    param([string] $ComputerName = '')
    $user = Get-LabUserForHost -ComputerName $ComputerName
    $sec = ConvertTo-SecureString $script:LabPass -AsPlainText -Force
    return [System.Management.Automation.PSCredential]::new($user, $sec)
}

function Get-LabPsExecArgs {
    <# Returns the PsExec auth args array: -u <user> -p <pass> (legacy AFT scripts only). #>
    param([string] $ComputerName = '')
    $user = Get-LabUserForHost -ComputerName $ComputerName
    return @('-u', $user, '-p', $script:LabPass)
}

function Initialize-LabSmbCredential {
    <#
    Ensures a persistent cmdkey SMB mapping exists for the given cabinet IP(s)
    so \\<ip>\c$ or \\<ip>\slot reads work without prompting. Idempotent.
    Uses the per-host user (does not overwrite 10.0.0.111\test with GOLD-CLUB\test).
    #>
    param([Parameter(Mandatory)][string[]] $Ip)
    foreach ($t in $Ip) {
        $user = Get-LabUserForHost -ComputerName $t
        cmdkey /add:$t /user:$user /pass:$script:LabPass 2>&1 | Out-Null
    }
}

function Test-LabWinRmReachable {
    param(
        [Parameter(Mandatory)][string] $Computer,
        [int] $TimeoutMs = 2500
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

function Get-LabPsExecPath {
    <# Resolve PsExec.exe for WinRM auto-enable / legacy AFT transport. #>
    $candidates = @(
        'C:\Tools\PSTools\PsExec.exe',
        (Join-Path $PSScriptRoot 'PsExec.exe'),
        (Join-Path $PSScriptRoot 'lab\PsExec.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return (Resolve-Path -LiteralPath $c).Path }
    }
    return $null
}

function Ensure-LabWinRmReady {
    <#
    Ensure WinRM answers on the cabinet. When port 5985 is closed, enable it
    via PsExec (SYSTEM): stage Enable-WinRM.ps1 / run D:\_ENABLE_WINRM.bat /
    Enable-PSRemoting -SkipNetworkProfileCheck, then wait.
    #>
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [int] $TimeoutSec = 90,
        [switch] $Quiet
    )
    if (Test-LabWinRmReachable -Computer $ComputerName) { return $true }

    if (-not $Quiet) {
        Write-Host "[*] WinRM not ready on $ComputerName - auto-enabling via PsExec..." -ForegroundColor Yellow
    }
    Initialize-LabSmbCredential -Ip @($ComputerName)
    $null = Initialize-LabWinRmTrustedHosts -Ip @($ComputerName)

    $psexec = Get-LabPsExecPath
    if (-not $psexec) {
        throw "WinRM port 5985 is not reachable on $ComputerName and PsExec.exe was not found (cannot auto-enable)."
    }

    $localEnable = @(
        (Join-Path $PSScriptRoot 'cabinet_tools\shared\Enable-WinRM.ps1'),
        (Join-Path $PSScriptRoot 'lab\Enable-WinRM.ps1'),
        'C:\Tools\PSTools\winrmscripts\Enable-WinRM.ps1'
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($localEnable) {
        $staged = $false
        foreach ($unc in @(
            "\\$ComputerName\c`$\Windows\Temp\Enable-WinRM.ps1",
            "\\$ComputerName\USB_Remote\Enable-WinRM.ps1",
            "\\$ComputerName\USB\Enable-WinRM.ps1"
        )) {
            try {
                $dir = Split-Path -Parent $unc
                if (-not (Test-Path -LiteralPath $dir)) {
                    New-Item -ItemType Directory -Force -Path $dir | Out-Null
                }
                Copy-Item -LiteralPath $localEnable -Destination $unc -Force -ErrorAction Stop
                $staged = $true
                break
            } catch { }
        }
        if (-not $staged -and -not $Quiet) {
            Write-Warning "Could not stage Enable-WinRM.ps1 to C`$ or USB_Remote on $ComputerName"
        }
    }

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

    if ($localEnable) {
        try {
            $dirUnc = "\\$ComputerName\c$\Windows\Temp"
            if (-not (Test-Path -LiteralPath $dirUnc)) {
                New-Item -ItemType Directory -Force -Path $dirUnc | Out-Null
            }
            Copy-Item -LiteralPath $localEnable -Destination "$dirUnc\lab_enable_winrm_full.ps1" -Force -ErrorAction Stop
        } catch {
            try {
                Copy-Item -LiteralPath $localEnable -Destination "\\$ComputerName\USB_Remote\Enable-WinRM.ps1" -Force -ErrorAction Stop
            } catch { }
        }
    }

    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($enableBody))
    $auth = @(Get-LabPsExecArgs -ComputerName $ComputerName)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & $psexec "\\$ComputerName" -accepteula @auth -s -n 120 `
            powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $enc 2>&1
    } finally {
        $ErrorActionPreference = $prevEap
    }

    $deadline = (Get-Date).AddSeconds([Math]::Max(15, $TimeoutSec))
    while ((Get-Date) -lt $deadline) {
        if (Test-LabWinRmReachable -Computer $ComputerName -TimeoutMs 1500) {
            if (-not $Quiet) {
                Write-Host "[+] WinRM ready on $ComputerName" -ForegroundColor Green
            }
            return $true
        }
        Start-Sleep -Seconds 2
    }
    throw "WinRM port 5985 is still not reachable on $ComputerName after auto-enable. Check firewall / UWF / reboot."
}

function Invoke-LabWinRmCommand {
    <#
    Run a script block on a lab cabinet via WinRM (Invoke-Command + Negotiate).
    Requires Initialize-LabAccess.ps1 / TrustedHosts for the target IP.
    Auto-enables WinRM via PsExec when port 5985 is down.
    #>
    param(
        [Parameter(Mandatory)][string] $ComputerName,
        [Parameter(Mandatory)][scriptblock] $ScriptBlock,
        [object[]] $ArgumentList = @(),
        [int] $OperationTimeoutMs = 120000
    )
    $trusted = Initialize-LabWinRmTrustedHosts -Ip @($ComputerName)
    if (-not $trusted.Ready) {
        throw "WinRM TrustedHosts not configured for $ComputerName. Run .\Initialize-LabAccess.ps1 as Administrator."
    }
    Ensure-LabWinRmReady -ComputerName $ComputerName | Out-Null
    $sessionOption = New-PSSessionOption -OperationTimeout $OperationTimeoutMs -OpenTimeout 15000
    Invoke-Command -ComputerName $ComputerName -Credential (Get-LabCredential -ComputerName $ComputerName) `
        -Authentication Negotiate -SessionOption $sessionOption `
        -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList -ErrorAction Stop
}

function Test-LabSmbAccess {
    <#
    Probes SMB admin-share access for one lab cabinet IP.
    Returns a PSCustomObject: Ip, Tcp445, AdminShare, LogPath, Ok, Status, Detail.
    #>
    param(
        [Parameter(Mandatory)][string] $Ip,
        [string] $LogSubPath = 'Goldclub\var\log'
    )
    $adminUnc = "\\$Ip\c$"
    $logUnc   = "\\$Ip\c$\$LogSubPath"

    $result = [PSCustomObject]@{
        Ip         = $Ip
        Tcp445     = $false
        AdminShare = $false
        LogPath    = $false
        Ok         = $false
        Status     = 'Unknown'
        Detail     = ''
    }

    # Bounded TCP connect (Test-NetConnection can block ~30s).
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Ip, 445, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2500, $false)
        if ($ok) {
            $client.EndConnect($iar)
            $result.Tcp445 = $true
        }
    }
    catch { }
    finally {
        if ($client) { try { $client.Close() } catch { } }
    }
    if (-not $result.Tcp445) {
        $result.Status = 'Offline'
        $result.Detail = 'no TCP 445 (cabinet offline or SMB blocked)'
        return $result
    }

    try {
        $null = Get-Item -LiteralPath $adminUnc -ErrorAction Stop
        $result.AdminShare = $true
    }
    catch {
        $cMsg = $_.Exception.Message
        if ($_.Exception.InnerException) { $cMsg = $_.Exception.InnerException.Message }
        $slotUnc = "\\$Ip\slot"
        $slotLog = "\\$Ip\slot\var\log"
        try {
            $null = Get-Item -LiteralPath $slotUnc -ErrorAction Stop
            $result.AdminShare = $true
            try {
                $null = Get-Item -LiteralPath $slotLog -ErrorAction Stop
                $result.LogPath = $true
                $result.Ok = $true
                $result.Status = 'OK'
                $result.Detail = "$slotUnc (no C`$)"
                return $result
            }
            catch {
                $result.Status = 'Partial'
                $result.Detail = "$slotUnc OK but $slotLog missing"
                return $result
            }
        }
        catch {
            $sMsg = $_.Exception.Message
            $result.Status = 'Denied'
            $result.Detail = "$adminUnc -> $cMsg; $slotUnc -> $sMsg"
            return $result
        }
    }

    try {
        $null = Get-Item -LiteralPath $logUnc -ErrorAction Stop
        $result.LogPath = $true
        $result.Ok = $true
        $result.Status = 'OK'
        $result.Detail = "$adminUnc + $LogSubPath"
        return $result
    }
    catch {
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) { $msg = $_.Exception.InnerException.Message }
        $result.Status = 'Partial'
        $result.Detail = "c`$ OK but $logUnc -> $msg"
        return $result
    }
}

function Write-LabSmbAccessReport {
    <#
    Runs Test-LabSmbAccess for each IP and prints a pass/fail line per cabinet.
    Returns the result objects (for scripting / exit-code checks).
    #>
    param([Parameter(Mandatory)][string[]] $Ip)

    Write-Host "[*] Verifying SMB access for $($Ip.Count) lab cabinet(s) ..." -ForegroundColor Cyan
    $results = foreach ($t in $Ip) {
        $r = Test-LabSmbAccess -Ip $t
        $label = switch ($r.Status) {
            'OK'      { 'OK   ' }
            'Offline' { 'SKIP ' }
            default   { 'FAIL ' }
        }
        $color = switch ($r.Status) {
            'OK'      { 'Green' }
            'Offline' { 'DarkGray' }
            default   { 'Red' }
        }
        Write-Host ("    [{0}] {1,-12} {2}" -f $label, $r.Ip, $r.Detail) -ForegroundColor $color
        $r
    }

    $okCount      = @($results | Where-Object Status -eq 'OK').Count
    $offlineCount = @($results | Where-Object Status -eq 'Offline').Count
    $failCount    = $Ip.Count - $okCount - $offlineCount
    Write-Host ("[*] SMB verify: {0} OK, {1} FAIL, {2} offline (of {3})" -f `
        $okCount, $failCount, $offlineCount, $Ip.Count) -ForegroundColor Cyan

    return ,$results
}

function Initialize-LabWinRmTrustedHosts {
    <#
    Best-effort: add lab cabinet IPs to the LOCAL WinRM client TrustedHosts list
    so Invoke-Command by IP with NTLM works. Requires an elevated shell to add
    new entries. Returns a hashtable: Ready (bool), AlreadyTrusted, Added, Failed.
    #>
    param([Parameter(Mandatory)][string[]] $Ip)
    $result = @{
        Ready          = $false
        AlreadyTrusted = @()
        Added          = @()
        Failed         = @()
    }
    try {
        $cur = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
    }
    catch {
        $result.Failed = @($Ip)
        return $result
    }
    $entries = @($cur -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($entries -contains '*') {
        $result.Ready = $true
        $result.AlreadyTrusted = @($Ip)
        return $result
    }
    $toAdd = @()
    foreach ($t in $Ip) {
        if ($entries -contains $t) {
            $result.AlreadyTrusted += $t
        }
        else {
            $toAdd += $t
        }
    }
    if ($toAdd.Count -eq 0) {
        $result.Ready = $true
        return $result
    }
    $newEntries = $entries + $toAdd
    try {
        Set-Item WSMan:\localhost\Client\TrustedHosts -Value ($newEntries -join ',') -Force -ErrorAction Stop
        $result.Added = $toAdd
        $result.Ready = $true
        return $result
    }
    catch {
        $result.Failed = $toAdd
        # Partial: WinRM works for IPs already in TrustedHosts (e.g. only .90 configured).
        $result.Ready = ($result.AlreadyTrusted.Count -gt 0)
        return $result
    }
}
