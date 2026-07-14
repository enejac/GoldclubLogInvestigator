"""Config Scanner tab for QA config SHA1 snapshots and diffs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThreadPool, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from config_manager import SettingsManager
from config_scanner.scanner import snapshot_content_root
from gui.notepad_pp import attach_open_with_npp_menu, extend_menu_with_npp_action, open_with_notepad_pp
from config_scanner.service import (
    CompareResult,
    ConfigScannerService,
    ScanResult,
    SnapshotInfo,
    scan_scope_zero_diff_hint,
)
from gui.config_scanner_worker import (
    ConfigScannerEmitter,
    SnapshotLoadResult,
    schedule_apply_snapshot,
    schedule_compare,
    schedule_delete_snapshot,
    schedule_load_snapshots,
    schedule_scan,
    schedule_set_baseline,
)


class SnapshotTableModel(QAbstractTableModel):
    HEADERS = [
        "Snapshot",
        "Profile",
        "Build",
        "Product",
        "Source",
        "Exe ver",
        "Exe name",
        "Scan time",
        "Files",
        "Baseline",
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[SnapshotInfo] = []

    def set_rows(self, rows: list[SnapshotInfo]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        if 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return row.name
            if index.column() == 1:
                return row.profile_label or "—"
            if index.column() == 2:
                return row.build_number or "—"
            if index.column() == 3:
                return row.product_version or "—"
            if index.column() == 4:
                return row.source_version or "—"
            if index.column() == 5:
                return row.exe_product_version or "—"
            if index.column() == 6:
                return row.exe_product_name or "—"
            if index.column() == 7:
                return row.scan_timestamp
            if index.column() == 8:
                return str(row.file_count)
            if index.column() == 9:
                return "Yes" if row.is_baseline else ""
        if role == Qt.ItemDataRole.UserRole:
            return row.name
        return None

    def snapshot_name_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].name
        return None


def _tight_label_field_row(label_text: str, field: QWidget, *, field_min_width: int = 240) -> QHBoxLayout:
    """Label immediately followed by field — no expanding gap between them."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    label = QLabel(label_text)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    field.setMinimumWidth(field_min_width)
    field.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    row.addWidget(label)
    row.addWidget(field)
    return row


class ConfigScannerTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._service = ConfigScannerService()
        self._pool = QThreadPool.globalInstance()
        self._emitter = ConfigScannerEmitter(self)
        self._emitter.progress.connect(self._on_progress)
        self._emitter.snapshots_loaded.connect(self._on_snapshots_loaded)
        self._emitter.snapshots_load_failed.connect(self._on_snapshots_load_failed)
        self._emitter.scan_finished.connect(self._on_scan_finished)
        self._emitter.compare_finished.connect(self._on_compare_finished)
        self._emitter.baseline_finished.connect(self._on_baseline_finished)
        self._emitter.delete_finished.connect(self._on_delete_finished)
        self._emitter.apply_finished.connect(self._on_apply_finished)

        self._snapshots: list[SnapshotInfo] = []
        self._last_report_path: Path | None = None
        self._last_compare_target_snapshot: str | None = None
        self._busy = False
        self._startup_detect_done = False
        self._initial_snapshot_load_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "<b>Config SHA1 Scanner</b> — snapshot QA config on a slot cabinet "
            "(<code>C:\\Goldclub\\slot</code>, <code>G:</code>) or roulette USB image "
            "(<code>D:\\config</code>). Slot vs roulette is detected automatically from "
            "repo markers (<code>OneHand.exe</code>, <code>Ruleta.exe</code>, "
            "<code>BuildVersion.txt</code>, RAM-clear scripts on <code>D:\\</code>). "
            "Compare snapshots after menu changes. Right-click a snapshot to write it back "
            "to the scan target (overwrite-only, scan scope)."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(intro)

        self._data_path_label = QLabel()
        self._data_path_label.setStyleSheet("color: #858585;")
        self._data_path_label.setWordWrap(True)
        root.addWidget(self._data_path_label)
        self._update_data_path_label()

        drive_row = QHBoxLayout()
        drive_row.setSpacing(6)
        drive_row.addWidget(QLabel("Scan target:"))
        self._drive_edit = QLineEdit(SettingsManager.get_config_scanner_game_drive())
        self._drive_edit.setPlaceholderText("Auto-detected: D:, G:, C:\\Goldclub\\slot, …")
        self._drive_edit.editingFinished.connect(self._persist_drive)
        drive_row.addWidget(self._drive_edit, stretch=1)
        self._detect_btn = QPushButton("Auto-detect repo")
        self._detect_btn.setToolTip(
            "Find slot or roulette game folder (OneHand.exe, Ruleta.exe, BuildVersion.txt)."
        )
        self._detect_btn.clicked.connect(self._on_detect_drive)
        drive_row.addWidget(self._detect_btn)
        self._scan_btn = QPushButton("Scan now")
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        drive_row.addWidget(self._scan_btn)
        self._refresh_btn = QPushButton("Refresh list")
        self._refresh_btn.clicked.connect(self.refresh_snapshots)
        drive_row.addWidget(self._refresh_btn)
        drive_row.addStretch(1)
        root.addLayout(drive_row)

        self._snapshot_model = SnapshotTableModel(self)
        self._snapshot_table = QTableView()
        self._snapshot_table.setModel(self._snapshot_model)
        self._snapshot_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._snapshot_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._snapshot_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._snapshot_table.setAlternatingRowColors(True)
        self._snapshot_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._snapshot_table.customContextMenuRequested.connect(self._on_snapshot_context_menu)
        root.addWidget(self._snapshot_table, stretch=2)

        compare_row = QHBoxLayout()
        compare_row.setSpacing(20)
        self._baseline_combo = QComboBox()
        compare_row.addLayout(_tight_label_field_row("Baseline:", self._baseline_combo))
        self._target_combo = QComboBox()
        compare_row.addLayout(_tight_label_field_row("Target:", self._target_combo))
        compare_row.addStretch(1)
        root.addLayout(compare_row)

        btn_row = QHBoxLayout()
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.clicked.connect(self._on_compare_clicked)
        btn_row.addWidget(self._compare_btn)
        self._compare_latest_btn = QPushButton("Quick compare")
        self._compare_latest_btn.setToolTip(
            "Baseline vs newest scan when a baseline exists; "
            "otherwise diff the two newest snapshots."
        )
        self._compare_latest_btn.clicked.connect(self._on_compare_latest_clicked)
        btn_row.addWidget(self._compare_latest_btn)

        self._more_btn = QToolButton()
        self._more_btn.setText("More")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self)
        self._set_baseline_action = QAction("Set baseline", self)
        self._set_baseline_action.setToolTip(
            "Rename the selected snapshot to slot_baseline or "
            "roulette_baseline_build… and register it as the compare baseline."
        )
        self._set_baseline_action.triggered.connect(self._on_set_baseline_clicked)
        more_menu.addAction(self._set_baseline_action)
        self._delete_action = QAction("Delete snapshot", self)
        self._delete_action.setToolTip(
            "Delete the selected snapshot from the table (or Target combo). "
            "Right-click a row for the same action."
        )
        self._delete_action.triggered.connect(self._on_delete_clicked)
        more_menu.addAction(self._delete_action)
        more_menu.addSeparator()
        self._open_report_action = QAction("Open report", self)
        self._open_report_action.triggered.connect(self._on_open_report_clicked)
        self._open_report_action.setEnabled(False)
        more_menu.addAction(self._open_report_action)
        self._open_snapshots_action = QAction("Open snapshots folder", self)
        self._open_snapshots_action.triggered.connect(self._on_open_snapshots_clicked)
        more_menu.addAction(self._open_snapshots_action)
        self._open_reports_action = QAction("Open reports folder", self)
        self._open_reports_action.triggered.connect(self._on_open_reports_clicked)
        more_menu.addAction(self._open_reports_action)
        self._more_btn.setMenu(more_menu)
        btn_row.addWidget(self._more_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._summary_label = QLabel("Summary: —")
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        self._changed_list = QPlainTextEdit()
        self._changed_list.setReadOnly(True)
        self._changed_list.setPlaceholderText("Changed files from the last compare appear here.")
        self._changed_list.setMaximumBlockCount(5000)
        root.addWidget(self._changed_list, stretch=1)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Progress and errors…")
        self._log.setMaximumBlockCount(2000)
        root.addWidget(self._log, stretch=1)

        attach_open_with_npp_menu(
            self._log,
            path_provider=lambda: self._last_report_path,
            parent=self,
            label="Open report with Notepad++",
        )
        attach_open_with_npp_menu(
            self._changed_list,
            path_provider=self._changed_list_path_at_cursor,
            parent=self,
            label="Open file with Notepad++",
        )

    def _changed_list_path_at_cursor(self) -> Path | None:
        if self._last_report_path and self._last_report_path.is_file():
            cursor = self._changed_list.textCursor()
            line = cursor.block().text().strip()
            if not line or line.startswith("(") or line.startswith("…"):
                return self._last_report_path
            if line.startswith("  "):
                return self._last_report_path
            parts = line.split("\t", 1)
            rel = (parts[0] if len(parts) == 1 else parts[1]).strip()
            if not rel:
                return self._last_report_path
            if self._last_compare_target_snapshot:
                snap_dir = self._service.get_snapshots_dir() / self._last_compare_target_snapshot
                content_root = snapshot_content_root(snap_dir)
                if content_root is not None:
                    candidate = content_root / rel.replace("/", "\\")
                    try:
                        if candidate.is_file():
                            return candidate
                    except OSError:
                        pass
        return self._last_report_path if self._last_report_path and self._last_report_path.is_file() else None

    def _ensure_initial_snapshot_load(self) -> None:
        if self._initial_snapshot_load_done:
            return
        self._initial_snapshot_load_done = True
        self.refresh_snapshots()

    def _update_data_path_label(self) -> None:
        data_root = self._service.get_data_root()
        snap_dir = self._service.get_snapshots_dir()
        self._data_path_label.setText(
            f"Data folder: {data_root}  ·  Snapshots: {snap_dir.name}\\"
        )

    def _apply_detect_result(self, result: object) -> None:
        from config_scanner.build_version import DiscoverResult

        if not isinstance(result, DiscoverResult):
            return
        self._drive_edit.setText(result.target.rstrip("\\"))
        self._persist_drive()

    def _apply_snapshot_list(
        self,
        snapshots: list[SnapshotInfo] | None = None,
        *,
        preserve_combo_selection: bool = True,
    ) -> None:
        rows = snapshots if snapshots is not None else self._service.list_snapshots()
        self._snapshots = rows
        self._snapshot_model.set_rows(rows)
        self._populate_combos(preserve_selection=preserve_combo_selection)

    def _snapshot_profile_for(self, name: str) -> tuple[str | None, str | None]:
        for row in self._snapshots:
            if row.name == name:
                return row.profile_id, row.profile_label
        return None, None

    def _snapshot_has_archive(self, snapshot_name: str) -> bool:
        snap_dir = self._service.get_snapshots_dir() / snapshot_name
        return snapshot_content_root(snap_dir) is not None

    def _snapshot_at_row(self, row: int) -> SnapshotInfo | None:
        if 0 <= row < len(self._snapshots):
            return self._snapshots[row]
        return None

    def _latest_snapshot_name_excluding(self, *exclude: str) -> str | None:
        excluded = {name for name in exclude if name}
        for row in reversed(self._snapshots):
            if row.name not in excluded:
                return row.name
        return None

    def _compare_snapshot_with_baseline(self, target_name: str) -> None:
        if self._busy:
            return
        baseline = self._service.get_baseline_name()
        if not baseline:
            QMessageBox.warning(self, "Config Scanner", "No baseline set yet.")
            return
        if target_name == baseline:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a non-baseline snapshot to compare against the baseline.",
            )
            return
        self._baseline_combo.setCurrentText(baseline)
        self._target_combo.setCurrentText(target_name)
        self._start_compare(baseline, target_name)

    def _compare_baseline_with_latest(self, baseline_name: str) -> None:
        if self._busy:
            return
        target = self._latest_snapshot_name_excluding(baseline_name)
        if not target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Need a scan newer than the baseline.",
            )
            return
        self._baseline_combo.setCurrentText(baseline_name)
        self._target_combo.setCurrentText(target)
        self._start_compare(baseline_name, target)

    def _on_snapshot_context_menu(self, pos) -> None:  # noqa: ANN001
        if self._busy:
            return
        index = self._snapshot_table.indexAt(pos)
        if not index.isValid():
            return
        snapshot = self._snapshot_at_row(index.row())
        if snapshot is None:
            return
        self._snapshot_table.selectRow(index.row())

        menu = QMenu(self)
        registered_baseline = self._service.get_baseline_name()
        is_baseline_row = snapshot.is_baseline or snapshot.name == registered_baseline
        if is_baseline_row:
            compare_action = QAction("Compare with latest scan…", self)
            compare_action.triggered.connect(
                lambda checked=False, name=snapshot.name: self._compare_baseline_with_latest(name)
            )
        else:
            compare_action = QAction("Compare with baseline…", self)
            compare_action.triggered.connect(
                lambda checked=False, name=snapshot.name: self._compare_snapshot_with_baseline(name)
            )
        menu.addAction(compare_action)
        baseline_action = QAction("Set as baseline", self)
        baseline_action.triggered.connect(self._on_set_baseline_clicked)
        menu.addAction(baseline_action)
        write_action = QAction("Write snapshot…", self)
        has_archive = self._snapshot_has_archive(snapshot.name)
        write_action.setEnabled(has_archive)
        if not has_archive:
            write_action.setToolTip("Re-scan to capture archived files")
        write_action.triggered.connect(
            lambda checked=False, name=snapshot.name: self._confirm_write_snapshot(name)
        )
        menu.addAction(write_action)
        menu.addSeparator()
        delete_action = QAction("Delete snapshot…", self)
        delete_action.triggered.connect(
            lambda checked=False, name=snapshot.name: self._confirm_delete_snapshot(name)
        )
        menu.addAction(delete_action)
        extend_menu_with_npp_action(
            menu,
            self,
            lambda: self._last_report_path,
            label="Open last report with Notepad++",
        )
        menu.exec(self._snapshot_table.viewport().mapToGlobal(pos))

    def _confirm_delete_snapshot(self, snapshot_name: str) -> None:
        if self._busy:
            return
        snapshot = next((row for row in self._snapshots if row.name == snapshot_name), None)
        baseline = self._service.get_baseline_name()
        is_registered_baseline = baseline == snapshot_name
        is_baseline_folder = snapshot.is_baseline if snapshot else False

        prompt = f"Delete snapshot?\n\n{snapshot_name}"
        if is_registered_baseline or is_baseline_folder:
            prompt += (
                "\n\nThis is the registered baseline. "
                "Deleting it will also clear the baseline setting."
            )
        prompt += "\n\nThis cannot be undone."

        reply = QMessageBox.warning(
            self,
            "Config Scanner",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        schedule_delete_snapshot(self._pool, self._service, snapshot_name, self._emitter)

    def _confirm_write_snapshot(self, snapshot_name: str) -> None:
        if self._busy:
            return
        snapshot = next((row for row in self._snapshots if row.name == snapshot_name), None)
        if snapshot is None:
            return
        if not self._snapshot_has_archive(snapshot_name):
            QMessageBox.warning(
                self,
                "Config Scanner",
                "This snapshot has no archived file content.\n\nRe-scan to capture files before writing back.",
            )
            return

        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter a scan target path before writing a snapshot to the machine.",
            )
            return

        profile_line = snapshot.profile_label or snapshot.profile_id or "—"
        version_parts = [
            part
            for part in (
                f"build {snapshot.build_number}" if snapshot.build_number else "",
                f"product {snapshot.product_version}" if snapshot.product_version else "",
            )
            if part
        ]
        version_line = ", ".join(version_parts) if version_parts else "—"

        prompt = (
            f"Write snapshot to machine?\n\n"
            f"Snapshot: {snapshot_name}\n"
            f"Profile: {profile_line}\n"
            f"Version: {version_line}\n"
            f"Files: {snapshot.file_count}\n"
            f"Scan target: {scan_target}\n\n"
            f"This overwrites up to {snapshot.file_count} config files on the live machine. "
            "Cannot be undone."
        )

        warnings = self._service.snapshot_apply_warnings(snapshot_name, scan_target)
        if warnings:
            prompt += "\n\n" + "\n".join(f"• {item}" for item in warnings)

        reply = QMessageBox.warning(
            self,
            "Config Scanner",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        schedule_apply_snapshot(
            self._pool,
            self._service,
            snapshot_name,
            scan_target,
            self._emitter,
        )

    def _on_apply_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._log.appendPlainText(("OK: " if ok else "FAIL: ") + message)
        if ok:
            return
        QMessageBox.critical(self, "Config Scanner", f"Write snapshot failed:\n\n{message}")

    def _on_delete_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._log.appendPlainText(("OK: " if ok else "FAIL: ") + message)
        if ok:
            if isinstance(result, str) and self._service.get_baseline_name() is None:
                self._summary_label.setText("Summary: —")
            self.refresh_snapshots()
            return
        QMessageBox.critical(self, "Config Scanner", f"Delete failed:\n\n{message}")

    def _resolve_snapshot_for_baseline(self) -> str | None:
        selected = self._snapshot_table.selectionModel().selectedRows()
        if selected:
            name = self._snapshot_model.snapshot_name_at(selected[0].row())
            if name:
                return name
        for combo in (self._target_combo, self._baseline_combo):
            text = combo.currentText().strip()
            if text:
                return text
        if self._snapshots:
            return self._snapshots[-1].name
        return None

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._ensure_initial_snapshot_load()
        if not self._startup_detect_done:
            self._startup_detect_done = True
            saved_target = SettingsManager.get_config_scanner_game_drive().strip()
            if not saved_target:
                self._run_auto_detect(silent=True)

    def _run_auto_detect(self, *, silent: bool) -> None:
        hint = self._drive_edit.text().strip() or None
        try:
            result = self._service.auto_detect_repo(hint)
        except FileNotFoundError as exc:
            if not silent:
                QMessageBox.warning(self, "Config Scanner", str(exc))
            self._log.appendPlainText(f"Auto-detect: {exc}")
            return
        self._apply_detect_result(result)
        self._log.appendPlainText(
            f"Auto-detected {result.profile_label} at {result.target}"
        )

    def refresh_snapshots(self) -> None:
        schedule_load_snapshots(self._pool, self._service, self._emitter)

    def _persist_drive(self) -> None:
        SettingsManager.set_config_scanner_game_drive(self._drive_edit.text().strip())

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._scan_btn.setEnabled(not busy)
        self._compare_btn.setEnabled(not busy)
        self._compare_latest_btn.setEnabled(not busy)
        self._detect_btn.setEnabled(not busy)
        self._refresh_btn.setEnabled(not busy)
        self._more_btn.setEnabled(not busy)
        self._set_baseline_action.setEnabled(not busy)
        self._delete_action.setEnabled(not busy)
        self._baseline_combo.setEnabled(not busy)
        self._target_combo.setEnabled(not busy)
        self._drive_edit.setEnabled(not busy)
        self._snapshot_table.setEnabled(not busy)
        report_ok = (
            not busy
            and self._last_report_path is not None
            and self._last_report_path.is_file()
        )
        self._open_report_action.setEnabled(report_ok)
        self._open_snapshots_action.setEnabled(not busy)
        self._open_reports_action.setEnabled(not busy)

    def _on_progress(self, message: str) -> None:
        self._log.appendPlainText(message)

    def _populate_combos(self, *, preserve_selection: bool = True) -> None:
        names = [row.name for row in self._snapshots]
        baseline = self._service.get_baseline_name()
        prev_baseline = self._baseline_combo.currentText().strip() if preserve_selection else ""
        prev_target = self._target_combo.currentText().strip() if preserve_selection else ""
        self._baseline_combo.clear()
        self._target_combo.clear()
        self._baseline_combo.addItems(names)
        self._target_combo.addItems(names)
        if not names:
            return
        target_name = next(
            (row.name for row in reversed(self._snapshots) if row.name != baseline),
            names[-1],
        )
        if preserve_selection and prev_baseline in names:
            self._baseline_combo.setCurrentText(prev_baseline)
        elif baseline and baseline in names:
            self._baseline_combo.setCurrentText(baseline)
        else:
            self._baseline_combo.setCurrentIndex(0)
        if preserve_selection and prev_target in names:
            self._target_combo.setCurrentText(prev_target)
        else:
            self._target_combo.setCurrentText(target_name)

    def _compare_pair_or_warn(self) -> tuple[str, str] | None:
        baseline = self._baseline_combo.currentText().strip()
        target = self._target_combo.currentText().strip()
        canonical = self._service.get_baseline_name()
        if canonical and canonical in {baseline, target}:
            baseline = canonical
            if target == baseline:
                target = next(
                    (row.name for row in reversed(self._snapshots) if row.name != baseline),
                    "",
                )
        if not baseline or not target:
            QMessageBox.warning(self, "Config Scanner", "Select baseline and target snapshots.")
            return None
        if baseline == target:
            QMessageBox.warning(self, "Config Scanner", "Baseline and target must differ.")
            return None
        return baseline, target

    def _confirm_compare_or_abort(self, baseline: str, target: str) -> bool:
        warnings = self._service.compare_warnings(baseline, target)
        if not warnings:
            return True
        text = (
            "This comparison may not be meaningful:\n\n"
            + "\n".join(f"• {item}" for item in warnings)
            + "\n\nContinue anyway?"
        )
        reply = QMessageBox.warning(
            self,
            "Config Scanner",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _format_changed_list_text(self, result: CompareResult) -> str:
        changed = [item for item in result.file_diffs if item.status != "unchanged"]
        if not changed:
            return "(no changes)"
        lines: list[str] = []
        for item in changed:
            lines.append(f"{item.status}\t{item.relative_path}")
            if item.content_diff:
                for line in item.content_diff[:8]:
                    detail = line.path
                    if line.old_value != line.new_value:
                        detail = f"{line.path}: {line.old_value!r} -> {line.new_value!r}"
                    lines.append(f"  {detail}")
                if len(item.content_diff) > 8:
                    lines.append(
                        f"  … {len(item.content_diff) - 8} more line(s); open report for full diff"
                    )
        return "\n".join(lines)

    def _log_compare_result(self, result: CompareResult) -> None:
        changed = [item for item in result.file_diffs if item.status != "unchanged"]
        if not changed:
            profile_id, profile_label = self._snapshot_profile_for(result.target_snapshot)
            if not profile_id and not profile_label:
                profile_id, profile_label = self._snapshot_profile_for(result.baseline_snapshot)
            self._log.appendPlainText(
                "No differences in scanned config files.\n"
                + scan_scope_zero_diff_hint(profile_id, profile_label)
            )

    def _on_snapshots_loaded(self, payload: object) -> None:
        if not isinstance(payload, SnapshotLoadResult):
            return
        self._apply_snapshot_list(payload.snapshots)
        self._update_data_path_label()

    def _on_snapshots_load_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Config Scanner", message)

    def _on_detect_drive(self) -> None:
        if self._busy:
            return
        self._run_auto_detect(silent=False)

    def _on_scan_clicked(self) -> None:
        if self._busy:
            return
        target = self._drive_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "Config Scanner", "Enter a scan target (e.g. D: or UNC path).")
            return
        self._persist_drive()
        self._set_busy(True)
        schedule_scan(self._pool, self._service, target, self._emitter)

    def _on_scan_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._log.appendPlainText(("OK: " if ok else "FAIL: ") + message)
        if ok and isinstance(result, ScanResult):
            self.refresh_snapshots()
            if self._service.get_baseline_name():
                self._log.appendPlainText(
                    "Tip: click Quick compare to diff against the registered baseline."
                )
        elif not ok:
            QMessageBox.critical(self, "Config Scanner", message)

    def _selected_compare_pair(self) -> tuple[str, str] | None:
        return self._compare_pair_or_warn()

    def _start_compare(self, baseline: str, target: str) -> None:
        if self._busy:
            return
        if not self._confirm_compare_or_abort(baseline, target):
            return
        self._set_busy(True)
        schedule_compare(self._pool, self._service, baseline, target, self._emitter)

    def _on_compare_clicked(self) -> None:
        if self._busy:
            return
        pair = self._selected_compare_pair()
        if pair:
            self._start_compare(*pair)

    def _on_compare_latest_clicked(self) -> None:
        if self._busy:
            return
        if len(self._snapshots) < 2:
            QMessageBox.warning(self, "Config Scanner", "Need at least two snapshots.")
            return
        baseline = self._service.get_baseline_name()
        if baseline:
            target = next(
                (row.name for row in reversed(self._snapshots) if row.name != baseline),
                None,
            )
            if not target:
                QMessageBox.warning(self, "Config Scanner", "Need a scan newer than the baseline.")
                return
        else:
            baseline = self._snapshots[-2].name
            target = self._snapshots[-1].name
        self._baseline_combo.setCurrentText(baseline)
        self._target_combo.setCurrentText(target)
        self._start_compare(baseline, target)

    def _on_delete_clicked(self) -> None:
        if self._busy:
            return
        name = self._resolve_snapshot_for_baseline()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a snapshot in the table (or Target combo), then click Delete snapshot.",
            )
            return
        self._confirm_delete_snapshot(name)

    def _on_set_baseline_clicked(self) -> None:
        if self._busy:
            return
        name = self._resolve_snapshot_for_baseline()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "No snapshot to use as baseline.\n\nRun Scan now first, then click Set baseline.",
            )
            return

        self._log.appendPlainText(f"Setting baseline from {name} …")
        self._set_busy(True)
        schedule_set_baseline(self._pool, self._service, name, self._emitter)

    def _on_baseline_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._log.appendPlainText(("OK: " if ok else "FAIL: ") + message)
        if ok and isinstance(result, str):
            new_name = result
            snap_dir = self._service.get_snapshots_dir()
            folder = snap_dir / new_name
            self._apply_snapshot_list()
            self._update_data_path_label()
            self._baseline_combo.setCurrentText(new_name)
            self._summary_label.setText(f"Baseline: {new_name}")
            QMessageBox.information(
                self,
                "Config Scanner",
                f"Baseline set to:\n{new_name}\n\nFolder:\n{folder}",
            )
            return

        if not ok:
            snap_dir = self._service.get_snapshots_dir()
            if isinstance(message, str) and "not found" in message.lower():
                QMessageBox.warning(self, "Config Scanner", message)
            elif isinstance(message, str) and (
                "Could not move" in message or "rename" in message.lower()
            ):
                QMessageBox.critical(
                    self,
                    "Config Scanner",
                    f"Could not rename baseline snapshot:\n\n{message}\n\nSnapshots folder:\n{snap_dir}",
                )
            else:
                QMessageBox.critical(self, "Config Scanner", f"Set baseline failed:\n\n{message}")

    def _on_compare_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        if ok and isinstance(result, CompareResult):
            self._log.appendPlainText(f"OK: Compare finished ({result.report_path.name}).")
            self._last_report_path = result.report_path
            self._last_compare_target_snapshot = result.target_snapshot
            self._open_report_action.setEnabled(True)
            summary = result.summary
            warning_text = ""
            if result.warnings:
                warning_text = "  |  " + "  |  ".join(result.warnings)
            self._summary_label.setText(
                "Summary: "
                f"added={summary['added']}, removed={summary['removed']}, "
                f"modified={summary['modified']}, unchanged={summary['unchanged']}"
                f"{warning_text}"
            )
            self._changed_list.setPlainText(self._format_changed_list_text(result))
            self._log_compare_result(result)
        else:
            self._log.appendPlainText(("OK: " if ok else "FAIL: ") + message)
            if not ok:
                QMessageBox.critical(self, "Config Scanner", message)

    def _on_open_report_clicked(self) -> None:
        if not self._last_report_path or not self._last_report_path.is_file():
            QMessageBox.warning(self, "Config Scanner", "No report available yet.")
            return
        ok, msg = open_with_notepad_pp(self._last_report_path)
        if not ok:
            QMessageBox.warning(self, "Config Scanner", msg)

    def _open_folder(self, folder: Path) -> None:
        path = folder.resolve()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_open_snapshots_clicked(self) -> None:
        self._open_folder(self._service.get_snapshots_dir())

    def _on_open_reports_clicked(self) -> None:
        self._open_folder(self._service.get_reports_dir())
