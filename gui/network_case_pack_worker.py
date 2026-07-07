"""Run :func:`network.case_packer.create_case_pack` off the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from parser import Incident


class _CasePackWorkerSignals(QObject):
    success = Signal(str)
    error = Signal(str)


class _NetworkCasePackWorker(QRunnable):
    def __init__(
        self,
        target_path: str,
        machine_info: dict[str, Any],
        bookmarks: dict[str, str],
        incidents: list[Incident],
        screenshot_path: str | None,
        remote_ip: str | None,
        signals: _CasePackWorkerSignals,
        sas_file_path: str | None = None,
        software_version: str | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._target_path = target_path
        self._machine_info = machine_info
        self._bookmarks = bookmarks
        self._incidents = incidents
        self._screenshot_path = screenshot_path
        self._remote_ip = remote_ip
        self._sas_file_path = sas_file_path
        self._software_version = software_version
        self._signals = signals

    def run(self) -> None:
        from network.case_packer import create_case_pack

        try:
            zip_path = create_case_pack(
                self._target_path,
                self._machine_info,
                self._bookmarks,
                self._incidents,
                screenshot_path=self._screenshot_path,
                remote_ip=self._remote_ip,
                sas_file_path=self._sas_file_path,
                software_version=self._software_version,
            )
            self._signals.success.emit(zip_path)
        except Exception as e:  # noqa: BLE001
            self._signals.error.emit(str(e))
