"""Scan-target writability and restore destination hints."""

from __future__ import annotations

import os
import re
from pathlib import Path

_IP_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_PROBE_NAME = ".gci_write_probe"


def cabinet_ip_from_scan_target(scan_target: str | None) -> str | None:
    """IPv4 host from a UNC scan target, or None."""
    text = (scan_target or "").replace("/", "\\").strip()
    if not text.startswith("\\\\"):
        if _IP_RE.match(text.rstrip("\\")):
            return text.rstrip("\\")
        return None
    host = text.lstrip("\\").split("\\", 1)[0].strip()
    return host if _IP_RE.match(host) else None


def cabinet_ip_from_game_drive(game_drive: str | None) -> str | None:
    """IPv4 host recorded on the snapshot (e.g. \\\\10.0.0.111\\slot)."""
    return cabinet_ip_from_scan_target(game_drive)


def goldclub_dest_writable(root: Path | str) -> tuple[bool, str | None]:
    """Return (True, None) when a tiny probe file can be created under *root*."""
    dest = Path(root)
    try:
        if not dest.exists():
            return False, f"Scan target does not exist: {dest}"
    except OSError as exc:
        return False, f"Scan target not reachable: {dest} ({exc})"

    probe_dir = dest / "ruleta" if (dest / "ruleta").is_dir() else dest
    try:
        if not probe_dir.is_dir():
            probe_dir = dest
            probe_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _writable_refusal(dest, exc)

    probe = probe_dir / _PROBE_NAME
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return _writable_refusal(dest, exc)


def _writable_refusal(dest: Path, exc: OSError) -> tuple[bool, str | None]:
    errno = getattr(exc, "errno", None)
    text = str(exc).casefold()
    if (
        errno in {13, 19}
        or "permission denied" in text
        or "write protected" in text
        or "read-only" in text
    ):
        return False, (
            f"Scan target {dest} is not writable ({exc}). "
            "Local VHD junctions on G: or R: are often read-only from this "
            "workstation. Set Scan target to the cabinet IP or UNC share "
            r"(e.g. 10.0.0.111 or \\10.0.0.111\slot) instead."
        )
    return False, f"Cannot write to scan target {dest}: {exc}"


def restore_target_hint(
    *,
    snapshot_game_drive: str | None,
    live_serial: str | None,
    snapshot_serial: str | None,
) -> str | None:
    """One-line hint when the user picked the wrong local disk."""
    ip = cabinet_ip_from_game_drive(snapshot_game_drive)
    if not ip:
        return None
    if live_serial and snapshot_serial and live_serial.strip() == snapshot_serial.strip():
        return None
    return f"Set Scan target to {ip} (snapshot source cabinet)."


def restore_scan_target_candidates(
    game_drive: str | None,
    *,
    cabinet_ip: str | None = None,
) -> tuple[str, ...]:
    """UNC paths to try for a snapshot's source cabinet."""
    from config_scanner.build_version import normalize_scan_target

    ip = cabinet_ip or cabinet_ip_from_game_drive(game_drive)
    if not ip:
        return ()
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        norm = normalize_scan_target(raw.strip())
        key = norm.casefold().rstrip("\\")
        if not key or key in seen:
            return
        seen.add(key)
        out.append(norm)

    if game_drive and game_drive.strip():
        add(game_drive)
    add(rf"\\{ip}\slot")
    add(rf"\\{ip}\c$\Goldclub")
    add(ip)
    return tuple(out)


def _target_reachable(target: str) -> bool:
    from config_scanner.build_version import scan_target_path

    try:
        return scan_target_path(target).exists()
    except OSError:
        return False


def target_ok_for_snapshot_restore(
    target: str,
    *,
    snapshot_serial: str | None,
) -> bool:
    """True when *target* is reachable, writable, and matches the snapshot serial."""
    from config_scanner.build_version import read_machine_serial_from_target, scan_target_path
    from config_scanner.machine_identity import egm_serials_match

    if not _target_reachable(target):
        return False
    ok, _ = goldclub_dest_writable(scan_target_path(target))
    if not ok:
        return False
    snap = (snapshot_serial or "").strip() or None
    if not snap:
        return True
    live = read_machine_serial_from_target(target)
    if not live:
        return True
    return egm_serials_match(live, snap)


def this_machine_is_snapshot_cabinet(
    snapshot_game_drive: str | None,
    snapshot_serial: str | None = None,
) -> bool:
    """True when this PC is the EGM that the snapshot was taken from."""
    name = (os.environ.get("COMPUTERNAME") or "").strip()
    snap = (snapshot_serial or "").strip()
    if snap and name.casefold() == snap.casefold():
        return True
    ip = cabinet_ip_from_game_drive(snapshot_game_drive)
    if not ip:
        return False
    from config_scanner.stack_restart import _hostname_matches

    return _hostname_matches(ip)


def resolve_restore_scan_target(
    user_target: str,
    *,
    snapshot_game_drive: str | None,
    snapshot_serial: str | None,
) -> tuple[str, str | None]:
    """Prefer the snapshot cabinet when the user's scan target is wrong or read-only."""
    from config_scanner.build_version import normalize_scan_target

    user = normalize_scan_target(user_target.strip())
    ip = cabinet_ip_from_game_drive(snapshot_game_drive)
    local_user = not user.replace("/", "\\").startswith("\\\\")
    on_cabinet = this_machine_is_snapshot_cabinet(
        snapshot_game_drive, snapshot_serial
    )
    # Workstation C:\Goldclub must not swallow a cabinet snapshot just because
    # a local tree exists (or even has a copied serial).
    if ip and local_user and not on_cabinet:
        try:
            from network.lab_access import ensure_lab_smb_credential

            ensure_lab_smb_credential(ip)
        except ImportError:
            pass
        for cand in restore_scan_target_candidates(
            snapshot_game_drive, cabinet_ip=ip
        ):
            if cand.casefold().rstrip("\\") == user.casefold().rstrip("\\"):
                continue
            if target_ok_for_snapshot_restore(cand, snapshot_serial=snapshot_serial):
                return cand, (
                    f"Scan target switched to {cand} (snapshot cabinet; was {user})."
                )

    if target_ok_for_snapshot_restore(user, snapshot_serial=snapshot_serial):
        return user, None

    if ip:
        try:
            from network.lab_access import ensure_lab_smb_credential

            ensure_lab_smb_credential(ip)
        except ImportError:
            pass

    for cand in restore_scan_target_candidates(snapshot_game_drive, cabinet_ip=ip):
        if cand.casefold().rstrip("\\") == user.casefold().rstrip("\\"):
            continue
        if target_ok_for_snapshot_restore(cand, snapshot_serial=snapshot_serial):
            return cand, (
                f"Scan target switched to {cand} (snapshot cabinet; was {user})."
            )
    return user, None
