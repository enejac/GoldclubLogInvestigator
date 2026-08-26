"""Background scan/compare jobs for the Config Scanner tab."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from config_scanner.service import (
    ApplyChangeResult,
    ApplyFileResult,
    ApplySnapshotResult,
    CompareResult,
    ConfigScannerService,
    ScanResult,
    SnapshotInfo,
)
from config_scanner.xml_diff import ContentChange


class ConfigScannerEmitter(QObject):
    progress = Signal(str)
    snapshots_loaded = Signal(object)
    snapshots_load_failed = Signal(str)
    auto_detect_finished = Signal(bool, object, str, bool)
    scan_target_ready = Signal(str)
    scan_target_failed = Signal(str)
    scan_finished = Signal(bool, object, str)
    compare_finished = Signal(bool, object, str)
    stack_restart_finished = Signal(bool, str)
    baseline_finished = Signal(bool, object, str)
    delete_finished = Signal(bool, object, str)
    apply_finished = Signal(bool, object, str)
    apply_change_finished = Signal(bool, object, str)
    apply_file_finished = Signal(bool, object, str)
    target_validated = Signal(str, bool)  # path, valid


@dataclass(frozen=True)
class SnapshotLoadResult:
    snapshots: list[SnapshotInfo]
    baseline_name: str | None


class _AutoDetectRunnable(QRunnable):
    """Probe slot/roulette repos off the UI thread (can touch many drives / UNC paths)."""

    def __init__(
        self,
        service: ConfigScannerService,
        preferred: str | None,
        *,
        silent: bool,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._preferred = preferred
        self._silent = silent
        self._emitter = emitter

    def run(self) -> None:
        try:
            result = self._service.auto_detect_repo(self._preferred)
            self._emitter.auto_detect_finished.emit(True, result, "", self._silent)
        except FileNotFoundError as exc:
            self._emitter.auto_detect_finished.emit(False, None, str(exc), self._silent)
        except Exception as exc:  # noqa: BLE001
            self._emitter.auto_detect_finished.emit(False, None, str(exc), self._silent)


class _StartupAutoDetectRunnable(QRunnable):
    """On first show: skip when saved target is valid; otherwise auto-detect in background."""

    def __init__(
        self,
        service: ConfigScannerService,
        saved_target: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._saved_target = saved_target.strip()
        self._emitter = emitter

    def run(self) -> None:
        if self._saved_target and self._service.is_scan_target_valid(self._saved_target):
            self._emitter.target_validated.emit(self._saved_target, True)
            return
        if self._saved_target:
            self._emitter.target_validated.emit(self._saved_target, False)
        try:
            result = self._service.auto_detect_repo(self._saved_target or None)
            self._emitter.auto_detect_finished.emit(True, result, "", True)
        except FileNotFoundError as exc:
            self._emitter.progress.emit(f"Auto-detect: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._emitter.progress.emit(f"Auto-detect: {exc}")


class _ValidateTargetRunnable(QRunnable):
    """Check whether the typed scan target resolves to a known game repo."""

    def __init__(
        self,
        service: ConfigScannerService,
        raw_target: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._raw_target = raw_target.strip()
        self._emitter = emitter

    def run(self) -> None:
        valid = bool(self._raw_target) and self._service.is_scan_target_valid(self._raw_target)
        self._emitter.target_validated.emit(self._raw_target, valid)


class _PrepareScanTargetRunnable(QRunnable):
    """Validate scan target or auto-detect, then hand off to scan."""

    def __init__(
        self,
        service: ConfigScannerService,
        raw_target: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._raw_target = raw_target.strip()
        self._emitter = emitter

    def run(self) -> None:
        try:
            if self._raw_target and self._service.is_scan_target_valid(self._raw_target):
                self._emitter.scan_target_ready.emit(self._raw_target)
                return
            result = self._service.auto_detect_repo(self._raw_target or None)
            self._emitter.auto_detect_finished.emit(True, result, "", False)
            self._emitter.scan_target_ready.emit(result.target.rstrip("\\"))
        except FileNotFoundError as exc:
            self._emitter.scan_target_failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self._emitter.scan_target_failed.emit(str(exc))


class _LoadSnapshotsRunnable(QRunnable):
    def __init__(self, service: ConfigScannerService, emitter: ConfigScannerEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._emitter = emitter

    def run(self) -> None:
        try:
            snapshots = self._service.list_snapshots()
            payload = SnapshotLoadResult(
                snapshots=snapshots,
                baseline_name=self._service.get_baseline_name(),
            )
            self._emitter.snapshots_loaded.emit(payload)
        except Exception as exc:  # noqa: BLE001
            message = f"Failed to load snapshots: {exc}"
            self._emitter.progress.emit(message)
            self._emitter.snapshots_load_failed.emit(message)


class _ScanRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        game_drive: str,
        emitter: ConfigScannerEmitter,
        *,
        include_software: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._game_drive = game_drive
        self._emitter = emitter
        self._include_software = include_software

    def run(self) -> None:
        try:
            self._emitter.progress.emit(
                "Scanning config"
                + (" + software" if self._include_software else "")
                + f" on {self._game_drive} …"
            )
            result = self._service.run_scan(
                self._game_drive, include_software=self._include_software
            )
            extra = ""
            if result.software_captured:
                extra = f" + {result.software_file_count} software files"
            self._emitter.scan_finished.emit(
                True,
                result,
                (
                    f"Snapshot saved: {result.snapshot_name} "
                    f"({result.manifest_file_count} files in {result.elapsed_seconds}s)"
                    + extra
                    + (
                        f" — {result.profile_label}: {result.version_summary}"
                        if result.profile_label and result.version_summary
                        else (
                            f" — {result.profile_label or result.version_summary}"
                            if result.profile_label or result.version_summary
                            else ""
                        )
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.scan_finished.emit(False, None, str(exc))


class _CompareRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        baseline: str,
        target: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._baseline = baseline
        self._target = target
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._emitter.progress.emit(
                f"Comparing {self._baseline} vs {self._target} …"
            )
            started = time.monotonic()
            result = self._service.run_compare(self._baseline, self._target)
            elapsed = time.monotonic() - started
            warning_note = ""
            if result.warnings:
                warning_note = f" warnings={len(result.warnings)}"
            self._emitter.progress.emit(
                f"Compare finished in {elapsed:.1f}s "
                f"({result.summary.get('modified', 0)} modified, "
                f"{result.summary.get('added', 0)} added, "
                f"{result.summary.get('removed', 0)} removed)"
            )
            self._emitter.compare_finished.emit(
                True,
                result,
                (
                    f"Report saved: {result.report_path.name} ({elapsed:.1f}s)"
                    f"{warning_note}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.compare_finished.emit(False, None, str(exc))


class _StackRestartRunnable(QRunnable):
    def __init__(
        self,
        plan: object,
        emitter: ConfigScannerEmitter,
        *,
        phase: str = "restart",
        service: ConfigScannerService | None = None,
        scan_target: str = "",
        snapshot_name: str = "",
        trial_prep: bool = False,
        ensure_llave: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._plan = plan
        self._emitter = emitter
        self._phase = phase or "restart"
        self._service = service
        self._scan_target = scan_target.strip()
        self._snapshot_name = snapshot_name.strip()
        self._trial_prep = trial_prep
        self._ensure_llave = ensure_llave

    def run(self) -> None:
        from config_scanner.stack_restart import (
            run_full_stack_restart,
            run_stack_kill,
            run_stack_start,
        )

        try:
            if self._phase == "kill":
                if self._trial_prep and self._service and self._scan_target:
                    self._emitter.progress.emit(
                        "Preparing cabinet clock + trial tokens …"
                    )
                    ok_prep, prep_detail = self._service.prepare_seamless_trial_transfer(
                        self._scan_target,
                        snapshot_name=self._snapshot_name or None,
                    )
                    if ok_prep:
                        self._emitter.progress.emit(f"Trial prep OK: {prep_detail}")
                    else:
                        self._emitter.progress.emit(
                            f"Trial prep warning: {prep_detail}"
                        )
                self._emitter.progress.emit("Stopping GoldClub stack (Kill-All) …")
                ok, detail = run_stack_kill(self._plan)
            elif self._phase == "start":
                self._emitter.progress.emit("Starting GoldClub stack (Run-FullStack) …")
                ok, detail = run_stack_start(
                    self._plan,
                    scan_target=self._scan_target or None,
                    ensure_llave=self._ensure_llave,
                    machine_serial=None,
                    tool_root=self._service.root if self._service else None,
                )
            else:
                self._emitter.progress.emit(
                    "Restarting GoldClub stack (Kill-All + Run-FullStack) …"
                )
                ok, detail = run_full_stack_restart(self._plan)
            self._emitter.stack_restart_finished.emit(ok, detail)
        except Exception as exc:  # noqa: BLE001
            self._emitter.stack_restart_finished.emit(False, str(exc))


class _DeleteSnapshotRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        snapshot_name: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._snapshot_name = snapshot_name
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._emitter.progress.emit(f"Deleting snapshot {self._snapshot_name} …")
            self._service.delete_snapshot(self._snapshot_name)
            self._emitter.delete_finished.emit(
                True,
                self._snapshot_name,
                f"Deleted snapshot: {self._snapshot_name}",
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.delete_finished.emit(False, None, str(exc))


class _ApplySnapshotRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        snapshot_name: str,
        scan_target: str,
        emitter: ConfigScannerEmitter,
        *,
        write_scope: str = "full",
        only_changed_relative_paths: list[str] | tuple[str, ...] | None = None,
        is_revert: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._snapshot_name = snapshot_name
        self._scan_target = scan_target
        self._emitter = emitter
        self._write_scope = write_scope
        self._only_changed = only_changed_relative_paths
        self._is_revert = is_revert

    def run(self) -> None:
        try:
            scope = self._write_scope or "full"
            self._emitter.progress.emit(
                f"Writing {scope} from {self._snapshot_name} to {self._scan_target} …"
            )
            result = self._service.apply_snapshot_to_target(
                self._snapshot_name,
                self._scan_target,
                write_scope=scope,
                only_changed_relative_paths=self._only_changed,
                is_revert=self._is_revert,
            )
            note = ""
            if result.missing_count:
                note = f" ({result.missing_count} archived paths missing, skipped)"
            scope_note = ""
            if result.write_scope and result.write_scope != "full":
                scope_note = f" [{result.write_scope}]"
            elif result.write_scope == "full":
                scope_note = " [full HW+SW]"
            skip_note = ""
            if result.skipped_count:
                skip_note = f"; skipped {result.skipped_count} out-of-scope"
            verify_note = ""
            if getattr(result, "verify_ok", False):
                verify_note = (
                    f"; verified {result.written_verified} written + "
                    f"{result.protected_verified} protected unchanged"
                )
            extra_notes = ""
            notes = getattr(result, "notes", ()) or ()
            if notes:
                extra_notes = "; " + "; ".join(notes)
            self._emitter.apply_finished.emit(
                True,
                result,
                (
                    f"Wrote {result.written_count} files{scope_note} to {result.target}"
                    f"{note}{skip_note}{verify_note}{extra_notes}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.apply_finished.emit(False, None, str(exc))


class _ApplyChangeRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        scan_target: str,
        relative_path: str,
        change: ContentChange,
        side: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._scan_target = scan_target
        self._relative_path = relative_path
        self._change = change
        self._side = side
        self._emitter = emitter

    def run(self) -> None:
        try:
            from config_scanner.report import setting_display_name

            setting = setting_display_name(self._change.path)
            self._emitter.progress.emit(
                f"Applying {setting} ({self._side}) to {self._scan_target} …"
            )
            result = self._service.apply_content_change_to_target(
                self._scan_target,
                self._relative_path,
                self._change,
                self._side,
            )
            self._emitter.apply_change_finished.emit(
                True,
                result,
                (
                    f"Applied {setting}={result.value!r} to "
                    f"{result.relative_path} on {result.target}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.apply_change_finished.emit(False, None, str(exc))


class _SetBaselineRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        snapshot_name: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._snapshot_name = snapshot_name
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._emitter.progress.emit(f"Setting baseline from {self._snapshot_name} …")
            new_name = self._service.set_baseline_snapshot(self._snapshot_name)
            self._emitter.baseline_finished.emit(
                True,
                new_name,
                f"Baseline set to {new_name}",
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.baseline_finished.emit(False, None, str(exc))


def schedule_auto_detect(
    pool: QThreadPool,
    service: ConfigScannerService,
    preferred: str | None,
    emitter: ConfigScannerEmitter,
    *,
    silent: bool,
) -> None:
    pool.start(_AutoDetectRunnable(service, preferred, silent=silent, emitter=emitter))


def schedule_startup_auto_detect(
    pool: QThreadPool,
    service: ConfigScannerService,
    saved_target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_StartupAutoDetectRunnable(service, saved_target, emitter))


def schedule_validate_scan_target(
    pool: QThreadPool,
    service: ConfigScannerService,
    raw_target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_ValidateTargetRunnable(service, raw_target, emitter))


def schedule_prepare_scan_target(
    pool: QThreadPool,
    service: ConfigScannerService,
    raw_target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_PrepareScanTargetRunnable(service, raw_target, emitter))


def schedule_load_snapshots(
    pool: QThreadPool,
    service: ConfigScannerService,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_LoadSnapshotsRunnable(service, emitter))


def schedule_scan(
    pool: QThreadPool,
    service: ConfigScannerService,
    game_drive: str,
    emitter: ConfigScannerEmitter,
    *,
    include_software: bool = False,
) -> None:
    pool.start(
        _ScanRunnable(
            service, game_drive, emitter, include_software=include_software
        )
    )


def schedule_compare(
    pool: QThreadPool,
    service: ConfigScannerService,
    baseline: str,
    target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_CompareRunnable(service, baseline, target, emitter))


def schedule_stack_restart(
    pool: QThreadPool,
    plan: object,
    emitter: ConfigScannerEmitter,
    *,
    phase: str = "restart",
    service: ConfigScannerService | None = None,
    scan_target: str = "",
    snapshot_name: str = "",
    trial_prep: bool = False,
    ensure_llave: bool = False,
) -> None:
    pool.start(
        _StackRestartRunnable(
            plan,
            emitter,
            phase=phase,
            service=service,
            scan_target=scan_target,
            snapshot_name=snapshot_name,
            trial_prep=trial_prep,
            ensure_llave=ensure_llave,
        )
    )


def schedule_delete_snapshot(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_DeleteSnapshotRunnable(service, snapshot_name, emitter))


class _ApplyFileRunnable(QRunnable):
    def __init__(
        self,
        service: ConfigScannerService,
        scan_target: str,
        snapshot_name: str,
        relative_path: str,
        emitter: ConfigScannerEmitter,
    ) -> None:
        super().__init__()
        self._service = service
        self._scan_target = scan_target
        self._snapshot_name = snapshot_name
        self._relative_path = relative_path
        self._emitter = emitter

    def run(self) -> None:  # noqa: D401
        try:
            self._emitter.progress.emit(
                f"Writing file {self._relative_path} from {self._snapshot_name} …"
            )
            result = self._service.apply_archived_file_to_target(
                self._scan_target,
                self._snapshot_name,
                self._relative_path,
            )
            self._emitter.apply_file_finished.emit(
                True,
                result,
                f"Wrote {result.relative_path} to {result.target}",
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.apply_file_finished.emit(False, None, str(exc))


def schedule_apply_snapshot(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    scan_target: str,
    emitter: ConfigScannerEmitter,
    *,
    write_scope: str = "full",
    only_changed_relative_paths: list[str] | tuple[str, ...] | None = None,
    is_revert: bool = False,
) -> None:
    pool.start(
        _ApplySnapshotRunnable(
            service,
            snapshot_name,
            scan_target,
            emitter,
            write_scope=write_scope,
            only_changed_relative_paths=only_changed_relative_paths,
            is_revert=is_revert,
        )
    )


def schedule_apply_content_change(
    pool: QThreadPool,
    service: ConfigScannerService,
    scan_target: str,
    relative_path: str,
    change: ContentChange,
    side: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(
        _ApplyChangeRunnable(
            service,
            scan_target,
            relative_path,
            change,
            side,
            emitter,
        )
    )


def schedule_set_baseline(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_SetBaselineRunnable(service, snapshot_name, emitter))


def schedule_apply_archived_file(
    pool: QThreadPool,
    service: ConfigScannerService,
    scan_target: str,
    snapshot_name: str,
    relative_path: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(
        _ApplyFileRunnable(
            service,
            scan_target,
            snapshot_name,
            relative_path,
            emitter,
        )
    )
