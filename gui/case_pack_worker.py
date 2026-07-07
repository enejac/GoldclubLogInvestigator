"""Run ``CasePacker`` / ``build_case_zip`` off the GUI thread."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

if TYPE_CHECKING:
    from parser import Incident


class CasePackEmitter(QObject):
    finished = Signal(bool, str)
    """``success``, human-readable message (path or error)."""


class _CasePackRunnable(QRunnable):
    def __init__(
        self,
        output_zip: Path,
        incidents: list[Incident],
        scan_path: str,
        target_ip: str | None,
        remote_mode: bool,
        user_notes: str,
        app_version: str | None,
        validation_summary: dict | None,
        machine_registry: dict | None,
        session_active_duration: str | None,
        emitter: CasePackEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._output_zip = output_zip
        self._incidents = incidents
        self._scan_path = scan_path
        self._target_ip = target_ip
        self._remote_mode = remote_mode
        self._user_notes = user_notes
        self._app_version = app_version
        self._validation_summary = validation_summary
        self._machine_registry = machine_registry
        self._session_active_duration = session_active_duration
        self._emitter = emitter

    def run(self) -> None:
        try:
            from case_packer import build_case_zip

            build_case_zip(
                self._output_zip,
                self._incidents,
                scan_path=self._scan_path,
                target_ip=self._target_ip,
                remote_mode=self._remote_mode,
                user_notes=self._user_notes,
                app_version=self._app_version,
                validation_summary=self._validation_summary,
                machine_registry=self._machine_registry,
                session_active_duration=self._session_active_duration,
            )
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))
            return
        self._emitter.finished.emit(True, str(self._output_zip))


def schedule_case_pack(
    pool: QThreadPool,
    output_zip: Path,
    incidents: list[Incident],
    scan_path: str,
    target_ip: str | None,
    remote_mode: bool,
    user_notes: str,
    app_version: str | None,
    validation_summary: dict | None,
    machine_registry: dict | None,
    session_active_duration: str | None,
    emitter: CasePackEmitter,
) -> None:
    pool.start(
        _CasePackRunnable(
            output_zip,
            incidents,
            scan_path,
            target_ip,
            remote_mode,
            user_notes,
            app_version,
            validation_summary,
            machine_registry,
            session_active_duration,
            emitter,
        )
    )
