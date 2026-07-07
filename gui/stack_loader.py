"""Load log context around an incident line on a thread pool (non-blocking UI)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class StackTraceEmitter(QObject):
    """Must live on the GUI thread."""

    loaded = Signal(int, str)


class _StackTraceRunnable(QRunnable):
    def __init__(
        self,
        path: Path,
        line_number: int,
        seq: int,
        emitter: StackTraceEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._line_number = line_number
        self._seq = seq
        self._emitter = emitter

    def run(self) -> None:
        from parser import read_log_context

        text = read_log_context(self._path, self._line_number)
        self._emitter.loaded.emit(self._seq, text)


def schedule_stack_load(
    pool: QThreadPool,
    path: Path,
    line_number: int,
    seq: int,
    emitter: StackTraceEmitter,
) -> None:
    pool.start(_StackTraceRunnable(path, line_number, seq, emitter))
