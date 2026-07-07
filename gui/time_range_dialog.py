"""Dialog for optional UTC time window when parsing logs (global scan filter)."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QDateTime, QTimeZone
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class TimeRangeScanDialog(QDialog):
    """Optional start/end UTC bounds; disabled checkbox means full file parse."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        current_start: datetime | None = None,
        current_end: datetime | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Time-Bounded Scan")
        self.resize(420, 160)

        tz = QTimeZone(b"UTC")
        now = QDateTime.currentDateTimeUtc()

        self._enable = QCheckBox("Enable Time-Bounded Scan")
        self._start_edit = QDateTimeEdit()
        self._end_edit = QDateTimeEdit()
        for ed in (self._start_edit, self._end_edit):
            ed.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            ed.setCalendarPopup(True)
            ed.setTimeZone(tz)

        if current_start is not None and current_end is not None:
            self._enable.setChecked(True)
            self._start_edit.setDateTime(
                self._utc_datetime_to_qdatetime(current_start, tz)
            )
            self._end_edit.setDateTime(
                self._utc_datetime_to_qdatetime(current_end, tz)
            )
        else:
            self._enable.setChecked(False)
            self._start_edit.setDateTime(now.addSecs(-3600))
            self._end_edit.setDateTime(now)

        self._start_edit.setEnabled(self._enable.isChecked())
        self._end_edit.setEnabled(self._enable.isChecked())
        self._enable.toggled.connect(self._start_edit.setEnabled)
        self._enable.toggled.connect(self._end_edit.setEnabled)

        hint = QLabel(
            "Times are UTC, matching typical cabinet log timestamps (+00:00). "
            "Parsing stops at the first line after the end time (chronological logs)."
        )
        hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Start Time", self._start_edit)
        form.addRow("End Time", self._end_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addWidget(self._enable)
        outer.addLayout(form)
        outer.addWidget(hint)
        outer.addWidget(buttons)

    @staticmethod
    def _utc_datetime_to_qdatetime(dt: datetime, tz: QTimeZone) -> QDateTime:
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        aware = aware.astimezone(timezone.utc)
        ms = int(aware.timestamp() * 1000)
        return QDateTime.fromMSecsSinceEpoch(ms, tz)

    @staticmethod
    def _qdatetime_to_utc_datetime(qdt: QDateTime) -> datetime:
        ms = qdt.toMSecsSinceEpoch()
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

    def get_time_bounds(self) -> tuple[datetime | None, datetime | None]:
        if not self._enable.isChecked():
            return None, None
        a = self._qdatetime_to_utc_datetime(self._start_edit.dateTime())
        b = self._qdatetime_to_utc_datetime(self._end_edit.dateTime())
        if a > b:
            a, b = b, a
        return a, b
