"""High-level config scanner API used by the GUI."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from network.lab_access import safe_join_under

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
from config_scanner.snapshot_clock import resolve_snapshot_datetime
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
    restore_single_archived_file,
    save_json,
    snapshot_content_root,
)
from config_scanner.xml_diff import (
    ContentChange,
    FileDiff,
    apply_xml_value_at_path,
    change_summary,
    compare_manifests,
    resolve_apply_value,
)


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
    has_software: bool = False
    has_archive: bool = False


def order_snapshots_newest_first(
    rows: list[SnapshotInfo],
    *,
    pin_name: str | None = None,
) -> list[SnapshotInfo]:
    """Newest scan first. ``pin_name`` stays on top (just-created, even if the clock is rolled back)."""
    ordered = sorted(
        rows,
        key=lambda row: (row.scan_timestamp or "", row.name),
        reverse=True,
    )
    pin = (pin_name or "").strip()
    if not pin:
        return ordered
    pinned = [row for row in ordered if row.name == pin]
    rest = [row for row in ordered if row.name != pin]
    return pinned + rest


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
    warnings: tuple[str, ...] = ()
    software_file_count: int = 0
    software_captured: bool = False


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
    write_scope: str = "full"
    skipped_count: int = 0
    scoped_file_count: int = 0
    verify_ok: bool = True
    protected_verified: int = 0
    written_verified: int = 0
    verify_errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyChangeResult:
    relative_path: str
    flat_path: str
    value: str | None
    target: str
    profile_label: str


@dataclass(frozen=True)
class ApplyFileResult:
    relative_path: str
    snapshot_name: str
    target: str
    profile_label: str


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

    def is_scan_target_valid(self, scan_target: str) -> bool:
        """True when the path resolves to a known slot or roulette repo."""
        text = (scan_target or "").strip()
        if not text:
            return False
        try:
            resolve_scan_for_target(text, load_profiles())
            return True
        except (FileNotFoundError, ValueError):
            return False

    def get_baseline_name(self) -> str | None:
        path = baseline_path(self.root)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data.get("snapshotName") or data.get("snapshot_name")

    def set_baseline_name(self, snapshot_name: str) -> None:
        path = baseline_path(self.root)
        save_json(path, {"snapshotName": snapshot_name})

    def get_rollback_info(self):
        from config_scanner.rollback import load_rollback

        return load_rollback(self.root)

    def rollback_is_available(self) -> bool:
        from config_scanner.rollback import load_rollback, rollback_is_available

        info = load_rollback(self.root)
        return rollback_is_available(self.root, info)

    def get_rollback_details(self):
        """Version and completeness of the current undo point, or None."""
        from config_scanner.rollback import describe_rollback, load_rollback

        return describe_rollback(load_rollback(self.root), self.root)

    def record_rollback_snapshot(
        self,
        *,
        snapshot_name: str,
        restored_from: str,
        write_scope: str,
        scan_target: str,
    ) -> None:
        from config_scanner.rollback import make_rollback_info, save_rollback

        save_rollback(
            make_rollback_info(
                snapshot_name=snapshot_name,
                restored_from=restored_from,
                write_scope=write_scope,
                scan_target=scan_target,
            ),
            self.root,
        )

    def clear_rollback_snapshot(self) -> None:
        from config_scanner.rollback import clear_rollback

        clear_rollback(self.root)

    def refresh_rollback_if_stale(self) -> None:
        """Drop rollback metadata when the snapshot folder is gone."""
        if not self.rollback_is_available() and self.get_rollback_info() is not None:
            self.clear_rollback_snapshot()

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

        from config_scanner.software_compat import snapshot_has_embedded_software

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
                    has_software=snapshot_has_embedded_software(entry),
                    has_archive=snapshot_content_root(entry) is not None,
                )
            )
        return order_snapshots_newest_first(rows)

    def run_scan(
        self,
        scan_target: str | None = None,
        *,
        include_software: bool = False,
    ) -> ScanResult:
        resolved_target = self.prepare_for_target(scan_target)
        scan_timestamp = resolve_snapshot_datetime()
        build_info = resolve_build_info(
            self.profile,
            resolved_target,
            scan_timestamp=scan_timestamp,
        )
        manifest, content_overrides = build_manifest(
            resolved_target, self.config, profile=self.profile
        )
        base_name = snapshot_folder_name(
            build_info.build_number,
            scan_timestamp,
            profile_id=self.profile.id,
            product_version=build_info.product_version,
            exe_product_version=build_info.exe_product_version,
            machine_serial=build_info.machine_serial,
        )
        snapshot_name, snapshot_dir = allocate_snapshot_dir(
            snapshots_path(self.root),
            base_name,
        )
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        archive_manifest_files(
            resolved_target,
            manifest,
            snapshot_dir,
            content_overrides=content_overrides,
        )
        save_json(snapshot_dir / "build-info.json", build_info_to_dict(build_info))
        save_json(snapshot_dir / "manifest.json", manifest_to_dict(manifest))
        from config_scanner.build_version import scan_target_path
        from roulette_trial import capture_trial_state_for_rollback

        try:
            capture_trial_state_for_rollback(
                scan_target_path(resolved_target),
                snapshot_dir,
            )
        except OSError:
            pass
        warnings = list(manifest.warnings)
        software_count = 0
        software_ok = False
        if include_software:
            from config_scanner.software_compat import (
                capture_ruleta_software_into_snapshot,
            )

            captured = capture_ruleta_software_into_snapshot(
                resolved_target, snapshot_dir
            )
            software_count = captured.file_count
            software_ok = captured.captured
            if captured.note and not captured.captured:
                warnings.append(captured.note)
        return ScanResult(
            snapshot_name=snapshot_name,
            snapshot_path=snapshot_dir,
            manifest_file_count=manifest.file_count,
            elapsed_seconds=manifest.elapsed_seconds,
            version_summary=format_build_info_log_line(build_info),
            profile_label=self.profile.label,
            warnings=tuple(warnings),
            software_file_count=software_count,
            software_captured=software_ok,
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
        for note in baseline_manifest.warnings:
            warnings.append(f"Baseline: {note}")
        for note in target_manifest.warnings:
            warnings.append(f"Target: {note}")
        from config_scanner.xml_diff import is_encrypted_origin_config_path

        for label, man, snap_dir in (
            ("Baseline", baseline_manifest, baseline_dir),
            ("Target", target_manifest, target_dir),
        ):
            bad = [
                e.relative_path
                for e in man.files
                if e.decrypt_ok is False
            ]
            if bad:
                warnings.append(
                    f"{label} hashed ciphertext for live ruleta setup "
                    f"({', '.join(bad[:3])}{'…' if len(bad) > 3 else ''}) — "
                    "setting changes may be invisible."
                )
            content_root = snapshot_content_root(snap_dir)
            if content_root is not None:
                missing_plain = [
                    e.relative_path
                    for e in man.files
                    if is_encrypted_origin_config_path(e.relative_path)
                    and e.decrypt_ok is not False
                    and not (content_root / Path(e.relative_path.replace("\\", "/"))).is_file()
                ]
                if missing_plain:
                    warnings.append(
                        f"{label} missing archived plain for "
                        f"{', '.join(missing_plain[:3])}{'…' if len(missing_plain) > 3 else ''} — "
                        "compare may fall back to live disk."
                    )
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

    def snapshot_apply_warnings(
        self,
        snapshot_name: str,
        scan_target: str,
        write_scope: str = "full",
    ) -> list[str]:
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
        from config_scanner.build_version import read_machine_serial_from_target, scan_target_path
        from config_scanner.machine_identity import (
            egm_serials_match,
            is_licence_path,
            is_protected_machine_identity_path,
            licensee_id_from_licence_bytes,
            licensee_ids_match,
            live_has_licence,
            live_licence_licensee_id,
        )

        identity_files = [
            e.relative_path
            for e in manifest.files
            if is_protected_machine_identity_path(e.relative_path)
        ]
        licence_files = [p for p in identity_files if is_licence_path(p)]
        other_identity = [p for p in identity_files if not is_licence_path(p)]
        if other_identity:
            warnings.append(
                f"{len(other_identity)} machine-identity file(s) in this snapshot "
                "(ProductSerialNumber, Aurum ids) are never written — "
                "live EGM serial and EgmId stay unchanged."
            )
        if licence_files:
            dest_root = scan_target_path(resolved)
            live_serial = read_machine_serial_from_target(resolved)
            snap_serial = (build_info.machine_serial or "").strip() or None
            live_wibu = live_licence_licensee_id(dest_root)
            snap_wibu = None
            if content_root is not None:
                for rel in licence_files:
                    archived = content_root / Path(rel.replace("\\", "/"))
                    try:
                        if archived.is_file():
                            snap_wibu = licensee_id_from_licence_bytes(
                                archived.read_bytes()
                            )
                            if snap_wibu:
                                break
                    except OSError:
                        continue
            if not egm_serials_match(live_serial, snap_serial):
                warnings.append(
                    f"{len(licence_files)} licence file(s) skipped — restore only "
                    "when the snapshot serial matches this EGM "
                    f"(live {live_serial!r} vs snapshot {snap_serial!r})."
                )
            elif live_wibu and snap_wibu and not licensee_ids_match(
                live_wibu, snap_wibu
            ):
                warnings.append(
                    f"{len(licence_files)} licence file(s) skipped — snapshot "
                    f"LicenseeId {snap_wibu} is not the WIBU on this EGM "
                    f"({live_wibu})."
                )
            elif live_has_licence(dest_root):
                warnings.append(
                    f"{len(licence_files)} licence file(s) will not be written — "
                    "the live licence stays unchanged so 10.1 ↔ 10.2 restore "
                    "cannot corrupt it."
                )
            else:
                warnings.append(
                    f"{len(licence_files)} licence file(s) will be restored because "
                    "none exist on this EGM and the snapshot serial matches."
                )
        from config_scanner.software_compat import software_version_mismatch_warning

        mismatch = software_version_mismatch_warning(build_info, resolved)
        if mismatch:
            warnings.append(mismatch)
        try:
            from config_scanner.software_compat import (
                live_ruleta_major_minor_for_target,
                resolve_software_pack_for_snapshot,
            )
            from config_scanner.transition_preflight import (
                analyze_transition,
                warning_messages,
            )

            pack = resolve_software_pack_for_snapshot(
                build_info,
                resolved,
                snapshot_dir=snapshot_dir,
                tool_root=self.root,
            )
            dest_ruleta = scan_target_path(resolved) / "ruleta"
            live_mm = live_ruleta_major_minor_for_target(resolved)
            findings = analyze_transition(
                snapshot_info=build_info,
                dest_major_minor=live_mm,
                content_root=content_root,
                pack=pack,
                dest_ruleta=dest_ruleta if dest_ruleta.is_dir() else None,
                write_scope=write_scope,
            )
            warnings.extend(warning_messages(findings))
        except (OSError, ValueError, TypeError):
            pass
        if write_scope in {"full_software", "binaries_only"}:
            try:
                from config_scanner.software_compat import cabinet_host_for_scan_target
                from config_scanner.stack_restart import plan_stack_restart
                from network.ruleta_stack_probe import dest_ruleta_file_locked

                host = cabinet_host_for_scan_target(resolved)
                dest_ruleta = scan_target_path(resolved) / "ruleta"
                if (
                    plan_stack_restart(resolved) is not None
                    and dest_ruleta.is_dir()
                    and dest_ruleta_file_locked(dest_ruleta)
                ):
                    warnings.append(
                        "Ruleta middleware DLL is in use now — Kill-All runs "
                        "immediately after you confirm, before any files are written."
                    )
            except (OSError, ValueError, TypeError):
                pass
        try:
            from config_scanner.ruleta_compat import plan_ruleta_compat

            dest_for_plan = scan_target_path(resolved)
            plan = plan_ruleta_compat(dest_for_plan)
            if plan.hold_start:
                warnings.append(plan.reason)
                if plan.bypass:
                    warnings.append(plan.bypass)
        except (OSError, ValueError, TypeError):
            pass
        return warnings

    def snapshot_apply_refuses(
        self,
        snapshot_name: str,
        scan_target: str,
        write_scope: str = "full",
        *,
        defer_lock_check: bool = False,
    ) -> list[str]:
        """Hard stops — restore must not continue.

        ``defer_lock_check``: Kill-All runs right after confirm, so a locked
        middleware DLL is not a hard stop here (it is rechecked after stop).
        """
        from config_scanner.transition_preflight import refuse_messages
        from config_scanner.write_scope import WriteScope

        snap_root = snapshots_path(self.root)
        snapshot_dir = snap_root / snapshot_name
        if not snapshot_dir.is_dir():
            return []
        try:
            from config_scanner.build_version import (
                read_machine_serial_from_target,
                scan_target_path,
            )
            from config_scanner.dest_preflight import (
                goldclub_dest_writable,
                restore_target_hint,
            )
            from config_scanner.machine_identity import egm_serials_match
            from config_scanner.software_compat import (
                live_ruleta_major_minor_for_target,
                resolve_software_pack_for_snapshot,
            )
            from config_scanner.transition_preflight import analyze_transition

            build_info = load_build_info(snapshot_dir)
            pack = resolve_software_pack_for_snapshot(
                build_info,
                scan_target,
                snapshot_dir=snapshot_dir,
                tool_root=self.root,
            )
            dest_root = scan_target_path(scan_target)
            dest_ruleta = dest_root / "ruleta"
            findings = analyze_transition(
                snapshot_info=build_info,
                dest_major_minor=live_ruleta_major_minor_for_target(scan_target),
                content_root=snapshot_content_root(snapshot_dir),
                pack=pack,
                dest_ruleta=dest_ruleta if dest_ruleta.is_dir() else None,
                write_scope=write_scope,
            )
            refuses = list(refuse_messages(findings))

            ok, writable_msg = goldclub_dest_writable(dest_root)
            if not ok and writable_msg:
                refuses.append(writable_msg)

            snap_serial = (build_info.machine_serial or "").strip() or None
            live_serial = read_machine_serial_from_target(scan_target)
            scope = WriteScope(write_scope)
            serial_sensitive = scope in {
                WriteScope.FULL,
                WriteScope.FULL_SOFTWARE,
                WriteScope.BINARIES_ONLY,
            }
            if (
                serial_sensitive
                and snap_serial
                and live_serial
                and not egm_serials_match(live_serial, snap_serial)
            ):
                hint = restore_target_hint(
                    snapshot_game_drive=build_info.game_drive,
                    live_serial=live_serial,
                    snapshot_serial=snap_serial,
                )
                body = (
                    f"EGM serial mismatch: live {live_serial!r} vs snapshot "
                    f"{snap_serial!r}. Restore to machine requires the same "
                    "cabinet or the correct mounted disk."
                )
                if hint:
                    body += f" {hint}"
                refuses.append(body)

            if scope in {WriteScope.FULL_SOFTWARE, WriteScope.BINARIES_ONLY}:
                from config_scanner.software_compat import cabinet_host_for_scan_target
                from network.ruleta_stack_probe import preflight_remote_software_swap

                host = cabinet_host_for_scan_target(scan_target)
                if host and host != "local":
                    refuses.extend(
                        preflight_remote_software_swap(
                            host,
                            dest_ruleta,
                            defer_lock_check=True,
                        )
                    )
                elif dest_ruleta.is_dir():
                    from network.ruleta_stack_probe import dest_ruleta_swap_writable

                    swap_ok, swap_msg = dest_ruleta_swap_writable(
                        dest_ruleta, check_dll_lock=not defer_lock_check
                    )
                    if not swap_ok and swap_msg:
                        refuses.append(swap_msg)

            return refuses
        except (OSError, ValueError, TypeError):
            return []

    def resolve_restore_scan_target(
        self,
        snapshot_name: str,
        scan_target: str,
    ) -> tuple[str, str | None]:
        """Pick a writable cabinet path when the UI still points at the wrong disk."""
        from config_scanner.dest_preflight import resolve_restore_scan_target
        from config_scanner.scanner import load_build_info

        snap_dir = snapshots_path(self.root) / snapshot_name
        if not snap_dir.is_dir():
            return scan_target, None
        try:
            build_info = load_build_info(snap_dir)
        except (OSError, ValueError, TypeError):
            return scan_target, None
        return resolve_restore_scan_target(
            scan_target,
            snapshot_game_drive=build_info.game_drive,
            snapshot_serial=build_info.machine_serial,
        )

    def count_scoped_snapshot_files(
        self,
        snapshot_name: str,
        write_scope: str = "full",
    ) -> int:
        """How many restorable archived files match the write scope.

        Counts only paths that exist under the snapshot ``files/`` tree (not
        bare manifest entries whose archive copy was skipped).
        """
        from config_scanner.write_scope import filter_relative_paths

        snapshot_dir = snapshots_path(self.root) / snapshot_name
        if not snapshot_dir.is_dir():
            return 0
        content_root = snapshot_content_root(snapshot_dir)
        if content_root is None:
            return 0
        manifest = load_manifest(snapshot_dir)
        paths = filter_relative_paths(
            [entry.relative_path for entry in manifest.files],
            write_scope,
        )
        present = 0
        for rel in paths:
            if (content_root / Path(rel.replace("\\", "/"))).is_file():
                present += 1
        return present

    def capture_rollback_trial_bind(
        self,
        snapshot_name: str,
        scan_target: str | None = None,
    ) -> list[str]:
        """Save live LLAVE trial files into a presave / rollback snapshot."""
        from config_scanner.build_version import scan_target_path
        from roulette_trial import capture_trial_state_for_rollback

        resolved = self.prepare_for_target(scan_target)
        dest = scan_target_path(resolved)
        snap_dir = snapshots_path(self.root) / snapshot_name
        if not snap_dir.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")
        return capture_trial_state_for_rollback(dest, snap_dir)

    def apply_snapshot_to_target(
        self,
        snapshot_name: str,
        scan_target: str | None = None,
        *,
        write_scope: str = "full",
        only_changed_relative_paths: list[str] | tuple[str, ...] | None = None,
        is_revert: bool = False,
    ) -> ApplySnapshotResult:
        """Write archived snapshot files back to the live scan target.

        ``write_scope``: ``full`` | ``hardware`` | ``software`` |
        ``no_paytable`` | ``full_software`` | ``binaries_only`` — filters
        which archived paths are restored. Serialport layout/locations are
        never written. Licence XML is never overwritten when a live licence
        exists. A different dongle is never written. ``full_software``
        pushes the matching Ruleta pack from ``software_versions`` first
        (hard fail if that copy fails), then restores config so paytables
        land on the matching exe. ``binaries_only`` pushes that pack and
        keeps this cabinet's setup / switches / SAS / licence.

        When ``only_changed_relative_paths`` is set (compare diffs), further
        narrows to that set (still scope-filtered).
        """
        from config_scanner.write_scope import WriteScope, filter_relative_paths

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

        try:
            scope = WriteScope(write_scope)
        except ValueError as exc:
            raise ValueError(
                f"Unknown write scope {write_scope!r}; use full, hardware, "
                "software, no_paytable, full_software, or binaries_only."
            ) from exc

        if scope in {WriteScope.FULL_SOFTWARE, WriteScope.BINARIES_ONLY}:
            from config_scanner.software_compat import SoftwarePushError

            refuses = self.snapshot_apply_refuses(
                snapshot_name, resolved_target, write_scope=scope.value
            )
            if refuses:
                raise SoftwarePushError("\n".join(refuses))

        candidates = [entry.relative_path for entry in manifest.files]
        changed_norm: set[str] | None = None
        if only_changed_relative_paths is not None:
            changed_norm = {
                p.replace("\\", "/").strip("/") for p in only_changed_relative_paths
            }
            candidates = [
                p for p in candidates if p.replace("\\", "/").strip("/") in changed_norm
            ]
        allowed = filter_relative_paths(candidates, scope)

        from config_scanner.build_version import (
            read_machine_serial_from_target,
            scan_target_path,
        )
        from config_scanner.machine_identity import (
            egm_serials_match,
            is_licence_path,
        )
        from config_scanner.write_verify import (
            capture_pre_write_state,
            verify_snapshot_restore,
        )

        from config_scanner.paytable_compat import (
            filter_incompatible_paytable_restore_paths,
            live_ruleta_major_minor,
        )

        dest_root = scan_target_path(resolved_target)
        previous_live_mm = live_ruleta_major_minor(dest_root)
        cleared_trial_during_push = False

        extra_notes: list[str] = []
        if scope is WriteScope.BINARIES_ONLY:
            return self._apply_binaries_only_to_target(
                snapshot_name=snapshot_name,
                snapshot_dir=snapshot_dir,
                manifest=manifest,
                build_info=build_info,
                dest_profile=dest_profile,
                resolved_target=resolved_target,
                dest_root=dest_root,
                candidates=candidates,
                is_revert=is_revert,
            )
        if scope is WriteScope.FULL_SOFTWARE:
            from config_scanner.software_compat import push_matching_ruleta_software

            cleared_trial_during_push = not is_revert
            extra_notes.append(
                push_matching_ruleta_software(
                    build_info,
                    resolved_target,
                    snapshot_dir=snapshot_dir,
                    tool_root=self.root,
                    clear_trial_tokens=cleared_trial_during_push,
                )
            )
            snap_mm_early = None
            try:
                from config_scanner.software_compat import snapshot_ruleta_major_minor

                snap_mm_early = snapshot_ruleta_major_minor(build_info)
            except OSError:
                pass
            if (
                cleared_trial_during_push
                and previous_live_mm
                and snap_mm_early
                and previous_live_mm.strip() != snap_mm_early.strip()
            ):
                from roulette_trial import clear_ruleta_var_arhiv

                wiped = clear_ruleta_var_arhiv(dest_root)
                if wiped:
                    extra_notes.append(
                        "Cleared stale ruleta/var + arhiv after version transfer "
                        f"({len(wiped)} path(s); Play1/PaytableId schema rebuilds on next start)."
                    )

        paytable_skip_notes: list[str] = []
        if scope is not WriteScope.FULL_SOFTWARE:
            allowed, paytable_skip_notes = filter_incompatible_paytable_restore_paths(
                allowed, dest_root
            )
        full_like = scope in (
            WriteScope.FULL,
            WriteScope.FULL_SOFTWARE,
            WriteScope.NO_PAYTABLE,
        )
        if full_like:
            live_serial = read_machine_serial_from_target(resolved_target)
            snap_serial = (build_info.machine_serial or "").strip() or None
            if egm_serials_match(live_serial, snap_serial):
                licence_extra: list[str] = []
                content_root = snapshot_content_root(snapshot_dir)
                for entry in manifest.files:
                    rel = entry.relative_path.replace("\\", "/").strip("/")
                    if not is_licence_path(rel):
                        continue
                    if changed_norm is not None and rel not in changed_norm:
                        continue
                    archived = content_root / Path(rel) if content_root else None
                    if archived is None or not archived.is_file():
                        continue
                    licence_extra.append(entry.relative_path)
                if licence_extra:
                    allowed = list(dict.fromkeys([*allowed, *licence_extra]))

        if not allowed:
            raise ValueError(
                f"No files match write scope {scope.value!r} in snapshot {snapshot_name}."
                + (
                    " Narrowed to compare changes only."
                    if only_changed_relative_paths is not None
                    else ""
                )
            )

        pre_write = capture_pre_write_state(
            dest_root,
            manifest,
            allowed_paths=set(allowed),
        )

        restore = restore_manifest_files(
            snapshot_dir,
            manifest,
            resolved_target,
            relative_path_allow=set(allowed),
        )
        if restore.errors:
            sample = "; ".join(restore.errors[:3])
            extra = f" (+{len(restore.errors) - 3} more)" if len(restore.errors) > 3 else ""
            raise OSError(
                f"Wrote {restore.written_count} files but {len(restore.errors)} failed: {sample}{extra}"
            )
        if restore.written_count <= 0:
            missing = restore.missing_count
            raise OSError(
                f"Wrote 0 files for write scope {scope.value!r} in snapshot {snapshot_name}"
                + (f" ({missing} scoped path(s) missing from archive)." if missing else ".")
            )

        verify = verify_snapshot_restore(
            snapshot_dir,
            manifest,
            dest_root,
            written_paths=restore.written_paths,
            pre_write=pre_write,
        )
        if not verify.ok:
            sample = "; ".join(verify.errors[:3])
            extra = f" (+{len(verify.errors) - 3} more)" if len(verify.errors) > 3 else ""
            raise OSError(
                "Post-write verification failed after restore: "
                f"{sample}{extra}"
            )

        from config_scanner.ruleta_compat import apply_ruleta_compat_after_restore
        from config_scanner.software_compat import snapshot_ruleta_major_minor
        from roulette_trial import restore_trial_bind_for_snapshot

        snap_mm = snapshot_ruleta_major_minor(build_info)
        trial_notes, trial_restored = restore_trial_bind_for_snapshot(
            dest_root,
            snapshot_dir,
            allow_gci_backup=not is_revert and not cleared_trial_during_push,
            snapshot_major_minor=snap_mm,
            previous_live_major_minor=previous_live_mm,
        )
        compat_notes = apply_ruleta_compat_after_restore(
            dest_root,
            snapshot_major_minor=snap_mm,
            previous_live_major_minor=previous_live_mm,
            skip_trial_reset=is_revert or trial_restored,
        )

        transfer_note: list[str] = []
        if (
            cleared_trial_during_push
            and previous_live_mm
            and snap_mm
            and previous_live_mm.strip() != snap_mm.strip()
            and not trial_restored
        ):
            transfer_note.append(
                "Trial bind cleared for Ruleta "
                f"{previous_live_mm} -> {snap_mm}. After stack start expect ERROR 99 "
                "once — enter the trial password for the displayed System ID (LLAVE)."
            )

        notes = tuple(
            paytable_skip_notes + extra_notes + compat_notes + trial_notes + transfer_note
        )

        return ApplySnapshotResult(
            snapshot_name=snapshot_name,
            target=resolved_target,
            profile_label=build_info.profile_label or dest_profile.label if dest_profile else "",
            written_count=restore.written_count,
            missing_count=restore.missing_count,
            errors=restore.errors,
            write_scope=scope.value,
            skipped_count=restore.skipped_count + len(paytable_skip_notes),
            scoped_file_count=len(allowed),
            verify_ok=verify.ok,
            protected_verified=verify.protected_checked,
            written_verified=verify.written_checked,
            verify_errors=verify.errors,
            notes=notes,
        )

    def _apply_binaries_only_to_target(
        self,
        *,
        snapshot_name: str,
        snapshot_dir: Path,
        manifest: object,
        build_info: object,
        dest_profile: object,
        resolved_target: str,
        dest_root: Path,
        candidates: list[str],
        is_revert: bool = False,
    ) -> ApplySnapshotResult:
        """Push Ruleta binaries; keep this cabinet's setup/SAS/wheel/licence."""
        from config_scanner.paytable_compat import (
            is_10_2_only_paytable_json,
            live_exe_is_ruleta_10_2,
            live_ruleta_major_minor,
        )
        from config_scanner.ruleta_compat import apply_ruleta_compat_after_restore
        from config_scanner.software_compat import import_ruleta_binaries_keep_profile
        from config_scanner.write_scope import WriteScope

        previous_live_mm = live_ruleta_major_minor(dest_root)
        cleared_trial_during_push = not is_revert
        extra_notes = import_ruleta_binaries_keep_profile(
            build_info,
            resolved_target,
            snapshot_dir=snapshot_dir,
            tool_root=self.root,
            clear_trial_tokens=cleared_trial_during_push,
        )
        json_written = 0
        json_errors: tuple[str, ...] = ()
        if live_exe_is_ruleta_10_2(live_ruleta_major_minor(dest_root)):
            json_allow = [path for path in candidates if is_10_2_only_paytable_json(path)]
            if json_allow:
                restore = restore_manifest_files(
                    snapshot_dir,
                    manifest,
                    resolved_target,
                    relative_path_allow=set(json_allow),
                )
                json_written = restore.written_count
                json_errors = restore.errors
                extra_notes.append(
                    f"copied {json_written} 10.2 paytable JSON file(s) for the live exe"
                )
        from config_scanner.software_compat import snapshot_ruleta_major_minor
        from roulette_trial import restore_trial_bind_for_snapshot

        snap_mm = snapshot_ruleta_major_minor(build_info)
        trial_notes, trial_restored = restore_trial_bind_for_snapshot(
            dest_root,
            snapshot_dir,
            allow_gci_backup=not is_revert and not cleared_trial_during_push,
            snapshot_major_minor=snap_mm,
            previous_live_major_minor=previous_live_mm,
        )
        compat_notes = apply_ruleta_compat_after_restore(
            dest_root,
            snapshot_major_minor=snap_mm,
            previous_live_major_minor=previous_live_mm,
            skip_trial_reset=is_revert or trial_restored,
        )

        notes = tuple(extra_notes + compat_notes + trial_notes)
        profile_label = ""
        snap_label = getattr(build_info, "profile_label", None)
        dest_label = getattr(dest_profile, "label", None)
        if snap_label:
            profile_label = str(snap_label)
        elif dest_label:
            profile_label = str(dest_label)
        return ApplySnapshotResult(
            snapshot_name=snapshot_name,
            target=resolved_target,
            profile_label=profile_label,
            written_count=max(1, json_written),
            missing_count=0,
            errors=json_errors,
            write_scope=WriteScope.BINARIES_ONLY.value,
            skipped_count=0,
            scoped_file_count=0,
            verify_ok=not json_errors,
            notes=notes,
        )

    def clear_error30_leftovers(self, scan_target: str | None = None) -> list[str]:
        """Remove foreign XML plus persistent trial tokens; keep live WIBU."""
        from config_scanner.build_version import scan_target_path
        from config_scanner.machine_identity import clear_error30_licence_leftovers
        from roulette_trial import clear_trial_persistent

        resolved = self.prepare_for_target(scan_target)
        dest = scan_target_path(resolved)
        removed = clear_error30_licence_leftovers(dest)
        removed.extend(clear_trial_persistent(dest))
        return removed

    def read_llave_challenge(self, scan_target: str | None = None):
        from config_scanner.llave_bind import read_llave_challenge

        return read_llave_challenge(self.prepare_for_target(scan_target))

    def save_trial_password(self, scan_target: str | None, password: str) -> str:
        from config_scanner.llave_bind import save_trial_password

        path = save_trial_password(self.prepare_for_target(scan_target), password)
        return str(path)

    def run_llave_auto_enter(self, scan_target: str | None = None) -> tuple[bool, str]:
        from config_scanner.llave_bind import run_llave_auto_enter

        return run_llave_auto_enter(self.prepare_for_target(scan_target))

    def prepare_seamless_trial_transfer(
        self,
        scan_target: str | None,
        *,
        snapshot_name: str | None = None,
    ) -> tuple[bool, str]:
        from config_scanner.build_version import scan_target_path
        from config_scanner.scanner import load_build_info
        from config_scanner.cabinet_trial_prep import prepare_cabinet_for_seamless_transfer
        from config_scanner.paths import snapshots_path

        resolved = self.prepare_for_target(scan_target)
        build_info = None
        if snapshot_name:
            snap_dir = snapshots_path(self.root) / snapshot_name
            if snap_dir.is_dir():
                build_info = load_build_info(snap_dir)
        serial = (build_info.machine_serial if build_info else None) or None
        snap_dir = snapshots_path(self.root) / snapshot_name if snapshot_name else None
        from roulette_trial import snapshot_should_auto_enter_llave

        return prepare_cabinet_for_seamless_transfer(
            resolved,
            machine_serial=serial,
            tool_root=self.root,
            sync_password=snapshot_should_auto_enter_llave(snap_dir),
        )

    def ensure_llave_after_stack_start(
        self,
        scan_target: str | None,
        *,
        snapshot_name: str | None = None,
    ) -> tuple[bool, str]:
        from config_scanner.scanner import load_build_info
        from config_scanner.cabinet_trial_prep import ensure_llave_bound_after_start
        from config_scanner.paths import snapshots_path

        resolved = self.prepare_for_target(scan_target)
        serial = None
        if snapshot_name:
            snap_dir = snapshots_path(self.root) / snapshot_name
            if snap_dir.is_dir():
                serial = load_build_info(snap_dir).machine_serial
        return ensure_llave_bound_after_start(
            resolved,
            machine_serial=serial,
            tool_root=self.root,
        )

    def apply_archived_file_to_target(
        self,
        scan_target: str,
        snapshot_name: str,
        relative_path: str,
    ) -> ApplyFileResult:
        """Write one archived snapshot file onto the live scan target (create if missing)."""
        from config_scanner.machine_identity import is_licence_path
        from config_scanner.write_scope import is_protected_write_path

        if is_protected_write_path(relative_path) and not is_licence_path(relative_path):
            from config_scanner.write_scope import protected_write_block_reason

            reason = protected_write_block_reason(relative_path) or (
                "Refusing to write protected path from Config Scanner."
            )
            raise ValueError(reason)

        resolved_target = self.prepare_for_target(scan_target)
        snapshot_dir = snapshots_path(self.root) / snapshot_name
        if not snapshot_dir.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

        build_info = load_build_info(snapshot_dir)
        snapshot_profile = (build_info.profile_id or "").strip()
        dest_profile = match_profile_for_target(resolved_target, load_profiles())
        if snapshot_profile and dest_profile and snapshot_profile != dest_profile.id:
            left = build_info.profile_label or snapshot_profile
            right = dest_profile.label or dest_profile.id
            raise ValueError(
                f"Cannot write file: profile mismatch ({left!r} snapshot vs {right!r} target)."
            )

        from config_scanner.build_version import scan_target_path as _scan_target_path
        from config_scanner.paytable_compat import (
            is_10_2_only_paytable_json,
            live_exe_is_ruleta_10_2,
            live_ruleta_major_minor,
        )
        from config_scanner.ruleta_compat import apply_ruleta_compat_after_restore

        dest_root = _scan_target_path(resolved_target)
        if is_10_2_only_paytable_json(relative_path) and not live_exe_is_ruleta_10_2(
            live_ruleta_major_minor(dest_root)
        ):
            raise ValueError(
                "Cannot restore 10.2-only paytable JSON onto this Ruleta.exe; "
                "Config Scanner remaps that name after a full restore instead."
            )

        restore_single_archived_file(snapshot_dir, relative_path, resolved_target)
        apply_ruleta_compat_after_restore(dest_root)
        return ApplyFileResult(
            relative_path=relative_path,
            snapshot_name=snapshot_name,
            target=resolved_target,
            profile_label=dest_profile.label if dest_profile else (
                build_info.profile_label or ""
            ),
        )

    def apply_content_change_to_target(
        self,
        scan_target: str,
        relative_path: str,
        change: ContentChange,
        side: str,
    ) -> ApplyChangeResult:
        """Write a single XML field from a compare diff to the live scan target."""
        resolved_target = self.prepare_for_target(scan_target)
        live_root = scan_target_path(resolved_target)
        live_path = safe_join_under(live_root, relative_path)

        if not live_path.is_file():
            raise FileNotFoundError(f"Live config file not found:\n{live_path}")

        if not relative_path.lower().endswith(".xml"):
            raise ValueError(
                f"Per-field apply supports XML files only, not {relative_path}"
            )

        if Path(relative_path).name.casefold() == "texts.xml":
            raise ValueError(
                "Per-field apply is not supported for texts.xml keyed strings yet"
            )

        if change.path.startswith("line:"):
            raise ValueError(
                "Per-field apply is not supported for line-based text diffs"
            )

        from config_scanner.write_scope import is_protected_write_path
        from config_scanner.machine_identity import (
            is_protected_identity_field,
            protected_identity_field_reason,
        )

        if is_protected_write_path(relative_path):
            from config_scanner.write_scope import protected_write_block_reason

            reason = protected_write_block_reason(relative_path) or (
                "Blocked from Config Scanner write."
            )
            raise ValueError(reason)

        if is_protected_identity_field(change.path, relative_path):
            raise ValueError(protected_identity_field_reason(change.path))

        value = resolve_apply_value(change, side)
        if value is None:
            raise ValueError(
                f"No value to apply for {side} side of {change.path}"
            )

        # Live application/ruleta/setup.xml is gcxml ciphertext — decrypt, patch, re-encrypt.
        from config_scanner.xml_diff import is_encrypted_origin_config_path

        if is_encrypted_origin_config_path(relative_path):
            try:
                from ai_helper.gcxml_decrypt import apply_value_to_live_ruleta_setup
            except ImportError as exc:
                raise OSError(
                    "gcxml helpers unavailable; cannot Write encrypted ruleta setup.xml"
                ) from exc
            apply_value_to_live_ruleta_setup(
                live_path,
                change.path,
                value,
                apply_xml_fn=apply_xml_value_at_path,
            )
        else:
            apply_xml_value_at_path(live_path, change.path, value)

        dest_profile = match_profile_for_target(resolved_target, load_profiles())
        return ApplyChangeResult(
            relative_path=relative_path,
            flat_path=change.path,
            value=value,
            target=resolved_target,
            profile_label=dest_profile.label if dest_profile else "",
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
