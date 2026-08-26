"""Ruleta ERROR 30 / 99 trial tokens that survive official RAM clear.

RAM clear (cleanup.d/ruleta.json) wipes ruleta/var and ruleta/arhiv only.
The binding token lives in ruleta/persistent/ (junctioned from
var/state/ruleta/persistent). Never touch licence XML or licence.dll.
"""

from __future__ import annotations

import re
import json
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PERSISTENT_TRIAL_NAMES: tuple[str, ...] = (
    "RouletteActivate.dat",
    "RouletteStop.flag",
    "HeapDataFinanceStamps.dat",
)
VAR_TRIAL_NAMES: tuple[str, ...] = (
    "Password.dat",
    "HeapDataDateTime.dat",
)
# Tito / WAT / wheel stay. These names must never be deleted by this module.
_KEEP_PERSISTENT: frozenset[str] = frozenset(
    {
        "HeapDataTitoPowerUp.dat",
        "HeapDataWatTransactions.dat",
        "DynamicPaytableWheelCurrent.dat",
        "HeapDataLongLongWrapper.dat",
    }
)


@dataclass(frozen=True, slots=True)
class TrialFileInfo:
    path: Path
    size: int
    all_zeros: bool


@dataclass(frozen=True, slots=True)
class TrialPersistReport:
    persistent_dir: Path
    files: tuple[TrialFileInfo, ...]
    activate_bound: bool


def goldclub_root(dest_root: Path) -> Path:
    """Scan target root (``C:\\goldclub``) or ``…\\ruleta`` -> GoldClub root."""
    dest = Path(dest_root)
    if dest.name.casefold() == "ruleta":
        return dest.parent
    return dest


def ruleta_root(dest_root: Path) -> Path:
    """GoldClub game root or the ruleta folder itself -> ruleta dir."""
    dest = Path(dest_root)
    if dest.name.casefold() == "ruleta":
        return dest
    return dest / "ruleta"


def persistent_dir(dest_root: Path) -> Path:
    return ruleta_root(dest_root) / "persistent"


def persistent_mirror_dir(dest_root: Path) -> Path:
    """``var/state/ruleta/persistent`` copy on cabinets that mirror the junction."""
    return goldclub_root(dest_root) / "var" / "state" / "ruleta" / "persistent"


def _goldclub_rel_path(dest_root: Path, rel: str) -> Path:
    parts = rel.replace("\\", "/").strip("/").split("/")
    return goldclub_root(dest_root).joinpath(*parts)


def _trial_live_path(dest_root: Path, rel: str) -> Path | None:
    """Resolve a trial file; fall back to the persistent mirror when junctions differ."""
    primary = _goldclub_rel_path(dest_root, rel)
    try:
        if primary.is_file():
            return primary
    except OSError:
        pass
    norm = rel.replace("\\", "/").strip("/")
    if not norm.startswith("ruleta/persistent/"):
        return None
    name = norm.rsplit("/", 1)[-1]
    mirror = persistent_mirror_dir(dest_root) / name
    try:
        if mirror.is_file():
            return mirror
    except OSError:
        pass
    return None


def _file_info(path: Path) -> TrialFileInfo | None:
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return TrialFileInfo(path=path, size=len(data), all_zeros=not any(data))


def inspect_trial_persistent(dest_root: Path) -> TrialPersistReport:
    persist = persistent_dir(dest_root)
    found: list[TrialFileInfo] = []
    activate_bound = False
    for name in PERSISTENT_TRIAL_NAMES:
        info = _file_info(persist / name)
        if info is None:
            continue
        found.append(info)
        if name == "RouletteActivate.dat" and not info.all_zeros:
            activate_bound = True
    return TrialPersistReport(
        persistent_dir=persist,
        files=tuple(found),
        activate_bound=activate_bound,
    )


def trial_paths_to_clear(dest_root: Path) -> list[Path]:
    """Existing trial token paths (persistent + var password/clock + mirror)."""
    root = ruleta_root(dest_root)
    gc = goldclub_root(dest_root)
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path).casefold()
        if key in seen:
            return
        try:
            if path.is_file():
                seen.add(key)
                paths.append(path)
        except OSError:
            pass

    persist = root / "persistent"
    mirror = gc / "var" / "state" / "ruleta" / "persistent"
    for name in PERSISTENT_TRIAL_NAMES:
        if name in _KEEP_PERSISTENT:
            continue
        _add(persist / name)
        _add(mirror / name)
    var = root / "var"
    for name in VAR_TRIAL_NAMES:
        _add(var / name)
    return paths


