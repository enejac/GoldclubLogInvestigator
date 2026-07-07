"""
Windows balloon notifications for live FATAL / CRITICAL incidents.
"""

from __future__ import annotations

import subprocess
import sys

from config_manager import SettingsManager
from parser import Incident

_SEV_NOTIFY = frozenset({"FATAL", "CRITICAL"})


def _ps_single_quoted_body(s: str) -> str:
    """Escape for use inside PowerShell single-quoted strings."""
    return s.replace("'", "''").replace("\r", " ").replace("\n", " ")


def show_toast(title: str, message: str) -> None:
    """Show a native Windows tray balloon via a short-lived PowerShell process."""
    if sys.platform != "win32":
        return
    t = _ps_single_quoted_body(title)[:128]
    m = _ps_single_quoted_body(message)[:512]
    script = (
        "[void][reflection.assembly]::loadwithpartialname('System.Windows.Forms'); "
        "$notify = New-Object System.Windows.Forms.NotifyIcon; "
        "$notify.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Get-Process -Id $pid).Path); "
        "$notify.Visible = $true; "
        f"$notify.ShowBalloonTip(10000, '{t}', '{m}', [System.Windows.Forms.ToolTipIcon]::Error); "
        "Start-Sleep -Seconds 11; $notify.Visible = $false; $notify.Dispose()"
    )
    kwargs: dict = {
        "args": [
            "powershell",
            "-NoProfile",
            "-Sta",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[assignment]
    try:
        subprocess.Popen(**kwargs)
    except OSError:
        pass


def should_notify(incident: Incident) -> bool:
    if not SettingsManager.get_notifications_enabled():
        return False
    if incident.severity not in _SEV_NOTIFY:
        return False
    msg = incident.message
    for sub in SettingsManager.get_notification_blacklist():
        if sub and sub in msg:
            return False
    return True


def notification_ignore_fingerprint(incident: Incident) -> str:
    """Stable substring to store when the user ignores notifications for this line."""
    m = incident.message.strip()
    if not m:
        return (incident.error_type or incident.severity or incident.id[:16]).strip()
    return m[:400] if len(m) > 400 else m


def format_live_toast(incident: Incident) -> tuple[str, str]:
    """(title, body) for a live incident toast."""
    title = f"Log Investigator — {incident.severity} (live)"
    parts = [incident.error_type, incident.message[:350]]
    body = " — ".join(p for p in parts if p)
    if len(body) > 480:
        body = body[:477] + "…"
    return title, body
