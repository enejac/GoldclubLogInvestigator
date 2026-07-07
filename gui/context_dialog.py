"""Dialog: table slice of unfiltered incidents around a selected row."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from gui.incident_table_model import (
    IncidentTableModel,
    _format_ram_display_cell,
    _format_ram_tooltip_cell,
    _incident_has_ram_data,
    _incident_has_validation_data,
)
from gui.table_smart_stretch import TableSmartStretchFilter
from gui.severity_delegate import SeverityDelegate
from gui.view_model import DisplayRow
from parser import Incident

_CONTEXT_RADIUS = 50


class _NullBookmarks:
    def is_bookmarked(self, _iid: object) -> bool:
        return False

    def get_bookmark_note(self, _iid: object) -> str:
        return ""


def _bookmark_source(parent: Any) -> Any:
    vm = getattr(parent, "_vm", None) if parent is not None else None
    return vm if vm is not None else _NullBookmarks()


class _ContextSliceTableModel(QAbstractTableModel):
    """Fixed slice of ``DisplayRow`` entries; pin column uses main ``IncidentViewModel`` when available."""

    severity_role = IncidentTableModel.severity_role
    flash_role = IncidentTableModel.flash_role
    validation_role = IncidentTableModel.validation_role
    ram_pct_role = IncidentTableModel.ram_pct_role
    ram_process_mb_role = IncidentTableModel.ram_process_mb_role
    HEADERS = IncidentTableModel.HEADERS

    def __init__(self, rows: list[DisplayRow], bookmark_vm: Any, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._bookmark_vm = bookmark_vm

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
        role: int = Qt.DisplayRole,
    ) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._rows):
            return None
        dr = self._rows[row]
        inc = dr.incident
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                if not self._bookmark_vm.is_bookmarked(inc.id):
                    return ""
                note = self._bookmark_vm.get_bookmark_note(inc.id) or ""
                return "📝" if note.strip() else "📌"
            if col == IncidentTableModel.TIMESTAMP_COLUMN:
                ts = inc.timestamp_display()
                if dr.count > 1:
                    return f"[{dr.count}x] {ts}"
                return ts
            if col == 2:
                return inc.game
            if col == 3:
                return inc.severity
            if col == 4:
                return inc.error_type
            if col == 5:
                vs = getattr(inc, "validation_status", None)
                if vs == "FAIL":
                    return "MATH DISCREPANCY"
                if vs == "PASS":
                    return "OK"
                return "—"
            if col == 6:
                return _format_ram_display_cell(inc)
        if role == Qt.ToolTipRole:
            if col == 0 and self._bookmark_vm.is_bookmarked(inc.id):
                note = (self._bookmark_vm.get_bookmark_note(inc.id) or "").strip()
                return note if note else "Bookmarked (no note)"
            if col == IncidentTableModel.TIMESTAMP_COLUMN and dr.count > 1:
                return (
                    f"{dr.count} consecutive incidents with the same severity, "
                    f"error type, and game (showing first timestamp)."
                )
            if col == 5:
                detail = getattr(inc, "validation_detail", None)
                if detail:
                    return detail
                if getattr(inc, "error_type", "") == "CRITICAL MATH DISCREPANCY":
                    return inc.probable_cause
                return None
            if col == 6:
                return _format_ram_tooltip_cell(inc)
        if role == self.severity_role:
            return inc.severity
        if role == self.validation_role:
            return getattr(inc, "validation_status", None)
        if role == self.ram_pct_role:
            return getattr(inc, "remote_ram_used_pct", None)
        if role == self.ram_process_mb_role:
            return getattr(inc, "remote_process_mb", None)
        if role == self.flash_role:
            return 0
        if role == Qt.TextAlignmentRole and col == IncidentTableModel.TIMESTAMP_COLUMN:
            return Qt.AlignLeft | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 0:
            return Qt.AlignCenter | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 5:
            return Qt.AlignLeft | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 6:
            return Qt.AlignLeft | Qt.AlignVCenter
        return None


class IncidentContextDialog(QDialog):
    """Shows ±50 unfiltered incidents around a target, with the target row centered."""

    def __init__(
        self,
        target_incident: Incident,
        unfiltered_incidents: list[Incident],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surrounding context (unfiltered)")
        self.resize(1000, 600)

        target_idx = next(
            (i for i, x in enumerate(unfiltered_incidents) if x.id == target_incident.id),
            None,
        )
        if target_idx is None:
            raise ValueError("target_incident must appear in unfiltered_incidents")

        start_idx = max(0, target_idx - _CONTEXT_RADIUS)
        end_idx = min(len(unfiltered_incidents), target_idx + _CONTEXT_RADIUS + 1)
        context_incidents = unfiltered_incidents[start_idx:end_idx]
        display_rows = [DisplayRow(incident=inc, count=1) for inc in context_incidents]

        bkm = _bookmark_source(parent)
        self._model = _ContextSliceTableModel(display_rows, bkm, self)
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setItemDelegate(SeverityDelegate(self._table))
        header = self._table.horizontalHeader()
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        header.setStretchLastSection(False)
        header.resizeSection(0, 40)
        for col in range(header.count()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._table_stretch_filter = TableSmartStretchFilter(self._table)
        self._table.viewport().installEventFilter(self._table_stretch_filter)
        has_v = any(
            _incident_has_validation_data(dr.incident) for dr in display_rows
        )
        has_r = any(_incident_has_ram_data(dr.incident) for dr in display_rows)
        self._table.setColumnHidden(
            IncidentTableModel.VALIDATION_COLUMN,
            not has_v,
        )
        self._table.setColumnHidden(
            IncidentTableModel.RAM_COLUMN,
            not has_r,
        )
        self._table_stretch_filter._stretch_message_column(self._table)

        self._target_row_index = target_idx - start_idx

        root = QVBoxLayout(self)
        root.addWidget(self._table, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)
        QTimer.singleShot(0, self._fit_context_table_columns)

    def _fit_context_table_columns(self) -> None:
        self._table.resizeColumnsToContents()
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        for col in range(hh.count()):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._table_stretch_filter._stretch_message_column(self._table)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)

        def _after_show() -> None:
            self._fit_context_table_columns()
            self._table.selectRow(self._target_row_index)
            self._table.scrollTo(
                self._model.index(self._target_row_index, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

        QTimer.singleShot(0, _after_show)
