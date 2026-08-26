# C:\goldclub\bin\Setup.exe superadmin + remote slot share (test user, G: share).
param(
    [string] $Keyword = 'keyword here',
    [string] $SetupExe = 'C:\goldclub\bin\Setup.exe',
    [string] $SetupProfile = 'application.ruleta.setup',
    [string] $User = 'superadmin',
    [string] $Mangler = 'type0',
    [string] $SharePath = 'G:\',
    [string] $ShareName = 'slot',
    [string] $RemoteUser = 'test',
    [string] $RemotePassword = 'test',
    [switch] $ShareOnly,
    [switch] $SetupOnly,
    [switch] $EnableWinRM,
    [switch] $DisableUwf,
    [switch] $Reboot,
    [int] $RebootSeconds = 30,
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$LogPath = Join-Path $PSScriptRoot 'cabinet-superadmin-share.log'

function Write-Log([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    Write-Host $line
    [IO.File]::AppendAllText($LogPath, $line + [Environment]::NewLine, $Utf8)
}

function Invoke-DisableUwfIfNeeded {
    $module = 'C:\goldclub\bin\lib\powershell\goldclub.filter.1\goldclub.filter.1.psm1'
    if (-not (Test-Path -LiteralPath $module)) { return }
    Import-Module $module -Force -ErrorAction Stop
    $state = Show-UWFState
    Write-Log "UWF state: $state"
    if ($state -eq 'enabled') {
        if ($WhatIf) { Write-Log 'WHATIF: Disable-UWF'; return }
        Disable-UWF | Out-Null
        Write-Log 'Disable-UWF scheduled (reboot required)'
    }
}

function Enable-RemoteCabinetShare {
    if (-not (Test-Path -LiteralPath $SharePath)) {
        throw "Share path missing: $SharePath (assign G: on BIWIN first)"
    }
    Write-Log "Enabling share $ShareName -> $SharePath"
    if ($WhatIf) { return }

    cmd /c "net share $ShareName /delete /y" 2>&1 | ForEach-Object { Write-Log "  $_" }
    cmd /c "net share $ShareName=$SharePath /grant:everyone,FULL" 2>&1 | ForEach-Object { Write-Log "  $_" }

    $userExists = $false
    cmd /c "net user $RemoteUser" 2>&1 | ForEach-Object {
        Write-Log "  net user: $_"
        if ($_ -match 'User name') { $userExists = $true }
    }
    if (-not $userExists) {
        cmd /c "net user $RemoteUser $RemotePassword /add" 2>&1 | ForEach-Object { Write-Log "  $_" }
    }
    cmd /c "net localgroup administrators $RemoteUser /add" 2>&1 | ForEach-Object { Write-Log "  $_" }

    try {
        Enable-NetFirewallRule -DisplayGroup 'File And Printer Sharing' -ErrorAction SilentlyContinue | Out-Null
        Set-NetFirewallRule -DisplayGroup 'File And Printer Sharing' -Enabled True -ErrorAction SilentlyContinue | Out-Null
        Write-Log 'Firewall: File And Printer Sharing enabled'
    }
    catch {
        Write-Log "WARN firewall: $($_.Exception.Message)"
    }

    $shares = cmd /c 'net share' 2>&1 | Out-String
    Write-Log ("net share:`n" + $shares.Trim())
}

function Invoke-RuletaSuperadminSetup {
    if (-not (Test-Path -LiteralPath $SetupExe)) {
        throw "Setup.exe not found: $SetupExe"
    }
    if (-not $Keyword.Trim()) {
        $Keyword = 'keyword here'
    }
    $binDir = Split-Path $SetupExe -Parent
    $args = @('--setup', $SetupProfile, '--user', $User, '--mangler', $Mangler, '--keyword', $Keyword)
    Write-Log ("Setup: $SetupExe " + ($args -join ' '))
    if ($WhatIf) { return }

    Push-Location $binDir
    try {
        & $SetupExe @args
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    Write-Log "Setup.exe exit: $code"
    if ($code -ne 0) { throw "Setup.exe failed with exit $code" }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin) {
    Write-Host 'Run Invoke-CabinetSuperadminAndShare.bat as Administrator.'
    exit 5
}

[IO.File]::WriteAllText($LogPath, '', $Utf8)
Write-Log '=== Cabinet superadmin + remote share ==='
Write-Log ("Computer=$env:COMPUTERNAME SetupExe=$SetupExe Share=$ShareName")

if ($DisableUwf) { Invoke-DisableUwfIfNeeded }

if (-not $SetupOnly) {
    Enable-RemoteCabinetShare
}

if (-not $ShareOnly) {
    Invoke-RuletaSuperadminSetup
}

if ($EnableWinRM -and -not $WhatIf) {
    $winrm = Join-Path $PSScriptRoot 'Enable-WinRM.ps1'
    if (Test-Path -LiteralPath $winrm) {
        Write-Log 'Running Enable-WinRM.ps1'
        & powershell -NoProfile -ExecutionPolicy Bypass -File $winrm
    }
}

if ($Reboot -and -not $WhatIf) {
    shutdown /r /t $RebootSeconds /c 'Cabinet superadmin + share setup'
}

Write-Log '=== DONE ==='
Write-Host "Log: $LogPath"
exit 0