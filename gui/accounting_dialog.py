"""Side-by-side view of decoded accounting QR and ``gm2au`` backend state."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.accounting_scan_worker import AccountingScanEmitter, schedule_accounting_scan


class AccountingAnalyzerDialog(QDialog):
    def __init__(
        self,
        qr_text: str,
        state_text: str,
        remote_ip: str,
        thread_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Accounting QR vs backend state")
        self.resize(800, 600)

        self._remote_ip = (remote_ip or "").strip()
        self._thread_pool = thread_pool
        self._refresh_emitter = AccountingScanEmitter(self)
        self._refresh_emitter.finished.connect(self._on_refresh_finished)

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QLabel("Decoded QR Payload"))
        self._qr_edit = QTextEdit()
        self._qr_edit.setReadOnly(True)
        self._qr_edit.setPlainText(qr_text)
        left_l.addWidget(self._qr_edit)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.addWidget(QLabel("Backend State (gm2au)"))
        self._state_edit = QTextEdit()
        self._state_edit.setReadOnly(True)
        self._state_edit.setPlainText(state_text)
        right_l.addWidget(self._state_edit)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([400, 400])
        root.addWidget(splitter)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_refresh = QPushButton("\U0001f504 Refresh Data")
        self._btn_refresh.setToolTip(
            "Re-capture the remote screen, decode the QR, and reload gm2au state."
        )
        self._btn_refresh.clicked.connect(self._on_refresh_clicked)
        if not self._remote_ip:
            self._btn_refresh.setEnabled(False)
            self._btn_refresh.setToolTip("No remote IP available for refresh.")
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_refresh)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _on_refresh_clicked(self) -> None:
        if not self._remote_ip:
            return
        self._btn_refresh.setEnabled(False)
        self._btn_refresh.setText("\U0001f504 Fetching...")
        schedule_accounting_scan(
            self._thread_pool,
            self._remote_ip,
            self._refresh_emitter,
        )

    def _on_refresh_finished(self, qr_text: str, state_text: str) -> None:
        self._qr_edit.setPlainText(qr_text)
        self._state_edit.setPlainText(state_text)
        self._btn_refresh.setEnabled(True)
        self._btn_refresh.setText("\U0001f504 Refresh Data")
