"""UI helpers for defect tickets (structured technical summaries)."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def strip_markdown_bolding(text: str) -> str:
    # Defensive: model may still emit markdown even when instructed not to.
    return (text or "").replace("**", "")


def parse_ai_response(text: str) -> dict[str, str]:
    t = (strip_markdown_bolding(text) or "").strip()

    # Full session audit / responses that use markdown ### section headers
    if re.search(r"(?im)^\s*###\s*Title\s*$", t):
        sections: dict[str, str] = {
            "Title": "",
            "Key details": "",
            "Actual result": "",
            "Expected result": "",
        }
        key_map = {
            "Title": "Title",
            "Key details": "Key details",
            "Actual result": "Actual result",
            "Expected result": "Expected result",
        }
        md_parts = re.split(
            r"(?im)^\s*###\s*(Title|Key details|Actual result|Expected result)\s*$",
            t,
        )
        i = 1
        while i + 1 < len(md_parts):
            header = md_parts[i].strip()
            body = md_parts[i + 1].strip()
            if header in key_map:
                sections[key_map[header]] = body
            i += 2
        return sections

    sections = {}
    title_match = re.search(
        r"(?im)^\s*Title\s*:\s*(.*?)(?:\n\s*\n|^\s*Key details\s*:)",
        t,
        re.DOTALL,
    )
    sections["Title"] = title_match.group(1).strip() if title_match else ""

    key_match = re.search(
        r"(?im)^\s*Key details\s*:\s*(.*?)(?:\n\s*\n|^\s*Actual result\s*:)",
        t,
        re.DOTALL,
    )
    sections["Key details"] = key_match.group(1).strip() if key_match else ""

    actual_match = re.search(
        r"(?im)^\s*Actual result\s*:\s*(.*?)(?:\n\s*\n|^\s*Expected result\s*:)",
        t,
        re.DOTALL,
    )
    sections["Actual result"] = actual_match.group(1).strip() if actual_match else ""

    expected_match = re.search(r"(?im)^\s*Expected result\s*:\s*(.*)", t, re.DOTALL)
    sections["Expected result"] = expected_match.group(1).strip() if expected_match else ""

    return sections


class CopyableFieldWidget(QWidget):
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

        # Adjust height based on content (clamped).
        doc_height = float(self.text_box.document().size().height())
        self.text_box.setMaximumHeight(int(max(60, min(doc_height + 24, 180))))

        layout.addLayout(header_layout)
        layout.addWidget(self.text_box)

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.text_box.toPlainText())
        self.copy_btn.setText("Copied")

