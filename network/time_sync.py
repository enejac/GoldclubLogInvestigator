"""
Remote cabinet time sync via Microsoft Sysinternals PsExec (SYSTEM, no WMI).

Place ``psexec.exe`` in the project ``tools`` folder (see README / deployment notes).
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
from pathlib import Path

from diag_logging import fleet_timesync_logger

logger = logging.getLogger(__name__)

_TS_MAX_OUT = 800


def _ts_clip(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").strip()
    if len(s) > _TS_MAX_OUT:
        return s[:_TS_MAX_OUT] + "…"
    return s


# Used only if ``tzutil /g`` on this PC fails (e.g. rare non-interactive environments).
_FALLBACK_WINDOWS_TZ_ID = "Central Europe Standard Time"


def _local_windows_timezone_id() -> str | None:
    """
    Windows timezone key from this machine (``tzutil /g``), e.g. ``Central Europe Standard Time``.

    Using the operator PC's zone copies DST rules (summer/winter) to the cabinet; a hardcoded
    zone name alone does not fix DST if the cabinet had DST disabled or a mismatched definition.
    """
    if os.name != "nt":
        return None
    run_kw: dict = {"capture_output": True, "text": True, "timeout": 15}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(["tzutil", "/g"], **run_kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("tzutil /g failed: %s", e)
        return None
    if r.returncode != 0:
        return None
    first = (r.stdout or "").strip().splitlines()
    if not first:
        return None
    zid = first[0].strip().strip('"')
    if not zid or any(c in zid for c in '<>|&'):
        return None
    return zid


def _ps_escape_single_quoted(zone_id: str) -> str:
    """PowerShell single-quoted literal: embed ``'`` as ``''``."""
    return zone_id.replace("'", "''")


