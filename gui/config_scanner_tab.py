"""Config Scanner tab for QA config SHA1 snapshots and diffs."""

from __future__ import annotations

import traceback
from pathlib import Path

from shiboken6 import isValid as _qt_widget_is_valid

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    QSize,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from config_manager import SettingsManager
from gui.app_logging import get_logger
from config_scanner.html_open import open_html_file
from config_scanner.profiles import display_profile_label
from config_scanner.scanner import snapshot_content_root
from gui.notepad_pp import attach_open_with_npp_menu, extend_menu_with_npp_action
from config_scanner.report import (
    COMPARE_PANEL_MAX_CHANGES_PER_FILE,
    apply_button_label,
    compare_panel_encrypted_origin_styles,
    compare_panel_file_slice,
    content_change_panel_partition,
    file_diff_header_label,
    filter_file_diffs_for_find,
    format_apply_value_detail,
    format_compare_panel_header,
    format_compare_panel_summary,
    format_compare_summary,
    format_diff_cell_value,
    format_setting_change_description,
    is_actionable_content_change,
    setting_display_name,
)
from config_scanner.stack_restart import (
    plan_stack_restart,
    should_autostart_after_write,
)
from config_scanner.service import (
    CompareResult,
    ConfigScannerService,
    ScanResult,
    SnapshotInfo,
    order_snapshots_newest_first,
    scan_scope_zero_diff_hint,
)
from config_scanner.software_compat import (
    format_live_ruleta_sw_banner,
    live_ruleta_exe_version_for_target,
)
from config_scanner.write_scope import (
    WRITE_SCOPE_DESCRIPTIONS,
    WRITE_SCOPE_LABELS,
    WRITE_SCOPE_SHORT,
    WriteScope,
    is_protected_write_path,
    protected_write_block_reason,
)
from config_scanner.machine_identity import (
    is_licence_path,
    is_protected_identity_field,
    protected_identity_field_reason,
)
from config_scanner.xml_diff import (
    ContentChange,
    is_encrypted_origin_config_path,
    is_structural_item_change,
    resolve_apply_value,
)
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
    schedule_stack_restart,
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


def snapshot_version_build_parts(version: str) -> tuple[str, str | None]:
    """Split a PE ProductVersion so the fourth part (``.876``) can be drawn bold.

    ``10.2.0.876`` → ``("10.2.0", "876")``. Three-part values like ``10.2.0``
    have no build token — caller paints them unchanged.
    """
    raw = (version or "").strip()
    if not raw or raw == "—":
        return raw or "—", None
    tokens = [part.strip() for part in raw.split(".")]
    if len(tokens) >= 4 and tokens[-1]:
        return ".".join(tokens[:-1]), tokens[-1]
    return raw, None


