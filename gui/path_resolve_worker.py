"""
Off-thread ``resolve_scan_path`` for LED updates and pre-scan validation (avoids UI hangs on UNC).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from config import ResolvedScanPath, resolve_scan_path


class PathResolveEmitter(QObject):
    """Lives on the GUI thread; ``finished`` may be emitted from a pool thread."""

    finished = Signal(object, int)


class _PathResolveRunnable(QRunnable):
    def __init__(
        self,
        target_ip: str | None,
        local_path: str | None,
        remote_mode: bool,
        seq: int,
        emitter: PathResolveEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._target_ip = target_ip
        self._local_path = local_path
        self._remote_mode = remote_mode
        self._seq = seq
        self._emitter = emitter

    def run(self) -> None:
        r = resolve_scan_path(
            self._target_ip,
            local_path=self._local_path,
            remote_mode=self._remote_mode,
        )
        self._emitter.finished.emit(r, self._seq)


def schedule_path_resolve(
    pool: QThreadPool,
    target_ip: str | None,
    local_path: str | None,
    remote_mode: bool,
    seq: int,
    emitter: PathResolveEmitter,
) -> None:
    pool.start(
        _PathResolveRunnable(
            target_ip, local_path, remote_mode, seq, emitter
        )
    )
