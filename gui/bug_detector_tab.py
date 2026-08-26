"""GUI tab: Bug Detector — live session recorder + reports."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.bug_detector_worker import BugSessionThread
from network.bug_session_events import CATEGORY_COLORS, EventCategory
from network.bug_session_reports import render_developer_session, render_tester_session
from network.bug_detector import scan_and_write_reports
from PySide6.QtCore import QThreadPool
from gui.bug_detector_worker import BugDetectorEmitter, schedule_bug_detector_scan


class BugDetectorTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._thread: BugSessionThread | None = None
        self._last_output_dir = ""
        self._events = []
        self._pool = QThreadPool.globalInstance()
        self._scan_emitter = BugDetectorEmitter(self)
        self._scan_emitter.progress.connect(self._on_scan_progress)
        self._scan_emitter.finished.connect(self._on_scan_finished)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        hdr = QLabel(
            "<b>Bug Detector</b>  "
            "<span style=\"color:#858585\">"
            "Live roulette session: tail godot/ruleta/Aurum/SAS logs + optional "
            "read-only WdSniff (8090 / Dallas 30300 / SAS 30550). "
            "Color-coded timeline; auto-stop on critical; tester + developer reports."
            "</span>"
        )
        hdr.setWordWrap(True)
        hdr.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(hdr)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cabinet IP:"))
        self._ip = QLineEdit("10.0.0.90")
        self._ip.setMaximumWidth(140)
        row.addWidget(self._ip)
        self._sniff = QCheckBox("Sniff 8090/30300/30550")
        self._sniff.setChecked(True)
        row.addWidget(self._sniff)
        self._auto = QCheckBox("Auto-stop on critical")
        self._auto.setChecked(True)
        row.addWidget(self._auto)
        self._start_btn = QPushButton("Start session")
        self._start_btn.clicked.connect(self._on_start)
        row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("Stop & write reports")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        row.addWidget(self._stop_btn)
        self._scan_btn = QPushButton("Scan past logs")
        self._scan_btn.clicked.connect(self._on_scan_past)
        row.addWidget(self._scan_btn)
        self._open_btn = QPushButton("Open folder")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_folder)
        row.addWidget(self._open_btn)
        row.addStretch(1)
        root.addLayout(row)

        legend = QLabel(
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.HUMAN]}\">user</span> · "
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.HARDWARE]}\">hardware</span> · "
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.SAS]}\">SAS/cashless</span> · "
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.MIDDLEWARE]}\">middleware</span> · "
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.FREE_FLOW]}\">machine</span> · "
            f"<span style=\"color:{CATEGORY_COLORS[EventCategory.CRITICAL]}\">critical</span>"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(legend)

        self._status = QLabel("Idle — press Start session, then reproduce on the cabinet.")
        self._status.setStyleSheet("color:#858585;")
        root.addWidget(self._status)

        split = QSplitter(Qt.Orientation.Vertical)
        self._live = QTextEdit()
        self._live.setReadOnly(True)
        self._live.setPlaceholderText("Live color timeline…")
        split.addWidget(self._live)

        panes = QSplitter(Qt.Orientation.Horizontal)
        left = QVBoxLayout()
        left_w = QWidget()
        left_w.setLayout(left)
        left.addWidget(QLabel("Tester report"))
        self._tester = QTextBrowser()
        left.addWidget(self._tester)
        panes.addWidget(left_w)

        right = QVBoxLayout()
        right_w = QWidget()
        right_w.setLayout(right)
        right.addWidget(QLabel("Developer report"))
        self._dev = QTextBrowser()
        right.addWidget(self._dev)
        panes.addWidget(right_w)
        split.addWidget(panes)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, stretch=1)

    def set_cabinet_ip(self, ip: str) -> None:
        text = (ip or "").strip()
        if text and self._ip.text().strip() != text:
            self._ip.setText(text)

    def _on_start(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        cab = (self._ip.text() or "").strip() or "10.0.0.90"
        self._events.clear()
        self._live.clear()
        self._tester.clear()
        self._dev.clear()
        self._status.setText(f"Recording on {cab}…")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._scan_btn.setEnabled(False)
        self._thread = BugSessionThread(
            cab,
            enable_sniff=self._sniff.isChecked(),
            auto_stop_on_critical=self._auto.isChecked(),
            parent=self,
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.event_html.connect(self._on_event_html)
        self._thread.event_obj.connect(self._on_event_obj)
        self._thread.finished_result.connect(self._on_finished)
        self._thread.start()

    def _on_stop(self) -> None:
        if self._thread:
            self._status.setText("Stopping…")
            self._thread.request_stop()

    def _on_progress(self, msg: str) -> None:
        self._status.setText(msg)

    def _on_event_html(self, html: str) -> None:
        self._live.append(html)
        self._live.moveCursor(QTextCursor.MoveOperation.End)

    def _on_event_obj(self, ev: object) -> None:
        self._events.append(ev)

    def _on_finished(self, result: object) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._scan_btn.setEnabled(True)
        self._thread = None
        from network.bug_session_engine import BugSessionResult

        if not isinstance(result, BugSessionResult):
            self._status.setText("Session ended with unexpected result")
            return
        self._last_output_dir = result.output_dir
        self._open_btn.setEnabled(bool(result.output_dir))
        if result.tester_path and Path(result.tester_path).is_file():
            self._tester.setMarkdown(Path(result.tester_path).read_text(encoding="utf-8"))
        else:
            self._tester.setMarkdown(
                render_tester_session(
                    cabinet=result.cabinet,
                    events=list(result.events),
                    started_utc="",
                    stopped_reason=result.stopped_reason,
                )
            )
        if result.developer_path and Path(result.developer_path).is_file():
            self._dev.setMarkdown(Path(result.developer_path).read_text(encoding="utf-8"))
        else:
            self._dev.setMarkdown(
                render_developer_session(
                    cabinet=result.cabinet,
                    events=list(result.events),
                    started_utc="",
                    stopped_reason=result.stopped_reason,
                    sniff_used=result.sniff_used,
                )
            )
        self._status.setText(
            f"Stopped ({result.stopped_reason}). "
            f"{len(result.events)} events. "
            f"{result.output_dir}"
        )

    def _on_scan_past(self) -> None:
        cab = (self._ip.text() or "").strip() or "local"
        self._status.setText(f"Scanning past logs on {cab}…")
        self._scan_btn.setEnabled(False)
        schedule_bug_detector_scan(self._pool, cabinet=cab, emitter=self._scan_emitter)

    def _on_scan_progress(self, msg: str) -> None:
        self._status.setText(msg)

    def _on_scan_finished(self, ok: bool, log: str, result: object) -> None:
        self._scan_btn.setEnabled(True)
        if not ok or result is None:
            self._status.setText(f"Past scan failed: {log}")
            return
        out = getattr(result, "output_dir", "") or ""
        self._last_output_dir = out
        self._open_btn.setEnabled(bool(out))
        n = len(getattr(result, "incidents", []) or [])
        self._status.setText(f"Past scan: {n} incident(s). {out}")
        # show latest pair if present
        t_paths = getattr(result, "tester_paths", []) or []
        d_paths = getattr(result, "developer_paths", []) or []
        if t_paths:
            self._tester.setMarkdown(Path(t_paths[0]).read_text(encoding="utf-8"))
        if d_paths:
            self._dev.setMarkdown(Path(d_paths[0]).read_text(encoding="utf-8"))

    def _on_open_folder(self) -> None:
        if not self._last_output_dir:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_output_dir))
