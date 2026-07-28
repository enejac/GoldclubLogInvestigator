"""Lab fleet allowlist and Credential Manager access (no embedded passwords)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

LAB_FLEET_IPS: frozenset[str] = frozenset(
    {
        "10.0.0.83",
        "10.0.0.90",
        "10.0.0.100",
        "10.0.0.110",
        "10.0.0.112",
        "10.0.0.171",
    }
)

LAB_USERNAME_HINT = r"GOLD-CLUB\test"

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


def get_lab_credential(ip: str | None = None) -> tuple[str, str]:
    """Return the lab fleet credential (``GOLD-CLUB\\test``) for WinRM/PsExec/SMB.

    Resolution order: ``GOLDCLUB_LAB_PASSWORD`` env override, then a readable
    Credential Manager entry, then the documented lab default. Never raises on
    Windows — remote probes must not be blocked just because Windows hides a
    ``cmdkey`` password from ``CredRead``.
    """
    from_manager = _lab_credential_from_manager(ip)

    env_pw = (os.environ.get(_LAB_PASSWORD_ENV) or "").strip()
    if env_pw:
        user = from_manager[0] if from_manager else LAB_USERNAME_HINT
        return user, env_pw

    if from_manager is not None:
        return from_manager

    if sys.platform != "win32":
        raise LabCredentialError(
            "Lab credentials are only available on Windows (or set "
            f"{_LAB_PASSWORD_ENV})."
        )
    return LAB_USERNAME_HINT, _LAB_DEFAULT_PASSWORD


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


def assert_ruleta_dest_unc(ip: str, dest: Path | str) -> Path:
    """Require remote Software Version dest under ``\\\\{ip}\\c$\\goldclub\\ruleta``."""
    host = require_lab_fleet_ip(ip)
    dest_path = Path(dest)
    allowed = Path(rf"\\{host}\c$\goldclub\ruleta")
    # Case-insensitive UNC compare via as_posix lower.
    dest_key = str(dest_path).replace("/", "\\").casefold().rstrip("\\")
    allowed_key = str(allowed).replace("/", "\\").casefold().rstrip("\\")
    if dest_key != allowed_key and not dest_key.startswith(allowed_key + "\\"):
        raise ValueError(
            f"Software Version destination must be under {allowed} (got {dest_path})"
        )
    return dest_path
