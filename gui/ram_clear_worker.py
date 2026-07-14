"""Run RAM clear off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from network.ram_clear import RamClearPlan, run_ram_clear_local, run_ram_clear_remote


class RamClearEmitter(QObject):
    finished = Signal(bool, str)
    """``success``, ``message`` (log excerpt or error)."""


class _RamClearRunnable(QRunnable):
    def __init__(
        self,
        *,
        local: bool,
        ip: str | None,
        plan: RamClearPlan | None,
        emitter: RamClearEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._local = local
        self._ip = ip
        self._plan = plan
        self._emitter = emitter

    def run(self) -> None:
        if self._local:
            if self._plan is None:
                self._emitter.finished.emit(False, "No slot or roulette RAM-clear layout found locally.")
                return
            ok, msg = run_ram_clear_local(self._plan)
        else:
            ok, msg = run_ram_clear_remote(self._ip or "")
        self._emitter.finished.emit(ok, msg)


def schedule_ram_clear(
    pool: QThreadPool,
    *,
    local: bool,
    ip: str | None,
    plan: RamClearPlan | None,
    emitter: RamClearEmitter,
) -> None:
    pool.start(_RamClearRunnable(local=local, ip=ip, plan=plan, emitter=emitter))
