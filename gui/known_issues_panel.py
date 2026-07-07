"""Catalog + session summary for data/known_issues.json pattern matching."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.palette_adapt import muted_text, text_success
from gui.theme import COLOR_ACCENT, COLOR_MEDIUM
from parser import Incident
from parser_rules import (
    KnownIssueMatch,
    format_related_tracking,
    list_known_issue_catalog,
    match_known_issue,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class KnownIssuesPanel(QFrame):
    """Shows the known-issue catalog and highlights the selected row match."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("knownIssuesPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title = QLabel()
        title.setObjectName("knownIssuesTitle")
        root.addWidget(title)
        self._title = title

        self._session_lbl = QLabel("Session: scan logs to see pattern matches.")
        self._session_lbl.setWordWrap(True)
        root.addWidget(self._session_lbl)

        self._catalog_host = QVBoxLayout()
        self._catalog_host.setSpacing(4)
        root.addLayout(self._catalog_host)

        self._selection_lbl = QLabel("Selected row: —")
        self._selection_lbl.setWordWrap(True)
        root.addWidget(self._selection_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._open_ticket_btn = QPushButton("Open local ticket")
        self._open_ticket_btn.setEnabled(False)
        self._open_ticket_btn.clicked.connect(self._on_open_ticket)
        self._copy_tracking_btn = QPushButton("Copy tracking block")
        self._copy_tracking_btn.setEnabled(False)
        self._copy_tracking_btn.clicked.connect(self._on_copy_tracking)
        btn_row.addWidget(self._open_ticket_btn)
        btn_row.addWidget(self._copy_tracking_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._current_match: KnownIssueMatch | None = None
        self._current_ticket_path: Path | None = None
        self._session_counts: dict[str, int] = {}
        self._session_total: int = 0
        self._selected_incident: Incident | None = None
        self._rebuild_catalog_cards()
        self._refresh_title()
        self._refresh_plain_labels()

    def _refresh_title(self) -> None:
        text, muted, _accent, _success, _medium = self._html_colors()
        self._title.setText(
            f"<span style='color:{text}'><b>Known issue patterns</b></span> "
            f"<span style='color:{muted}'>(data/known_issues.json)</span>"
        )
        self._title.setTextFormat(Qt.TextFormat.RichText)

    def _html_colors(self) -> tuple[str, str, str, str, str]:
        """Rich-text colors from the active palette (HTML defaults to black otherwise)."""
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.Text).name()
        muted = muted_text(pal).name()
        accent = pal.color(QPalette.ColorRole.Link).name() or COLOR_ACCENT
        success = text_success(pal).name()
        medium = COLOR_MEDIUM if pal.color(QPalette.ColorRole.Window).lightness() <= 127 else "#996633"
        return text, muted, accent, success, medium

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._rebuild_catalog_cards()
            self._refresh_plain_labels()
            if self._session_counts:
                self.set_session_counts(self._session_counts, self._session_total)
            else:
                self.set_session_counts({}, self._session_total)
            self.set_selected_incident(self._selected_incident)

    def _refresh_plain_labels(self) -> None:
        _text, muted, _accent, _success, medium = self._html_colors()
        if not self._session_counts:
            self._session_lbl.setStyleSheet(f"color: {muted}; font-size: 12px;")
        if self._selected_incident is None:
            self._selection_lbl.setStyleSheet(
                f"color: {medium}; font-size: 12px; padding-top: 4px;"
            )

    def _rebuild_catalog_cards(self) -> None:
        while self._catalog_host.count():
            item = self._catalog_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        entries = list_known_issue_catalog()
        text, muted, accent, _success, _medium = self._html_colors()
        if not entries:
            empty = QLabel("No catalog entries in data/known_issues.json.")
            empty.setStyleSheet(f"color: {muted};")
            self._catalog_host.addWidget(empty)
            return

        for entry in entries:
            tests = ", ".join(entry.jira_test_keys) if entry.jira_test_keys else "—"
            bug = entry.jira_bug_key or "—"
            card = QLabel(
                f"<b style='color:{accent}'>{entry.issue_id}</b>"
                f"<span style='color:{text}'> — {entry.name}</span><br>"
                f"<span style='color:{muted}'>Jira bug: {bug} · Tests: {tests}</span>"
            )
            card.setTextFormat(Qt.TextFormat.RichText)
            card.setWordWrap(True)
            self._catalog_host.addWidget(card)

    def set_session_counts(self, counts: dict[str, int], total_incidents: int) -> None:
        self._session_counts = dict(counts)
        self._session_total = total_incidents
        text, muted, _accent, success, _medium = self._html_colors()
        if not counts:
            self._session_lbl.setText(
                f"Session: {total_incidents} incident(s) — no known-issue pattern matches yet."
            )
            self._session_lbl.setTextFormat(Qt.TextFormat.PlainText)
            self._session_lbl.setStyleSheet(f"color: {muted}; font-size: 12px;")
            return
        parts = [f"{n}× {issue_id}" for issue_id, n in sorted(counts.items())]
        self._session_lbl.setText(
            f"Session: <b style='color:{success}'>{sum(counts.values())}</b>"
            f"<span style='color:{text}'> match(es) in {total_incidents} incident(s) — "
            f"{', '.join(parts)}. Use quick filter <b>Known issue</b>.</span>"
        )
        self._session_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._session_lbl.setStyleSheet("font-size: 12px; background: transparent;")

    def set_selected_incident(self, incident: Incident | None) -> None:
        self._selected_incident = incident
        self._current_match = None
        self._current_ticket_path = None
        self._open_ticket_btn.setEnabled(False)
        self._copy_tracking_btn.setEnabled(False)

        text, muted, _accent, success, medium = self._html_colors()

        if incident is None:
            self._selection_lbl.setText("Selected row: —")
            self._selection_lbl.setTextFormat(Qt.TextFormat.PlainText)
            self._selection_lbl.setStyleSheet(
                f"color: {medium}; font-size: 12px; padding-top: 4px;"
            )
            return

        match = match_known_issue(incident.line_snippet or "", incident.severity or "")
        if match is None:
            self._selection_lbl.setText(
                f"Selected row: <span style='color:{muted}'>no catalog pattern match</span>"
            )
            self._selection_lbl.setTextFormat(Qt.TextFormat.RichText)
            self._selection_lbl.setStyleSheet("font-size: 12px; padding-top: 4px; background: transparent;")
            return

        self._current_match = match
        if match.local_ticket:
            self._current_ticket_path = (_REPO_ROOT / match.local_ticket).resolve()
            self._open_ticket_btn.setEnabled(self._current_ticket_path.is_file())
        self._copy_tracking_btn.setEnabled(True)

        self._selection_lbl.setText(
            f"Selected row: <b style='color:{success}'>MATCHES {match.issue_id}</b>"
            f"<span style='color:{text}'> — {match.name}</span>"
        )
        self._selection_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._selection_lbl.setStyleSheet("font-size: 12px; padding-top: 4px; background: transparent;")

    def _on_open_ticket(self) -> None:
        path = self._current_ticket_path
        if path is None or not path.is_file():
            return
        url = QUrl.fromLocalFile(str(path))
        if not QDesktopServices.openUrl(url):
            if os.name == "nt":
                subprocess.Popen(["notepad.exe", str(path)], close_fds=True)

    def _on_copy_tracking(self) -> None:
        if self._current_match is None:
            return
        text = format_related_tracking(self._current_match)
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
