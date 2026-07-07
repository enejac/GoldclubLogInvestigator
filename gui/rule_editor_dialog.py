"""
Editor for user-defined log signature rules (regex + severity + category).
"""

from __future__ import annotations

import re
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.palette_adapt import error_border_color
from rules_engine import CustomRule, get_rules_manager


class RuleEditorDialog(QDialog):
    """Custom Signatures Editor: list + form, validate regex on save."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Signatures Editor")
        self.resize(720, 480)
        mgr = get_rules_manager()
        self._rules = [deepcopy(r) for r in mgr.get_active_rules()]
        self._suppress_list_signal = False
        self._prev_row = -1
        self._regex_error_active = False

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_list_row_changed)
        left_lay.addWidget(QLabel("Rules"))
        left_lay.addWidget(self._list, stretch=1)
        btn_row = QHBoxLayout()
        self._new_btn = QPushButton("New Rule")
        self._new_btn.clicked.connect(self._new_rule)
        self._del_btn = QPushButton("Delete Selected")
        self._del_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._del_btn)
        left_lay.addLayout(btn_row)

        right = QWidget()
        form = QFormLayout(right)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Short display name")
        self._regex_edit = QPlainTextEdit()
        self._regex_edit.setPlaceholderText("Python regex (re.search on full line)")
        self._regex_edit.setFixedHeight(72)
        self._severity_combo = QComboBox()
        for s in ("CRITICAL", "WARN", "INFO", "DEBUG"):
            self._severity_combo.addItem(s)
        self._type_edit = QLineEdit()
        self._type_edit.setPlaceholderText("Shown as error type / category in the table")
        form.addRow("Rule name:", self._name_edit)
        form.addRow("Regex pattern:", self._regex_edit)
        form.addRow("Severity:", self._severity_combo)
        form.addRow("Error type:", self._type_edit)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        root.addWidget(split)

        self._save_btn = QPushButton("Save && Apply")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_and_apply)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close,
        )
        bbox.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self._save_btn)
        bottom.addWidget(bbox.button(QDialogButtonBox.StandardButton.Close))
        root.addLayout(bottom)

        self._refresh_list(select_row=0 if self._rules else -1)

        app = QApplication.instance()
        if app is not None:
            app.paletteChanged.connect(self._on_app_palette_changed)

    def _on_app_palette_changed(self) -> None:
        if self._regex_error_active:
            self._highlight_regex_error()

    def _clear_regex_highlight(self) -> None:
        self._regex_error_active = False
        self._regex_edit.setStyleSheet("")

    def _highlight_regex_error(self) -> None:
        self._regex_error_active = True
        ec = error_border_color(self.palette()).name()
        self._regex_edit.setStyleSheet(
            f"QPlainTextEdit {{ border: 2px solid {ec}; border-radius: 4px; }}"
        )

    def _refresh_list(self, *, select_row: int) -> None:
        self._suppress_list_signal = True
        self._list.clear()
        for r in self._rules:
            QListWidgetItem(r.name or "(unnamed)", self._list)
        self._suppress_list_signal = False
        if self._rules:
            row = max(0, min(select_row, len(self._rules) - 1))
            self._list.setCurrentRow(row)
            self._load_form_for_row(row)
        else:
            self._list.setCurrentRow(-1)
            self._blank_form()
            self._prev_row = -1

    def _blank_form(self) -> None:
        self._name_edit.clear()
        self._regex_edit.clear()
        self._severity_combo.setCurrentIndex(self._severity_combo.findText("INFO"))
        self._type_edit.clear()
        self._clear_regex_highlight()

    def _load_form_for_row(self, row: int) -> None:
        if row < 0 or row >= len(self._rules):
            self._blank_form()
            return
        r = self._rules[row]
        self._name_edit.setText(r.name)
        self._regex_edit.setPlainText(r.regex_pattern)
        sev = r.normalized_severity()
        idx = self._severity_combo.findText(sev)
        if idx >= 0:
            self._severity_combo.setCurrentIndex(idx)
        self._type_edit.setText(r.error_type)
        self._clear_regex_highlight()
        self._prev_row = row

    def _read_form_into_rule(self, row: int) -> None:
        if row < 0 or row >= len(self._rules):
            return
        r = self._rules[row]
        r.name = self._name_edit.text().strip() or "Untitled"
        r.regex_pattern = self._regex_edit.toPlainText()
        r.severity = self._severity_combo.currentText()
        r.error_type = self._type_edit.text().strip() or "Custom"

    def _on_list_row_changed(self, row: int) -> None:
        if self._suppress_list_signal:
            return
        if self._prev_row >= 0:
            self._read_form_into_rule(self._prev_row)
            item = self._list.item(self._prev_row)
            if item is not None:
                item.setText(self._rules[self._prev_row].name or "(unnamed)")
        self._prev_row = row
        self._load_form_for_row(row)

    def _new_rule(self) -> None:
        if self._prev_row >= 0:
            self._read_form_into_rule(self._prev_row)
            item = self._list.item(self._prev_row)
            if item is not None:
                item.setText(self._rules[self._prev_row].name or "(unnamed)")
        self._rules.append(CustomRule.new_blank())
        self._refresh_list(select_row=len(self._rules) - 1)

    def _delete_selected(self) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        del self._rules[row]
        self._prev_row = -1
        self._refresh_list(select_row=min(row, len(self._rules) - 1))

    def _save_and_apply(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._read_form_into_rule(row)
            item = self._list.item(row)
            if item is not None:
                item.setText(self._rules[row].name or "(unnamed)")
        self._clear_regex_highlight()
        for i, rule in enumerate(self._rules):
            pat = (rule.regex_pattern or "").strip()
            if not pat:
                self._list.setCurrentRow(i)
                self._load_form_for_row(i)
                self._highlight_regex_error()
                QMessageBox.warning(
                    self,
                    "Invalid rule",
                    f"Rule “{rule.name or '(unnamed)'}” has an empty regex pattern.",
                )
                return
            try:
                re.compile(pat)
            except re.error as e:
                self._list.setCurrentRow(i)
                self._load_form_for_row(i)
                self._highlight_regex_error()
                QMessageBox.warning(
                    self,
                    "Invalid regex",
                    f"Rule “{rule.name or '(unnamed)'}” has an invalid pattern:\n{e}",
                )
                return
        get_rules_manager().save_rules(self._rules)
        self.accept()
