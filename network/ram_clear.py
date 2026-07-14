"""
Immediate RAM-clear for slot and roulette cabinets.

Stops the running game and GoldClub processes, then runs the maintenance ramclear
task chain (slot: ramclear folder via RunManteinanceTasks; roulette: ramclear.d dot-source).
"""

from __future__ import annotations

import logging
import os
import subprocess
import textwrap
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from automation.remote_exec import psexec_run, resolve_psexec_path

logger = logging.getLogger(__name__)

_LAB_USER = r"GOLD-CLUB\test"
_LAB_PASS = "test"
_REMOTE_SCRIPT = r"C:\Windows\Temp\LogInvestigator-RamClear.ps1"
_LOCAL_TIMEOUT_SEC = 900
_REMOTE_TIMEOUT_SEC = 900

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
    candidates = (
        Path(r"D:\maintenance\tasks\ramclear.d"),
        Path(r"C:\Goldclub\maintenance\tasks\ramclear.d"),
        Path(r"G:\Goldclub\maintenance\tasks\ramclear.d"),
    )
    for p in candidates:
        if _path_exists_dir(p):
            return p
    return None


def _slot_ramclear_folder(goldclub_root: Path) -> Path | None:
    folder = goldclub_root / "maintenance" / "tasks" / "ramclear"
    if _path_exists_dir(folder):
        return folder
    return None


def resolve_ram_clear_plan() -> RamClearPlan | None:
    """Resolve a local RAM-clear plan from installed slot or roulette layout."""
    from network.goldclub_paths import _find_roulette_install_root, _find_slot_install_root

    slot_install = _find_slot_install_root()
    if slot_install is not None:
        parts = [p.lower() for p in slot_install.parts]
        if "goldclub" in parts:
            idx = parts.index("goldclub")
            goldclub_root = Path(*slot_install.parts[: idx + 1])
        else:
            goldclub_root = Path(r"C:\Goldclub")
        task_folder = _slot_ramclear_folder(goldclub_root)
        if task_folder is not None:
            runner = goldclub_root / "bin" / "RunManteinanceTasks.1.ps1"
            if not _path_exists_file(runner):
                runner = goldclub_root / "bin" / "RunManteinanceTasks.1"
            if _path_exists_file(runner):
                return RamClearPlan(
                    game_kind="slot",
                    goldclub_root=goldclub_root,
                    task_folder=task_folder,
                    runner_mode=RamClearRunnerMode.NESTED,
                    tasks_root=goldclub_root / "maintenance" / "tasks",
                )

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
            tasks_root = task_folder.parent
            goldclub_root = Path(r"C:\Goldclub")
            if _path_exists_dir(goldclub_root):
                pass
            elif _path_exists_dir(Path(r"D:\Goldclub")):
                goldclub_root = Path(r"D:\Goldclub")
            else:
                goldclub_root = roulette_install
            return RamClearPlan(
                game_kind="roulette",
                goldclub_root=goldclub_root,
                task_folder=task_folder,
                runner_mode=RamClearRunnerMode.DOT_SOURCE_D,
                tasks_root=tasks_root,
            )

    return None


