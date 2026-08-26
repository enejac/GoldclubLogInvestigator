from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from automation.remote_exec import winrm_run_script
from network.lab_access import require_lab_fleet_ip


@dataclass(frozen=True, slots=True)
class StagedAgent:
    remote_exe_path: str
    remote_dir: Path
    remote_launcher_path: str


_BIN_NAME_RE = re.compile(r"^bin-([0-9a-f]{12})$", re.I)
_EXE_NAME_RE = re.compile(r"^InputAgent-([0-9a-f]{12})\.exe$", re.I)


def _repo_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _sha12_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _sha12(path: Path) -> str:
    return _sha12_bytes(path.read_bytes())


def input_agent_source_path() -> Path:
    return _repo_root() / "cabinet_tools" / "InputAgent" / "InputAgent.cs"


def input_agent_source_sha12() -> str:
    src = input_agent_source_path()
    if not src.is_file():
        raise FileNotFoundError(f"Missing InputAgent source: {src}")
    return _sha12(src)


def staging_identity(local_exe: Path) -> str:
    """
    Stable remote folder identity for a built agent.

    Must NOT hash the PE bytes: ``csc`` embeds a fresh timestamp each compile, so
    content hashes differ every build even when ``InputAgent.cs`` is unchanged.
    That is what filled ``Windows\\Temp\\investigator_inputagent\\bin-*`` during
    the two-week soak (one new remote dir per cycle).
    """
    m = _EXE_NAME_RE.match(local_exe.name)
    if m:
        return m.group(1).lower()
    try:
        return input_agent_source_sha12()
    except FileNotFoundError:
        # Last resort for ad-hoc binaries outside the repo build path.
        return _sha12(local_exe)


def default_remote_stage_roots(ip: str) -> list[Path]:
    """Preferred Goldclub tmp first; Windows\\Temp only as fallback."""
    return [
        Path(rf"\\{ip}\c$\Goldclub\var\tmp\investigator_inputagent"),
        Path(rf"\\{ip}\c$\Windows\Temp\investigator_inputagent"),
    ]


