"""
Immediate RAM-clear for slot and roulette cabinets (no reboot).

Stops the running game and GoldClub processes, runs the maintenance ramclear
task chain (slot: ``ramclear`` nested; roulette: ``ramclear.d`` dot-source),
ensures official state wipe, restarts services + game, then late-stamps
``LogDaemonRamClear.exe`` in the background (after cabinet ONLINE) so the
UI can report success as soon as the game is back up.

SAS maps FilteredEventLog ``RAMCLEAR`` -> ``MetersResetToZero`` (CBE029 / 0x7A)
only when cabinet devices already exist; stamping before the game registers
the cabinet drops the event. ``OS_START`` (from bouncing LogDaemon) drives
power lost/applied (0x18/0x17). Official ``51-LogDaemon`` is skipped during
the wipe pass; the late stamp runs after the game is up.
"""

from __future__ import annotations

import logging
import os
import subprocess
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from automation.remote_exec import psexec_run, resolve_psexec_path, winrm_run_script
from network.lab_access import FleetAllowlistError, LabCredentialError, require_lab_fleet_ip

logger = logging.getLogger(__name__)

_REMOTE_SCRIPT = r"C:\Windows\Temp\LogInvestigator-RamClear.ps1"
_REMOTE_STATUS = r"C:\Windows\Temp\LogInvestigator-RamClear.status"
_LOCAL_TIMEOUT_SEC = 600
_REMOTE_TIMEOUT_SEC = 600
_SERVICE_STOP_TIMEOUT_SEC = 45

_GAME_PROCESS_NAMES = ("Ruleta", "OneHand", "game-start")
_GOLDCLUB_PROCESS_NAMES = ("Bootstrap", "BiOS2")


class RamClearRunnerMode(str, Enum):
    NESTED = "nested"
    DOT_SOURCE_D = "dot_source_d"


@dataclass(frozen=True, slots=True)
class RamClearPlan:
    game_kind: str  # "slot" | "roulette"
    goldclub_root: Path
    task_folder: Path
    runner_mode: RamClearRunnerMode
    tasks_root: Path
    game_process_names: tuple[str, ...] = _GAME_PROCESS_NAMES


def _path_exists_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _path_exists_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def _first_ramclear_d_folder() -> Path | None:
    # Alegro roulette often uses G:\ as the Goldclub volume root (not G:\Goldclub).
    # C:\Goldclub is commonly a junction to that same volume.
    candidates = (
        Path(r"D:\maintenance\tasks\ramclear.d"),
        Path(r"C:\Goldclub\maintenance\tasks\ramclear.d"),
        Path(r"G:\maintenance\tasks\ramclear.d"),
        Path(r"G:\Goldclub\maintenance\tasks\ramclear.d"),
        Path(r"D:\Goldclub\maintenance\tasks\ramclear.d"),
    )
    for p in candidates:
        if _path_exists_dir(p):
            return p
    return None


def _build_status_helper_powershell() -> str:
    """Milestone logger: stdout + status file for UI polling while WinRM/PsExec buffers."""
    status_path = _REMOTE_STATUS.replace("'", "''")
    return textwrap.dedent(
        f"""
        function Write-RamClearStatus([string]$Message) {{
            Write-Output $Message
            try {{
                Set-Content -LiteralPath '{status_path}' -Value $Message -Encoding UTF8 -Force
            }} catch {{ }}
        }}
        """
    ).strip()


def _build_bounded_service_stop_powershell() -> str:
    """Stop goldclub* services with a hard per-service wait (official 01-StopServices can hang)."""
    timeout = int(_SERVICE_STOP_TIMEOUT_SEC)
    return textwrap.dedent(
        f"""
        Write-RamClearStatus '[START] pre-stop services (bounded)'
        Get-Service -ErrorAction SilentlyContinue | Where-Object {{
            $_.Name -like 'goldclub*'
        }} | ForEach-Object {{
            $svc = $_
            Write-RamClearStatus ('Stopping service: ' + $svc.Name)
            Stop-Service -Name $svc.Name -Force -ErrorAction SilentlyContinue
            $sw = [Diagnostics.Stopwatch]::StartNew()
            while ($svc.Status -ne 'Stopped' -and $sw.Elapsed.TotalSeconds -lt {timeout}) {{
                Start-Sleep -Milliseconds 500
                $svc.Refresh()
            }}
            if ($svc.Status -ne 'Stopped') {{
                Write-RamClearStatus ('[WARN] service still ' + $svc.Status + ': ' + $svc.Name + ' — sc stop + continue')
                & sc.exe stop $svc.Name | Out-Null
            }}
        }}
        Start-Sleep -Seconds 1
        Write-RamClearStatus '[END] pre-stop services (bounded)'
        """
    ).strip()