def _build_post_start_powershell(*, game_kind_expr: str) -> str:
    """Restart goldclub services and auto-start the game from bootstrap.ini."""
    return textwrap.dedent(
        f"""
        Write-Output '[START] post-start services'
        Get-Service -ErrorAction SilentlyContinue | Where-Object {{
            $_.Name -like 'goldclub*'
        }} | ForEach-Object {{
            Write-Output ('Starting service: ' + $_.Name)
            Start-Service -Name $_.Name -ErrorAction SilentlyContinue
        }}
        Start-Sleep -Seconds 3
        Write-Output '[END] post-start services'

        Write-Output '[START] post-start game'
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
                Write-Output ('Starting game: ' + $gameBin + ' @ ' + $gameWdir)
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
                    Write-Output ('Starting game (fallback): ' + $candidate)
                    Start-Process -FilePath $candidate -WorkingDirectory $wdir
                    $started = $true
                    break
                }}
            }}
        }}

        if (-not $started -and $gameKind -eq 'slot') {{
            foreach ($pair in @(
                @('C:\\Goldclub\\slot\\game-start.exe', 'C:\\Goldclub\\slot'),
                @('C:\\Goldclub\\slot\\OneHand.exe', 'C:\\Goldclub\\slot'),
                @('G:\\Goldclub\\slot\\game-start.exe', 'G:\\Goldclub\\slot'),
                @('G:\\Goldclub\\slot\\OneHand.exe', 'G:\\Goldclub\\slot')
            )) {{
                if (Test-Path -LiteralPath $pair[0]) {{
                    Write-Output ('Starting game (fallback): ' + $pair[0])
                    Start-Process -FilePath $pair[0] -WorkingDirectory $pair[1]
                    $started = $true
                    break
                }}
            }}
            if (-not $started) {{
                foreach ($bootstrap in @('C:\\Goldclub\\bootstrap.exe', 'G:\\Goldclub\\bootstrap.exe')) {{
                    if (Test-Path -LiteralPath $bootstrap) {{
                        Write-Output ('Starting bootstrap: ' + $bootstrap)
                        Start-Process -FilePath $bootstrap
                        $started = $true
                        break
                    }}
                }}
            }}
        }}

        if (-not $started) {{
            Write-Output '[WARN] Could not auto-start game — start Ruleta or OneHand manually.'
        }} else {{
            Write-Output '[END] post-start game'
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
            Write-Output '[START] ramclear chain (slot nested)'
            if (-not (Test-Path -LiteralPath '{runner_ps}')) {{
                throw "RunManteinanceTasks runner not found: {runner_ps}"
            }}
            & '{runner_ps}' -path '{task_folder}' -nested
            Write-Output '[END] ramclear chain'
            """
        ).strip()
    else:
        chain_block = textwrap.dedent(
            f"""
            Write-Output '[START] ramclear chain (roulette dot-source)'
            $taskFolder = '{task_folder}'
            if (-not (Test-Path -LiteralPath $taskFolder -PathType Container)) {{
                throw "RAM-clear task folder not found: $taskFolder"
            }}
            $tasksRoot = '{tasks_root}'
            $libPath = Join-Path $tasksRoot 'lib\\powershell'
            if (Test-Path -LiteralPath $libPath) {{
                $env:PSModulePath = $env:PSModulePath + ';' + $libPath
            }}
            $VerbosePreference = 'Continue'
            $ErrorActionPreference = 'Continue'
            foreach ($item in ([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)) {{
                Write-Output ('[START] ' + (Split-Path -Leaf $item))
                try {{
                    & {{ . $item }}
                    Write-Output ('[END] ' + (Split-Path -Leaf $item))
                }} catch {{
                    Write-Output ('[ERROR] ' + (Split-Path -Leaf $item) + ': ' + $_.Exception.Message)
                    if ($_.ScriptStackTrace) {{ Write-Output $_.ScriptStackTrace }}
                }}
            }}
            Write-Output '[END] ramclear chain'
            """
        ).strip()

    return textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        Write-Output '[START] LogInvestigator RAM Clear'
        Write-Output ('game_kind={plan.game_kind} goldclub={goldclub_root} tasks={task_folder}')

        Write-Output '[START] pre-stop processes'
        $gameNames = @({game_names_ps})
        foreach ($name in $gameNames) {{
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {{
                Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }}
        }}
        $gcNames = @({gc_names_ps})
        foreach ($name in $gcNames) {{
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {{
                Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }}
        }}
        Get-Process -ErrorAction SilentlyContinue | Where-Object {{
            $_.ProcessName -like 'goldclub*'
        }} | ForEach-Object {{
            Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }}
        Write-Output '[END] pre-stop processes'

        Write-Output '[START] pre-stop services'
        Get-Service -ErrorAction SilentlyContinue | Where-Object {{
            $_.Name -like 'goldclub*'
        }} | ForEach-Object {{
            Write-Output ('Stopping service: ' + $_.Name)
            Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue
        }}
        Start-Sleep -Seconds 2
        Write-Output '[END] pre-stop services'

        {chain_block}

        {_build_post_start_powershell(game_kind_expr=f"'{plan.game_kind}'")}
        Write-Output '[END] LogInvestigator RAM Clear'
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
                'G:\Goldclub\maintenance\tasks\ramclear.d'
            )) {
                if (Test-Dir $p) { return $p }
            }
            return $null
        }

        $gameKind = $null
        $taskFolder = $null
        $runnerMode = $null
        $tasksRoot = $null
        $goldclubRoot = 'C:\Goldclub'

        $slot = Find-SlotInstall
        $roulette = Find-RouletteInstall
        if ($slot -and -not $roulette) {
            $gc = 'C:\Goldclub'
            if ($slot -like 'G:\*') { $gc = 'G:\Goldclub' }
            $ramclear = Join-Path $gc 'maintenance\tasks\ramclear'
            if (Test-Dir $ramclear) {
                $gameKind = 'slot'
                $taskFolder = $ramclear
                $runnerMode = 'nested'
                $tasksRoot = Join-Path $gc 'maintenance\tasks'
                $goldclubRoot = $gc
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
                elseif (Test-Dir 'D:\Goldclub') { $goldclubRoot = 'D:\Goldclub' }
                else { $goldclubRoot = $roulette }
            }
        }
        if (-not $gameKind) {
            throw 'No slot or roulette RAM-clear layout found on this cabinet.'
        }

        Write-Output ('[DETECT] game_kind=' + $gameKind + ' task_folder=' + $taskFolder)

        $gameNames = @('Ruleta', 'OneHand', 'game-start')
        $gcNames = @('Bootstrap', 'BiOS2')

        Write-Output '[START] LogInvestigator RAM Clear (remote)'
        Write-Output '[START] pre-stop processes'
        foreach ($name in $gameNames) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        foreach ($name in $gcNames) {
            Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'goldclub*' } |
            ForEach-Object {
                Write-Output ('Stopping process: ' + $_.ProcessName + ' pid=' + $_.Id)
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        Write-Output '[END] pre-stop processes'

        Write-Output '[START] pre-stop services'
        Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'goldclub*' } |
            ForEach-Object {
                Write-Output ('Stopping service: ' + $_.Name)
                Stop-Service -Name $_.Name -Force -ErrorAction SilentlyContinue
            }
        Start-Sleep -Seconds 2
        Write-Output '[END] pre-stop services'

        if ($runnerMode -eq 'nested') {
            Write-Output '[START] ramclear chain (slot nested)'
            $runner = Join-Path $goldclubRoot 'bin\RunManteinanceTasks.1.ps1'
            if (-not (Test-File $runner)) { $runner = Join-Path $goldclubRoot 'bin\RunManteinanceTasks.1' }
            if (-not (Test-File $runner)) { throw "RunManteinanceTasks runner not found under $goldclubRoot\bin" }
            & $runner -path $taskFolder -nested
            Write-Output '[END] ramclear chain'
        } else {
            Write-Output '[START] ramclear chain (roulette dot-source)'
            $libPath = Join-Path $tasksRoot 'lib\powershell'
            if (Test-Dir $libPath) { $env:PSModulePath = $env:PSModulePath + ';' + $libPath }
            $VerbosePreference = 'Continue'
            $ErrorActionPreference = 'Continue'
            foreach ($item in ([System.IO.Directory]::GetFiles($taskFolder, '*.ps1') | Sort-Object)) {
                Write-Output ('[START] ' + (Split-Path -Leaf $item))
                try {
                    & { . $item }
                    Write-Output ('[END] ' + (Split-Path -Leaf $item))
                } catch {
                    Write-Output ('[ERROR] ' + (Split-Path -Leaf $item) + ': ' + $_.Exception.Message)
                    if ($_.ScriptStackTrace) { Write-Output $_.ScriptStackTrace }
                }
            }
            Write-Output '[END] ramclear chain'
        }
        """
    ).strip()
    post_start = _build_post_start_powershell(game_kind_expr="$gameKind")
    return core + "\n\n" + post_start + "\nWrite-Output '[END] LogInvestigator RAM Clear (remote)'"


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
    if r.returncode != 0:
        return False, _clip_output(combined or f"PowerShell exited with code {r.returncode}")
    if "[ERROR]" in combined:
        return False, _clip_output(combined)
    return True, _clip_output(combined or "RAM Clear completed.")


def run_ram_clear_local(plan: RamClearPlan) -> tuple[bool, str]:
    """Execute RAM clear on the local machine (EGM or USB host)."""
    script = build_ram_clear_powershell(plan)
    return _run_powershell_script(script, timeout=_LOCAL_TIMEOUT_SEC)


def run_ram_clear_remote(ip: str) -> tuple[bool, str]:
    """Execute RAM clear on a remote cabinet via PsExec (SYSTEM)."""
    ip = (ip or "").strip()
    if not ip:
        return False, "Enter a host IP for remote RAM Clear."
    if os.name != "nt":
        return False, "Remote RAM Clear requires Windows and PsExec."

    if not resolve_psexec_path():
        return (
            False,
            "PsExec.exe not found in the 'tools' directory. "
            "Download Sysinternals PsExec and place psexec.exe there.",
        )

    script_body = build_remote_detect_and_run_powershell()
    remote_unc = f"\\\\{ip}\\c$\\Windows\\Temp\\LogInvestigator-RamClear.ps1"
    try:
        Path(remote_unc).write_text(script_body, encoding="utf-8")
    except OSError as e:
        return False, f"Could not upload RAM Clear script to {ip}: {e}"

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
            username=_LAB_USER,
            password=_LAB_PASS,
            timeout=_REMOTE_TIMEOUT_SEC,
        )
    except FileNotFoundError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, f"Remote RAM Clear timed out after {_REMOTE_TIMEOUT_SEC} seconds."

    combined = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode != 0:
        return False, _clip_output(combined or f"PsExec exited with code {result.returncode}")
    if "[ERROR]" in combined:
        return False, _clip_output(combined)
    return True, _clip_output(combined or "RAM Clear completed on remote host.")


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
        "• Restart GoldClub services and auto-start the game (Ruleta / OneHand)\n\n"
        "State folders may be wiped after backup. This cannot be undone easily."
    )
