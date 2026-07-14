from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.automation_worker import AutomationEmitter, schedule_automation_run
from gui.notepad_pp import attach_open_with_npp_menu


class AutomationTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._pool = QThreadPool.globalInstance()
        self._emitter = AutomationEmitter(self)
        self._emitter.progress.connect(self._on_progress)
        self._emitter.finished.connect(self._on_finished)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel("<b>Automated Tests</b>  <span style='color:#858585'>(cabinet spin runner)</span>")
        hdr.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(hdr)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cabinet IP:"))
        self._ip = QLineEdit("10.0.0.90")
        self._ip.setPlaceholderText("10.0.0.90")
        self._ip.setMaximumWidth(220)
        row.addWidget(self._ip)
        row.addSpacing(16)
        row.addWidget(QLabel("Theme:"))
        self._theme = QLineEdit("TutankhamenGSBHW")
        self._theme.setPlaceholderText("TutankhamenGSBHW")
        self._theme.setMaximumWidth(200)
        row.addWidget(self._theme)
        row.addSpacing(16)
        row.addWidget(QLabel("Forced combos (one per line, | between rows):"))
        row.addStretch(1)
        root.addLayout(row)

        self._combos = QPlainTextEdit()
        from automation.tutankhamen_symbols import tutankhamen_symbol_sweep_combos_text

        self._combos.setPlaceholderText(
            "Tutankhamen F11 IDs are 0..14 (0=WILD). Three-row sweep example:\n"
            "0 0 0 0 0|1 1 1 1 1|2 2 2 2 2"
        )
        self._combos.setPlainText(tutankhamen_symbol_sweep_combos_text())
        self._combos.setMaximumBlockCount(2000)
        root.addWidget(self._combos, stretch=1)

        btn_row = QHBoxLayout()
        self._load_sweep_btn = QPushButton("Load Tutankhamen 0–11 sweep")
        self._load_sweep_btn.clicked.connect(self._on_load_tutankhamen_sweep)
        btn_row.addWidget(self._load_sweep_btn)
        self._run_btn = QPushButton("Run automation")
        self._run_btn.clicked.connect(self._on_run_clicked)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Run log…")
        self._log.setMaximumBlockCount(4000)
        root.addWidget(self._log, stretch=2)
        attach_open_with_npp_menu(
            self._log,
            path_provider=self._automation_results_path,
            parent=self,
            label="Open results.jsonl with Notepad++",
        )

        self._active_out_dir: Path | None = None

    def _automation_results_path(self) -> Path | None:
        if self._active_out_dir is None:
            return None
        path = self._active_out_dir / "results.jsonl"
        return path if path.is_file() else None

    def _on_load_tutankhamen_sweep(self) -> None:
        from automation.tutankhamen_symbols import tutankhamen_symbol_sweep_combos_text

        self._combos.setPlainText(tutankhamen_symbol_sweep_combos_text())

    def _on_run_clicked(self) -> None:
        ip = (self._ip.text() or "").strip()
        if not ip:
            QMessageBox.warning(self, "Automation", "Enter a cabinet IP.")
            return
        from automation.input_script import normalize_combo_text

        combos = [
            normalize_combo_text(ln.strip())
            for ln in (self._combos.toPlainText() or "").splitlines()
            if ln.strip()
        ]
        if not combos:
            QMessageBox.warning(self, "Automation", "Enter at least one forced combo.")
            return

        self._run_btn.setEnabled(False)
        self._log.clear()
        theme = (self._theme.text() or "").strip() or None
        themes = [theme] if theme else None
        self._active_out_dir = schedule_automation_run(
            self._pool,
            ip=ip,
            themes=themes,
            forced_combos=combos,
            emitter=self._emitter,
        )
        self._on_progress(f"Output folder: {self._active_out_dir}")

    def _on_progress(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_finished(self, ok: bool, msg: str) -> None:
        self._run_btn.setEnabled(True)
        self._log.appendPlainText("")
        self._log.appendPlainText(("DONE OK: " if ok else "DONE FAIL: ") + msg)

