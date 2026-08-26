"""
Remote screen capture via PsExec (SYSTEM, interactive session 1) and admin share (``c$``).

Captures the first two monitors from ``AllScreens``; if ``$s[0]`` is physically below ``$s[1]``,
swaps them so the vertical stitch is top-to-bottom (avoids pipeline sort issues). Uses ``-s -i 1``.
**Limitation:** if the live desktop is not session 1 (e.g. RDP), capture may fail or be blank.
Requires ``tools/psexec.exe`` (Sysinternals) and SMB access to ``\\\\IP\\c$``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from network.time_sync import _resolve_psexec_path

logger = logging.getLogger(__name__)

REMOTE_FILENAME = "temp_screenshot.jpg"
REMOTE_ABS = rf"C:\{REMOTE_FILENAME}"
_PSEXEC_TIMEOUT_SEC = 180


def _remote_unc_path(ip: str) -> str:
    return f"\\\\{ip.strip()}\\c$\\{REMOTE_FILENAME}"


def _powershell_capture_script() -> str:
    # Two-monitor vertical stitch + JPEG; optional swap if AllScreens order is bottom-over-top.
    return (
        "Add-Type -AssemblyName System.Drawing; "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$s = [System.Windows.Forms.Screen]::AllScreens; "
        "if ($s.Count -gt 1 -and $s[0].Bounds.Y -gt $s[1].Bounds.Y) { $tmp = $s[0]; $s[0] = $s[1]; $s[1] = $tmp; }; "
        "$w = $s[0].Bounds.Width; "
        "$h = $s[0].Bounds.Height; "
        "if ($s.Count -gt 1) { $w = [Math]::Max($w, $s[1].Bounds.Width); $h += $s[1].Bounds.Height; }; "
        "$bmp = New-Object System.Drawing.Bitmap $w, $h; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($s[0].Bounds.X, $s[0].Bounds.Y, 0, 0, $s[0].Bounds.Size); "
        "if ($s.Count -gt 1) { $g.CopyFromScreen($s[1].Bounds.X, $s[1].Bounds.Y, 0, $s[0].Bounds.Height, $s[1].Bounds.Size); }; "
        "$bmp.Save('C:\\temp_screenshot.jpg', [System.Drawing.Imaging.ImageFormat]::Jpeg); "
        "$g.Dispose(); "
        "$bmp.Dispose()"
    )


def _run_psexec_powershell(ip: str, psexec_path: str, ps_command: str) -> tuple[int, str, str]:
    # -i 1 targets the usual interactive user desktop on a slot cabinet (see module docstring).
    cmd = [
        psexec_path,
        f"\\\\{ip.strip()}",
        "-s",
        "-i",
        "1",
        "-accepteula",
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-Command",
        ps_command,
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": _PSEXEC_TIMEOUT_SEC,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, **run_kw)
    except subprocess.TimeoutExpired:
        return -1, "", "PsExec timed out while running the capture command."
    except OSError as e:
        return -1, "", str(e)
    return r.returncode, r.stdout or "", r.stderr or ""


def _run_psexec_cmd(ip: str, psexec_path: str, inner_cmd: str) -> None:
    cmd = [
        psexec_path,
        f"\\\\{ip.strip()}",
        "-s",
        "-accepteula",
        "cmd.exe",
        "/c",
        inner_cmd,
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 60,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(cmd, **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("Remote cleanup cmd failed for %s: %s", ip, e)


def _delete_remote_file(ip: str, psexec_path: str) -> None:
    unc = _remote_unc_path(ip)
    p = Path(unc)
    try:
        if p.is_file():
            p.unlink()
            return
    except OSError as e:
        logger.debug("UNC delete failed for %s: %s", unc, e)
    _run_psexec_cmd(ip, psexec_path, f"del /f /q {REMOTE_ABS}")


def capture_remote_screen(ip_address: str, local_save_path: str) -> tuple[bool, str]:
    """
    Run a multi-monitor JPEG capture on the remote host (PsExec ``-s -i 1``), copy from ``c$``, then delete remote file.

    Returns ``(True, local_save_path)`` on success, or ``(False, error_message)``.
    """
    ip = (ip_address or "").strip()
    if not ip:
        return False, "No IP address provided."

    if os.name != "nt":
        return False, "Remote screen capture requires Windows and PsExec."

    psexec_path = _resolve_psexec_path()
    if not psexec_path:
        return (
            False,
            "PsExec.exe not found in the 'tools' directory. "
            "Download Sysinternals PsExec and place psexec.exe there.",
        )

    local = Path(local_save_path).expanduser()
    local.parent.mkdir(parents=True, exist_ok=True)

    ps = _powershell_capture_script()
    code, out, err = _run_psexec_powershell(ip, psexec_path, ps)
    if code != 0:
        detail = (err or "").strip() or (out or "").strip() or f"exit code {code}"
        _delete_remote_file(ip, psexec_path)
        return False, f"Remote capture failed: {detail}"

    unc = _remote_unc_path(ip)
    src = Path(unc)
    if not src.is_file():
        _delete_remote_file(ip, psexec_path)
        return (
            False,
            f"Capture ran but file was not found at {unc}. "
            "Check admin share (c$) access and firewall rules.",
        )

    try:
        shutil.copy(src, local)
    except OSError as e:
        return False, f"Could not copy screenshot to local path: {e}"
    finally:
        _delete_remote_file(ip, psexec_path)

    if not local.is_file():
        return False, "Local copy missing after transfer."

    return True, str(local.resolve())
