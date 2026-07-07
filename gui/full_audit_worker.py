"""Run full session audit off the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from shiboken6 import Shiboken

from network.ai_summarizer import generate_full_audit


class FullAuditEmitter(QObject):
    finished = Signal(bool, str)


class _FullAuditRunnable(QRunnable):
    def __init__(
        self,
        incidents_data: str,
        software_version: str,
        api_key: str,
        ai_provider: str,
        groq_api_key: str,
        openrouter_api_key: str,
        venice_api_key: str,
        emitter: FullAuditEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._incidents_data = incidents_data
        self._software_version = software_version
        self._api_key = api_key
        self._ai_provider = ai_provider
        self._groq_api_key = groq_api_key
        self._openrouter_api_key = openrouter_api_key
        self._venice_api_key = venice_api_key
        self._emitter = emitter

    def run(self) -> None:
        try:
            out = generate_full_audit(
                self._incidents_data,
                self._software_version,
                self._api_key,
                ai_provider=self._ai_provider,
                groq_api_key=self._groq_api_key,
                openrouter_api_key=self._openrouter_api_key,
                venice_api_key=self._venice_api_key,
            )
            if Shiboken.isValid(self._emitter):
                self._emitter.finished.emit(True, out)
        except Exception as e:  # noqa: BLE001
            if Shiboken.isValid(self._emitter):
                self._emitter.finished.emit(
                    False,
                    f"Session audit unavailable (worker failed: {e}).",
                )


def schedule_full_audit(
    pool: QThreadPool,
    *,
    incidents_data: str,
    software_version: str,
    api_key: str,
    ai_provider: str,
    groq_api_key: str,
    openrouter_api_key: str,
    venice_api_key: str,
    emitter: FullAuditEmitter,
) -> None:
    pool.start(
        _FullAuditRunnable(
            incidents_data,
            software_version,
            api_key,
            ai_provider,
            groq_api_key,
            openrouter_api_key,
            venice_api_key,
            emitter,
        )
    )

