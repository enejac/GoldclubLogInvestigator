"""
Remote cabinet time sync (timezone + wall clock).

Roulette Alegro cabinets typically run **GoldClub.NTP** (Meinberg ntpd) with
**W32Time disabled**. Their ``ntp.conf`` often has no external servers (only the
local clock), so ``w32tm /resync`` cannot correct drift. Sync Time therefore:

1. Copies this PC's Windows time zone + re-enables DST
2. Sets the cabinet wall clock from this PC's UTC (``Set-Date``)
3. Restarts ``GoldClub.NTP`` when present; otherwise best-effort ``w32tm``

Prefers WinRM; falls back to PsExec SYSTEM when WinRM is unavailable.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from diag_logging import fleet_timesync_logger
from network.lab_access import (
    FleetAllowlistError,
    LabCredentialError,
    ensure_lab_smb_credential,
    format_lab_lan_unreachable,
    probe_tcp_port,
    require_lab_fleet_ip,
)

logger = logging.getLogger(__name__)

_TS_MAX_OUT = 800
_SYNC_OK_RE = re.compile(
    r"SYNC_OK\s+zone=(?P<zone>.+?)\s+drift_sec=(?P<drift>[-0-9.]+)\s+"
    r"clock_source=(?P<source>\S+)",
    re.IGNORECASE,
)


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
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 15,
    }
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
    if not zid or any(c in zid for c in "<>|&"):
        return None
    return zid


def _ps_escape_single_quoted(zone_id: str) -> str:
    """PowerShell single-quoted literal: embed ``'`` as ``''``."""
    return zone_id.replace("'", "''")


