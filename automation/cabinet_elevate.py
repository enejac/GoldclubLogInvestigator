"""Ensure workgroup cabinets can run Kill-All elevated over WinRM (session-0 fix)."""

from __future__ import annotations

import shutil
import textwrap
import time
from pathlib import Path

from app_paths import app_install_dir

_ELEVATE_PS1 = app_install_dir() / "cabinet_tools" / "roulette" / "GoldClubElevate.ps1"
_KILL_ALL_PS1 = app_install_dir() / "cabinet_tools" / "roulette" / "Kill-All.ps1"
_BOOTSTRAP_PS1 = app_install_dir() / "cabinet_tools" / "roulette" / "Bootstrap-Elevate-111.ps1"
_BOOTSTRAP_CMD = app_install_dir() / "cabinet_tools" / "roulette" / "GCI-ELEVATE-NOW.cmd"
_ONSTART_PS1 = (
    app_install_dir() / "cabinet_tools" / "shared" / "onstart.d" / "91-EnableShareAndWinRM.ps1"
)
_REMOTE_KILL = r"D:\usb_scripts\roulette\Kill-All.ps1"
_ELEVATE_TASK = "GoldClub-ElevateOnce"


def _usb_roulette_unc(host: str) -> Path:
    for share in ("USB", "USB_Remote"):
        base = Path(rf"\\{host}\{share}\usb_scripts\roulette")
        try:
            if base.is_dir():
                return base
        except OSError:
            continue
    return Path(rf"\\{host}\USB\usb_scripts\roulette")


def _platform_onstart_unc(host: str) -> Path:
    return Path(
        rf"\\{host}\slot\platform\system\init\onstart.d\91-EnableShareAndWinRM.ps1"
    )


def _usb_root_unc(host: str) -> Path:
    for share in ("USB", "USB_Remote"):
        base = Path(rf"\\{host}\{share}")
        try:
            if base.is_dir():
                return base
        except OSError:
            continue
    return Path(rf"\\{host}\USB")


def push_elevate_scripts(host: str) -> Path:
    """Copy Kill-All + GoldClubElevate (+ onstart patch) to the cabinet USB share."""
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

    ip = require_lab_fleet_ip(host.strip())
    ensure_lab_smb_credential(ip)
    dest = _usb_roulette_unc(ip)
    dest.mkdir(parents=True, exist_ok=True)
    for src in (_ELEVATE_PS1, _KILL_ALL_PS1, _BOOTSTRAP_PS1, _BOOTSTRAP_CMD):
        if src.is_file():
            shutil.copy2(src, dest / src.name)
    # Top-level USB shortcuts (cmd + ps1 must sit together on D:\)
    if _BOOTSTRAP_CMD.is_file():
        try:
            usb_root = _usb_root_unc(ip)
            shutil.copy2(_BOOTSTRAP_CMD, usb_root / _BOOTSTRAP_CMD.name)
            if _BOOTSTRAP_PS1.is_file():
                shutil.copy2(_BOOTSTRAP_PS1, usb_root / _BOOTSTRAP_PS1.name)
        except OSError:
            pass
    onstart_dest = _platform_onstart_unc(ip)
    if _ONSTART_PS1.is_file():
        try:
            onstart_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_ONSTART_PS1, onstart_dest)
        except OSError:
            pass
    return dest


def elevate_task_registered(host: str) -> bool:
    from automation.remote_exec import winrm_run_inline
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

    ip = require_lab_fleet_ip(host.strip())
    ensure_lab_smb_credential(ip)
    script = textwrap.dedent(
        f"""
        $out = cmd /c 'schtasks /Query /TN {_ELEVATE_TASK} /FO LIST 2>&1'
        if ($LASTEXITCODE -eq 0) {{ 'REGISTERED'; exit 0 }}
        if ($out -match 'cannot find the file') {{ 'MISSING'; exit 0 }}
        # Access denied: task may exist but test cannot query it — probe /Run instead.
        $run = cmd /c 'schtasks /Run /TN {_ELEVATE_TASK} 2>&1'
        if ($run -notmatch 'cannot find the file' -and $run -notmatch 'Access is denied') {{
            'REGISTERED'
        }} else {{
            'MISSING'
        }}
        """
    ).strip()
    result = winrm_run_inline(ip=ip, script=script, timeout=45)
    blob = ((result.stdout or "") + (result.stderr or "")).strip()
    return "REGISTERED" in blob


