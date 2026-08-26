"""Kill-All + Run-FullStack after Config Scanner bulk restore (no EGM reboot)."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app_paths import app_install_dir
from config_scanner.build_version import is_unc_path, normalize_scan_target

_PS_ARGS = ("-NoProfile", "-ExecutionPolicy", "Bypass", "-File")
_REMOTE_KILL = r"D:\usb_scripts\roulette\Kill-All.ps1"
_REMOTE_RUN = r"D:\usb_scripts\roulette\Run-FullStack.ps1"


@dataclass(frozen=True)
class StackRestartPlan:
    """How to restart the GoldClub roulette stack."""

    mode: str  # "local" | "remote"
    kill_ps1: str
    run_ps1: str
    host: str | None = None


def running_on_egm() -> bool:
    if sys.platform != "win32":
        return False
    try:
        if Path(r"C:\Goldclub").is_dir():
            return True
    except OSError:
        pass
    name = (os.environ.get("COMPUTERNAME") or "").strip()
    return bool(re.match(r"^(GRT|GST)", name, re.IGNORECASE))


def _hostname_matches(host: str) -> bool:
    token = (host or "").strip().strip(".").casefold()
    if not token:
        return False
    if token in {"localhost", "127.0.0.1", "."}:
        return True
    local = (os.environ.get("COMPUTERNAME") or "").strip().casefold()
    if local and token == local:
        return True
    try:
        fqdn = socket.getfqdn().casefold()
        if fqdn and (token == fqdn or fqdn.startswith(f"{token}.")):
            return True
    except OSError:
        pass
    return False


def unc_host_from_target(target: str) -> str | None:
    cleaned = (target or "").strip()
    match = re.match(r"^\\\\([^\\]+)\\", cleaned, re.IGNORECASE)
    return match.group(1) if match else None


def find_stack_scripts() -> tuple[Path, Path] | None:
    """Resolve Kill-All.ps1 and Run-FullStack.ps1 beside LogInvestigator."""
    root = app_install_dir()
    candidates = (
        root.parent / "usb_scripts" / "roulette",
        root / "usb_scripts" / "roulette",
        root / "scripts" / "roulette",
        root / "cabinet_tools" / "roulette",
    )
    kill: Path | None = None
    run: Path | None = None
    for base in candidates:
        k = base / "Kill-All.ps1"
        r = base / "Run-FullStack.ps1"
        try:
            if kill is None and k.is_file():
                kill = k
            if run is None and r.is_file():
                run = r
        except OSError:
            continue
    if kill is None or run is None:
        return None
    return kill, run


def plan_stack_restart(scan_target: str) -> StackRestartPlan | None:
    """Return a stack-restart plan, or None when scripts or target are unavailable."""
    scripts = find_stack_scripts()
    normalized = normalize_scan_target(scan_target)
    if is_unc_path(normalized):
        host = unc_host_from_target(normalized)
        if not host:
            return None
        if _hostname_matches(host):
            if scripts is None:
                return None
            kill, run = scripts
            return StackRestartPlan(
                mode="local",
                host=None,
                kill_ps1=str(kill),
                run_ps1=str(run),
            )
        kill_path, run_path = _remote_script_paths(host, scripts)
        if scripts is None:
            try:
                usb_run = Path(
                    rf"\\{host}\USB_Remote\usb_scripts\roulette\Run-FullStack.ps1"
                )
                if not usb_run.is_file():
                    usb_run = Path(
                        rf"\\{host}\USB_Remote\ConfigScanner\scripts\roulette\Run-FullStack.ps1"
                    )
                if not usb_run.is_file():
                    return None
            except OSError:
                return None
        return StackRestartPlan(
            mode="remote",
            host=host,
            kill_ps1=kill_path,
            run_ps1=run_path,
        )

    if running_on_egm():
        if scripts is None:
            return None
        kill, run = scripts
        return StackRestartPlan(
            mode="local",
            host=None,
            kill_ps1=str(kill),
            run_ps1=str(run),
        )
    return None


def _remote_script_paths(host: str, scripts: tuple[Path, Path] | None) -> tuple[str, str]:
    probes = (
        (rf"\\{host}\USB_Remote\usb_scripts\roulette", _REMOTE_KILL, _REMOTE_RUN),
        (rf"\\{host}\USB\usb_scripts\roulette", _REMOTE_KILL, _REMOTE_RUN),
        (
            rf"\\{host}\USB_Remote\ConfigScanner\scripts\roulette",
            r"D:\ConfigScanner\scripts\roulette\Kill-All.ps1",
            r"D:\ConfigScanner\scripts\roulette\Run-FullStack.ps1",
        ),
    )
    for unc_dir, kill, run in probes:
        try:
            base = Path(unc_dir)
            if (base / "Kill-All.ps1").is_file() and (base / "Run-FullStack.ps1").is_file():
                return kill, run
        except OSError:
            continue
    if scripts is not None:
        return str(scripts[0]), str(scripts[1])
    return _REMOTE_KILL, _REMOTE_RUN


def stack_restart_message() -> str:
    return (
        "Restart the GoldClub stack (Kill-All + Run-FullStack) so services and "
        "the game reload the restored config — no full EGM reboot needed."
    )


def stack_stop_before_write_text() -> str:
    return (
        "This target looks like a running cabinet: Kill-All always runs "
        "before restore or revert so files can be written. If Auto-start "
        "stack is on, Run-FullStack starts the game afterward (that step "
        "can take several minutes). Offline VHD / lettered disks skip this."
    )


def should_autostart_after_write(
    *,
    auto_start: bool,
    stack_killed: bool,
    has_plan: bool,
) -> bool:
    """Run-FullStack only after a successful Kill-All when the operator wants it."""
    return bool(auto_start and stack_killed and has_plan)


def run_stack_kill(plan: StackRestartPlan) -> tuple[bool, str]:
    """Stop the GoldClub stack so config and DeviceManagerData can be written."""
    if sys.platform != "win32":
        return False, "Stack stop is only supported on Windows."
    if plan.mode == "local":
        ok, detail = _run_powershell_file(
            plan.kill_ps1,
            ["-AlreadyElevated"],
            timeout=180,
            label="Kill-All",
        )
    else:
        host = (plan.host or "").strip()
        if not host:
            return False, "Remote stack stop requires a cabinet host."
        ok, detail = _run_remote_one(
            host,
            plan.kill_ps1,
            label="Kill-All",
            allow_exit_1=True,
            timeout=180,
        )
    if not ok:
        return False, detail
    if plan.mode == "remote":
        return True, detail
    return _verify_stack_stopped_after_kill(detail)


def run_stack_start(
    plan: StackRestartPlan,
    *,
    scan_target: str | None = None,
    ensure_llave: bool = False,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
) -> tuple[bool, str]:
    """Start the GoldClub stack after a restore."""
    if sys.platform != "win32":
        return False, "Stack start is only supported on Windows."
    if plan.mode == "local":
        ok, detail = _run_powershell_file(
            plan.run_ps1,
            ["-AlreadyElevated"],
            timeout=360,
            label="Run-FullStack",
        )
        if not ok:
            up, verify_detail = _wait_for_stack_running(None, wait_seconds=120)
            if up:
                ok = True
                detail = f"Run-FullStack OK ({verify_detail})"
    else:
        host = (plan.host or "").strip()
        if not host:
            return False, "Remote stack start requires a cabinet host."
        ok, detail = _run_remote_one(
            host,
            plan.run_ps1,
            label="Run-FullStack",
            allow_exit_1=False,
            timeout=600,
        )
    if not ok:
        return False, detail
    if ensure_llave and scan_target:
        from config_scanner.cabinet_trial_prep import ensure_llave_bound_after_start

        llave_ok, llave_detail = ensure_llave_bound_after_start(
            scan_target,
            machine_serial=machine_serial,
            tool_root=tool_root,
        )
        if llave_ok:
            return True, f"{detail}; {llave_detail}"
        return False, f"{detail}; LLAVE bind failed: {llave_detail}"
    return True, detail


def run_full_stack_restart(plan: StackRestartPlan) -> tuple[bool, str]:
    """Run Kill-All then Run-FullStack. Returns (ok, detail)."""
    ok, kill_msg = run_stack_kill(plan)
    if not ok:
        return False, kill_msg
    ok, run_msg = run_stack_start(plan)
    if not ok:
        return False, run_msg
    return True, f"{kill_msg}; {run_msg}"


def _run_powershell_file(
    script_path: str,
    args: list[str],
    *,
    timeout: int,
    label: str,
) -> tuple[bool, str]:
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", *_PS_ARGS, script_path, *args],
            **run_kw,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label} timed out after {timeout}s"
    except OSError as exc:
        return False, f"{label}: {exc}"

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    detail = out or err or f"exit {result.returncode}"
    if result.returncode == 0:
        return True, f"{label} OK"
    if label == "Kill-All" and result.returncode == 1:
        return True, f"{label} finished (exit 1, continuing)"
    tail = detail.splitlines()[-1] if detail else f"exit {result.returncode}"
    return False, f"{label} failed: {tail}"


def _verify_stack_stopped_after_kill(kill_detail: str) -> tuple[bool, str]:
    """Post-kill verify for local stack stop (remote Kill-All verifies in cabinet_elevate)."""
    from network.ruleta_stack_probe import (
        force_stop_blocking_processes_local,
        probe_blocking_processes,
    )

    running = probe_blocking_processes(None)
    if not running:
        return True, kill_detail
    forced_ok, forced_msg = force_stop_blocking_processes_local()
    running = probe_blocking_processes(None)
    if running:
        names = ", ".join(running)
        return (
            False,
            f"Kill-All finished but {names} still running"
            + (f" (force stop: {forced_msg})" if not forced_ok else "")
            + ".",
        )
    suffix = " (forced stop)" if forced_ok else ""
    return True, f"{kill_detail}{suffix}"


def _wait_for_stack_running(host: str | None, *, wait_seconds: int = 180) -> tuple[bool, str]:
    """Poll until ruleta (+ godot) are up — Run-FullStack often outlives WinRM."""
    from network.ruleta_stack_probe import verify_stack_running

    deadline = time.monotonic() + max(wait_seconds, 30)
    last = "stack not up yet"
    while time.monotonic() < deadline:
        ok, detail = verify_stack_running(host, require_godot=False)
        last = detail
        if ok:
            ok_godot, detail_godot = verify_stack_running(host, require_godot=True)
            return ok_godot, detail_godot if ok_godot else detail
        time.sleep(5)
    return False, last


def _run_remote_one(
    host: str,
    script_path: str,
    *,
    label: str,
    allow_exit_1: bool,
    timeout: int,
    script_args: list[str] | None = None,
) -> tuple[bool, str]:
    name = Path(script_path).name.casefold()
    if name == "kill-all.ps1" or label == "Kill-All":
        from automation.cabinet_elevate import run_remote_kill_all_elevated

        ok, detail = run_remote_kill_all_elevated(host, timeout=timeout)
        if ok:
            return True, f"{label} OK on {host}"
        if allow_exit_1 and "exit=1" in detail.casefold():
            return True, f"{label} exit=1 on {host} (continuing)"
        return False, detail

    from automation.remote_exec import winrm_run_elevated_script, winrm_run_script
    from automation.winrm_output import meaningful_winrm_detail
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

    ensure_lab_smb_credential(host)
    ip = require_lab_fleet_ip(host)
    extra = list(script_args or [])
    is_llave_auto = name == "clear-error30.ps1" or "Clear-Error30" in label
    # Clear-Error30.ps1 only declares -Auto; -AlreadyElevated breaks parameter binding.
    if is_llave_auto:
        args = extra
    else:
        args = ["-AlreadyElevated", *extra]
    use_elevated = name in {"run-fullstack.ps1", "fix-error30clock.ps1", "clear-error30.ps1"}
    is_stack_start = name == "run-fullstack.ps1" or label == "Run-FullStack"

    try:
        if use_elevated:
            result = winrm_run_elevated_script(
                ip=ip,
                remote_script_path=script_path,
                script_args=args,
                timeout=timeout,
            )
        else:
            result = winrm_run_script(
                ip=ip,
                remote_script_path=script_path,
                script_args=args,
                timeout=timeout,
            )
    except Exception as exc:
        result = None
        winrm_error = str(exc)
    else:
        winrm_error = ""

    detail = ""
    rc: int | None = None
    if result is not None:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        rc = result.returncode

    if is_llave_auto:
        if rc == 0:
            return True, f"{label} OK on {host}"
        if rc == 5:
            return (
                False,
                f"{label} on {host}: trial re-locked after password (clock/stamp).",
            )
        clean = meaningful_winrm_detail(detail, fallback=winrm_error or f"exit {rc}")
        return False, clean or f"{label} failed exit {rc}"

    if name == "fix-error30clock.ps1" or label == "Fix-Error30Clock":
        lowered = detail.casefold()
        if "skiplaunch" in lowered and "set-date" in lowered:
            return True, f"{label} OK on {host} (clock + trial wipe logged)"

    if is_stack_start:
        if "stack healthy" in detail.casefold():
            up, verify_detail = _wait_for_stack_running(host, wait_seconds=30)
            if up:
                return True, f"{label} OK on {host} ({verify_detail})"
            return True, f"{label} OK on {host} (Run-FullStack reported HEALTHY)"
        up, verify_detail = _wait_for_stack_running(host, wait_seconds=240)
        if up:
            return True, f"{label} OK on {host} ({verify_detail})"
        clean = meaningful_winrm_detail(
            detail,
            fallback=winrm_error or verify_detail or f"exit {rc}",
        )
        return False, f"{label} on {host} did not start the stack within 240s.\n{clean}"

    if rc in (0, None):
        return True, f"{label} OK on {host}"
    if allow_exit_1 and rc == 1:
        return True, f"{label} exit=1 on {host} (continuing)"
    clean = meaningful_winrm_detail(detail, fallback=winrm_error or f"exit {rc}")
    return False, clean or f"Remote {label} failed exit {rc}"


def _run_remote_elevated(
    host: str,
    script_path: str,
    script_args: list[str],
    *,
    label: str,
    timeout: int,
) -> tuple[bool, str]:
    """Elevated remote PS1 (Fix-Error30Clock, Clear-Error30 -Auto, …)."""
    return _run_remote_one(
        host,
        script_path,
        label=label,
        allow_exit_1=False,
        timeout=timeout,
        script_args=script_args,
    )
