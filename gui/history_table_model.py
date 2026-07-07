"""Table model for History tab (database query results)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class HistoryTableModel(QAbstractTableModel):
    HEADERS = [
        "Timestamp",
        "Machine",
        "Game",
        "Severity",
        "Error Type",
        "Validation",
        "Log file",
        "Line",
    ]

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return row.get("timestamp_raw") or "—"
            if col == 1:
                return row.get("machine_label") or "—"
            if col == 2:
                return row.get("game") or "—"
            if col == 3:
                return row.get("severity") or "—"
            if col == 4:
                return row.get("error_type") or "—"
            if col == 5:
                return row.get("validation_status") or "—"
            if col == 6:
                p = row.get("log_file_path") or ""
                return p if len(p) < 80 else "…" + p[-76:]
            if col == 7:
                return str(row.get("line_number", ""))
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.get("line_snippet") or row.get("probable_cause") or ""
        return None
