"""High-level config scanner API used by the GUI."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config_scanner.build_version import (
    DiscoverResult,
    baseline_folder_name,
    discover_game_repo,
    is_baseline_folder_name,
    match_profile_for_target,
    normalize_scan_target,
    format_build_info_log_line,
    resolve_build_info,
    resolve_scan_for_target,
    scan_target_path,
    snapshot_folder_name,
)
from config_scanner.paths import (
    baseline_path,
    load_tool_config,
    reports_path,
    snapshots_path,
    template_path,
    tool_root,
)
from config_scanner.profiles import GameProfile, default_profile_id, get_profile, load_profiles
from config_scanner.report import write_comparison_report
from config_scanner.scanner import (
    archive_manifest_files,
    build_info_to_dict,
    build_info_version_fields_missing,
    build_manifest,
    enrich_snapshot_build_info,
    load_build_info,
    load_manifest,
    manifest_to_dict,
    merge_build_info_versions,
    restore_manifest_files,
    save_json,
    snapshot_content_root,
)
from config_scanner.xml_diff import FileDiff, change_summary, compare_manifests


def _move_snapshot_dir(source: Path, dest: Path) -> None:
    """Rename snapshot folder; copy+delete fallback for locked or cross-volume paths."""
    try:
        source.rename(dest)
    except OSError as exc:
        try:
            shutil.copytree(source, dest)
            shutil.rmtree(source)
        except OSError as copy_exc:
            raise OSError(
                f"Could not move snapshot folder:\n  from: {source}\n  to: {dest}\n"
                f"Rename error: {exc}\nCopy error: {copy_exc}"
            ) from copy_exc


@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    build_number: str | None
    product_version: str | None
    source_version: str | None
    exe_product_version: str | None
    exe_product_name: str | None
    branch: str | None
    scan_timestamp: str
    file_count: int
    game_drive: str
    is_baseline: bool
    profile_label: str | None = None
    profile_id: str | None = None


def scan_scope_zero_diff_hint(
    profile_id: str | None,
    profile_label: str | None = None,
) -> str:
    """Explain likely out-of-scope paths when a compare finds no file diffs."""
    pid = (profile_id or "").strip()
    label = (profile_label or "").strip()
    if pid == "roulette_usb" or "roulette" in label.casefold():
        return (
            "The game change may be outside scan scope (roulette config\\ XML/INI; "
            "not var\\state or runtime caches). Re-scan after the file is saved to disk."
        )
    if pid == "slot_lab_90" or "slot" in label.casefold():
        return (
            "The game change may be outside scan scope (only top-level slot XML/INI; "
            "not var\\state or nested theme assets). Re-scan after the file is saved to disk."
        )
    scope = label or pid or "this profile"
    return (
        f"The game change may be outside the configured scan scope for {scope}. "
        "Re-scan after the file is saved to disk."
    )


@dataclass(frozen=True)
class ScanResult:
    snapshot_name: str
    snapshot_path: Path
    manifest_file_count: int
    elapsed_seconds: float
    version_summary: str = ""
    profile_label: str = ""


@dataclass(frozen=True)
class CompareResult:
    baseline_snapshot: str
    target_snapshot: str
    report_path: Path
    summary: dict[str, int]
    file_diffs: list[FileDiff]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplySnapshotResult:
    snapshot_name: str
    target: str
    profile_label: str
    written_count: int
    missing_count: int
    errors: tuple[str, ...]


def allocate_snapshot_dir(snap_root: Path, base_name: str) -> tuple[str, Path]:
    """Return a snapshot folder name/path that does not already exist."""
    suffix = 0
    candidate = base_name
    while True:
        path = snap_root / candidate
        if not path.exists():
            return candidate, path
        suffix += 1
        candidate = f"{base_name}_{suffix:02d}"


class ConfigScannerService:
    def __init__(self, root: Path | None = None, profile_id: str | None = None) -> None:
        self.root = root or tool_root()
        self.config = load_tool_config(self.root)
        self.profile_id = profile_id or default_profile_id()
        self.profile = get_profile(self.profile_id)

    def set_profile(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.profile = get_profile(profile_id)

    @staticmethod
    def list_profile_choices() -> list[GameProfile]:
        return load_profiles()

    def get_data_root(self) -> Path:
        return self.root

    def get_snapshots_dir(self) -> Path:
        return snapshots_path(self.root)

    def get_reports_dir(self) -> Path:
        return reports_path(self.root)

    def prepare_for_target(self, scan_target: str | None = None) -> str:
        """Auto-detect slot vs roulette; honor an explicit scan path when provided."""
        explicit = (scan_target or "").strip()
        profiles = load_profiles()
        if explicit:
            result = resolve_scan_for_target(explicit, profiles)
        else:
            hint = self.config.game_drive
            result = discover_game_repo(profiles, hint)
        self.set_profile(result.profile_id)
        return result.target

    def detect_scan_target(self, preferred: str | None = None) -> str:
        hint = preferred or self.config.game_drive
        return discover_game_repo(load_profiles(), hint).target

    def auto_detect_repo(self, preferred: str | None = None) -> DiscoverResult:
        """Detect slot or roulette repo; may differ from the currently selected profile."""
        hint = preferred or self.config.game_drive
        result = discover_game_repo(load_profiles(), hint)
        self.set_profile(result.profile_id)
        return result

    def get_baseline_name(self) -> str | None:
        path = baseline_path(self.root)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data.get("snapshotName") or data.get("snapshot_name")

    def set_baseline_name(self, snapshot_name: str) -> None:
        path = baseline_path(self.root)
        save_json(path, {"snapshotName": snapshot_name})

    def set_baseline_snapshot(self, snapshot_name: str) -> str:
        """Rename snapshot to a friendly baseline folder and register it."""
        snap_root = snapshots_path(self.root)
        source_dir = snap_root / snapshot_name
        if not source_dir.is_dir():
            raise FileNotFoundError(
                f"Snapshot not found: {snapshot_name}\n\nLooked in:\n{snap_root}"
            )

        build_info = load_build_info(source_dir)
        profile_id = build_info.profile_id or self.profile.id
        new_name = baseline_folder_name(build_info, profile_id=profile_id)
        dest_dir = snap_root / new_name

        if source_dir.resolve() != dest_dir.resolve():
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            _move_snapshot_dir(source_dir, dest_dir)

        for entry in snap_root.iterdir():
            if not entry.is_dir() or entry.name == new_name:
                continue
            if is_baseline_folder_name(entry.name):
                shutil.rmtree(entry)

        final_dir = snap_root / new_name
        enrich_snapshot_build_info(
            final_dir,
            fallback_profile_id=profile_id,
            persist=True,
        )
        self._backfill_snapshot_versions_from_peers(snap_root)

        self.set_baseline_name(new_name)
        return new_name

    def _backfill_snapshot_versions_from_peers(self, snap_root: Path) -> None:
        """Copy version metadata between snapshots that share a build fingerprint hash."""
        loaded: list[tuple[Path, BuildInfo]] = []
        for entry in sorted(snap_root.iterdir()):
            if not entry.is_dir():
                continue
            build_info_path = entry / "build-info.json"
            manifest_path = entry / "manifest.json"
            if not build_info_path.is_file() or not manifest_path.is_file():
                continue
            loaded.append((entry, load_build_info(entry)))

        donors: dict[str, BuildInfo] = {}
        for _entry, info in loaded:
            build_no = (info.build_number or "").strip()
            if build_no and not build_info_version_fields_missing(info):
                donors[build_no] = info

        for entry, info in loaded:
            build_no = (info.build_number or "").strip()
            if not build_no or build_no not in donors:
                continue
            if not build_info_version_fields_missing(info):
                continue
            merged = merge_build_info_versions(info, donors[build_no])
            save_json(entry / "build-info.json", build_info_to_dict(merged))

    def delete_snapshot(self, snapshot_name: str) -> None:
        """Remove a snapshot folder from disk; clear baseline if it pointed at this scan."""
        snap_root = snapshots_path(self.root)
        snap_dir = snap_root / snapshot_name
        if not snap_dir.is_dir():
            raise FileNotFoundError(
                f"Snapshot not found: {snapshot_name}\n\nLooked in:\n{snap_root}"
            )
        shutil.rmtree(snap_dir)
        if self.get_baseline_name() == snapshot_name:
            baseline_file = baseline_path(self.root)
            if baseline_file.is_file():
                baseline_file.unlink()

    def list_snapshots(self) -> list[SnapshotInfo]:
        baseline = self.get_baseline_name()
        snap_root = snapshots_path(self.root)
        if not snap_root.is_dir():
            return []

        rows: list[SnapshotInfo] = []
        loaded: list[tuple[Path, BuildInfo, Manifest]] = []
        for entry in sorted(snap_root.iterdir()):
            if not entry.is_dir():
                continue
            build_info_path = entry / "build-info.json"
            manifest_path = entry / "manifest.json"
            if not build_info_path.is_file() or not manifest_path.is_file():
                continue
            build_info = load_build_info(entry)
            if build_info_version_fields_missing(build_info):
                build_info = enrich_snapshot_build_info(
                    entry,
                    fallback_profile_id=self.profile.id,
                    persist=True,
                )
            manifest = load_manifest(entry)
            loaded.append((entry, build_info, manifest))

        donors: dict[str, BuildInfo] = {}
        for _entry, info, _manifest in loaded:
            build_no = (info.build_number or "").strip()
            if build_no and not build_info_version_fields_missing(info):
                donors[build_no] = info

        for entry, build_info, manifest in loaded:
            build_no = (build_info.build_number or "").strip()
            if build_no and build_info_version_fields_missing(build_info) and build_no in donors:
                build_info = merge_build_info_versions(build_info, donors[build_no])
                save_json(entry / "build-info.json", build_info_to_dict(build_info))
            rows.append(
                SnapshotInfo(
                    name=entry.name,
                    build_number=build_info.build_number,
                    product_version=build_info.product_version,
                    source_version=build_info.source_version,
                    exe_product_version=build_info.exe_product_version,
                    exe_product_name=build_info.exe_product_name,
                    branch=build_info.branch,
                    scan_timestamp=build_info.scan_timestamp,
                    file_count=manifest.file_count,
                    game_drive=build_info.game_drive,
                    is_baseline=is_baseline_folder_name(entry.name)
                    or (baseline is not None and entry.name == baseline),
                    profile_label=build_info.profile_label,
                    profile_id=build_info.profile_id,
                )
            )
        rows.sort(key=lambda row: row.scan_timestamp)
        return rows

    def run_scan(self, scan_target: str | None = None) -> ScanResult:
        resolved_target = self.prepare_for_target(scan_target)
        scan_timestamp = datetime.now().astimezone()
        build_info = resolve_build_info(
            self.profile,
            resolved_target,
            scan_timestamp=scan_timestamp,
        )
        manifest = build_manifest(resolved_target, self.config, profile=self.profile)
        base_name = snapshot_folder_name(
            build_info.build_number,
            scan_timestamp,
            profile_id=self.profile.id,
        )
        snapshot_name, snapshot_dir = allocate_snapshot_dir(
            snapshots_path(self.root),
            base_name,
        )
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        archive_manifest_files(resolved_target, manifest, snapshot_dir)
        save_json(snapshot_dir / "build-info.json", build_info_to_dict(build_info))
        save_json(snapshot_dir / "manifest.json", manifest_to_dict(manifest))
        return ScanResult(
            snapshot_name=snapshot_name,
            snapshot_path=snapshot_dir,
            manifest_file_count=manifest.file_count,
            elapsed_seconds=manifest.elapsed_seconds,
            version_summary=format_build_info_log_line(build_info),
            profile_label=self.profile.label,
        )

    def compare_warnings(self, baseline_snapshot: str, target_snapshot: str) -> list[str]:
        snap_root = snapshots_path(self.root)
        baseline_dir = snap_root / baseline_snapshot
        target_dir = snap_root / target_snapshot
        if not baseline_dir.is_dir() or not target_dir.is_dir():
            return []

        baseline_info = load_build_info(baseline_dir)
        target_info = load_build_info(target_dir)
        warnings: list[str] = []

        baseline_profile = (baseline_info.profile_id or "").strip()
        target_profile = (target_info.profile_id or "").strip()
        if baseline_profile and target_profile and baseline_profile != target_profile:
            left = baseline_info.profile_label or baseline_profile
            right = target_info.profile_label or target_profile
            warnings.append(
                f"Different scan profiles: baseline is {left!r}, target is {right!r}. "
                "Added/removed counts may reflect mismatched scan scopes, not real config drift."
            )

        baseline_manifest = load_manifest(baseline_dir)
        target_manifest = load_manifest(target_dir)
        baseline_paths = {entry.relative_path for entry in baseline_manifest.files}
        target_paths = {entry.relative_path for entry in target_manifest.files}
        union = baseline_paths | target_paths
        if union:
            overlap = len(baseline_paths & target_paths)
            symmetric = len(baseline_paths.symmetric_difference(target_paths))
            if overlap == 0:
                warnings.append(
                    "No overlapping scanned files between baseline and target. "
                    "The comparison is almost entirely added/removed noise."
                )
            elif symmetric > len(union) * 0.5:
                warnings.append(
                    f"Scanned file sets differ substantially ({symmetric} of {len(union)} "
                    "paths only on one side). Confirm both snapshots use the same profile "
                    "and scan scope before trusting added/removed counts."
                )
        return warnings

    def snapshot_apply_warnings(self, snapshot_name: str, scan_target: str) -> list[str]:
        """Advisory messages before writing a snapshot back to the live machine."""
        snap_root = snapshots_path(self.root)
        snapshot_dir = snap_root / snapshot_name
        if not snapshot_dir.is_dir():
            return []

        build_info = load_build_info(snapshot_dir)
        manifest = load_manifest(snapshot_dir)
        warnings: list[str] = []

        if snapshot_content_root(snapshot_dir) is None:
            warnings.append(
                "Snapshot has no archived file content. Re-scan to capture files before writing back."
            )

        content_root = snapshot_content_root(snapshot_dir)
        if content_root is not None:
            missing = 0
            for entry in manifest.files:
                rel = Path(entry.relative_path.replace("\\", "/"))
                if not (content_root / rel).is_file():
                    missing += 1
            if missing:
                warnings.append(
                    f"{missing} of {manifest.file_count} manifest paths are missing from the archive; "
                    "those files will be skipped."
                )

        explicit = (scan_target or "").strip()
        if explicit:
            try:
                resolved = resolve_scan_for_target(explicit, load_profiles()).target
            except (FileNotFoundError, ValueError):
                resolved = normalize_scan_target(explicit)
        else:
            resolved = self.detect_scan_target()

        snapshot_profile = (build_info.profile_id or "").strip()
        dest_profile = match_profile_for_target(resolved, load_profiles())
        if snapshot_profile and dest_profile and snapshot_profile != dest_profile.id:
            left = build_info.profile_label or snapshot_profile
            right = dest_profile.label or dest_profile.id
            warnings.append(
                f"Profile mismatch: snapshot is {left!r}, scan target looks like {right!r}."
            )

        recorded = normalize_scan_target((build_info.game_drive or "").strip())
        if recorded and resolved and recorded.casefold() != resolved.casefold():
            warnings.append(
                f"Scan target ({resolved}) differs from snapshot source ({recorded})."
            )
        return warnings

    def apply_snapshot_to_target(
        self,
        snapshot_name: str,
        scan_target: str | None = None,
    ) -> ApplySnapshotResult:
        """Write archived snapshot files back to the live scan target."""
        snap_root = snapshots_path(self.root)
        snapshot_dir = snap_root / snapshot_name
        if not snapshot_dir.is_dir():
            raise FileNotFoundError(
                f"Snapshot not found: {snapshot_name}\n\nLooked in:\n{snap_root}"
            )

        build_info = load_build_info(snapshot_dir)
        manifest = load_manifest(snapshot_dir)
        if snapshot_content_root(snapshot_dir) is None:
            raise FileNotFoundError(
                f"Snapshot {snapshot_name} has no archived files under files/. "
                "Re-scan to capture file content before writing back."
            )

        resolved_target = self.prepare_for_target(scan_target)
        profiles = load_profiles()
        snapshot_profile = (build_info.profile_id or "").strip()
        dest_profile = match_profile_for_target(resolved_target, profiles)
        if snapshot_profile and dest_profile and snapshot_profile != dest_profile.id:
            left = build_info.profile_label or snapshot_profile
            right = dest_profile.label or dest_profile.id
            raise ValueError(
                f"Cannot write snapshot: profile mismatch ({left!r} snapshot vs {right!r} target)."
            )

        restore = restore_manifest_files(snapshot_dir, manifest, resolved_target)
        if restore.errors:
            sample = "; ".join(restore.errors[:3])
            extra = f" (+{len(restore.errors) - 3} more)" if len(restore.errors) > 3 else ""
            raise OSError(
                f"Wrote {restore.written_count} files but {len(restore.errors)} failed: {sample}{extra}"
            )

        return ApplySnapshotResult(
            snapshot_name=snapshot_name,
            target=resolved_target,
            profile_label=build_info.profile_label or dest_profile.label if dest_profile else "",
            written_count=restore.written_count,
            missing_count=restore.missing_count,
            errors=restore.errors,
        )

    def run_compare(self, baseline_snapshot: str, target_snapshot: str) -> CompareResult:
        snap_root = snapshots_path(self.root)
        baseline_dir = snap_root / baseline_snapshot
        target_dir = snap_root / target_snapshot
        if not baseline_dir.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {baseline_snapshot}")
        if not target_dir.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {target_snapshot}")

        baseline_info = load_build_info(baseline_dir)
        target_info = load_build_info(target_dir)
        baseline_manifest = load_manifest(baseline_dir)
        target_manifest = load_manifest(target_dir)
        warnings = tuple(self.compare_warnings(baseline_snapshot, target_snapshot))
        file_diffs = compare_manifests(
            baseline_manifest,
            target_manifest,
            baseline_content_root=snapshot_content_root(baseline_dir),
            target_content_root=snapshot_content_root(target_dir),
            baseline_game_drive=baseline_info.game_drive,
            target_game_drive=target_info.game_drive,
        )
        report_name = f"diff_{baseline_snapshot}_vs_{target_snapshot}.html"
        report_file = reports_path(self.root) / report_name
        write_comparison_report(
            baseline_info=baseline_info,
            target_info=target_info,
            baseline_snapshot=baseline_snapshot,
            target_snapshot=target_snapshot,
            file_diffs=file_diffs,
            template_path=template_path(self.root),
            output_path=report_file,
            warnings=warnings,
        )
        return CompareResult(
            baseline_snapshot=baseline_snapshot,
            target_snapshot=target_snapshot,
            report_path=report_file,
            summary=change_summary(file_diffs),
            file_diffs=file_diffs,
            warnings=warnings,
        )