def _build_ensure_state_wipe_powershell() -> str:
    """Mirror official cleanup targets on every Goldclub root alias.

    Must not blank-wipe all of ``var\\state`` (keeps ConfigureDisplays / ntp /
    SwitchConfigIntelligent / ``var\\state\\ruleta``). After ``ruleta\\var`` clear,
    recreate ``IdleMode.flag`` so official ``51-SetIdleMode`` is not undone.
    """
    return textwrap.dedent(
        r"""
        Write-RamClearStatus '[START] ensure state wipe (official targets only)'
        function Test-GoldclubRoot($root) {
            if (-not (Test-Path -LiteralPath $root -PathType Container)) { return $false }
            foreach ($marker in @('var\state', 'maintenance\tasks', 'ruleta', 'slot')) {
                if (Test-Path -LiteralPath (Join-Path $root $marker)) { return $true }
            }
            return $false
        }
        function Clear-FolderContents($dir) {
            if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return }
            Write-RamClearStatus ('Clearing: ' + $dir)
            Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue | ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        function Ensure-IdleModeFlag($root) {
            $flagDir = Join-Path $root 'ruleta\var\events'
            if (-not (Test-Path -LiteralPath (Join-Path $root 'ruleta'))) { return }
            if (-not (Test-Path -LiteralPath $flagDir -PathType Container)) {
                New-Item -ItemType Directory -Path $flagDir -Force | Out-Null
            }
            $flag = Join-Path $flagDir 'IdleMode.flag'
            Get-Date | Set-Content -LiteralPath $flag -Encoding UTF8
            Write-RamClearStatus ('Restored IdleMode.flag: ' + $flag)
        }
        $roots = @()
        foreach ($cand in @('C:\Goldclub', 'G:\Goldclub', 'D:\Goldclub', 'G:\', 'D:\')) {
            if (Test-GoldclubRoot $cand) { $roots += $cand }
        }
        function Get-VolumeKey($root) {
            $probe = Join-Path $root 'var\state'
            if (-not (Test-Path -LiteralPath $probe)) { $probe = $root }
            try {
                $full = (Resolve-Path -LiteralPath $probe).Path
                $vol = Get-Volume -FilePath $full -ErrorAction Stop
                if ($vol -and $vol.Path) { return ([string]$vol.Path).ToLowerInvariant() }
            } catch { }
            try {
                return (Resolve-Path -LiteralPath $probe).Path.TrimEnd('\').ToLowerInvariant()
            } catch {
                return $root.TrimEnd('\').ToLowerInvariant()
            }
        }
        $uniqueRoots = @()
        $seen = @{}
        foreach ($root in $roots) {
            $key = Get-VolumeKey $root
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            $uniqueRoots += $root
        }
        # Official cleanup.d (+ slot). Never blank-wipe all of var\state.
        $relFolders = @(
            'var\state\GoldClub.Aurum.Services',
            'var\state\goldclub.aurum.services',
            'var\state\hwsubsys',
            'var\state\GoldClub.Logging.LogDaemon',
            'var\state\GoldClub.Logging.LogDaemon.Plugin.FilteredEventLog',
            'var\state\OneHand',
            'var\cache',
            'services\aurum\var',
            'services\logdaemon\var',
            'ruleta\var',
            'ruleta\arhiv',
            'slot\var'
        )
        foreach ($root in $uniqueRoots) {
            Write-RamClearStatus ('Goldclub root: ' + $root)
            foreach ($rel in $relFolders) {
                Clear-FolderContents (Join-Path $root $rel)
            }
            $sasGlob = Join-Path $root 'ruleta\online_sas\*.bin'
            Get-Item -Path $sasGlob -ErrorAction SilentlyContinue | ForEach-Object {
                Write-RamClearStatus ('Removing: ' + $_.FullName)
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            }
            Ensure-IdleModeFlag $root
        }
        Write-RamClearStatus '[END] ensure state wipe (official targets only)'
        """
    ).strip()



def _build_logdaemon_ramclear_powershell() -> str:
    """Late-stamp DEBG.RAMCLEAR after the game/cabinet is online (SAS 0x7A).

    Bounce LogDaemon uncleanly so FilteredEventLog emits ``OS_START`` (power
    0x18/0x17), stamp ``RAMCLEAR`` while LogDaemon is down, then restart it.
    Must run *after* wipe *and* after cabinet devices exist — otherwise
    ``SASControler.ConsumeEvent("RAMCLEAR")`` sees Count=0 and drops 0x7A.
    """
    return textwrap.dedent(
        r"""
        Write-RamClearStatus '[START] late LogDaemonRamClear (soft meters / SAS 0x7A after cabinet online)'
        function Get-LogDaemonServices {
            Get-Service -ErrorAction SilentlyContinue | Where-Object {
                $_.Name -like 'goldclub*' -and $_.Name -match '(?i)logdaemon|logging\.logdaemon'
            }
        }
        function Stop-LogDaemonUnclean {
            Get-LogDaemonServices | ForEach-Object {
                $svc = $_
                Write-RamClearStatus ('Unclean stop LogDaemon: ' + $svc.Name)
                try {
                    $procId = (Get-CimInstance Win32_Service -Filter ("Name='" + $svc.Name.Replace("'","''") + "'") -ErrorAction SilentlyContinue).ProcessId
                    if ($procId -and $procId -gt 0) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
                } catch {}
                & sc.exe stop $svc.Name | Out-Null
            }
            Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.ProcessName -match '(?i)LogDaemon'
            } | ForEach-Object {
                Write-RamClearStatus ('Killing ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 1
        }
        function Wait-CabinetDeviceOnline {
            param([int]$TimeoutSec = 90)
            Write-RamClearStatus ("Waiting up to ${TimeoutSec}s for Aurum cabinet device ONLINE")
            $roots = @()
            if ($goldclubRoot) { $roots += $goldclubRoot }
            $roots += @('C:\Goldclub', 'G:\Goldclub', 'D:\Goldclub')
            $sw = [Diagnostics.Stopwatch]::StartNew()
            while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
                foreach ($root in $roots) {
                    foreach ($rel in @(
                        'var\state\goldclub.aurum.services\GCMessenger\*\DeviceManagerData.xml_*',
                        'var\state\GoldClub.Aurum.Services\GCMessenger\*\DeviceManagerData.xml_*'
                    )) {
                        Get-ChildItem -Path (Join-Path $root $rel) -ErrorAction SilentlyContinue | ForEach-Object {
                            try {
                                $fs = [IO.File]::Open($_.FullName, 'Open', 'Read', 'ReadWrite')
                                try { $t = (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
                                if ($t -match 'DeviceClass="cabinet"' -and $t -match 'Status="ONLINE"') {
                                    Write-RamClearStatus ('Cabinet ONLINE: ' + $_.FullName)
                                    return $true
                                }
                            } catch {}
                        }
                    }
                }
                Start-Sleep -Seconds 2
            }
            Write-RamClearStatus '[WARN] cabinet device not ONLINE - stamping anyway (0x7A may miss)'
            return $false
        }
        Wait-CabinetDeviceOnline -TimeoutSec 90 | Out-Null
        Stop-LogDaemonUnclean
        $candidates = @()
        if ($goldclubRoot) {
            $candidates += (Join-Path $goldclubRoot 'bin\LogDaemonRamClear.exe')
            $hb = Join-Path $goldclubRoot 'services\logdaemon\var\GoldClub.Logging.LogDaemon.Plugin.FilteredEventLog\heartbeat'
            if (Test-Path -LiteralPath $hb) {
                try { (Get-Item -LiteralPath $hb).LastWriteTime = (Get-Date).AddHours(-2) } catch {}
            }
        }
        foreach ($root in @('C:\Goldclub', 'G:\Goldclub', 'D:\Goldclub')) {
            $candidates += (Join-Path $root 'bin\LogDaemonRamClear.exe')
        }
        $exe = $null
        $seen = @{}
        foreach ($c in $candidates) {
            $key = ([string]$c).ToLowerInvariant()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            if (Test-Path -LiteralPath $c -PathType Leaf) { $exe = $c; break }
        }
        if (-not $exe) {
            Write-RamClearStatus '[WARN] LogDaemonRamClear.exe not found - soft meters SAS 0x7A may not fire'
        } else {
            Write-RamClearStatus ('Running: ' + $exe)
            $p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -Wait -PassThru
            Write-RamClearStatus ('LogDaemonRamClear exit=' + $p.ExitCode)
        }
        Get-LogDaemonServices | ForEach-Object {
            Write-RamClearStatus ('Starting LogDaemon: ' + $_.Name)
            Start-Service -Name $_.Name -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 10
        Write-RamClearStatus '[END] late LogDaemonRamClear'
        """
    ).strip()



