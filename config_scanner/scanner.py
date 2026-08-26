"""Parallel SHA1 scanning and manifest persistence."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config_scanner.build_version import (
    BuildInfo,
    has_ramclear_script,
    is_unc_path,
    normalize_scan_target,
    resolve_build_info,
    scan_target_path,
)
from config_scanner.paths import ToolConfig
from config_scanner.profiles import GameProfile, ScanRootSpec, get_profile
from network.lab_access import safe_join_under

SNAPSHOT_FILES_SUBDIR = "files"


def snapshot_content_root(snapshot_dir: Path) -> Path | None:
    """Archived config files under a snapshot (offline compare)."""
    archived = snapshot_dir / SNAPSHOT_FILES_SUBDIR
    return archived if archived.is_dir() else None


def _plain_override_bytes(path: Path) -> bytes | None:
    """Decrypt encrypted live ruleta setup.xml to plain settings XML for hash/archive."""
    try:
        from ai_helper.gcxml_decrypt import plain_bytes_for_config_scan
    except ImportError:
        return None
    return plain_bytes_for_config_scan(path)


def _needs_live_settings_decrypt(path: Path) -> bool:
    try:
        from ai_helper.gcxml_decrypt import needs_live_settings_decrypt
    except ImportError:
        return False
    return needs_live_settings_decrypt(path)


def _restore_archived_bytes(dest: Path, raw: bytes) -> bool:
    """Write archived bytes to live path; re-encrypt plain live ruleta setup.xml."""
    try:
        from ai_helper.gcxml_decrypt import (
            encrypt_gcxml_plain_file,
            is_live_ruleta_settings_xml,
            looks_like_gcxml_plain,
        )
    except ImportError:
        dest.write_bytes(raw)
        return True

    if looks_like_gcxml_plain(raw) and is_live_ruleta_settings_xml(dest):
        with tempfile.TemporaryDirectory(prefix="gcxml-restore-") as tmp:
            plain_path = Path(tmp) / "setup.plain.xml"
            plain_path.write_bytes(raw)
            return encrypt_gcxml_plain_file(plain_path, dest)
    dest.write_bytes(raw)
    return True


def archive_manifest_files(
    game_drive: str,
    manifest: Manifest,
    snapshot_dir: Path,
    *,
    content_overrides: dict[str, bytes] | None = None,
) -> None:
    """Copy scanned files into the snapshot for offline diffs.

    Encrypted live ruleta ``setup.xml`` is archived as Convert-GcxmlSetup plain XML
    so compare can show readable setting diffs (not gcxml token churn).
    ``content_overrides`` must be the exact bytes hashed into the manifest SHA1.
    Never archive ciphertext for live settings when decrypt failed.
    """
    source_root = scan_target_path(game_drive)
    dest_root = snapshot_dir / SNAPSHOT_FILES_SUBDIR
    overrides = content_overrides or {}
    for entry in manifest.files:
        rel_parts = Path(entry.relative_path.replace("\\", "/"))
        source = source_root / rel_parts
        dest = dest_root / rel_parts
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.relative_path in overrides:
            dest.write_bytes(overrides[entry.relative_path])
            continue
        if entry.decrypt_ok is False:
            # Hashed ciphertext — do not poison the archive with opaque gcxml.
            continue
        if not source.is_file():
            continue
        shutil.copy2(source, dest)


@dataclass(frozen=True)
class RestoreResult:
    written_count: int
    missing_count: int
    errors: tuple[str, ...]
    skipped_count: int = 0
    written_paths: tuple[str, ...] = ()


def restore_manifest_files(
    snapshot_dir: Path,
    manifest: Manifest,
    game_drive: str,
    *,
    relative_path_allow: frozenset[str] | set[str] | None = None,
) -> RestoreResult:
    """Copy archived snapshot files back to the live scan target (overwrite-only).

    When ``relative_path_allow`` is set, only those relative paths are written
    (used for hardware/software write scopes).

    Always skips serialport COM maps and non-licence machine-identity files
    even if they appear in the allow-list. Live licence XML is never
    overwritten. A different dongle is never written. A missing licence is
    restored only when the snapshot serial matches this cabinet.
    """
    from config_scanner.build_version import read_machine_serial_from_target
    from config_scanner.machine_identity import (
        decide_licence_write,
        is_licence_dll_path,
        is_licence_path,
        licensee_id_from_licence_bytes,
        live_has_licence,
        live_licence_licensee_id,
        prepare_restore_bytes_preserving_identity,
        serial_from_licence_bytes,
    )
    from config_scanner.write_scope import is_protected_write_path

    content_root = snapshot_content_root(snapshot_dir)
    if content_root is None:
        raise FileNotFoundError(
            f"Snapshot has no archived files under {SNAPSHOT_FILES_SUBDIR}/. "
            "Re-scan to capture file content before writing back."
        )

    dest_root = scan_target_path(game_drive)
    live_had_licence = live_has_licence(dest_root)
    live_licensee_id = live_licence_licensee_id(dest_root)
    live_serial = read_machine_serial_from_target(game_drive)
    snapshot_serial: str | None = None
    try:
        snapshot_serial = load_build_info(snapshot_dir).machine_serial
    except (OSError, ValueError, TypeError, KeyError):
        snapshot_serial = None

    written = 0
    missing: list[str] = []
    errors: list[str] = []
    skipped = 0
    written_paths: list[str] = []

    allow_norm: set[str] | None = None
    if relative_path_allow is not None:
        allow_norm = {p.replace("\\", "/").strip("/") for p in relative_path_allow}

    for entry in manifest.files:
        rel = entry.relative_path.replace("\\", "/").strip("/")
        if allow_norm is not None and rel not in allow_norm:
            skipped += 1
            continue
        licence = is_licence_path(rel)
        if not licence and is_protected_write_path(rel):
            skipped += 1
            continue
        try:
            dest = safe_join_under(dest_root, entry.relative_path)
            source = safe_join_under(content_root, entry.relative_path)
        except ValueError as exc:
            errors.append(f"{entry.relative_path}: {exc}")
            continue
        if not source.is_file():
            missing.append(entry.relative_path)
            continue
        if licence:
            try:
                dest_exists = dest.is_file()
            except OSError:
                dest_exists = False
            incoming_raw = source.read_bytes()
            allow, _reason = decide_licence_write(
                dest_exists=dest_exists,
                live_has_any=live_had_licence,
                live_serial=live_serial,
                snapshot_serial=snapshot_serial,
                incoming_licence_serial=serial_from_licence_bytes(incoming_raw),
                incoming_licensee_id=licensee_id_from_licence_bytes(incoming_raw),
                live_licensee_id=live_licensee_id,
                is_dll=is_licence_dll_path(rel),
            )
            if not allow:
                skipped += 1
                continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = source.read_bytes()
            if dest.is_file() and not licence:
                raw = prepare_restore_bytes_preserving_identity(dest, raw, rel)
            if not _restore_archived_bytes(dest, raw):
                errors.append(
                    f"{entry.relative_path}: failed to re-encrypt plain setup.xml for live write-back"
                )
                continue
            written += 1
            written_paths.append(entry.relative_path)
        except OSError as exc:
            errors.append(f"{entry.relative_path}: {exc}")

    return RestoreResult(
        written_count=written,
        missing_count=len(missing),
        errors=tuple(errors),
        skipped_count=skipped,
        written_paths=tuple(written_paths),
    )


def restore_single_archived_file(
    snapshot_dir: Path,
    relative_path: str,
    game_drive: str,
) -> None:
    """Copy one archived snapshot file to the live scan target (create parents).

    Plain archived ruleta setup.xml is re-encrypted on write-back.
    Serialport COM maps and non-licence identity files are refused. Live
    licence XML is never overwritten. Other XML merges live identity leaves.
    """
    from config_scanner.build_version import read_machine_serial_from_target
    from config_scanner.machine_identity import (
        decide_licence_write,
        is_licence_dll_path,
        is_licence_path,
        licensee_id_from_licence_bytes,
        live_has_licence,
        live_licence_licensee_id,
        prepare_restore_bytes_preserving_identity,
        serial_from_licence_bytes,
    )
    from config_scanner.write_scope import (
        is_protected_write_path,
        protected_write_block_reason,
    )

    rel = relative_path.replace("\\", "/").strip("/")
    content_root = snapshot_content_root(snapshot_dir)
    if content_root is None:
        raise FileNotFoundError(
            f"Snapshot has no archived files under {SNAPSHOT_FILES_SUBDIR}/. "
            "Re-scan to capture file content before writing back."
        )
    dest_root = scan_target_path(game_drive)
    dest = safe_join_under(dest_root, relative_path)
    source = safe_join_under(content_root, relative_path)
    if not source.is_file():
        raise FileNotFoundError(
            f"Archived file not found in snapshot:\n{relative_path}"
        )

    if is_licence_path(rel):
        snapshot_serial = None
        try:
            snapshot_serial = load_build_info(snapshot_dir).machine_serial
        except (OSError, ValueError, TypeError, KeyError):
            snapshot_serial = None
        try:
            dest_exists = dest.is_file()
        except OSError:
            dest_exists = False
        incoming_raw = source.read_bytes()
        live_had = live_has_licence(dest_root)
        allow, reason = decide_licence_write(
            dest_exists=dest_exists,
            live_has_any=live_had,
            live_serial=read_machine_serial_from_target(game_drive),
            snapshot_serial=snapshot_serial,
            incoming_licence_serial=serial_from_licence_bytes(incoming_raw),
            incoming_licensee_id=licensee_id_from_licence_bytes(incoming_raw),
            live_licensee_id=live_licence_licensee_id(dest_root),
            is_dll=is_licence_dll_path(rel),
        )
        if not allow:
            raise ValueError(reason)
    elif is_protected_write_path(rel):
        raise ValueError(
            protected_write_block_reason(rel)
            or "Refusing to write protected path from Config Scanner."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    if dest.is_file() and not is_licence_path(rel):
        raw = prepare_restore_bytes_preserving_identity(dest, raw, rel)
    if not _restore_archived_bytes(dest, raw):
        raise OSError(
            f"{relative_path}: failed to write/re-encrypt file to live target"
        )


@dataclass(frozen=True)
class FileEntry:
    relative_path: str
    sha1: str
    size_bytes: int
    last_write_utc: str
    content_kind: str | None = None  # "plain" | "cipher" | None
    decrypt_ok: bool | None = None


@dataclass(frozen=True)
class Manifest:
    scanned_at: str
    file_count: int
    elapsed_seconds: float
    files: list[FileEntry]
    warnings: tuple[str, ...] = ()


def _relative_path(game_drive: Path, file_path: Path) -> str:
    try:
        rel = file_path.relative_to(game_drive)
    except ValueError:
        rel = file_path
    return rel.as_posix()


def _hash_file(path: Path) -> tuple[FileEntry, bytes | None]:
    """Return ``(entry, archive_bytes)``.

    ``archive_bytes`` is the exact payload hashed into ``entry.sha1`` when it
    should be stored under ``files/`` (plain live setup). ``None`` means copy
    the on-disk file, or skip archive when ``decrypt_ok is False``.
    """
    needs_decrypt = _needs_live_settings_decrypt(path)
    plain = _plain_override_bytes(path) if needs_decrypt else None
    digest = hashlib.sha1()
    content_kind: str | None = None
    decrypt_ok: bool | None = None
    archive_bytes: bytes | None = None
    if needs_decrypt:
        decrypt_ok = plain is not None
        content_kind = "plain" if decrypt_ok else "cipher"
        if plain is not None:
            digest.update(plain)
            size_bytes = len(plain)
            archive_bytes = plain
        else:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            size_bytes = path.stat().st_size
            archive_bytes = None
    else:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        size_bytes = path.stat().st_size
    stat = path.stat()
    entry = FileEntry(
        relative_path="",
        sha1=digest.hexdigest().upper(),
        size_bytes=size_bytes,
        last_write_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        content_kind=content_kind,
        decrypt_ok=decrypt_ok,
    )
    return entry, archive_bytes


def collect_scan_files(
    game_drive: Path,
    scan_roots: list[str] | list[ScanRootSpec],
    include_patterns: list[str],
) -> list[Path]:
    from config_scanner.path_mirror import select_highest_etc_mirror_paths

    seen: set[Path] = set()
    files: list[Path] = []
    specs: list[ScanRootSpec] = []
    for item in scan_roots:
        if isinstance(item, ScanRootSpec):
            specs.append(item)
        else:
            specs.append(ScanRootSpec(path=str(item), recursive=True))

    for spec in specs:
        root_name = spec.path.strip().replace("\\", "/")
        if root_name in (".", ""):
            scan_root = game_drive
        else:
            scan_root = game_drive / root_name
        if not scan_root.exists():
            continue
        for pattern in include_patterns:
            iterator = scan_root.rglob(pattern) if spec.recursive else scan_root.glob(pattern)
            for path in iterator:
                if path.is_file() and path not in seen:
                    seen.add(path)
                    files.append(path)

    # config/etc and bios/etc are the same settings tree — keep highest path only.
    rel_to_path = {_relative_path(game_drive, path): path for path in files}
    kept_rels = select_highest_etc_mirror_paths(rel_to_path.keys())
    return sorted(
        (rel_to_path[rel] for rel in kept_rels),
        key=lambda path: _relative_path(game_drive, path).lower(),
    )


def effective_scan_roots(game_drive: Path, profile: GameProfile | None) -> list[ScanRootSpec]:
    """Profile scan roots, plus RAM-clear maintenance config when scripts are present."""
    if not profile:
        return [
            ScanRootSpec(path="config", recursive=True),
            ScanRootSpec(path="bios/etc", recursive=True),
            ScanRootSpec(path="data", recursive=True),
        ]
    roots = list(profile.scan_roots)
    if profile.build_version_relative_path and has_ramclear_script(game_drive):
        extras = (
            ScanRootSpec(path="maintenance/config/ramclear", recursive=True),
            ScanRootSpec(path="maintenance/config/ramclear/backup", recursive=True),
            ScanRootSpec(path="maintenance/config/ramclear/cleanup", recursive=True),
        )
        seen = {spec.path.casefold() for spec in roots}
        for spec in extras:
            if spec.path.casefold() not in seen:
                roots.append(spec)
                seen.add(spec.path.casefold())
    return roots


def build_manifest(
    game_drive: str,
    config: ToolConfig,
    profile: GameProfile | None = None,
) -> tuple[Manifest, dict[str, bytes]]:
    drive_path = Path(normalize_scan_target(game_drive))
    if not is_unc_path(game_drive) and not str(drive_path).endswith("\\"):
        drive_path = Path(str(drive_path) + "\\")
    if profile:
        scan_roots = effective_scan_roots(drive_path, profile)
    else:
        scan_roots = [ScanRootSpec(path=str(item), recursive=True) for item in config.scan_roots]
    include_patterns = profile.include_patterns if profile else config.include_patterns
    parallel_workers = profile.parallel_workers if profile else config.parallel_workers
    started = time.perf_counter()
    files = collect_scan_files(drive_path, scan_roots, include_patterns)

    def hash_one(path: Path) -> tuple[FileEntry, bytes | None]:
        entry, archive_bytes = _hash_file(path)
        return (
            FileEntry(
                relative_path=_relative_path(drive_path, path),
                sha1=entry.sha1,
                size_bytes=entry.size_bytes,
                last_write_utc=entry.last_write_utc,
                content_kind=entry.content_kind,
                decrypt_ok=entry.decrypt_ok,
            ),
            archive_bytes,
        )

    workers = max(1, parallel_workers)
    if len(files) <= 1 or workers == 1:
        hashed = [hash_one(path) for path in files]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            hashed = list(pool.map(hash_one, files))

    entries: list[FileEntry] = []
    content_overrides: dict[str, bytes] = {}
    for entry, archive_bytes in hashed:
        entries.append(entry)
        if archive_bytes is not None:
            content_overrides[entry.relative_path] = archive_bytes

    warnings: list[str] = []
    for entry in entries:
        if entry.decrypt_ok is False:
            warnings.append(
                f"Decrypt failed for {entry.relative_path} — hashed ciphertext "
                "(not archived); setting diffs may be wrong until decrypt works."
            )

    elapsed = round(time.perf_counter() - started, 2)
    manifest = Manifest(
        scanned_at=datetime.now().astimezone().isoformat(),
        file_count=len(entries),
        elapsed_seconds=elapsed,
        files=sorted(entries, key=lambda item: item.relative_path),
        warnings=tuple(warnings),
    )
    return manifest, content_overrides


def save_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_info_version_fields_missing(info: BuildInfo) -> bool:
    return not any(
        (
            info.product_version,
            info.source_version,
            info.exe_product_version,
            info.exe_product_name,
        )
    )


def merge_build_info_versions(target: BuildInfo, donor: BuildInfo) -> BuildInfo:
    """Fill missing version fields on target from another snapshot with the same build."""
    return BuildInfo(
        source_version=target.source_version or donor.source_version,
        branch=target.branch or donor.branch,
        product_version=target.product_version or donor.product_version,
        build_number=target.build_number or donor.build_number,
        build_date=target.build_date or donor.build_date,
        trigger=target.trigger or donor.trigger,
        requested_by=target.requested_by or donor.requested_by,
        scan_timestamp=target.scan_timestamp or donor.scan_timestamp,
        game_drive=target.game_drive or donor.game_drive,
        profile_id=target.profile_id or donor.profile_id,
        profile_label=target.profile_label or donor.profile_label,
        exe_product_version=target.exe_product_version or donor.exe_product_version,
        exe_file_version=target.exe_file_version or donor.exe_file_version,
        exe_product_name=target.exe_product_name or donor.exe_product_name,
        machine_serial=target.machine_serial or donor.machine_serial,
    )


def _parse_snapshot_timestamp(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def enrich_snapshot_build_info(
    snapshot_dir: Path,
    *,
    fallback_profile_id: str | None = None,
    persist: bool = True,
) -> BuildInfo:
    """Re-read version metadata from the snapshot's game_drive when build-info is sparse."""
    existing = load_build_info(snapshot_dir)
    if not build_info_version_fields_missing(existing):
        return existing

    profile_id = existing.profile_id or fallback_profile_id
    if not profile_id or not (existing.game_drive or "").strip():
        return existing

    try:
        profile = get_profile(profile_id)
        refreshed = resolve_build_info(
            profile,
            existing.game_drive,
            scan_timestamp=_parse_snapshot_timestamp(existing.scan_timestamp),
        )
    except (FileNotFoundError, OSError, ValueError, KeyError):
        return existing

    merged = merge_build_info_versions(existing, refreshed)
    merged = BuildInfo(
        source_version=merged.source_version,
        branch=merged.branch,
        product_version=merged.product_version,
        build_number=existing.build_number or refreshed.build_number,
        build_date=merged.build_date,
        trigger=existing.trigger or refreshed.trigger,
        requested_by=existing.requested_by or refreshed.requested_by,
        scan_timestamp=existing.scan_timestamp or refreshed.scan_timestamp,
        game_drive=existing.game_drive or refreshed.game_drive,
        profile_id=profile_id,
        profile_label=refreshed.profile_label or existing.profile_label,
        exe_product_version=merged.exe_product_version,
        exe_file_version=merged.exe_file_version,
        exe_product_name=merged.exe_product_name,
        machine_serial=existing.machine_serial or refreshed.machine_serial,
    )
    if persist and not build_info_version_fields_missing(merged):
        save_json(snapshot_dir / "build-info.json", build_info_to_dict(merged))
    return merged


