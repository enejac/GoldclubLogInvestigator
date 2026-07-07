"""Dialog to add or edit a bookmark note for an incident."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
)


class BookmarkDialog(QDialog):
    def __init__(self, parent=None, *, initial_note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Log bookmark")
        self.resize(420, 200)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Note (shown as tooltip on the pin column):"))
        self._text = QTextEdit()
        self._text.setPlainText(initial_note)
        self._text.setPlaceholderText("Optional — leave empty to pin without a note.")
        root.addWidget(self._text)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        save_btn = bbox.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
        root.addWidget(bbox)

    def note(self) -> str:
        return self._text.toPlainText().strip()
