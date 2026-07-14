"""Background scan/compare jobs for the Config Scanner tab."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from config_scanner.service import ApplySnapshotResult, CompareResult, ConfigScannerService, ScanResult, SnapshotInfo


class ConfigScannerEmitter(QObject):
    progress = Signal(str)
    snapshots_loaded = Signal(object)
    snapshots_load_failed = Signal(str)
    scan_finished = Signal(bool, object, str)
    compare_finished = Signal(bool, object, str)
    baseline_finished = Signal(bool, object, str)
    delete_finished = Signal(bool, object, str)
    apply_finished = Signal(bool, object, str)


@dataclass(frozen=True)
class SnapshotLoadResult:
    snapshots: list[SnapshotInfo]
    baseline_name: str | None


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
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._game_drive = game_drive
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._emitter.progress.emit(f"Scanning config on {self._game_drive} …")
            result = self._service.run_scan(self._game_drive)
            self._emitter.scan_finished.emit(
                True,
                result,
                (
                    f"Snapshot saved: {result.snapshot_name} "
                    f"({result.manifest_file_count} files in {result.elapsed_seconds}s)"
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
            result = self._service.run_compare(self._baseline, self._target)
            warning_note = ""
            if result.warnings:
                warning_note = f" warnings={len(result.warnings)}"
            self._emitter.compare_finished.emit(
                True,
                result,
                (
                    f"Report saved: {result.report_path.name}"
                    f"{warning_note}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.compare_finished.emit(False, None, str(exc))


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
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._service = service
        self._snapshot_name = snapshot_name
        self._scan_target = scan_target
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._emitter.progress.emit(
                f"Writing snapshot {self._snapshot_name} to {self._scan_target} …"
            )
            result = self._service.apply_snapshot_to_target(
                self._snapshot_name,
                self._scan_target,
            )
            note = ""
            if result.missing_count:
                note = f" ({result.missing_count} archived paths missing, skipped)"
            self._emitter.apply_finished.emit(
                True,
                result,
                (
                    f"Wrote {result.written_count} files to {result.target}"
                    f"{note}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._emitter.apply_finished.emit(False, None, str(exc))


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
) -> None:
    pool.start(_ScanRunnable(service, game_drive, emitter))


def schedule_compare(
    pool: QThreadPool,
    service: ConfigScannerService,
    baseline: str,
    target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_CompareRunnable(service, baseline, target, emitter))


def schedule_delete_snapshot(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_DeleteSnapshotRunnable(service, snapshot_name, emitter))


def schedule_apply_snapshot(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    scan_target: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_ApplySnapshotRunnable(service, snapshot_name, scan_target, emitter))


def schedule_set_baseline(
    pool: QThreadPool,
    service: ConfigScannerService,
    snapshot_name: str,
    emitter: ConfigScannerEmitter,
) -> None:
    pool.start(_SetBaselineRunnable(service, snapshot_name, emitter))