def _powershell_encoded_command(script: str) -> str:
    """UTF-16LE base64 for ``powershell.exe -EncodedCommand`` (avoids cmd quoting bugs)."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _remote_set_timezone_script(zone_id: str) -> str:
    z = _ps_escape_single_quoted(zone_id)
    # Set-TimeZone is more reliable than ``tzutil /s`` through PsExec/cmd. Stop on failure so
    # we do not report success when only w32tm ran (old bug: cmd ``&`` ignores tzutil errors).
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"Set-TimeZone -Id '{z}'; "
        "Set-ItemProperty -LiteralPath "
        "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation' "
        "-Name 'DynamicDaylightTimeDisabled' -Value 0 -Type DWord -Force; "
        "Restart-Service w32time -Force -ErrorAction SilentlyContinue; "
        "& w32tm /resync /force; "
        "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    )


def _line_looks_like_windows_tz_id(line: str) -> bool:
    """True if ``line`` is plausibly ``tzutil /g`` output (not PsExec banner / CLIXML)."""
    s = line.strip().strip('"')
    if len(s) < 3 or len(s) > 120:
        return False
    low = s.lower()
    if low.startswith("psexec") or "execute processes remotely" in low:
        return False
    if low.startswith("connecting to") or low.startswith("starting psexesvc"):
        return False
    if low.startswith("copying authentication") or "sysinternals" in low:
        return False
    if s.startswith("<") or s.startswith("#") or s.startswith("<?"):
        return False
    if not any(c.isalpha() for c in s):
        return False
    return True


def _tzutil_zone_from_psexec_output(stdout: str, stderr: str) -> str | None:
    """Pick the timezone line out of PsExec-mingled stdout/stderr."""
    for block in (stdout or "", stderr or ""):
        for raw in block.replace("\r\n", "\n").splitlines():
            if _line_looks_like_windows_tz_id(raw):
                return raw.strip().strip('"')
    return None


def _psexec_run(
    psexec_path: str,
    ip: str,
    remote_argv: list[str],
    *,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        psexec_path,
        f"\\\\{ip}",
        "-s",
        "-accepteula",
        "-nobanner",
        *remote_argv,
    ]
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **run_kw)


def _remote_tzutil_g(psexec_path: str, ip: str) -> str | None:
    """Return first line of ``tzutil /g`` on the remote host, or ``None``."""
    try:
        r = _psexec_run(
            psexec_path,
            ip,
            ["cmd.exe", "/c", "tzutil /g"],
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        fleet_timesync_logger().warning("remote tzutil /g failed: %s", e)
        return None
    if r.returncode != 0:
        return None
    z = _tzutil_zone_from_psexec_output(r.stdout or "", r.stderr or "")
    return z


def _resolve_psexec_path() -> str | None:
    cwd_tool = Path(os.getcwd()) / "tools" / "psexec.exe"
    if cwd_tool.is_file():
        return str(cwd_tool)
    root = Path(__file__).resolve().parents[1]
    pkg_tool = root / "tools" / "psexec.exe"
    if pkg_tool.is_file():
        return str(pkg_tool)
    return None


def force_remote_time_sync(ip_address: str) -> tuple[bool, str]:
    """
    Set the cabinet's Windows time zone to **this PC's** zone (``tzutil /g``), start W32Time,
    and ``w32tm /resync`` on the remote host via PsExec so DST matches the operator machine.

    Returns ``(success, message_or_error_text)``.
    """
    ip = (ip_address or "").strip()
    if not ip:
        fleet_timesync_logger().warning("force_remote_time_sync: rejected (empty IP)")
        return False, "No IP address provided."

    if os.name != "nt":
        fleet_timesync_logger().warning("force_remote_time_sync: rejected (not Windows)")
        return False, "Sync Time requires Windows and PsExec (Sysinternals)."

    psexec_path = _resolve_psexec_path()
    if not psexec_path:
        fleet_timesync_logger().warning(
            "force_remote_time_sync: rejected (PsExec not found in tools/)"
        )
        return (
            False,
            "PsExec.exe not found in the 'tools' directory. "
            "Please download it from Microsoft Sysinternals.",
        )

    local_zone = _local_windows_timezone_id()
    zone_id = local_zone or _FALLBACK_WINDOWS_TZ_ID
    fleet_timesync_logger().info(
        "force_remote_time_sync: target Windows zone_id=%r source=%s",
        zone_id,
        "local_tzutil_g" if local_zone else "fallback_default",
    )
    ps_script = _remote_set_timezone_script(zone_id)
    enc = _powershell_encoded_command(ps_script)
    remote_argv = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        enc,
    ]

    fleet_timesync_logger().info(
        "force_remote_time_sync: PsExec powershell Set-TimeZone for ip=%s psexec=%s",
        ip,
        psexec_path,
    )
    try:
        result = _psexec_run(psexec_path, ip, remote_argv, timeout=180)
    except OSError as e:
        logger.warning("PsExec run failed for %s: %s", ip, e)
        fleet_timesync_logger().warning(
            "force_remote_time_sync: OSError ip=%s err=%s", ip, e
        )
        return False, str(e)
    except subprocess.TimeoutExpired as e:
        fleet_timesync_logger().warning("force_remote_time_sync: timeout ip=%s", ip)
        return False, f"PsExec timed out: {e}"

    out_s = _ts_clip(result.stdout or "")
    err_s = _ts_clip(result.stderr or "")
    fleet_timesync_logger().info(
        "force_remote_time_sync: subprocess returned ip=%s returncode=%s stderr=%r stdout=%r",
        ip,
        result.returncode,
        err_s,
        out_s,
    )

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        out = (result.stdout or "").strip()
        detail = err or out or f"Exit code {result.returncode}"
        fleet_timesync_logger().warning(
            "force_remote_time_sync: failure ip=%s detail=%s", ip, _ts_clip(detail)
        )
        return False, detail

    reported = _remote_tzutil_g(psexec_path, ip)
    fleet_timesync_logger().info(
        "force_remote_time_sync: remote tzutil /g reports %r (expected %r)",
        reported,
        zone_id,
    )
    if reported is not None and reported.casefold() != zone_id.casefold():
        return (
            False,
            "PsExec returned success but the cabinet still reports a different Windows "
            f"time zone.\n\nExpected (this PC): {zone_id}\n"
            f"Remote tzutil /g: {reported}\n\n"
            "If a custom game 'Date/Time' screen still shows the old zone, restart the "
            "game or the machine so it reloads Windows settings.",
        )

    fleet_timesync_logger().info("force_remote_time_sync: success ip=%s", ip)
    msg = (
        "Time and time zone synchronized successfully via PsExec "
        f"(Windows zone: {zone_id}; remote tzutil /g matches)."
    )
    if reported is None:
        msg += (
            "\n\nCould not re-query the remote zone (tzutil /g); confirm in Windows "
            "Settings that the cabinet matches this PC."
        )
    msg += (
        "\n\nIf a game configuration dialog still shows an old time zone, it may cache "
        "values until restart."
    )
    return True, msg
