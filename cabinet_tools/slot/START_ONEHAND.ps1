# START_ONEHAND.ps1 - lab soft RAM-clear without reboot, then start OneHand.
#
# SAS soft meters ($7A / CBE029) come from SASControler.ConsumeEvent("RAMCLEAR")
# -> CabinetManager.MetersResetToZero. That only succeeds when cabinet devices
# already exist (Count -gt 0); otherwise the event is dropped.
#
# Official reboot works because onlogon stamps DEBG.RAMCLEAR, then services +
# game come up and EventProvider redelivers while devices are online. Soft path
# must stamp *after* OneHand has registered the cabinet, then bounce LogDaemon
# so FilteredEventLog emits a fresh OS_START (power $18/$17) + the new RAMCLEAR.

[CmdletBinding()]
param(
    [string]$GoldclubRoot = "",
    [switch]$SeedCopy,
    [int]$ServiceStopTimeoutSec = 45,
    [int]$PostServiceSettleSec = 8,
    [int]$CabinetWaitSec = 90
)

$ErrorActionPreference = "Continue"

function Write-Step([string]$Message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

function Find-GoldclubRoot {
    param([string]$Preferred)
    if ($Preferred -and (Test-Path -LiteralPath $Preferred)) {
        return (Resolve-Path -LiteralPath $Preferred).Path
    }
    foreach ($cand in @("C:\Goldclub", "G:\Goldclub", "D:\Goldclub")) {
        if (Test-Path -LiteralPath (Join-Path $cand "bin")) {
            return $cand
        }
    }
    throw "Goldclub root not found (tried C:\Goldclub, G:\Goldclub, D:\Goldclub)"
}

function Stop-GoldclubServicesBounded {
    param([int]$TimeoutSec)
    Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "goldclub*"
    } | ForEach-Object {
        $svc = $_
        Write-Step ("Stopping service: " + $svc.Name)
        Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($svc.Status -ne "Stopped" -and $sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
            Start-Sleep -Milliseconds 400
            $svc.Refresh()
        }
        if ($svc.Status -ne "Stopped") {
            Write-Step ("WARN still " + $svc.Status + " - sc stop " + $svc.Name)
            & sc.exe stop $svc.Name | Out-Null
        }
    }
}

function Clear-FolderContents {
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return }
    Write-Step ("Clearing: " + $Dir)
    Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Start-GoldclubServices {
    Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "goldclub*"
    } | ForEach-Object {
        Write-Step ("Starting service: " + $_.Name)
        Start-Service -Name $_.Name -ErrorAction SilentlyContinue
    }
}

function Get-LogDaemonServices {
    Get-Service -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "goldclub*" -and $_.Name -match "(?i)logdaemon|logging\.logdaemon"
    }
}

function Stop-LogDaemonUnclean {
    # Clean Stop-Service clears the unexpected-shutdown flag; force-kill so
    # FilteredEventLog writes OS_START (and often UNEXPECTED_SHUTDOWN) on restart.
    Get-LogDaemonServices | ForEach-Object {
        $svc = $_
        Write-Step ("Unclean stop LogDaemon service: " + $svc.Name)
        try {
            $pid = (Get-CimInstance Win32_Service -Filter ("Name='" + $svc.Name.Replace("'", "''") + "'") -ErrorAction SilentlyContinue).ProcessId
            if ($pid -and $pid -gt 0) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            }
        } catch {}
        & sc.exe stop $svc.Name | Out-Null
    }
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match "(?i)LogDaemon"
    } | ForEach-Object {
        Write-Step ("Killing " + $_.ProcessName + " pid=" + $_.Id)
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

function Start-LogDaemonServices {
    Get-LogDaemonServices | ForEach-Object {
        Write-Step ("Starting LogDaemon service: " + $_.Name)
        Start-Service -Name $_.Name -ErrorAction SilentlyContinue
    }
}

function Wait-CabinetDeviceOnline {
    param([string]$Root, [int]$TimeoutSec)
    Write-Step ("Waiting up to ${TimeoutSec}s for Aurum cabinet device ONLINE")
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $patterns = @(
        (Join-Path $Root "var\state\goldclub.aurum.services\GCMessenger\*\DeviceManagerData.xml_*"),
        (Join-Path $Root "var\state\GoldClub.Aurum.Services\GCMessenger\*\DeviceManagerData.xml_*")
    )
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        foreach ($pat in $patterns) {
            Get-ChildItem -Path $pat -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    # File may be mid-write / locked; share read.
                    $fs = [IO.File]::Open($_.FullName, "Open", "Read", "ReadWrite")
                    try {
                        $sr = New-Object IO.StreamReader($fs)
                        $t = $sr.ReadToEnd()
                    } finally { $fs.Close() }
                    if ($t -match 'DeviceClass="cabinet"' -and $t -match 'Status="ONLINE"') {
                        Write-Step ("Cabinet ONLINE in " + $_.FullName)
                        return $true
                    }
                } catch {}
            }
        }
        Start-Sleep -Seconds 2
    }
    Write-Step "WARN cabinet device not seen ONLINE - stamping anyway (SAS 0x7A may miss)"
    return $false
}

