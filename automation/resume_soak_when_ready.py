"""Wait until cabinet WinRM answers, then resume the two-week soak pack.

  python -m automation.resume_soak_when_ready --ip 10.0.0.90 --until 2026-08-17T07:30:00
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PTR = ROOT / "_tmp_logs" / "two_week_soak_active.txt"
SI = timezone(timedelta(hours=2))


def log(msg: str) -> None:
    line = f"[{datetime.now(SI).strftime('%Y-%m-%d %H:%M:%S %z')}] {msg}"
    print(line, flush=True)
    path = ROOT / "_tmp_logs" / "resume_soak_when_ready.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def active_out_root() -> Path | None:
    if not ACTIVE_PTR.is_file():
        return None
    line = ACTIVE_PTR.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not line:
        return None
    path = Path(line[0].strip())
    return path if path.is_dir() else None


def winrm_ok(ip: str) -> bool:
    script = (
        f"$ErrorActionPreference='Stop'; "
        f". '{ROOT / 'LabAccess.ps1'}'; "
        f"Invoke-LabWinRmCommand -ComputerName {ip} -ScriptBlock {{ hostname }} | Out-Null; "
        f"'OK'"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
            cwd=str(ROOT),
        )
        return r.returncode == 0 and "OK" in (r.stdout or "")
    except Exception:
        return False


def smb_ok(ip: str) -> bool:
    return Path(rf"\\{ip}\c$\Windows").is_dir()


def stage_and_prune(ip: str, out_root: Path) -> None:
    from automation.remote_input_agent import (
        build_input_agent_local,
        input_agent_source_sha12,
        prune_stale_input_agent_bins,
        stage_input_agent,
    )

    exe = build_input_agent_local(out_dir=out_root / "input_agent_build")
    agent = stage_input_agent(ip=ip, local_exe=exe, prune_stale=True)
    sha = input_agent_source_sha12()
    stats = prune_stale_input_agent_bins(ip=ip, keep_sha=sha)
    log(f"staged {agent.remote_dir} sha={sha} prune={stats}")


def start_supervisor(ip: str, until: str, out_root: Path) -> None:
    stop = out_root / "STOP"
    if stop.is_file():
        stop.unlink()
    script = ROOT / "Start-RouletteTwoWeekSoak.ps1"
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Ip",
            ip,
            "-Until",
            until,
            "-OutRoot",
            str(out_root),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ACTIVE_PTR.write_text(str(out_root.resolve()) + "\n", encoding="utf-8")


def start_watchdog(ip: str, until: str) -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "automation.soak_watchdog",
            "--ip",
            ip,
            "--until",
            until,
            "--loop-hours",
            "3",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--until", default="2026-08-17T07:30:00")
    p.add_argument("--poll-sec", type=int, default=30)
    args = p.parse_args(argv)

    out = active_out_root()
    if out is None:
        log("no active soak folder in _tmp_logs/two_week_soak_active.txt")
        return 2

    log(f"watching {args.ip} to resume {out.name} until={args.until}")
    while True:
        smb = smb_ok(args.ip)
        wr = winrm_ok(args.ip) if smb else False
        log(f"probe smb={smb} winrm={wr}")
        if smb and wr:
            try:
                stage_and_prune(args.ip, out)
            except Exception as exc:  # noqa: BLE001
                log(f"stage warn: {exc}")
            start_supervisor(args.ip, args.until, out)
            start_watchdog(args.ip, args.until)
            log("supervisor + watchdog launched; resume helper exiting")
            return 0
        time.sleep(max(5, int(args.poll_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
