from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from automation.remote_exec import winrm_run_script

_LAB_USER = "GOLD-CLUB\\test"
_LAB_PASS = "test"


@dataclass(frozen=True, slots=True)
class StagedAgent:
    remote_exe_path: str
    remote_dir: Path
    remote_launcher_path: str


def _repo_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _sha12(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:12]


def build_input_agent_local(*, out_dir: Path) -> Path:
    """
    Build InputAgent.exe locally using the .NET Framework csc (C# 5 compatible).
    """

    root = _repo_root()
    src = root / "cabinet_tools" / "InputAgent" / "InputAgent.cs"
    if not src.is_file():
        raise FileNotFoundError(f"Missing InputAgent source: {src}")
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / "InputAgent.exe"

    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not csc.is_file():
        raise FileNotFoundError(f"csc.exe not found at {csc}")

    cmd = [str(csc), "/nologo", "/target:exe", f"/out:{exe}", str(src)]
    run_kw: dict = {"capture_output": True, "text": True, "timeout": 60}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    r = subprocess.run(cmd, **run_kw)
    if r.returncode != 0 or not exe.is_file():
        raise RuntimeError(f"InputAgent compile failed: rc={r.returncode} out={r.stdout} err={r.stderr}")
    return exe


def _unc_to_remote_local(path: str | Path) -> str:
    """``\\\\host\\c$\\foo`` -> ``C:\\foo`` for commands running on the cabinet."""
    s = str(path).replace("/", "\\")
    marker = "\\c$\\"
    idx = s.lower().find(marker)
    if idx >= 0:
        return "C:\\" + s[idx + len(marker) :]
    return s


def stage_input_agent(
    *,
    ip: str,
    local_exe: Path,
    remote_root: Path = Path(r"\\{ip}\c$\Windows\Temp\investigator_inputagent"),
) -> StagedAgent:
    """
    Copy InputAgent.exe and the WinRM launcher script to the cabinet.
    """

    root = _repo_root()
    launcher_src = root / "automation" / "run_input_agent_interactive.ps1"
    if not launcher_src.is_file():
        raise FileNotFoundError(f"Missing launcher: {launcher_src}")

    sha = _sha12(local_exe)
    remote_dir = Path(str(remote_root).format(ip=ip)) / f"bin-{sha}"
    remote_dir.mkdir(parents=True, exist_ok=True)
    remote_exe = remote_dir / "InputAgent.exe"
    if not remote_exe.is_file():
        remote_exe.write_bytes(local_exe.read_bytes())

    remote_launcher = remote_dir / "run_input_agent_interactive.ps1"
    raw = launcher_src.read_bytes()
    if b"\x00" in raw:
        raw = raw.decode("utf-16").encode("utf-8")
    remote_launcher.write_bytes(raw)

    return StagedAgent(
        remote_exe_path=str(remote_exe),
        remote_dir=remote_dir,
        remote_launcher_path=str(remote_launcher),
    )


def run_input_script_on_cabinet(
    *,
    ip: str,
    agent: StagedAgent,
    script: dict,
    focus_process: str = "OneHand",
    session: int = 1,
    timeout: int = 120,
    transport: str = "winrm",
) -> tuple[bool, str]:
    """
    Run InputAgent on the cabinet via WinRM + schtasks /IT (interactive session).

    PsExec is intentionally not used here (too slow). Fix WinRM if connection fails.
    """

    _ = session
    if transport != "winrm":
        raise ValueError(f"unsupported transport {transport!r}; use winrm")

    script_path = agent.remote_dir / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    out_path = agent.remote_dir / "input_out.jsonl"
    err_path = agent.remote_dir / "input_err.txt"

    r = winrm_run_script(
        ip=ip,
        remote_script_path=_unc_to_remote_local(agent.remote_launcher_path),
        script_args=[
            _unc_to_remote_local(agent.remote_exe_path),
            _unc_to_remote_local(script_path),
            focus_process,
            _unc_to_remote_local(out_path),
            _unc_to_remote_local(err_path),
            str(timeout),
        ],
        timeout=timeout + 15,
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
        return False, f"winrm: {detail}"

    if not out_path.is_file() or out_path.stat().st_size == 0:
        err_tail = ""
        if err_path.is_file():
            err_tail = err_path.read_text(encoding="utf-8", errors="replace")[:500]
        return False, f"no InputAgent output at {out_path}; stderr={err_tail}"

    return True, str(out_path)
