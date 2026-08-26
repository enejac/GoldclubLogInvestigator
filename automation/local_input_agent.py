"""
Run InputAgent on **this** machine instead of over WinRM.

The remote path stages the agent on a cabinet and launches it through
``schtasks /IT`` because a service session cannot see the interactive desktop.
Locally none of that applies: the program is already running in the operator's
own session, so the agent is a plain subprocess and a click costs milliseconds
rather than the ~2 s a WinRM round trip does.

Everything else is deliberately identical to :mod:`automation.remote_input_agent`
— same script JSON, same step types, same client-percent coordinates — so a
mapping or verification run does not care which machine it is driving.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

from app_paths import app_tmp_logs_dir
from automation.remote_input_agent import build_input_agent_local

# The roulette client's own window, when one is running here.
DEFAULT_FOCUS = "godot"
# Reserved capture/click title meaning "the primary monitor" (InputAgent.cs).
PRIMARY = "screen"


def agent_dir(*, mkdir: bool = True) -> Path:
    """Working folder for the locally built agent and its per-run files."""
    path = app_tmp_logs_dir() / "inputagent"
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_local_agent() -> Path:
    """Compile InputAgent.exe for this machine (cached by source hash)."""
    return build_input_agent_local(out_dir=agent_dir())


def process_running(name: str) -> bool:
    """Is a process whose image name starts with *name* running in this session?"""
    if not name:
        return False
    stem = name.split(".")[0]
    cmd = ["tasklist", "/FI", f"IMAGENAME eq {stem}.exe", "/NH"]
    kw: dict = {"capture_output": True, "text": True, "timeout": 15}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        res = subprocess.run(cmd, **kw)
    except (OSError, subprocess.SubprocessError):
        return False
    return f"{stem.lower()}.exe" in (res.stdout or "").lower()


def primary_screen_size() -> tuple[int, int]:
    """Primary monitor size in pixels, or (0, 0) off Windows."""
    if sys.platform != "win32":
        return (0, 0)
    import ctypes

    user32 = ctypes.windll.user32
    return (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))


def run_input_script_locally(
    *,
    script: dict,
    focus_process: str | None = DEFAULT_FOCUS,
    timeout: int = 90,
) -> tuple[bool, str]:
    """
    Run one InputAgent script here and return ``(ok, output_path_or_error)``.

    The agent writes one JSON line per step to stdout; that stream is kept in
    ``input_out.jsonl`` so local and remote runs leave the same evidence behind.
    """
    exe = ensure_local_agent()
    work = agent_dir()
    script_path = work / f"script-{uuid.uuid4().hex[:8]}.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    cmd = [str(exe), "--scriptPath", str(script_path)]
    if focus_process:
        cmd += ["--focusProcess", focus_process]
    kw: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        res = subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired:
        return False, f"InputAgent did not finish within {timeout}s"
    except OSError as exc:
        return False, f"could not start InputAgent: {exc}"
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass

    out_path = work / "input_out.jsonl"
    out_path.write_text(res.stdout or "", encoding="utf-8")
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
        return False, detail[:500]
    if res.stderr and res.stderr.strip():
        # A failed step reports on stderr while the run itself exits 0.
        return False, res.stderr.strip()[:500]
    return True, str(out_path)


def capture_local_screenshot(
    *,
    dest: Path,
    focus_process: str | None = DEFAULT_FOCUS,
    settle_ms: int = 250,
    steps: list[dict] | None = None,
    timeout: int = 90,
) -> tuple[bool, str]:
    """
    Screenshot the game's client area, or the primary monitor when it is not running.

    A named window that cannot be found makes InputAgent fall back to the primary
    monitor, which is the behaviour a local run wants: map the game if it is here,
    otherwise map the screen in front of the operator.
    """
    shot = agent_dir() / f"shot-{uuid.uuid4().hex[:10]}.png"
    script_steps: list[dict] = list(steps or [])
    script_steps.append(
        {
            "type": "screenshot",
            "value": f"{focus_process or PRIMARY}@{shot}",
            "ms": max(0, settle_ms),
        }
    )
    ok, detail = run_input_script_locally(
        script={"defaultKeyDelayMs": 30, "steps": script_steps},
        focus_process=focus_process,
        timeout=timeout,
    )
    if not ok:
        return False, detail
    if not shot.is_file() or shot.stat().st_size == 0:
        return False, "agent wrote no screenshot"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(shot.read_bytes())
    try:
        shot.unlink()
    except OSError:
        pass
    return True, str(dest)
