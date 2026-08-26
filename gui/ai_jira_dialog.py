"""UI helpers for defect tickets (structured technical summaries)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from network.defect_ticket import (
    normalize_defect_ticket_text,
    parse_defect_ticket_sections,
    strip_markdown_bolding,
)

__all__ = [
    "CopyableFieldWidget",
    "normalize_defect_ticket_text",
    "parse_ai_response",
    "strip_markdown_bolding",
]


def parse_ai_response(text: str) -> dict[str, str]:
    return parse_defect_ticket_sections(text)


class CopyableFieldWidget(QWidget):
    """Read-only ticket section that expands to show all text (no inner scrollbar)."""

    def __init__(self, title: str, content: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)

        header_layout = QHBoxLayout()
        label = QLabel(f"<b>{title}</b>")
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedWidth(100)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)

        header_layout.addWidget(label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.copy_btn)

        self.text_box = QTextEdit()
        self.text_box.setReadOnly(True)
        self.text_box.setPlainText((content or "").strip())
        self.text_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.text_box.setMinimumHeight(40)

        layout.addLayout(header_layout)
        layout.addWidget(self.text_box)
        self._fit_text_height()

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_text_height()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_text_height()

    def _fit_text_height(self) -> None:
        """Grow the editor to the full wrapped document height (outer dialog scrolls)."""
        box = self.text_box
        doc = box.document()
        # Use current viewport width when known; otherwise a typical dialog content width.
        width = box.viewport().width()
        if width < 40:
            width = max(200, self.width() - 24) if self.width() > 40 else 820
        doc.setTextWidth(float(width))
        # document().size() is in logical pixels; add frame + small padding for last line.
        needed = int(doc.size().height()) + int(box.frameWidth()) * 2 + 12
        box.setFixedHeight(max(40, needed))

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.text_box.toPlainText())
        self.copy_btn.setText("Copied")
