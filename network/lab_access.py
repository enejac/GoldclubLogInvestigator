"""Lab fleet allowlist and Credential Manager access (no embedded passwords)."""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

LAB_FLEET_IPS: frozenset[str] = frozenset(
    {
        "10.0.0.83",
        "10.0.0.90",
        "10.0.0.100",
        "10.0.0.110",
        "10.0.0.111",
        "10.0.0.112",
        "10.0.0.171",
    }
)

# Roulette-only lab cabinets (no ``ruleta`` in a generic ``…\\var`` scan path).
LAB_ROULETTE_IPS: frozenset[str] = frozenset({"10.0.0.111"})

LAB_USERNAME_HINT = r"GOLD-CLUB\test"

# Workgroup cabinets have no GOLD-CLUB domain account. WinRM/SMB must use
# the local ``test`` user (IP\test or machine\test), not GOLD-CLUB\test.
LAB_WORKGROUP_USERS: dict[str, tuple[str, ...]] = {
    "10.0.0.111": (r"10.0.0.111\test", r"GRT330106\test"),
}

# Lab-only fallback credential. The fleet uses a single throwaway login
# (GOLD-CLUB\test / test — see .cursor/rules/lab-cabinet-access.mdc). Windows
# will NOT return a ``cmdkey``-stored domain password via ``CredRead`` (fails
# with error 87), yet WinRM/PsExec need the password to build a PSCredential.
# SMB works transparently but remote PowerShell does not, which is why the
# "could not verify" banner appears even with full access. Supply the documented
# lab password here so remote probes work out of the box; override with the
# GOLDCLUB_LAB_PASSWORD environment variable. The fleet allowlist prevents this
# credential from ever reaching a non-lab host.
_LAB_PASSWORD_ENV = "GOLDCLUB_LAB_PASSWORD"
_LAB_DEFAULT_PASSWORD = "test"

_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


class LabCredentialError(RuntimeError):
    """Raised when lab credentials are missing from Windows Credential Manager."""


class FleetAllowlistError(ValueError):
    """Raised when a remote IP is outside the known lab fleet."""


def is_lab_fleet_ip(ip: str) -> bool:
    host = (ip or "").strip()
    return host in LAB_FLEET_IPS


def require_lab_fleet_ip(ip: str) -> str:
    host = (ip or "").strip()
    if not host or not _IPV4_RE.fullmatch(host):
        raise FleetAllowlistError(f"Invalid cabinet IP: {ip!r}")
    octets = [int(p) for p in host.split(".")]
    if any(o > 255 for o in octets):
        raise FleetAllowlistError(f"Invalid cabinet IP: {ip!r}")
    if host not in LAB_FLEET_IPS:
        allowed = ", ".join(sorted(LAB_FLEET_IPS))
        raise FleetAllowlistError(
            f"Remote operations are limited to the lab fleet ({allowed}). "
            f"Refusing non-fleet IP: {host}"
        )
    return host


def _decode_cred_blob(blob: object) -> str:
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob.rstrip("\x00")
    if isinstance(blob, (bytes, bytearray)):
        raw = bytes(blob)
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore").rstrip("\x00")
    return str(blob).rstrip("\x00")


def _lab_credential_from_manager(ip: str | None) -> tuple[str, str] | None:
    """Best-effort read of a *readable* lab credential from Credential Manager.

    Returns ``(user, password)`` only when Windows actually hands back a password
    blob (generic credentials). Domain (``cmdkey``) entries never expose their
    password, so this usually returns ``None`` and the caller falls back.
    """
    if sys.platform != "win32":
        return None
    try:
        import win32cred  # type: ignore
    except ImportError:
        return None

    host = (ip or "").strip()
    targets: list[str] = []
    if host:
        targets.extend([host, f"Domain:target={host}", f"TERMSRV/{host}"])
    targets.extend(["GOLD-CLUB", r"GOLD-CLUB\test", "GoldClubLab", "Domain:target=GOLD-CLUB"])
    for fleet_ip in sorted(LAB_FLEET_IPS):
        if fleet_ip not in targets:
            targets.append(fleet_ip)
            targets.append(f"Domain:target={fleet_ip}")

    for target in targets:
        cred = None
        for cred_type in (win32cred.CRED_TYPE_DOMAIN_PASSWORD, win32cred.CRED_TYPE_GENERIC):
            try:
                cred = win32cred.CredRead(target, cred_type, 0)
                break
            except Exception:  # noqa: BLE001 — CredRead raises pywintypes.error
                cred = None
        if cred is None:
            continue
        user = str(cred.get("UserName") or "").strip()
        password = _decode_cred_blob(cred.get("CredentialBlob"))
        if user and password:
            return user, password
    return None


