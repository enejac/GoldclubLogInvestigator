"""Run Gemini "Enhance Summary" off the GUI thread."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from shiboken6 import Shiboken

if TYPE_CHECKING:
    from parser import Incident

from network.ai_summarizer import enhance_incident_summary


class AiEnhanceEmitter(QObject):
    """Emits the final analysis text back to the GUI thread."""

    finished = Signal(bool, str)
    # success, text (or error message)


class _AiEnhanceRunnable(QRunnable):
    def __init__(
        self,
        error_details: str,
        preceding: list[str],
        software_version: str,
        api_key: str,
        ai_provider: str,
        groq_api_key: str,
        openrouter_api_key: str,
        venice_api_key: str,
        emitter: AiEnhanceEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._error_details = error_details
        self._preceding = preceding
        self._software_version = software_version
        self._api_key = api_key
        self._ai_provider = ai_provider
        self._groq_api_key = groq_api_key
        self._openrouter_api_key = openrouter_api_key
        self._venice_api_key = venice_api_key
        self._emitter = emitter

    def run(self) -> None:
        try:
            out = enhance_incident_summary(
                error_details=self._error_details,
                preceding_logs=self._preceding,
                software_version=self._software_version,
                api_key=self._api_key,
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
                    f"Technical summary unavailable (worker failed: {e}).",
                )


def schedule_ai_enhance(
    pool: QThreadPool,
    *,
    error_details: str,
    preceding: list[str],
    software_version: str,
    api_key: str,
    ai_provider: str,
    groq_api_key: str,
    openrouter_api_key: str,
    venice_api_key: str,
    emitter: AiEnhanceEmitter,
) -> None:
    pool.start(
        _AiEnhanceRunnable(
            error_details=error_details,
            preceding=preceding,
            software_version=software_version,
            api_key=api_key,
            ai_provider=ai_provider,
            groq_api_key=groq_api_key,
            openrouter_api_key=openrouter_api_key,
            venice_api_key=venice_api_key,
            emitter=emitter,
        )
    )