def build_input_agent_local(*, out_dir: Path) -> Path:
    """
    Build InputAgent.exe locally using the .NET Framework csc (C# 5 compatible).
    """

    src = input_agent_source_path()
    if not src.is_file():
        raise FileNotFoundError(f"Missing InputAgent source: {src}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Unique exe name avoids CS0016 when a previous InputAgent.exe is still mapped.
    # Name is source-sha so staging_identity() stays stable across recompiles.
    sha = _sha12(src)
    exe = out_dir / f"InputAgent-{sha}.exe"
    if exe.is_file() and exe.stat().st_mtime >= src.stat().st_mtime:
        return exe

    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not csc.is_file():
        raise FileNotFoundError(f"csc.exe not found at {csc}")

    # System.Drawing is only in the default response file, and the agent's
    # screenshot step must not depend on that being present.
    cmd = [
        str(csc),
        "/nologo",
        "/target:exe",
        "/r:System.Drawing.dll",
        f"/out:{exe}",
        str(src),
    ]
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


def _rmtree_quiet(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return not path.exists()
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
    except OSError:
        return False
    return False


def prune_stale_input_agent_bins(
    *,
    ip: str,
    keep_sha: str | None = None,
    roots: list[Path] | None = None,
) -> dict[str, int]:
    """
    Delete remote ``bin-*`` folders that are not the active source-sha build.

    Always walks both Goldclub and Windows\\Temp roots so a fallback never
    accumulates again. Returns counts: removed / kept / errors.
    """
    ip = require_lab_fleet_ip(ip)
    keep = (keep_sha or "").lower() or None
    removed = 0
    kept = 0
    errors = 0
    for root in roots or default_remote_stage_roots(ip):
        try:
            if not root.is_dir():
                continue
        except OSError:
            errors += 1
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            errors += 1
            continue
        for child in children:
            try:
                name = child.name
                if not child.is_dir():
                    # Drop leftover scripts / empty heal markers.
                    if _rmtree_quiet(child):
                        removed += 1
                    continue
                m = _BIN_NAME_RE.match(name)
                if m and keep and m.group(1).lower() == keep:
                    kept += 1
                    continue
                if _rmtree_quiet(child):
                    removed += 1
                else:
                    errors += 1
            except OSError:
                errors += 1
    return {"removed": removed, "kept": kept, "errors": errors}


def stage_input_agent(
    *,
    ip: str,
    local_exe: Path,
    remote_root: Path | None = None,
    prune_stale: bool = True,
) -> StagedAgent:
    """
    Copy InputAgent.exe and the WinRM launcher script to the cabinet.

    Prefer ``Goldclub\\var\\tmp``. Remote folder is ``bin-<source-sha>`` so
    recompiles reuse one directory. Stale ``bin-*`` trees are pruned after a
    successful stage (including under ``Windows\\Temp``).
    """
    ip = require_lab_fleet_ip(ip)

    root = _repo_root()
    launcher_src = root / "automation" / "run_input_agent_interactive.ps1"
    if not launcher_src.is_file():
        raise FileNotFoundError(f"Missing launcher: {launcher_src}")

    if remote_root is not None:
        candidates = [remote_root]
    else:
        candidates = default_remote_stage_roots(ip)

    sha = staging_identity(local_exe)
    payload = local_exe.read_bytes()
    raw = launcher_src.read_bytes()
    if b"\x00" in raw:
        raw = raw.decode("utf-16").encode("utf-8")

    last_err: Exception | None = None
    staged: StagedAgent | None = None
    for base in candidates:
        remote_dir = Path(str(base).format(ip=ip)) / f"bin-{sha}"
        try:
            remote_dir.mkdir(parents=True, exist_ok=True)
            remote_exe = remote_dir / "InputAgent.exe"
            # Rewrite when missing or size differs; PE timestamps may change but
            # we stay in the same folder (source-sha identity).
            if not remote_exe.is_file() or remote_exe.stat().st_size != len(payload):
                remote_exe.write_bytes(payload)
            remote_launcher = remote_dir / "run_input_agent_interactive.ps1"
            if (
                not remote_launcher.is_file()
                or remote_launcher.stat().st_size != len(raw)
            ):
                remote_launcher.write_bytes(raw)
            # Identity marker for operators / prune tools.
            (remote_dir / "source_sha12.txt").write_text(sha + "\n", encoding="utf-8")
            staged = StagedAgent(
                remote_exe_path=str(remote_exe),
                remote_dir=remote_dir,
                remote_launcher_path=str(remote_launcher),
            )
            break
        except OSError as exc:
            last_err = exc
            continue

    if staged is None:
        raise OSError(f"stage_input_agent failed for {ip}: {last_err}")

    if prune_stale:
        # Always scrub both known roots so Temp cannot refill from older callers.
        prune_stale_input_agent_bins(ip=ip, keep_sha=sha)

    return staged


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


def capture_client_screenshot(
    *,
    ip: str,
    agent: StagedAgent,
    dest: Path,
    focus_process: str = "godot",
    settle_ms: int = 250,
    steps: list[dict] | None = None,
    timeout: int = 90,
) -> tuple[bool, str]:
    """
    Screenshot the game's client area through InputAgent and copy it back.

    The agent already runs in the interactive session, so this costs a couple of
    seconds where ``network.screen_capture.capture_remote_screen`` costs ~45 s over
    PsExec. Pixels come back in client coordinates, which is the space every mapped
    hitbox is measured in — no cropping or rescaling.

    ``steps`` are run before the capture, which is how a click and the screenshot
    that proves what it did fit in a single remote round trip.
    """
    remote_shot = agent.remote_dir / f"shot-{uuid.uuid4().hex[:10]}.png"

    script_steps: list[dict] = list(steps or [])
    script_steps.append(
        {
            "type": "screenshot",
            "value": f"{focus_process}@{_unc_to_remote_local(remote_shot)}",
            "ms": max(0, settle_ms),
        }
    )
    last_detail = ""
    for attempt in range(3):
        ok, detail = run_input_script_on_cabinet(
            ip=ip,
            agent=agent,
            script={"defaultKeyDelayMs": 30, "steps": script_steps},
            focus_process=focus_process,
            timeout=timeout,
        )
        last_detail = detail
        if not ok:
            continue
        if remote_shot.is_file() and remote_shot.stat().st_size > 0:
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(remote_shot.read_bytes())
            try:
                remote_shot.unlink()
            except OSError:
                pass
            return True, str(dest)
    return False, last_detail or "agent wrote no screenshot"