function Invoke-LogDaemonRamClearStamp {
    param([string]$Root)
    $ramClearExe = Join-Path $Root "bin\LogDaemonRamClear.exe"
    if (-not (Test-Path -LiteralPath $ramClearExe)) {
        throw ("Missing LogDaemonRamClear.exe at " + $ramClearExe)
    }
    # Age heartbeat so plugin treats restart as unexpected shutdown / OS_START.
    $hb = Join-Path $Root "services\logdaemon\var\GoldClub.Logging.LogDaemon.Plugin.FilteredEventLog\heartbeat"
    if (Test-Path -LiteralPath $hb) {
        try { (Get-Item -LiteralPath $hb).LastWriteTime = (Get-Date).AddHours(-2) } catch {}
    }
    Write-Step ("Running " + $ramClearExe + " (after cabinet online)")
    $p = Start-Process -FilePath $ramClearExe -WorkingDirectory (Split-Path $ramClearExe) -Wait -PassThru
    Write-Step ("LogDaemonRamClear exit=" + $p.ExitCode)
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Find-GoldclubRoot -Preferred $GoldclubRoot
Write-Step ("Goldclub root: " + $root)

# --- 1) Kill bootloader FIRST, then game ---
# Bootstrap.exe is the slot bootloader/watchdog. If it is still running when
# OneHand is force-killed, it may reboot the cabinet. Kill Bootstrap/BiOS2
# before OneHand/game-start. After wipe we restart Bootstrap so ESC from
# OneHand can bring BiOS2 back up.
foreach ($procName in @("Bootstrap", "BiOS2", "OneHand", "game-start")) {
    Get-Process -Name $procName -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Step ("Killing " + $_.ProcessName + " pid=" + $_.Id)
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 1

# --- 2) Stop all goldclub* services ---
Stop-GoldclubServicesBounded -TimeoutSec $ServiceStopTimeoutSec
Start-Sleep -Seconds 1

# --- 3) Official cleanup targets (contents only) ---
$relFolders = @(
    "slot\var",
    "var\state\OneHand",
    "var\state\GoldClub.Aurum.Services",
    "var\state\goldclub.aurum.services",
    "var\state\hwsubsys",
    "var\cache",
    "services\aurum\var",
    "services\logdaemon\var",
    "var\state\GoldClub.Logging.LogDaemon",
    "var\state\GoldClub.Logging.LogDaemon.Plugin.FilteredEventLog"
)
foreach ($rel in $relFolders) {
    Clear-FolderContents (Join-Path $root $rel)
}

# --- 4) Optional empty seed templates (lab only; off by default — seeds can hide soft meters) ---
if ($SeedCopy) {
    $seedAurum = Join-Path $scriptDir "goldclub.aurum.services"
    $seedVar = Join-Path $scriptDir "var"
    $destAurum = Join-Path $root "var\state\GoldClub.Aurum.Services"
    $destSlotVar = Join-Path $root "slot\var"
    if (Test-Path -LiteralPath $seedAurum) {
        Write-Step ("Seeding Aurum state from " + $seedAurum)
        & xcopy.exe $seedAurum $destAurum /E /Y /I /Q | Out-Null
    }
    if (Test-Path -LiteralPath $seedVar) {
        Write-Step ("Seeding slot\var from " + $seedVar)
        & xcopy.exe $seedVar $destSlotVar /E /Y /I /Q | Out-Null
    }
}

# --- 5) Services + Bootstrap bootloader (starts OneHand; ESC -> BiOS2) ---
Start-GoldclubServices
Write-Step ("Waiting ${PostServiceSettleSec}s for services")
Start-Sleep -Seconds $PostServiceSettleSec

$bootstrap = Join-Path $root "Bootstrap.exe"
$oneHand = Join-Path $root "slot\OneHand.exe"
if (-not (Test-Path -LiteralPath $oneHand)) {
    throw ("Missing OneHand.exe at " + $oneHand)
}

if (Test-Path -LiteralPath $bootstrap) {
    Write-Step ("Starting bootloader " + $bootstrap + " (keeps BiOS2 recoverable on ESC)")
    Start-Process -FilePath $bootstrap -WorkingDirectory $root
    $swOh = [Diagnostics.Stopwatch]::StartNew()
    while ($swOh.Elapsed.TotalSeconds -lt 45) {
        if (Get-Process -Name "OneHand" -ErrorAction SilentlyContinue) {
            Write-Step "OneHand started by Bootstrap"
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not (Get-Process -Name "OneHand" -ErrorAction SilentlyContinue)) {
        Write-Step "WARN Bootstrap did not start OneHand within 45s - starting OneHand directly"
        Start-Process -FilePath $oneHand -WorkingDirectory (Split-Path $oneHand)
    }
} else {
    Write-Step ("WARN missing " + $bootstrap + " - starting OneHand directly (ESC may not return to BiOS2)")
    Start-Process -FilePath $oneHand -WorkingDirectory (Split-Path $oneHand)
}

Wait-CabinetDeviceOnline -Root $root -TimeoutSec $CabinetWaitSec | Out-Null

# --- 6) Late stamp: bounce LogDaemon uncleanly, stamp RAMCLEAR, restart LogDaemon ---
Stop-LogDaemonUnclean
Invoke-LogDaemonRamClearStamp -Root $root
Start-LogDaemonServices
Write-Step "Waiting 10s for EventProvider / SAS to consume RAMCLEAR + OS_START"
Start-Sleep -Seconds 10

Write-Step "Done - expect SAS 0x7A (soft meters) and usually 0x18/0x17 (power) on the tester"
exit 0