def _powershell_encoded_command(script: str) -> str:
    """UTF-16LE base64 for ``powershell.exe -EncodedCommand`` (avoids cmd quoting bugs)."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _operator_utc_iso() -> str:
    """UTC timestamp this PC considers correct (millisecond precision, Zulu)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _remote_sync_script(zone_id: str, utc_iso: str) -> str:
    """
    Remote body: zone + DST + Set-Date from operator UTC, then GoldClub.NTP or w32time.
    """
    z = _ps_escape_single_quoted(zone_id)
    u = _ps_escape_single_quoted(utc_iso)
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        Set-TimeZone -Id '{z}'
        try {{
            Set-ItemProperty -LiteralPath `
              'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\TimeZoneInformation' `
              -Name 'DynamicDaylightTimeDisabled' -Value 0 -Type DWord -Force
        }} catch {{
            Write-Output 'WARN: could not re-enable DST registry flag (non-admin?)'
        }}
        $target = [DateTimeOffset]::Parse('{u}')
        Set-Date -Date $target.LocalDateTime
        $clockSource = 'Set-Date'
        $gcNtp = Get-Service -Name 'GoldClub.NTP' -ErrorAction SilentlyContinue
        if ($null -ne $gcNtp) {{
            try {{
                Restart-Service -Name 'GoldClub.NTP' -Force -ErrorAction Stop
                $clockSource = 'GoldClub.NTP+Set-Date'
            }} catch {{
                $clockSource = 'Set-Date+GoldClub.NTP-restart-failed'
            }}
        }} else {{
            try {{
                $w32 = Get-Service -Name 'w32time' -ErrorAction SilentlyContinue
                if ($null -ne $w32) {{
                    if ($w32.StartType -eq 'Disabled') {{
                        Set-Service -Name 'w32time' -StartupType Manual -ErrorAction SilentlyContinue
                    }}
                    Restart-Service -Name 'w32time' -Force -ErrorAction SilentlyContinue
                    & w32tm /resync /force | Out-Null
                    $clockSource = 'w32time+Set-Date'
                }}
            }} catch {{ }}
        }}
        Start-Sleep -Milliseconds 200
        $now = [DateTimeOffset]::Now
        $drift = [math]::Round(($now - $target).TotalSeconds, 1)
        $zoneNow = (Get-TimeZone).Id
        Write-Output ("SYNC_OK zone=" + $zoneNow + " drift_sec=" + $drift + " clock_source=" + $clockSource)
        """
    ).strip()


def _remote_set_timezone_script(zone_id: str) -> str:
    """Legacy helper kept for unit tests that inspect timezone-only script fragments."""
    return _remote_sync_script(zone_id, "1970-01-01T00:00:00.000Z")


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


def _parse_sync_ok(stdout: str, stderr: str = "") -> dict[str, str] | None:
    for block in (stdout or "", stderr or ""):
        for raw in block.replace("\r\n", "\n").splitlines():
            m = _SYNC_OK_RE.search(raw.strip())
            if m:
                return m.groupdict()
    return None


def _psexec_run(
    psexec_path: str,
    ip: str,
    remote_argv: list[str],
    *,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    """Run PsExec with lab credentials (workgroup cabinets often lack admin$)."""
    from automation.remote_exec import psexec_run

    result = psexec_run(ip=ip, remote_argv=remote_argv, timeout=timeout)
    return subprocess.CompletedProcess(
        args=[psexec_path, f"\\\\{ip}", *remote_argv],
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


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
    try:
        from automation.remote_exec import resolve_psexec_path

        return resolve_psexec_path()
    except ImportError:
        return None


def _winrm_failure_hint(detail: str) -> str:
    """Actionable hint appended to WinRM errors from Sync Time."""
    low = (detail or "").casefold()
    hints: list[str] = []
    if "0x8009030e" in low or "logon session does not exist" in low:
        hints.append(
            "WinRM Negotiate failed for this logon session. Re-run "
            ".\\Initialize-LabAccess.ps1 -Verify (elevated) on this PC, then retry."
        )
    if "permissiondenied" in low.replace(" ", "") or "registry access is not allowed" in low:
        hints.append(
            "The lab account on the cabinet needs administrator rights for "
            "Set-TimeZone / Set-Date."
        )
    if "trustedhosts" in low:
        hints.append(
            "Add the cabinet IP to WinRM TrustedHosts: "
            ".\\Initialize-LabAccess.ps1 -Verify"
        )
    if not hints:
        return detail
    return detail + "\n\n" + "\n".join(hints)


def _success_message(
    *,
    zone_id: str,
    transport: str,
    sync_meta: dict[str, str] | None,
) -> str:
    parts = [
        f"Time synchronized via {transport} "
        f"(Windows zone: {zone_id})."
    ]
    if sync_meta:
        parts.append(
            f"Remote clock_source={sync_meta.get('source', '?')}; "
            f"post-sync drift~{sync_meta.get('drift', '?')}s "
            f"(zone {sync_meta.get('zone', '?')})."
        )
        if sync_meta.get("zone") and sync_meta["zone"].casefold() != zone_id.casefold():
            parts.append(
                f"Warning: remote zone {sync_meta['zone']!r} differs from "
                f"operator {zone_id!r}."
            )
    parts.append(
        "Roulette cabinets use GoldClub.NTP (W32Time often disabled); "
        "wall clock was set from this PC because ntp.conf may have no upstream servers."
    )
    parts.append(
        "Fleet card drift is refreshed from the cabinet Windows clock (not old log lines)."
    )
    parts.append(
        "If a game Date/Time dialog still shows old values, restart the game so it "
        "reloads Windows settings."
    )
    return "\n\n".join(parts)


def _try_winrm_sync(ip: str, script: str) -> tuple[bool, str, dict[str, str] | None]:
    """Returns (ok, detail, sync_meta). ok False with detail = error for fallback."""
    try:
        from automation.remote_exec import winrm_run_inline
    except ImportError as e:
        return False, f"WinRM helper unavailable: {e}", None

    try:
        result = winrm_run_inline(ip=ip, script=script, timeout=90)
    except (FleetAllowlistError, LabCredentialError, OSError, RuntimeError, ValueError) as e:
        return False, str(e), None
    except Exception as e:  # noqa: BLE001 — surface unexpected WinRM failures for PsExec fallback
        return False, f"{type(e).__name__}: {e}", None

    meta = _parse_sync_ok(result.stdout or "", result.stderr or "")
    if result.returncode == 0 and meta is not None:
        return True, _ts_clip(result.stdout or ""), meta

    detail = _ts_clip(
        (result.stderr or "").strip()
        or (result.stdout or "").strip()
        or f"WinRM exit {result.returncode}"
    )
    return False, _winrm_failure_hint(detail), meta


def _try_psexec_sync(
    ip: str,
    zone_id: str,
    script: str,
) -> tuple[bool, str, dict[str, str] | None]:
    psexec_path = _resolve_psexec_path()
    if not psexec_path:
        return (
            False,
            "PsExec.exe not found in the 'tools' directory. "
            "Please download it from Microsoft Sysinternals "
            "(or use WinRM — preferred on lab cabinets).",
            None,
        )

    enc = _powershell_encoded_command(script)
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
        "force_remote_time_sync: PsExec fallback for ip=%s psexec=%s",
        ip,
        psexec_path,
    )
    try:
        result = _psexec_run(psexec_path, ip, remote_argv, timeout=180)
    except OSError as e:
        return False, str(e), None
    except subprocess.TimeoutExpired as e:
        return False, f"PsExec timed out: {e}", None

    out_s = _ts_clip(result.stdout or "")
    err_s = _ts_clip(result.stderr or "")
    fleet_timesync_logger().info(
        "force_remote_time_sync: PsExec returncode=%s stderr=%r stdout=%r",
        result.returncode,
        err_s,
        out_s,
    )
    meta = _parse_sync_ok(result.stdout or "", result.stderr or "")
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip() or (
            f"Exit code {result.returncode}"
        )
        return False, detail, meta

    if meta is None:
        # Older cabinets / partial script: fall back to tzutil verify only.
        reported = _remote_tzutil_g(psexec_path, ip)
        if reported is not None and reported.casefold() != zone_id.casefold():
            return (
                False,
                "PsExec returned success but the cabinet still reports a different Windows "
                f"time zone.\n\nExpected (this PC): {zone_id}\n"
                f"Remote tzutil /g: {reported}",
                None,
            )
        meta = {"zone": reported or zone_id, "drift": "?", "source": "PsExec"}

    return True, out_s, meta


def _drift_seconds_from_meta(sync_meta: dict[str, str] | None) -> float | None:
    """Parse post-sync wall-clock drift (seconds) from remote SYNC_OK meta."""
    if not sync_meta:
        return None
    raw = (sync_meta.get("drift") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def preflight_remote_time_sync(ip_address: str) -> tuple[bool, str]:
    """
    Quick lab-LAN checks before WinRM/PsExec time sync.

    Returns ``(ok, error_message)``. Does not require internet — only reachability
    to the cabinet on the local subnet.
    """
    try:
        ip = require_lab_fleet_ip(ip_address)
    except FleetAllowlistError as e:
        return False, str(e)

    if os.name != "nt":
        return False, "Sync Time requires Windows (WinRM or PsExec)."

    from network.fleet_scanner import ping_host, probe_smb_port

    ensure_lab_smb_credential(ip)

    ping_ok = ping_host(ip)
    smb_open = probe_smb_port(ip)
    winrm_open = probe_tcp_port(ip, 5985)

    if winrm_open:
        return True, ""

    return False, format_lab_lan_unreachable(
        ip, winrm_open=winrm_open, smb_open=smb_open, ping_ok=ping_ok
    )


def force_remote_time_sync(ip_address: str) -> tuple[bool, str, float | None]:
    """
    Align the cabinet's Windows time zone and wall clock with **this PC**.

    Prefer WinRM; fall back to PsExec. On roulette cabinets, ``Set-Date`` is required
    because GoldClub.NTP often has no external NTP peers and W32Time is disabled.

    Returns ``(success, message_or_error_text, post_sync_drift_seconds)``.
    ``post_sync_drift_seconds`` is remote wall-clock vs operator UTC after Set-Date
    (``None`` on failure). Use it to refresh the fleet UI — do not re-measure from
    log line timestamps, which stay skewed until new logs are written.
    """
    try:
        ip = require_lab_fleet_ip(ip_address)
    except FleetAllowlistError as e:
        fleet_timesync_logger().warning("force_remote_time_sync: rejected (%s)", e)
        return False, str(e), None

    pre_ok, pre_msg = preflight_remote_time_sync(ip)
    if not pre_ok:
        fleet_timesync_logger().warning(
            "force_remote_time_sync: preflight failed ip=%s detail=%s",
            ip,
            _ts_clip(pre_msg),
        )
        return False, pre_msg, None

    if os.name != "nt":
        fleet_timesync_logger().warning("force_remote_time_sync: rejected (not Windows)")
        return False, "Sync Time requires Windows (WinRM or PsExec).", None

    local_zone = _local_windows_timezone_id()
    zone_id = local_zone or _FALLBACK_WINDOWS_TZ_ID
    utc_iso = _operator_utc_iso()
    script = _remote_sync_script(zone_id, utc_iso)
    fleet_timesync_logger().info(
        "force_remote_time_sync: zone_id=%r utc=%s source=%s",
        zone_id,
        utc_iso,
        "local_tzutil_g" if local_zone else "fallback_default",
    )

    ok, detail, meta = _try_winrm_sync(ip, script)
    if ok:
        drift = _drift_seconds_from_meta(meta)
        if drift is None:
            drift = 0.0
        fleet_timesync_logger().info(
            "force_remote_time_sync: WinRM success ip=%s meta=%s drift=%s",
            ip,
            meta,
            drift,
        )
        return (
            True,
            _success_message(zone_id=zone_id, transport="WinRM", sync_meta=meta),
            drift,
        )

    winrm_detail = detail
    fleet_timesync_logger().warning(
        "force_remote_time_sync: WinRM failed ip=%s detail=%s — trying PsExec",
        ip,
        _ts_clip(winrm_detail),
    )

    ok, detail, meta = _try_psexec_sync(ip, zone_id, script)
    if ok:
        drift = _drift_seconds_from_meta(meta)
        if drift is None:
            drift = 0.0
        fleet_timesync_logger().info(
            "force_remote_time_sync: PsExec success ip=%s meta=%s drift=%s",
            ip,
            meta,
            drift,
        )
        return (
            True,
            _success_message(zone_id=zone_id, transport="PsExec", sync_meta=meta),
            drift,
        )

    fleet_timesync_logger().warning(
        "force_remote_time_sync: failure ip=%s winrm=%s psexec=%s",
        ip,
        _ts_clip(winrm_detail),
        _ts_clip(detail),
    )
    return (
        False,
        "Time sync failed.\n\n"
        f"WinRM: {_ts_clip(winrm_detail)}\n\n"
        f"PsExec: {_ts_clip(detail)}\n\n"
        "Note: Sync Time needs the lab LAN (10.0.0.x), not internet. "
        "If WinRM auth failed, run .\\Initialize-LabAccess.ps1 -Verify elevated on this PC.",
        None,
    )
