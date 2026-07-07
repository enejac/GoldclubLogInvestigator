"""
Background scan: enumerate log files and parse each on a dedicated QThread.

Keeps the GUI thread free for repaints and user input during deep UNC walks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from parser import parse_log_file_safe
from scanner import ScanRootError, collect_log_files


class ScanWorker(QThread):
    """Runs ``collect_log_files`` + per-file ``parse_log_file_safe`` off the GUI thread."""

    enumeration_done = Signal(int)
    """Total number of log files discovered."""

    file_progress = Signal(str, int, int)
    """Current file path, 1-based index, total files."""

    file_parsed = Signal(object)
    """``ParseResult`` (incidents + state_nodes) for the file just parsed."""

    scan_finished = Signal()
    scan_failed = Signal(str)

    def __init__(
        self,
        roots: list[str],
        parent: QObject | None = None,
        *,
        scan_start_time: datetime | None = None,
        scan_end_time: datetime | None = None,
    ) -> None:
        super().__init__(parent)
        self._roots = [str(Path(r)) for r in roots]
        self._scan_start_time = scan_start_time
        self._scan_end_time = scan_end_time

    def run(self) -> None:
        try:
            files = collect_log_files(self._roots)
        except ScanRootError as e:
            self.scan_failed.emit(str(e))
            return
        except Exception as e:  # noqa: BLE001 — surface to UI
            self.scan_failed.emit(str(e))
            return
        try:
            total = len(files)
            self.enumeration_done.emit(total)
            for i, fp in enumerate(files, start=1):
                if self.isInterruptionRequested():
                    break
                self.file_progress.emit(str(fp), i, total)
                result = parse_log_file_safe(
                    fp,
                    scan_start_time=self._scan_start_time,
                    scan_end_time=self._scan_end_time,
                )
                self.file_parsed.emit(result)
            self.scan_finished.emit()
        except Exception as e:  # noqa: BLE001 — surface to UI
            self.scan_failed.emit(str(e))