def build_info_to_dict(info: BuildInfo) -> dict[str, str | None]:
    return {
        "sourceVersion": info.source_version,
        "branch": info.branch,
        "productVersion": info.product_version,
        "buildNumber": info.build_number,
        "buildDate": info.build_date,
        "trigger": info.trigger,
        "requestedBy": info.requested_by,
        "scanTimestamp": info.scan_timestamp,
        "gameDrive": info.game_drive,
        "profileId": info.profile_id,
        "profileLabel": info.profile_label,
        "exeProductVersion": info.exe_product_version,
        "exeFileVersion": info.exe_file_version,
        "exeProductName": info.exe_product_name,
        "machineSerial": info.machine_serial,
    }


def _file_entry_to_dict(entry: FileEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "relativePath": entry.relative_path,
        "sha1": entry.sha1,
        "sizeBytes": entry.size_bytes,
        "lastWriteUtc": entry.last_write_utc,
    }
    if entry.content_kind is not None:
        payload["contentKind"] = entry.content_kind
    if entry.decrypt_ok is not None:
        payload["decryptOk"] = entry.decrypt_ok
    return payload


def manifest_to_dict(manifest: Manifest) -> dict[str, object]:
    payload: dict[str, object] = {
        "scannedAt": manifest.scanned_at,
        "fileCount": manifest.file_count,
        "elapsedSeconds": manifest.elapsed_seconds,
        "files": [_file_entry_to_dict(entry) for entry in manifest.files],
    }
    if manifest.warnings:
        payload["warnings"] = list(manifest.warnings)
    return payload


