"""Virtual QAbstractTableModel backed by the ViewModel's filtered index list."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal

from gui.view_model import IncidentViewModel


def _incident_has_validation_data(inc: object) -> bool:
    """True when the Validation column would show something other than the default dash."""
    vs = getattr(inc, "validation_status", None)
    if vs is None:
        return False
    s = str(vs).strip()
    return s in ("FAIL", "PASS")


def _incident_has_ram_data(inc: object) -> bool:
    return getattr(inc, "remote_ram_used_pct", None) is not None


def _format_ram_display_cell(inc: object) -> str:
    pct = getattr(inc, "remote_ram_used_pct", None)
    if pct is None:
        return "—"
    pmb = getattr(inc, "remote_process_mb", None)
    if pmb is not None and float(pmb) > 0:
        gb = float(pmb) / 1024.0
        return f"{float(pct):.0f}% ({gb:.1f}GB)"
    return f"{float(pct):.0f}%"


def _format_ram_tooltip_cell(inc: object) -> str:
    pct = getattr(inc, "remote_ram_used_pct", None)
    if pct is None:
        return (
            "Physical memory on remote host (WMIC); captured after this line arrived via Live Watch."
        )
    total = getattr(inc, "remote_ram_total_mb", None)
    used = getattr(inc, "remote_ram_used_mb", None)
    pmb = getattr(inc, "remote_process_mb", None)
    lines: list[str] = []
    if total is not None and used is not None:
        lines.append(
            f"System RAM: {float(pct):.0f}% (Used: {float(used):.0f}MB / Total: {float(total):.0f}MB)"
        )
    else:
        lines.append(f"System RAM: {float(pct):.0f}%")
    if pmb is not None and float(pmb) > 0:
        lines.append(f"Game Process (OneHand.exe): {float(pmb):.0f} MB")
    else:
        lines.append(
            "Game Process (OneHand.exe): not running or not queryable."
        )
    return "\n".join(lines)


class IncidentTableModel(QAbstractTableModel):
    """O(1) row access via ViewModel; no per-row QWidget allocation."""

    column_visibility_changed = Signal()

    TIMESTAMP_COLUMN = 1
    VALIDATION_COLUMN = 5
    RAM_COLUMN = 6
    """Physical RAM / process memory column (last data column)."""

    severity_role = Qt.UserRole + 1
    flash_role = Qt.UserRole + 2
    """0–100 fade strength for live CRITICAL row highlight."""
    validation_role = Qt.UserRole + 3
    ram_pct_role = Qt.UserRole + 4
    """``remote_ram_used_pct`` for RAM column styling."""
    ram_process_mb_role = Qt.UserRole + 5
    """``remote_process_mb`` (OneHand.exe working set, MB)."""

    HEADERS = ["📌", "Timestamp", "Game", "Severity", "Error Type", "Validation", "RAM %"]

    def __init__(self, view_model: IncidentViewModel, parent: Any = None) -> None:
        super().__init__(parent)
        self._vm = view_model
        self.has_validation_data = False
        self.has_ram_data = False
        self._flash: dict[int, int] = {}
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(80)
        self._flash_timer.timeout.connect(self._tick_flash)
        view_model.filter_rebuilt.connect(self._on_filter_rebuilt)
        view_model.rows_inserted.connect(self._on_rows_inserted)
        view_model.bookmarks_changed.connect(self._on_bookmarks_changed)
        view_model.incident_ram_updated.connect(self._on_incident_ram_updated)
        view_model.remote_ram_bulk_updated.connect(self._on_remote_ram_bulk_updated)

    def _on_bookmarks_changed(self) -> None:
        n = self.rowCount()
        if n <= 0:
            return
        top_left = self.index(0, 0)
        bottom_right = self.index(n - 1, 0)
        self.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
        )

    def _on_incident_ram_updated(self, incident_id: str) -> None:
        ram_col = self.RAM_COLUMN
        for row in range(self.rowCount()):
            inc = self._vm.incident_at_filtered_row(row)
            if inc is not None and inc.id == incident_id:
                i0 = self.index(row, ram_col)
                self.dataChanged.emit(
                    i0,
                    i0,
                    [
                        Qt.ItemDataRole.DisplayRole,
                        Qt.ItemDataRole.ForegroundRole,
                        self.ram_pct_role,
                        self.ram_process_mb_role,
                    ],
                )
                if (
                    not self.has_ram_data
                    and _incident_has_ram_data(inc)
                ):
                    self.has_ram_data = True
                    self.column_visibility_changed.emit()
                break

    def _on_remote_ram_bulk_updated(self) -> None:
        ram_col = self.RAM_COLUMN
        n = self.rowCount()
        if n <= 0:
            return
        roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ForegroundRole,
            self.ram_pct_role,
            self.ram_process_mb_role,
        ]
        self.dataChanged.emit(self.index(0, ram_col), self.index(n - 1, ram_col), roles)
        if not self.has_ram_data:
            for r in range(n):
                inc = self._vm.incident_at_filtered_row(r)
                if inc is not None and _incident_has_ram_data(inc):
                    self.has_ram_data = True
                    self.column_visibility_changed.emit()
                    break

    def register_flash_row(self, filtered_row: int) -> None:
        if filtered_row < 0 or filtered_row >= self.rowCount():
            return
        self._flash[filtered_row] = 100
        if not self._flash_timer.isActive():
            self._flash_timer.start()
        self._emit_flash_changed(filtered_row)

    def _emit_flash_changed(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        top_left = self.index(row, 0)
        bottom_right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(
            top_left,
            bottom_right,
            [self.flash_role, Qt.ItemDataRole.BackgroundRole],
        )

    def _tick_flash(self) -> None:
        if not self._flash:
            self._flash_timer.stop()
            return
        rows = list(self._flash.keys())
        for r in rows:
            v = self._flash[r] - 14
            if v <= 0:
                del self._flash[r]
            else:
                self._flash[r] = v
        for r in rows:
            self._emit_flash_changed(r)

    def _recompute_column_visibility_flags(self) -> None:
        has_v = False
        has_r = False
        n = self._vm.filtered_row_count()
        for i in range(n):
            inc = self._vm.incident_at_filtered_row(i)
            if inc is None:
                continue
            if not has_v and _incident_has_validation_data(inc):
                has_v = True
            if not has_r and _incident_has_ram_data(inc):
                has_r = True
            if has_v and has_r:
                break
        prev_v, prev_r = self.has_validation_data, self.has_ram_data
        self.has_validation_data = has_v
        self.has_ram_data = has_r
        if prev_v != has_v or prev_r != has_r:
            self.column_visibility_changed.emit()

    def _merge_column_visibility_from_new_rows(self, first: int, last: int) -> None:
        changed = False
        for r in range(first, last + 1):
            inc = self._vm.incident_at_filtered_row(r)
            if inc is None:
                continue
            if not self.has_validation_data and _incident_has_validation_data(inc):
                self.has_validation_data = True
                changed = True
            if not self.has_ram_data and _incident_has_ram_data(inc):
                self.has_ram_data = True
                changed = True
            if self.has_validation_data and self.has_ram_data:
                break
        if changed:
            self.column_visibility_changed.emit()

    def _on_filter_rebuilt(self) -> None:
        self._flash.clear()
        self._flash_timer.stop()
        self.beginResetModel()
        self.endResetModel()
        self._recompute_column_visibility_flags()

    def _on_rows_inserted(self, first: int, last: int) -> None:
        self.beginInsertRows(QModelIndex(), first, last)
        self.endInsertRows()
        self._merge_column_visibility_from_new_rows(first, last)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._vm.filtered_row_count()

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
        dr = self._vm.display_row_at(index.row())
        if dr is None:
            return None
        inc = dr.incident
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                if not self._vm.is_bookmarked(inc.id):
                    return ""
                note = self._vm.get_bookmark_note(inc.id) or ""
                return "📝" if note.strip() else "📌"
            if col == self.TIMESTAMP_COLUMN:
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
            if col == 0 and self._vm.is_bookmarked(inc.id):
                note = (self._vm.get_bookmark_note(inc.id) or "").strip()
                return note if note else "Bookmarked (no note)"
            if col == self.TIMESTAMP_COLUMN and dr.count > 1:
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
            return self._flash.get(index.row(), 0)
        if role == Qt.TextAlignmentRole and col == self.TIMESTAMP_COLUMN:
            return Qt.AlignLeft | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 0:
            return Qt.AlignCenter | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 5:
            return Qt.AlignLeft | Qt.AlignVCenter
        if role == Qt.TextAlignmentRole and col == 6:
            return Qt.AlignLeft | Qt.AlignVCenter
        return None

    def incident_for_index(self, index: QModelIndex) -> Any:
        if not index.isValid():
            return None
        return self._vm.incident_at_filtered_row(index.row())