def _try_remote_kill_all_script(host: str, *, timeout: int) -> tuple[bool, str]:
    from automation.remote_exec import winrm_run_script

    result = winrm_run_script(
        ip=host,
        remote_script_path=_REMOTE_KILL,
        script_args=[],
        timeout=timeout,
    )
    blob = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode in (0, None):
        return True, blob.splitlines()[-1] if blob else "Kill-All OK"
    if "Elevated: True" in blob or "Elevated:  True" in blob:
        return True, blob
    tail = blob.splitlines()[-1] if blob else f"exit {result.returncode}"
    return False, tail


def bootstrap_hint(host: str) -> str:
    return (
        f"On {host} run once from Total Commander or GoldClub Admin Shell (elevated): "
        r"D:\GCI-ELEVATE-NOW.cmd or D:\usb_scripts\roulette\GCI-ELEVATE-NOW.cmd "
        "(registers SYSTEM Kill-All for remote restore). Then retry restore."
    )


def reboot_cabinet(host: str, *, wait_seconds: int = 300) -> bool:
    from automation.remote_exec import winrm_probe, winrm_run_inline
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

    ip = require_lab_fleet_ip(host.strip())
    ensure_lab_smb_credential(ip)
    try:
        winrm_run_inline(ip=ip, script="Restart-Computer -Force", timeout=30)
    except Exception:
        pass
    deadline = time.monotonic() + max(wait_seconds, 60)
    time.sleep(20)
    while time.monotonic() < deadline:
        if winrm_probe(ip=ip):
            return True
        time.sleep(8)
    return winrm_probe(ip=ip)


def ensure_cabinet_kill_ready(
    host: str,
    *,
    allow_reboot: bool = False,
    reboot_wait_seconds: int = 300,
) -> tuple[bool, str]:
    """Push USB scripts; ensure remote Kill-All can elevate (bootstrap cmd if needed)."""
    from network.lab_access import require_lab_fleet_ip

    ip = require_lab_fleet_ip(host.strip())
    try:
        push_elevate_scripts(ip)
    except OSError as exc:
        return False, f"Could not push elevate scripts: {exc}"

    if elevate_task_registered(ip):
        return True, "Elevate task ready"

    if allow_reboot:
        if reboot_cabinet(ip, wait_seconds=reboot_wait_seconds) and elevate_task_registered(ip):
            return True, f"Elevate task registered on {ip} after reboot"

    return False, bootstrap_hint(ip)


def run_remote_kill_all_elevated(host: str, *, timeout: int = 200) -> tuple[bool, str]:
    """Stop the roulette stack on a remote cabinet (SYSTEM Kill-All when needed)."""
    from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip
    from network.ruleta_stack_probe import probe_blocking_processes, verify_stack_clear_for_swap

    ip = require_lab_fleet_ip(host.strip())
    ensure_lab_smb_credential(ip)

    ready, ready_detail = ensure_cabinet_kill_ready(ip)
    if not ready:
        return False, ready_detail

    ok, detail = _try_remote_kill_all_script(ip, timeout=timeout)
    if not ok:
        return False, detail

    running = probe_blocking_processes(ip)
    if running:
        names = ", ".join(running)
        return False, f"Kill-All finished but still running: {names}. {bootstrap_hint(ip)}"

    ruleta = Path(rf"\\{ip}\slot\ruleta")
    ok, detail = verify_stack_clear_for_swap(ip, ruleta)
    if not ok:
        return False, detail
    return True, ready_detail if ready_detail else "Kill-All OK"