def clear_ruleta_var_arhiv(dest_root: Path) -> list[str]:
    """Wipe ``ruleta/var`` + ``ruleta/arhiv`` (official RAM-clear scope).

    Fixes stale Play1 / PaytableId schema after a 10.1↔10.2 software swap.
    Never touches ``ruleta/persistent`` or licence files.
    """
    root = ruleta_root(dest_root)
    removed: list[str] = []
    for sub in ("var", "arhiv"):
        path = root / sub
        try:
            if not path.exists():
                continue
            shutil.rmtree(path)
            removed.append(str(path))
        except OSError:
            continue
    db = root / "DataBase.db"
    try:
        if db.is_file():
            db.unlink()
            removed.append(str(db))
    except OSError:
        pass
    return removed


def default_trial_backup_dir(dest_root: Path, stamp: str | None = None) -> Path:
    when = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return goldclub_root(dest_root) / "var" / "state" / "gci-backup-trial" / when


def clear_trial_persistent(
    dest_root: Path,
    backup_dir: Path | None = None,
) -> list[str]:
    """Backup then delete trial tokens. Never licence XML/dll or Tito/WAT."""
    dest = Path(dest_root)
    to_clear = trial_paths_to_clear(dest)
    if not to_clear:
        return []
    bak = Path(backup_dir) if backup_dir is not None else default_trial_backup_dir(dest)
    bak.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for path in to_clear:
        if path.name in _KEEP_PERSISTENT:
            continue
        if path.suffix.casefold() in {".xml", ".dll"}:
            continue
        try:
            shutil.copy2(path, bak / path.name)
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def clear_stale_llave_after_software_swap(dest_root: Path) -> list[str]:
    """After a surgical Ruleta push: wipe LLAVE tokens so ERROR 30 does not return.

    Same files as ``Push-Ruleta102876.ps1`` (persistent + var password/clock).
    Expect ERROR 99 with a fresh System ID until the operator enters one trial
    password for that id (then ``RouletteActivate.dat`` is written).
    """
    return clear_trial_persistent(dest_root)


def trial_password_path(dest_root: Path) -> Path:
    """Where ``Clear-Error30.ps1 -Auto`` reads the LLAVE password on cabinet."""
    return goldclub_root(dest_root) / "var" / "state" / "error30.password"


@dataclass(frozen=True, slots=True)
class TrialDisplayChallenge:
    """Parsed ``<TRIAL error="N" type="DISPLAYED"> challenge id`` from ruleta logs."""

    error_code: int
    challenge_raw: str

    @property
    def ui_system_id(self) -> str:
        """Format shown on the LLAVE keypad (``1694097371-6442060005``)."""
        raw = self.challenge_raw.strip()
        if len(raw) >= 20 and raw.isdigit():
            return f"{raw[:10]}-{raw[10:]}"
        return raw


_TRIAL_DISPLAYED_BODY_RE = re.compile(
    r"""<TRIAL\s+error\s*=\s*["'](\d+)["']\s+type\s*=\s*["']DISPLAYED["']\s*>\s*(\d+)\s*</TRIAL>""",
    re.IGNORECASE,
)


def extract_trial_display_challenge(line: str) -> TrialDisplayChallenge | None:
    if not line:
        return None
    m = _TRIAL_DISPLAYED_BODY_RE.search(line)
    if not m:
        return None
    try:
        code = int(m.group(1))
    except ValueError:
        return None
    challenge = (m.group(2) or "").strip()
    if not challenge:
        return None
    return TrialDisplayChallenge(error_code=code, challenge_raw=challenge)


def _ruleta_log_dirs(dest_root: Path) -> list[Path]:
    root = goldclub_root(dest_root)
    log_root = root / "var" / "log"
    out: list[Path] = []
    try:
        if not log_root.is_dir():
            return out
        for child in log_root.iterdir():
            if not child.is_dir():
                continue
            name = child.name.casefold()
            if "ruleta" in name or "godot" in name:
                out.append(child)
    except OSError:
        return out
    return out


