"""Live Bug Detector session worker (off GUI thread)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from network.bug_session_engine import BugSessionEngine, BugSessionResult, default_session_dir
from network.bug_session_events import SessionEvent
from network.bug_session_reports import event_to_html


class BugSessionThread(QThread):
    """Runs BugSessionEngine until stop / auto-critical."""

    progress = Signal(str)
    event_html = Signal(str)
    event_obj = Signal(object)
    finished_result = Signal(object)

    def __init__(
        self,
        cabinet: str,
        *,
        enable_sniff: bool = True,
        auto_stop_on_critical: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cabinet = cabinet
        self._enable_sniff = enable_sniff
        self._auto_stop = auto_stop_on_critical
        self._engine: BugSessionEngine | None = None

    def request_stop(self) -> None:
        if self._engine:
            self._engine.request_stop("user_stop")

    def run(self) -> None:
        out = default_session_dir(self._cabinet)

        def on_progress(msg: str) -> None:
            self.progress.emit(msg)

        def on_event(ev: SessionEvent) -> None:
            self.event_obj.emit(ev)
            self.event_html.emit(event_to_html(ev))

        engine = BugSessionEngine(
            self._cabinet,
            output_dir=out,
            enable_sniff=self._enable_sniff,
            auto_stop_on_critical=self._auto_stop,
            progress=on_progress,
            on_event=on_event,
        )
        self._engine = engine
        try:
            result = engine.run_until_stopped()
        except Exception as e:  # noqa: BLE001
            result = BugSessionResult(
                ok=False,
                cabinet=self._cabinet,
                log=str(e),
                stopped_reason="error",
                output_dir=str(out),
            )
        self.finished_result.emit(result)


class BugDetectorEmitter(QObject):
    progress = Signal(str)
    finished = Signal(bool, str, object)
    """success, log text, BugDetectorResult | None."""


class _BugDetectorRunnable(QRunnable):
    def __init__(
        self,
        *,
        cabinet: str,
        output_dir: str | None,
        emitter: BugDetectorEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._cabinet = cabinet
        self._output_dir = output_dir
        self._emitter = emitter

    def run(self) -> None:
        try:
            from network.bug_detector import scan_and_write_reports

            def on_progress(msg: str) -> None:
                self._emitter.progress.emit(msg)

            result = scan_and_write_reports(
                self._cabinet,
                output_dir=Path(self._output_dir) if self._output_dir else None,
                progress=on_progress,
            )
            self._emitter.finished.emit(result.ok, result.log, result)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e), None)


def schedule_bug_detector_scan(
    pool: QThreadPool,
    *,
    cabinet: str,
    emitter: BugDetectorEmitter,
    output_dir: str | None = None,
) -> None:
    pool.start(
        _BugDetectorRunnable(
            cabinet=cabinet,
            output_dir=output_dir,
            emitter=emitter,
        )
    )