def lab_username_for_host(ip: str | None) -> str:
    """Default WinRM/SMB user for a fleet host (domain vs workgroup)."""
    host = (ip or "").strip()
    aliases = LAB_WORKGROUP_USERS.get(host)
    if aliases:
        return aliases[0]
    return LAB_USERNAME_HINT


def lab_winrm_authentication(ip: str | None) -> str:
    """``Invoke-Command -Authentication`` value for a fleet cabinet.

    Fleet hosts are always reached by IP. ``Negotiate`` tries Kerberos first and
    often fails with ``0x8009030e`` ("logon session does not exist") when the
    app runs on a local EGM or other non-interactive logon context. ``Default``
    picks NTLM for workgroup hosts and is safe for IP-based domain cabinets too
    (TrustedHosts + explicit ``PSCredential``).
    """
    host = (ip or "").strip()
    if host in LAB_FLEET_IPS:
        return "Default"
    return "Negotiate"


def _username_ok_for_host(user: str, ip: str | None) -> bool:
    host = (ip or "").strip()
    aliases = LAB_WORKGROUP_USERS.get(host)
    if not aliases:
        return True
    folded = (user or "").replace("/", "\\").casefold()
    return any(folded == alias.casefold() for alias in aliases)


def get_lab_credential(ip: str | None = None) -> tuple[str, str]:
    """Return the lab fleet credential for WinRM/PsExec/SMB.

    Resolution order: ``GOLDCLUB_LAB_PASSWORD`` env override, then a readable
    Credential Manager entry (must match the host's workgroup user when set),
    then the documented lab default. Never raises on Windows — remote probes
    must not be blocked just because Windows hides a ``cmdkey`` password from
    ``CredRead``.
    """
    default_user = lab_username_for_host(ip)
    from_manager = _lab_credential_from_manager(ip)

    env_pw = (os.environ.get(_LAB_PASSWORD_ENV) or "").strip()
    if env_pw:
        user = (
            from_manager[0]
            if from_manager and _username_ok_for_host(from_manager[0], ip)
            else default_user
        )
        return user, env_pw

    if from_manager is not None and _username_ok_for_host(from_manager[0], ip):
        return from_manager

    if sys.platform != "win32":
        raise LabCredentialError(
            "Lab credentials are only available on Windows (or set "
            f"{_LAB_PASSWORD_ENV})."
        )
    return default_user, _LAB_DEFAULT_PASSWORD


def probe_tcp_port(host: str, port: int, *, timeout_sec: float = 2.0) -> bool:
    """True when ``host:port`` accepts a TCP connection (lab LAN probe, not internet)."""
    ip = (host or "").strip()
    if not ip:
        return False
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout_sec):
            return True
    except OSError:
        return False


