"""Config Scanner tab for QA config SHA1 snapshots and diffs."""

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from config_manager import SettingsManager
from gui.app_logging import get_logger
from config_scanner.profiles import display_profile_label
from config_scanner.scanner import snapshot_content_root
from gui.notepad_pp import attach_open_with_npp_menu, extend_menu_with_npp_action, open_with_notepad_pp
from config_scanner.report import (
    COMPARE_PANEL_MAX_CHANGES_PER_FILE,
    apply_button_label,
    compare_panel_encrypted_origin_styles,
    compare_panel_file_slice,
    compare_panel_layout_metrics,
    content_change_panel_partition,
    estimate_compare_panel_height,
    file_diff_header_label,
    filter_file_diffs_for_find,
    format_apply_value_detail,
    format_compare_summary,
    format_diff_cell_value,
    format_setting_change_description,
    is_actionable_content_change,
    setting_display_name,
)
from config_scanner.service import (
    CompareResult,
    ConfigScannerService,
    ScanResult,
    SnapshotInfo,
    scan_scope_zero_diff_hint,
)
from config_scanner.xml_diff import ContentChange, is_encrypted_origin_config_path, resolve_apply_value
from gui.config_scanner_worker import (
    ConfigScannerEmitter,
    SnapshotLoadResult,
    schedule_apply_archived_file,
    schedule_apply_content_change,
    schedule_apply_snapshot,
    schedule_auto_detect,
    schedule_compare,
    schedule_delete_snapshot,
    schedule_load_snapshots,
    schedule_prepare_scan_target,
    schedule_validate_scan_target,
    schedule_scan,
    schedule_set_baseline,
    schedule_startup_auto_detect,
)


logger = get_logger(__name__)


def _snapshot_profile_cell(row: SnapshotInfo) -> str:
    """Profile label for the table; infer roulette/slot for legacy build-info."""
    if (row.profile_label or "").strip():
        return display_profile_label(row.profile_label, row.game_drive)
    base: str | None = None
    if row.profile_id == "roulette_usb":
        base = "Roulette"
    elif row.profile_id == "slot_lab_90":
        base = "Slot"
    else:
        branch = (row.branch or "").casefold()
        drive = (row.game_drive or "").casefold()
        if "ruleta" in branch or "ruleta" in drive or (row.build_number and row.source_version):
            base = "Roulette"
        elif "slot" in drive or (row.exe_product_name or "").casefold() == "gamestar":
            base = "Slot"
    if not base:
        return "—"
    return display_profile_label(base, row.game_drive)


class SnapshotTableModel(QAbstractTableModel):
    # Lean list: name encodes date/build; details stay in the inspector pane.
    HEADERS = [
        "Snapshot",
        "Profile",
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
                return _snapshot_profile_cell(row)
            if index.column() == 2:
                return str(row.file_count)
            if index.column() == 3:
                return "Yes" if row.is_baseline else ""
        if role == Qt.ItemDataRole.UserRole:
            return row.name
        return None

    def snapshot_name_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].name
        return None


