"""SAS accounting verification: cabinet XML comparison, SlotLog accounting, decoded hex."""

from __future__ import annotations

import html

from PySide6.QtGui import QFont, QFontInfo
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class SASAnalyzerDialog(QDialog):
    """Legacy plain-text viewer (single pane)."""

    def __init__(
        self,
        parsed_text: str,
        file_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"SAS log — {file_path}")
        self.resize(800, 600)

        root = QVBoxLayout(self)
        self._text = QTextBrowser()
        self._text.setReadOnly(True)
        self._text.setPlainText(parsed_text)
        font = QFont("Consolas", 10)
        if not QFontInfo(font).exactMatch():
            font = QFont("Courier New", 10)
        self._text.setFont(font)
        root.addWidget(self._text)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)


def _plain_with_matches_to_html(plain: str) -> str:
    """Color MATCH (green) and MISMATCH / NO_LOG (red bold); neutral for headers."""
    parts: list[str] = [
        "<pre style=\"font-family:Consolas,'Courier New',monospace;white-space:pre-wrap\">"
    ]
    for line in plain.split("\n"):
        esc = html.escape(line)
        if "MISMATCH" in line:
            parts.append(f'<span style="color:#b00020;font-weight:700">{esc}</span>\n')
        elif line.strip().startswith("MATCH:"):
            parts.append(f'<span style="color:#0a6d0a">{esc}</span>\n')
        else:
            parts.append(f"{esc}\n")
    parts.append("</pre>")
    return "".join(parts)


def _gm2au_table_to_html(plain: str) -> str:
    """Monospace block for SAS vs gm2au ASCII table."""
    esc = html.escape(plain)
    return (
        "<pre style=\"font-family:Consolas,'Courier New',monospace;white-space:pre-wrap\">"
        f"{esc}</pre>"
    )


class SASVerificationReportDialog(QDialog):
    """
    Side-by-side: SAS vs gm2au (left), SAS vs SlotLog accounting (right, colored).
    """

    def __init__(
        self,
        *,
        gm2au_report_plain: str,
        slotlog_report_plain: str,
        raw_decode_plain: str,
        sas_file_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("SAS accounting verification")
        self.resize(1100, 700)

        self._full_plain = self._build_copy_text(
            gm2au_report_plain, slotlog_report_plain, raw_decode_plain, sas_file_path
        )

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"<b>File:</b> {html.escape(sas_file_path)}"))

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>SAS vs cabinet (gm2au XML)</b>"))
        br_left = QTextBrowser()
        br_left.setReadOnly(True)
        br_left.setHtml(_gm2au_table_to_html(gm2au_report_plain))
        ll.addWidget(br_left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("<b>SAS vs SlotLog (Accounting::UpdateRegisters)</b>"))
        br_right = QTextBrowser()
        br_right.setReadOnly(True)
        br_right.setHtml(_plain_with_matches_to_html(slotlog_report_plain))
        rl.addWidget(br_right)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        root.addWidget(split, stretch=1)

        dec_label = QLabel("<b>Decoded SAS 6F (last packets)</b>")
        root.addWidget(dec_label)
        br_dec = QTextBrowser()
        br_dec.setReadOnly(True)
        br_dec.setMaximumHeight(200)
        br_dec.setPlainText(raw_decode_plain)
        df = QFont("Consolas", 9)
        if not QFontInfo(df).exactMatch():
            df = QFont("Courier New", 9)
        br_dec.setFont(df)
        root.addWidget(br_dec)

        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = QPushButton("Copy report")
        copy_btn.clicked.connect(self._copy_full)
        row.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

    def _build_copy_text(
        self,
        g: str,
        s: str,
        raw: str,
        path: str,
    ) -> str:
        return (
            f"SAS accounting verification\nFile: {path}\n\n"
            f"=== SAS vs gm2au ===\n{g}\n\n"
            f"=== SAS vs SlotLog ===\n{s}\n\n"
            f"=== Decoded 6F ===\n{raw}\n"
        )

    def _copy_full(self) -> None:
        QApplication.clipboard().setText(self._full_plain)

    def full_report_plain(self) -> str:
        return self._full_plain
