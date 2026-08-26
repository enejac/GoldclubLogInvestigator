"""Background worker for offline AI Helper prompts."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from shiboken6 import Shiboken

from ai_helper.agent import HelperAnswer, ask


class AiHelperEmitter(QObject):
    finished = Signal(object, int)  # HelperAnswer, request_id
    error = Signal(str, int)  # message, request_id
    status = Signal(str)


class _AiHelperRunnable(QRunnable):
    def __init__(
        self,
        question: str,
        roots: list[str],
        model_override: str | None,
        emitter: AiHelperEmitter,
        cancel_event: threading.Event,
        request_id: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._question = question
        self._roots = list(roots)
        self._model_override = model_override
        self._emitter = emitter
        self._cancel_event = cancel_event
        self._request_id = request_id

    def run(self) -> None:
        try:
            if self._cancel_event.is_set():
                if Shiboken.isValid(self._emitter):
                    self._emitter.finished.emit(
                        HelperAnswer(
                            text="Cancelled.",
                            cancelled=True,
                        ),
                        self._request_id,
                    )
                return
            if Shiboken.isValid(self._emitter):
                self._emitter.status.emit("Searching…")
            answer = ask(
                self._question,
                self._roots,
                model_override=self._model_override,
                cancel_check=self._cancel_event.is_set,
            )
            if Shiboken.isValid(self._emitter):
                self._emitter.finished.emit(answer, self._request_id)
        except Exception as exc:  # noqa: BLE001
            if Shiboken.isValid(self._emitter):
                self._emitter.error.emit(str(exc), self._request_id)


def schedule_ai_helper_ask(
    pool: QThreadPool,
    *,
    question: str,
    roots: list[str],
    model_override: str | None,
    emitter: AiHelperEmitter,
    cancel_event: threading.Event,
    request_id: int,
) -> None:
    pool.start(
        _AiHelperRunnable(
            question=question,
            roots=roots,
            model_override=model_override,
            emitter=emitter,
            cancel_event=cancel_event,
            request_id=request_id,
        )
    )


__all__ = [
    "AiHelperEmitter",
    "HelperAnswer",
    "schedule_ai_helper_ask",
]
