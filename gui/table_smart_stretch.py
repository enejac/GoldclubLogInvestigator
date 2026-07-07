"""Resize column 4 (Error Type) to absorb leftover viewport width; keeps all header modes Interactive."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QTableView

MESSAGE_STRETCH_COLUMN = 4


class TableSmartStretchFilter(QObject):
    def __init__(self, table: QTableView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._table = table

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, lambda: self._stretch_message_column(self._table))
        return False

    def _stretch_message_column(self, table: QTableView) -> None:
        header = table.horizontalHeader()
        if header is None or header.count() <= MESSAGE_STRETCH_COLUMN:
            return
        total_width = table.viewport().width()
        used_width = sum(
            header.sectionSize(i)
            for i in range(header.count())
            if i != MESSAGE_STRETCH_COLUMN and not header.isSectionHidden(i)
        )
        remaining = total_width - used_width
        if remaining > 50:
            header.resizeSection(MESSAGE_STRETCH_COLUMN, remaining)
