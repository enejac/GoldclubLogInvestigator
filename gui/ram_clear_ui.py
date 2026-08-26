"""Shared RAM Clear confirm copy and target helpers for main + SAS Verify UIs."""

from __future__ import annotations

from network.ram_clear import (
    RamClearPlan,
    ram_clear_summary_for_confirm,
    resolve_ram_clear_plan,
    summarize_ram_clear_output,
)


def cabinet_label_for_ip(ip: str) -> str:
    """Human label for a fleet IP (serial/DNS when available)."""
    ip = (ip or "").strip()
    if not ip:
        return "this EGM"
    try:
        from database.format_label import format_machine_label
        from network.fleet_scanner import is_resolved_cabinet_name, resolve_cabinet_name

        live = resolve_cabinet_name(ip)
        if is_resolved_cabinet_name(live):
            return format_machine_label(live, ip)
    except Exception:  # noqa: BLE001
        pass
    return ip


def remote_ram_clear_confirm_text(cabinet_label: str) -> str:
    label = (cabinet_label or "").strip() or "cabinet"
    return (
        f"Remote cabinet: {label}\n\n"
        "Detects slot vs roulette (prefers the running game), then:\n"
        "• Close Bootstrap/BiOS2 first (slot bootloader), then the game\n"
        "• Stop all GoldClub services and related processes\n"
        "• Run the RAM-clear maintenance chain (backup + cleanup)\n"
        "• Ensure wipe of official state targets only\n"
        "• Restart GoldClub services; slot starts Bootstrap (bootloader) so ESC returns to BiOS2\n"
        "• Soft-meter stamp (LogDaemonRamClear / 0x7A) continues in background\n\n"
        "No reboot required. Licences / Wibu keys are preserved.\n"
        "State wipe cannot be undone easily."
    )


def local_ram_clear_confirm_text(plan: RamClearPlan | None) -> str:
    if plan is not None:
        return ram_clear_summary_for_confirm(plan)
    return (
        "Local machine (this EGM)\n\n"
        "Scans for slot or roulette layout (prefers the running game), then:\n"
        "• Close the running game (Ruleta / OneHand / game-start)\n"
        "• Stop Bootstrap/BiOS2 first (bootloader), then the game\n"
        "• Stop all GoldClub services and related processes\n"
        "• Run the RAM-clear maintenance chain (backup + cleanup)\n"
        "• Ensure wipe of official state targets only\n"
        "• Restart GoldClub services; slot starts Bootstrap (bootloader) so ESC returns to BiOS2\n"
        "• Soft-meter stamp (LogDaemonRamClear / 0x7A) continues in background\n\n"
        "No reboot required. State wipe cannot be undone easily."
    )


def ram_clear_confirm_text(
    *,
    remote: bool,
    cabinet_label: str,
    plan: RamClearPlan | None,
) -> str:
    if remote:
        return remote_ram_clear_confirm_text(cabinet_label)
    return local_ram_clear_confirm_text(plan)


def resolve_ram_clear_run(
    *,
    ui_remote: bool,
    target_ip: str | None,
) -> tuple[bool, str, RamClearPlan | None, str]:
    """Return ``(remote, ip, plan, cabinet_label)`` for schedule_ram_clear."""
    from network.app_runtime import wants_remote_operations

    tip = (target_ip or "").strip()
    remote = wants_remote_operations(ui_remote=ui_remote, target_ip=tip)
    ip = tip if remote else ""
    plan = None if remote else resolve_ram_clear_plan()
    if remote and ip:
        label = cabinet_label_for_ip(ip)
    elif plan is not None:
        label = plan.game_kind
    else:
        label = "this EGM"
    return remote, ip, plan, label


def format_ram_clear_finished_dialog(
    *,
    ok: bool,
    msg: str,
    label: str = "",
) -> tuple[bool, str]:
    """Short success/failure body for the completion message box (no log dump)."""
    text = (msg or "").strip()
    # Worker may already have summarized; never turn a completed clear into a red X.
    if text.startswith("RAM Clear completed"):
        display_ok, body = True, text
    elif text.startswith("RAM Clear failed") and "Exporting function" not in text:
        if "[END] LogInvestigator RAM Clear" in text:
            display_ok, body = summarize_ram_clear_output(text, returncode=1)
        else:
            display_ok, body = False, text
    else:
        display_ok, body = summarize_ram_clear_output(text, returncode=0 if ok else 1)
    lab = (label or "").strip()
    if lab:
        body = f"{lab}\n\n{body}"
    return display_ok, body