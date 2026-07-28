<#
.SYNOPSIS
    Shared lab-cabinet credential helper. Dot-source this from any remote script
    to get the standard GoldClub lab credential and WinRM helpers.

.DESCRIPTION
    Usage (inside another script):

        . "$PSScriptRoot\LabAccess.ps1"
        $cred = Get-LabCredential
        Invoke-LabWinRmCommand -ComputerName $ip -ScriptBlock { hostname }

    The credential is the lab account GOLD-CLUB\test / test. SMB share access is
    handled separately by the persistent cmdkey mapping (see Initialize-LabAccess.ps1).
    Remote execution should use WinRM (Invoke-Command), not PsExec.

.NOTES
    Lab credential only (test/test). Not for production hosts.
#>

$script:LabUser = 'GOLD-CLUB\test'
$script:LabPass = 'test'

function Get-LabCredential {
    <# Returns a [pscredential] for the lab account. #>
    $sec = ConvertTo-SecureString $script:LabPass -AsPlainText -Force
    return [System.Management.Automation.PSCredential]::new($script:LabUser, $sec)
}

function Get-LabPsExecArgs {
    <# Returns the PsExec auth args array: -u <user> -p <pass> (legacy AFT scripts only). #>
    return @('-u', $script:LabUser, '-p', $script:LabPass)
}

function Initialize-LabSmbCredential {
    <#
    Ensures a persistent cmdkey SMB mapping exists for the given cabinet IP(s)
    so \\<ip>\c$ reads work without prompting. Idempotent.
    #>
    param([Parameter(Mandatory)][string[]] $Ip)
    foreach ($t in $Ip) {
        cmdkey /add:$t /user:$script:LabUser /pass:$script:LabPass 2>&1 | Out-Null
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

function Invoke-LabWinRmCommand {
    <#
    Run a script block on a lab cabinet via WinRM (Invoke-Command + Negotiate).
    Requires Initialize-LabAccess.ps1 / TrustedHosts for the target IP.
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
    if (-not (Test-LabWinRmReachable -Computer $ComputerName)) {
        throw "WinRM port 5985 is not reachable on $ComputerName."
    }
    $sessionOption = New-PSSessionOption -OperationTimeout $OperationTimeoutMs -OpenTimeout 15000
    Invoke-Command -ComputerName $ComputerName -Credential (Get-LabCredential) `
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
        $msg = $_.Exception.Message
        if ($_.Exception.InnerException) { $msg = $_.Exception.InnerException.Message }
        $result.Status = 'Denied'
        $result.Detail = "$adminUnc -> $msg"
        return $result
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
