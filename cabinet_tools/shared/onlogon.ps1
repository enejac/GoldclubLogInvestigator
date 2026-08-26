# onlogon.ps1 - self-contained (no helper files required). Copy via CopyOnlogon.bat to C:\goldclub\platform\user\init\
$ErrorActionPreference = 'Continue'

function Write-OnlogonRunLog {
    param([string]$Message)
    $line = "[$(Get-Date -Format o)] $Message"
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $paths = New-Object System.Collections.Generic.List[string]
    if ($PSScriptRoot) { [void]$paths.Add((Join-Path $PSScriptRoot 'onlogon-run.log')) }
    [void]$paths.Add('C:\goldclub\var\log\onlogon.log')
    $marker = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        $root = $psd.Root.TrimEnd('\')
        if ($root -and (Test-Path -LiteralPath (Join-Path $root $marker))) {
            [void]$paths.Add((Join-Path $root 'onlogon-run.log'))
            $usbScripts = Join-Path $root 'usb_scripts'
            if (Test-Path -LiteralPath $usbScripts) { [void]$paths.Add((Join-Path $usbScripts 'onlogon-run.log')) }
        }
    }
    foreach ($logPath in ($paths | Select-Object -Unique)) {
        try {
            $dir = Split-Path -Parent $logPath
            if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            [IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine, $utf8)
        } catch {}
    }
    Write-Host $line
}

Write-OnlogonRunLog '========== ONLOGON RUN START =========='
Write-OnlogonRunLog "script=$PSCommandPath psscriptroot=$PSScriptRoot user=$env:USERDOMAIN\$env:USERNAME computer=$env:COMPUTERNAME"

Get-Module | Where-Object -Property 'Name' -Like 'goldclub.*' | Remove-Module
Write-OnlogonRunLog 'removed goldclub.* modules'

$initModule = Join-Path $PSScriptRoot '/../../bin/lib/powershell/goldclub.init.1'
$initJson = Join-Path $PSScriptRoot '/init.json'
Write-OnlogonRunLog "init module=$initModule exists=$(Test-Path -LiteralPath $initModule)"
Write-OnlogonRunLog "init json=$initJson exists=$(Test-Path -LiteralPath $initJson)"

try {
    Write-OnlogonRunLog 'Initialize-Platform: starting'
    Import-Module $initModule -ErrorAction Stop
    goldclub.init.1\Initialize-Platform -taskToRun ($PSScriptRoot + '/onlogon') -configFile $initJson -ErrorAction Stop -Verbose
    Write-OnlogonRunLog 'Initialize-Platform: OK'
}
catch {
    Write-OnlogonRunLog "Initialize-Platform: FAILED - $($_.Exception.Message)"
}

function Get-GoldClubUsbRoot {
    $parent = Split-Path $PSScriptRoot -Parent
    $markerOnParent = Join-Path $parent 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    if (Test-Path -LiteralPath $markerOnParent) { return $parent.TrimEnd('\') }
    $marker = 'TeamViewer_LoginBackup\restore_tv_login.cmd'
    foreach ($psd in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        $candidate = Join-Path $psd.Root $marker
        if (Test-Path -LiteralPath $candidate) { return $psd.Root.TrimEnd('\') }
    }
    return $null
}

function Resolve-UsbShareBatch {
    param([string]$Root)
    foreach ($name in @('share.bat', '_share.bat')) {
        $path = Join-Path $Root $name
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}

function Start-TotalCommanderFromUsb {
    param([string]$UsbRoot)
    $candidates = @()
    if ($UsbRoot) {
        $candidates += (Join-Path $UsbRoot 'totalcmd\TOTALCMD.EXE')
        $candidates += (Join-Path $UsbRoot 'totalcmd\TOTALCMD64.EXE')
    }
    $candidates += @('D:\totalcmd\TOTALCMD.EXE', 'C:\totalcmd\TOTALCMD.EXE')
    foreach ($exe in $candidates) {
        if ($exe -and (Test-Path -LiteralPath $exe)) {
            Write-OnlogonRunLog "Starting Total Commander: $exe"
            Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -ErrorAction SilentlyContinue
            return
        }
    }
    Write-OnlogonRunLog 'Total Commander not found'
}

$UsbRoot = Get-GoldClubUsbRoot
if (-not $UsbRoot) {
    Write-OnlogonRunLog 'USB root not found - skip TeamViewer/share/TC from USB'
}
else {
    Write-OnlogonRunLog "USB root: $UsbRoot"

    Write-OnlogonRunLog 'before restore_tv_login'
    try {
        $tvProc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$UsbRoot\TeamViewer_LoginBackup\restore_tv_login.cmd`"" -PassThru
        if ($tvProc) { Wait-Process -Id $tvProc.Id -Timeout 90 -ErrorAction Stop }
        $tvExit = if ($tvProc) { $tvProc.ExitCode } else { 'N/A' }
        Write-OnlogonRunLog "after restore_tv_login exit=$tvExit"
    }
    catch {
        Write-OnlogonRunLog "restore_tv_login failed: $($_.Exception.Message)"
        if ($tvProc -and -not $tvProc.HasExited) { Stop-Process -Id $tvProc.Id -Force -ErrorAction SilentlyContinue }
        $fallback = Join-Path $UsbRoot 'TeamViewerPortable\TeamViewer.exe'
        if (Test-Path -LiteralPath $fallback) {
            Write-OnlogonRunLog "TeamViewer fallback: $fallback"
            Start-Process -FilePath $fallback -WorkingDirectory (Split-Path -Parent $fallback) -ErrorAction SilentlyContinue
        }
    }

    $shareBat = Resolve-UsbShareBatch -Root $UsbRoot
    if ($shareBat) {
        Write-OnlogonRunLog "before share ($shareBat)"
        try {
            $shareProc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$shareBat`" -nopause" -PassThru
            if ($shareProc) { Wait-Process -Id $shareProc.Id -Timeout 30 -ErrorAction Stop }
            $shareExit = if ($shareProc) { $shareProc.ExitCode } else { 'N/A' }
            Write-OnlogonRunLog "after share exit=$shareExit"
        }
        catch { Write-OnlogonRunLog "share failed: $($_.Exception.Message)" }
    }
    else { Write-OnlogonRunLog 'share skipped: share.bat / _share.bat not found' }

    if (Test-Path -LiteralPath 'G:\') {
        cmd /c 'net share slot=G:\ /grant:everyone,FULL' 2>&1 | ForEach-Object { Write-OnlogonRunLog "share-cmd: $_" }
    }
    cmd /c 'net user test' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { cmd /c 'net user test test /add' 2>&1 | ForEach-Object { Write-OnlogonRunLog "user: $_" } }
    cmd /c 'net localgroup administrators test /add' 2>&1 | ForEach-Object { Write-OnlogonRunLog "admin: $_" }

    Start-TotalCommanderFromUsb -UsbRoot $UsbRoot
}

Write-OnlogonRunLog '========== ONLOGON RUN END =========='