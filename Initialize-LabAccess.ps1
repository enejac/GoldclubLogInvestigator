<#
.SYNOPSIS
    One-shot lab-cabinet access bootstrap. Registers persistent SMB credentials
    (Windows Credential Manager via cmdkey) for every known GoldClub lab cabinet
    so `\\<ip>\c$\...` share reads "just work" with no interactive prompt, across
    reboots and new sessions.

.DESCRIPTION
    The lab cabinets authenticate with the local/AD account GOLD-CLUB\test
    (password "test"). The interactive workstation user is NOT an admin on every
    cabinet (e.g. 10.0.0.171), so without a stored credential, SMB and PsExec to
    those cabinets get "Access is denied".

    `cmdkey /add:<ip>` stores the credential for the SMB redirector. This persists
    in Credential Manager (survives logoff/reboot), so the Python log-investigator
    and the PowerShell remote scripts can read the admin shares automatically.

    This script is idempotent: re-running it simply refreshes each mapping.

    For remote process execution, dot-source LabAccess.ps1 instead (or below) to
    get a [pscredential] and the PsExec -u/-p arguments for cabinets where the
    interactive user lacks admin rights.

.EXAMPLE
    # Register all known cabinets (run once per workstation / after a profile reset):
    .\Initialize-LabAccess.ps1

.EXAMPLE
    # Add an extra cabinet and verify share access afterwards:
    .\Initialize-LabAccess.ps1 -ExtraIp 10.0.0.123 -Verify

.NOTES
    Lab credential only (test/test). Do not reuse this pattern for production hosts.
#>
[CmdletBinding()]
param(
    # Known GoldClub lab cabinet IPs. Edit here when the fleet changes.
    [string[]] $Ip = @(
        '10.0.0.83',
        '10.0.0.90',
        '10.0.0.100',
        '10.0.0.110',
        '10.0.0.112',
        '10.0.0.171'
    ),
    [string[]] $ExtraIp = @(),
    [string]   $User = 'GOLD-CLUB\test',
    [string]   $Pass = 'test',
    [switch]   $Verify
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$targets = @($Ip + $ExtraIp | Select-Object -Unique)

Write-Host "[*] Registering SMB credentials for $($targets.Count) lab cabinet(s) as $User ..." -ForegroundColor Cyan
foreach ($t in $targets) {
    $out = cmdkey /add:$t /user:$User /pass:$Pass 2>&1 | Out-String
    if ($out -match 'successfully') {
        Write-Host "    [+] $t" -ForegroundColor Green
    }
    else {
        Write-Host "    [!] $t -> $($out.Trim())" -ForegroundColor Yellow
    }
}

$labAccess = Join-Path $PSScriptRoot 'LabAccess.ps1'
if (Test-Path -LiteralPath $labAccess) {
    . $labAccess
    if ($Verify) {
        $null = Write-LabSmbAccessReport -Ip $targets
    }
}

Write-Host "[*] Done. Credentials persist in Credential Manager (survive reboot)." -ForegroundColor Cyan
Write-Host "    For remote exec on non-admin cabinets, dot-source LabAccess.ps1 to get -u/-p." -ForegroundColor DarkGray

# WinRM-by-IP needs TrustedHosts on this workstation (one-time, requires admin to add NEW IPs).
if (Test-Path -LiteralPath $labAccess) {
    $th = Initialize-LabWinRmTrustedHosts -Ip $targets
    if ($th.AlreadyTrusted.Count) {
        Write-Host ("[+] WinRM TrustedHosts already includes: {0}" -f ($th.AlreadyTrusted -join ', ')) -ForegroundColor Green
    }
    if ($th.Added.Count) {
        Write-Host ("[+] WinRM TrustedHosts added: {0}" -f ($th.Added -join ', ')) -ForegroundColor Green
    }
    if ($th.Failed.Count) {
        Write-Host ("[*] Could not add to TrustedHosts (need elevated shell): {0}" -f ($th.Failed -join ', ')) -ForegroundColor DarkGray
        Write-Host '    Run once as Administrator:' -ForegroundColor DarkGray
        $all = ($th.AlreadyTrusted + $th.Failed | Select-Object -Unique) -join ','
        Write-Host "      Set-Item WSMan:\localhost\Client\TrustedHosts -Value '$all' -Force" -ForegroundColor DarkGray
    }
    if ($th.Ready) {
        Write-Host '[+] WinRM inject transport is ready for cabinets already in TrustedHosts.' -ForegroundColor Green
    }
}
