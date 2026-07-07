"""Run :func:`fetch_accounting_data_with_tempfile` off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class AccountingScanEmitter(QObject):
    finished = Signal(str, str)
    """``qr_text``, ``state_text`` (errors are embedded as strings, not raised)."""


class _AccountingScanRunnable(QRunnable):
    def __init__(self, ip: str, emitter: AccountingScanEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._emitter = emitter

    def run(self) -> None:
        from network.accounting_scanner import fetch_accounting_data_with_tempfile

        try:
            qr_text, state_text = fetch_accounting_data_with_tempfile(self._ip)
        except Exception as e:  # noqa: BLE001 — isolate worker failures
            qr_text = f"Accounting scan failed: {e}"
            state_text = ""
        self._emitter.finished.emit(qr_text, state_text)


def schedule_accounting_scan(
    pool: QThreadPool,
    ip: str,
    emitter: AccountingScanEmitter,
) -> None:
    pool.start(_AccountingScanRunnable(ip, emitter))