class SnapshotVersionDelegate(QStyledItemDelegate):
    """Version column: bold only the fourth component when it exists."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if index.column() != SnapshotTableModel.VERSION_COL:
            super().paint(painter, option, index)
            return
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        prefix, build = snapshot_version_build_parts(text)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        if not text:
            return
        painter.save()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        color = opt.palette.color(
            QPalette.ColorGroup.Normal,
            (
                QPalette.ColorRole.HighlightedText
                if selected
                else QPalette.ColorRole.Text
            ),
        )
        painter.setPen(color)
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget
        )
        if not text_rect.isValid():
            text_rect = opt.rect.adjusted(4, 0, -4, 0)
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        if build:
            painter.setFont(opt.font)
            prefix_text = f"{prefix}."
            painter.drawText(text_rect, align, prefix_text)
            advance = QFontMetrics(opt.font).horizontalAdvance(prefix_text)
            bold = QFont(opt.font)
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(text_rect.adjusted(advance, 0, 0, 0), align, build)
        else:
            painter.setFont(opt.font)
            painter.drawText(text_rect, align, text)
        painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        if index.column() != SnapshotTableModel.VERSION_COL:
            return super().sizeHint(option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        prefix, build = snapshot_version_build_parts(text)
        base = super().sizeHint(option, index)
        fm = option.fontMetrics
        if build:
            bold = QFont(option.font)
            bold.setBold(True)
            width = fm.horizontalAdvance(f"{prefix}.") + QFontMetrics(bold).horizontalAdvance(
                build
            )
        else:
            width = fm.horizontalAdvance(text)
        return QSize(width + 12, base.height())


class SnapshotTableModel(QAbstractTableModel):
    # Lean list: name encodes date/build; details stay in the inspector pane.
    VERSION_COL = 2
    HEADERS = [
        "Snapshot",
        "Profile",
        "Version",
        "Kind",
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
            if index.column() == self.VERSION_COL:
                return (row.exe_product_version or row.product_version or "").strip() or "—"
            if index.column() == 3:
                return "Full" if row.has_software else "Config"
            if index.column() == 4:
                return str(row.file_count)
            if index.column() == 5:
                return "Yes" if row.is_baseline else ""
        if role == Qt.ItemDataRole.UserRole:
            return row.name
        return None

    def snapshot_name_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row].name
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:  # noqa: N802
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


SNAPSHOT_TABLE_ROW_PX = 28


def configure_snapshot_table_view(table: QTableView) -> None:
    """Row-select list: fixed height, no editor, clicks always land on a row.

    App-wide ``QTableView::item { padding }`` offsets ``indexAt`` on Fusion, so
    this view uses a named object (theme override) plus a widget sheet. Do not
    call ``selectRow`` from ``pressed`` and from an event filter on the same
    click — that stacks layout work and feels like lag between rows.
    """
    table.setObjectName("configScannerSnapshotTable")
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.setTabKeyNavigation(False)
    table.setMouseTracking(False)
    table.setAutoScroll(False)
    table.setStyleSheet(
        "QTableView#configScannerSnapshotTable::item { padding: 0px; }"
    )
    vheader = table.verticalHeader()
    vheader.setVisible(False)
    vheader.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    vheader.setDefaultSectionSize(SNAPSHOT_TABLE_ROW_PX)
    vheader.setMinimumSectionSize(SNAPSHOT_TABLE_ROW_PX)
    vheader.setMaximumSectionSize(SNAPSHOT_TABLE_ROW_PX)


def resize_snapshot_table_columns_to_contents(table: QTableView) -> None:
    """Size every column to fit header + cell text (horizontal scroll if needed)."""
    model = table.model()
    header = table.horizontalHeader()
    if model is None or header is None:
        return
    table.resizeColumnsToContents()
    padding = 12
    for col in range(model.columnCount()):
        header.resizeSection(
            col,
            max(header.sectionSize(col) + padding, header.minimumSectionSize()),
        )
    header.setStretchLastSection(False)


def snapshot_table_row_from_pos(table: QTableView, pos) -> int:
    """Row under a viewport point. Falls back to fixed-height math if indexAt misses."""
    index = table.indexAt(pos)
    if index.isValid():
        return index.row()
    model = table.model()
    if model is None:
        return -1
    row_h = table.verticalHeader().defaultSectionSize() or SNAPSHOT_TABLE_ROW_PX
    if row_h <= 0:
        return -1
    y = int(pos.y()) + int(table.verticalOffset())
    if y < 0:
        return -1
    row = y // row_h
    if 0 <= row < model.rowCount():
        return row
    return -1


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


# Snapshots drawer: ~45% of the splitter so full folder names stay readable
# (e.g. 2026-08-17_GRT330106_Ruleta_Alegro_Wing_v10.2_b40114_083420).
SNAPSHOT_DRAWER_OPEN_RATIO = 0.45
SNAPSHOT_DRAWER_MIN_OPEN_PX = 720
SNAPSHOT_DRAWER_COMPARE_MIN_PX = 280


def snapshots_drawer_open_sizes(total_width: int) -> tuple[int, int]:
    """Left/right splitter sizes when the Snapshots list is shown."""
    total = max(int(total_width), 0)
    if total <= 0:
        return (SNAPSHOT_DRAWER_MIN_OPEN_PX, 800)
    left = max(int(total * SNAPSHOT_DRAWER_OPEN_RATIO), SNAPSHOT_DRAWER_MIN_OPEN_PX)
    max_left = max(total - SNAPSHOT_DRAWER_COMPARE_MIN_PX, total // 2)
    left = min(left, max_left)
    left = max(left, min(SNAPSHOT_DRAWER_MIN_OPEN_PX, total))
    return (left, max(total - left, 1))


def full_snapshot_saved_message(
    snapshot_name: str,
    file_count: int,
    *,
    software_file_count: int = 0,
    software_captured: bool = False,
    extra_notes: tuple[str, ...] | list[str] = (),
) -> str:
    """One success dialog after Create full snapshot (no compare, no write-back)."""
    from config_scanner.software_compat import scan_user_warnings

    name = (snapshot_name or "").strip() or "(unnamed)"
    count = max(int(file_count), 0)
    files = "file" if count == 1 else "files"
    lines = [
        "This machine is saved.",
        "",
        name,
        "",
        f"• {count} config {files}",
    ]
    if software_captured:
        sw = max(int(software_file_count), 0)
        lines.append(f"• {sw} Ruleta software files (exe + Godot)")
    else:
        lines.append("• Ruleta software was not included")
        lines.append(
            "  Restore will look for a matching pack if you need the exe later."
        )
    lines.extend(
        [
            "",
            "The machine was not changed.",
            "",
            "This snapshot is selected. Click Restore snapshot when you "
            "want this version back.",
        ]
    )
    notes = scan_user_warnings(tuple(extra_notes))
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"• {note}" for note in notes)
    return "\n".join(lines)


def _configure_snapshot_combo(combo: QComboBox, *, min_width: int = 200) -> None:
    """Show full snapshot folder names (date_build_time_ms) without clipping."""
    combo.setEditable(False)
    combo.setMinimumWidth(min_width)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    # Typical name: 2026-07-27_Ruleta_Alegro_Wing_v10.2.0.876_b40119_091759
    combo.setMinimumContentsLength(36)


# Primary restore: config + matching Ruleta software (seamless transfer).
_SNAPSHOT_CONTEXT_RESTORE_LABELS: dict[WriteScope, str] = {
    WriteScope.FULL_SOFTWARE: "Restore to machine (config + software)…",
    WriteScope.FULL: "Restore config only…",
    WriteScope.BINARIES_ONLY: "Restore software only (keep profile)…",
}
_SNAPSHOT_CONTEXT_PRIMARY_RESTORE = WriteScope.FULL_SOFTWARE


def snapshot_context_primary_restore_label() -> str:
    return _SNAPSHOT_CONTEXT_RESTORE_LABELS[_SNAPSHOT_CONTEXT_PRIMARY_RESTORE]


def snapshot_context_advanced_restore_actions() -> list[tuple[str, WriteScope]]:
    """Extra restore scopes. Config-only is also on the snapshot right-click menu."""
    return [
        (_SNAPSHOT_CONTEXT_RESTORE_LABELS[scope], scope)
        for scope in (WriteScope.FULL, WriteScope.BINARIES_ONLY)
    ]


def snapshot_context_restore_actions(
    extra_scope: WriteScope | None = None,
) -> list[tuple[str, WriteScope]]:
    """Legacy helper — primary restore first, then advanced scopes."""
    items = [
        (_SNAPSHOT_CONTEXT_RESTORE_LABELS[_SNAPSHOT_CONTEXT_PRIMARY_RESTORE], _SNAPSHOT_CONTEXT_PRIMARY_RESTORE),
        *snapshot_context_advanced_restore_actions(),
    ]
    if extra_scope is not None and extra_scope not in {a[1] for a in items}:
        items.append(
            (f"Restore {WRITE_SCOPE_SHORT[extra_scope]} to machine…", extra_scope)
        )
    return items


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
        self._emitter.stack_restart_finished.connect(self._on_stack_restart_finished)
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
        self._apply_buttons: list[tuple[QWidget, bool]] = []
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
        self._snapshot_sel_guard = False
        self._snapshot_sel_ui_pending = False
        self._pending_scan_and_compare = False
        self._pending_create_snapshot = False
        self._pending_include_software = False
        self._last_scan_file_count = 0
        self._last_scan_software_count = 0
        self._last_scan_software_ok = False
        self._last_scan_user_warnings: tuple[str, ...] = ()
        # After Confirm → scan live config first, then write this snapshot.
        self._pending_write_snapshot: str | None = None
        self._pending_write_scope: str = WriteScope.FULL.value
        self._presave_snapshot_for_write: str | None = None
        self._pending_is_revert = False
        self._pending_stack_plan = None
        self._pending_stack_phase: str | None = None
        self._stack_killed_ok = False
        self._offer_llave_after_restart = False
        self._pending_restore_title: str | None = None
        self._pending_restore_body: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(6)

        # --- Slim toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._drive_edit = QLineEdit(SettingsManager.get_config_scanner_game_drive())
        self._drive_edit.setPlaceholderText("Scan target: local drive or \\\\10.0.0.90\\c$\\Goldclub")
        self._drive_edit.textChanged.connect(self._on_drive_text_changed)
        self._drive_edit.editingFinished.connect(self._on_drive_editing_finished)
        toolbar.addWidget(self._drive_edit, stretch=1)
        self._detect_btn = QPushButton("Auto-detect")
        self._detect_btn.setToolTip(
            "Find local image or remote .90 Goldclub when EXEs exist (OneHand.exe / Ruleta.exe)."
        )
        self._detect_btn.clicked.connect(self._on_detect_drive)
        toolbar.addWidget(self._detect_btn)
        self._create_snapshot_btn = QPushButton("Create full snapshot")
        self._create_snapshot_btn.setObjectName("primary")
        self._create_snapshot_btn.setToolTip(
            "Save live config and Ruleta software into one snapshot. "
            "Does not write anything back. Restore that snapshot later to "
            "return this machine to this version."
        )
        self._create_snapshot_btn.clicked.connect(self._on_create_snapshot_clicked)
        self._create_snapshot_btn.setEnabled(False)
        toolbar.addWidget(self._create_snapshot_btn)
        self._restore_toolbar_btn = QPushButton("Restore snapshot")
        self._restore_toolbar_btn.setToolTip(
            "Put the selected snapshot back on the machine "
            "(config + Ruleta software). Live config is saved first so you can undo."
        )
        self._restore_toolbar_btn.clicked.connect(self._on_restore_selected_clicked)
        self._restore_toolbar_btn.setEnabled(False)
        toolbar.addWidget(self._restore_toolbar_btn)
        self._scan_btn = QPushButton("Scan && compare")
        self._scan_btn.setToolTip(
            "Scan config only, then compare it to the previous snapshot."
        )
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._scan_btn.setEnabled(False)
        self._scan_btn.hide()
        toolbar.addWidget(self._scan_btn)
        self._compare_latest_btn = QPushButton("Quick compare")
        self._compare_latest_btn.setToolTip(
            "Diff the last two scans. If you Set baseline in this session, "
            "compares that baseline to the newest other scan."
        )
        self._compare_latest_btn.clicked.connect(self._on_compare_latest_clicked)
        self._compare_latest_btn.hide()
        toolbar.addWidget(self._compare_latest_btn)
        self._snapshots_toggle = QToolButton()
        self._snapshots_toggle.setText("Snapshots")
        self._snapshots_toggle.setCheckable(True)
        self._snapshots_toggle.setChecked(False)
        self._snapshots_toggle.setToolTip("Show or hide the snapshots drawer.")
        self._snapshots_toggle.toggled.connect(self._on_snapshots_drawer_toggled)
        toolbar.addWidget(self._snapshots_toggle)
        self._more_btn = QToolButton()
        self._more_btn.setText("More")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self)
        self._scan_action = QAction("Scan && compare (config only)", self)
        self._scan_action.setToolTip(
            "Scan live config (no software copy), then compare to the previous snapshot."
        )
        self._scan_action.triggered.connect(self._on_scan_clicked)
        more_menu.addAction(self._scan_action)
        self._compare_latest_action = QAction("Quick compare last two", self)
        self._compare_latest_action.triggered.connect(self._on_compare_latest_clicked)
        more_menu.addAction(self._compare_latest_action)
        self._compare_action = QAction("Compare selected snapshots", self)
        self._compare_action.setToolTip(
            "Compare the Reference and Compared snapshots selected in the Snapshots list."
        )
        self._compare_action.triggered.connect(self._on_compare_clicked)
        more_menu.addAction(self._compare_action)
        self._refresh_action = QAction("Refresh list", self)
        self._refresh_action.triggered.connect(self.refresh_snapshots)
        more_menu.addAction(self._refresh_action)
        self._create_snapshot_action = QAction("Create full snapshot", self)
        self._create_snapshot_action.setToolTip(
            "Save live config and Ruleta software into one snapshot."
        )
        self._create_snapshot_action.triggered.connect(self._on_create_snapshot_clicked)
        more_menu.addAction(self._create_snapshot_action)
        more_menu.addSeparator()
        self._set_baseline_action = QAction("Set reference baseline", self)
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
        more_menu.addSeparator()
        self._restore_selected_action = QAction("Restore snapshot", self)
        self._restore_selected_action.setToolTip(
            "Put the selected snapshot back (config + Ruleta software). "
            "Live config is saved first so you can undo."
        )
        self._restore_selected_action.triggered.connect(self._on_restore_selected_clicked)
        self._restore_selected_action.setEnabled(False)
        more_menu.addAction(self._restore_selected_action)
        self._restore_selected_software_action = QAction(
            "Restore snapshot",
            self,
        )
        self._restore_selected_software_action.setToolTip(
            "Put the selected snapshot back (config + Ruleta software)."
        )
        self._restore_selected_software_action.triggered.connect(
            self._on_restore_selected_software_clicked
        )
        self._restore_selected_software_action.setEnabled(False)
        self._restore_selected_config_action = QAction(
            "Restore config only…",
            self,
        )
        self._restore_selected_config_action.setToolTip(
            "Write config files only. Live Ruleta.exe stays as-is."
        )
        self._restore_selected_config_action.triggered.connect(
            self._on_restore_selected_config_clicked
        )
        self._restore_selected_config_action.setEnabled(False)
        more_menu.addAction(self._restore_selected_config_action)
        self._restore_selected_binaries_action = QAction(
            "Restore software only (keep profile)…",
            self,
        )
        self._restore_selected_binaries_action.setToolTip(
            "Push Ruleta binaries only. Keeps this cabinet's setup, "
            "switches, wheel, SAS, serialport, and licence."
        )
        self._restore_selected_binaries_action.triggered.connect(
            self._on_restore_selected_binaries_clicked
        )
        self._restore_selected_binaries_action.setEnabled(False)
        more_menu.addAction(self._restore_selected_binaries_action)
        self._write_baseline_action = QAction("Restore reference to machine", self)
        self._write_baseline_action.setToolTip(
            "After a compare: save live config, then restore the reference "
            "archive onto the machine."
        )
        self._write_baseline_action.triggered.connect(self._on_write_baseline_from_compare)
        self._write_baseline_action.setEnabled(False)
        more_menu.addAction(self._write_baseline_action)
        self._revert_action = QAction("Revert last restore", self)
        self._revert_action.setToolTip(
            "Undo the last bulk restore by putting back the config saved before it."
        )
        self._revert_action.triggered.connect(self._on_revert_clicked)
        self._revert_action.setEnabled(False)
        more_menu.addAction(self._revert_action)
        self._clear_error30_action = QAction("Clear ERROR 30 / 99 trial leftovers…", self)
        self._clear_error30_action.setToolTip(
            "Backup and delete Ruleta persistent trial tokens "
            "(RouletteActivate.dat / FinanceStamps / Password.dat) that survive "
            "RAM clear, plus leftover USB/wrong-WIBU XML only. Live 37A55022 / "
            "licence.dll are not written."
        )
        self._clear_error30_action.triggered.connect(self._on_clear_error30_clicked)
        more_menu.addAction(self._clear_error30_action)
        self._llave_password_action = QAction("Enter LLAVE trial password…", self)
        self._llave_password_action.setToolTip(
            "Save a vendor trial password and auto-type it on the cabinet "
            "(Clear-Error30.ps1 -Auto). Use the System ID from ERROR 99."
        )
        self._llave_password_action.triggered.connect(self._on_llave_password_clicked)
        more_menu.addAction(self._llave_password_action)
        self._start_stack_action = QAction("Start GoldClub stack…", self)
        self._start_stack_action.setToolTip(
            "Run-FullStack only (stack should already be down after Kill-All)."
        )
        self._start_stack_action.triggered.connect(self._on_start_stack_clicked)
        more_menu.addAction(self._start_stack_action)
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
        more_menu.addSeparator()
        self._about_action = QAction("About Config Scanner", self)
        self._about_action.triggered.connect(self._on_about_scanner)
        more_menu.addAction(self._about_action)
        self._more_btn.setMenu(more_menu)
        toolbar.addWidget(self._more_btn)
        self._write_scope_combo = QComboBox(self)
        self._write_scope_combo.setObjectName("writeScopeCombo")
        self._write_scope_combo.setMinimumWidth(200)
        self._write_scope_combo.setMaximumWidth(360)
        for scope in (
            WriteScope.FULL,
            WriteScope.NO_PAYTABLE,
            WriteScope.FULL_SOFTWARE,
            WriteScope.BINARIES_ONLY,
            WriteScope.HARDWARE,
            WriteScope.SOFTWARE,
        ):
            self._write_scope_combo.addItem(WRITE_SCOPE_LABELS[scope], scope.value)
            idx = self._write_scope_combo.count() - 1
            self._write_scope_combo.setItemData(
                idx,
                WRITE_SCOPE_DESCRIPTIONS[scope],
                Qt.ItemDataRole.ToolTipRole,
            )
        full_sw = self._write_scope_combo.findData(WriteScope.FULL_SOFTWARE.value)
        self._write_scope_combo.setCurrentIndex(full_sw if full_sw >= 0 else 0)
        self._write_scope_combo.setToolTip(
            "Bulk restore scope: Full, Config except paytables, Config + Ruleta "
            "software, Ruleta software only (keep cabinet profile), Hardware, "
            "or Software. Per-setting Apply always changes one field."
        )
        self._write_scope_combo.currentIndexChanged.connect(self._on_write_scope_changed)
        self._write_scope_combo.hide()
        self._changes_find_edit = QLineEdit()
        self._changes_find_edit.setPlaceholderText("Filter changes…")
        self._changes_find_edit.setToolTip(
            "Filter changed files and settings (case-insensitive substring match)."
        )
        self._changes_find_edit.setClearButtonEnabled(True)
        self._changes_find_edit.setMinimumWidth(160)
        self._changes_find_edit.setMaximumWidth(240)
        self._changes_find_edit.textChanged.connect(self._on_changes_find_changed)
        toolbar.addWidget(self._changes_find_edit)
        root.addLayout(toolbar)

        live_row = QHBoxLayout()
        live_row.setContentsMargins(2, 2, 2, 0)
        live_row.setSpacing(8)
        self._live_sw_label = QLabel(format_live_ruleta_sw_banner(None))
        self._live_sw_label.setObjectName("liveRuletaVersion")
        self._live_sw_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._live_sw_label.setStyleSheet(
            "QLabel#liveRuletaVersion { font-weight: 700; font-size: 15px; }"
        )
        self._live_sw_label.setToolTip(
            "ProductVersion of live ruleta\\Ruleta.exe on this scan target."
        )
        live_row.addWidget(self._live_sw_label, stretch=1)
        root.addLayout(live_row)

        from gui.thin_progress import make_thin_busy_progress

        self._scan_progress = make_thin_busy_progress(self)
        root.addWidget(self._scan_progress)

        self._rollback_banner = QFrame()
        self._rollback_banner.setObjectName("rollbackBanner")
        self._rollback_banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(self._rollback_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_layout.setSpacing(10)
        self._rollback_banner_label = QLabel()
        self._rollback_banner_label.setWordWrap(True)
        banner_layout.addWidget(self._rollback_banner_label, stretch=1)
        self._revert_btn = QPushButton("Revert last restore")
        self._revert_btn.setToolTip(
            "Restore the machine to the config saved immediately before your last bulk restore."
        )
        self._revert_btn.clicked.connect(self._on_revert_clicked)
        self._revert_btn.setEnabled(False)
        banner_layout.addWidget(self._revert_btn, stretch=0)
        self._rollback_banner.setVisible(False)
        root.addWidget(self._rollback_banner)

        # --- Snapshots drawer (hidden by default) ---
        self._snapshots_drawer = QWidget()
        drawer_layout = QVBoxLayout(self._snapshots_drawer)
        drawer_layout.setContentsMargins(0, 0, 4, 0)
        drawer_layout.setSpacing(6)

        restore_sel_row = QHBoxLayout()
        restore_sel_row.setSpacing(6)
        self._restore_selected_btn = QPushButton("Restore to machine")
        self._restore_selected_btn.setToolTip(
            "Put the selected snapshot back on the machine (config + Ruleta "
            "software, with LLAVE trial bind when the snapshot captured it). "
            "Live config is saved first so you can undo."
        )
        self._restore_selected_btn.clicked.connect(self._on_restore_selected_clicked)
        self._restore_selected_btn.setEnabled(False)
        restore_sel_row.addWidget(self._restore_selected_btn, stretch=0)
        self._restore_config_only_btn = QPushButton("Restore config only")
        self._restore_config_only_btn.setToolTip(
            "Write config files only. Live Ruleta.exe stays as-is. "
            "Kill-All still runs first on a live cabinet."
        )
        self._restore_config_only_btn.clicked.connect(
            self._on_restore_selected_config_clicked
        )
        self._restore_config_only_btn.setEnabled(False)
        restore_sel_row.addWidget(self._restore_config_only_btn, stretch=0)
        self._auto_start_stack_cb = QCheckBox("Auto-start stack")
        self._auto_start_stack_cb.setChecked(
            SettingsManager.get_config_scanner_auto_start_stack()
        )
        self._auto_start_stack_cb.setToolTip(
            "After restore or revert: always Kill-All first, then Run-FullStack "
            "if this is checked. Uncheck to leave the stack down (faster; "
            "start later from More)."
        )
        self._auto_start_stack_cb.toggled.connect(
            SettingsManager.set_config_scanner_auto_start_stack
        )
        restore_sel_row.addWidget(self._auto_start_stack_cb, stretch=0)
        restore_sel_row.addStretch(1)
        drawer_layout.addLayout(restore_sel_row)

        self._snapshot_model = SnapshotTableModel(self)
        self._snapshot_table = QTableView()
        self._snapshot_table.setModel(self._snapshot_model)
        configure_snapshot_table_view(self._snapshot_table)
        self._snapshot_table.setItemDelegateForColumn(
            SnapshotTableModel.VERSION_COL,
            SnapshotVersionDelegate(self._snapshot_table),
        )
        header = self._snapshot_table.horizontalHeader()
        header.setMinimumSectionSize(48)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        for col in range(self._snapshot_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._snapshot_table.setMinimumHeight(64)
        self._snapshot_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._snapshot_table.customContextMenuRequested.connect(self._on_snapshot_context_menu)
        self._snapshot_table.installEventFilter(self)
        self._snapshot_table.viewport().installEventFilter(self)
        self._snapshot_table.addAction(self._delete_action)
        drawer_layout.addWidget(self._snapshot_table, stretch=1)

        self._baseline_combo = QComboBox()
        _configure_snapshot_combo(self._baseline_combo, min_width=140)
        self._baseline_combo.setToolTip(
            "Reference snapshot — left side of the diff (older or known-good config)."
        )
        drawer_layout.addLayout(
            _tight_label_field_row("Reference:", self._baseline_combo, field_min_width=140)
        )
        self._target_combo = QComboBox()
        _configure_snapshot_combo(self._target_combo, min_width=140)
        self._target_combo.setToolTip(
            "Compared snapshot — right side of the diff (usually the newer scan)."
        )
        drawer_layout.addLayout(
            _tight_label_field_row("Compared:", self._target_combo, field_min_width=140)
        )

        drawer_btn_row = QHBoxLayout()
        drawer_btn_row.setSpacing(6)
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setToolTip("Compare the Baseline and Target snapshots selected above.")
        self._compare_btn.clicked.connect(self._on_compare_clicked)
        drawer_btn_row.addWidget(self._compare_btn)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh_snapshots)
        drawer_btn_row.addWidget(self._refresh_btn)
        drawer_btn_row.addStretch(1)
        drawer_layout.addLayout(drawer_btn_row)

        self._snapshot_table.selectionModel().selectionChanged.connect(
            lambda *_: self._on_snapshot_selection_changed()
        )
        self._target_combo.activated.connect(self._on_compared_combo_activated)
        self._target_combo.currentTextChanged.connect(
            lambda *_: self._refresh_restore_selected_ui()
        )

        self._snapshots_drawer.setVisible(False)
        self._snapshots_drawer.setMinimumWidth(520)

        # --- Compare hero panel ---
        self._changes_scroll = QScrollArea()
        self._changes_scroll.setWidgetResizable(True)
        self._changes_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._changes_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._changes_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._changes_content = QWidget()
        self._changes_layout = QVBoxLayout(self._changes_content)
        self._changes_layout.setContentsMargins(12, 12, 12, 12)
        self._changes_layout.setSpacing(8)
        self._changes_scroll.setWidget(self._changes_content)

        self._write_baseline_btn = QPushButton("Restore to machine")
        self._write_baseline_btn.setToolTip(
            "Save live config first (rollback snapshot), then restore using the scope below."
        )
        self._write_baseline_btn.clicked.connect(self._on_write_baseline_from_compare)
        self._write_baseline_btn.setVisible(False)
        self._write_baseline_btn.setEnabled(False)
        self._write_baseline_btn.setParent(self)
        self._refresh_write_action_labels()

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._snapshots_drawer)
        self._main_splitter.addWidget(self._changes_scroll)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setCollapsible(0, True)
        self._main_splitter.setCollapsible(1, False)
        self._main_splitter.setSizes([720, 880])
        root.addWidget(self._main_splitter, stretch=1)

        # --- Collapsed status / expandable log ---
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._status_label = QLabel("Ready")
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        status_row.addWidget(self._status_label, stretch=1)
        self._log_toggle = QToolButton()
        self._log_toggle.setText("Log")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setChecked(False)
        self._log_toggle.setToolTip("Show or hide the detailed status log.")
        self._log_toggle.toggled.connect(self._on_log_toggled)
        status_row.addWidget(self._log_toggle)
        root.addLayout(status_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Status, progress, and errors…")
        self._log.setMaximumBlockCount(2000)
        self._log.setMaximumHeight(160)
        self._log.setVisible(False)
        root.addWidget(self._log)

        attach_open_with_npp_menu(
            self._log,
            path_provider=lambda: self._last_report_path,
            parent=self,
            label="Open report with Notepad++",
        )

        self._update_data_path_label()
        QTimer.singleShot(0, self._apply_snapshot_table_column_widths)
        self._rebuild_changes_panel(None)
        self._validate_pending = self._drive_edit.text().strip()
        QTimer.singleShot(0, self._run_target_validation)
        logger.info(
            "ConfigScannerTabWidget.__init__ done snapshots_dir=%s",
            self._service.get_snapshots_dir(),
        )
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

    def _widget_is_alive(self, widget: QWidget | None) -> bool:
        return widget is not None and _qt_widget_is_valid(widget)

    def _detach_persistent_compare_controls(self) -> None:
        """Keep bulk-restore controls out of the scroll content tree we delete."""
        if self._widget_is_alive(getattr(self, "_write_baseline_btn", None)):
            self._write_baseline_btn.setParent(self)
            self._write_baseline_btn.hide()
        # Write-scope combo lives on the snapshots drawer; do not hide it.

    def _replace_changes_content(self) -> None:
        """Drop the previous compare panel widget tree so a new result always paints fresh."""
        self._apply_buttons.clear()
        self._detach_persistent_compare_controls()
        # takeWidget() transfers ownership to us. setWidget() would delete the old
        # child immediately — never touch that pointer afterward (RuntimeError).
        old = self._changes_scroll.takeWidget()
        self._changes_content = QWidget()
        self._changes_layout = QVBoxLayout(self._changes_content)
        self._changes_layout.setContentsMargins(12, 12, 12, 12)
        self._changes_layout.setSpacing(8)
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
        self._write_baseline_action.setEnabled(False)
        self._append_status(f"Comparing {baseline} → {target} …", force=True)
        self._replace_changes_content()
        self._add_changes_panel_note(f"Comparing {baseline} → {target} …")
        self._changes_scroll.verticalScrollBar().setValue(0)

    def _on_snapshots_drawer_toggled(self, checked: bool) -> None:
        self._snapshots_drawer.setVisible(checked)
        if checked:
            QTimer.singleShot(0, self._expand_snapshots_drawer)

    def _expand_snapshots_drawer(self) -> None:
        """Open the snapshot list at ~45% width so full names stay readable."""
        if not self._widget_is_alive(getattr(self, "_main_splitter", None)):
            return
        total = self._main_splitter.width()
        if total < 80:
            total = max(self.width(), 1600)
        left, right = snapshots_drawer_open_sizes(total)
        self._main_splitter.setSizes([left, right])
        self._apply_snapshot_table_column_widths()

    def _ensure_snapshots_drawer_visible(self) -> None:
        if not self._snapshots_toggle.isChecked():
            self._snapshots_toggle.setChecked(True)

    def _on_log_toggled(self, checked: bool) -> None:
        self._log.setVisible(checked)
        if checked:
            bar = self._log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _on_about_scanner(self) -> None:
        data_root = self._service.get_data_root()
        snap_dir = self._service.get_snapshots_dir()
        QMessageBox.information(
            self,
            "About Config Scanner",
            "Config SHA1 Scanner snapshots QA config from a local slot/roulette "
            "image or a remote lab cabinet "
            "(e.g. \\\\10.0.0.90\\c$\\Goldclub).\n\n"
            "Requires real binaries (OneHand.exe / Ruleta.exe); "
            "empty C:\\Goldclub is ignored.\n\n"
            "Create full snapshot saves live config and Ruleta software together. "
            "Restore snapshot puts that version back. Right-click a snapshot to write "
            "it back to the scan target (overwrite-only, scan scope).\n\n"
            f"Data folder: {data_root}\n"
            f"Snapshots: {snap_dir}",
        )

    def _format_diff_cell_value(self, value: str | None) -> str:
        return format_diff_cell_value(value)

    def _compare_panel_styles(self) -> dict[str, str]:
        return compare_panel_encrypted_origin_styles(self.palette())

    def _make_value_chip(
        self,
        value: str | None,
        *,
        styles: dict[str, str],
        encrypted_origin: bool,
        side_label: str,
        snapshot_name: str,
    ) -> QLabel:
        display = self._format_diff_cell_value(value)
        chip = QLabel(display)
        muted = value is None or not (value or "").strip()
        if encrypted_origin:
            chip.setStyleSheet(
                styles["encrypted_value_muted"] if muted else styles["encrypted_value"]
            )
        else:
            chip.setStyleSheet(styles["chip_muted"] if muted else styles["chip"])
        chip.setToolTip(
            f"{side_label} snapshot: {snapshot_name}\n"
            f"Value: {format_apply_value_detail(value)}"
        )
        chip.setWordWrap(False)
        chip.setMaximumWidth(260)
        chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return chip

    def _make_diff_row(
        self,
        *,
        change: ContentChange,
        relative_path: str,
        baseline_name: str,
        target_name: str,
        encrypted_origin: bool,
        styles: dict[str, str],
    ) -> QWidget:
        """One compact row: Setting | Baseline → Target | Write target (+ menu)."""
        row_host = QWidget()
        layout = QHBoxLayout(row_host)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        setting_name = setting_display_name(change.path)
        change_line = format_setting_change_description(change).strip()
        setting_lbl = QLabel(setting_name)
        setting_lbl.setWordWrap(True)
        setting_lbl.setMinimumWidth(120)
        setting_lbl.setStyleSheet(
            styles["encrypted_setting"] if encrypted_origin else styles["plain_setting"]
        )
        tip = f"XML path:\n{change.path}\n\n{change_line}"
        if encrypted_origin:
            tip += "\n\nFrom encrypted-on-disk ruleta setup.xml (decrypted for compare)."
        setting_lbl.setToolTip(tip)
        layout.addWidget(setting_lbl, stretch=1)

        baseline_value = resolve_apply_value(change, "baseline")
        target_value = resolve_apply_value(change, "target")
        layout.addWidget(
            self._make_value_chip(
                baseline_value,
                styles=styles,
                encrypted_origin=encrypted_origin,
                side_label="Reference",
                snapshot_name=baseline_name,
            ),
            stretch=0,
        )
        arrow = QLabel("\u2192")
        arrow.setStyleSheet(styles["arrow"])
        layout.addWidget(arrow, stretch=0)
        layout.addWidget(
            self._make_value_chip(
                target_value,
                styles=styles,
                encrypted_origin=encrypted_origin,
                side_label="Compared",
                snapshot_name=target_name,
            ),
            stretch=0,
        )

        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        can_write_target = target_value is not None and is_actionable_content_change(
            change, relative_path=relative_path
        )
        can_write_baseline = baseline_value is not None and is_actionable_content_change(
            change, relative_path=relative_path
        )

        primary_side: str | None = None
        primary_value: str | None = None
        if can_write_target:
            primary_side, primary_value = "target", target_value
        elif can_write_baseline:
            primary_side, primary_value = "baseline", baseline_value

        if primary_side is not None and primary_value is not None:
            write_btn = QPushButton(apply_button_label(primary_value))
            if apply_button_label(primary_value) == "Apply":
                write_btn.setText("Apply to machine")
            else:
                write_btn.setText("Apply empty")
            write_btn.setObjectName("primary")
            write_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            snap = target_name if primary_side == "target" else baseline_name
            write_btn.setToolTip(
                f"Apply {primary_side} snapshot value to the live machine:\n"
                f"{format_apply_value_detail(primary_value)}\n\n"
                f"Source: {primary_side} snapshot {snap}"
            )
            write_btn.setEnabled(not self._busy)
            write_btn.clicked.connect(
                lambda checked=False, rp=relative_path, ch=change, s=primary_side: (
                    self._confirm_apply_change(rp, ch, s)
                )
            )
            actions_layout.addWidget(write_btn)
            self._apply_buttons.append((write_btn, True))

        # Secondary: Write the other side when both exist.
        if can_write_target and can_write_baseline:
            more = QToolButton()
            more.setText("\u22ef")
            more.setToolTip("Apply the other snapshot value")
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            more.setEnabled(not self._busy)
            menu = QMenu(more)
            label = apply_button_label(baseline_value)
            action_text = (
"Apply reference value" if label == "Apply" else "Apply empty (reference)"
            )
            act = QAction(action_text, more)
            act.setToolTip(
                f"Apply reference snapshot value to live:\n"
                f"{format_apply_value_detail(baseline_value)}"
            )
            act.triggered.connect(
                lambda checked=False, rp=relative_path, ch=change: self._confirm_apply_change(
                    rp, ch, "baseline"
                )
            )
            menu.addAction(act)
            more.setMenu(menu)
            actions_layout.addWidget(more)
            self._apply_buttons.append((more, True))

        if primary_side is None and is_structural_item_change(change):
            note = QLabel("Whole driver — use Restore file")
            note.setStyleSheet(styles["legend"])
            note.setToolTip(
                "Entire HW driver entry add/remove (not a field rename).\n"
                "Restore configuration.xml via Write file from a snapshot/backup.\n"
                "Other drivers kept their identity; only list indices shifted."
            )
            actions_layout.addWidget(note)

        layout.addWidget(actions, stretch=0)
        return row_host

    def _make_file_section(
        self,
        file_diff,
        *,
        baseline_name: str,
        target_name: str,
        styles: dict[str, str],
        collapse: bool,
        start_expanded: bool,
    ) -> QWidget:
        """File card with optional collapsible body."""
        encrypted_origin = is_encrypted_origin_config_path(file_diff.relative_path)
        protected = is_protected_write_path(file_diff.relative_path)
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(styles.get("file_card", ""))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        toggle: QToolButton | None = None
        if collapse:
            toggle = QToolButton()
            toggle.setCheckable(True)
            toggle.setChecked(start_expanded)
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if start_expanded else Qt.ArrowType.RightArrow
            )
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            toggle.setAutoRaise(True)
            header_row.addWidget(toggle, stretch=0)

        header = QLabel(file_diff_header_label(file_diff.relative_path, file_diff.status))
        if protected:
            header.setStyleSheet(styles.get("protected_header", styles["legend"]))
            if is_licence_path(file_diff.relative_path):
                header.setToolTip(
                    "Live licence is never overwritten. A different dongle is "
                    "never written. Restored only if missing and the serial matches."
                )
            else:
                header.setToolTip(
                    protected_write_block_reason(file_diff.relative_path)
                    or "Config Scanner never writes this path (serialport/ or EGM identity)."
                )
        else:
            header.setStyleSheet(
                styles["encrypted_header"] if encrypted_origin else styles["plain_header"]
            )
        header.setWordWrap(True)
        if encrypted_origin and not protected:
            header.setToolTip(
                "Live file is gcxml-encrypted ruleta setup.xml. "
                "Values shown are from the decrypted plain settings used for scan/compare."
            )
        header_row.addWidget(header, stretch=1)
        card_layout.addLayout(header_row)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        if not file_diff.content_diff:
            if file_diff.status in {"removed", "added"}:
                # Attach missing-file write into body via temporary host.
                host = QWidget()
                host_layout = QVBoxLayout(host)
                host_layout.setContentsMargins(0, 0, 0, 0)
                # Reuse existing helper by temporarily pointing layout — call inline.
                self._fill_missing_file_write(host_layout, file_diff, baseline_name, target_name, styles)
                body_layout.addWidget(host)
            else:
                note = QLabel(
                    "File hash differs but no archived content is available for field-level apply"
                )
                note.setStyleSheet(styles["legend"])
                note.setWordWrap(True)
                body_layout.addWidget(note)
        else:
            actionable, opaque = content_change_panel_partition(
                file_diff.content_diff,
                relative_path=file_diff.relative_path,
            )
            visible_changes = actionable[:COMPARE_PANEL_MAX_CHANGES_PER_FILE]
            omitted_actionable = max(0, len(actionable) - len(visible_changes))

            if not visible_changes and opaque:
                note = QLabel(
                    f"Encrypted config churn: {len(opaque)} token change(s) "
                    f"(gcxml re-encryption — not writable as settings; open report for full diff)"
                )
                note.setStyleSheet(styles["legend"])
                note.setWordWrap(True)
                body_layout.addWidget(note)
            else:
                for change in visible_changes:
                    body_layout.addWidget(
                        self._make_diff_row(
                            change=change,
                            relative_path=file_diff.relative_path,
                            baseline_name=baseline_name,
                            target_name=target_name,
                            encrypted_origin=encrypted_origin,
                            styles=styles,
                        )
                    )
                if omitted_actionable > 0:
                    note = QLabel(
                        f"… {omitted_actionable} more writable change(s) in this file; "
                        f"open report for full diff"
                    )
                    note.setStyleSheet(styles["legend"])
                    note.setWordWrap(True)
                    body_layout.addWidget(note)
                if opaque:
                    note = QLabel(
                        f"… {len(opaque)} encrypted/opaque token change(s) omitted "
                        f"(not writable; open report)"
                    )
                    note.setStyleSheet(styles["legend"])
                    note.setWordWrap(True)
                    body_layout.addWidget(note)

        card_layout.addWidget(body)
        if toggle is not None:
            body.setVisible(start_expanded)

            def _on_toggled(checked: bool, b=body, t=toggle) -> None:
                b.setVisible(checked)
                t.setArrowType(
                    Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
                )

            toggle.toggled.connect(_on_toggled)
        return card

    def _fill_missing_file_write(
        self,
        parent_layout,  # noqa: ANN001
        file_diff,
        baseline_name: str,
        target_name: str,
        styles: dict[str, str],
    ) -> None:
        encrypted_origin = is_encrypted_origin_config_path(file_diff.relative_path)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if file_diff.status == "removed":
            note = f"Missing on target — Write file from baseline snapshot ({baseline_name})"
            source_side = "baseline"
            source_snap = baseline_name
        else:
            note = f"Missing on baseline — Write file from target snapshot ({target_name})"
            source_side = "target"
            source_snap = target_name
        note_lbl = QLabel(note)
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(
            styles["encrypted_setting"] if encrypted_origin else styles["legend"]
        )
        row.addWidget(note_lbl, stretch=1)
        protected = is_protected_write_path(file_diff.relative_path)
        licence = is_licence_path(file_diff.relative_path)
        btn = QPushButton(f"Write file (from {source_side})")
        if protected and not licence:
            block_reason = protected_write_block_reason(file_diff.relative_path) or (
                "Write blocked for this path."
            )
            btn.setEnabled(False)
            btn.setToolTip(block_reason)
            note_lbl.setText(note + " — write blocked (protected path).")
        else:
            btn.setEnabled(not self._busy)
            tip = (
                f"Copy archived {file_diff.relative_path} from {source_snap} "
                "onto the live scan target."
            )
            if licence:
                tip = (
                    "Licence: live XML is never overwritten. Restored only if "
                    "missing on this EGM. A different dongle is never written."
                )
            btn.setToolTip(tip)
            btn.clicked.connect(
                lambda checked=False, rp=file_diff.relative_path, sn=source_snap: (
                    self._confirm_write_archived_file(rp, sn)
                )
            )
        row.addWidget(btn)
        self._apply_buttons.append((btn, (not protected) or licence))
        parent_layout.addWidget(host)

    def _add_truncation_report_row(self, message: str) -> None:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        note = QLabel(message)
        note.setStyleSheet(self._compare_panel_styles()["legend"])
        note.setWordWrap(True)
        row.addWidget(note, stretch=1)
        btn = QPushButton("Open HTML report")
        btn.setEnabled(
            self._last_report_path is not None and self._last_report_path.is_file()
        )
        btn.clicked.connect(self._on_open_report_clicked)
        row.addWidget(btn, stretch=0)
        self._changes_layout.addWidget(host)

    def _add_changes_panel_note(self, text: str) -> None:
        note = QLabel(text)
        note.setStyleSheet(self._compare_panel_styles()["legend"])
        note.setWordWrap(True)
        self._changes_layout.addWidget(note)

    def _add_compare_summary_strip(
        self,
        *,
        text: str,
        styles: dict[str, str],
        tooltip: str = "",
        show_write_baseline: bool = False,
    ) -> None:
        """Hero summary + bulk-restore controls."""
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(6)
        summary = QLabel(text)
        summary.setStyleSheet(styles["summary"])
        summary.setWordWrap(True)
        if tooltip:
            summary.setToolTip(tooltip)
        outer.addWidget(summary)
        if show_write_baseline:
            restore_row = QHBoxLayout()
            restore_row.setSpacing(8)
            self._write_baseline_btn.setVisible(True)
            restore_row.addWidget(self._write_baseline_btn, stretch=0)
            hint = QLabel(
                "Restore puts config + software back. Live config is saved first. "
                "Apply below changes one setting at a time."
            )
            hint.setStyleSheet(styles["legend"])
            hint.setWordWrap(True)
            restore_row.addWidget(hint, stretch=1)
            outer.addLayout(restore_row)
        self._changes_layout.addWidget(host)


    def _current_write_scope(self) -> WriteScope:
        if not self._widget_is_alive(getattr(self, "_write_scope_combo", None)):
            return WriteScope.FULL_SOFTWARE
        raw = self._write_scope_combo.currentData()
        try:
            return WriteScope(raw) if raw else WriteScope.FULL_SOFTWARE
        except ValueError:
            return WriteScope.FULL_SOFTWARE

    def _write_scope_value(self) -> str:
        return self._current_write_scope().value

    def _write_scope_short_label(self) -> str:
        return WRITE_SCOPE_SHORT[self._current_write_scope()]

    def _on_snapshot_selection_changed(self) -> None:
        if self._snapshot_sel_guard:
            return
        self._schedule_snapshot_selection_ui()

    def _on_compared_combo_activated(self, _index: int = 0) -> None:
        if self._snapshot_sel_guard:
            return
        name = ""
        if self._widget_is_alive(getattr(self, "_target_combo", None)):
            name = self._target_combo.currentText().strip()
        if name:
            self._select_snapshot_named(name, sync_combo=False, scroll=False)

    def _schedule_snapshot_selection_ui(self) -> None:
        """Paint the row first; sync Compared / Restore after the click returns."""
        if self._snapshot_sel_ui_pending:
            return
        self._snapshot_sel_ui_pending = True
        QTimer.singleShot(0, self._flush_snapshot_selection_ui)

    def _flush_snapshot_selection_ui(self) -> None:
        self._snapshot_sel_ui_pending = False
        if not self._widget_is_alive(getattr(self, "_snapshot_table", None)):
            return
        self._sync_compared_combo_from_table()
        self._refresh_restore_selected_ui()
        name = self._snapshot_name_for_restore()
        self._maybe_align_scan_target_to_snapshot(name)

    def _select_snapshot_row(self, row: int, *, sync_combo: bool = True) -> None:
        if row < 0 or row >= self._snapshot_model.rowCount():
            return
        model = self._snapshot_table.selectionModel()
        already = (
            model is not None
            and model.hasSelection()
            and self._snapshot_table.currentIndex().row() == row
        )
        if not already:
            self._snapshot_sel_guard = True
            try:
                self._snapshot_table.selectRow(row)
            finally:
                self._snapshot_sel_guard = False
        if sync_combo:
            self._schedule_snapshot_selection_ui()
        else:
            self._refresh_restore_selected_ui()

    def _sync_compared_combo_from_table(self) -> None:
        model = self._snapshot_table.selectionModel()
        if model is None:
            return
        rows = model.selectedRows()
        if not rows:
            return
        name = self._snapshot_model.snapshot_name_at(rows[0].row())
        combo = getattr(self, "_target_combo", None)
        if not name or not self._widget_is_alive(combo):
            return
        if combo.currentText().strip() == name:
            return
        combo.blockSignals(True)
        if combo.findText(name) >= 0:
            combo.setCurrentText(name)
        combo.blockSignals(False)

    def _snapshot_name_for_restore(self) -> str | None:
        """Table selection first, then Compared combo (snapshots drawer)."""
        model = self._snapshot_table.selectionModel()
        if model is not None:
            rows = model.selectedRows()
            if rows:
                snap = self._snapshot_at_row(rows[0].row())
                if snap is not None:
                    return snap.name
        name = ""
        if self._widget_is_alive(getattr(self, "_target_combo", None)):
            name = self._target_combo.currentText().strip()
        if name:
            return name
        if self._snapshots:
            return self._snapshots[0].name
        return None

    def _refresh_restore_selected_ui(self) -> None:
        name = self._snapshot_name_for_restore()
        has_archive = bool(name) and self._snapshot_has_archive(name)
        enabled = has_archive and not self._busy and self._target_valid
        if hasattr(self, "_restore_selected_btn"):
            self._restore_selected_btn.setEnabled(enabled)
            if not name:
                self._restore_selected_btn.setToolTip(
                    "Select a snapshot, then restore it onto the machine."
                )
            elif not has_archive:
                self._restore_selected_btn.setToolTip(
                    f"{name} has no archived files. Create a full snapshot first."
                )
        if hasattr(self, "_restore_toolbar_btn"):
            self._restore_toolbar_btn.setEnabled(enabled)
        if hasattr(self, "_restore_selected_action"):
            self._restore_selected_action.setEnabled(enabled)
            self._restore_selected_action.setText("Restore snapshot")
        if hasattr(self, "_restore_selected_software_action"):
            self._restore_selected_software_action.setEnabled(enabled)
        if hasattr(self, "_restore_selected_config_action"):
            self._restore_selected_config_action.setEnabled(enabled)
        if hasattr(self, "_restore_config_only_btn"):
            self._restore_config_only_btn.setEnabled(enabled)
        if hasattr(self, "_restore_selected_binaries_action"):
            self._restore_selected_binaries_action.setEnabled(enabled)

    def _on_restore_selected_clicked(self) -> None:
        name = self._snapshot_name_for_restore()
        if not name:
            self._ensure_snapshots_drawer_visible()
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a snapshot first, then click Restore snapshot.",
            )
            return
        self._confirm_write_snapshot(name, write_scope=WriteScope.FULL_SOFTWARE)

    def _on_restore_selected_config_clicked(self) -> None:
        name = self._snapshot_name_for_restore()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a snapshot first.",
            )
            return
        self._confirm_write_snapshot(name, write_scope=WriteScope.FULL)

    def _on_restore_selected_software_clicked(self) -> None:
        name = self._snapshot_name_for_restore()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a snapshot in the table (or Compared) first.",
            )
            return
        self._confirm_write_snapshot(name, write_scope=WriteScope.FULL_SOFTWARE)

    def _on_restore_selected_binaries_clicked(self) -> None:
        name = self._snapshot_name_for_restore()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Select a snapshot in the table (or Compared) first.",
            )
            return
        self._confirm_write_snapshot(name, write_scope=WriteScope.BINARIES_ONLY)

    def _refresh_revert_ui(self) -> None:
        """Show or hide the rollback banner when an undo point exists."""
        self._service.refresh_rollback_if_stale()
        info = self._service.get_rollback_info()
        available = self._service.rollback_is_available()
        if hasattr(self, "_rollback_banner"):
            if available and info is not None:
                from_name = info.restored_from or "previous snapshot"
                details = self._service.get_rollback_details()
                puts_back = f" — puts back {details.summary()}" if details else ""
                self._rollback_banner_label.setText(
                    f"Undo available — revert to config from before you restored "
                    f"\u00ab{from_name}\u00bb  (saved as {info.snapshot_name})"
                    f"{puts_back}"
                )
                self._rollback_banner.setVisible(True)
            else:
                self._rollback_banner.setVisible(False)
        enabled = available and not self._busy
        if hasattr(self, "_revert_btn"):
            self._revert_btn.setEnabled(enabled)
        if hasattr(self, "_revert_action"):
            self._revert_action.setEnabled(enabled)

    def _on_revert_clicked(self) -> None:
        if self._busy:
            return
        self._confirm_revert_config()

    def _on_clear_error30_clicked(self) -> None:
        if self._busy:
            return
        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter the machine path before clearing ERROR 30 leftovers.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Config Scanner — clear ERROR 30 / 99 trial leftovers",
            "ERROR 30 after a clock rollback is almost never leftover XML. "
            "Official RAM clear does not wipe ruleta\\persistent.\n\n"
            "This will:\n"
            "• Backup then delete RouletteActivate.dat, RouletteStop.flag, "
            "HeapDataFinanceStamps.dat, Password.dat, HeapDataDateTime.dat.\n"
            "• Also remove leftover USB/wrong-WIBU XML only "
            "(6051106A… / 12-12327444) if present.\n"
            "• Keep the live licence (37A55022… / 12-12262688) and licence.dll.\n"
            "• Not restore Tito/WAT or any snapshot.\n\n"
            "After restart expect ERROR 99 with a NEW System ID. "
            "Enter that id's password. Do not restore an expired Activate.dat.\n\n"
            f"Machine: {scan_target}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self._service.clear_error30_leftovers(scan_target)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Config Scanner",
                f"Could not clear ERROR 30 leftovers:\n\n{exc}",
            )
            return
        if removed:
            body = "Removed leftover trial / licence file(s):\n  " + "\n  ".join(removed)
        else:
            body = (
                "No leftover trial tokens or USB/wrong-WIBU licence XML were found. "
                "The live licence was left unchanged."
            )
        plan = plan_stack_restart(scan_target)
        if plan is None:
            QMessageBox.information(self, "Config Scanner", body)
            return
        restart = QMessageBox.question(
            self,
            "Config Scanner",
            body + "\n\nRestart the GoldClub stack now (no config write)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if restart != QMessageBox.StandardButton.Yes:
            return
        self._offer_llave_after_restart = True
        self._set_busy(True, op="stack_restart")
        schedule_stack_restart(self._pool, plan, self._emitter)

    def _on_llave_password_clicked(self) -> None:
        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter the machine path before entering a LLAVE trial password.",
            )
            return
        self._prompt_llave_trial_password(scan_target, after_restart=False)

    def _prompt_llave_trial_password(
        self, scan_target: str, *, after_restart: bool
    ) -> None:
        from config_scanner.llave_bind import (
            format_llave_prompt,
            format_manual_system_id_prompt,
        )

        challenge = self._service.read_llave_challenge(scan_target)
        prompt = format_llave_prompt(challenge)
        if after_restart and challenge is None:
            prompt += (
                "\n\n(Log not updated yet — read CODE on the cabinet, or paste it below.)"
            )
        if challenge is None:
            system_id, ok_id = QInputDialog.getText(
                self,
                "Config Scanner — LLAVE System ID",
                format_manual_system_id_prompt(),
            )
            if not ok_id:
                return
            code = (system_id or "").strip()
            if code:
                prompt += f"\n\nCabinet CODE (for your trial generator):\n  {code}"
        password, ok = QInputDialog.getText(
            self,
            "Config Scanner — LLAVE trial password",
            prompt + "\n\nPaste the vendor trial password:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not (password or "").strip():
            return
        try:
            path = self._service.save_trial_password(scan_target, password.strip())
            ok_run, detail = self._service.run_llave_auto_enter(scan_target)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Config Scanner",
                f"Could not save or auto-enter trial password:\n\n{exc}",
            )
            return
        if ok_run:
            QMessageBox.information(
                self,
                "Config Scanner",
                f"Saved password to {path}\n\n{detail}\n\n"
                "Watch the cabinet for type=\"SUCCEEDED\" and Bets are open.",
            )
        else:
            QMessageBox.warning(
                self,
                "Config Scanner",
                f"Password saved to {path} but auto-type failed:\n\n{detail}\n\n"
                "Type the password on the LLAVE keypad by hand.",
            )

    def _confirm_revert_config(self) -> None:
        info = self._service.get_rollback_info()
        if info is None or not self._service.rollback_is_available():
            QMessageBox.information(
                self,
                "Config Scanner",
                "Nothing to revert.\n\n"
                "A rollback snapshot is created automatically before each bulk restore.",
            )
            self._refresh_revert_ui()
            return

        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "Enter the machine path before reverting config.",
            )
            return

        details = self._service.get_rollback_details()
        puts_back = f"\n\nPuts back:\n  {details.summary()}" if details else ""
        if details is not None and not details.is_self_contained:
            puts_back += (
                "\n  Warning: this undo point has no Ruleta binaries, so the "
                "exe stays on the version running now. Restore a full snapshot "
                "of the version you want if the game must go back too."
            )
        prompt = (
            "Revert this machine to how it was before the last swap?\n\n"
            f"  Was restored: {info.restored_from or '(unknown snapshot)'}\n"
            f"  Undo snapshot: {info.snapshot_name}"
            f"{puts_back}\n"
            f"  Machine: {scan_target}\n\n"
            "Yes will:\n"
            "1. Save a backup of what is running now (config + Ruleta binaries).\n"
            f"2. Restore that undo snapshot ({info.write_scope or 'full'}).\n"
            "\nLive licence XML and serialport maps are never overwritten.\n"
            "Continue?"
        )

        from config_scanner.build_version import normalize_scan_target

        if info.scan_target:
            try:
                saved = normalize_scan_target(info.scan_target).casefold()
                current = normalize_scan_target(scan_target).casefold()
            except ValueError:
                saved = info.scan_target.casefold()
                current = scan_target.casefold()
            if saved and current and saved != current:
                prompt += (
                    "\n\nNote: this rollback was recorded for a different machine path:\n"
                    f"  {info.scan_target}"
                )

        reply = QMessageBox.question(
            self,
            "Config Scanner — revert last restore",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._pending_is_revert = True
        revert_scope = info.write_scope or WriteScope.FULL.value
        self._confirm_write_snapshot(
            info.snapshot_name,
            revert_from=info.restored_from or None,
            write_scope=revert_scope,
        )

    def _refresh_write_action_labels(self) -> None:
        short = self._write_scope_short_label()
        scope = self._current_write_scope()
        if scope is WriteScope.FULL:
            btn = "Restore to machine"
            act = "Restore reference to machine"
            tip = (
                "Save live config as a rollback snapshot, then restore the reference "
                "archive (all config except serialport/ and EGM identity)."
            )
        elif scope is WriteScope.NO_PAYTABLE:
            btn = f"Restore {short} to machine"
            act = f"Restore {short} to machine"
            tip = (
                "Save live config first, then restore all restorable files except "
                "paytable JSON. Use when live Ruleta cannot load the snapshot paytables."
            )
        elif scope is WriteScope.FULL_SOFTWARE:
            btn = f"Restore {short} to machine"
            act = f"Restore {short} to machine"
            tip = (
                "Push the matching Ruleta pack from software_versions first, then "
                "restore config (including paytables) onto that exe. Stops if "
                "the pack is missing or the copy fails."
            )
        elif scope is WriteScope.BINARIES_ONLY:
            btn = f"Restore {short} to machine"
            act = f"Restore {short} to machine"
            tip = (
                "Push matching Ruleta binaries only. This cabinet's setup, "
                "switches, wheel, SAS, serialport, and licence stay in place. "
                "Refuses 10.2.0.684 trial and Downloads 10.2.0.0."
            )
        else:
            btn = f"Restore {short} to machine"
            act = f"Restore {short} to machine"
            tip = (
                f"Save live config first, then restore only {short} files from the "
                "reference snapshot. Serialport/ is never bulk-written."
            )
        if hasattr(self, "_write_baseline_btn"):
            self._write_baseline_btn.setText(btn)
            self._write_baseline_btn.setToolTip(tip)
        self._write_baseline_action.setText(act)
        self._write_baseline_action.setToolTip(tip)
        if hasattr(self, "_restore_selected_btn"):
            self._restore_selected_btn.setText("Restore to machine")
        self._refresh_restore_selected_ui()

    def _on_write_scope_changed(self, _index: int = 0) -> None:
        self._refresh_write_action_labels()
        if self._last_compare_result is not None:
            self._rebuild_changes_panel(self._last_compare_result)

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
            empty = QLabel(
                "<p style='font-size:15px; margin:24px 12px 12px 12px;'>"
                "<b>Config Scanner</b> — save a machine, put it back later."
                "</p>"
                "<ol style='margin:8px 12px 8px 28px; color:#cccccc; line-height:1.5;'>"
                "<li>Set the <b>Machine</b> path (or Auto-detect)</li>"
                "<li>Click <b>Create full snapshot</b> — saves config "
                "<i>and</i> Ruleta software</li>"
                "<li>To go back: select that snapshot and click "
                "<b>Restore snapshot</b> (live config is saved first so you can undo)</li>"
                "</ol>"
                "<p style='margin:12px 12px; color:gray;'>"
                "Compare and config-only restore stay under <b>More</b>. "
                "Serialport maps and the live licence file are never overwritten."
                "</p>"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            empty.setWordWrap(True)
            empty.setTextFormat(Qt.TextFormat.RichText)
            self._changes_layout.addWidget(empty)
            self._changes_layout.addStretch(1)
            self._changes_scroll.verticalScrollBar().setValue(0)
            return

        needle = self._changes_find_edit.text().strip()
        panel_diffs = self._file_diffs_for_changes_panel(result)
        changed, omitted_files = compare_panel_file_slice(panel_diffs)
        styles = self._compare_panel_styles()
        show_write = self._write_baseline_btn.isEnabled() or (
            self._last_compare_result is not None
            and self._snapshot_has_archive(result.baseline_snapshot)
        )

        if not changed and omitted_files == 0:
            if needle:
                self._add_changes_panel_note(f'(no matches for "{needle}")')
            else:
                self._add_compare_summary_strip(
                    text=format_compare_panel_header(
                        result.baseline_snapshot,
                        result.target_snapshot,
                        panel_diffs,
                        session_reference=self._session_baseline_name,
                    ),
                    styles=styles,
                    tooltip=format_compare_summary(
                        result.baseline_snapshot,
                        result.target_snapshot,
                        result.summary,
                        result.warnings or (),
                    ),
                    show_write_baseline=show_write,
                )
                self._add_changes_panel_note("Configs match — no setting differences in this compare.")
            self._changes_layout.addStretch(1)
            self._changes_scroll.verticalScrollBar().setValue(0)
            return

        self._add_compare_summary_strip(
            text=format_compare_panel_header(
                result.baseline_snapshot,
                result.target_snapshot,
                changed,
                session_reference=self._session_baseline_name,
            ),
            styles=styles,
            tooltip=format_compare_summary(
                result.baseline_snapshot,
                result.target_snapshot,
                result.summary,
                result.warnings or (),
            ),
            show_write_baseline=show_write,
        )

        if needle and omitted_files:
            self._add_truncation_report_row(
                f'Find "{needle}": showing {len(changed)} matching file(s); '
                f"{omitted_files} more omitted."
            )
        elif needle:
            self._add_changes_panel_note(
                f'Find "{needle}": showing {len(changed)} matching file(s)'
            )
        elif omitted_files:
            self._add_truncation_report_row(
                f"Showing the first {len(changed)} changed file(s); "
                f"{omitted_files} more omitted."
            )

        legend_bits: list[str] = []
        if any(is_encrypted_origin_config_path(fd.relative_path) for fd in changed):
            legend_bits.append("Amber = encrypted ruleta setup.xml")
        if any(is_protected_write_path(fd.relative_path) for fd in changed):
            legend_bits.append("Gray = protected (licences kept if present)")
        if legend_bits:
            legend = QLabel(" · ".join(legend_bits))
            legend.setStyleSheet(styles["legend"])
            legend.setWordWrap(True)
            self._changes_layout.addWidget(legend)

        baseline_name = result.baseline_snapshot
        target_name = result.target_snapshot
        collapse = len(changed) > 2
        for index, file_diff in enumerate(changed):
            start_expanded = (not collapse or index == 0) and not (
                collapse and is_protected_write_path(file_diff.relative_path)
            )
            self._changes_layout.addWidget(
                self._make_file_section(
                    file_diff,
                    baseline_name=baseline_name,
                    target_name=target_name,
                    styles=styles,
                    collapse=collapse,
                    start_expanded=start_expanded,
                )
            )

        self._changes_layout.addStretch(1)
        self._changes_scroll.verticalScrollBar().setValue(0)
        self._changes_content.update()
        self._changes_scroll.update()

    def _apply_compare_result_ui(self, result: CompareResult) -> None:
        self._last_report_path = result.report_path
        self._last_compare_target_snapshot = result.target_snapshot
        self._last_compare_result = result
        self._open_report_action.setEnabled(True)
        has_archive = self._snapshot_has_archive(result.baseline_snapshot)
        enabled = has_archive and not self._busy
        self._write_baseline_btn.setEnabled(enabled)
        self._write_baseline_action.setEnabled(enabled)
        if not has_archive:
            tip = "Baseline snapshot has no archived files — re-scan to capture content first."
        else:
            tip = (
                f"Save live config, then switch the machine to {result.baseline_snapshot}. "
                "Write another snapshot back anytime to switch again."
            )
        self._write_baseline_btn.setToolTip(tip)
        self._write_baseline_action.setToolTip(tip)
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
        if is_structural_item_change(change):
            QMessageBox.information(
                self,
                "Config Scanner",
                "This is an entire HW driver entry add/remove, not a single field.\n\n"
                "Restore configuration.xml with Write file from a snapshot/backup.\n"
                "Other drivers were not renamed — only list indices shifted.",
            )
            return
        if is_protected_write_path(relative_path):
            reason = protected_write_block_reason(relative_path) or (
                "This path is protected from Config Scanner writes."
            )
            QMessageBox.warning(self, "Config Scanner", reason)
            return
        if is_protected_identity_field(change.path, relative_path):
            QMessageBox.warning(
                self,
                "Config Scanner",
                protected_identity_field_reason(change.path),
            )
            return
        if not is_actionable_content_change(change, relative_path=relative_path):
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

    def _confirm_write_archived_file(self, relative_path: str, snapshot_name: str) -> None:
        if self._busy:
            return
        if is_protected_write_path(relative_path) and not is_licence_path(relative_path):
            reason = protected_write_block_reason(relative_path) or (
                "This path is protected from Config Scanner writes."
            )
            QMessageBox.warning(self, "Config Scanner", reason)
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
        if is_licence_path(relative_path):
            prompt += (
                "\n\nThis is a licence file. Config Scanner never overwrites a "
                "live licence. A different dongle is never written."
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
        tip = (
            "Config SHA1 Scanner — snapshot QA config from a local slot/roulette image "
            "or a remote lab cabinet (e.g. \\\\10.0.0.90\\c$\\Goldclub). "
            "Requires real binaries (OneHand.exe / Ruleta.exe).\n\n"
            f"Data folder: {data_root}\n"
            f"Snapshots: {snap_dir}"
        )
        if hasattr(self, "_drive_edit"):
            self._drive_edit.setToolTip(tip)

    def _apply_detect_result(self, result: object) -> None:
        from config_scanner.build_version import DiscoverResult

        if not isinstance(result, DiscoverResult):
            return
        cleaned = result.target.rstrip("\\")
        self._apply_scan_target(cleaned)

    def _apply_scan_target(
        self, target: str, status_note: str | None = None
    ) -> None:
        cleaned = (target or "").strip().rstrip("\\")
        if not cleaned:
            return
        self._drive_edit.blockSignals(True)
        self._drive_edit.setText(cleaned)
        self._drive_edit.blockSignals(False)
        self._persist_drive()
        self._target_valid = True
        self._validate_pending = cleaned
        self._update_scan_status_ui()
        self._refresh_action_enabled()
        self._refresh_live_ruleta_version()
        if status_note:
            self._append_status(status_note, force=True)

    def _refresh_live_ruleta_version(self) -> None:
        """Show the live Ruleta.exe ProductVersion for the current scan target."""
        if not hasattr(self, "_live_sw_label"):
            return
        target = self._drive_edit.text().strip()
        version = None
        if self._target_valid and target:
            try:
                version = live_ruleta_exe_version_for_target(target)
            except OSError:
                version = None
        self._live_sw_label.setText(format_live_ruleta_sw_banner(version))
        self._live_sw_label.setToolTip(
            "ProductVersion of live ruleta\\Ruleta.exe on this scan target.\n"
            f"Target: {target or '(none)'}"
        )

    def _maybe_align_scan_target_to_snapshot(self, snapshot_name: str | None) -> None:
        if not snapshot_name or self._busy:
            return
        scan_target = self._drive_edit.text().strip()
        if not scan_target:
            return
        resolved, note = self._service.resolve_restore_scan_target(
            snapshot_name, scan_target
        )
        if resolved.casefold().rstrip("\\") == scan_target.casefold().rstrip("\\"):
            return
        self._apply_scan_target(resolved, note)

    def _apply_snapshot_list(
        self,
        snapshots: list[SnapshotInfo] | None = None,
        *,
        preserve_combo_selection: bool = True,
    ) -> None:
        selected_name = None
        model = self._snapshot_table.selectionModel()
        if model is not None:
            selected = model.selectedRows()
            if selected:
                selected_name = self._snapshot_model.snapshot_name_at(selected[0].row())
        rows = snapshots if snapshots is not None else self._service.list_snapshots()
        rows = order_snapshots_newest_first(
            rows, pin_name=self._last_scan_snapshot_name
        )
        self._snapshots = rows
        self._snapshot_model.set_rows(rows)
        QTimer.singleShot(0, lambda: resize_snapshot_table_columns_to_contents(self._snapshot_table))
        self._populate_combos(
            preserve_selection=preserve_combo_selection,
            compared_name=selected_name,
        )
        if selected_name:
            self._select_snapshot_named(selected_name, scroll=False)
        self._refresh_revert_ui()
        self._refresh_restore_selected_ui()

    def _snapshot_profile_for(self, name: str) -> tuple[str | None, str | None]:
        for row in self._snapshots:
            if row.name == name:
                return row.profile_id, row.profile_label
        return None, None

    def _snapshot_has_archive(self, snapshot_name: str) -> bool:
        for row in self._snapshots:
            if row.name == snapshot_name:
                return row.has_archive
        snap_dir = self._service.get_snapshots_dir() / snapshot_name
        return snapshot_content_root(snap_dir) is not None

    def _snapshot_at_row(self, row: int) -> SnapshotInfo | None:
        if 0 <= row < len(self._snapshots):
            return self._snapshots[row]
        return None

    def _latest_snapshot_name_excluding(self, *exclude: str) -> str | None:
        excluded = {name for name in exclude if name}
        for row in self._snapshots:
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
        row = snapshot_table_row_from_pos(self._snapshot_table, pos)
        if row < 0:
            return
        snapshot = self._snapshot_at_row(row)
        if snapshot is None:
            return
        self._select_snapshot_row(row)

        menu = QMenu(self)
        registered_baseline = self._service.get_baseline_name()
        is_baseline_row = snapshot.is_baseline or snapshot.name == registered_baseline
        has_archive = self._snapshot_has_archive(snapshot.name)
        if not has_archive:
            restore_tip = "Re-scan to capture archived files"
        else:
            restore_tip = (
                "Save live config first, then restore config + matching Ruleta "
                "software. WIBU licence is kept; LLAVE trial bind is restored "
                "from this snapshot when it was captured."
            )
        restore_action = QAction(snapshot_context_primary_restore_label(), self)
        restore_action.setEnabled(has_archive)
        restore_action.setToolTip(restore_tip)
        restore_action.triggered.connect(
            lambda checked=False, name=snapshot.name: self._confirm_write_snapshot(
                name, write_scope=WriteScope.FULL_SOFTWARE
            )
        )
        menu.addAction(restore_action)
        if not has_archive:
            config_tip = "Re-scan to capture archived files"
        else:
            config_tip = (
                "Save live config first, then restore config only. "
                "Live Ruleta.exe stays as-is. Kill-All still runs first."
            )
        config_action = QAction(
            _SNAPSHOT_CONTEXT_RESTORE_LABELS[WriteScope.FULL], self
        )
        config_action.setEnabled(has_archive)
        config_action.setToolTip(config_tip)
        config_action.triggered.connect(
            lambda checked=False, name=snapshot.name: self._confirm_write_snapshot(
                name, write_scope=WriteScope.FULL
            )
        )
        menu.addAction(config_action)
        menu.addSeparator()
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
        baseline_action = QAction("Set as reference baseline", self)
        baseline_action.triggered.connect(self._on_set_baseline_clicked)
        menu.addAction(baseline_action)
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

    def _confirm_write_snapshot(
        self,
        snapshot_name: str,
        *,
        revert_from: str | None = None,
        write_scope: WriteScope | str | None = None,
    ) -> None:
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

        resolved_target, switch_note = self._service.resolve_restore_scan_target(
            snapshot_name, scan_target
        )
        if (
            resolved_target.casefold().rstrip("\\")
            != scan_target.casefold().rstrip("\\")
        ):
            self._apply_scan_target(resolved_target, switch_note)
            scan_target = resolved_target

        if write_scope is None:
            scope = WriteScope.FULL if revert_from else WriteScope.FULL_SOFTWARE
        elif isinstance(write_scope, WriteScope):
            scope = write_scope
        else:
            try:
                scope = WriteScope(write_scope)
            except ValueError:
                scope = (
                    WriteScope.FULL if revert_from else WriteScope.FULL_SOFTWARE
                )
        scope_label = WRITE_SCOPE_LABELS[scope]
        scoped_count = self._service.count_scoped_snapshot_files(
            snapshot_name, scope.value
        )
        if scoped_count <= 0 and scope is not WriteScope.BINARIES_ONLY:
            QMessageBox.warning(
                self,
                "Config Scanner",
                f"No files in this snapshot match write scope:\n{scope_label}\n\n"
                "Pick Full (HW + SW) or another scope, or re-scan.",
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

        plan = plan_stack_restart(scan_target)
        self._pending_stack_plan = plan
        self._pending_stack_phase = None
        self._stack_killed_ok = False
        self._pending_restore_title = None
        self._pending_restore_body = None

        if revert_from:
            prompt = ""
        else:
            if scope is WriteScope.BINARIES_ONLY:
                step_two = (
                    "Push Ruleta binaries from this snapshot and keep this "
                    "cabinet's setup / SAS / licence."
                )
            else:
                step_two = (
                    f"Restore {WRITE_SCOPE_SHORT[scope]} from that snapshot."
                )
            prompt = (
                "Swap this machine to:\n\n"
                f"  {snapshot_name}\n"
                f"  Version: {version_line}\n"
                f"  {scope_label} ({scoped_count} of {snapshot.file_count} files)\n"
                f"  Profile: {profile_line}\n"
                f"  Machine: {scan_target}\n\n"
                "Yes will:\n"
                "1. Save a backup of what is running now (config + Ruleta "
                "binaries) so you can revert.\n"
                f"2. {step_two}\n"
            )
            if plan is not None:
                if self._auto_start_stack_enabled():
                    start_bit = (
                        " then start it again (Auto-start stack is on)."
                    )
                else:
                    start_bit = (
                        " Auto-start stack is off, so the game stays stopped."
                    )
                prompt += (
                    "3. Stop the GoldClub stack (Kill-All) before writing;"
                    f"{start_bit}\n"
                )
            else:
                prompt += (
                    "3. Offline disk: config is written only (no Kill-All).\n"
                )
            prompt += (
                "\nLive licence XML and serialport maps are never overwritten.\n"
                "Continue?"
            )

        warnings = self._service.snapshot_apply_warnings(
            snapshot_name, scan_target, write_scope=scope.value
        )
        refuses = self._service.snapshot_apply_refuses(
            snapshot_name,
            scan_target,
            write_scope=scope.value,
            defer_lock_check=plan is not None,
        )
        if warnings:
            prompt += "\n\n" + "\n".join(f"• {item}" for item in warnings)
        if refuses:
            QMessageBox.critical(
                self,
                "Config Scanner — restore blocked",
                "This restore cannot run until the runtime / build issues "
                "below are fixed:\n\n" + "\n\n".join(refuses),
            )
            return

        if not revert_from:
            reply = QMessageBox.question(
                self,
                "Config Scanner — confirm restore",
                prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._pending_stack_plan = None
                return

        # Preserve live config *and* the live Ruleta binaries, so the undo point
        # puts the machine back without depending on a software_versions pack.
        self._pending_scan_and_compare = False
        self._pending_create_snapshot = False
        self._pending_include_software = True
        self._pending_write_snapshot = snapshot_name
        self._pending_write_scope = scope.value
        self._presave_snapshot_for_write = None
        if revert_from:
            self._pending_is_revert = True

        self._append_status(
            f"Saving live config, then restoring {snapshot_name} …",
            force=True,
        )
        self._set_busy(True, op="scan")
        schedule_prepare_scan_target(
            self._pool,
            self._service,
            scan_target,
            self._emitter,
        )

    def _clear_pending_write_state(self) -> None:
        self._pending_write_snapshot = None
        self._pending_write_scope = WriteScope.FULL.value
        self._pending_include_software = False
        self._presave_snapshot_for_write = None
        self._pending_is_revert = False
        self._pending_stack_plan = None
        self._pending_stack_phase = None
        self._stack_killed_ok = False
        self._pending_restore_title = None
        self._pending_restore_body = None

    def _auto_start_stack_enabled(self) -> bool:
        box = getattr(self, "_auto_start_stack_cb", None)
        if self._widget_is_alive(box):
            return bool(box.isChecked())
        return SettingsManager.get_config_scanner_auto_start_stack()

    def _on_start_stack_clicked(self) -> None:
        if self._busy:
            return
        scan_target = self._drive_edit.text().strip()
        plan = plan_stack_restart(scan_target)
        if plan is None:
            QMessageBox.information(
                self,
                "Config Scanner",
                "No GoldClub stack scripts for this machine path "
                "(offline disk skips Kill-All / Run-FullStack).",
            )
            return
        self._pending_stack_plan = plan
        self._pending_stack_phase = "start"
        self._append_status("Starting GoldClub stack …", force=True)
        self._set_busy(True, op="stack_restart")
        schedule_stack_restart(self._pool, plan, self._emitter, phase="start")

    def _offer_stack_start_after_write(self, title: str, body: str) -> None:
        """Ask to Run-FullStack after a write that already did Kill-All."""
        plan = self._pending_stack_plan or plan_stack_restart(
            self._drive_edit.text().strip()
        )
        if plan is None:
            QMessageBox.information(self, title, body)
            return
        reply = QMessageBox.question(
            self,
            title,
            body
            + "\n\nStack is down (Kill-All). Auto-start stack is off.\n\n"
            "Start full stack now (Run-FullStack)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._pending_stack_plan = None
            self._stack_killed_ok = False
            return
        self._pending_restore_title = title
        self._pending_restore_body = body
        self._pending_stack_plan = plan
        self._pending_stack_phase = "start"
        self._append_status("Starting GoldClub stack …", force=True)
        self._set_busy(True, op="stack_restart")
        schedule_stack_restart(self._pool, plan, self._emitter, phase="start")

    def _on_stack_restart_finished(self, ok: bool, detail: str) -> None:
        phase = self._pending_stack_phase
        self._pending_stack_phase = None
        if phase == "kill":
            self._stack_killed_ok = ok
            if ok:
                self._append_status("OK: " + detail, force=True)
                from config_scanner.build_version import scan_target_path
                from network.ruleta_stack_probe import verify_stack_clear_for_swap

                target = self._drive_edit.text().strip()
                plan = self._pending_stack_plan
                host = (plan.host if plan is not None else None) or None
                dest_ruleta = scan_target_path(target) / "ruleta"
                clear, block_detail = verify_stack_clear_for_swap(host, dest_ruleta)
                if not clear:
                    self._clear_pending_write_state()
                    self._set_busy(False)
                    self._append_status("FAIL: " + block_detail, force=True)
                    hint = ""
                    if host:
                        from automation.cabinet_elevate import bootstrap_hint

                        hint = "\n\n" + bootstrap_hint(host)
                    QMessageBox.critical(
                        self,
                        "Config Scanner — restore blocked",
                        "Kill-All ran but Ruleta files are still locked, so "
                        "software restore cannot continue.\n\n"
                        f"{block_detail}{hint}",
                    )
                    return
            else:
                self._clear_pending_write_state()
                self._set_busy(False)
                self._append_status("FAIL: " + detail, force=True)
                hint = ""
                plan = self._pending_stack_plan
                host = (plan.host if plan is not None else None) or None
                if host:
                    from automation.cabinet_elevate import bootstrap_hint

                    hint = "\n\n" + bootstrap_hint(host)
                elif "GCI-ELEVATE-NOW" not in detail:
                    hint = (
                        "\n\nStop the GoldClub stack on the cabinet, then retry "
                        "Restore to machine."
                    )
                QMessageBox.critical(
                    self,
                    "Config Scanner — restore blocked",
                    "GoldClub stack could not be stopped on the cabinet, so "
                    "software files would stay locked during restore.\n\n"
                    f"{detail}{hint}",
                )
                return
            target = self._drive_edit.text().strip()
            self._set_busy(True, op="scan")
            schedule_scan(
                self._pool,
                self._service,
                target,
                self._emitter,
                include_software=self._pending_include_software,
            )
            return
        if phase == "start":
            self._set_busy(False)
            self._append_status(("OK: " if ok else "FAIL: ") + detail, force=True)
            body = self._pending_restore_body or detail
            title = self._pending_restore_title or "Config Scanner — restore complete"
            offer_llave = self._offer_llave_after_restart
            self._pending_restore_body = None
            self._pending_restore_title = None
            self._pending_stack_plan = None
            self._stack_killed_ok = False
            self._offer_llave_after_restart = False
            if ok:
                self._refresh_live_ruleta_version()
                QMessageBox.information(
                    self,
                    title,
                    f"{body}\n\nStack restarted: {detail}",
                )
                if offer_llave:
                    scan_target = self._drive_edit.text().strip()
                    if scan_target:
                        self._prompt_llave_trial_password(scan_target, after_restart=True)
            else:
                up_hint = ""
                if "ruleta running" in detail.casefold():
                    up_hint = (
                        "\n\nRuleta may already be up — check the cabinet screen. "
                        "ERROR 99 after a version transfer is normal once; use "
                        "More → Enter trial password (LLAVE)."
                    )
                QMessageBox.warning(
                    self,
                    title,
                    f"{body}\n\nStack start failed: {detail}{up_hint}",
                )
            return
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + detail, force=True)
        if ok:
            QMessageBox.information(
                self,
                "Config Scanner — stack restarted",
                f"{detail}\n\nThe game should reload with the restored config.",
            )
            if self._offer_llave_after_restart:
                self._offer_llave_after_restart = False
                scan_target = self._drive_edit.text().strip()
                if scan_target:
                    self._prompt_llave_trial_password(scan_target, after_restart=True)
            return
        QMessageBox.warning(
            self,
            "Config Scanner",
            f"Stack restart failed.\n\n{detail}\n\n"
            "Try Run-FullStack.bat from the USB stick, or reboot only if the game "
            "still shows old settings.",
        )

    def _on_apply_finished(self, ok: bool, result: object, message: str) -> None:
        wrote = self._pending_write_snapshot
        wrote_scope = self._pending_write_scope
        presave = self._presave_snapshot_for_write
        was_revert = self._pending_is_revert
        will_autostart = bool(
            ok
            and wrote
            and should_autostart_after_write(
                auto_start=self._auto_start_stack_enabled(),
                stack_killed=self._stack_killed_ok,
                has_plan=self._pending_stack_plan is not None,
            )
        )
        # Snapshot name / write scope still describe this restore. Compute
        # start-phase kwargs now — clearing them first dropped the snapshot name.
        start_kwargs = (
            self._stack_trial_kwargs(phase="start") if will_autostart else None
        )
        self._pending_write_snapshot = None
        self._pending_write_scope = WriteScope.FULL.value
        self._presave_snapshot_for_write = None
        self._pending_is_revert = False
        if not will_autostart:
            self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        self._refresh_live_ruleta_version()
        if ok:
            self.refresh_snapshots()
            body = message
            if wrote:
                try:
                    scope_e = WriteScope(wrote_scope)
                    scope_txt = WRITE_SCOPE_SHORT[scope_e]
                except ValueError:
                    scope_txt = wrote_scope or "config"
                if was_revert:
                    body = (
                        f"Reverted to config from:\n{wrote}\n\n"
                        f"{message}"
                    )
                    if presave and presave != wrote:
                        body += (
                            f"\n\nConfig before this revert was saved as:\n{presave}\n\n"
                            "Use Revert last restore again if you need to undo the revert."
                        )
                        self._service.record_rollback_snapshot(
                            snapshot_name=presave,
                            restored_from=wrote,
                            write_scope=wrote_scope,
                            scan_target=self._drive_edit.text().strip(),
                        )
                else:
                    body = f"Restored {scope_txt} from:\n{wrote}"
                    if presave and presave != wrote:
                        body += (
                            f"\n\nLive config before restore was saved as:\n{presave}\n\n"
                            "Use Revert last restore if you need to undo."
                        )
                        self._service.record_rollback_snapshot(
                            snapshot_name=presave,
                            restored_from=wrote,
                            write_scope=wrote_scope,
                            scan_target=self._drive_edit.text().strip(),
                        )
                    else:
                        body += (
                            "\n\nOther snapshots remain on disk — "
                            "restore any of them to roll back or switch configs."
                        )
                self._refresh_revert_ui()
            title = (
                "Config Scanner — revert complete"
                if was_revert
                else "Config Scanner — restore complete"
            )
            if wrote and self._pending_stack_plan is not None and not self._stack_killed_ok:
                body += (
                    "\n\nThe GoldClub stack was not stopped (Kill-All failed). "
                    "Config was still written. Run FIX-ERROR30-LOOP.cmd from "
                    "GoldClub Admin Shell, or reboot, if settings do not appear."
                )
            if wrote and self._stack_killed_ok and self._pending_stack_plan is not None:
                if will_autostart:
                    self._pending_restore_title = title
                    self._pending_restore_body = body
                    if message:
                        self._pending_restore_body += f"\n\n{message}"
                    self._pending_stack_phase = "start"
                    self._append_status("Starting GoldClub stack …", force=True)
                    self._set_busy(True, op="stack_restart")
                    schedule_stack_restart(
                        self._pool,
                        self._pending_stack_plan,
                        self._emitter,
                        phase="start",
                        **(start_kwargs or {}),
                    )
                    return
                self._offer_stack_start_after_write(title, body)
                return
            QMessageBox.information(self, title, body)
            return
        self._pending_stack_plan = None
        self._stack_killed_ok = False
        QMessageBox.critical(self, "Config Scanner", f"Restore failed:\n\n{message}")

    def _on_delete_finished(self, ok: bool, result: object, message: str) -> None:
        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok:
            if isinstance(result, str):
                rollback = self._service.get_rollback_info()
                if rollback is not None and rollback.snapshot_name == result:
                    self._service.clear_rollback_snapshot()
            if isinstance(result, str) and self._session_baseline_name == result:
                self._session_baseline_name = None
            if isinstance(result, str) and self._service.get_baseline_name() is None:
                self._write_baseline_btn.setVisible(False)
                self._write_baseline_btn.setEnabled(False)
                self._write_baseline_action.setEnabled(False)
            self.refresh_snapshots()
            self._refresh_revert_ui()
            return
        QMessageBox.critical(self, "Config Scanner", f"Delete failed:\n\n{message}")

    def _software_swapping_scope(self) -> bool:
        """True when the pending restore replaces the Ruleta binaries."""
        try:
            scope = WriteScope(self._pending_write_scope)
        except ValueError:
            return False
        return scope in (WriteScope.FULL_SOFTWARE, WriteScope.BINARIES_ONLY)

    def _remote_software_transfer(self) -> bool:
        """Remote restore that swaps Ruleta binaries (needs clock + LLAVE pipeline)."""
        if not self._software_swapping_scope():
            return False
        plan = self._pending_stack_plan
        return plan is not None and getattr(plan, "mode", None) == "remote"

    def _stack_trial_kwargs(self, *, phase: str) -> dict:
        seamless = self._remote_software_transfer()
        snap = self._pending_write_snapshot or ""
        kwargs: dict = {
            "service": self._service,
            "scan_target": self._drive_edit.text().strip(),
            "snapshot_name": snap,
        }
        if phase == "kill":
            kwargs["trial_prep"] = seamless
        if phase == "start":
            from config_scanner.paths import snapshots_path
            from roulette_trial import snapshot_should_auto_enter_llave

            snap_dir = None
            if snap and self._service is not None:
                snap_dir = snapshots_path(self._service.root) / snap
            kwargs["ensure_llave"] = seamless and snapshot_should_auto_enter_llave(
                snap_dir
            )
        return kwargs

    def _confirm_incomplete_undo_point(self, result: ScanResult) -> bool:
        """Ask before swapping binaries when the undo point cannot put them back.

        The pre-restore snapshot is taken with software capture on. If that
        capture failed, reverting would leave the machine on the new exe.
        """
        if result.software_captured or not self._software_swapping_scope():
            return True
        reply = QMessageBox.question(
            self,
            "Config Scanner — undo point is incomplete",
            "Live config was saved, but the Ruleta binaries running now could "
            "not be copied into the undo point:\n\n"
            f"  {result.snapshot_name}\n\n"
            "If you continue, Revert last restore puts the config back but "
            "leaves the exe on the version you are about to install.\n\n"
            "The GoldClub stack is already stopped. Choose No to stop here and "
            "start it again with Start GoldClub stack.\n\n"
            "Install the new Ruleta software anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            return True
        self._append_status(
            "Restore cancelled — undo point has no Ruleta binaries.", force=True
        )
        return False

    def _on_scan_finished(self, ok: bool, result: object, message: str) -> None:
        pending_write = self._pending_write_snapshot
        if pending_write:
            self._pending_scan_and_compare = False
            if not ok or not isinstance(result, ScanResult):
                self._clear_pending_write_state()
                self._set_busy(False)
                self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
                QMessageBox.critical(
                    self,
                    "Config Scanner",
                    "Could not save live config before restore.\n\n"
                    f"{message}",
                )
                return
            self._last_scan_snapshot_name = result.snapshot_name
            if result.manifest_file_count == 0:
                self._clear_pending_write_state()
                self._set_busy(False)
                self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
                self.refresh_snapshots()
                QMessageBox.warning(
                    self,
                    "Config Scanner",
                    "Scan finished but found 0 config files — switch aborted.\n\n"
                    f"Scan target: {self._drive_edit.text().strip()}",
                )
                return
            for note in result.warnings:
                self._append_status(f"WARN: {note}", force=True)
            if not self._confirm_incomplete_undo_point(result):
                self._clear_pending_write_state()
                self._set_busy(False)
                self.refresh_snapshots()
                self._refresh_revert_ui()
                return
            self._presave_snapshot_for_write = result.snapshot_name
            try:
                trial_notes = self._service.capture_rollback_trial_bind(
                    result.snapshot_name,
                    self._drive_edit.text().strip(),
                )
                for note in trial_notes:
                    self._append_status(f"rollback trial: {note}", force=True)
            except Exception as exc:
                self._append_status(
                    f"WARN: could not capture LLAVE trial bind for revert: {exc}",
                    force=True,
                )
            scope_txt = WRITE_SCOPE_SHORT.get(
                WriteScope(self._pending_write_scope), self._pending_write_scope
            )
            self._append_status(
                f"OK: Saved live as {result.snapshot_name}; "
                f"writing {scope_txt} from {pending_write} …",
                force=True,
            )
            # Stay busy: restore archived files from the chosen snapshot.
            self._set_busy(True)
            schedule_apply_snapshot(
                self._pool,
                self._service,
                pending_write,
                self._drive_edit.text().strip(),
                self._emitter,
                write_scope=self._pending_write_scope,
                is_revert=bool(self._pending_is_revert),
            )
            self.refresh_snapshots()
            return

        self._set_busy(False)
        self._append_status(("OK: " if ok else "FAIL: ") + message, force=True)
        if ok and isinstance(result, ScanResult):
            self._last_scan_snapshot_name = result.snapshot_name
            self._last_scan_file_count = result.manifest_file_count
            self._last_scan_software_count = result.software_file_count
            self._last_scan_software_ok = result.software_captured
            from config_scanner.software_compat import scan_user_warnings

            self._last_scan_user_warnings = scan_user_warnings(result.warnings)
        self._update_scan_status_ui()
        if ok and isinstance(result, ScanResult):
            if result.manifest_file_count == 0:
                self._pending_scan_and_compare = False
                self._pending_create_snapshot = False
                self.refresh_snapshots()
                QMessageBox.warning(
                    self,
                    "Config Scanner",
                    "Scan finished but found 0 config files.\n\n"
                    f"Scan target: {self._drive_edit.text().strip()}\n\n"
                    "Use the game root (e.g. \\\\10.0.0.90\\c$\\Goldclub or D:), "
                    "not the ConfigScanner tools folder. Confirm config\\ exists under that root.",
                )
            else:
                if result.software_captured:
                    self._append_status(
                        f"OK: Saved {result.software_file_count} Ruleta software files "
                        "with this snapshot.",
                        force=True,
                    )
                for note in self._last_scan_user_warnings:
                    self._append_status(f"WARN: {note}", force=True)
                # Create-full-snapshot already shows one success box; do not
                # also pop a yellow warning for leftover notes.
                if self._last_scan_user_warnings and not self._pending_create_snapshot:
                    QMessageBox.warning(
                        self,
                        "Config Scanner",
                        "Scan completed with warnings:\n\n"
                        + "\n".join(f"• {n}" for n in self._last_scan_user_warnings[:8]),
                    )
                # Refresh list; _on_snapshots_loaded starts the compare when pending.
                self.refresh_snapshots()
        elif not ok:
            self._pending_scan_and_compare = False
            self._pending_create_snapshot = False
            self._last_scan_user_warnings = ()
            QMessageBox.critical(self, "Config Scanner", message)

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
            return self._snapshots[0].name
        return None

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802
        table = getattr(self, "_snapshot_table", None)
        if table is not None and obj is table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                button = getattr(event, "button", None)
                if button is None or button() == Qt.MouseButton.LeftButton:
                    pos = (
                        event.position().toPoint()
                        if hasattr(event, "position")
                        else event.pos()
                    )
                    # Let Qt handle a clean indexAt hit. Only correct misses —
                    # a second selectRow on the same click is the lag.
                    if not table.indexAt(pos).isValid():
                        row = snapshot_table_row_from_pos(table, pos)
                        if row >= 0:
                            self._select_snapshot_row(row)
                            return True
            return False
        if obj is table and event.type() == QEvent.Type.KeyPress:
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
        self._refresh_live_ruleta_version()
        if self._pending_write_snapshot and self._pending_stack_plan is not None:
            self._pending_stack_phase = "kill"
            self._set_busy(True, op="stack_kill")
            self._append_status("Stopping GoldClub stack before restore …", force=True)
            schedule_stack_restart(
                self._pool,
                self._pending_stack_plan,
                self._emitter,
                phase="kill",
                **self._stack_trial_kwargs(phase="kill"),
            )
            return
        if self._busy_op != "scan":
            self._set_busy(True, op="scan")
        else:
            self._update_scan_status_ui()
        schedule_scan(
            self._pool,
            self._service,
            target,
            self._emitter,
            include_software=self._pending_include_software,
        )

    def _on_scan_target_failed(self, message: str) -> None:
        self._clear_pending_write_state()
        self._pending_scan_and_compare = False
        self._pending_create_snapshot = False
        self._pending_include_software = False
        self._set_busy(False)
        self._refresh_live_ruleta_version()
        QMessageBox.warning(
            self,
            "Config Scanner",
            "Could not find a slot or roulette game folder.\n\n"
            f"{message}\n\n"
            "Set Scan target to the game root (e.g. \\\\10.0.0.90\\c$\\Goldclub or D:) "
            "and try again, or click Auto-detect.",
        )


    def _apply_snapshot_table_column_widths(self) -> None:
        """Fit snapshot table columns to their content."""
        resize_snapshot_table_columns_to_contents(self._snapshot_table)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)

    def refresh_snapshots(self) -> None:
        schedule_load_snapshots(self._pool, self._service, self._emitter)

    def _persist_drive(self) -> None:
        SettingsManager.set_config_scanner_game_drive(self._drive_edit.text().strip())

    def _set_scan_progress_active(self, active: bool) -> None:
        """Toggle indeterminate animation without changing layout geometry."""
        from gui.thin_progress import set_thin_busy_progress_active

        set_thin_busy_progress_active(self._scan_progress, active)

    def _set_busy(self, busy: bool, op: str | None = None) -> None:
        self._busy = busy
        self._busy_op = op if busy else None
        if busy and op == "scan":
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Scanning…")
        elif busy and op == "compare":
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Comparing…")
        elif busy and op == "stack_restart":
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Restarting stack…")
        elif busy and op == "stack_kill":
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Stopping stack…")
        elif busy:
            self._set_scan_progress_active(True)
            self._scan_btn.setText("Scan && compare")
        else:
            self._set_scan_progress_active(False)
            self._scan_btn.setText("Scan && compare")
        self._update_scan_status_ui()
        self._refresh_action_enabled()

    def _refresh_action_enabled(self) -> None:
        busy = self._busy
        can_scan = (not busy) and self._target_valid
        self._scan_btn.setEnabled(can_scan)
        if hasattr(self, "_create_snapshot_btn"):
            self._create_snapshot_btn.setEnabled(can_scan)
            if busy and self._busy_op == "scan" and self._pending_create_snapshot:
                self._create_snapshot_btn.setText("Saving snapshot…")
            else:
                self._create_snapshot_btn.setText("Create full snapshot")
        if hasattr(self, "_create_snapshot_action"):
            self._create_snapshot_action.setEnabled(can_scan)
        if hasattr(self, "_scan_action"):
            self._scan_action.setEnabled(can_scan)
        if hasattr(self, "_compare_latest_action"):
            self._compare_latest_action.setEnabled(not busy)
        if not self._target_valid and not busy:
            self._scan_btn.setToolTip(
                "Scan & compare is disabled until a valid machine path is set "
                "(local image or \\10.0.0.90\\c$\\Goldclub)."
            )
        elif busy and self._busy_op == "scan":
            self._scan_btn.setToolTip("Scan in progress…")
        elif busy and self._busy_op == "compare":
            self._scan_btn.setToolTip("Compare in progress…")
        elif busy and self._busy_op == "stack_restart":
            self._scan_btn.setToolTip("Stack restart in progress…")
        elif busy and self._busy_op == "stack_kill":
            self._scan_btn.setToolTip("Stopping the GoldClub stack before restore…")
        else:
            self._scan_btn.setToolTip(
                "Primary action: scan config at the target into a new snapshot, "
                "then compare it to your session baseline (or the previous scan)."
            )
        self._compare_btn.setEnabled(not busy)
        self._compare_action.setEnabled(not busy)
        self._compare_latest_btn.setEnabled(not busy)
        self._detect_btn.setEnabled(not busy)
        self._refresh_btn.setEnabled(not busy)
        self._refresh_action.setEnabled(not busy)
        self._more_btn.setEnabled(not busy)
        if hasattr(self, "_clear_error30_action"):
            self._clear_error30_action.setEnabled((not busy) and self._target_valid)
        if hasattr(self, "_llave_password_action"):
            self._llave_password_action.setEnabled((not busy) and self._target_valid)
        if hasattr(self, "_start_stack_action"):
            self._start_stack_action.setEnabled((not busy) and self._target_valid)
        if self._widget_is_alive(getattr(self, "_auto_start_stack_cb", None)):
            self._auto_start_stack_cb.setEnabled(not busy)
        self._snapshots_toggle.setEnabled(True)
        self._set_baseline_action.setEnabled(not busy)
        self._delete_action.setEnabled(not busy)
        self._baseline_combo.setEnabled(not busy)
        self._target_combo.setEnabled(not busy)
        self._drive_edit.setEnabled(not busy)
        # Keep rows clickable during scan/restore — only the actions go idle.
        self._snapshot_table.setEnabled(True)
        if self._widget_is_alive(getattr(self, "_write_scope_combo", None)):
            self._write_scope_combo.setEnabled(not busy)
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
            enabled = not busy and has_archive
            self._write_baseline_btn.setEnabled(enabled)
            self._write_baseline_action.setEnabled(enabled)
        self._refresh_restore_selected_ui()
        self._refresh_revert_ui()

    def _append_status(self, message: str, *, force: bool = False) -> None:
        """Single status surface: one-line label + append to expandable log (dedupe idle repeats)."""
        text = (message or "").strip()
        if not text:
            return
        if not force and text == self._last_status_message:
            return
        self._last_status_message = text
        if hasattr(self, "_status_label"):
            metrics = self._status_label.fontMetrics()
            elided = metrics.elidedText(
                text, Qt.TextElideMode.ElideMiddle, max(120, self._status_label.width() or 400)
            )
            self._status_label.setText(elided)
            self._status_label.setToolTip(text)
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
                "Enter a machine path or click Auto-detect (Scan & compare disabled)."
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
        self._refresh_live_ruleta_version()

    def _on_progress(self, message: str) -> None:
        self._append_status(message, force=True)

    def _default_compare_pair(self) -> tuple[str, str] | None:
        """Last two scans, or session baseline vs newest other scan."""
        names = [row.name for row in self._snapshots]
        if len(names) < 2:
            return None
        session_bl = self._session_baseline_name
        if session_bl and session_bl in names:
            target = next((name for name in names if name != session_bl), None)
            if target:
                return session_bl, target
        return names[1], names[0]

    def _populate_combos(
        self,
        *,
        preserve_selection: bool = True,
        compared_name: str | None = None,
    ) -> None:
        names = [row.name for row in self._snapshots]
        prev_baseline = self._baseline_combo.currentText().strip() if preserve_selection else ""
        prev_target = self._target_combo.currentText().strip() if preserve_selection else ""
        self._baseline_combo.blockSignals(True)
        self._target_combo.blockSignals(True)
        self._baseline_combo.clear()
        self._target_combo.clear()
        self._baseline_combo.addItems(names)
        self._target_combo.addItems(names)
        if not names:
            self._baseline_combo.blockSignals(False)
            self._target_combo.blockSignals(False)
            return

        default_pair = self._default_compare_pair()
        if default_pair is not None:
            default_baseline, default_target = default_pair
        elif len(names) == 1:
            default_baseline, default_target = names[0], names[0]
        else:
            default_baseline, default_target = names[0], names[0]
        pinned = (compared_name or "").strip()
        if pinned and pinned in names:
            default_target = pinned
            if default_baseline == default_target:
                other = next((name for name in names if name != default_target), default_target)
                default_baseline = other

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
        self._baseline_combo.blockSignals(False)
        self._target_combo.blockSignals(False)

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
        if self._pending_scan_and_compare:
            self._pending_scan_and_compare = False
            self._pending_create_snapshot = False
            QTimer.singleShot(0, self._run_pending_scan_and_compare)
            return
        if self._pending_create_snapshot:
            self._pending_create_snapshot = False
            QTimer.singleShot(0, self._finish_create_full_snapshot)

    def _run_pending_scan_and_compare(self) -> None:
        """After Scan & compare: diff new scan vs session baseline / previous."""
        if self._busy:
            return
        names = [row.name for row in self._snapshots]
        target = self._last_scan_snapshot_name
        if not target or target not in names:
            pair = self._default_compare_pair()
            if pair is None:
                self._append_status(
                    "Scan done — need another snapshot to compare "
                    "(set a baseline or scan twice).",
                    force=True,
                )
                return
            baseline, target = pair
        else:
            session_bl = self._session_baseline_name
            if session_bl and session_bl in names and session_bl != target:
                baseline = session_bl
            else:
                baseline = next(
                    (name for name in names if name != target),
                    None,
                )
            if baseline is None:
                self._append_status(
                    "Scan done — need another snapshot to compare "
                    "(set a baseline or scan twice).",
                    force=True,
                )
                return
        self._baseline_combo.setCurrentText(baseline)
        self._target_combo.setCurrentText(target)
        self._append_status(f"Comparing {baseline} → {target} …", force=True)
        self._start_compare(baseline, target)

    def _on_snapshots_load_failed(self, message: str) -> None:
        self._pending_scan_and_compare = False
        self._pending_create_snapshot = False
        QMessageBox.warning(self, "Config Scanner", message)

    def _on_detect_drive(self) -> None:
        if self._busy:
            return
        self._run_auto_detect(silent=False)

    def _on_scan_clicked(self) -> None:
        self._start_live_scan(compare_after=True, include_software=False)

    def _on_create_snapshot_clicked(self) -> None:
        self._start_live_scan(compare_after=False, include_software=True)

    def _start_live_scan(self, *, compare_after: bool, include_software: bool) -> None:
        if self._busy or not self._target_valid:
            return
        self._clear_pending_write_state()
        self._pending_scan_and_compare = compare_after
        self._pending_create_snapshot = not compare_after
        self._pending_include_software = include_software
        kind = "Scan started" if compare_after else "Full snapshot started"
        self._append_status(
            f"{kind}: {self._drive_edit.text().strip()}",
            force=True,
        )
        self._set_busy(True, op="scan")
        schedule_prepare_scan_target(
            self._pool,
            self._service,
            self._drive_edit.text().strip(),
            self._emitter,
        )

    def _select_snapshot_named(
        self,
        snapshot_name: str,
        *,
        sync_combo: bool = True,
        scroll: bool = False,
    ) -> None:
        name = (snapshot_name or "").strip()
        if not name:
            return
        for index, row in enumerate(self._snapshots):
            if row.name != name:
                continue
            self._select_snapshot_row(index, sync_combo=sync_combo)
            if scroll:
                model_index = self._snapshot_model.index(index, 0)
                self._snapshot_table.scrollTo(
                    model_index,
                    QAbstractItemView.ScrollHint.EnsureVisible,
                )
            return

    def _finish_create_full_snapshot(self) -> None:
        name = (self._last_scan_snapshot_name or "").strip()
        count = self._last_scan_file_count
        for row in self._snapshots:
            if row.name == name:
                count = row.file_count
                break
        self._ensure_snapshots_drawer_visible()
        self._select_snapshot_named(name, scroll=True)
        QMessageBox.information(
            self,
            "Snapshot saved",
            full_snapshot_saved_message(
                name,
                count,
                software_file_count=self._last_scan_software_count,
                software_captured=self._last_scan_software_ok,
                extra_notes=self._last_scan_user_warnings,
            ),
        )

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
        self._ensure_snapshots_drawer_visible()
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
        self._ensure_snapshots_drawer_visible()
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
        self._ensure_snapshots_drawer_visible()
        name = self._resolve_snapshot_for_baseline()
        if not name:
            QMessageBox.warning(
                self,
                "Config Scanner",
                "No snapshot to use as baseline.\n\n"
                "Run Scan & compare first, then click Set reference baseline.",
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
        try:
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
                if self._widget_is_alive(getattr(self, "_write_baseline_btn", None)):
                    self._write_baseline_btn.setVisible(False)
                    self._write_baseline_btn.setEnabled(False)
                self._write_baseline_action.setEnabled(False)
                self._rebuild_changes_panel(None)
                if not ok:
                    QMessageBox.critical(self, "Config Scanner", message)
        finally:
            self._set_busy(False)

    def _on_open_report_clicked(self) -> None:
        if not self._last_report_path or not self._last_report_path.is_file():
            QMessageBox.warning(self, "Config Scanner", "No report available yet.")
            return
        ok, message = open_html_file(self._last_report_path)
        if not ok:
            QMessageBox.warning(self, "Config Scanner", message)

    def _open_folder(self, folder: Path) -> None:
        path = folder.resolve()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_open_snapshots_clicked(self) -> None:
        self._open_folder(self._service.get_snapshots_dir())

    def _on_open_reports_clicked(self) -> None:
        self._open_folder(self._service.get_reports_dir())
