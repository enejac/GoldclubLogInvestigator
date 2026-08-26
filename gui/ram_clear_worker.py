"""Run RAM clear off the GUI thread."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from network.ram_clear import RamClearPlan


class RamClearEmitter(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)
    """``success``, ``message`` (log excerpt or error)."""


class _RamClearRunnable(QRunnable):
    def __init__(
        self,
        *,
        local: bool,
        ip: str | None,
        plan: RamClearPlan | None,
        cabinet_label: str | None,
        emitter: RamClearEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._local = local
        self._ip = ip
        self._plan = plan
        self._cabinet_label = (cabinet_label or "").strip()
        self._emitter = emitter

    def run(self) -> None:
        from network.ram_clear import run_ram_clear_local, run_ram_clear_remote

        stop = threading.Event()
        poller: threading.Thread | None = None
        try:
            if self._local:
                self._emitter.progress.emit("RAM Clear running locally…")
                ok, msg = run_ram_clear_local(self._plan)
            else:
                ip = (self._ip or "").strip()
                label = self._cabinet_label or ip
                status_unc = rf"\\{ip}\c$\Windows\Temp\LogInvestigator-RamClear.status"

                def _poll() -> None:
                    last = ""
                    while not stop.wait(1.5):
                        try:
                            text = Path(status_unc).read_text(encoding="utf-8", errors="replace")
                            line = (text or "").strip().splitlines()
                            if not line:
                                continue
                            cur = line[-1].strip()
                            if cur and cur != last:
                                last = cur
                                self._emitter.progress.emit(cur)
                        except OSError:
                            continue

                poller = threading.Thread(target=_poll, name="ram-clear-status", daemon=True)
                poller.start()
                self._emitter.progress.emit(f"RAM Clear starting on {label}…")
                ok, msg = run_ram_clear_remote(
                    ip,
                    cabinet_label=label,
                    progress_cb=lambda m: self._emitter.progress.emit(m),
                )
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        finally:
            stop.set()
            if poller is not None:
                poller.join(timeout=2.0)
        self._emitter.finished.emit(ok, msg)


def schedule_ram_clear(
    pool: QThreadPool,
    *,
    local: bool,
    ip: str | None,
    plan: RamClearPlan | None,
    emitter: RamClearEmitter,
    cabinet_label: str | None = None,
) -> None:
    pool.start(
        _RamClearRunnable(
            local=local,
            ip=ip,
            plan=plan,
            cabinet_label=cabinet_label,
            emitter=emitter,
        )
    )
