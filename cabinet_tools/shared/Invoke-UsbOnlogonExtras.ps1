# USB on-logon extras: TeamViewer restore, share/test user, Total Commander.
$ErrorActionPreference = 'Continue'

. (Join-Path $PSScriptRoot 'Onlogon-Logging.ps1')
Initialize-OnlogonLog -ScriptRoot $PSScriptRoot
# USB on-logon extras: TeamViewer restore, share/test user, Total Commander.
# Called from usb_scripts\onlogon.ps1 after Initialize-Platform.
$ErrorActionPreference = 'Continue'

function Write-OnlogonLog([string]$Message) {
    $logDir = 'C:\goldclub\var\log'
    if (-not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $line = "[$(Get-Date -Format o)] $Message"
    Add-Content -LiteralPath (Join-Path $logDir 'onlogon.log') -Value $line -Encoding UTF8
    Write-Verbose $line
}

function Get-UsbRoot {
    if ($PSScriptRoot -and $PSScriptRoot -match 'usb_scripts') {
        $fromScript = (Split-Path -Parent $PSScriptRoot).TrimEnd('\')
        $marker = Join-Path $fromScript 'TeamViewer_LoginBackup\restore_tv_login.cmd'
        if (Test-Path -LiteralPath $marker) { return $fromScript }
    }
    $markerRel = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        $candidate = Join-Path $psd.Root.TrimEnd('\') $markerRel
        if (Test-Path -LiteralPath $candidate) { return $psd.Root.TrimEnd('\') }
    }
    $vol = Get-Volume -ErrorAction SilentlyContinue | Where-Object {
        $_.DriveLetter -and $_.FileSystemLabel -eq 'USB'
    } | Select-Object -First 1
    if ($vol) {
        $root = "$($vol.DriveLetter):"
        if (Test-Path -LiteralPath (Join-Path $root $markerRel)) { return $root }
    }
    return $null
}

function Invoke-SafeCmdScript {
    param(
        [string]$Label,
        [string]$ScriptPath,
        [string]$ExtraArgs = '',
        [int]$TimeoutSec = 45
    )
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        Write-OnlogonLog "$Label skipped: not found ($ScriptPath)"
        return $false
    }
    Write-OnlogonLog "before $Label"
    $proc = $null
    try {
        $cmdTail = if ($ExtraArgs) { "`"$ScriptPath`" $ExtraArgs" } else { "`"$ScriptPath`"" }
        $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmdTail -PassThru -WindowStyle Hidden
        if ($proc) {
            Wait-Process -Id $proc.Id -Timeout $TimeoutSec -ErrorAction Stop
        }
        $code = if ($proc) { $proc.ExitCode } else { 'N/A' }
        Write-OnlogonLog "after $Label (exit=$code)"
        return ($code -eq 0 -or $code -eq 'N/A')
    }
    catch {
        Write-OnlogonLog "$Label timeout/error; continuing: $($_.Exception.Message)"
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
        return $false
    }
}

function Start-TeamViewerFromUsb {
    param([string]$UsbRoot)
    $restore = Join-Path $UsbRoot 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    if (Invoke-SafeCmdScript -Label 'restore_tv_login' -ScriptPath $restore -TimeoutSec 90) {
        return $true
    }
    foreach ($rel in @(
        'TeamViewerPortable\TeamViewer.exe',
        'TeamViewer\TeamViewer.exe'
    )) {
        $exe = Join-Path $UsbRoot $rel
        if (Test-Path -LiteralPath $exe) {
            Write-OnlogonLog "TeamViewer fallback launch: $exe"
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -ErrorAction SilentlyContinue
            return $true
        }
    }
    Write-OnlogonLog 'TeamViewer not started (restore failed and no portable exe found)'
    return $false
}

function Enable-UsbShareAndTestUser {
    param([string]$UsbRoot)
    $shareBat = Join-Path $UsbRoot '_share.bat'
    if (Test-Path -LiteralPath $shareBat) {
        Invoke-SafeCmdScript -Label '_share.bat' -ScriptPath $shareBat -ExtraArgs '-nopause' -TimeoutSec 30 | Out-Null
    }
    try {
        if (Test-Path -LiteralPath 'G:\') {
            cmd /c 'net share slot=G:\ /grant:everyone,FULL' 2>&1 | ForEach-Object { Write-OnlogonLog "share: $_" }
        }
        else {
            Write-OnlogonLog 'share skipped: G:\ not present'
        }
        $userCheck = cmd /c 'net user test' 2>&1 | Out-String
        if ($userCheck -match 'The user name could not be found') {
            cmd /c 'net user test test /add' 2>&1 | ForEach-Object { Write-OnlogonLog "user: $_" }
        }
        else {
            Write-OnlogonLog 'user test already exists'
        }
        cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { Write-OnlogonLog "admin: $_" }
    }
    catch {
        Write-OnlogonLog "share/user setup error (continuing): $($_.Exception.Message)"
    }
}

function Start-TotalCommanderFromUsb {
    param([string]$UsbRoot)
    $startTc = Join-Path $PSScriptRoot 'Start-TotalCommander.ps1'
    if (Test-Path -LiteralPath $startTc) {
        try { . $startTc } catch { Write-OnlogonLog "Start-TotalCommander.ps1 failed: $($_.Exception.Message)" }
        return
    }
    foreach ($name in @('TOTALCMD.EXE', 'TOTALCMD64.EXE')) {
        $tc = Join-Path $UsbRoot "totalcmd\$name"
        if (Test-Path -LiteralPath $tc) {
            Write-OnlogonLog "Total Commander fallback: $tc"
            Start-Process -FilePath $tc -WorkingDirectory (Split-Path -Parent $tc) -ErrorAction SilentlyContinue
            return
        }
    }
    Write-OnlogonLog 'Total Commander not found on USB'
}

# --- run ---
$usbRoot = Get-UsbRoot
if (-not $usbRoot) {
    Write-OnlogonLog 'USB root not found (TeamViewer_LoginBackup marker missing)'
    return
}
Write-OnlogonLog "USB root: $usbRoot"

$bootstrap = 'C:\goldclub\bootstrap.exe'
if (Test-Path -LiteralPath $bootstrap) {
    try {
        Write-OnlogonLog 'Starting bootstrap.exe'
        Start-Process -FilePath $bootstrap -ErrorAction Stop
    }
    catch {
        Write-OnlogonLog "bootstrap.exe failed: $($_.Exception.Message)"
    }
}

Start-TeamViewerFromUsb -UsbRoot $usbRoot
Enable-UsbShareAndTestUser -UsbRoot $usbRoot
Start-TotalCommanderFromUsb -UsbRoot $usbRoot
Write-OnlogonLog 'USB on-logon extras complete'