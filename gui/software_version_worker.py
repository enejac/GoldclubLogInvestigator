"""Run software version swap off the GUI thread."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class SoftwareVersionEmitter(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)
    """``success``, log text or error."""


class _SoftwareVersionRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        source_ruleta: Path,
        dest_unc: str | None,
        dry_run: bool,
        skip_kill: bool,
        skip_launch: bool,
        emitter: SoftwareVersionEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._source = source_ruleta
        self._dest_unc = dest_unc
        self._dry_run = dry_run
        self._skip_kill = skip_kill
        self._skip_launch = skip_launch
        self._emitter = emitter

    def run(self) -> None:
        try:
            from network.software_version_swap import run_swap

            def on_progress(msg: str) -> None:
                self._emitter.progress.emit(msg)

            result = run_swap(
                self._ip,
                self._source,
                dest_unc=self._dest_unc or None,
                skip_kill=self._skip_kill,
                skip_launch=self._skip_launch,
                dry_run=self._dry_run,
                progress=on_progress,
            )
            self._emitter.finished.emit(result.ok, result.log)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


def schedule_software_version_swap(
    pool: QThreadPool,
    *,
    ip: str,
    source_ruleta: Path,
    emitter: SoftwareVersionEmitter,
    dest_unc: str | None = None,
    dry_run: bool = False,
    skip_kill: bool = False,
    skip_launch: bool = False,
) -> None:
    pool.start(
        _SoftwareVersionRunnable(
            ip=ip,
            source_ruleta=source_ruleta,
            dest_unc=dest_unc,
            dry_run=dry_run,
            skip_kill=skip_kill,
            skip_launch=skip_launch,
            emitter=emitter,
        )
    )