def load_manifest(snapshot_dir: Path) -> Manifest:
    data = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    files = [
        FileEntry(
            relative_path=item["relativePath"] if "relativePath" in item else item["relative_path"],
            sha1=item["sha1"],
            size_bytes=int(item["sizeBytes"] if "sizeBytes" in item else item["size_bytes"]),
            last_write_utc=item["lastWriteUtc"] if "lastWriteUtc" in item else item["last_write_utc"],
            content_kind=item.get("contentKind") or item.get("content_kind"),
            decrypt_ok=item.get("decryptOk") if "decryptOk" in item else item.get("decrypt_ok"),
        )
        for item in data.get("files", [])
    ]
    warnings_raw = data.get("warnings") or ()
    return Manifest(
        scanned_at=data.get("scannedAt") or data.get("scanned_at", ""),
        file_count=int(data.get("fileCount") or data.get("file_count") or len(files)),
        elapsed_seconds=float(data.get("elapsedSeconds") or data.get("elapsed_seconds") or 0),
        files=files,
        warnings=tuple(str(w) for w in warnings_raw),
    )


def load_build_info(snapshot_dir: Path) -> BuildInfo:
    data = json.loads((snapshot_dir / "build-info.json").read_text(encoding="utf-8-sig"))
    return BuildInfo(
        source_version=data.get("sourceVersion") or data.get("source_version"),
        branch=data.get("branch"),
        product_version=data.get("productVersion") or data.get("product_version"),
        build_number=data.get("buildNumber") or data.get("build_number"),
        build_date=data.get("buildDate") or data.get("build_date"),
        trigger=data.get("trigger"),
        requested_by=data.get("requestedBy") or data.get("requested_by"),
        scan_timestamp=data.get("scanTimestamp") or data.get("scan_timestamp") or "",
        game_drive=data.get("gameDrive") or data.get("game_drive") or "",
        profile_id=data.get("profileId") or data.get("profile_id"),
        profile_label=data.get("profileLabel") or data.get("profile_label"),
        exe_product_version=data.get("exeProductVersion") or data.get("exe_product_version"),
        exe_file_version=data.get("exeFileVersion") or data.get("exe_file_version"),
        exe_product_name=data.get("exeProductName") or data.get("exe_product_name"),
        machine_serial=data.get("machineSerial") or data.get("machine_serial"),
    )
