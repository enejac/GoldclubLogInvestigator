"""Parallel SHA1 scanning and manifest persistence."""

from __future__ import annotations

import hashlib
import json
import shutil
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

SNAPSHOT_FILES_SUBDIR = "files"


def snapshot_content_root(snapshot_dir: Path) -> Path | None:
    """Archived config files under a snapshot (offline compare)."""
    archived = snapshot_dir / SNAPSHOT_FILES_SUBDIR
    return archived if archived.is_dir() else None


def archive_manifest_files(game_drive: str, manifest: Manifest, snapshot_dir: Path) -> None:
    """Copy scanned files into the snapshot for offline diffs."""
    source_root = scan_target_path(game_drive)
    dest_root = snapshot_dir / SNAPSHOT_FILES_SUBDIR
    for entry in manifest.files:
        rel_parts = Path(entry.relative_path.replace("\\", "/"))
        source = source_root / rel_parts
        dest = dest_root / rel_parts
        if not source.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


@dataclass(frozen=True)
class RestoreResult:
    written_count: int
    missing_count: int
    errors: tuple[str, ...]


def restore_manifest_files(
    snapshot_dir: Path,
    manifest: Manifest,
    game_drive: str,
) -> RestoreResult:
    """Copy archived snapshot files back to the live scan target (overwrite-only)."""
    content_root = snapshot_content_root(snapshot_dir)
    if content_root is None:
        raise FileNotFoundError(
            f"Snapshot has no archived files under {SNAPSHOT_FILES_SUBDIR}/. "
            "Re-scan to capture file content before writing back."
        )

    dest_root = scan_target_path(game_drive)
    written = 0
    missing: list[str] = []
    errors: list[str] = []

    for entry in manifest.files:
        rel_parts = Path(entry.relative_path.replace("\\", "/"))
        source = content_root / rel_parts
        dest = dest_root / rel_parts
        if not source.is_file():
            missing.append(entry.relative_path)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            written += 1
        except OSError as exc:
            errors.append(f"{entry.relative_path}: {exc}")

    return RestoreResult(
        written_count=written,
        missing_count=len(missing),
        errors=tuple(errors),
    )


@dataclass(frozen=True)
class FileEntry:
    relative_path: str
    sha1: str
    size_bytes: int
    last_write_utc: str


@dataclass(frozen=True)
class Manifest:
    scanned_at: str
    file_count: int
    elapsed_seconds: float
    files: list[FileEntry]


def _relative_path(game_drive: Path, file_path: Path) -> str:
    try:
        rel = file_path.relative_to(game_drive)
    except ValueError:
        rel = file_path
    return rel.as_posix()


def _hash_file(path: Path) -> FileEntry:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return FileEntry(
        relative_path="",
        sha1=digest.hexdigest().upper(),
        size_bytes=stat.st_size,
        last_write_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    )


def collect_scan_files(
    game_drive: Path,
    scan_roots: list[str] | list[ScanRootSpec],
    include_patterns: list[str],
) -> list[Path]:
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
    return sorted(files)


def effective_scan_roots(game_drive: Path, profile: GameProfile | None) -> list[ScanRootSpec]:
    """Profile scan roots, plus RAM-clear maintenance config when scripts are present."""
    if not profile:
        return [ScanRootSpec(path="config", recursive=True)]
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
) -> Manifest:
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

    def hash_one(path: Path) -> FileEntry:
        entry = _hash_file(path)
        return FileEntry(
            relative_path=_relative_path(drive_path, path),
            sha1=entry.sha1,
            size_bytes=entry.size_bytes,
            last_write_utc=entry.last_write_utc,
        )

    workers = max(1, parallel_workers)
    if len(files) <= 1 or workers == 1:
        entries = [hash_one(path) for path in files]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            entries = list(pool.map(hash_one, files))

    elapsed = round(time.perf_counter() - started, 2)
    return Manifest(
        scanned_at=datetime.now().astimezone().isoformat(),
        file_count=len(entries),
        elapsed_seconds=elapsed,
        files=sorted(entries, key=lambda item: item.relative_path),
    )


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
    }


def _file_entry_to_dict(entry: FileEntry) -> dict[str, object]:
    return {
        "relativePath": entry.relative_path,
        "sha1": entry.sha1,
        "sizeBytes": entry.size_bytes,
        "lastWriteUtc": entry.last_write_utc,
    }


def manifest_to_dict(manifest: Manifest) -> dict[str, object]:
    return {
        "scannedAt": manifest.scanned_at,
        "fileCount": manifest.file_count,
        "elapsedSeconds": manifest.elapsed_seconds,
        "files": [_file_entry_to_dict(entry) for entry in manifest.files],
    }


def load_manifest(snapshot_dir: Path) -> Manifest:
    data = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    files = [
        FileEntry(
            relative_path=item["relativePath"] if "relativePath" in item else item["relative_path"],
            sha1=item["sha1"],
            size_bytes=int(item["sizeBytes"] if "sizeBytes" in item else item["size_bytes"]),
            last_write_utc=item["lastWriteUtc"] if "lastWriteUtc" in item else item["last_write_utc"],
        )
        for item in data.get("files", [])
    ]
    return Manifest(
        scanned_at=data.get("scannedAt") or data.get("scanned_at", ""),
        file_count=int(data.get("fileCount") or data.get("file_count") or len(files)),
        elapsed_seconds=float(data.get("elapsedSeconds") or data.get("elapsed_seconds") or 0),
        files=files,
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
    )
