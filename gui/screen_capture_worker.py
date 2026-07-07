"""Run ``capture_remote_screen`` off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class ScreenCaptureEmitter(QObject):
    finished = Signal(bool, str)
    """``success``, ``local_path`` on success or ``error_message`` on failure."""


class _ScreenCaptureRunnable(QRunnable):
    def __init__(self, ip: str, local_path: str, emitter: ScreenCaptureEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._local_path = local_path
        self._emitter = emitter

    def run(self) -> None:
        from network.screen_capture import capture_remote_screen

        ok, msg = capture_remote_screen(self._ip, self._local_path)
        self._emitter.finished.emit(ok, msg)


def schedule_remote_screen_capture(
    pool: QThreadPool,
    ip: str,
    local_path: str,
    emitter: ScreenCaptureEmitter,
) -> None:
    pool.start(_ScreenCaptureRunnable(ip, local_path, emitter))