def _build_late_stamp_detach_powershell() -> str:
    # Launch late LogDaemonRamClear in background so UI returns after game start.
    stamp_inner = (
        _build_status_helper_powershell()
        + "\n$ErrorActionPreference = 'Continue'\n"
        + _build_logdaemon_ramclear_powershell()
        + "\nWrite-RamClearStatus '[END] background soft-meter stamp'\n"
    )
    if "\n'@" in stamp_inner or stamp_inner.strip().startswith("'@"):
        raise ValueError("late-stamp body contains PowerShell here-string terminator")
    template = textwrap.dedent(
        """
        Write-RamClearStatus '[MILESTONE] game up - soft-meter stamp (0x7A) continuing in background'
        $stampFile = 'C:\\Windows\\Temp\\LogInvestigator-RamClear-Stamp.ps1'
        $stampRun = 'C:\\Windows\\Temp\\LogInvestigator-RamClear-Stamp-Run.ps1'
        if (-not (Test-Path -LiteralPath 'C:\\Windows\\Temp' -PathType Container)) {
            $stampFile = Join-Path $env:TEMP 'LogInvestigator-RamClear-Stamp.ps1'
            $stampRun = Join-Path $env:TEMP 'LogInvestigator-RamClear-Stamp-Run.ps1'
        }
        @'
STAMP_INNER_PLACEHOLDER
'@ | Set-Content -LiteralPath $stampFile -Encoding UTF8 -Force
        $q = [char]39
        if ($goldclubRoot) {
            $gcLine = ('$goldclubRoot = ' + $q + ($goldclubRoot -replace [string]$q, ($q + $q)) + $q)
        } else {
            $gcLine = '$goldclubRoot = $null'
        }
        $dot = ('. ' + $q + ($stampFile -replace [string]$q, ($q + $q)) + $q)
        @($gcLine, $dot) | Set-Content -LiteralPath $stampRun -Encoding UTF8 -Force
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stampRun
        ) -WindowStyle Hidden | Out-Null
        Write-RamClearStatus '[END] LogInvestigator RAM Clear'
        """
    )
    return template.replace("STAMP_INNER_PLACEHOLDER", stamp_inner).strip()


def _slot_ramclear_folder(goldclub_root: Path) -> Path | None:
    folder = goldclub_root / "maintenance" / "tasks" / "ramclear"
    if _path_exists_dir(folder):
        return folder
    return None


def _slot_runner_exists(goldclub_root: Path) -> bool:
    runner = goldclub_root / "bin" / "RunManteinanceTasks.1.ps1"
    if _path_exists_file(runner):
        return True
    return _path_exists_file(goldclub_root / "bin" / "RunManteinanceTasks.1")


def _plan_from_slot_goldclub(goldclub_root: Path) -> RamClearPlan | None:
    task_folder = _slot_ramclear_folder(goldclub_root)
    if task_folder is None or not _slot_runner_exists(goldclub_root):
        return None
    return RamClearPlan(
        game_kind="slot",
        goldclub_root=goldclub_root,
        task_folder=task_folder,
        runner_mode=RamClearRunnerMode.NESTED,
        tasks_root=goldclub_root / "maintenance" / "tasks",
    )


def _plan_from_roulette_task_folder(
    task_folder: Path, *, preferred_root: Path | None = None
) -> RamClearPlan:
    tasks_root = task_folder.parent
    goldclub_root = Path(r"C:\Goldclub")
    if preferred_root is not None and _path_exists_dir(preferred_root):
        goldclub_root = preferred_root
    elif _path_exists_dir(goldclub_root):
        pass
    elif _path_exists_dir(Path(r"D:\Goldclub")):
        goldclub_root = Path(r"D:\Goldclub")
    elif _path_exists_dir(Path(r"G:\Goldclub")):
        goldclub_root = Path(r"G:\Goldclub")
    return RamClearPlan(
        game_kind="roulette",
        goldclub_root=goldclub_root,
        task_folder=task_folder,
        runner_mode=RamClearRunnerMode.DOT_SOURCE_D,
        tasks_root=tasks_root,
    )



def _running_game_kind() -> str | None:
    """Best-effort: which game process is running on this machine (slot vs roulette)."""
    if os.name != "nt":
        return None
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            errors="replace",
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lower = (out or "").lower()
    if "ruleta.exe" in lower:
        return "roulette"
    if "onehand.exe" in lower or "game-start.exe" in lower:
        return "slot"
    return None


