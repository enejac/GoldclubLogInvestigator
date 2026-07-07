"""History tab: search persisted incidents without rescanning log files."""

from __future__ import annotations

from datetime import datetime, time, timezone

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from database.manager import DatabaseManager, IncidentQueryFilters
from gui.history_table_model import HistoryTableModel


def _qdate_to_start_sort_key(qd: QDate) -> float:
    d = qd.toPython()
    dt = datetime.combine(d, time.min, tzinfo=timezone.utc)
    return dt.timestamp()


def _qdate_to_end_sort_key(qd: QDate) -> float:
    d = qd.toPython()
    dt = datetime.combine(d, time(23, 59, 59, 999999), tzinfo=timezone.utc)
    return dt.timestamp()


class HistoryTabWidget(QWidget):
    """Hosts filters, results table, and signals for sync / search."""

    sync_scan_requested = Signal()
    search_requested = Signal()
    count_refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._history_model = HistoryTableModel(self)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "Search the local SQLite database (<code>goldclub_investigator.db</code> in the app folder). "
            "Use <b>Sync scan to database</b> after a scan to persist new rows (deduplicated by timestamp, machine IP, and message)."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(intro)

        stat_row = QHBoxLayout()
        self._db_stat = QLabel("Incidents in DB: —")
        stat_row.addWidget(self._db_stat)
        stat_row.addStretch(1)
        btn_count = QPushButton("Refresh count")
        btn_count.clicked.connect(self._on_refresh_count_clicked)
        stat_row.addWidget(btn_count)
        root.addLayout(stat_row)

        filt = QGroupBox("Filters")
        fl = QFormLayout(filt)
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDate(QDate.currentDate().addDays(-7))
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDate(QDate.currentDate())
        fl.addRow("From date (UTC):", self._date_from)
        fl.addRow("To date (UTC):", self._date_to)
        self._game_edit = QLineEdit()
        self._game_edit.setPlaceholderText("Game, theme, machine id (gst…), or IP substring")
        fl.addRow("Search contains:", self._game_edit)
        self._severity = QComboBox()
        self._severity.addItems(["(any)", "CRITICAL", "MEDIUM", "LOW"])
        fl.addRow("Severity:", self._severity)
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(100, 50_000)
        self._limit_spin.setValue(5000)
        self._limit_spin.setSingleStep(500)
        fl.addRow("Max rows:", self._limit_spin)
        root.addWidget(filt)

        btn_row = QHBoxLayout()
        self._search_btn = QPushButton("Search database")
        self._search_btn.clicked.connect(self.search_requested.emit)
        btn_row.addWidget(self._search_btn)
        self._sync_btn = QPushButton("Sync scan to database")
        self._sync_btn.setToolTip(
            "Run the same scan as the main toolbar, then save incidents and state nodes to SQLite."
        )
        self._sync_btn.clicked.connect(self.sync_scan_requested.emit)
        btn_row.addWidget(self._sync_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._table = QTableView()
        self._table.setModel(self._history_model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table, stretch=1)

        self._busy = QLabel("")
        self._busy.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        root.addWidget(self._busy)

    def database_manager(self) -> DatabaseManager:
        return self._db

    def set_sync_enabled(self, enabled: bool) -> None:
        self._sync_btn.setEnabled(enabled)

    def set_busy(self, text: str) -> None:
        self._busy.setText(text)

    def apply_query_results(self, rows: object) -> None:
        if isinstance(rows, dict) and "__error__" in rows:
            QMessageBox.warning(self, "Database query", str(rows["__error__"]))
            self._history_model.set_rows([])
            return
        if not isinstance(rows, list):
            self._history_model.set_rows([])
            return
        self._history_model.set_rows(rows)

    def build_filters(self) -> IncidentQueryFilters:
        sev = self._severity.currentText()
        if sev == "(any)":
            sev = ""
        return IncidentQueryFilters(
            date_from_sort_key=_qdate_to_start_sort_key(self._date_from.date()),
            date_to_sort_key=_qdate_to_end_sort_key(self._date_to.date()),
            game_substring=self._game_edit.text().strip() or None,
            severity=sev or None,
            limit=self._limit_spin.value(),
        )

    def update_db_count(self, n: int) -> None:
        if n < 0:
            self._db_stat.setText("Incidents in DB: (unavailable)")
        else:
            self._db_stat.setText(f"Incidents in DB: {n:,}")

    def _on_refresh_count_clicked(self) -> None:
        self.set_busy("Counting rows…")
        self.count_refresh_requested.emit()
