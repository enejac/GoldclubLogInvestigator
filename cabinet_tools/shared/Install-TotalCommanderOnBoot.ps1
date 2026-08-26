<#
.SYNOPSIS
    Install Total Commander auto-start on every EGM reboot (roulette/slot normal boot).

.DESCRIPTION
    - Copies Start-TotalCommander.ps1 to C:\goldclub\bin\
    - Installs 99-StartTotalCommander.ps1 into GoldClub onlogon task folder
    - Registers SYSTEM scheduled task GoldClub-TotalCommander (logon trigger)

    Hooks Windows login on C:\goldclub\... — not GCEFISYS (EFI).

.EXAMPLE
    .\_INSTALL_TOTALCMD_ON_BOOT.bat
#>
param([switch]$WhatIf)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$coreScript = Join-Path $here 'Start-TotalCommander.ps1'
$onlogonDir = 'C:\goldclub\platform\user\init\onlogon'
$targetScript = Join-Path $onlogonDir '99-StartTotalCommander.ps1'

function Log([string]$Message) {
    Write-Host "[$(Get-Date -Format o)] $Message"
}

if (-not (Test-Path -LiteralPath $coreScript)) {
    Log "Missing $coreScript"
    exit 1
}

if ($WhatIf) {
    Log 'WOULD copy Start-TotalCommander.ps1 to C:\goldclub\bin\'
    Log "WOULD install $targetScript"
    Log 'WOULD register scheduled task GoldClub-TotalCommander (SYSTEM, AtLogOn)'
    exit 0
}

$binDir = 'C:\goldclub\bin'
if (-not (Test-Path -LiteralPath $binDir)) {
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
}
Copy-Item -LiteralPath $coreScript -Destination (Join-Path $binDir 'Start-TotalCommander.ps1') -Force
Log 'Copied Start-TotalCommander.ps1 to C:\goldclub\bin\'

if (-not (Test-Path -LiteralPath $onlogonDir)) {
    New-Item -ItemType Directory -Path $onlogonDir -Force | Out-Null
}
Copy-Item -LiteralPath (Join-Path $here 'onlogon\99-StartTotalCommander.ps1') -Destination $targetScript -Force
Log "Installed $targetScript"

. (Join-Path $binDir 'Start-TotalCommander.ps1')

Log 'Done. Total Commander will start as SYSTEM on every logon/reboot.'
Log 'Log file: C:\goldclub\var\log\totalcmd-boot.log'
exit 0