def find_latest_trial_displayed(
    dest_root: Path,
    *,
    tail_bytes: int = 120_000,
) -> TrialDisplayChallenge | None:
    """Newest TRIAL DISPLAYED line in ruleta / godot log folders."""
    best: tuple[float, TrialDisplayChallenge] | None = None
    for folder in _ruleta_log_dirs(dest_root):
        try:
            logs = sorted(
                folder.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for log_path in logs[:3]:
            try:
                data = log_path.read_bytes()
            except OSError:
                continue
            if len(data) > tail_bytes:
                data = data[-tail_bytes:]
            try:
                text = data.decode("utf-8", errors="replace")
            except OSError:
                continue
            for line in reversed(text.splitlines()):
                hit = extract_trial_display_challenge(line)
                if hit is None:
                    continue
                try:
                    mtime = log_path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if best is None or mtime >= best[0]:
                    best = (mtime, hit)
                break
    return best[1] if best else None


def _read_ruleta_log_tail(dest_root: Path, *, tail_bytes: int = 80_000) -> str:
    """Tail of the newest ruleta Roulette daily log."""
    best_text = ""
    best_mtime = -1.0
    for folder in _ruleta_log_dirs(dest_root):
        try:
            logs = sorted(
                folder.glob("*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for log_path in logs[:2]:
            try:
                data = log_path.read_bytes()
                mtime = log_path.stat().st_mtime
            except OSError:
                continue
            if len(data) > tail_bytes:
                data = data[-tail_bytes:]
            try:
                text = data.decode("utf-8", errors="replace")
            except OSError:
                continue
            if mtime >= best_mtime:
                best_mtime = mtime
                best_text = text
    return best_text


def trial_log_state(dest_root: Path) -> str:
    """Mirror ``Get-Error30State`` in Clear-Error30.ps1 (newest event wins).

    Returns one of: ``none``, ``displayed``, ``accepted``, ``re-locked``.
    """
    tail = _read_ruleta_log_tail(dest_root)
    if not tail:
        return "none"
    disp = tail.rfind('type="DISPLAYED"')
    ok = tail.rfind('type="SUCCEEDED"')
    expired = tail.rfind("Trial expired")
    err30 = tail.rfind("ERR: number on screen: 30")
    stop = tail.rfind("No buttons enabled in stop dialog")
    if disp < 0 and ok < 0 and expired < 0:
        return "none"
    lock_at = max(expired, err30, stop)
    if disp >= 0 and disp > ok and disp > lock_at:
        return "displayed"
    if ok >= 0 and ok >= disp and ok >= lock_at:
        return "accepted"
    if lock_at > ok and lock_at > disp:
        return "re-locked"
    return "none"


def llave_bind_note_after_clear(removed: list[str]) -> str:
    if not removed:
        return ""
    return (
        "Cleared stale LLAVE trial tokens (live WIBU licence unchanged). "
        "After stack restart expect ERROR 99 once — enter the trial password "
        "for the System ID shown on screen (not an old 30-code). "
        "Success writes RouletteActivate.dat; later restores skip the keypad "
        "until tokens are cleared again."
    )


ROLLBACK_TRIAL_SUBDIR = "rollback_trial"
ROLLBACK_TRIAL_FILES = "files"
ROLLBACK_TRIAL_MANIFEST = "manifest.json"


def trial_rel_paths_for_rollback() -> tuple[str, ...]:
    """Relative paths under GoldClub root saved for revert undo."""
    root = Path("ruleta")
    paths: list[str] = []
    for name in PERSISTENT_TRIAL_NAMES:
        paths.append(str(root / "persistent" / name).replace("\\", "/"))
    for name in VAR_TRIAL_NAMES:
        paths.append(str(root / "var" / name).replace("\\", "/"))
    return tuple(paths)


def rollback_trial_dir(snapshot_dir: Path) -> Path:
    return Path(snapshot_dir) / ROLLBACK_TRIAL_SUBDIR


def has_rollback_trial_state(snapshot_dir: Path) -> bool:
    manifest = rollback_trial_dir(snapshot_dir) / ROLLBACK_TRIAL_MANIFEST
    return manifest.is_file()


def snapshot_has_bound_activate(snapshot_dir: Path) -> bool:
    """True when ``rollback_trial`` archived a non-zero RouletteActivate.dat."""
    manifest_path = rollback_trial_dir(snapshot_dir) / ROLLBACK_TRIAL_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    paths = manifest.get("paths")
    if not isinstance(paths, list):
        return False
    for item in paths:
        if not isinstance(item, dict):
            continue
        if not str(item.get("rel") or "").endswith("RouletteActivate.dat"):
            continue
        return bool(item.get("present")) and bool(item.get("activate_bound"))
    return False


def snapshot_should_auto_enter_llave(snapshot_dir: Path | None) -> bool:
    """False when the snapshot already has a bound token — do not type a password."""
    if snapshot_dir is None:
        return True
    try:
        return not snapshot_has_bound_activate(snapshot_dir)
    except OSError:
        return True


def clear_auto_llave_password_files(scan_target: str) -> list[str]:
    """Remove leftover ``error30.password`` so Run-FullStack will not auto-type."""
    from config_scanner.build_version import scan_target_path
    from config_scanner.stack_restart import unc_host_from_target

    removed: list[str] = []
    dest = scan_target_path(scan_target)
    path = trial_password_path(dest)
    try:
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    except OSError:
        pass
    host = (unc_host_from_target(scan_target) or "").strip()
    if host:
        for extra in (
            Path(rf"\\{host}\USB\usb_scripts\roulette\error30.password"),
            Path(rf"\\{host}\D$\usb_scripts\roulette\error30.password"),
        ):
            try:
                if extra.is_file():
                    extra.unlink()
                    removed.append(str(extra))
            except OSError:
                continue
    return removed


# Heap clock / expire files. Restoring them under a parked lab clock logs
# "System time changed" and ERROR 30. Proven bypass is Activate.dat only.
_HEAP_CLOCK_RELS = frozenset(
    {
        "ruleta/var/Password.dat",
        "ruleta/var/HeapDataDateTime.dat",
        "ruleta/persistent/HeapDataFinanceStamps.dat",
        "ruleta/persistent/RouletteStop.flag",
    }
)


def capture_trial_state_for_rollback(dest_root: Path, snapshot_dir: Path) -> list[str]:
    """Archive live LLAVE trial files into a presave / rollback snapshot folder."""
    dest = goldclub_root(dest_root)
    out_root = rollback_trial_dir(snapshot_dir)
    files_root = out_root / ROLLBACK_TRIAL_FILES
    files_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    notes: list[str] = []
    for rel in trial_rel_paths_for_rollback():
        live = _trial_live_path(dest, rel)
        present = live is not None
        archived_name = rel.replace("/", "__")
        entry: dict[str, object] = {"rel": rel, "present": present}
        if present and live is not None:
            try:
                data = live.read_bytes()
            except OSError:
                present = False
                entry["present"] = False
            else:
                archive_path = files_root / archived_name
                archive_path.write_bytes(data)
                if not archive_path.is_file() or archive_path.read_bytes() != data:
                    present = False
                    entry["present"] = False
                    notes.append(f"rollback capture failed to write {rel}")
                else:
                    entry["archived"] = archived_name
                    if rel.endswith("RouletteActivate.dat"):
                        bound = bool(data) and any(data)
                        entry["activate_bound"] = bound
                        notes.append(
                            f"captured {rel} ({'bound' if bound else 'empty'})"
                        )
                    else:
                        notes.append(f"captured {rel}")
        elif not present:
            notes.append(f"rollback: {rel} was absent on machine")
        entries.append(entry)
    manifest = {
        "version": 1,
        "paths": entries,
        "capturedAt": datetime.now().isoformat(),
    }
    (out_root / ROLLBACK_TRIAL_MANIFEST).write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return notes


def restore_trial_state_from_rollback(dest_root: Path, snapshot_dir: Path) -> list[str]:
    """Put back the snapshot LLAVE bind. Activate only — never heap clock files."""
    out_root = rollback_trial_dir(snapshot_dir)
    manifest_path = out_root / ROLLBACK_TRIAL_MANIFEST
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["rollback trial manifest unreadable"]
    paths = manifest.get("paths")
    if not isinstance(paths, list):
        return ["rollback trial manifest invalid"]
    dest = goldclub_root(dest_root)
    files_root = out_root / ROLLBACK_TRIAL_FILES
    notes: list[str] = []
    for item in paths:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("rel") or "").strip()
        if not rel:
            continue
        live = _goldclub_rel_path(dest, rel)
        present = bool(item.get("present"))
        archived = str(item.get("archived") or "").strip()
        try:
            if rel in _HEAP_CLOCK_RELS:
                if live.is_file():
                    live.unlink()
                    notes.append(f"dropped heap clock file {rel}")
                continue
            if present and archived:
                src = files_root / archived
                if not src.is_file():
                    notes.append(f"missing archived {rel}")
                    continue
                data = src.read_bytes()
                if rel.endswith("RouletteActivate.dat") and not any(data):
                    if live.is_file():
                        live.unlink()
                    notes.append(f"skipped empty {rel}")
                    continue
                live.parent.mkdir(parents=True, exist_ok=True)
                live.write_bytes(data)
                if rel.startswith("ruleta/persistent/"):
                    mirror = persistent_mirror_dir(dest) / live.name
                    try:
                        mirror.parent.mkdir(parents=True, exist_ok=True)
                        mirror.write_bytes(data)
                    except OSError:
                        pass
                if rel.endswith("RouletteActivate.dat"):
                    bound = bool(item.get("activate_bound")) or any(data)
                    notes.append(
                        f"restored {rel} ({'bound' if bound else 'empty'})"
                    )
                else:
                    notes.append(f"restored {rel}")
            elif live.is_file():
                live.unlink()
                notes.append(f"removed {rel} (was absent before restore)")
        except OSError as exc:
            notes.append(f"{rel}: {exc}")
    if notes:
        notes.insert(
            0,
            "Restored LLAVE trial bind from snapshot.",
        )
    return notes


def _copy_trial_backup_file(src: Path, dest_file: Path) -> None:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_file)


def restore_trial_from_newest_gci_backup(dest_root: Path) -> list[str]:
    """Fallback when a snapshot has no embedded trial bind: newest gci-backup-trial folder."""
    root = goldclub_root(dest_root)
    backup_root = root / "var" / "state" / "gci-backup-trial"
    try:
        if not backup_root.is_dir():
            return []
        folders = sorted(
            (p for p in backup_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return []

    notes: list[str] = []
    for folder in folders:
        activate = folder / "RouletteActivate.dat"
        try:
            if not activate.is_file():
                continue
            data = activate.read_bytes()
        except OSError:
            continue
        if not any(data):
            continue

        restored_any = False
        persist = persistent_dir(dest_root)
        var = ruleta_root(dest_root) / "var"
        mapping = [
            *(persist / name for name in PERSISTENT_TRIAL_NAMES),
            *(var / name for name in VAR_TRIAL_NAMES),
        ]
        for dest_file in mapping:
            src = folder / dest_file.name
            if not src.is_file():
                continue
            try:
                _copy_trial_backup_file(src, dest_file)
                restored_any = True
                notes.append(f"restored {dest_file.name} from {folder.name}")
            except OSError as exc:
                notes.append(f"{dest_file.name}: {exc}")
        if restored_any:
            notes.insert(
                0,
                f"Restored LLAVE trial bind from gci-backup-trial/{folder.name}.",
            )
            return notes
    return notes


def restore_trial_bind_for_snapshot(
    dest_root: Path,
    snapshot_dir: Path,
    *,
    allow_gci_backup: bool = True,
    snapshot_major_minor: str | None = None,
    previous_live_major_minor: str | None = None,
) -> tuple[list[str], bool]:
    """Restore per-build LLAVE files after a config/software swap.

    A bound token *from this snapshot* matches the software just pushed, so it
    is restored even on 10.1↔10.2. Cabinet ``gci-backup-trial`` is skipped on
    version transfer — that folder can hold the *previous* exe's hash.
    """
    snap = _major_minor_token(snapshot_major_minor)
    prev = _major_minor_token(previous_live_major_minor)
    version_transfer = bool(snap and prev and prev != snap)
    if has_rollback_trial_state(snapshot_dir):
        notes = restore_trial_state_from_rollback(dest_root, snapshot_dir)
        restored = any(
            n.startswith("restored ") and "RouletteActivate.dat" in n for n in notes
        )
        return notes, restored
    if version_transfer:
        return (
            [
                f"Ruleta {prev} -> {snap}: no snapshot trial bind to restore."
            ],
            False,
        )
    if allow_gci_backup:
        notes = restore_trial_from_newest_gci_backup(dest_root)
        restored = any(n.startswith("restored ") for n in notes)
        return notes, restored
    return [], False


def finance_stamp_unix(dest_root: Path) -> int | None:
    """First Trial-expired Unix stamp in HeapDataFinanceStamps.dat, if present."""
    path = persistent_dir(dest_root) / "HeapDataFinanceStamps.dat"
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 8:
        return None
    stamp = struct.unpack_from("<I", data, 4)[0]
    if stamp < 1_500_000_000 or stamp > 2_200_000_000:
        return None
    return stamp


def finance_stamp_mismatches_clock(
    dest_root: Path,
    now: datetime | None = None,
) -> bool:
    """True when the finance stamp's calendar day is not the cabinet clock's day."""
    stamp = finance_stamp_unix(dest_root)
    if stamp is None:
        return False
    here = now if now is not None else datetime.now().astimezone()
    if here.tzinfo is None:
        here = here.astimezone()
    day = datetime.fromtimestamp(stamp).astimezone(here.tzinfo).date()
    return day != here.date()


def _major_minor_token(version: str | None) -> str:
    raw = (version or "").strip()
    if not raw:
        return ""
    parts = [p for p in raw.split(".") if p]
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return raw


def should_reset_trial_bind(
    dest_root: Path,
    *,
    snapshot_major_minor: str | None = None,
    live_major_minor: str | None = None,
    previous_live_major_minor: str | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """When a leftover LLAVE hash / finance stamp will look like a wrecked licence.

    The Aurum XML (37A55022, RSA-SHA1 DigestValue) is not this. Ruleta 10.2
    Development remembers ``RouletteActivate.dat`` (20-byte bind token) and a
    first-expire Unix stamp. Crossing 10.1↔10.2, or moving the clock across
    that stamp's day, brings ERROR 30 back even though the WIBU file is fine.
    """
    report = inspect_trial_persistent(dest_root)
    snap = _major_minor_token(snapshot_major_minor)
    live = _major_minor_token(live_major_minor)
    prev = _major_minor_token(previous_live_major_minor)
    # After a software push live == snap; compare against the *prior* build.
    if snap and prev and prev != snap:
        has_finance = finance_stamp_unix(dest_root) is not None
        if report.activate_bound or has_finance:
            return True, (
                f"Ruleta {prev} -> {snap}: leftover trial bind from the prior "
                "version would show ERROR 30 (not a broken 37A55022 licence)."
            )
    if snap and live and snap != live:
        return True, (
            f"Ruleta {live} -> {snap}: leftover RouletteActivate bind hash "
            "would come up as ERROR 30 (not a broken 37A55022 licence)."
        )
    if finance_stamp_mismatches_clock(dest_root, now):
        return True, (
            "HeapDataFinanceStamps.dat is a different calendar day than the "
            "cabinet clock (time moved between snapshots) — Ruleta treats "
            "that as trial tamper / ERROR 30."
        )
    return False, ""


def reset_trial_bind_if_needed(
    dest_root: Path,
    *,
    snapshot_major_minor: str | None = None,
    live_major_minor: str | None = None,
    previous_live_major_minor: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Backup and delete trial tokens when ``should_reset_trial_bind``. Never licence."""
    needed, reason = should_reset_trial_bind(
        dest_root,
        snapshot_major_minor=snapshot_major_minor,
        live_major_minor=live_major_minor,
        previous_live_major_minor=previous_live_major_minor,
        now=now,
    )
    if not needed:
        return []
    removed = clear_trial_persistent(dest_root)
    notes = [reason]
    if removed:
        notes.append(
            f"cleared {len(removed)} trial token(s); live licence XML/dll not touched"
        )
    return notes


def is_trial_persist_rel_path(relative_path: str) -> bool:
    """True when a snapshot restore would put an expired LLAVE token back."""
    norm = (relative_path or "").replace("\\", "/").strip("/").casefold()
    if not norm:
        return False
    name = norm.rsplit("/", 1)[-1]
    if name in {n.casefold() for n in PERSISTENT_TRIAL_NAMES}:
        return (
            "/persistent/" in f"/{norm}"
            or norm.startswith("persistent/")
            or norm.startswith("ruleta/persistent/")
            or "var/state/ruleta/persistent/" in norm
        )
    if name in {n.casefold() for n in VAR_TRIAL_NAMES}:
        return norm.endswith("ruleta/var/" + name) or norm == "var/" + name
    return False
