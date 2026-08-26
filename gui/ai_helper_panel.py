"""Chat panel for the offline AI Helper window."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_helper.agent import HelperAnswer, model_status
from config_manager import SettingsManager
from gui.ai_helper_worker import AiHelperEmitter, schedule_ai_helper_ask


_EXAMPLE_PROMPTS = (
    "Where is the SAS controller config?",
    "How do I configure ticket printer payout as the main payout method?",
    "Where is CashoutButtonMode?",
    "Where is the ticket printer tito driver?",
    "Where is mgconfig inactivity setting?",
)


class AiHelperPanel(QWidget):
    """Prompt box + answer view; emits status for the parent window bar."""

    status_changed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._roots: list[str] = []
        self._roots_notes: list[str] = []
        self._pool = QThreadPool.globalInstance()
        self._emitter = AiHelperEmitter(self)
        self._emitter.finished.connect(self._on_finished)
        self._emitter.error.connect(self._on_error)
        self._emitter.status.connect(self.status_changed.emit)
        self._busy = False
        self._cancel_event = threading.Event()
        self._request_id = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        intro = QLabel(
            "Offline helper — searches local GoldClub configs/logs. "
            "Optional: place Qwen3-4B Q4_K_M.gguf in the models folder for LLM answers."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        chips = QHBoxLayout()
        chips_lbl = QLabel("Examples:")
        chips.addWidget(chips_lbl)
        for text in _EXAMPLE_PROMPTS:
            btn = QPushButton(text)
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, t=text: self._use_example(t))
            chips.addWidget(btn)
        chips.addStretch(1)
        root.addLayout(chips)

        self._answer = QTextEdit()
        self._answer.setReadOnly(True)
        self._answer.setPlaceholderText("Answers appear here…")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self._answer.setFont(mono)
        root.addWidget(self._answer, stretch=1)

        row = QHBoxLayout()
        self._prompt = QLineEdit()
        self._prompt.setPlaceholderText("Ask where a config or setting lives…")
        self._prompt.returnPressed.connect(self._on_send)
        row.addWidget(self._prompt, stretch=1)
        self._send_btn = QPushButton("Send")
        self._send_btn.setDefault(True)
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setToolTip("Stop the current search (LLM may finish its current token batch).")
        self._cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._cancel_btn)
        root.addLayout(row)

        self.refresh_model_status()

    def set_search_roots(
        self,
        roots: list[str],
        *,
        notes: list[str] | None = None,
    ) -> None:
        self._roots = [r for r in roots if (r or "").strip()]
        self._roots_notes = list(notes or [])

    def set_roots_diagnosis(self, roots: list[str], notes: list[str]) -> None:
        self.set_search_roots(roots, notes=notes)

    def refresh_model_status(self) -> None:
        try:
            override = SettingsManager.get_ai_helper_model_path()
            self.status_changed.emit(model_status(override or None))
        except Exception as exc:  # noqa: BLE001
            self.status_changed.emit(f"Search-only (status error: {exc})")

    def _use_example(self, text: str) -> None:
        self._prompt.setText(text)
        self._prompt.setFocus()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send_btn.setEnabled(not busy)
        self._prompt.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        self.busy_changed.emit(busy)

    def _on_cancel(self) -> None:
        if not self._busy:
            return
        self._cancel_event.set()
        self.status_changed.emit("Cancelling…")

    def _on_send(self) -> None:
        if self._busy:
            return
        q = self._prompt.text().strip()
        if not q:
            return
        if not self._roots:
            lines = [
                "No reachable search roots.",
                "",
            ]
            if self._roots_notes:
                lines.extend(self._roots_notes)
                lines.append("")
            lines.extend(
                [
                    "Fix one of:",
                    "  • Main window log path (local folder or UNC with lab cmdkey)",
                    "  • Config Scanner game drive (Settings / Config Scanner target)",
                    "Then reopen AI Helper (Ctrl+Shift+A).",
                ]
            )
            self._answer.setPlainText("\n".join(lines))
            return
        self._cancel_event.clear()
        self._request_id += 1
        rid = self._request_id
        self._set_busy(True)
        self.status_changed.emit("Searching…")
        self._answer.append(f"\n▸ You: {q}\n")
        override = SettingsManager.get_ai_helper_model_path() or None
        schedule_ai_helper_ask(
            self._pool,
            question=q,
            roots=list(self._roots),
            model_override=override,
            emitter=self._emitter,
            cancel_event=self._cancel_event,
            request_id=rid,
        )

    def _on_finished(self, answer: object, request_id: int) -> None:
        if request_id != self._request_id:
            return
        self._set_busy(False)
        if not isinstance(answer, HelperAnswer):
            self._answer.append("(unexpected worker result)\n")
            self.refresh_model_status()
            return
        if answer.cancelled:
            self._answer.append("▸ Cancelled.\n")
            self.refresh_model_status()
            return
        mode = "LLM" if answer.used_llm else "search"
        self._answer.append(f"▸ Helper ({mode}):\n{answer.text}\n")
        self._answer.moveCursor(QTextCursor.MoveOperation.End)
        self.status_changed.emit(answer.model_label)

    def _on_error(self, message: str, request_id: int) -> None:
        if request_id != self._request_id:
            return
        self._set_busy(False)
        self._answer.append(f"▸ Error: {message}\n")
        self.refresh_model_status()
