"""Clock + trial prep and LLAVE auto-bind for seamless FULL_SOFTWARE restores."""

from __future__ import annotations

import time
from pathlib import Path

from config_scanner.build_version import scan_target_path
from config_scanner.cabinet_secrets import resolve_trial_password
from config_scanner.stack_restart import (
    find_stack_scripts,
    plan_stack_restart,
    unc_host_from_target,
)
from roulette_trial import (
    finance_stamp_mismatches_clock,
    inspect_trial_persistent,
    trial_log_state,
    trial_password_path,
)

# 10.1.8 and 876 both stay up on this calendar day (date-only → noon in the script).
DEFAULT_LAB_CLOCK = "2026-08-10"


def _fix_error30_clock_script(host: str | None) -> str | None:
    if host:
        probes = (
            (rf"\\{host}\USB\usb_scripts\roulette\Fix-Error30Clock.ps1", r"D:\usb_scripts\roulette\Fix-Error30Clock.ps1"),
            (rf"\\{host}\USB_Remote\usb_scripts\roulette\Fix-Error30Clock.ps1", r"D:\usb_scripts\roulette\Fix-Error30Clock.ps1"),
            (rf"\\{host}\c$\Windows\Temp\wd_software_swap\Fix-Error30Clock.ps1", r"C:\Windows\Temp\wd_software_swap\Fix-Error30Clock.ps1"),
        )
        for unc, remote in probes:
            try:
                if Path(unc).is_file():
                    return remote
            except OSError:
                continue
    scripts = find_stack_scripts()
    if scripts is None:
        return None
    candidate = scripts[0].parent / "Fix-Error30Clock.ps1"
    try:
        if candidate.is_file():
            return str(candidate)
    except OSError:
        pass
    return None


def needs_seamless_trial_prep(
    scan_target: str,
    *,
    write_scope: str,
    version_transfer: bool,
) -> bool:
    """True when remote FULL_SOFTWARE should roll clock + clear trial first."""
    if write_scope != "full_software":
        return False
    if not version_transfer:
        return False
    plan = plan_stack_restart(scan_target)
    return plan is not None and plan.mode == "remote"


def prepare_cabinet_for_seamless_transfer(
    scan_target: str,
    *,
    clock: str = DEFAULT_LAB_CLOCK,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
    sync_password: bool = True,
) -> tuple[bool, str]:
    """Stop w32time, set lab clock, wipe trial tokens (no Kill-All / launch)."""
    from config_scanner.stack_restart import _run_powershell_file, _run_remote_elevated

    plan = plan_stack_restart(scan_target)
    if plan is None:
        return False, "No stack plan — trial prep needs cabinet access."
    script = _fix_error30_clock_script(plan.host)
    if not script:
        return False, "Fix-Error30Clock.ps1 not found beside Kill-All / on USB."
    clock_arg = (clock or DEFAULT_LAB_CLOCK).strip()
    args = ["-SkipKill", "-SkipLaunch", "-TargetLocal", clock_arg]
    sync_note = ""
    if sync_password:
        sync_note = sync_trial_password_to_cabinet(
            scan_target,
            machine_serial=machine_serial,
            tool_root=tool_root,
        )
    else:
        sync_note = "skipped trial password sync"
        from roulette_trial import clear_auto_llave_password_files

        cleared_pw = clear_auto_llave_password_files(scan_target)
        if cleared_pw:
            sync_note += f"; removed {len(cleared_pw)} leftover auto-LLAVE password file(s)"
    from roulette_trial import clear_stale_llave_after_software_swap

    dest = scan_target_path(scan_target)
    cleared = clear_stale_llave_after_software_swap(dest)
    clear_note = (
        f"cleared {len(cleared)} trial token(s) via SMB"
        if cleared
        else "trial tokens already clear on share"
    )
    if plan.mode == "local":
        ok, detail = _run_powershell_file(
            script,
            args,
            timeout=120,
            label="Fix-Error30Clock",
        )
    else:
        host = (plan.host or "").strip()
        if not host:
            return False, "Remote trial prep requires a cabinet host."
        ok, detail = _run_remote_elevated(
            host,
            script,
            args,
            label="Fix-Error30Clock",
            timeout=120,
        )
    if sync_note and ok:
        detail = f"{detail}; {sync_note}"
    if ok:
        detail = f"{detail}; {clear_note}"
    elif cleared:
        detail = f"{clear_note}; clock script: {detail}"
        ok = True
    return ok, detail


