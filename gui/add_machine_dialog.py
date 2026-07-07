"""Add a cabinet to the fleet by IP with reverse-DNS name resolution."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from network.fleet_scanner import is_ipv4_address, resolve_cabinet_name


class AddMachineDialog(QDialog):
    """Enter an IPv4 address, resolve hostname, and accept with resolved cabinet label."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Cabinet")
        self.resize(420, 160)
        self._resolved_ip: str | None = None
        self._resolved_name: str | None = None

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(QLabel("Enter cabinet IP address:"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText("e.g. 10.0.0.90")
        form.addRow(self._edit)
        root.addLayout(form)

        self._preview = QLabel("")
        self._preview.setTextFormat(Qt.TextFormat.RichText)
        self._preview.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._preview.setStyleSheet("font-size: 11px;")
        self._preview.setWordWrap(True)
        root.addWidget(self._preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        add_btn = QPushButton("Resolve & Add")
        add_btn.setDefault(True)
        add_btn.clicked.connect(self._on_resolve_and_add)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def resolved_ip(self) -> str:
        return self._resolved_ip or ""

    def resolved_name(self) -> str:
        return self._resolved_name or ""

    def _on_resolve_and_add(self) -> None:
        ip = self._edit.text().strip()
        if not is_ipv4_address(ip):
            QMessageBox.warning(
                self,
                "Invalid IP",
                "Enter a valid IPv4 address (e.g. 10.0.0.90).",
            )
            return
        self._preview.setText("Resolving hostname…")
        QApplication.processEvents()
        name = resolve_cabinet_name(ip)
        self._preview.setText(f"Resolved label: <b>{name}</b>")
        QApplication.processEvents()
        self._resolved_ip = ip
        self._resolved_name = name
        self.accept()