def resolve_ram_clear_plan() -> RamClearPlan | None:
    """Resolve a local RAM-clear plan from installed slot or roulette layout.

    When both layouts are present, prefer the game that is currently running so
    Tools → RAM Clear stays seamless on dual-install cabinets.
    """
    from network.goldclub_paths import _find_roulette_install_root, _find_slot_install_root

    slot_plan: RamClearPlan | None = None
    roulette_plan: RamClearPlan | None = None

    slot_install = _find_slot_install_root()
    if slot_install is not None:
        parts = [p.lower() for p in slot_install.parts]
        if "goldclub" in parts:
            idx = parts.index("goldclub")
            goldclub_root = Path(*slot_install.parts[: idx + 1])
        else:
            goldclub_root = Path(r"C:\Goldclub")
        slot_plan = _plan_from_slot_goldclub(goldclub_root)

    roulette_install = _find_roulette_install_root()
    if roulette_install is not None:
        task_folder = _first_ramclear_d_folder()
        if task_folder is None:
            for base in (roulette_install, Path(r"C:\Goldclub"), Path(r"D:\Goldclub")):
                candidate = base / "maintenance" / "tasks" / "ramclear.d"
                if _path_exists_dir(candidate):
                    task_folder = candidate
                    break
        if task_folder is not None:
            preferred = None
            if _path_exists_dir(Path(r"C:\Goldclub")):
                preferred = Path(r"C:\Goldclub")
            elif _path_exists_dir(Path(r"D:\Goldclub")):
                preferred = Path(r"D:\Goldclub")
            else:
                preferred = roulette_install
            roulette_plan = _plan_from_roulette_task_folder(
                task_folder, preferred_root=preferred
            )

    # Fallback: maintenance folders alone (cabinet may have tasks without game exe markers).
    if slot_plan is None:
        for gc in (Path(r"C:\Goldclub"), Path(r"G:\Goldclub"), Path(r"D:\Goldclub")):
            if not _path_exists_dir(gc):
                continue
            slot_plan = _plan_from_slot_goldclub(gc)
            if slot_plan is not None:
                break

    if roulette_plan is None:
        task_folder = _first_ramclear_d_folder()
        if task_folder is not None:
            roulette_plan = _plan_from_roulette_task_folder(task_folder)

    running = _running_game_kind()
    if running == "slot" and slot_plan is not None:
        return slot_plan
    if running == "roulette" and roulette_plan is not None:
        return roulette_plan
    if slot_plan is not None and roulette_plan is None:
        return slot_plan
    if roulette_plan is not None and slot_plan is None:
        return roulette_plan
    # Both present and nothing running: prefer slot (nested 51-LogDaemon path).
    return slot_plan or roulette_plan

def _build_post_start_powershell(*, game_kind_expr: str) -> str:
    """Restart goldclub services and auto-start the game from bootstrap.ini.

    Slot: prefer ``Bootstrap.exe`` (bootloader/watchdog). It starts GameBin from
    bootstrap.ini and brings BiOS2 back when OneHand exits with ESC. Starting
    OneHand alone leaves no bootloader, so ESC cannot recover BiOS2.
    """
    return textwrap.dedent(
        f"""
        Write-RamClearStatus '[START] post-start services'
        Get-Service -ErrorAction SilentlyContinue | Where-Object {{
            $_.Name -like 'goldclub*'
        }} | ForEach-Object {{
            Write-RamClearStatus ('Starting service: ' + $_.Name)
            Start-Service -Name $_.Name -ErrorAction SilentlyContinue
        }}
        Start-Sleep -Seconds 3
        Write-RamClearStatus '[END] post-start services'

        Write-RamClearStatus '[START] post-start game'
        $gameKind = {game_kind_expr}
        function Find-BootstrapIni {{
            foreach ($root in @('D:\\', 'C:\\Goldclub', 'G:\\Goldclub')) {{
                if (-not (Test-Path -LiteralPath $root)) {{ continue }}
                $ini = Get-ChildItem -LiteralPath $root -Filter 'bootstrap.ini' -File -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($ini) {{ return $ini.FullName }}
            }}
            return $null
        }}
        function Read-BootstrapValue($iniPath, $key) {{
            if (-not $iniPath -or -not (Test-Path -LiteralPath $iniPath)) {{ return $null }}
            $bootstrapDir = Split-Path -Parent $iniPath
            $line = Get-Content -LiteralPath $iniPath -Encoding Ascii -ErrorAction SilentlyContinue |
                Where-Object {{ $_ -match ('^' + [regex]::Escape($key) + '\\s+') }} |
                Select-Object -First 1
            if (-not $line) {{ return $null }}
            $value = ($line -split '\\s+', 2)[1].Trim()
            return ($value -replace '%:', $bootstrapDir)
        }}

        $started = $false
        $iniPath = Find-BootstrapIni

        if ($gameKind -eq 'slot') {{
            # Bootloader first — keeps BiOS2 recoverable when OneHand exits (ESC).
            foreach ($bootstrap in @(
                'C:\\Goldclub\\Bootstrap.exe',
                'G:\\Goldclub\\Bootstrap.exe',
                'D:\\Goldclub\\Bootstrap.exe'
            )) {{
                if (Test-Path -LiteralPath $bootstrap) {{
                    $wdir = Split-Path -Parent $bootstrap
                    Write-RamClearStatus ('Starting bootloader (Bootstrap): ' + $bootstrap)
                    Start-Process -FilePath $bootstrap -WorkingDirectory $wdir
                    $swOh = [Diagnostics.Stopwatch]::StartNew()
                    while ($swOh.Elapsed.TotalSeconds -lt 45) {{
                        if (Get-Process -Name 'OneHand','game-start' -ErrorAction SilentlyContinue) {{
                            Write-RamClearStatus 'OneHand/game-start started by Bootstrap'
                            $started = $true
                            break
                        }}
                        Start-Sleep -Seconds 1
                    }}
                    if (-not $started) {{
                        Write-RamClearStatus '[WARN] Bootstrap running but game not seen yet - continuing'
                        $started = $true
                    }}
                    break
                }}
            }}
            if (-not $started) {{
                Write-RamClearStatus '[WARN] Bootstrap.exe missing - starting game without bootloader (ESC may not return to BiOS2)'
                if ($iniPath) {{
                    Write-Output ('bootstrap.ini: ' + $iniPath)
                    $gameDir = Read-BootstrapValue $iniPath 'GameDir'
                    $gameBin = Read-BootstrapValue $iniPath 'GameBin'
                    if (-not $gameBin) {{ $gameBin = Read-BootstrapValue $iniPath 'GameBinRun' }}
                    if ($gameBin -and (Test-Path -LiteralPath $gameBin)) {{
                        $gameWdir = if ($gameDir -and (Test-Path -LiteralPath $gameDir)) {{
                            $gameDir
                        }} else {{
                            Split-Path -Parent $gameBin
                        }}
                        Write-RamClearStatus ('Starting game: ' + $gameBin + ' @ ' + $gameWdir)
                        Start-Process -FilePath $gameBin -WorkingDirectory $gameWdir
                        $started = $true
                    }}
                }}
            }}
            if (-not $started) {{
                foreach ($pair in @(
                    @('C:\\Goldclub\\slot\\game-start.exe', 'C:\\Goldclub\\slot'),
                    @('C:\\Goldclub\\slot\\OneHand.exe', 'C:\\Goldclub\\slot'),
                    @('G:\\Goldclub\\slot\\game-start.exe', 'G:\\Goldclub\\slot'),
                    @('G:\\Goldclub\\slot\\OneHand.exe', 'G:\\Goldclub\\slot')
                )) {{
                    if (Test-Path -LiteralPath $pair[0]) {{
                        Write-RamClearStatus ('Starting game (fallback): ' + $pair[0])
                        Start-Process -FilePath $pair[0] -WorkingDirectory $pair[1]
                        $started = $true
                        break
                    }}
                }}
            }}
        }} else {{
            if ($iniPath) {{
                Write-Output ('bootstrap.ini: ' + $iniPath)
                $gameDir = Read-BootstrapValue $iniPath 'GameDir'
                $gameBin = Read-BootstrapValue $iniPath 'GameBin'
                if (-not $gameBin) {{ $gameBin = Read-BootstrapValue $iniPath 'GameBinRun' }}
                if ($gameBin -and (Test-Path -LiteralPath $gameBin)) {{
                    $gameWdir = if ($gameDir -and (Test-Path -LiteralPath $gameDir)) {{
                        $gameDir
                    }} else {{
                        Split-Path -Parent $gameBin
                    }}
                    Write-RamClearStatus ('Starting game: ' + $gameBin + ' @ ' + $gameWdir)
                    Start-Process -FilePath $gameBin -WorkingDirectory $gameWdir
                    $started = $true
                }}
            }}
            if (-not $started -and $gameKind -eq 'roulette') {{
                foreach ($candidate in @(
                    'D:\\ruleta\\Ruleta.exe', 'D:\\ruleta\\ruleta.exe',
                    'C:\\Goldclub\\ruleta\\Ruleta.exe', 'G:\\Goldclub\\ruleta\\Ruleta.exe'
                )) {{
                    if (Test-Path -LiteralPath $candidate) {{
                        $wdir = Split-Path -Parent $candidate
                        Write-RamClearStatus ('Starting game (fallback): ' + $candidate)
                        Start-Process -FilePath $candidate -WorkingDirectory $wdir
                        $started = $true
                        break
                    }}
                }}
            }}
        }}

        if (-not $started) {{
            Write-RamClearStatus '[WARN] Could not auto-start game - start Ruleta or OneHand manually.'
        }} else {{
            Write-RamClearStatus '[END] post-start game'
        }}
        """
    ).strip()



