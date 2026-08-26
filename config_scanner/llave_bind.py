"""One-time LLAVE bind after ERROR 99 (trial password auto-type on cabinet)."""

from __future__ import annotations

from pathlib import Path

from config_scanner.build_version import scan_target_path
from config_scanner.stack_restart import find_stack_scripts, plan_stack_restart
from roulette_trial import (
    TrialDisplayChallenge,
    find_latest_trial_displayed,
    llave_bind_note_after_clear,
    trial_password_path,
)


def read_llave_challenge(scan_target: str | None) -> TrialDisplayChallenge | None:
    resolved = (scan_target or "").strip()
    if not resolved:
        return None
    return find_latest_trial_displayed(scan_target_path(resolved))


def save_trial_password(scan_target: str | None, password: str) -> Path:
    """Write ``error30.password`` for ``Clear-Error30.ps1 -Auto`` on the cabinet."""
    token = (password or "").strip()
    if not token:
        raise ValueError("Trial password is empty.")
    dest = scan_target_path(scan_target or "")
    path = trial_password_path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    return path


def format_llave_prompt(challenge: TrialDisplayChallenge | None) -> str:
    base = llave_bind_note_after_clear(["cleared"])
    where = (
        "The System ID is on the cabinet roulette screen under LLAVE — "
        "the CODE line (e.g. 0079043296-2962621042). Log Investigator does "
        "not show a QR code or call the licensing server; use your vendor "
        "trial generator with that CODE, then paste the returned password below."
    )
    if challenge is None:
        return (
            f"{base}\n\n{where}\n\n"
            "System ID: not in ruleta log yet — read CODE from the cabinet "
            "screen after stack restart, or paste it in the next box."
        )
    return (
        f"{base}\n\n"
        f"ERROR {challenge.error_code} — System ID for your trial generator:\n"
        f"  {challenge.ui_system_id}\n\n"
        f"{where}"
    )


def format_manual_system_id_prompt() -> str:
    return (
        "Paste the CODE from the cabinet LLAVE screen (under ERROR / LLAVE).\n"
        "Example: 0079043296-2962621042 or 1694097371-6442060005.\n\n"
        "Use that id in your vendor trial tool to obtain the password.\n"
        "Leave blank if you already have the password."
    )


def _clear_error30_script_path(host: str | None) -> str | None:
    if host:
        probes = (
            rf"\\{host}\USB\usb_scripts\roulette\Clear-Error30.ps1",
            rf"\\{host}\USB_Remote\usb_scripts\roulette\Clear-Error30.ps1",
            rf"\\{host}\c$\Windows\Temp\wd_software_swap\Clear-Error30.ps1",
        )
        for unc in probes:
            try:
                if Path(unc).is_file():
                    if unc.startswith(rf"\\{host}\USB"):
                        return r"D:\usb_scripts\roulette\Clear-Error30.ps1"
                    return r"C:\Windows\Temp\wd_software_swap\Clear-Error30.ps1"
            except OSError:
                continue
    scripts = find_stack_scripts()
    if scripts is None:
        return None
    candidate = scripts[0].parent / "Clear-Error30.ps1"
    try:
        if candidate.is_file():
            return str(candidate)
    except OSError:
        pass
    return None


def run_llave_auto_enter(scan_target: str | None) -> tuple[bool, str]:
    """Run ``Clear-Error30.ps1 -Auto`` on the cabinet (types saved password)."""
    from config_scanner.stack_restart import _run_powershell_file, _run_remote_one

    plan = plan_stack_restart(scan_target or "")
    if plan is None:
        return False, "No stack-restart plan for this target (Clear-Error30 needs cabinet access)."
    script = _clear_error30_script_path(plan.host)
    if not script:
        return False, "Clear-Error30.ps1 not found beside Kill-All / on USB."
    if plan.mode == "local":
        return _run_powershell_file(
            script,
            ["-Auto"],
            timeout=120,
            label="Clear-Error30 -Auto",
        )
    host = (plan.host or "").strip()
    if not host:
        return False, "Remote LLAVE auto-enter requires a cabinet host."
    return _run_remote_one(
        host,
        script if script.startswith("D:") else script,
        label="Clear-Error30 -Auto",
        allow_exit_1=False,
        timeout=120,
        script_args=["-Auto"],
    )