def format_lab_lan_unreachable(ip: str, *, winrm_open: bool, smb_open: bool, ping_ok: bool) -> str:
    """User-facing hint when a fleet cabinet cannot be reached on the lab subnet."""
    lines = [
        f"Cannot sync time — {ip} is not reachable on the lab LAN.",
        "",
        "Sync Time uses WinRM on the local subnet (10.0.0.x). It does not need internet.",
    ]
    if not ping_ok and not smb_open and not winrm_open:
        lines.extend(
            [
                "",
                "This PC may be off the lab network (e.g. home/office Wi‑Fi).",
                "Connect to the lab subnet, or run Log Investigator from the lab workstation.",
                "Fleet → Refresh known hosts to confirm the cabinet is online.",
            ]
        )
    elif ping_ok or smb_open:
        if not winrm_open:
            lines.extend(
                [
                    "",
                    f"{ip} responds on the network but WinRM (port 5985) is closed.",
                    "Enable WinRM on the cabinet: cabinet_tools\\shared\\Enable-WinRM.ps1",
                    "(or the USB Enable-WinRM script), then retry.",
                ]
            )
    lines.extend(
        [
            "",
            "First time from this PC? Run elevated:",
            "  .\\Initialize-LabAccess.ps1 -Verify",
        ]
    )
    return "\n".join(lines)


def ensure_lab_smb_credential(ip: str) -> bool:
    """Idempotent ``cmdkey`` mapping for a lab cabinet admin share (``C$``).

    Silent first-time auth for fleet IPs so UNC reads (SAS Verify Machine column,
    health probes, software swap) work without a Windows password dialog.

    Returns ``True`` when ``cmdkey`` was invoked successfully, ``False`` when
    skipped (non-Windows, non-fleet IP, missing tools) or the process failed.
    Never raises.
    """
    import logging
    import subprocess

    host = (ip or "").strip()
    if not host or sys.platform != "win32":
        return False
    try:
        host = require_lab_fleet_ip(host)
        user, password = get_lab_credential(host)
    except (FleetAllowlistError, LabCredentialError, ValueError):
        return False

    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": 15,
    }
    run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["cmdkey", f"/add:{host}", f"/user:{user}", f"/pass:{password}"],
            **run_kw,
        )
        return int(getattr(completed, "returncode", 1) or 0) == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).debug(
            "cmdkey lab credential for %s failed: %s", host, exc
        )
        return False


def safe_join_under(root: Path, relative_path: str) -> Path:
    """
    Join ``relative_path`` under ``root``, rejecting absolute paths and ``..``.

    Raises ``ValueError`` when the resolved destination would escape ``root``.
    """
    rel_raw = (relative_path or "").replace("\\", "/").strip()
    if not rel_raw:
        raise ValueError("Relative path is empty")
    if rel_raw.startswith("/") or re.match(r"^[A-Za-z]:/", rel_raw):
        raise ValueError(f"Absolute paths are not allowed: {relative_path!r}")
    parts = [p for p in Path(rel_raw).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"Path traversal is not allowed: {relative_path!r}")
    root_resolved = root.resolve()
    dest = (root_resolved.joinpath(*parts)).resolve()
    try:
        dest.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Resolved path escapes destination root:\n  {dest}\n  root={root_resolved}"
        ) from exc
    return dest


def allowed_ruleta_dest_uncs(ip: str) -> tuple[Path, ...]:
    """Remote Ruleta dests: admin share, or the ``slot`` share (GoldClub root)."""
    host = require_lab_fleet_ip(ip)
    return (
        Path(rf"\\{host}\c$\goldclub\ruleta"),
        Path(rf"\\{host}\slot\ruleta"),
    )


def _unc_key(path: Path | str) -> str:
    return str(path).replace("/", "\\").casefold().rstrip("\\")


def assert_ruleta_dest_unc(ip: str, dest: Path | str) -> Path:
    """Require dest under ``c$\\goldclub\\ruleta`` or ``slot\\ruleta`` on a fleet IP."""
    dest_path = Path(dest)
    dest_key = _unc_key(dest_path)
    allowed = allowed_ruleta_dest_uncs(ip)
    for root in allowed:
        root_key = _unc_key(root)
        if dest_key == root_key or dest_key.startswith(root_key + "\\"):
            return dest_path
    shown = " or ".join(str(p) for p in allowed)
    raise ValueError(
        f"Software Version destination must be under {shown} (got {dest_path})"
    )
