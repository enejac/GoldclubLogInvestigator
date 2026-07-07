"""
Checkable pill-style quick filters for the incident table (additive OR semantics).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QToolButton, QWidget

from gui.palette_adapt import (
    chip_math_tint,
    filter_chip_stylesheet,
    text_danger,
    text_warning,
)


class FilterChipsBar(QWidget):
    """Four additive quick filters wired to ``IncidentViewModel``."""

    def __init__(self, view_model: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = view_model
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 6)
        lay.setSpacing(8)
        lab = QLabel("Quick:")
        lab.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        lab.setStyleSheet("font-size: 12px;")
        lay.addWidget(lab)

        self._btn_crit = QToolButton()
        self._btn_crit.setText("🔴 Critical")
        self._btn_crit.setCheckable(True)
        self._btn_crit.setProperty("filter_chip", True)
        self._btn_crit.setToolTip("CRITICAL / FATAL-style incidents")
        self._btn_crit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_crit.toggled.connect(
            lambda on: self._vm.set_quick_filter_critical(on)
        )
        lay.addWidget(self._btn_crit)

        self._btn_warn = QToolButton()
        self._btn_warn.setText("🟠 Warnings")
        self._btn_warn.setCheckable(True)
        self._btn_warn.setProperty("filter_chip", True)
        self._btn_warn.setToolTip("MEDIUM severity and log WARN lines")
        self._btn_warn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_warn.toggled.connect(lambda on: self._vm.set_quick_filter_warn(on))
        lay.addWidget(self._btn_warn)

        self._btn_math = QToolButton()
        self._btn_math.setText("🧮 Math Fails")
        self._btn_math.setCheckable(True)
        self._btn_math.setProperty("filter_chip", True)
        self._btn_math.setToolTip("Roulette validation FAIL or CRITICAL MATH DISCREPANCY")
        self._btn_math.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_math.toggled.connect(lambda on: self._vm.set_quick_filter_math_fails(on))
        lay.addWidget(self._btn_math)

        self._btn_drift = QToolButton()
        self._btn_drift.setText("⚙️ System Drift")
        self._btn_drift.setCheckable(True)
        self._btn_drift.setProperty("filter_chip", True)
        self._btn_drift.setToolTip("Environment drift: SYSTEM DRIFT DETECTED")
        self._btn_drift.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_drift.toggled.connect(lambda on: self._vm.set_quick_filter_drift(on))
        lay.addWidget(self._btn_drift)

        self._btn_known = QToolButton()
        self._btn_known.setText("🔗 Known issue")
        self._btn_known.setCheckable(True)
        self._btn_known.setProperty("filter_chip", True)
        self._btn_known.setToolTip(
            "Rows matching data/known_issues.json (e.g. GCI-AFT-001 AFT transfer naming)"
        )
        self._btn_known.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_known.toggled.connect(
            lambda on: self._vm.set_quick_filter_known_issues(on)
        )
        lay.addWidget(self._btn_known)

        lay.addStretch(1)

        self._refresh_chip_styles()
        app = QApplication.instance()
        if app is not None:
            app.paletteChanged.connect(self._refresh_chip_styles)

        view_model.quick_filters_changed.connect(self._sync_from_view_model)
        self._sync_from_view_model()

    def _refresh_chip_styles(self) -> None:
        # Let the global application stylesheet control contrast/visibility.
        # (We still keep palette-driven logic available, but widget-level styles would
        # override the requested `[filter_chip="true"]` rules.)
        self._btn_crit.setStyleSheet("")
        self._btn_warn.setStyleSheet("")
        self._btn_math.setStyleSheet("")
        self._btn_drift.setStyleSheet("")
        self._btn_known.setStyleSheet("")

    def _sync_from_view_model(self) -> None:
        for btn, getter in (
            (self._btn_crit, self._vm.quick_filter_critical_active),
            (self._btn_warn, self._vm.quick_filter_warn_active),
            (self._btn_math, self._vm.quick_filter_math_fails_active),
            (self._btn_drift, self._vm.quick_filter_drift_active),
            (self._btn_known, self._vm.quick_filter_known_issues_active),
        ):
            btn.blockSignals(True)
            btn.setChecked(getter())
            btn.blockSignals(False)