def _tight_label_field_row(label_text: str, field: QWidget, *, field_min_width: int = 360) -> QHBoxLayout:
    """Label immediately followed by field — no expanding gap between them."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    label = QLabel(label_text)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    field.setMinimumWidth(field_min_width)
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    row.addWidget(label)
    row.addWidget(field, stretch=1)
    return row


def _configure_snapshot_combo(combo: QComboBox) -> None:
    """Show full snapshot folder names (date_build_time_ms) without clipping."""
    combo.setEditable(False)
    combo.setMinimumWidth(380)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    # Typical name: 2026-07-21_build40097_142016_538 (~35 chars)
    combo.setMinimumContentsLength(40)


class ConfigScannerTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        logger.info("ConfigScannerTabWidget.__init__ start")
        self.setFrameShape(QFrame.Shape.NoFrame)

        try:
            self._service = ConfigScannerService()
        except Exception:
            logger.exception("ConfigScannerService() failed")
            raise
        self._pool = QThreadPool.globalInstance()
        self._emitter = ConfigScannerEmitter(self)
        self._emitter.progress.connect(self._on_progress)
        self._emitter.snapshots_loaded.connect(self._on_snapshots_loaded)
        self._emitter.snapshots_load_failed.connect(self._on_snapshots_load_failed)
        self._emitter.auto_detect_finished.connect(self._on_auto_detect_finished)
        self._emitter.scan_target_ready.connect(self._on_scan_target_ready)
        self._emitter.scan_target_failed.connect(self._on_scan_target_failed)
        self._emitter.scan_finished.connect(self._on_scan_finished)
        self._emitter.compare_finished.connect(self._on_compare_finished)
        self._emitter.baseline_finished.connect(self._on_baseline_finished)
        self._emitter.delete_finished.connect(self._on_delete_finished)
        self._emitter.apply_finished.connect(self._on_apply_finished)
        self._emitter.apply_change_finished.connect(self._on_apply_change_finished)
        self._emitter.apply_file_finished.connect(self._on_apply_file_finished)
        self._emitter.target_validated.connect(self._on_target_validated)

        self._snapshots: list[SnapshotInfo] = []
        self._last_report_path: Path | None = None
        self._last_compare_target_snapshot: str | None = None
        self._last_compare_result: CompareResult | None = None
        self._last_scan_snapshot_name: str | None = None
        self._apply_buttons: list[tuple[QPushButton, bool]] = []
        self._busy = False
        self._busy_op: str | None = None
        self._target_valid = False
        self._validate_seq = 0
        self._validate_pending = ""
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.timeout.connect(self._run_target_validation)
        self._last_status_message: str | None = None
        self._startup_detect_done = False
        self._initial_snapshot_load_done = False
        self._auto_detect_only = False
        # Only a baseline set via More → Set baseline in this app session
        # becomes the default compare left side; otherwise last two scans.
        self._session_baseline_name: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        intro = QLabel(
            "<b>Config SHA1 Scanner</b> — snapshot QA config from a <b>local</b> slot/roulette "
            "image or a <b>remote lab cabinet</b> "
            "(e.g. <code>\\\\10.0.0.90\\c$\\Goldclub</code>). "
            "Requires real binaries (<code>OneHand.exe</code> / <code>Ruleta.exe</code>); "
            "empty <code>C:\\Goldclub</code> is ignored. "
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
        self._drive_edit.setPlaceholderText("Auto: local drive or \\\\10.0.0.90\\c$\\Goldclub")
        self._drive_edit.textChanged.connect(self._on_drive_text_changed)
        self._drive_edit.editingFinished.connect(self._on_drive_editing_finished)
        drive_row.addWidget(self._drive_edit, stretch=1)
        self._detect_btn = QPushButton("Auto-detect repo")
        self._detect_btn.setToolTip(
            "Find local image or remote .90 Goldclub when EXEs exist (OneHand.exe / Ruleta.exe)."
        )
        self._detect_btn.clicked.connect(self._on_detect_drive)
        drive_row.addWidget(self._detect_btn)
        self._scan_btn = QPushButton("Scan now")
        self._scan_btn.setToolTip("Scan config files at the target into a new snapshot.")
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._scan_btn.setEnabled(False)
        drive_row.addWidget(self._scan_btn)
        self._refresh_btn = QPushButton("Refresh list")
        self._refresh_btn.clicked.connect(self.refresh_snapshots)
        drive_row.addWidget(self._refresh_btn)
        drive_row.addStretch(1)
        root.addLayout(drive_row)

        self._scan_progress = QProgressBar()
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setFixedHeight(6)
        self._scan_progress.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        # Always keep the bar in layout (no show/hide jump); idle = empty track.
        self._set_scan_progress_active(False)
        root.addWidget(self._scan_progress)

        self._snapshot_model = SnapshotTableModel(self)
        self._snapshot_table = QTableView()
        self._snapshot_table.setModel(self._snapshot_model)
        self._snapshot_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._snapshot_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._snapshot_table.verticalHeader().setVisible(False)
        header = self._snapshot_table.horizontalHeader()
        header.setMinimumSectionSize(64)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        # All Interactive so the Snapshot|Profile grip stays usable; Profile absorbs leftover width.
        for col in range(self._snapshot_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 280)  # Snapshot
        header.resizeSection(1, 200)  # Profile
        header.resizeSection(2, 64)   # Files
        header.resizeSection(3, 72)   # Baseline
        self._snapshot_table.setAlternatingRowColors(True)
        self._snapshot_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._snapshot_table.customContextMenuRequested.connect(self._on_snapshot_context_menu)
        self._snapshot_table.installEventFilter(self)
        root.addWidget(self._snapshot_table, stretch=2)
        QTimer.singleShot(0, self._apply_snapshot_table_column_widths)

        compare_row = QHBoxLayout()
        compare_row.setSpacing(16)
        self._baseline_combo = QComboBox()
        _configure_snapshot_combo(self._baseline_combo)
        compare_row.addLayout(
            _tight_label_field_row("Baseline:", self._baseline_combo, field_min_width=380),
            stretch=1,
        )
        self._target_combo = QComboBox()
        _configure_snapshot_combo(self._target_combo)
        compare_row.addLayout(
            _tight_label_field_row("Target:", self._target_combo, field_min_width=380),
            stretch=1,
        )
        root.addLayout(compare_row)

        btn_row = QHBoxLayout()
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setToolTip("Compare the Baseline and Target snapshots selected above.")
        self._compare_btn.clicked.connect(self._on_compare_clicked)
        btn_row.addWidget(self._compare_btn)
        self._compare_latest_btn = QPushButton("Quick compare")
        self._compare_latest_btn.setToolTip(
            "Diff the last two scans. If you Set baseline in this session, "
            "compares that baseline to the newest other scan."
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
        self._delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self._delete_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_action.setToolTip(
            "Delete the selected snapshot from the table (or Target combo). "
            "Del key works when the snapshot table is focused. "
            "Right-click a row for the same action."
        )
        self._delete_action.triggered.connect(self._on_delete_clicked)
        more_menu.addAction(self._delete_action)
        # So Del works while the snapshot table (or its viewport) has focus.
        self._snapshot_table.addAction(self._delete_action)
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
        self._changes_find_edit = QLineEdit()
        self._changes_find_edit.setPlaceholderText("Find… (swi → switches)")
        self._changes_find_edit.setToolTip(
            "Filter changed files and settings (case-insensitive). "
            "Characters can be skipped: swi matches switches.xml and SwitchName."
        )
        self._changes_find_edit.setClearButtonEnabled(True)
        self._changes_find_edit.setMinimumWidth(180)
        self._changes_find_edit.setMaximumWidth(280)
        self._changes_find_edit.textChanged.connect(self._on_changes_find_changed)
        btn_row.addWidget(self._changes_find_edit)
        self._write_baseline_btn = QPushButton("Write baseline to machine")
        self._write_baseline_btn.setToolTip(
            "Overwrite live config on the scan target with all files from the baseline snapshot."
        )
        self._write_baseline_btn.clicked.connect(self._on_write_baseline_from_compare)
        self._write_baseline_btn.setVisible(False)
        self._write_baseline_btn.setEnabled(False)
        btn_row.addWidget(self._write_baseline_btn)
        root.addLayout(btn_row)

        self._changes_scroll = QScrollArea()
        self._changes_scroll.setWidgetResizable(True)
        self._changes_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._changes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes_content = QWidget()
        self._changes_layout = QVBoxLayout(self._changes_content)
        self._changes_layout.setContentsMargins(8, 8, 8, 8)
        self._changes_layout.setSpacing(6)
        self._changes_scroll.setWidget(self._changes_content)
        root.addWidget(self._changes_scroll, stretch=0)
        self._changes_scroll_stretch = 0
        self._log_stretch = 1

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Status, progress, and errors…")
        self._log.setMaximumBlockCount(2000)
        root.addWidget(self._log, stretch=self._log_stretch)

        attach_open_with_npp_menu(
            self._log,
            path_provider=lambda: self._last_report_path,
            parent=self,
            label="Open report with Notepad++",
        )

        self._rebuild_changes_panel(None)
        self._validate_pending = self._drive_edit.text().strip()
        QTimer.singleShot(0, self._run_target_validation)
        logger.info("ConfigScannerTabWidget.__init__ done snapshots_dir=%s", self._service.get_snapshots_dir())

    def _clear_layout(self, layout) -> None:  # noqa: ANN001
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                self._clear_layout(child_layout)

    def _replace_changes_content(self) -> None:
        """Drop the previous compare panel widget tree so a new result always paints fresh."""
        self._apply_buttons.clear()
        # takeWidget() transfers ownership to us. setWidget() would delete the old
        # child immediately — never touch that pointer afterward (RuntimeError).
        old = self._changes_scroll.takeWidget()
        self._changes_content = QWidget()
        self._changes_layout = QVBoxLayout(self._changes_content)
        self._changes_layout.setContentsMargins(8, 8, 8, 8)
        self._changes_layout.setSpacing(6)
        self._changes_scroll.setWidget(self._changes_content)
        if old is not None:
            old.deleteLater()

    def _show_compare_pending_ui(self, baseline: str, target: str) -> None:
        """Clear stale compare results as soon as Compare / Quick compare is pressed."""
        self._last_compare_result = None
        self._last_compare_target_snapshot = None
        self._last_report_path = None
        self._open_report_action.setEnabled(False)
        self._write_baseline_btn.setVisible(False)
        self._write_baseline_btn.setEnabled(False)
        self._append_status(f"Comparing {baseline} → {target} …", force=True)
        self._replace_changes_content()
        self._add_changes_panel_note(f"Comparing {baseline} → {target} …")
        self._update_changes_panel_sizing(None, placeholder=True)
        self._changes_scroll.verticalScrollBar().setValue(0)

    def _format_diff_cell_value(self, value: str | None) -> str:
        return format_diff_cell_value(value)

    def _add_compare_column_headers(self, grid: QGridLayout, row: int) -> int:
        muted = "color: #858585; font-size: 11px; font-weight: 600;"
        for col, title in enumerate(("Setting (change)", "Baseline", "Target")):
            label = QLabel(title)
            label.setStyleSheet(muted)
            grid.addWidget(label, row, col)
        return row + 1

    def _make_apply_side_cell(
        self,
        *,
        value: str | None,
        side: str,
        relative_path: str,
        change: ContentChange,
        snapshot_name: str,
        other_value: str | None,
        encrypted_origin: bool = False,
    ) -> QWidget:
        styles = compare_panel_encrypted_origin_styles()
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        display = self._format_diff_cell_value(value)
        value_lbl = QLabel(display)
        tip_lines = [
            f"{side.title()} snapshot: {snapshot_name}",
            f"Value: {format_apply_value_detail(value)}",
        ]
        if encrypted_origin:
            tip_lines.append(
                "Source file is encrypted on disk (gcxml ruleta setup.xml); "
                "scan/compare use decrypted plain settings."
            )
        if value is not None:
            tip_lines.append(
                f"Write copies this onto the live scan target "
                f"(other side: {format_apply_value_detail(other_value)})."
            )
        else:
            tip_lines.append(
                "No Write button — this snapshot does not have this setting."
            )
        value_lbl.setToolTip("\n".join(tip_lines))
        value_lbl.setWordWrap(True)
        # Keep value + Write packed; do not stretch a gap between them on wide windows.
        value_lbl.setMaximumWidth(220)
        value_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        muted = value is None or not (value or "").strip()
        if encrypted_origin:
            value_lbl.setStyleSheet(
                styles["encrypted_value_muted"] if muted else styles["encrypted_value"]
            )
        elif muted:
            value_lbl.setStyleSheet(styles["plain_value_muted"])
        else:
            value_lbl.setStyleSheet(styles["plain_value"])
        layout.addWidget(value_lbl, stretch=0)

        if value is not None and is_actionable_content_change(change):
            apply_btn = QPushButton(apply_button_label(value))
            # Size to full caption ("Write" / "Write blank") — old 52px clipped to "Writ".
            apply_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            apply_btn.setFixedWidth(max(apply_btn.sizeHint().width() + 8, 68))
            apply_btn.setToolTip(
                "Write to live machine:\n"
                f"{format_apply_value_detail(value)}\n\n"
                f"Source: {side} snapshot {snapshot_name}"
            )
            apply_btn.setEnabled(not self._busy)
            apply_btn.clicked.connect(
                lambda checked=False, rp=relative_path, ch=change, s=side: self._confirm_apply_change(
                    rp, ch, s
                )
            )
            layout.addWidget(apply_btn, stretch=0)
            self._apply_buttons.append((apply_btn, True))
        elif value is not None:
            tip_lines.append("Write disabled — encrypted/opaque token (not a writable setting).")
            value_lbl.setToolTip("\n".join(tip_lines))
        layout.addStretch(1)
        return cell

    def _add_changes_panel_note(self, text: str) -> None:
        note = QLabel(text)
        note.setStyleSheet("color: #858585;")
        note.setWordWrap(True)
        self._changes_layout.addWidget(note)

    def _update_changes_panel_sizing(
        self,
        result: CompareResult | None,
        *,
        placeholder: bool = False,
        file_diffs_override: list | None = None,
    ) -> None:
        """Size the changes scroll area from visible row count (post-truncation)."""
        scroll = self._changes_scroll
        root = self.layout()
        if root is None:
            return

        if placeholder or result is None:
            target_height = 88
            scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            scroll.setMinimumHeight(target_height)
            scroll.setMaximumHeight(target_height)
            changes_stretch = 0
            log_stretch = 1
        else:
            diffs = file_diffs_override if file_diffs_override is not None else result.file_diffs
            metrics = compare_panel_layout_metrics(diffs)
            estimated = estimate_compare_panel_height(metrics)
            content_hint = self._changes_content.sizeHint().height()
            if content_hint > 0:
                estimated = max(estimated, content_hint + 12)

            if metrics["empty"]:
                target_height = 88
                scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
                scroll.setMinimumHeight(target_height)
                scroll.setMaximumHeight(target_height)
                changes_stretch = 0
                log_stretch = 1
            elif metrics["apply_rows"] <= 1 and metrics["file_headers"] <= 1:
                target_height = max(120, min(140, estimated))
                scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
                scroll.setMinimumHeight(target_height)
                scroll.setMaximumHeight(target_height)
                changes_stretch = 0
                log_stretch = 1
            elif estimated <= 320:
                target_height = max(180, min(320, estimated))
                scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
                scroll.setMinimumHeight(min(160, target_height))
                scroll.setMaximumHeight(target_height)
                changes_stretch = 1
                log_stretch = 2
            else:
                scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
                scroll.setMinimumHeight(max(400, min(500, estimated)))
                scroll.setMaximumHeight(16777215)
                changes_stretch = 3
                log_stretch = 1

        scroll_idx = root.indexOf(scroll)
        log_idx = root.indexOf(self._log) if hasattr(self, "_log") else -1
        if scroll_idx >= 0:
            root.setStretch(scroll_idx, changes_stretch)
            self._changes_scroll_stretch = changes_stretch
        if log_idx >= 0:
            root.setStretch(log_idx, log_stretch)
            self._log_stretch = log_stretch

    def _on_changes_find_changed(self, _text: str) -> None:
        if self._last_compare_result is not None:
            self._rebuild_changes_panel(self._last_compare_result)

    def _file_diffs_for_changes_panel(self, result: CompareResult) -> list:
        needle = self._changes_find_edit.text().strip()
        if needle:
            return filter_file_diffs_for_find(result.file_diffs, needle)
        return result.file_diffs

    def _rebuild_changes_panel(self, result: CompareResult | None) -> None:
        # Replace the whole content widget so previous compare rows cannot linger
        # (takeAt + deleteLater alone can leave stale UI until a later event loop tick).
        self._replace_changes_content()

        if result is None:
            self._add_changes_panel_note(
                "Run Compare to see changed settings. "
                "Write copies that cell's value onto the live scan target. "
                'not present = missing in that snapshot; "" (blank) = present but empty.'
            )
            self._update_changes_panel_sizing(None, placeholder=True)
            self._changes_scroll.verticalScrollBar().setValue(0)
            return

        needle = self._changes_find_edit.text().strip()
        panel_diffs = self._file_diffs_for_changes_panel(result)
        changed, omitted_files = compare_panel_file_slice(panel_diffs)
        if not changed and omitted_files == 0:
            if needle:
                self._add_changes_panel_note(f'(no matches for "{needle}")')
            else:
                self._add_changes_panel_note("(no changes)")
            self._update_changes_panel_sizing(result, file_diffs_override=panel_diffs)
            self._changes_scroll.verticalScrollBar().setValue(0)
            return

        baseline_name = result.baseline_snapshot
        target_name = result.target_snapshot

        if needle:
            self._add_changes_panel_note(
                f'Find "{needle}": showing {len(changed)} matching file(s)'
                + (f"; {omitted_files} more omitted" if omitted_files else "")
            )
        elif omitted_files:
            self._add_changes_panel_note(
                f"Showing the first {len(changed)} changed file(s); "
                f"{omitted_files} more omitted. Open the HTML report for the full diff."
            )

        styles = compare_panel_encrypted_origin_styles()
        if any(is_encrypted_origin_config_path(fd.relative_path) for fd in changed):
            legend = QLabel(
                "Color: amber = setting from encrypted-on-disk file (ruleta setup.xml); "
                "blue = plain config file."
            )
            legend.setStyleSheet(styles["legend"])
            legend.setWordWrap(True)
            self._changes_layout.addWidget(legend)

        for file_diff in changed:
            encrypted_origin = is_encrypted_origin_config_path(file_diff.relative_path)
            header = QLabel(file_diff_header_label(file_diff.relative_path, file_diff.status))
            header.setStyleSheet(
                styles["encrypted_header"] if encrypted_origin else styles["plain_header"]
            )
            header.setWordWrap(True)
            if encrypted_origin:
                header.setToolTip(
                    "Live file is gcxml-encrypted ruleta setup.xml. "
                    "Values shown are from the decrypted plain settings used for scan/compare."
                )
            self._changes_layout.addWidget(header)

            if not file_diff.content_diff:
                if file_diff.status in {"removed", "added"}:
                    self._add_missing_file_write_row(file_diff, baseline_name, target_name)
                else:
                    self._add_changes_panel_note(
                        "  File hash differs but no archived content is available for field-level apply"
                    )
                continue

            actionable, opaque = content_change_panel_partition(file_diff.content_diff)
            visible_changes = actionable[:COMPARE_PANEL_MAX_CHANGES_PER_FILE]
            omitted_actionable = max(0, len(actionable) - len(visible_changes))

            if not visible_changes and opaque:
                self._add_changes_panel_note(
                    f"  Encrypted config churn: {len(opaque)} token change(s) "
                    f"(gcxml re-encryption — not writable as settings; open report for full diff)"
                )
                continue

            grid_host = QWidget()
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(4)
            # Setting grows; Baseline/Target stay compact (value + Write packed).
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)
            grid.setColumnStretch(2, 0)
            grid.setColumnMinimumWidth(0, 140)
            grid.setColumnMinimumWidth(1, 120)
            grid.setColumnMinimumWidth(2, 120)
            grid_row = self._add_compare_column_headers(grid, 0)

            for change in visible_changes:
                setting_name = setting_display_name(change.path)
                change_line = format_setting_change_description(change).strip()
                setting_lbl = QLabel(f"{setting_name}\n{change_line}")
                setting_lbl.setWordWrap(True)
                setting_lbl.setStyleSheet(
                    styles["encrypted_setting"]
                    if encrypted_origin
                    else styles["plain_setting"]
                )
                tip = f"XML path:\n{change.path}\n\n{change_line}"
                if encrypted_origin:
                    tip += "\n\nFrom encrypted-on-disk ruleta setup.xml (decrypted for compare)."
                setting_lbl.setToolTip(tip)
                grid.addWidget(setting_lbl, grid_row, 0)

                baseline_value = resolve_apply_value(change, "baseline")
                target_value = resolve_apply_value(change, "target")
                grid.addWidget(
                    self._make_apply_side_cell(
                        value=baseline_value,
                        side="baseline",
                        relative_path=file_diff.relative_path,
                        change=change,
                        snapshot_name=baseline_name,
                        other_value=target_value,
                        encrypted_origin=encrypted_origin,
                    ),
                    grid_row,
                    1,
                )
                grid.addWidget(
                    self._make_apply_side_cell(
                        value=target_value,
                        side="target",
                        relative_path=file_diff.relative_path,
                        change=change,
                        snapshot_name=target_name,
                        other_value=baseline_value,
                        encrypted_origin=encrypted_origin,
                    ),
                    grid_row,
                    2,
                )
                grid_row += 1

            self._changes_layout.addWidget(grid_host)

            if omitted_actionable > 0:
                self._add_changes_panel_note(
                    f"  … {omitted_actionable} more writable change(s) in this file; "
                    f"open report for full diff"
                )
            if opaque:
                self._add_changes_panel_note(
                    f"  … {len(opaque)} encrypted/opaque token change(s) omitted "
                    f"(not writable; open report)"
                )

        self._update_changes_panel_sizing(result, file_diffs_override=panel_diffs)
        self._changes_scroll.verticalScrollBar().setValue(0)
        self._changes_content.update()
        self._changes_scroll.update()

    def _apply_compare_result_ui(self, result: CompareResult) -> None:
        self._last_report_path = result.report_path
        self._last_compare_target_snapshot = result.target_snapshot
        self._last_compare_result = result
        self._open_report_action.setEnabled(True)
        has_archive = self._snapshot_has_archive(result.baseline_snapshot)
        self._write_baseline_btn.setVisible(True)
        self._write_baseline_btn.setEnabled(has_archive and not self._busy)
        if not has_archive:
            self._write_baseline_btn.setToolTip(
                "Baseline snapshot has no archived files — re-scan to capture content first."
            )
        else:
            self._write_baseline_btn.setToolTip(
                f"Overwrite live config on the scan target with all files from {result.baseline_snapshot}."
            )
        # Always rebuild (including identical / zero-diff) so the previous compare cannot stick.
        self._rebuild_changes_panel(result)
        self._log_compare_result(result)

    def _on_write_baseline_from_compare(self) -> None:
        result = self._last_compare_result
        if result is None:
            return
        self._confirm_write_snapshot(result.baseline_snapshot)

    def _confirm_apply_change(
        self,
        relative_path: str,
        change: ContentChange,
        side: str,
    ) -> None:
        if self._busy:
            return
        if not is_actionable_content_change(change):
            QMessageBox.information(
                self,
                "Config Scanner",
                "This change is encrypted/opaque config churn and cannot be written "
                "as a named setting.",
            )
            return
        value = resolve_apply_value(change, side)
        if value is None:
            return

        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter a scan target path before applying a change to the machine.",
            )
            return

        setting = setting_display_name(change.path)
        other_side = "target" if side == "baseline" else "baseline"
        other_value = resolve_apply_value(change, other_side)
        side_label = "Baseline" if side == "baseline" else "Target"
        other_label = "Target" if side == "baseline" else "Baseline"
        result = self._last_compare_result
        source_snap = ""
        if result is not None:
            source_snap = (
                result.baseline_snapshot if side == "baseline" else result.target_snapshot
            )

        prompt = (
            f"Write this value onto the LIVE machine?\n\n"
            f"Machine (scan target):\n  {scan_target}\n\n"
            f"File:\n  {relative_path}\n\n"
            f"Setting:\n  {setting}\n"
            f"Path:\n  {change.path}\n\n"
            f"Will WRITE (from {side_label} snapshot"
            + (f" {source_snap}" if source_snap else "")
            + "):\n"
            f"  {format_apply_value_detail(value)}\n\n"
            f"{other_label} snapshot has:\n"
            f"  {format_apply_value_detail(other_value)}\n\n"
            "Only this one field is overwritten on the live config file."
        )

        reply = QMessageBox.warning(
            self,
            "Config Scanner — confirm Write",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(True)
        schedule_apply_content_change(
            self._pool,
            self._service,
            scan_target,
            relative_path,
            change,
            side,
            self._emitter,
        )

    def _on_apply_change_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok:
            return
        QMessageBox.critical(self, "Config Scanner", f"Apply change failed:\n\n{message}")

    def _add_missing_file_write_row(
        self,
        file_diff,
        baseline_name: str,
        target_name: str,
    ) -> None:
        """Whole-file Write when a file is only on baseline or only on target."""
        styles = compare_panel_encrypted_origin_styles()
        encrypted_origin = is_encrypted_origin_config_path(file_diff.relative_path)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(8)

        if file_diff.status == "removed":
            note = (
                f"Missing on target — Write file from baseline snapshot ({baseline_name})"
            )
            source_side = "baseline"
            source_snap = baseline_name
        else:
            note = (
                f"Missing on baseline — Write file from target snapshot ({target_name})"
            )
            source_side = "target"
            source_snap = target_name

        note_lbl = QLabel(note)
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(
            styles["encrypted_setting"] if encrypted_origin else "color: #858585;"
        )
        if encrypted_origin:
            note_lbl.setToolTip(
                "Live destination will receive this file; ruleta setup.xml is "
                "re-encrypted on write-back when archived content is plain."
            )
        row.addWidget(note_lbl, stretch=1)

        btn = QPushButton(
            f"Write file (from {source_side})"
        )
        btn.setEnabled(not self._busy)
        btn.setToolTip(
            f"Copy archived {file_diff.relative_path} from {source_snap} onto the live scan target."
        )
        btn.clicked.connect(
            lambda checked=False, rp=file_diff.relative_path, sn=source_snap: (
                self._confirm_write_archived_file(rp, sn)
            )
        )
        row.addWidget(btn)
        self._apply_buttons.append((btn, True))
        self._changes_layout.addWidget(host)

    def _confirm_write_archived_file(self, relative_path: str, snapshot_name: str) -> None:
        if self._busy:
            return
        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter a scan target path before writing a file to the machine.",
            )
            return
        origin = (
            "encrypted on disk (gcxml)"
            if is_encrypted_origin_config_path(relative_path)
            else "plain file"
        )
        prompt = (
            f"Write this whole file onto the LIVE machine?\n\n"
            f"Machine (scan target):\n  {scan_target}\n\n"
            f"File:\n  {relative_path}\n"
            f"Origin type: {origin}\n\n"
            f"Source snapshot:\n  {snapshot_name}\n\n"
            "Creates or overwrites that path on the live target."
        )
        reply = QMessageBox.warning(
            self,
            "Config Scanner — confirm Write file",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        schedule_apply_archived_file(
            self._pool,
            self._service,
            scan_target,
            snapshot_name,
            relative_path,
            self._emitter,
        )

    def _on_apply_file_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok:
            return
        QMessageBox.critical(self, "Config Scanner", f"Write file failed:\n\n{message}")

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
        cleaned = result.target.rstrip("\\")
        self._drive_edit.blockSignals(True)
        self._drive_edit.setText(cleaned)
        self._drive_edit.blockSignals(False)
        self._persist_drive()
        self._target_valid = True
        self._validate_pending = cleaned
        self._update_scan_status_ui()
        self._refresh_action_enabled()

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

        profile_line = _snapshot_profile_cell(snapshot)
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
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok:
            return
        QMessageBox.critical(self, "Config Scanner", f"Write snapshot failed:\n\n{message}")

    def _on_delete_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok:
            if isinstance(result, str) and self._session_baseline_name == result:
                self._session_baseline_name = None
            if isinstance(result, str) and self._service.get_baseline_name() is None:
                self._write_baseline_btn.setVisible(False)
                self._write_baseline_btn.setEnabled(False)
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

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        if obj is self._snapshot_table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._on_delete_clicked()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._ensure_initial_snapshot_load()
        if not self._startup_detect_done:
            self._startup_detect_done = True
            QTimer.singleShot(0, self._schedule_startup_auto_detect)

    def _schedule_startup_auto_detect(self) -> None:
        saved_target = SettingsManager.get_config_scanner_game_drive().strip()
        schedule_startup_auto_detect(self._pool, self._service, saved_target, self._emitter)

    def _run_auto_detect(self, *, silent: bool) -> None:
        if self._busy:
            return
        self._auto_detect_only = True
        hint = self._drive_edit.text().strip() or None
        self._set_busy(True, op="detect")
        schedule_auto_detect(self._pool, self._service, hint, self._emitter, silent=silent)

    def _on_auto_detect_finished(
        self,
        ok: bool,
        result: object,
        message: str,
        silent: bool,
    ) -> None:
        if self._auto_detect_only:
            self._set_busy(False)
            self._auto_detect_only = False
        if ok and result is not None:
            self._apply_detect_result(result)
            from config_scanner.build_version import DiscoverResult

            if isinstance(result, DiscoverResult):
                self._append_status(
                    f"Auto-detected {display_profile_label(result.profile_label, result.target)} at {result.target.rstrip(chr(92))}",
                    force=True,
                )
            return
        if not ok:
            if silent:
                self._append_status(f"Auto-detect: {message}", force=True)
            else:
                QMessageBox.warning(self, "Config Scanner", message)

    def _on_scan_target_ready(self, target: str) -> None:
        cleaned = target.rstrip("\\")
        self._drive_edit.blockSignals(True)
        self._drive_edit.setText(cleaned)
        self._drive_edit.blockSignals(False)
        self._persist_drive()
        self._target_valid = True
        self._validate_pending = cleaned
        if self._busy_op != "scan":
            self._set_busy(True, op="scan")
        else:
            self._update_scan_status_ui()
        schedule_scan(self._pool, self._service, target, self._emitter)

    def _on_scan_target_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(
            self,
            "Config Scanner",
            "Could not find a slot or roulette game folder.\n\n"
            f"{message}\n\n"
            "Set Scan target to the game root (e.g. \\\\10.0.0.90\\c$\\Goldclub or D:) "
            "and try again, or click Auto-detect.",
        )


    def _apply_snapshot_table_column_widths(self) -> None:
        """Keep Snapshot|Profile resize grips; give leftover width to Profile."""
        table = self._snapshot_table
        header = table.horizontalHeader()
        if header is None or header.count() < 2:
            return
        profile_col = 1
        fixed = sum(
            header.sectionSize(i)
            for i in range(header.count())
            if i != profile_col and not header.isSectionHidden(i)
        )
        available = max(table.viewport().width() - 4, 0)
        header.resizeSection(profile_col, max(header.minimumSectionSize(), available - fixed))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_snapshot_table_column_widths()

    def refresh_snapshots(self) -> None:
        schedule_load_snapshots(self._pool, self._service, self._emitter)

    def _persist_drive(self) -> None:
        SettingsManager.set_config_scanner_game_drive(self._drive_edit.text().strip())

    def _set_scan_progress_active(self, active: bool) -> None:
        """Toggle indeterminate animation without changing layout geometry."""
        if active:
            self._scan_progress.setRange(0, 0)
        else:
            self._scan_progress.setRange(0, 1)
            self._scan_progress.setValue(0)

    def _set_busy(self, busy: bool, op: str | None = None) -> None:
        self._busy = busy
        self._busy_op = op if busy else None
        if busy and op == "scan":
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Scanning…")
        elif busy:
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Scan now")
        else:
            self._set_scan_progress_active(False)
            self._scan_btn.setText("Scan now")
        self._update_scan_status_ui()
        self._refresh_action_enabled()

    def _refresh_action_enabled(self) -> None:
        busy = self._busy
        can_scan = (not busy) and self._target_valid
        self._scan_btn.setEnabled(can_scan)
        if not self._target_valid and not busy:
            self._scan_btn.setToolTip(
                "Scan now is disabled until a valid game root is set "
                "(local image or \\10.0.0.90\\c$\\Goldclub)."
            )
        elif busy and self._busy_op == "scan":
            self._scan_btn.setToolTip("Scan in progress…")
        else:
            self._scan_btn.setToolTip("Scan config files at the target into a new snapshot.")
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
        for button, has_value in self._apply_buttons:
            button.setEnabled(not busy and has_value)
        if hasattr(self, "_write_baseline_btn"):
            result = self._last_compare_result
            has_archive = (
                result is not None
                and self._snapshot_has_archive(result.baseline_snapshot)
            )
            self._write_baseline_btn.setEnabled(not busy and has_archive)

    def _append_status(self, message: str, *, force: bool = False) -> None:
        """Single status surface: append to the bottom log (dedupe idle repeats)."""
        text = (message or "").strip()
        if not text:
            return
        if not force and text == self._last_status_message:
            return
        self._last_status_message = text
        self._log.appendPlainText(text)
        bar = self._log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _update_scan_status_ui(self) -> None:
        if self._busy and self._busy_op == "scan":
            # Scan start / progress callbacks already write the log; progress bar shows activity.
            return
        if self._busy and self._busy_op == "detect":
            self._append_status("Looking for a game repo…")
            return
        if self._busy and self._busy_op == "compare":
            return
        if self._busy:
            self._append_status("Working…")
            return
        if self._target_valid:
            target = self._drive_edit.text().strip()
            ready = f"Ready — valid target: {target}"
            if self._last_scan_snapshot_name:
                ready = f"{ready} | Last scan: {self._last_scan_snapshot_name}"
            self._append_status(ready)
        elif self._drive_edit.text().strip():
            self._last_scan_snapshot_name = None
            self._append_status(
                "No valid game root at this path — Auto-detect or fix Scan target."
            )
        else:
            self._last_scan_snapshot_name = None
            self._append_status(
                "Enter a scan target or click Auto-detect (Scan now disabled)."
            )

    def _on_drive_text_changed(self, _text: str = "") -> None:
        text = self._drive_edit.text().strip()
        if not text:
            self._target_valid = False
            self._validate_timer.stop()
            self._update_scan_status_ui()
            self._refresh_action_enabled()
            return
        if self._target_valid:
            self._target_valid = False
            self._refresh_action_enabled()
        self._append_status("Checking scan target…")
        self._validate_timer.start(400)

    def _on_drive_editing_finished(self) -> None:
        self._persist_drive()
        self._validate_timer.stop()
        self._run_target_validation()

    def _run_target_validation(self) -> None:
        text = self._drive_edit.text().strip()
        self._validate_seq += 1
        self._validate_pending = text
        if not text:
            self._on_target_validated("", False)
            return
        schedule_validate_scan_target(self._pool, self._service, text, self._emitter)

    def _on_target_validated(self, path: str, valid: bool) -> None:
        current = self._drive_edit.text().strip()
        if path != current:
            return
        self._target_valid = bool(valid and current)
        self._update_scan_status_ui()
        self._refresh_action_enabled()

    def _on_progress(self, message: str) -> None:
        self._append_status(message, force=True)

    def _default_compare_pair(self) -> tuple[str, str] | None:
        """Last two scans, or session baseline vs newest other scan."""
        names = [row.name for row in self._snapshots]
        if len(names) < 2:
            return None
        session_bl = self._session_baseline_name
        if session_bl and session_bl in names:
            target = next(
                (name for name in reversed(names) if name != session_bl),
                None,
            )
            if target:
                return session_bl, target
        return names[-2], names[-1]

    def _populate_combos(self, *, preserve_selection: bool = True) -> None:
        names = [row.name for row in self._snapshots]
        prev_baseline = self._baseline_combo.currentText().strip() if preserve_selection else ""
        prev_target = self._target_combo.currentText().strip() if preserve_selection else ""
        self._baseline_combo.clear()
        self._target_combo.clear()
        self._baseline_combo.addItems(names)
        self._target_combo.addItems(names)
        if not names:
            return

        default_pair = self._default_compare_pair()
        if default_pair is not None:
            default_baseline, default_target = default_pair
        elif len(names) == 1:
            default_baseline, default_target = names[0], names[0]
        else:
            default_baseline, default_target = names[0], names[0]

        # Keep a manual combo choice only when it is not the stale disk-baseline default
        # and still differs from the smart default pair in a useful way.
        use_prev = (
            preserve_selection
            and prev_baseline in names
            and prev_target in names
            and prev_baseline != prev_target
            and default_pair is not None
            and {prev_baseline, prev_target} == {default_baseline, default_target}
        )
        if use_prev:
            self._baseline_combo.setCurrentText(prev_baseline)
            self._target_combo.setCurrentText(prev_target)
        else:
            self._baseline_combo.setCurrentText(default_baseline)
            self._target_combo.setCurrentText(default_target)

    def _compare_pair_or_warn(self) -> tuple[str, str] | None:
        baseline = self._baseline_combo.currentText().strip()
        target = self._target_combo.currentText().strip()
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

    def _log_compare_result(self, result: CompareResult) -> None:
        self._append_status(
            format_compare_summary(
                result.baseline_snapshot,
                result.target_snapshot,
                result.summary,
                result.warnings,
            ),
            force=True,
        )
        changed = [item for item in result.file_diffs if item.status != "unchanged"]
        if not changed:
            profile_id, profile_label = self._snapshot_profile_for(result.target_snapshot)
            if not profile_id and not profile_label:
                profile_id, profile_label = self._snapshot_profile_for(result.baseline_snapshot)
            self._append_status(
                "No differences in scanned config files.\n"
                + scan_scope_zero_diff_hint(profile_id, profile_label),
                force=True,
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
        if self._busy or not self._target_valid:
            return
        self._append_status(
            f"Scan started: {self._drive_edit.text().strip()}",
            force=True,
        )
        self._set_busy(True, op="scan")
        schedule_prepare_scan_target(
            self._pool,
            self._service,
            self._drive_edit.text().strip(),
            self._emitter,
        )

    def _on_scan_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok and isinstance(result, ScanResult):
            self._last_scan_snapshot_name = result.snapshot_name
        self._update_scan_status_ui()
        if ok and isinstance(result, ScanResult):
            self.refresh_snapshots()
            if result.manifest_file_count == 0:
                QMessageBox.warning(
                    self,
                    "Config Scanner",
                    "Scan finished but found 0 config files.\n\n"
                    f"Scan target: {self._drive_edit.text().strip()}\n\n"
                    "Use the game root (e.g. \\\\10.0.0.90\\c$\\Goldclub or D:), "
                    "not the ConfigScanner tools folder. Confirm config\\ exists under that root.",
                )
            else:
                tip = (
                    "Tip: Quick compare uses your session baseline vs the newest scan."
                    if self._session_baseline_name
                    else "Tip: click Quick compare to diff the last two scans."
                )
                self._append_status(tip, force=True)
                for note in result.warnings:
                    self._append_status(f"WARN: {note}", force=True)
                if result.warnings:
                    QMessageBox.warning(
                        self,
                        "Config Scanner",
                        "Scan completed with warnings:\n\n"
                        + "\n".join(f"• {n}" for n in result.warnings[:8]),
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
        self._show_compare_pending_ui(baseline, target)
        self._set_busy(True, op="compare")
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
        pair = self._default_compare_pair()
        if pair is None:
            QMessageBox.warning(self, "Config Scanner", "Need at least two snapshots.")
            return
        baseline, target = pair
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

        self._append_status(f"Setting baseline from {name} …", force=True)
        self._set_busy(True)
        schedule_set_baseline(self._pool, self._service, name, self._emitter)

    def _on_baseline_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok and isinstance(result, str):
            self._session_baseline_name = result
            new_name = result
            snap_dir = self._service.get_snapshots_dir()
            folder = snap_dir / new_name
            self._apply_snapshot_list()
            self._update_data_path_label()
            self._baseline_combo.setCurrentText(new_name)
            self._append_status(f"Baseline: {new_name}", force=True)
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
            self._append_status(
                f"OK: Compare finished ({result.report_path.name}).",
                force=True,
            )
            try:
                self._apply_compare_result_ui(result)
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                self._append_status(
                    f"FAIL: Compare UI update: {exc}\n{detail}",
                    force=True,
                )
                self._last_report_path = result.report_path
                self._last_compare_target_snapshot = result.target_snapshot
                self._last_compare_result = result
                self._open_report_action.setEnabled(True)
                self._append_status(
                    format_compare_summary(
                        result.baseline_snapshot,
                        result.target_snapshot,
                        result.summary,
                        result.warnings,
                    ),
                    force=True,
                )
                self._replace_changes_content()
                self._add_changes_panel_note(
                    f"Compare finished ({result.baseline_snapshot} → {result.target_snapshot}) "
                    "but the details panel failed to render. Open the HTML report from More."
                )
                self._update_changes_panel_sizing(None, placeholder=True)
                QMessageBox.critical(
                    self,
                    "Config Scanner",
                    "Compare finished but the results panel could not be updated.\n\n"
                    f"{exc}\n\n"
                    "Open the HTML report from More → Open report for the full diff.",
                )
        else:
            self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
            # Failed compare must not leave the previous successful compare on screen.
            self._last_compare_result = None
            self._last_compare_target_snapshot = None
            self._write_baseline_btn.setVisible(False)
            self._rebuild_changes_panel(None)
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