def sync_trial_password_to_cabinet(
    scan_target: str,
    *,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
) -> str:
    """Write error30.password on the cabinet when secrets/env provide one."""
    from config_scanner.llave_bind import save_trial_password

    plan = plan_stack_restart(scan_target)
    host = plan.host if plan is not None else unc_host_from_target(scan_target)
    password = resolve_trial_password(
        host=host,
        machine_serial=machine_serial,
        tool_root=tool_root,
    )
    if not password:
        return ""
    dest = scan_target_path(scan_target)
    existing = trial_password_path(dest)
    try:
        if existing.is_file() and existing.read_text(encoding="utf-8").strip() == password:
            return "trial password already on cabinet"
    except OSError:
        pass
    path = save_trial_password(scan_target, password)
    return f"synced trial password to {path.name}"


def trial_password_available(
    scan_target: str,
    *,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
) -> bool:
    password = resolve_trial_password(
        host=unc_host_from_target(scan_target),
        machine_serial=machine_serial,
        tool_root=tool_root,
    )
    if password:
        return True
    try:
        path = trial_password_path(scan_target_path(scan_target))
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def ensure_llave_bound_after_start(
    scan_target: str,
    *,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
    wait_seconds: int = 180,
    poll_seconds: float = 5.0,
) -> tuple[bool, str]:
    """Poll trial logs; run Clear-Error30 -Auto when ERROR 99/30 keypad is up."""
    from config_scanner.llave_bind import run_llave_auto_enter

    dest = scan_target_path(scan_target)
    sync_trial_password_to_cabinet(
        scan_target,
        machine_serial=machine_serial,
        tool_root=tool_root,
    )

    report = inspect_trial_persistent(dest)
    if report.activate_bound and trial_log_state(dest) == "accepted":
        return True, "LLAVE already bound (RouletteActivate.dat present)."

    deadline = time.monotonic() + max(wait_seconds, 30)
    last_state = "none"
    auto_attempts = 0
    while time.monotonic() < deadline:
        state = trial_log_state(dest)
        last_state = state
        if state == "accepted":
            return True, "Trial password accepted (log SUCCEEDED)."
        report = inspect_trial_persistent(dest)
        if report.activate_bound and state in {"none", "accepted"}:
            if not finance_stamp_mismatches_clock(dest):
                return True, "Trial bind token present on disk."
        if state == "re-locked":
            return (
                False,
                "Trial re-locked (clock/stamp mismatch or 10.2 beta date lock). "
                "Re-run Fix-Error30Clock before restore.",
            )
        if state == "displayed" and trial_password_available(
            scan_target,
            machine_serial=machine_serial,
            tool_root=tool_root,
        ):
            auto_attempts += 1
            ok, detail = run_llave_auto_enter(scan_target)
            if ok:
                time.sleep(poll_seconds)
                if trial_log_state(dest) == "accepted":
                    return True, f"LLAVE auto-enter OK ({detail})"
                report = inspect_trial_persistent(dest)
                if report.activate_bound:
                    return True, f"LLAVE bind written ({detail})"
            elif auto_attempts >= 2:
                return False, detail
        time.sleep(poll_seconds)

    if last_state == "displayed":
        return (
            False,
            "LLAVE keypad still showing — save trial password in "
            "config-scanner/cabinet_secrets.json or More → Enter trial password.",
        )
    report = inspect_trial_persistent(dest)
    if last_state == "none" and report.activate_bound:
        if trial_log_state(dest) in {"accepted", "none"} and not finance_stamp_mismatches_clock(
            dest
        ):
            return True, "No keypad in logs; activate token present."
        return (
            False,
            "Stale trial bind token on disk (likely from prior Ruleta version). "
            "Re-run trial prep or clear persistent before stack start.",
        )
    return False, f"No LLAVE bind within {wait_seconds}s (state={last_state})."