def build_ram_clear_powershell(plan: RamClearPlan) -> str:
    """Build a self-contained PowerShell script for local or remote execution."""
    game_names_ps = ", ".join(f"'{n}'" for n in plan.game_process_names)
    gc_names_ps = ", ".join(f"'{n}'" for n in _GOLDCLUB_PROCESS_NAMES)
    task_folder = str(plan.task_folder).replace("'", "''")
    tasks_root = str(plan.tasks_root).replace("'", "''")
    goldclub_root = str(plan.goldclub_root).replace("'", "''")

    if plan.runner_mode == RamClearRunnerMode.NESTED:
        runner_path = plan.goldclub_root / "bin" / "RunManteinanceTasks.1.ps1"
        if not _path_exists_file(runner_path):
            runner_path = plan.goldclub_root / "bin" / "RunManteinanceTasks.1"
        runner_ps = str(runner_path).replace("'", "''")
        chain_block = textwrap.dedent(
            f"""
            Write-RamClearStatus '[START] ramclear chain (slot nested)'
            if (-not (Test-Path -LiteralPath '{runner_ps}')) {{
                throw "RunManteinanceTasks runner not found: {runner_ps}"
            }}
            # Prefer direct *.ps1 loop so we can skip hang-prone 01-StopServices.ps1.
            $taskFolder = '{task_folder}'
            $VerbosePreference = 'SilentlyContinue'
            $ErrorActionPreference = 'Continue'
            $scripts = @([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)
            if ($scripts.Count -gt 0) {{
                foreach ($item in $scripts) {{
                    $leaf = Split-Path -Leaf $item
                    if ($leaf -ieq '01-StopServices.ps1') {{
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (bounded pre-stop already done)')
                        continue
                    }}
                    # Stamp after ensure-wipe so DEBG.RAMCLEAR is not deleted.
                    if ($leaf -ieq '51-LogDaemon.ps1' -or $leaf -match '(?i)^51-LogDaemon') {{
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (LogDaemonRamClear runs after ensure wipe)')
                        continue
                    }}
                    # Never clear Wibu/licence keys during LI RAM Clear (official BiOS path does).
                    if ($leaf -match '(?i)ClearWibu') {{
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (preserve licences / Wibu keys)')
                        continue
                    }}
                    Write-RamClearStatus ('[START] ' + $leaf)
                    try {{
                        & {{ . $item }}
                        Write-RamClearStatus ('[END] ' + $leaf)
                    }} catch {{
                        Write-RamClearStatus ('[ERROR] ' + $leaf + ': ' + $_.Exception.Message)
                    }}
                }}
            }} else {{
                & '{runner_ps}' -path $taskFolder -nested
            }}
            Write-RamClearStatus '[END] ramclear chain'
            """
        ).strip()
    else:
        chain_block = textwrap.dedent(
            f"""
            Write-RamClearStatus '[START] ramclear chain (roulette dot-source)'
            $taskFolder = '{task_folder}'
            if (-not (Test-Path -LiteralPath $taskFolder -PathType Container)) {{
                throw "RAM-clear task folder not found: $taskFolder"
            }}
            $tasksRoot = '{tasks_root}'
            $libPath = Join-Path $tasksRoot 'lib\\powershell'
            if (Test-Path -LiteralPath $libPath) {{
                $env:PSModulePath = $env:PSModulePath + ';' + $libPath
            }}
            $VerbosePreference = 'SilentlyContinue'
            $ErrorActionPreference = 'Continue'
            foreach ($item in ([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)) {{
                $leaf = Split-Path -Leaf $item
                # Official 01-StopServices uses Stop-Service without -Force and can hang forever.
                if ($leaf -ieq '01-StopServices.ps1') {{
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (bounded pre-stop already done)')
                    continue
                }}
                if ($leaf -ieq '51-LogDaemon.ps1' -or $leaf -match '(?i)^51-LogDaemon') {{
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (LogDaemonRamClear runs after ensure wipe)')
                    continue
                }}
                if ($leaf -match '(?i)ClearWibu') {{
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (preserve licences / Wibu keys)')
                    continue
                }}
                Write-RamClearStatus ('[START] ' + $leaf)
                try {{
                    & {{ . $item }}
                    Write-RamClearStatus ('[END] ' + $leaf)
                }} catch {{
                    Write-RamClearStatus ('[ERROR] ' + $leaf + ': ' + $_.Exception.Message)
                    if ($_.ScriptStackTrace) {{ Write-Output $_.ScriptStackTrace }}
                }}
            }}
            Write-RamClearStatus '[END] ramclear chain'
            """
        ).strip()

    return textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        {_build_status_helper_powershell()}
        Write-RamClearStatus '[START] LogInvestigator RAM Clear'
        Write-RamClearStatus ('game_kind={plan.game_kind} goldclub={goldclub_root} tasks={task_folder}')

        Write-RamClearStatus '[START] pre-stop processes'
        # Stop Bootstrap (bootloader) before OneHand so a force-kill does not reboot.
        $gcNames = @({gc_names_ps})
        foreach ($name in $gcNames) {{
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {{
                Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }}
        }}
        $gameNames = @({game_names_ps})
        foreach ($name in $gameNames) {{
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {{
                Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }}
        }}
        Get-Process -ErrorAction SilentlyContinue | Where-Object {{
            $_.ProcessName -like 'goldclub*'
        }} | ForEach-Object {{
            Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }}
        Write-RamClearStatus '[END] pre-stop processes'

        {_build_bounded_service_stop_powershell()}

        {chain_block}

        {_build_ensure_state_wipe_powershell()}

        {_build_post_start_powershell(game_kind_expr=f"'{plan.game_kind}'")}

        {_build_late_stamp_detach_powershell()}
        """
    ).strip()


def build_remote_detect_and_run_powershell() -> str:
    """
    Remote-only script: detect slot vs roulette on the cabinet, then run RAM clear.
    """
    core = textwrap.dedent(
        r"""
        $ErrorActionPreference = 'Stop'

        function Test-Dir($p) {
            try { return Test-Path -LiteralPath $p -PathType Container } catch { return $false }
        }
        function Test-File($p) {
            try { return Test-Path -LiteralPath $p -PathType Leaf } catch { return $false }
        }

        function Find-SlotInstall {
            $candidates = @(
                'G:\Goldclub\slot', 'C:\Goldclub\slot', 'D:\Goldclub\slot'
            )
            foreach ($root in $candidates) {
                foreach ($rel in @('OneHand.exe', 'bin\OneHand.exe', 'game-start.exe')) {
                    if (Test-File (Join-Path $root $rel)) { return $root }
                }
            }
            return $null
        }

        function Find-RouletteInstall {
            $candidates = @('D:\', 'G:\', 'C:\Goldclub', 'D:\Goldclub', 'G:\Goldclub')
            foreach ($root in $candidates) {
                $ruleta = Join-Path $root 'ruleta'
                if (-not (Test-Dir $ruleta)) { continue }
                foreach ($name in @('Ruleta.exe', 'ruleta.exe')) {
                    if (Test-File (Join-Path $ruleta $name)) { return $root }
                }
            }
            return $null
        }

        function Find-RamClearD {
            foreach ($p in @(
                'D:\maintenance\tasks\ramclear.d',
                'C:\Goldclub\maintenance\tasks\ramclear.d',
                'G:\maintenance\tasks\ramclear.d',
                'G:\Goldclub\maintenance\tasks\ramclear.d',
                'D:\Goldclub\maintenance\tasks\ramclear.d'
            )) {
                if (Test-Dir $p) { return $p }
            }
            return $null
        }

        function Find-SlotRamClear($gc) {
            foreach ($name in @('ramclear', 'ramclear.d')) {
                $p = Join-Path $gc ("maintenance\tasks\" + $name)
                if (Test-Dir $p) { return $p }
            }
            return $null
        }

        $gameKind = $null
        $taskFolder = $null
        $runnerMode = $null
        $tasksRoot = $null
        $goldclubRoot = 'C:\Goldclub'

        function Get-RunningGameKind {
            if (Get-Process -Name 'Ruleta' -ErrorAction SilentlyContinue) { return 'roulette' }
            if (Get-Process -Name 'OneHand','game-start' -ErrorAction SilentlyContinue) { return 'slot' }
            return $null
        }
        $runningKind = Get-RunningGameKind
        $slot = Find-SlotInstall
        $roulette = Find-RouletteInstall
        if ($runningKind -eq 'slot' -and $slot) {
            $roulette = $null
        }
        if ($runningKind -eq 'roulette' -and $roulette) {
            $slot = $null
        }
        if ($slot -and -not $roulette) {
            $gcCandidates = @('C:\Goldclub', 'G:\Goldclub', 'D:\Goldclub')
            if ($slot -like 'G:\*') { $gcCandidates = @('G:\Goldclub', 'C:\Goldclub', 'D:\Goldclub') }
            foreach ($gc in $gcCandidates) {
                $ramclear = Find-SlotRamClear $gc
                if (-not $ramclear) { continue }
                $gameKind = 'slot'
                $taskFolder = $ramclear
                $runnerMode = 'nested'
                $tasksRoot = Join-Path $gc 'maintenance\tasks'
                $goldclubRoot = $gc
                break
            }
        }
        if (-not $gameKind -and $roulette) {
            $ramclearD = Find-RamClearD
            if ($ramclearD) {
                $gameKind = 'roulette'
                $taskFolder = $ramclearD
                $runnerMode = 'dot_source_d'
                $tasksRoot = Split-Path -Parent $ramclearD
                if (Test-Dir 'C:\Goldclub') { $goldclubRoot = 'C:\Goldclub' }
                elseif (Test-Dir 'G:\') { $goldclubRoot = 'G:\' }
                elseif (Test-Dir 'D:\Goldclub') { $goldclubRoot = 'D:\Goldclub' }
                else { $goldclubRoot = $roulette }
            }
        }
        if (-not $gameKind) {
            throw 'No slot or roulette RAM-clear layout found on this cabinet.'
        }

        Write-RamClearStatus ('[DETECT] game_kind=' + $gameKind + ' task_folder=' + $taskFolder)

        $gameNames = @('Ruleta', 'OneHand', 'game-start')
        $gcNames = @('Bootstrap', 'BiOS2')

        Write-RamClearStatus '[START] LogInvestigator RAM Clear (remote)'
        Write-RamClearStatus '[START] pre-stop processes'
        # Stop Bootstrap (bootloader) before OneHand so a force-kill does not reboot.
        foreach ($name in $gcNames) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        foreach ($name in $gameNames) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'goldclub*' } |
            ForEach-Object {
                Write-RamClearStatus ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        Write-RamClearStatus '[END] pre-stop processes'

        __BOUNDED_STOP__

        if ($runnerMode -eq 'nested') {
            Write-RamClearStatus '[START] ramclear chain (slot nested)'
            $VerbosePreference = 'SilentlyContinue'
            $ErrorActionPreference = 'Continue'
            $scripts = @([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)
            if ($scripts.Count -gt 0) {
                foreach ($item in $scripts) {
                    $leaf = Split-Path -Leaf $item
                    if ($leaf -ieq '01-StopServices.ps1') {
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (bounded pre-stop already done)')
                        continue
                    }
                    if ($leaf -ieq '51-LogDaemon.ps1' -or $leaf -match '(?i)^51-LogDaemon') {
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (LogDaemonRamClear runs after ensure wipe)')
                        continue
                    }
                    if ($leaf -match '(?i)ClearWibu') {
                        Write-RamClearStatus ('[SKIP] ' + $leaf + ' (preserve licences / Wibu keys)')
                        continue
                    }
                    Write-RamClearStatus ('[START] ' + $leaf)
                    try {
                        & { . $item }
                        Write-RamClearStatus ('[END] ' + $leaf)
                    } catch {
                        Write-RamClearStatus ('[ERROR] ' + $leaf + ': ' + $_.Exception.Message)
                    }
                }
            } else {
                $runner = Join-Path $goldclubRoot 'bin\RunManteinanceTasks.1.ps1'
                if (-not (Test-File $runner)) { $runner = Join-Path $goldclubRoot 'bin\RunManteinanceTasks.1' }
                if (-not (Test-File $runner)) { throw "RunManteinanceTasks runner not found under $goldclubRoot\bin" }
                & $runner -path $taskFolder -nested
            }
            Write-RamClearStatus '[END] ramclear chain'
        } else {
            Write-RamClearStatus '[START] ramclear chain (roulette dot-source)'
            $libPath = Join-Path $tasksRoot 'lib\powershell'
            if (Test-Dir $libPath) { $env:PSModulePath = $env:PSModulePath + ';' + $libPath }
            $VerbosePreference = 'SilentlyContinue'
            $ErrorActionPreference = 'Continue'
            foreach ($item in ([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)) {
                $leaf = Split-Path -Leaf $item
                if ($leaf -ieq '01-StopServices.ps1') {
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (bounded pre-stop already done)')
                    continue
                }
                if ($leaf -ieq '51-LogDaemon.ps1' -or $leaf -match '(?i)^51-LogDaemon') {
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (LogDaemonRamClear runs after ensure wipe)')
                    continue
                }
                if ($leaf -match '(?i)ClearWibu') {
                    Write-RamClearStatus ('[SKIP] ' + $leaf + ' (preserve licences / Wibu keys)')
                    continue
                }
                Write-RamClearStatus ('[START] ' + $leaf)
                try {
                    & { . $item }
                    Write-RamClearStatus ('[END] ' + $leaf)
                } catch {
                    Write-RamClearStatus ('[ERROR] ' + $leaf + ': ' + $_.Exception.Message)
                    if ($_.ScriptStackTrace) { Write-Output $_.ScriptStackTrace }
                }
            }
            Write-RamClearStatus '[END] ramclear chain'
        }
        """
    ).strip()
    # Inject helpers that must exist before Write-RamClearStatus is used.
    core = (
        _build_status_helper_powershell()
        + "\n\n"
        + core.replace("__BOUNDED_STOP__", _build_bounded_service_stop_powershell())
    )
    ensure_wipe = _build_ensure_state_wipe_powershell()
    post_start = _build_post_start_powershell(game_kind_expr="$gameKind")
    late_stamp = _build_late_stamp_detach_powershell()
    return (
        core
        + "\n\n"
        + ensure_wipe
        + "\n\n"
        + post_start
        + "\n\n"
        + late_stamp
    )



def summarize_ram_clear_output(text: str, *, returncode: int = 0) -> tuple[bool, str]:
    """Turn raw PowerShell output into a short dialog body.

    Maintenance scripts often Import-Module Storage with VerbosePreference on,
    which floods stderr with Exporting-function noise and can yield a
    non-zero exit even when the clear finished. Prefer status markers.
    """
    raw = (text or "").replace("\r\n", "\n")
    lines = [ln.rstrip() for ln in raw.split("\n")]
    useful: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("verbose:"):
            continue
        if "exporting function" in low or "exporting alias" in low:
            continue
        useful.append(s)

    joined = "\n".join(useful)
    completed = "[END] LogInvestigator RAM Clear" in joined
    # Individual maintenance scripts log [ERROR] and continue; only fail when the
    # orchestrator never reached its END marker (WinRM exit != 0 is common anyway).
    hard_error = any(ln.startswith("[ERROR]") for ln in useful) and not completed
    stamp_ok = any(
        "LogDaemonRamClear exit=0" in ln or ln == "[END] LogDaemonRamClear"
        for ln in useful
    )
    ok = (returncode == 0 and not hard_error) or (completed and not hard_error)

    bullets: list[str] = []
    if any("ensure state wipe" in ln for ln in useful):
        bullets.append("State wiped (official targets)")
    bg_stamp = any(
        "soft-meter stamp (0x7A) continuing in background" in ln
        or ln.startswith("[MILESTONE] game up")
        for ln in useful
    )
    if stamp_ok:
        bullets.append("LogDaemonRamClear stamped (soft meters / SAS 0x7A)")
    elif bg_stamp or any("LogDaemonRamClear" in ln for ln in useful):
        bullets.append("Soft-meter stamp (0x7A) started in background")
    if any("post-start services" in ln for ln in useful):
        bullets.append("GoldClub services restarted")
    if any("post-start game" in ln or "Starting game:" in ln for ln in useful):
        bullets.append("Game restarted (OneHand / Ruleta)")

    if ok:
        body_lines = ["RAM Clear completed.", ""]
        if bullets:
            body_lines.extend(("* " + b) for b in bullets)
        else:
            body_lines.append("* Maintenance chain finished")
        body_lines.extend(
            [
                "",
                "SAS soft-meters exception (0x7A) is issued by Aurum when the host link is online.",
            ]
        )
        return True, "\n".join(body_lines)

    tail = useful[-18:] if useful else ["(no output)"]
    head = "RAM Clear failed."
    if returncode not in (0, None) and not completed:
        head = "RAM Clear failed (exit %s)." % (returncode,)
    return False, head + "\n\n" + "\n".join(tail)

def _clip_output(text: str, *, max_chars: int = 4000) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _run_powershell_script(script: str, *, timeout: int) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "RAM Clear requires Windows."
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            **run_kw,
        )
    except subprocess.TimeoutExpired:
        return False, f"RAM Clear timed out after {timeout} seconds."
    except OSError as e:
        return False, str(e)

    combined = "\n".join(part for part in (r.stdout, r.stderr) if part).strip()
    return summarize_ram_clear_output(
        combined or "RAM Clear completed.",
        returncode=int(r.returncode or 0),
    )


def run_ram_clear_local(plan: RamClearPlan | None = None) -> tuple[bool, str]:
    """Execute RAM clear on the local machine (EGM or USB host).

    When *plan* is None, detect slot/roulette layout at runtime (same script as remote).
    """
    if plan is None:
        script = build_remote_detect_and_run_powershell()
    else:
        script = build_ram_clear_powershell(plan)
    return _run_powershell_script(script, timeout=_LOCAL_TIMEOUT_SEC)


def run_ram_clear_remote(
    ip: str,
    *,
    cabinet_label: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Execute RAM clear on a remote cabinet (WinRM preferred, PsExec fallback).

    When Investigator is already running on that cabinet (same IP), run locally.
    """
    try:
        ip = require_lab_fleet_ip(ip)
    except FleetAllowlistError as e:
        return False, str(e)
    if os.name != "nt":
        return False, "Remote RAM Clear requires Windows."

    from network.health_monitor import is_this_host

    if is_this_host(ip):
        return run_ram_clear_local(None)

    label = (cabinet_label or "").strip() or ip
    script_body = build_remote_detect_and_run_powershell()
    remote_unc = f"\\\\{ip}\\c$\\Windows\\Temp\\LogInvestigator-RamClear.ps1"
    status_unc = f"\\\\{ip}\\c$\\Windows\\Temp\\LogInvestigator-RamClear.status"
    try:
        Path(remote_unc).write_text(script_body, encoding="utf-8")
        try:
            Path(status_unc).write_text("uploaded — starting…\n", encoding="utf-8")
        except OSError:
            pass
    except OSError as e:
        return False, f"Could not upload RAM Clear script to {label}: {e}"

    if progress_cb is not None:
        progress_cb(f"Uploaded script to {label}; running via WinRM…")

    # Prefer WinRM (lab TrustedHosts + GOLD-CLUB\\test). PsExec often sits silent for minutes.
    winrm_err: str | None = None
    try:
        result = winrm_run_script(
            ip=ip,
            remote_script_path=_REMOTE_SCRIPT,
            timeout=_REMOTE_TIMEOUT_SEC,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        ok, msg = summarize_ram_clear_output(
            combined or f"RAM Clear completed on {label} (WinRM).",
            returncode=int(result.returncode or 0),
        )
        if ok:
            return True, msg
        winrm_err = _clip_output(
            combined or msg or f"WinRM exited with code {result.returncode}"
        )
    except (FleetAllowlistError, LabCredentialError, FileNotFoundError, OSError, ValueError) as e:
        winrm_err = str(e)
    except subprocess.TimeoutExpired:
        return False, (
            f"Remote RAM Clear timed out on {label} after "
            f"{_REMOTE_TIMEOUT_SEC} seconds (WinRM)."
        )

    if not resolve_psexec_path():
        return (
            False,
            f"WinRM RAM Clear failed on {label} and PsExec.exe is not available.\n\n"
            f"WinRM error:\n{winrm_err}\n\n"
            "On the EGM itself, use Local connection mode — RAM Clear then runs without remoting.",
        )

    if progress_cb is not None:
        progress_cb(f"WinRM failed on {label} — falling back to PsExec…")

    try:
        result = psexec_run(
            ip=ip,
            remote_argv=[
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                _REMOTE_SCRIPT,
            ],
            as_system=True,
            timeout=_REMOTE_TIMEOUT_SEC,
        )
    except FileNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, (
            f"Remote RAM Clear timed out on {label} after "
            f"{_REMOTE_TIMEOUT_SEC} seconds (PsExec)."
        )

    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    ok, msg = summarize_ram_clear_output(
        combined or f"RAM Clear completed on {label} (PsExec).",
        returncode=int(result.returncode or 0),
    )
    if ok:
        return True, msg
    detail = (msg if msg.startswith("RAM Clear failed") else combined) or (
        f"PsExec exited with code {result.returncode}"
    )
    if winrm_err:
        detail = f"{detail}\n\n(WinRM earlier: {winrm_err})"
    return False, _clip_output(detail)


def ram_clear_summary_for_confirm(plan: RamClearPlan) -> str:
    """Human-readable summary for the confirmation dialog."""
    return (
        f"Game type: {plan.game_kind}\n"
        f"Goldclub root: {plan.goldclub_root}\n"
        f"Task folder: {plan.task_folder}\n\n"
        "This will:\n"
        "• Close the running game (Ruleta / OneHand / game-start)\n"
        "• Stop all GoldClub services and related processes\n"
        "• Run the RAM-clear maintenance chain (backup + cleanup)\n"
        "• Ensure wipe of official state targets only (keeps licences / Wibu keys)\n"
        "• Restart GoldClub services; slot starts Bootstrap (bootloader) so ESC returns to BiOS2\n"
        "• After cabinet ONLINE: late-stamp LogDaemonRamClear (SAS soft meters / 0x7A)\n\n"
        "Works for slot and roulette. Licences are preserved (ClearWibuKey is skipped).\n"
        "State wipe cannot be undone easily."
    )
