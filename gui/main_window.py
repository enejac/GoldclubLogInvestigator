"""
Main window (View): dashboard, virtualized table, inspector, scan controls.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QEasingCurve,
    Qt,
    QPropertyAnimation,
    QSettings,
    Signal,
    QStandardPaths,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPalette,
    QPixmap,
    QShortcut,
    QShowEvent,
)
from shiboken6 import Shiboken
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QStyle,
    QTabWidget,
    QSystemTrayIcon,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from diag_logging import fleet_timesync_logger
from database.manager import format_machine_label
from config import (
    DEFAULT_LOCAL_LOG_ROOT,
    DEFAULT_REMOTE_IP,
    FLEET_HEARTBEAT_ENABLED,
    ResolvedScanPath,
    SCAN_UI_YIELD_MS,
    TIMELINE_SORT_MAX_NODES,
    format_unc_log_root,
)
from config_manager import SettingsManager
from product_version import PRODUCT_VERSION_PLACEHOLDER
from gui.db_worker import (
    DbCountEmitter,
    DbPersistEmitter,
    DbQueryEmitter,
    schedule_db_count,
    schedule_db_persist,
    schedule_db_query,
)
from gui.fleet_tab import FleetTabWidget
from gui.fleet_heartbeat import FleetHeartbeatWorker
from gui.fleet_worker import (
    FleetEmitter,
    TimeSyncEmitter,
    schedule_fleet_refresh,
    schedule_fleet_subnet_scan,
    schedule_remote_time_sync,
    schedule_single_host_drift_refresh,
)
from gui.history_tab import HistoryTabWidget
from gui.automation_tab import AutomationTabWidget
from gui.config_scanner_tab import ConfigScannerTabWidget
from gui.path_resolve_worker import PathResolveEmitter, schedule_path_resolve
from gui.bookmark_dialog import BookmarkDialog
from gui.context_dialog import IncidentContextDialog
from gui.timeline_widget import SessionTimelineWidget
from gui.incident_table_model import IncidentTableModel
from gui.filter_chips import FilterChipsBar
from gui.live_watch_thread import LiveWatchThread
from gui.rule_editor_dialog import RuleEditorDialog
from gui.accounting_scan_worker import AccountingScanEmitter, schedule_accounting_scan
from gui.sas_dialog import SASVerificationReportDialog
from gui.sas_verify_dialog import SasVerifyDialog
from gui.network_case_pack_worker import (
    _CasePackWorkerSignals,
    _NetworkCasePackWorker,
)
from gui.screen_capture_worker import ScreenCaptureEmitter, schedule_remote_screen_capture
from gui.ram_clear_worker import RamClearEmitter, schedule_ram_clear
from gui.screenshot_preview_dialog import ScreenshotPreviewDialog
from gui.log_highlighter import LogSyntaxHighlighter
from gui.scan_worker import ScanWorker
from gui.help_dialog import HelpDialog
from gui.settings_dialog import SettingsDialog
from gui.time_range_dialog import TimeRangeScanDialog
from gui.theme_utils import apply_theme
from gui.analytics_widget import GameAnalyticsWidget
from gui.state_timeline_widget import build_timeline_scroll_area
from gui.severity_delegate import SeverityDelegate
from gui.table_smart_stretch import TableSmartStretchFilter
from gui.stack_loader import StackTraceEmitter, schedule_stack_load
from gui.palette_adapt import (
    muted_text,
    syntax_critical,
    syntax_warning,
    text_danger,
    text_success,
)
from gui.ui_feedback import install_disabled_click_warner
from gui.view_model import IncidentViewModel
from network.case_packer import suggest_case_pack_zip_name
from network.meter_comparator import compare_sas_and_xml
from network.sas_decoder import parse_sas_log_file
from network.log_janitor import LogJanitorWorker
from network.notifier import (
    format_live_toast,
    notification_ignore_fingerprint,
    should_notify,
    show_toast,
)
from parser import Incident
from rules_engine import reload_rules_manager
from analytics_engine import calculate_session_analytics
from timeline_engine import StateNode
from gui.ai_enhance_worker import AiEnhanceEmitter, schedule_ai_enhance
from gui.ai_jira_dialog import CopyableFieldWidget, parse_ai_response, strip_markdown_bolding
from gui.known_issues_panel import KnownIssuesPanel
from gui.full_audit_worker import FullAuditEmitter, schedule_full_audit
from parser_rules import count_known_issue_matches, related_tracking_for_incident

logger = logging.getLogger(__name__)

# QFileDialog filter: logs + TXRXData.dat-style files without forcing "All Files"
_LOG_OPEN_FILTER = "Log & Data Files (*.log *.txt *.dat);;All Files (*)"
# Suffixes accepted via drag-and-drop onto the window (matches the open-file filter).
_DROP_LOG_SUFFIXES = frozenset({".log", ".txt", ".dat"})


def _collect_related_tracking(incidents: list[Incident]) -> str:
    """De-duplicated Related tracking text for defect tickets."""
    seen: set[str] = set()
    blocks: list[str] = []
    for item in incidents:
        text = related_tracking_for_incident(item.line_snippet or "", item.severity or "")
        if text and text not in seen:
            seen.add(text)
            blocks.append(text)
    return "\n\n".join(blocks)


def _format_hms_duration(total_secs: int) -> str:
    total_secs = max(0, total_secs)
    h, rem = divmod(total_secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_scan_wall_duration(secs: float) -> str:
    """Human-readable scan duration for status line and dashboard."""
    if secs < 0:
        secs = 0.0
    if secs < 60:
        return f"{secs:.1f}s"
    whole = int(round(secs))
    m, s = divmod(whole, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


class _SessionSaveEmitter(QObject):
    finished = Signal()


class _SessionSaveRunnable(QRunnable):
    def __init__(
        self,
        vm: IncidentViewModel,
        *,
        log_directory: str,
        connection_mode: str,
        remote_ip: str | None,
        emitter: _SessionSaveEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._vm = vm
        self._log_directory = log_directory
        self._connection_mode = connection_mode
        self._remote_ip = remote_ip
        self._emitter = emitter

    def run(self) -> None:
        try:
            self._vm.save_session_state(
                log_directory=self._log_directory,
                connection_mode=self._connection_mode,
                remote_ip=self._remote_ip,
            )
        except Exception:
            pass
        finally:
            self._emitter.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Log Investigator")
        from gui.app_branding import apply_window_branding

        apply_window_branding(self)
        self.resize(1280, 780)

        self._vm = IncidentViewModel(self)
        self._table_model = IncidentTableModel(self._vm, self)
        self._worker: ScanWorker | None = None
        self._live_thread: LiveWatchThread | None = None
        self._scan_started_monotonic: float | None = None
        self._live_watch_started_monotonic: float | None = None
        self._stack_seq = 0
        self._stack_emitter = StackTraceEmitter(self)
        self._stack_emitter.loaded.connect(self._on_stack_loaded)
        self._inspector_log_base = ""

        self._settings = QSettings("Goldclub", "LogInvestigator")
        self._pending_ai_tasks = 0
        self._active_ai_emitters: list[QObject] = []
        self._shutdown_force_quit = False
        self._shutdown_started = False
        self._shutdown_cleanup_started = False
        self._palette_refresh_busy = False
        self._shutdown_save_emitter: _SessionSaveEmitter | None = None
        self._probe_seq = 0
        self._prescan_seq = 0
        self._version_fetch_seq = 0
        self._version_fetch_timer = QTimer(self)
        self._version_fetch_timer.setSingleShot(True)
        self._version_fetch_timer.setInterval(500)
        self._version_fetch_timer.timeout.connect(self._flush_version_fetch)
        self._version_fetch_pending_path: str | None = None
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(400)
        self._probe_timer.timeout.connect(self._fire_path_probe)
        self._path_probe_emitter = PathResolveEmitter(self)
        self._path_probe_emitter.finished.connect(self._on_path_probe_finished)
        self._path_prescan_emitter = PathResolveEmitter(self)
        self._path_prescan_emitter.finished.connect(self._on_path_prescan_finished)
        self._screen_capture_emitter = ScreenCaptureEmitter(self)
        self._screen_capture_emitter.finished.connect(self._on_screen_capture_finished)
        self._remote_capture_busy = False
        self._ram_clear_emitter = RamClearEmitter(self)
        self._ram_clear_emitter.finished.connect(self._on_ram_clear_finished)
        self._ram_clear_busy = False
        self._last_remote_screenshot_path: str | None = None
        self._pending_sas_file = ""
        self._last_sas_verification_plaintext = ""
        self._last_sas_verification_summary = ""
        self._sas_sync_emitter = AccountingScanEmitter(self)
        self._sas_sync_emitter.finished.connect(self._on_sas_sync_finished)

        self._case_pack_worker_signals = _CasePackWorkerSignals(self)
        self._case_pack_worker_signals.success.connect(self._on_case_pack_success)
        self._case_pack_worker_signals.error.connect(self._on_case_pack_error)

        self._persist_db_after_scan = False
        self._last_scan_roots: list[str] = []
        self._db_persist_emitter = DbPersistEmitter(self)
        self._db_persist_emitter.finished.connect(self._on_db_persist_finished)
        self._db_query_emitter = DbQueryEmitter(self)
        self._db_query_emitter.finished.connect(self._on_db_query_finished)
        self._db_count_emitter = DbCountEmitter(self)
        self._db_count_emitter.finished.connect(self._on_db_count_finished)

        self._fleet_op_active = False
        self._janitor_worker: LogJanitorWorker | None = None
        self._janitor_busy_ip: str | None = None
        self._pending_parse_results: list[object] = []
        self._parse_drain_scheduled = False
        self._scan_complete_pending = False
        self._scan_total_files = 0
        self._scan_done_files = 0
        self._path_led_state: str = "muted"
        self._incident_table_initial_column_fit_done = False
        self._incident_table_column_fit_scheduled = False
        self._incident_table_header_restored_from_settings = False
        self._window_geometry_restored = False

        self._global_scan_start: datetime | None = None
        self._global_scan_end: datetime | None = None

        self._build_ui()
        self._apply_theme_styles()
        bar = self.menuBar()
        m_file = bar.addMenu("&File")
        act_settings = QAction("Settings…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self._on_settings_clicked)
        m_file.addAction(act_settings)
        m_file.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.setShortcut(QKeySequence("Alt+F4"))
        act_exit.triggered.connect(self.close)
        m_file.addAction(act_exit)
        m_help = bar.addMenu("&Help")
        act_help = QAction("User Guide…", self)
        act_help.setShortcut(QKeySequence("F1"))
        act_help.setStatusTip("Open the Log Investigator user guide")
        act_help.triggered.connect(self._on_help_clicked)
        m_help.addAction(act_help)
        self._apply_path_led_style()
        self._apply_rec_label_style()
        _app = QApplication.instance()
        if _app is not None:
            _app.paletteChanged.connect(self._on_application_palette_changed)
        self._fleet_emitter = FleetEmitter(self)
        self._fleet_emitter.finished.connect(
            self._on_fleet_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._fleet_emitter.scan_progress.connect(self._fleet_tab.set_scan_progress)
        self._fleet_drift_emitter = FleetEmitter(self)
        self._fleet_drift_emitter.finished.connect(
            self._on_fleet_drift_refresh_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._time_sync_emitter = TimeSyncEmitter(self)
        self._time_sync_emitter.finished.connect(
            self._on_remote_time_sync_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        self._fleet_heartbeat: FleetHeartbeatWorker | None = None
        if FLEET_HEARTBEAT_ENABLED:
            self._fleet_heartbeat = FleetHeartbeatWorker(self._fleet_tab.database_manager(), self)
            self._fleet_heartbeat.fleet_updated.connect(self._on_heartbeat_fleet)
            self._fleet_heartbeat.heartbeat_failed.connect(self._on_heartbeat_failed)
            self._fleet_heartbeat.start()

        self._load_connection_settings()
        self._wire_vm()
        self._try_restore_last_session_snapshot()
        self._refresh_game_analytics_dashboard()
        self._update_secondary_actions()
        self._schedule_path_probe()
        self._setup_tray_icon()
        QTimer.singleShot(400, self._refresh_history_db_count)
        QTimer.singleShot(450, self._fleet_load_from_db_sync)
        QTimer.singleShot(600, self._schedule_fleet_startup_refresh)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        # --- connection settings ---
        conn_box = QGroupBox("Connection settings")
        conn_outer = QVBoxLayout(conn_box)
        conn_row = QHBoxLayout()
        self._radio_local = QRadioButton("Local machine")
        self._radio_remote = QRadioButton("Remote IP")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._radio_local)
        self._mode_group.addButton(self._radio_remote)
        self._radio_local.setChecked(True)
        conn_row.addWidget(self._radio_local)
        conn_row.addWidget(self._radio_remote)
        conn_row.addSpacing(16)
        conn_row.addWidget(QLabel("Host:"))
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("e.g. 10.0.0.90")
        self._ip_edit.setMaximumWidth(200)
        conn_row.addWidget(self._ip_edit)
        conn_row.addSpacing(12)
        self._led = QLabel("●")
        self._led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._led.setFixedSize(24, 24)
        conn_row.addWidget(self._led)
        self._led_status = QLabel("Not checked")
        conn_row.addWidget(self._led_status)
        conn_row.addSpacing(12)
        self._incidents_capture_btn = QPushButton("\U0001f4f8 Capture Screen")
        self._incidents_capture_btn.setToolTip(
            "Capture the remote cabinet display (PsExec + admin c$ share). "
            "Use Remote IP mode with a reachable host."
        )
        self._incidents_capture_btn.clicked.connect(self._on_incidents_capture_screen_clicked)
        conn_row.addWidget(self._incidents_capture_btn)
        self._ram_clear_btn = QPushButton("RAM Clear")
        self._ram_clear_btn.setToolTip(
            "Stop the running game and GoldClub processes, then run the maintenance "
            "RAM-clear chain immediately (slot or roulette). Requires local game "
            "install or Remote IP with PsExec access."
        )
        self._ram_clear_btn.clicked.connect(self._on_ram_clear_clicked)
        conn_row.addWidget(self._ram_clear_btn)
        conn_row.addStretch(1)
        conn_outer.addLayout(conn_row)

        live_row = QHBoxLayout()
        self._live_toggle = QPushButton("Live Watch")
        self._live_toggle.setCheckable(True)
        self._live_toggle.setToolTip(
            "Tail-follow the most recently modified log files under the scan root "
            f"(see config: active file limit & poll interval)."
        )
        self._rec_label = QLabel("● REC")
        self._rec_label.setVisible(False)
        self._autoscroll_chk = QCheckBox("Auto-scroll to latest")
        self._autoscroll_chk.setToolTip(
            "When live incidents arrive, select and scroll to the newest table row."
        )
        live_row.addWidget(self._live_toggle)
        live_row.addWidget(self._rec_label)
        self._live_elapsed_lbl = QLabel("Live [--:--:--]")
        self._live_elapsed_lbl.setToolTip(
            "Elapsed time while Live Watch is tailing logs (resets when Live Watch stops)."
        )
        self._live_elapsed_lbl.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._live_elapsed_lbl.setStyleSheet("font-family: monospace; min-width: 11em;")
        live_row.addWidget(self._live_elapsed_lbl)
        live_row.addSpacing(14)
        live_row.addWidget(self._autoscroll_chk)
        live_row.addSpacing(10)
        self._notif_bell_btn = QToolButton()
        self._notif_bell_btn.setCheckable(True)
        self._notif_bell_btn.setChecked(SettingsManager.get_notifications_enabled())
        self._notif_bell_btn.setText("\U0001f514")
        self._notif_bell_btn.setAutoRaise(True)
        self._notif_bell_btn.toggled.connect(self._on_notifications_bell_toggled)
        self._update_notif_bell_tooltip()
        live_row.addWidget(self._notif_bell_btn)
        live_row.addSpacing(20)
        live_row.addWidget(QLabel("Session:"))
        self._session_btn = QPushButton("Start Session")
        self._session_btn.setToolTip(
            "Start / stop recording session (Ctrl+R). "
            "Show only incidents at or after session start; with Live Watch on, tail jumps to EOF."
        )
        _rec_pix = getattr(
            QStyle.StandardPixmap,
            "SP_MediaRecord",
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        self._session_btn.setIcon(self.style().standardIcon(_rec_pix))
        self._session_btn.clicked.connect(self._on_session_toggle_clicked)
        live_row.addWidget(self._session_btn)
        self._session_elapsed_lbl = QLabel("[--:--:--]")
        self._session_elapsed_lbl.setToolTip(
            "Session recording duration (Start Session / Ctrl+R). Independent of Live Watch."
        )
        self._session_elapsed_lbl.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._session_elapsed_lbl.setStyleSheet("font-family: monospace; min-width: 9em;")
        live_row.addWidget(self._session_elapsed_lbl)
        self._session_clear_on_start_chk = QCheckBox("Clear UI on Start")
        self._session_clear_on_start_chk.setToolTip(
            "When starting a session, clear the incident table and state timeline first."
        )
        live_row.addWidget(self._session_clear_on_start_chk)
        live_row.addSpacing(16)
        self._custom_rules_btn = QPushButton("Custom Signatures")
        self._custom_rules_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView)
        )
        self._custom_rules_btn.setToolTip(
            "Edit user-defined regex signatures (saved under Local AppData)."
        )
        self._custom_rules_btn.clicked.connect(self._on_custom_signatures)
        live_row.addWidget(self._custom_rules_btn)
        self._settings_btn = QPushButton("\u2699 Settings")
        self._settings_btn.setToolTip(
            "Log janitor retention, fleet clock drift threshold, and other preferences."
        )
        self._settings_btn.clicked.connect(self._on_settings_clicked)
        live_row.addWidget(self._settings_btn)
        live_row.addStretch(1)
        conn_outer.addLayout(live_row)

        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self._tick_session_elapsed)

        self._live_watch_timer = QTimer(self)
        self._live_watch_timer.setInterval(1000)
        self._live_watch_timer.timeout.connect(self._tick_live_watch_elapsed)

        rec_fx = QGraphicsOpacityEffect(self._rec_label)
        self._rec_label.setGraphicsEffect(rec_fx)
        self._rec_pulse = QPropertyAnimation(rec_fx, b"opacity")
        self._rec_pulse.setDuration(850)
        self._rec_pulse.setStartValue(1.0)
        self._rec_pulse.setEndValue(0.3)
        self._rec_pulse.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._rec_pulse.setLoopCount(-1)

        root.addWidget(conn_box)

        # --- scan root + actions ---
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Scan root:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(r"e.g. C:\Goldclub\var\log or D:\_LogFiles\log_DD_MM_YYYY")
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self._open_file_btn = QPushButton("Open file…")
        self._open_file_btn.setToolTip(
            "Choose a single .log / .txt file (local path). Use ⏱ Time Filter to limit "
            "parse range (applies to Scan and open file)."
        )
        self._btn_time_filter = QPushButton("⏱ Time Filter: Off")
        self._btn_time_filter.setToolTip(
            "Set a UTC time window for the next Scan (and single-file open). "
            "Large logs parse faster when bounded."
        )
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setObjectName("primary")
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        bar.addWidget(self._path_edit, stretch=1)
        bar.addWidget(self._browse_btn)
        bar.addWidget(self._open_file_btn)
        self._btn_verify_sas = QPushButton("Verify SAS Accounting")
        self._btn_verify_sas.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self._btn_verify_sas_default_text = self._btn_verify_sas.text()
        self._btn_verify_sas.setToolTip(
            "Choose a SAS traffic log (.dat), fetch gm2au from the remote host when configured, "
            "and compare extended meters to cabinet XML and SlotLog accounting lines.\n"
            "Set Scan root to the cabinet log folder for SlotLog comparison."
        )
        self._btn_verify_sas.clicked.connect(self._on_verify_sas_clicked)
        bar.addWidget(self._btn_verify_sas)
        bar.addWidget(self._btn_time_filter)
        bar.addWidget(self._scan_btn)
        bar.addWidget(self._stop_btn)
        root.addLayout(bar)

        # --- search / filter ---
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText(
            "Search logs… (Ctrl+F) — Exception, SlotMachine, theme, path, …"
        )
        self._filter_edit.setToolTip(
            "Focus: Ctrl+F. Esc: clear search text, or reset quick-filter chips when empty."
        )
        self._filter_busy = QLabel("")
        self._filter_busy.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        filt.addWidget(self._filter_edit, stretch=1)
        filt.addWidget(self._filter_busy)
        root.addLayout(filt)

        # --- dashboard ---
        dash = QHBoxLayout()
        self._card_files, self._files_value, self._files_hint = self._metric_card(
            "Total logs scanned", "0", "—"
        )
        self._card_crit, self._crit_value = self._metric_card_pulse("Critical errors", "0")
        self._card_game, self._game_value, _ = self._metric_card(
            "Currently active game", "—", "from Themes\\ path"
        )
        dash.addWidget(self._card_files)
        dash.addWidget(self._card_crit)
        dash.addWidget(self._card_game)
        dash.addStretch(1)
        self._scan_last_duration_lbl = QLabel("Last scan: —")
        self._scan_last_duration_lbl.setObjectName("metricHint")
        self._scan_last_duration_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._scan_last_duration_lbl.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._scan_last_duration_lbl.setStyleSheet("font-size: 12px; font-family: monospace;")
        self._scan_last_duration_lbl.setToolTip(
            "Wall time for the most recent completed scan (parse + UI drain)."
        )
        dash.addWidget(self._scan_last_duration_lbl)
        self._onehand_version_lbl = QLabel("")
        self._onehand_version_lbl.setObjectName("metricHint")
        self._onehand_version_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._onehand_version_lbl.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._onehand_version_lbl.setStyleSheet("font-size: 12px;")
        self._onehand_version_lbl.setToolTip(
            "Primary source (Windows): Product version from OneHand.exe beside the scan root "
            "(same as file Properties → Details), including RC labels (e.g. +RC6).\n"
            "Fallback: log lines «OneHand.MainFrm - SlotMachine v…» / LogDaemon «Spawning v…»."
        )
        self.software_version_label = self._onehand_version_lbl
        self.software_version_label.setText("Version: [Scanning...]")
        dash.addWidget(self._onehand_version_lbl)
        root.addLayout(dash)

        crit_fx = QGraphicsOpacityEffect(self._crit_value)
        self._crit_value.setGraphicsEffect(crit_fx)
        self._crit_pulse = QPropertyAnimation(crit_fx, b"opacity")
        self._crit_pulse.setDuration(1400)
        self._crit_pulse.setStartValue(1.0)
        self._crit_pulse.setEndValue(0.55)
        self._crit_pulse.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._crit_pulse.setLoopCount(-1)

        # --- progress ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # --- Incidents tab (table + inspector) & State Timeline tab ---
        self._tabs = QTabWidget()

        tab_incidents = QWidget()
        inc_v = QVBoxLayout(tab_incidents)
        inc_v.setContentsMargins(0, 0, 0, 0)
        self._filter_chips = FilterChipsBar(self._vm, parent=tab_incidents)
        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.addWidget(self._filter_chips, stretch=1)
        self._case_pack_btn = QPushButton("\U0001f4e6 Create Case Pack")
        self._case_pack_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self._case_pack_btn.setToolTip(
            "Zip metadata, bookmarks, and the current filtered incidents for developer handoff "
            "(Ctrl+S)."
        )
        self._case_pack_btn.clicked.connect(self._on_create_case_pack_clicked)
        self._case_pack_btn_default_text = self._case_pack_btn.text()
        chips_row.addWidget(
            self._case_pack_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._full_audit_btn = QPushButton("Full Session Audit")
        self._full_audit_btn.setObjectName("full_ai_audit_btn")
        self._full_audit_btn.setToolTip(
            "Analyze all incidents in the current session and summarize systemic issues and stability."
        )
        _f = self._full_audit_btn.font()
        _f.setBold(True)
        self._full_audit_btn.setFont(_f)
        self._full_audit_btn.clicked.connect(self._on_full_audit_clicked)
        chips_row.addWidget(
            self._full_audit_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._collapse_dup_chk = QCheckBox("Collapse duplicates")
        self._collapse_dup_chk.setToolTip(
            "Merge consecutive rows that share the same severity, error type, and game "
            "into one row with a [Nx] count (first row’s timestamp)."
        )
        self._collapse_dup_chk.toggled.connect(self._vm.set_collapse_duplicates)
        chips_row.addWidget(
            self._collapse_dup_chk,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        inc_v.addLayout(chips_row)

        self._session_timeline = SessionTimelineWidget(tab_incidents)
        self._session_timeline.setToolTip(
            "Filtered incidents over time (by log timestamp). Click to jump; drag to select a "
            "time range (filters the table); double-click to clear the range."
        )
        self._session_timeline.incident_clicked.connect(
            self._on_session_timeline_incident_clicked
        )
        self._session_timeline.time_range_selected.connect(self._vm.set_time_slice)
        inc_v.addWidget(self._session_timeline)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._table = QTableView()
        self._table.setModel(self._table_model)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(26)
        header = self._table.horizontalHeader()
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        SettingsManager.invalidate_table_header_state_if_schema_stale()
        header.setStretchLastSection(False)
        saved_state = SettingsManager.get_table_state()
        restored = bool(
            saved_state
            and header.restoreState(QByteArray(saved_state))
        )
        self._incident_table_header_restored_from_settings = restored
        if restored:
            self._reassert_incident_table_header_resize_modes()
        else:
            self._apply_incident_table_header_factory_modes()
        self._table.setItemDelegate(SeverityDelegate(self._table))
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table_model.modelReset.connect(self._schedule_initial_incident_table_column_fit)
        self._table_model.rowsInserted.connect(
            self._on_incident_table_rows_inserted_for_column_fit
        )
        self._table_model.column_visibility_changed.connect(
            self._sync_incident_table_validation_ram_columns
        )
        self._table_stretch_filter = TableSmartStretchFilter(self._table)
        self._table.viewport().installEventFilter(self._table_stretch_filter)
        self._sync_incident_table_validation_ram_columns()

        inspect = QWidget()
        il = QVBoxLayout(inspect)
        il.setContentsMargins(0, 0, 0, 0)
        _hdr1 = QLabel("<b>Root cause inspector</b>")
        _hdr1.setTextFormat(Qt.TextFormat.RichText)
        il.addWidget(_hdr1)
        self._known_issues_panel = KnownIssuesPanel(inspect)
        il.addWidget(self._known_issues_panel)
        self._refresh_known_issues_session_counts()
        self._inspector_meta = QPlainTextEdit()
        self._inspector_meta.setReadOnly(True)
        self._inspector_meta.setMaximumHeight(140)
        self._inspector_meta.setPlaceholderText("Select an incident row…")
        il.addWidget(self._inspector_meta)
        log_header = QHBoxLayout()
        _hdr2 = QLabel("<b>Log context & stack trace</b>")
        _hdr2.setTextFormat(Qt.TextFormat.RichText)
        log_header.addWidget(_hdr2)
        log_header.addStretch(1)
        self._ai_enhance_btn = QPushButton("Generate Technical Summary")
        self._ai_enhance_btn.setEnabled(False)
        self._ai_enhance_btn.setToolTip(
            "Use the configured analysis provider to produce a structured root-cause report for the\n"
            "selected incident and the preceding state timeline events."
        )
        self._ai_enhance_btn.clicked.connect(self._on_ai_enhance_clicked)
        log_header.addWidget(self._ai_enhance_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self._open_notepad_btn = QPushButton("Open in Notepad")
        self._open_notepad_btn.setEnabled(False)
        self._open_notepad_btn.setToolTip(
            "Open the full log file in Windows Notepad (UNC paths supported when reachable)."
        )
        self._open_notepad_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self._open_notepad_btn.clicked.connect(self._on_open_notepad_clicked)
        log_header.addWidget(self._open_notepad_btn, alignment=Qt.AlignmentFlag.AlignRight)
        il.addLayout(log_header)
        self._inspector_log = QPlainTextEdit()
        self._inspector_log.setReadOnly(True)
        self._inspector_log.setPlaceholderText("Context loads when a row is selected.")
        il.addWidget(self._inspector_log, stretch=1)
        _pal = self.palette()
        self._inspector_meta_highlighter = LogSyntaxHighlighter(
            self._inspector_meta.document(), _pal
        )
        self._inspector_log_highlighter = LogSyntaxHighlighter(
            self._inspector_log.document(), _pal
        )

        split.addWidget(self._table)
        split.addWidget(inspect)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        inc_v.addWidget(split, stretch=1)

        tab_timeline = QWidget()
        tl_v = QVBoxLayout(tab_timeline)
        tl_v.setContentsMargins(8, 8, 8, 8)
        self._timeline_legend_label = QLabel()
        self._timeline_legend_label.setWordWrap(True)
        self._timeline_legend_label.setTextFormat(Qt.TextFormat.RichText)
        self._refresh_timeline_legend()
        tl_v.addWidget(self._timeline_legend_label)
        self._timeline_scroll, self._timeline_canvas = build_timeline_scroll_area()
        tl_v.addWidget(self._timeline_scroll, stretch=1)
        btn_clear_tl = QPushButton("Clear timeline filter")
        btn_clear_tl.clicked.connect(self._vm.clear_timeline_filter)
        tl_v.addWidget(btn_clear_tl)

        self._history_tab = HistoryTabWidget()
        self._fleet_tab = FleetTabWidget()
        self._automation_tab = AutomationTabWidget()
        self._config_scanner_tab = ConfigScannerTabWidget()
        self._tabs.addTab(tab_incidents, "Incidents")
        self._tabs.addTab(tab_timeline, "State Timeline")
        tab_analytics = QWidget()
        analytics_v = QVBoxLayout(tab_analytics)
        analytics_v.setContentsMargins(8, 8, 8, 8)
        self._game_analytics_widget = GameAnalyticsWidget(tab_analytics)
        analytics_v.addWidget(self._game_analytics_widget, stretch=1)
        self._tabs.addTab(tab_analytics, "Game Analytics")
        self._tabs.addTab(self._automation_tab, "Automated Tests")
        self._tabs.addTab(self._config_scanner_tab, "Config Scanner")
        self._tabs.addTab(self._history_tab, "History")
        self._tabs.addTab(self._fleet_tab, "Fleet Overview")
        root.addWidget(self._tabs, stretch=1)

        self._history_tab.sync_scan_requested.connect(self._on_history_sync_scan)
        self._history_tab.search_requested.connect(self._on_history_search)
        self._history_tab.count_refresh_requested.connect(self._on_history_count_refresh)
        self._fleet_tab.request_subnet_scan.connect(self._on_fleet_subnet_scan)
        self._fleet_tab.request_refresh.connect(self._on_fleet_manual_refresh)
        self._fleet_tab.cabinet_added.connect(self._on_fleet_cabinet_added)
        self._fleet_tab.card_action.connect(self._on_fleet_card_action)

        sb = QStatusBar()
        self.setStatusBar(sb)
        from gui.app_branding import status_bar_brand_pixmap

        self._status_brand = QLabel()
        self._status_brand.setPixmap(status_bar_brand_pixmap(size=18))
        self._status_brand.setToolTip(
            "Log Investigator — cabinet log analysis, SAS meter verification, and fleet triage"
        )
        self._status_brand.setContentsMargins(0, 0, 6, 0)
        sb.addWidget(self._status_brand)
        self._status_app_label = QLabel("Log Investigator")
        self._status_app_label.setStyleSheet("QLabel { padding-right: 10px; }")
        sb.addWidget(self._status_app_label)
        self._status = QLabel("Ready")
        sb.addWidget(self._status, stretch=1)

        self._browse_btn.clicked.connect(self._pick_directory)
        self._open_file_btn.clicked.connect(self._on_open_log_file_clicked)
        # Drag a .log/.txt/.dat file (or a log folder) anywhere onto the window to analyze it.
        self.setAcceptDrops(True)
        self._btn_time_filter.clicked.connect(self._on_time_filter_clicked)
        self._scan_btn.clicked.connect(self._start_scan)
        self._stop_btn.clicked.connect(self._stop_scan)
        self._filter_edit.textChanged.connect(self._vm.set_filter_text)

        self._live_toggle.toggled.connect(self._on_live_watch_toggled)

        self._radio_local.toggled.connect(self._on_mode_toggled)
        self._radio_remote.toggled.connect(self._on_mode_toggled)
        self._ip_edit.textChanged.connect(self._schedule_path_probe)
        self._ip_edit.textChanged.connect(self._sync_remote_ram_target_ip)
        self._ip_edit.textChanged.connect(lambda _t: self._update_ram_clear_button_enabled())
        self._path_edit.textChanged.connect(self._on_path_text_changed)

        sel = self._table.selectionModel()
        assert sel is not None
        sel.currentChanged.connect(self._on_table_current_changed)

        self._vm.filter_rebuilt.connect(self._refresh_session_timeline)
        self._vm.rows_inserted.connect(self._refresh_session_timeline)
        self._refresh_session_timeline()

        self._setup_keyboard_shortcuts()
        self._install_disabled_button_feedback()

    def _sync_incident_table_validation_ram_columns(self) -> None:
        m = self._table_model
        self._table.setColumnHidden(
            IncidentTableModel.VALIDATION_COLUMN,
            not m.has_validation_data,
        )
        self._table.setColumnHidden(
            IncidentTableModel.RAM_COLUMN,
            not m.has_ram_data,
        )
        self._table_stretch_filter._stretch_message_column(self._table)

    def _apply_incident_table_header_factory_modes(self) -> None:
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.resizeSection(0, 40)
        self._reassert_incident_table_header_resize_modes()

    def _reassert_incident_table_header_resize_modes(self) -> None:
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        for col in range(hh.count()):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)

    def _reset_incident_table_column_fit_state(self) -> None:
        self._incident_table_initial_column_fit_done = False
        self._incident_table_column_fit_scheduled = False

    def _schedule_initial_incident_table_column_fit(self) -> None:
        self._defer_initial_incident_table_column_fit()

    def _on_incident_table_rows_inserted_for_column_fit(
        self, _parent: QModelIndex, _first: int, _last: int
    ) -> None:
        self._defer_initial_incident_table_column_fit()

    def _defer_initial_incident_table_column_fit(self) -> None:
        if self._incident_table_initial_column_fit_done:
            return
        if self._table_model.rowCount() <= 0:
            return
        if self._incident_table_column_fit_scheduled:
            return
        self._incident_table_column_fit_scheduled = True
        QTimer.singleShot(0, self._run_initial_incident_table_column_fit)

    def _run_initial_incident_table_column_fit(self) -> None:
        self._incident_table_column_fit_scheduled = False
        if self._incident_table_initial_column_fit_done:
            return
        if self._table_model.rowCount() <= 0:
            return
        if self._incident_table_header_restored_from_settings:
            self._sync_incident_table_validation_ram_columns()
            self._incident_table_initial_column_fit_done = True
            return
        self._table.resizeColumnsToContents()
        self._reassert_incident_table_header_resize_modes()
        self._sync_incident_table_validation_ram_columns()
        self._incident_table_initial_column_fit_done = True

    def _metric_card(self, title: str, value: str, hint: str) -> tuple[QFrame, QLabel, QLabel]:
        f = QFrame()
        f.setObjectName("metricCard")
        lay = QVBoxLayout(f)
        t = QLabel(title)
        t.setObjectName("metricLabel")
        v = QLabel(value)
        v.setObjectName("metricValue")
        h = QLabel(hint)
        h.setObjectName("metricHint")
        h.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        h.setStyleSheet("font-size: 11px;")
        lay.addWidget(t)
        lay.addWidget(v)
        lay.addWidget(h)
        return f, v, h

    def _metric_card_pulse(self, title: str, value: str) -> tuple[QFrame, QLabel]:
        f = QFrame()
        f.setObjectName("metricCardPulse")
        lay = QVBoxLayout(f)
        t = QLabel(title)
        t.setObjectName("metricLabel")
        v = QLabel(value)
        v.setObjectName("metricValue")
        lay.addWidget(t)
        lay.addWidget(v)
        return f, v

    def _wire_vm(self) -> None:
        self._vm.stats_changed.connect(self._refresh_dashboard)
        self._vm.active_game_changed.connect(self._game_value.setText)
        self._vm.filter_busy_changed.connect(self._on_filter_busy)
        self._vm.selected_incident_changed.connect(self._on_selection_vm)
        self._vm.filter_rebuilt.connect(self._refresh_known_issues_session_counts)
        self._vm.incident_event_ram_updated.connect(self._on_incident_event_ram_updated)
        self._vm.live_critical_filtered.connect(self._on_live_critical_alert)
        self._vm.scan_state_changed.connect(self._update_secondary_actions)
        self._vm.state_nodes_changed.connect(self._refresh_timeline_canvas)
        self._vm.state_nodes_changed.connect(self._refresh_game_analytics_dashboard)
        self._vm.session_state_changed.connect(self._on_session_state_changed)
        self._vm.version_identified.connect(self._on_version_found)
        self._timeline_canvas.nodeClicked.connect(self._on_timeline_node_clicked)

    def _install_disabled_button_feedback(self) -> None:
        """Warn (non-modally) when a disabled button is clicked, with a helpful reason."""
        reasons = {
            "_scan_btn": "A scan is already running — wait for it to finish, or press Stop.",
            "_stop_btn": "No scan is running right now, so there is nothing to stop.",
            "_open_notepad_btn": "Select an incident row first — then you can open its log file.",
            "_ai_enhance_btn": "Select an incident row first to generate a technical summary.",
            "_incidents_capture_btn": "Switch to ‘Remote IP’ mode and enter the cabinet IP to capture the screen.",
            "_ram_clear_btn": "Enable Local mode with a detected slot/roulette install, or Remote IP with a host address.",
            "_live_toggle": "Live Watch isn’t available while a scan is in progress.",
            "_case_pack_btn": "A case pack is already being created — please wait for it to finish.",
            "_full_audit_btn": "A session audit is already running — please wait for it to finish.",
            "_btn_verify_sas": "A SAS verification is already running — please wait for it to finish.",
        }
        for attr, reason in reasons.items():
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setProperty("disabledReason", reason)
        self._disabled_click_warner = install_disabled_click_warner(
            self._warn_status_message
        )

    def _warn_status_message(self, message: str) -> None:
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(message, 6000)

    def _refresh_timeline_canvas(self) -> None:
        nodes = self._vm.state_nodes_for_timeline()
        total = len(nodes)
        key_fn = lambda n: (
            getattr(n, "timestamp_sort_key", 0.0),
            str(getattr(n, "log_file_path", "")),
            int(getattr(n, "line_number", 0)),
        )
        if total <= TIMELINE_SORT_MAX_NODES:
            ordered = sorted(nodes, key=key_fn)
        else:
            ordered = sorted(nodes[-TIMELINE_SORT_MAX_NODES:], key=key_fn)
        self._timeline_canvas.set_nodes(ordered, source_total=total)

    def _refresh_game_analytics_dashboard(self) -> None:
        if not hasattr(self, "_game_analytics_widget"):
            return
        nodes = self._vm.state_nodes_for_timeline()
        stats = calculate_session_analytics(nodes)
        self._game_analytics_widget.update_dashboard(stats)

    def _on_timeline_node_clicked(self, node: object) -> None:
        if not isinstance(node, StateNode):
            return
        t0 = float(node.timestamp_sort_key)
        t1 = float(node.end_timestamp_sort_key)
        eps = 1e-3
        if t1 <= t0 + eps:
            if node.duration_sec is not None and node.duration_sec > 0:
                t1 = t0 + float(node.duration_sec)
            else:
                t1 = t0 + 86_400.0
        else:
            t1 = max(t1, t0 + eps)
        self._vm.set_timeline_window(t0, t1)
        self._tabs.setCurrentIndex(0)
        self._status.setText(
            f"Incident table filtered to state «{node.state_name}» (time window)"
        )

    def _on_filter_busy(self, busy: bool) -> None:
        self._filter_busy.setText("Filtering…" if busy else "")

    def _on_custom_signatures(self) -> None:
        dlg = RuleEditorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            reload_rules_manager()

    def _on_settings_clicked(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._sync_notification_bell_from_settings()
        # Defer theme apply until the modal dialog has finished closing — applying a
        # global palette/stylesheet during exec() teardown can crash Qt on Windows.
        QTimer.singleShot(0, self._apply_saved_settings)

    def _on_help_clicked(self) -> None:
        HelpDialog(self).exec()

    def _apply_saved_settings(self) -> None:
        try:
            apply_theme(QApplication.instance(), SettingsManager.get_theme())
            self._on_application_palette_changed()
        except Exception:
            logger.exception("Failed to apply theme after settings save")
            QMessageBox.warning(
                self,
                "Settings",
                "Theme could not be applied. Other settings were saved.",
            )
            return
        if self._vm.is_scanning():
            self._fleet_load_from_db_sync()
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Refreshing fleet after settings…")
        schedule_fleet_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            max_workers=12,
            emitter=self._fleet_emitter,
        )

    def _on_session_toggle_clicked(self) -> None:
        if self._vm.is_session_active():
            self._vm.stop_session()
            return
        if self._session_clear_on_start_chk.isChecked():
            self._vm.clear()
            self._reset_incident_table_column_fit_state()
            self._table.clearSelection()
            sm = self._table.selectionModel()
            if sm is not None:
                sm.clearCurrentIndex()
            self._inspector_meta.clear()
            self._inspector_log.clear()
        self._vm.start_session()
        lt = self._live_thread
        if lt is not None and lt.isRunning():
            lt.request_align_tracked_files_to_eof()

    def _on_session_state_changed(self) -> None:
        _rec_pix = getattr(
            QStyle.StandardPixmap,
            "SP_MediaRecord",
            QStyle.StandardPixmap.SP_MediaPlay,
        )
        if self._vm.is_session_active():
            self._session_btn.setText("Stop Session")
            self._session_btn.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
            )
            self._session_timer.start()
            self._tick_session_elapsed()
        else:
            self._session_btn.setText("Start Session")
            self._session_btn.setIcon(self.style().standardIcon(_rec_pix))
            self._session_timer.stop()
            self._session_elapsed_lbl.setText("[--:--:--]")

    def _tick_session_elapsed(self) -> None:
        st = self._vm.session_start_time()
        if st is None:
            return
        secs = int((datetime.now(timezone.utc) - st).total_seconds())
        secs = max(0, secs)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self._session_elapsed_lbl.setText(f"[{h:02d}:{m:02d}:{s:02d}]")

    def _tick_live_watch_elapsed(self) -> None:
        if self._live_watch_started_monotonic is None:
            return
        secs = int(time.monotonic() - self._live_watch_started_monotonic)
        self._live_elapsed_lbl.setText(f"Live [{_format_hms_duration(secs)}]")

    def _reset_live_watch_elapsed_timer(self) -> None:
        self._live_watch_timer.stop()
        self._live_watch_started_monotonic = None
        self._live_elapsed_lbl.setText("Live [--:--:--]")

    def _refresh_dashboard(self) -> None:
        done = self._vm.files_scanned()
        total = self._vm.files_total()
        self._files_value.setText(str(done))
        if total > 0:
            self._files_hint.setText(f"of {total} log files")
        else:
            self._files_hint.setText("log files on disk")

        crit = self._vm.critical_count()
        self._crit_value.setText(str(crit))
        self._card_crit.setProperty("alert", crit > 0)
        self._card_crit.style().unpolish(self._card_crit)
        self._card_crit.style().polish(self._card_crit)

        if crit > 0:
            if self._crit_pulse.state() != QAbstractAnimation.State.Running:
                self._crit_pulse.start()
        else:
            self._crit_pulse.stop()
            eff = self._crit_value.graphicsEffect()
            if isinstance(eff, QGraphicsOpacityEffect):
                eff.setOpacity(1.0)

        self._game_value.setText(self._vm.last_active_game())

        self._on_version_found(self._vm.software_version_for_ai())

    def _on_version_found(self, version_str: str) -> None:
        if (
            not version_str
            or version_str == "[Version]"
            or version_str == PRODUCT_VERSION_PLACEHOLDER
        ):
            display_text = "Version: —"
        else:
            display_text = version_str
        self.software_version_label.setText(display_text)
        raw_pv = self._vm.software_version_product_raw()
        if raw_pv:
            self.software_version_label.setToolTip(
                "OneHand.exe — Product version (file metadata):\n"
                f"{raw_pv}\n\n"
                "Displayed label is normalized for export (RC from metadata when present)."
            )
        else:
            self.software_version_label.setToolTip(
                "Primary source (Windows): Product version from OneHand.exe beside the scan root "
                "(same as file Properties → Details).\n"
                "Fallback: log lines «OneHand.MainFrm - SlotMachine v…» / LogDaemon «Spawning v…»."
            )
        inc = self._vm.selected_incident()
        if isinstance(inc, Incident):
            self._on_selection_vm(inc)

    def _trigger_background_version_fetch(self, log_dir: str) -> None:
        root = (log_dir or "").strip()
        if not root:
            return
        self.software_version_label.setText("Fetching version...")
        self._version_fetch_seq += 1
        seq = self._version_fetch_seq

        def fetch() -> None:
            try:
                self._vm.refresh_current_software_version(root)
            except Exception:
                if seq == self._version_fetch_seq:
                    self._vm.version_identified.emit(PRODUCT_VERSION_PLACEHOLDER)

        threading.Thread(target=fetch, daemon=True).start()

    def _queue_version_fetch(self, log_dir: str) -> None:
        root = (log_dir or "").strip()
        if not root:
            return
        self._version_fetch_pending_path = root
        self._version_fetch_timer.start()

    def _flush_version_fetch(self) -> None:
        root = (self._version_fetch_pending_path or "").strip()
        self._version_fetch_pending_path = None
        if root:
            self._trigger_background_version_fetch(root)

    def _setup_tray_icon(self) -> None:
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        from gui.app_branding import app_icon

        self._tray = QSystemTrayIcon(app_icon(), self)
        self._tray.setToolTip("Log Investigator — live CRITICAL alerts")
        self._tray.show()

    def _stop_live_watch_ui(self) -> None:
        self._reset_live_watch_elapsed_timer()
        self._teardown_live_thread()
        if self._live_toggle.isChecked():
            self._live_toggle.blockSignals(True)
            self._live_toggle.setChecked(False)
            self._live_toggle.blockSignals(False)
        self._rec_pulse.stop()
        fx = self._rec_label.graphicsEffect()
        if isinstance(fx, QGraphicsOpacityEffect):
            fx.setOpacity(1.0)
        self._rec_label.setVisible(False)

    def _teardown_live_thread(self) -> None:
        t = self._live_thread
        if t is None:
            return
        t.requestInterruption()
        t.wait(5000)
        t.deleteLater()
        self._live_thread = None

    def _on_live_watch_toggled(self, on: bool) -> None:
        if on:
            root = self._path_edit.text().strip()
            if not root:
                QMessageBox.warning(self, "Live Watch", "Set a scan root first.")
                self._live_toggle.blockSignals(True)
                self._live_toggle.setChecked(False)
                self._live_toggle.blockSignals(False)
                return
            p = Path(root)
            try:
                ok = p.exists() and p.is_dir()
            except OSError:
                ok = False
            if not ok:
                QMessageBox.warning(
                    self,
                    "Live Watch",
                    f"Scan root is not reachable:\n{root}",
                )
                self._live_toggle.blockSignals(True)
                self._live_toggle.setChecked(False)
                self._live_toggle.blockSignals(False)
                return
            self._rec_label.setVisible(True)
            self._rec_pulse.start()
            self._live_thread = LiveWatchThread(str(p.resolve()), parent=self)
            self._live_thread.incidents_batch.connect(self._on_live_incidents_batch)
            self._live_thread.status_message.connect(self._status.setText)
            self._live_thread.start()
            self._live_watch_started_monotonic = time.monotonic()
            self._live_watch_timer.start()
            self._tick_live_watch_elapsed()
            self._status.setText("Live watch running…")
        else:
            self._rec_pulse.stop()
            fx = self._rec_label.graphicsEffect()
            if isinstance(fx, QGraphicsOpacityEffect):
                fx.setOpacity(1.0)
            self._rec_label.setVisible(False)
            self._reset_live_watch_elapsed_timer()
            self._teardown_live_thread()
            self._status.setText("Live watch stopped.")

    def _on_live_incidents_batch(self, batch: object) -> None:
        incs: list
        game_hint: str | None = None
        if isinstance(batch, tuple) and len(batch) == 2:
            incs, game_hint = batch[0], batch[1]
        elif isinstance(batch, list):
            incs = batch
        else:
            return
        if not incs and game_hint is None:
            return
        self._vm.append_live_incidents(incs, active_game_hint=game_hint)
        if not incs:
            return
        for inc in incs:
            if should_notify(inc):
                t, body = format_live_toast(inc)
                show_toast(t, body)
        if self._autoscroll_chk.isChecked():
            last = self._vm.filtered_row_count() - 1
            if last >= 0:
                idx = self._table_model.index(last, 0)
                self._table.setCurrentIndex(idx)
                self._table.scrollTo(idx, QTableView.ScrollHint.PositionAtBottom)

    def _on_live_critical_alert(self, filtered_row: int, incident: object) -> None:
        self._table_model.register_flash_row(filtered_row)
        tray = self._tray
        if tray is not None and self.isMinimized():
            snippet = getattr(incident, "line_snippet", "") or ""
            body = (snippet[:220] + "…") if len(snippet) > 220 else snippet
            tray.showMessage(
                "CRITICAL (live)",
                body,
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )

    def _default_local_scan_root(self) -> str:
        from network.goldclub_paths import discover_startup_scan_target

        remote_ip = str(
            self._settings.value("connection/remote_ip", DEFAULT_REMOTE_IP)
        )
        discovery = discover_startup_scan_target(remote_ip=remote_ip)
        if discovery.mode == "local":
            return discovery.scan_root
        saved = str(
            self._settings.value("connection/local_log_path", DEFAULT_LOCAL_LOG_ROOT)
        )
        return saved if Path(saved).is_dir() else DEFAULT_LOCAL_LOG_ROOT

    def _load_connection_settings(self) -> None:
        from network.goldclub_paths import discover_startup_scan_target

        remote_ip = str(
            self._settings.value("connection/remote_ip", DEFAULT_REMOTE_IP)
        )
        discovery = discover_startup_scan_target(remote_ip=remote_ip)
        self._radio_local.blockSignals(True)
        self._radio_remote.blockSignals(True)
        if discovery.mode == "remote":
            self._radio_remote.setChecked(True)
            self._radio_local.setChecked(False)
            self._ip_edit.setText(discovery.remote_ip or DEFAULT_REMOTE_IP)
        else:
            self._radio_local.setChecked(True)
            self._radio_remote.setChecked(False)
        self._radio_local.blockSignals(False)
        self._radio_remote.blockSignals(False)
        self._path_edit.setText(discovery.scan_root)
        self._apply_mode_to_widgets()
        self._autoscroll_chk.setChecked(
            bool(self._settings.value("live/autoscroll", False))
        )
        if discovery.game_kind in ("slot", "roulette"):
            self._status.setText(
                f"Auto-detected {discovery.game_kind} logs at {discovery.scan_root}"
            )
        elif discovery.mode == "remote":
            self._status.setText(
                f"No local game found — using remote logs at {discovery.scan_root}"
            )
        self._update_ram_clear_button_enabled()

    def _save_connection_settings(self) -> None:
        self._settings.setValue(
            "connection/mode",
            "remote" if self._radio_remote.isChecked() else "local",
        )
        self._settings.setValue("connection/remote_ip", self._ip_edit.text().strip())
        # Do not persist the read-only UNC string as the user's local path.
        if not self._radio_remote.isChecked():
            self._settings.setValue(
                "connection/local_log_path", self._path_edit.text().strip()
            )
        self._settings.setValue("live/autoscroll", self._autoscroll_chk.isChecked())

    def _apply_mode_to_widgets(self) -> None:
        remote = self._radio_remote.isChecked()
        self._ip_edit.setEnabled(remote)
        # Operators frequently need to paste a UNC path manually, even in Remote mode.
        # Keep the field editable; remote mode just *suggests* a resolved UNC value.
        self._path_edit.setReadOnly(False)
        # Keep these enabled in both modes so users can browse UNC shares manually.
        self._browse_btn.setEnabled(True)
        self._open_file_btn.setEnabled(True)
        if remote:
            self._path_edit.setPlaceholderText(r"Enter root directory or UNC path...")
        else:
            self._path_edit.setPlaceholderText(r"e.g. C:\Goldclub\var\log or D:\_LogFiles\log_DD_MM_YYYY")
        self._update_incidents_capture_button_enabled()
        self._update_ram_clear_button_enabled()
        self._sync_remote_ram_target_ip()

    def _sync_remote_ram_target_ip(self) -> None:
        if self._radio_remote.isChecked():
            self._vm.set_remote_ram_target_ip(self._ip_edit.text())
        else:
            self._vm.set_remote_ram_target_ip(None)

    def _update_incidents_capture_button_enabled(self) -> None:
        if not hasattr(self, "_incidents_capture_btn"):
            return
        remote = self._radio_remote.isChecked()
        self._incidents_capture_btn.setEnabled(remote and not self._remote_capture_busy)

    def _update_ram_clear_button_enabled(self) -> None:
        if not hasattr(self, "_ram_clear_btn"):
            return
        if self._ram_clear_busy:
            self._ram_clear_btn.setEnabled(False)
            return
        if os.name != "nt":
            self._ram_clear_btn.setEnabled(False)
            return
        if self._radio_remote.isChecked():
            self._ram_clear_btn.setEnabled(bool((self._ip_edit.text() or "").strip()))
            return
        from network.ram_clear import resolve_ram_clear_plan

        self._ram_clear_btn.setEnabled(resolve_ram_clear_plan() is not None)

    def _on_mode_toggled(self) -> None:
        self._apply_mode_to_widgets()
        if self._radio_local.isChecked():
            cur = self._path_edit.text().strip()
            if cur.startswith("\\\\") or not cur:
                lp = self._default_local_scan_root()
                self._path_edit.blockSignals(True)
                self._path_edit.setText(lp)
                self._path_edit.blockSignals(False)
        self._schedule_path_probe()

    def _on_path_text_changed(self) -> None:
        self._last_sas_verification_plaintext = ""
        self._last_sas_verification_summary = ""
        if not self._radio_remote.isChecked():
            self._schedule_path_probe()
            self._queue_version_fetch(self._path_edit.text())

    def _gather_path_resolve_args(self) -> tuple[str | None, str | None, bool]:
        remote = self._radio_remote.isChecked()
        if remote:
            return self._ip_edit.text().strip(), None, True
        return None, self._path_edit.text().strip(), False

    def _schedule_path_probe(self) -> None:
        self._probe_seq += 1
        self._probe_timer.start()

    def _apply_theme_styles(self) -> None:
        """Apply push-button / chip QSS from the active palette (light vs dark surfaces)."""
        bg_color = self.palette().color(QPalette.ColorRole.Window)
        is_dark_mode = bg_color.lightness() < 128

        if is_dark_mode:
            stylesheet = """
                QPushButton {
                    background-color: #3d3d3d;
                    color: #ffffff;
                    border: 1px solid #555555;
                    border-radius: 4px;
                    padding: 5px 15px;
                    min-height: 25px;
                }
                QPushButton:hover { background-color: #505050; border: 1px solid #0078d4; }
                QPushButton:pressed { background-color: #2b2b2b; }
                QPushButton:disabled { color: #777777; background-color: #2d2d2d; }

                QPushButton#full_ai_audit_btn {
                    background-color: #005a9e;
                    color: #ffffff;
                    font-weight: bold;
                }

                QToolButton[filter_chip="true"], QPushButton[filter_chip="true"] {
                    background-color: #252525;
                    color: #e0e0e0;
                    border: 1px solid #444444;
                    border-radius: 12px;
                    padding: 2px 10px;
                    min-height: 22px;
                }
                QToolButton[filter_chip="true"]:checked, QToolButton[filter_chip="true"]:hover {
                    background-color: #404040;
                    border: 1px solid #0078d4;
                }
                QToolButton[filter_chip="true"]:checked { font-weight: 600; }
                QPushButton[filter_chip="true"]:checked {
                    background-color: #404040;
                    border: 1px solid #0078d4;
                }

                QLineEdit#software_version_input {
                    background: #252525;
                    border: 1px solid #555;
                    color: white;
                    padding: 2px;
                }

                QPushButton#wait_shutdown_btn {
                    background-color: #444444;
                    color: #ffffff;
                    border: 1px solid #c7c7c7;
                    font-weight: 600;
                }
                QPushButton#wait_shutdown_btn:hover {
                    background-color: #555555;
                    border: 1px solid #ffffff;
                }
                QPushButton#close_anyways_btn[destructive="true"] {
                    background-color: #B91C1C;
                    color: #ffffff;
                    border: 1px solid #ef4444;
                    font-weight: 700;
                }
                QPushButton#close_anyways_btn[destructive="true"]:hover {
                    background-color: #991B1B;
                    border: 1px solid #f87171;
                }
            """
        else:
            stylesheet = """
                QPushButton {
                    background-color: #f0f0f0;
                    color: #000000;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    padding: 5px 15px;
                    min-height: 25px;
                }
                QPushButton:hover { background-color: #e5e5e5; border: 1px solid #0078d4; }
                QPushButton:pressed { background-color: #d4d4d4; }
                QPushButton:disabled { color: #a0a0a0; background-color: #f5f5f5; }

                QPushButton#full_ai_audit_btn {
                    background-color: #0078d4;
                    color: #ffffff;
                    font-weight: bold;
                }

                QToolButton[filter_chip="true"], QPushButton[filter_chip="true"] {
                    background-color: #ffffff;
                    color: #333333;
                    border: 1px solid #bbbbbb;
                    border-radius: 12px;
                    padding: 2px 10px;
                    min-height: 22px;
                }
                QToolButton[filter_chip="true"]:checked, QToolButton[filter_chip="true"]:hover {
                    background-color: #e0e0e0;
                    border: 1px solid #0078d4;
                }
                QToolButton[filter_chip="true"]:checked { font-weight: 600; }
                QPushButton[filter_chip="true"]:checked {
                    background-color: #e0e0e0;
                    border: 1px solid #0078d4;
                }

                QLineEdit#software_version_input {
                    background: #ffffff;
                    border: 1px solid #ccc;
                    color: black;
                    padding: 2px;
                }

                QPushButton#wait_shutdown_btn {
                    background-color: #e8e8e8;
                    color: #000000;
                    border: 1px solid #cccccc;
                    font-weight: 600;
                }
                QPushButton#wait_shutdown_btn:hover {
                    background-color: #d8d8d8;
                    border: 1px solid #0078d4;
                }
                QPushButton#close_anyways_btn[destructive="true"] {
                    background-color: #B91C1C;
                    color: #ffffff;
                    border: 1px solid #ef4444;
                    font-weight: 700;
                }
                QPushButton#close_anyways_btn[destructive="true"]:hover {
                    background-color: #991B1B;
                    border: 1px solid #f87171;
                }
            """

        self.setStyleSheet(stylesheet)

    def _apply_path_led_style(self) -> None:
        p = QApplication.palette()
        if self._path_led_state == "ok":
            c = text_success(p).name()
        elif self._path_led_state == "bad":
            c = text_danger(p).name()
        else:
            c = muted_text(p).name()
        self._led.setStyleSheet(f"color: {c}; font-size: 20px;")
        self._led_status.setStyleSheet(f"color: {c};")

    def _apply_rec_label_style(self) -> None:
        c = text_danger(QApplication.palette()).name()
        self._rec_label.setStyleSheet(
            f"color: {c}; font-size: 15px; font-weight: 800;"
        )

    def _timeline_legend_markup(self) -> str:
        p = self.palette()
        cg = text_success(p).name()
        cy = syntax_warning(p).name()
        cr = syntax_critical(p).name()
        return (
            "Segments from <b>Change MachineState from … to …</b> (same parse pass as scan). "
            f"<span style='color:{cg}'>Green</span> = no incidents in range; "
            f"<span style='color:{cy}'>Yellow</span> = LOW/MEDIUM; "
            f"<span style='color:{cr}'>Red</span> = CRITICAL. "
            "Click a block to filter the incident table to that time window."
        )

    def _refresh_timeline_legend(self) -> None:
        if getattr(self, "_timeline_legend_label", None) is None:
            return
        self._timeline_legend_label.setText(self._timeline_legend_markup())

    def _on_application_palette_changed(self) -> None:
        if self._palette_refresh_busy:
            return
        self._palette_refresh_busy = True
        try:
            self._apply_theme_styles()
            self._apply_path_led_style()
            self._apply_rec_label_style()
            p = self.palette()
            self._inspector_meta_highlighter.reapply_formats(p)
            self._inspector_log_highlighter.reapply_formats(p)
            self._refresh_timeline_legend()
        finally:
            self._palette_refresh_busy = False

    def _fire_path_probe(self) -> None:
        seq = self._probe_seq
        tip, lp, remote = self._gather_path_resolve_args()
        schedule_path_resolve(
            self._vm.thread_pool(),
            tip,
            lp,
            remote,
            seq,
            self._path_probe_emitter,
        )

    def _on_path_probe_finished(self, result: object, seq: int) -> None:
        if seq != self._probe_seq:
            return
        if not isinstance(result, ResolvedScanPath):
            return
        r = result
        if self._radio_remote.isChecked() and r.path:
            self._path_edit.blockSignals(True)
            self._path_edit.setText(r.path)
            self._path_edit.blockSignals(False)

        if not r.path:
            self._path_led_state = "muted"
            self._led_status.setText(r.error_hint or "Configure host")
            self._apply_path_led_style()
            return

        if r.exists:
            self._path_led_state = "ok"
            self._led_status.setText("Reachable")
            self._queue_version_fetch(r.path)
        else:
            self._path_led_state = "bad"
            self._led_status.setText("Unreachable")
        self._apply_path_led_style()

    def _on_path_prescan_finished(self, result: object, seq: int) -> None:
        if seq != self._prescan_seq:
            return
        if not isinstance(result, ResolvedScanPath):
            return
        r = result
        self._scan_btn.setEnabled(True)
        self._status.setText("Ready")
        if not r.path:
            QMessageBox.warning(
                self,
                "Scan",
                r.error_hint or "Invalid scan configuration.",
            )
            return
        if not r.exists:
            QMessageBox.warning(
                self,
                "Scan",
                r.error_hint or f"Cannot reach scan root:\n{r.path}",
            )
            return
        self._save_connection_settings()
        self._begin_scan_worker([r.path])

    def _sync_time_filter_button(self) -> None:
        active = (
            self._global_scan_start is not None and self._global_scan_end is not None
        )
        if active:
            self._btn_time_filter.setText("⏱ Time Filter: Active")
            self._btn_time_filter.setStyleSheet(
                "background-color: #e6ffe6; font-weight: 600; padding: 4px 8px;"
            )
        else:
            self._btn_time_filter.setText("⏱ Time Filter: Off")
            self._btn_time_filter.setStyleSheet("")

    def _on_time_filter_clicked(self) -> None:
        dlg = TimeRangeScanDialog(
            self,
            current_start=self._global_scan_start,
            current_end=self._global_scan_end,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._global_scan_start, self._global_scan_end = dlg.get_time_bounds()
        self._sync_time_filter_button()

    def _pick_directory(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            "Select log root directory",
            self._path_edit.text() or DEFAULT_LOCAL_LOG_ROOT,
        )
        if d:
            self._path_edit.setText(d)

    def _on_open_log_file_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        # Works in both Local and Remote mode — QFileDialog can browse UNC shares.
        start_dir = self._path_edit.text().strip()
        if not start_dir and self._radio_remote.isChecked():
            ip = (self._ip_edit.text() or "").strip()
            if ip:
                try:
                    start_dir = format_unc_log_root(ip)
                except Exception:
                    start_dir = ""
        if not start_dir:
            start_dir = DEFAULT_LOCAL_LOG_ROOT
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open log file",
            start_dir,
            _LOG_OPEN_FILTER,
        )
        if not path:
            return
        self._open_log_files([path])

    def _open_log_files(self, paths: list[str]) -> None:
        """Parse one or more dropped/selected log files (sets Scan root to the first parent)."""
        resolved = [str(Path(p).resolve()) for p in paths if p]
        if not resolved:
            return
        parent = str(Path(resolved[0]).parent)
        if parent:
            self._path_edit.setText(parent)
        self._begin_scan_worker(resolved)

    def _collect_dropped_local_paths(self, event: QDropEvent) -> list[str]:
        """Return local file/dir paths from a drop, keeping only supported log types."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        out: list[str] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_dir():
                out.append(str(p))
            elif p.suffix.lower() in _DROP_LOG_SUFFIXES:
                out.append(str(p))
        return out

    def _drag_has_supported_paths(self, event: QDragEnterEvent | QDragMoveEvent) -> bool:
        mime = event.mimeData()
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_dir() or p.suffix.lower() in _DROP_LOG_SUFFIXES:
                return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt override)
        if self._drag_has_supported_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 (Qt override)
        if self._drag_has_supported_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt override)
        if self._worker and self._worker.isRunning():
            self._status.setText("Scan in progress — wait for it to finish before dropping files.")
            event.ignore()
            return
        paths = self._collect_dropped_local_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()

        dirs = [p for p in paths if Path(p).is_dir()]
        files = [p for p in paths if not Path(p).is_dir()]

        # A single dropped folder becomes the scan root (full recursive scan).
        if dirs and not files:
            self._path_edit.setText(dirs[0])
            self._begin_scan_worker([dirs[0]])
            return

        # Otherwise parse the dropped log files directly (ignore any mixed-in folders).
        if files:
            self._open_log_files(files)

    def _start_scan(self, persist_to_db: bool = False) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._persist_db_after_scan = persist_to_db
        self._prescan_seq += 1
        seq = self._prescan_seq
        tip, lp, remote = self._gather_path_resolve_args()
        self._scan_btn.setEnabled(False)
        self._status.setText("Validating path…")
        schedule_path_resolve(
            self._vm.thread_pool(),
            tip,
            lp,
            remote,
            seq,
            self._path_prescan_emitter,
        )

    def _begin_scan_worker(self, roots: list[str]) -> None:
        self._last_scan_roots = list(roots)
        self._stop_live_watch_ui()
        self._live_toggle.setEnabled(False)
        self._vm.clear()
        self._reset_incident_table_column_fit_state()
        self._table.clearSelection()
        sm = self._table.selectionModel()
        if sm is not None:
            sm.clearCurrentIndex()
        self._inspector_meta.clear()
        self._inspector_log.clear()
        # Determine a stable software version string early for defect ticket titles (background).
        if roots:
            self._queue_version_fetch(roots[0])
        self._vm.set_scanning(True)
        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # busy/indeterminate until enumeration completes
        self._scan_total_files = 0
        self._scan_done_files = 0
        self._status.setText("Scanning…")

        self._pending_parse_results.clear()
        self._parse_drain_scheduled = False
        self._scan_complete_pending = False
        self._scan_started_monotonic = time.monotonic()

        self._worker = ScanWorker(
            roots,
            self,
            scan_start_time=self._global_scan_start,
            scan_end_time=self._global_scan_end,
        )
        self._worker.enumeration_done.connect(self._on_enum_done)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_parsed.connect(self._on_file_parsed_yielding)
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.scan_failed.connect(self._on_scan_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_enum_done(self, total: int) -> None:
        self._vm.set_scan_totals(total)
        self._scan_total_files = total
        self._scan_done_files = 0
        if total > 1:
            # Determinate: the bar advances as files *finish* parsing (_drain_one_parsed_file),
            # so it never reads 100% while a file is still being scanned.
            self._progress.setRange(0, total)
            self._progress.setValue(0)
        else:
            # A single file has no per-file granularity — show a busy/indeterminate bar for
            # the whole parse instead of a misleading 100%.
            self._progress.setRange(0, 0)

    def _on_file_progress(self, path: str, index: int, total: int) -> None:
        name = Path(path).name or path
        self._status.setText(f"Scanning [{index}/{total}] {name}…")

    def _on_file_parsed_yielding(self, result: object) -> None:
        """Apply one file at a time with a short gap so the UI thread can repaint (UNC scans)."""
        self._pending_parse_results.append(result)
        if not self._parse_drain_scheduled:
            self._parse_drain_scheduled = True
            QTimer.singleShot(SCAN_UI_YIELD_MS, self._drain_one_parsed_file)

    def _drain_one_parsed_file(self) -> None:
        if self._pending_parse_results:
            self._vm.append_file_results(self._pending_parse_results.pop(0))
            self._scan_done_files += 1
            if self._scan_total_files > 1:
                self._progress.setValue(
                    min(self._scan_done_files, self._scan_total_files)
                )
        if self._pending_parse_results:
            QTimer.singleShot(SCAN_UI_YIELD_MS, self._drain_one_parsed_file)
        else:
            self._parse_drain_scheduled = False
            if self._scan_complete_pending:
                self._scan_complete_pending = False
                self._finalize_scan_ui()

    def _on_scan_finished(self) -> None:
        self._scan_complete_pending = True
        if not self._pending_parse_results and not self._parse_drain_scheduled:
            self._scan_complete_pending = False
            self._finalize_scan_ui()

    def _finalize_scan_ui(self) -> None:
        self._vm.set_scanning(False)
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.setVisible(False)
        self._live_toggle.setEnabled(True)
        # One bounded, log-only CLR/OS lookup per scan (LogDaemon truncates the banner).
        roots = getattr(self, "_last_scan_roots", None)
        primary_root = roots[0] if roots else None
        if self._vm.enrich_environment_fingerprint(primary_root):
            inc = self._vm.selected_incident()
            if inc is not None:
                self._on_selection_vm(inc)
        dur = ""
        if self._scan_started_monotonic is not None:
            elapsed = time.monotonic() - self._scan_started_monotonic
            self._scan_started_monotonic = None
            dur = _format_scan_wall_duration(elapsed)
            self._scan_last_duration_lbl.setText(f"Last scan: {dur}")
        msg = f"Scan finished in {dur}." if dur else "Scan finished."
        self._status.setText(msg)
        sb = self.statusBar()
        sb.showMessage(msg, 12_000)
        self._refresh_known_issues_session_counts()
        self._worker = None
        if self._persist_db_after_scan:
            self._persist_db_after_scan = False
            self._schedule_persist_scan_to_db()

    def _on_scan_failed(self, msg: str) -> None:
        self._pending_parse_results.clear()
        self._parse_drain_scheduled = False
        self._scan_complete_pending = False
        self._vm.set_scanning(False)
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.setVisible(False)
        self._live_toggle.setEnabled(True)
        dur = ""
        if self._scan_started_monotonic is not None:
            elapsed = time.monotonic() - self._scan_started_monotonic
            self._scan_started_monotonic = None
            dur = _format_scan_wall_duration(elapsed)
            self._scan_last_duration_lbl.setText(f"Last scan: {dur} (failed)")
        fail_status = f"Scan failed after {dur}." if dur else "Scan failed."
        self._status.setText(fail_status)
        self.statusBar().showMessage(fail_status, 12_000)
        self._worker = None
        QMessageBox.critical(self, "Scan failed", msg)

    def _stop_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._status.setText("Stopping…")

    def _setup_keyboard_shortcuts(self) -> None:
        self._sc_focus_filter = QShortcut(QKeySequence("Ctrl+F"), self)
        self._sc_focus_filter.activated.connect(self._on_shortcut_focus_filter)

        self._sc_session_toggle = QShortcut(QKeySequence("Ctrl+R"), self)
        self._sc_session_toggle.activated.connect(self._on_session_toggle_clicked)

        self._sc_create_case_pack = QShortcut(QKeySequence("Ctrl+S"), self)
        self._sc_create_case_pack.activated.connect(self._on_shortcut_create_case_pack)

        self._sc_filter_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self._filter_edit)
        self._sc_filter_escape.activated.connect(self._on_filter_escape)

    def _on_shortcut_focus_filter(self) -> None:
        self._tabs.setCurrentIndex(0)
        self._filter_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._filter_edit.selectAll()

    def _on_shortcut_create_case_pack(self) -> None:
        if self._case_pack_btn.isEnabled():
            self._on_create_case_pack_clicked()

    def _on_filter_escape(self) -> None:
        if self._filter_edit.text().strip():
            self._filter_edit.clear()
        else:
            self._vm.clear_quick_filter_chips()

    def _on_table_current_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        self._sync_inspector_to_table_index(current)

    def _sync_inspector_to_table_index(self, current: QModelIndex) -> None:
        if not current.isValid():
            self._vm.set_selected_incident(None)
            return
        inc = self._table_model.incident_for_index(current)
        self._vm.set_selected_incident(inc)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        inc = self._table_model.incident_for_index(idx)
        if inc is None:
            return
        menu = QMenu(self)
        act_context = QAction("Show Surrounding Context", self)
        act_context.triggered.connect(
            lambda checked=False, i=inc: self._on_show_surrounding_context(i)
        )
        menu.addAction(act_context)
        act_ignore_notif = QAction("Ignore this message for notifications", self)
        act_ignore_notif.setCheckable(True)
        fp = notification_ignore_fingerprint(inc)
        msg = inc.message
        ignored = any(sub and sub in msg for sub in SettingsManager.get_notification_blacklist())
        act_ignore_notif.setChecked(bool(ignored))
        act_ignore_notif.toggled.connect(
            lambda checked, i=inc, f=fp: self._on_ignore_notification_for_incident(i, checked, f)
        )
        menu.addAction(act_ignore_notif)
        act_edit = QAction("Add/Edit Bookmark…", self)
        act_edit.triggered.connect(lambda checked=False, i=inc: self._bookmark_add_edit(i))
        menu.addAction(act_edit)
        if self._vm.is_bookmarked(inc.id):
            act_rm = QAction("Remove Bookmark", self)
            act_rm.triggered.connect(
                lambda checked=False, iid=inc.id: self._vm.remove_bookmark(iid)
            )
            menu.addAction(act_rm)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_show_surrounding_context(self, inc: Incident) -> None:
        unfiltered = self._vm.unfiltered_incidents()
        if not any(x.id == inc.id for x in unfiltered):
            QMessageBox.warning(
                self,
                "Surrounding context",
                "This incident is not in the current scan buffer.",
            )
            return
        IncidentContextDialog(inc, unfiltered, self).exec()

    def _update_notif_bell_tooltip(self) -> None:
        on = self._notif_bell_btn.isChecked()
        self._notif_bell_btn.setToolTip(
            "Live Windows toasts for FATAL/CRITICAL while Live Watch is on. Click to mute."
            if on
            else "Notifications muted. Click to enable live FATAL/CRITICAL toasts."
        )

    def _sync_notification_bell_from_settings(self) -> None:
        self._notif_bell_btn.blockSignals(True)
        self._notif_bell_btn.setChecked(SettingsManager.get_notifications_enabled())
        self._notif_bell_btn.blockSignals(False)
        self._update_notif_bell_tooltip()

    def _on_notifications_bell_toggled(self, on: bool) -> None:
        SettingsManager.set_notifications_enabled(on)
        self._update_notif_bell_tooltip()
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(
                "Live notifications enabled." if on else "Live notifications disabled.",
                4000,
            )

    def _on_ignore_notification_for_incident(
        self, inc: Incident, checked: bool, fingerprint: str
    ) -> None:
        if checked:
            SettingsManager.add_notification_blacklist_entry(fingerprint)
        else:
            SettingsManager.remove_notification_blacklist_entry(fingerprint)
        sb = self.statusBar()
        if sb is not None:
            sb.showMessage(
                "This log line pattern will no longer trigger live notifications."
                if checked
                else "This log line pattern may trigger live notifications again.",
                5000,
            )

    def _bookmark_add_edit(self, inc: Incident) -> None:
        prev = self._vm.get_bookmark_note(inc.id) or ""
        dlg = BookmarkDialog(self, initial_note=prev)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._vm.add_bookmark(inc.id, dlg.note())

    def _refresh_session_timeline(self) -> None:
        self._session_timeline.set_incidents(self._vm.incidents_for_session_timeline())
        if not self._vm.has_session_time_slice():
            self._session_timeline.clear_brush_visual()

    def _on_session_timeline_incident_clicked(self, row: int) -> None:
        incs = self._vm.incidents_for_session_timeline()
        if row < 0 or row >= len(incs):
            return
        target_id = incs[row].id
        n = self._table_model.rowCount()
        for r in range(n):
            inc = self._vm.incident_at_filtered_row(r)
            if inc is not None and inc.id == target_id:
                self._table.selectRow(r)
                idx = self._table_model.index(r, 0)
                self._table.scrollTo(idx, QTableView.ScrollHint.PositionAtCenter)
                return

    def _refresh_known_issues_session_counts(self) -> None:
        incidents = list(self._vm.all_incidents or [])
        counts = count_known_issue_matches(incidents)
        self._known_issues_panel.set_session_counts(counts, len(incidents))

    def _on_selection_vm(self, incident: object) -> None:
        if not isinstance(incident, Incident):
            self._known_issues_panel.set_selected_incident(None)
            self._inspector_meta.clear()
            self._inspector_log_base = ""
            self._inspector_log.clear()
            self._open_notepad_btn.setEnabled(False)
            self._ai_enhance_btn.setEnabled(False)
            return
        self._known_issues_panel.set_selected_incident(incident)
        # Async log-RAM parse for CRITICAL incidents (cached; runs once per incident).
        self._vm.request_event_ram(incident)
        self._inspector_meta.setPlainText(self._build_meta_text(incident))
        path_ok = bool((incident.log_file_path or "").strip())
        self._open_notepad_btn.setEnabled(path_ok)
        self._ai_enhance_btn.setEnabled(True)
        self._stack_seq += 1
        seq = self._stack_seq
        self._inspector_log_base = ""
        self._inspector_log.setPlainText("Loading log context…")
        schedule_stack_load(
            self._vm.thread_pool(),
            Path(incident.log_file_path),
            incident.line_number,
            seq,
            self._stack_emitter,
        )

    def _build_meta_text(self, incident: Incident) -> str:
        vstat = getattr(incident, "validation_status", None)
        vdet = getattr(incident, "validation_detail", None)
        meta_lines: list[str] = []
        meta_lines.extend(self._vm.environment_fingerprint_lines())
        meta_lines.extend(self._event_ram_meta_lines(incident))
        meta_lines.append("")
        meta_lines.extend(
            [
                f"Timestamp:     {incident.timestamp_display()}",
                f"Game / theme:  {incident.game}",
                f"Severity:      {incident.severity}",
                f"Error type:    {incident.error_type}",
                (f"Validation:    {vstat or '—'}" + (f"\n{vdet}" if vdet else "")),
                f"Probable cause:\n{incident.probable_cause}",
                "",
                f"Log file:\n{incident.log_file_path}",
                f"Line:          {incident.line_number}",
                "",
                "First cause (lookback):",
                f"  Line {incident.first_cause_line or '—'}",
                f"  {incident.first_cause_snippet or '—'}",
                "",
                f"Matched line:\n{incident.line_snippet}",
            ]
        )
        return "\n".join(meta_lines)

    @staticmethod
    def _fmt_mb(value: float | None) -> str:
        return "—" if value is None else f"{value:,.0f} MB"

    @staticmethod
    def _fmt_gb(value_mb: float | None) -> str:
        return "—" if value_mb is None else f"{value_mb / 1024.0:.1f} GB"

    @staticmethod
    def _clean_sample_ts(ts: str | None) -> str:
        """``2026-06-04T08:38:39.846+01:00`` -> ``2026-06-04 08:38:39`` (readable)."""
        if not ts:
            return "—"
        s = ts.strip().replace("T", " ")
        return s[:19] if len(s) >= 19 else s

    @staticmethod
    def _humanize_ram_delta(delta_sec: float | None) -> str:
        """Plain-language gap between the memory sample and the error."""
        if delta_sec is None:
            return ""
        s = int(round(abs(delta_sec)))
        if s <= 1:
            return "at the moment of the error"
        if s < 60:
            body = f"{s}s"
        elif s < 3600:
            body = f"{s // 60}m {s % 60}s"
        else:
            body = f"{s // 3600}h {(s % 3600) // 60}m"
        return f"{body} {'after' if delta_sec >= 0 else 'before'} the error"

    def _proc_mem_display(self, proc_mb: float | None, total_mb: float | None) -> str:
        """Process memory label (Task Manager 'Committed'-style); flags virtual when > physical RAM."""
        if proc_mb is None:
            return "—"
        text = f"{self._fmt_gb(proc_mb)} committed"
        if total_mb and proc_mb > total_mb:
            text += " (virtual)"
        return text

    def _live_ram_meta_lines(self, incident: Incident) -> list[str]:
        """Near-real-time RAM captured at detection during Live Watch (preferred when present).

        Values come from a live WMIC snapshot, so they line up with Task Manager *now*: the
        system figures match Performance > Memory and OneHand is its working set (the
        Processes 'Memory' column), not the committed/virtual figure the logs record.
        """
        if not getattr(incident, "live_ram_captured_ts", None):
            return []
        total = incident.live_ram_total_mb
        if total is None:
            return []
        used = incident.live_ram_used_mb
        free = (total - used) if (used is not None) else None
        pct = incident.live_ram_used_pct
        pct_s = f" ({pct:.0f}%)" if pct is not None else ""
        proc = incident.live_ram_process_mb
        proc_s = f"{self._fmt_gb(proc)} (working set)" if proc is not None else "—"
        when = self._clean_sample_ts(incident.live_ram_captured_ts)
        delta = self._humanize_ram_delta(incident.live_ram_delta_sec)
        when_line = when + (f"  ({delta})" if delta else "")
        return [
            "RAM at error (LIVE snapshot — Task Manager view, captured at detection):",
            f"  In use:      {self._fmt_gb(used)} / {self._fmt_gb(total)}{pct_s}",
            f"  Available:   {self._fmt_gb(free)}",
            f"  OneHand:     {proc_s}",
            f"  Captured:    {when_line}",
        ]

    def _event_ram_meta_lines(self, incident: Incident) -> list[str]:
        """RAM-at-event block for the root-cause inspector (CRITICAL incidents only).

        Prefers a near-real-time live snapshot (Live Watch) when available; otherwise falls
        back to the historical value the EGM *logged at the incident's time*. The historical
        block mirrors Task Manager's Performance > Memory tab (In use / Available / Total, GB)
        but is a snapshot from the past, so it won't match the cabinet's current reading.
        """
        if (incident.severity or "").strip().upper() != "CRITICAL":
            return []
        live = self._live_ram_meta_lines(incident)
        if live:
            return live
        status = getattr(incident, "event_ram_status", None)
        if status is None:
            return ["RAM at event:  fetching from EGM logs…"]
        if status != "ok" or incident.event_ram_total_mb is None:
            return ["RAM at event:  no memory sample was logged near this time"]
        total = incident.event_ram_total_mb
        used = incident.event_ram_used_mb
        free = incident.event_ram_free_mb
        pct = (used / total * 100.0) if (used is not None and total) else None
        pct_s = f" ({pct:.0f}%)" if pct is not None else ""
        when = self._clean_sample_ts(incident.event_ram_sample_ts)
        delta = self._humanize_ram_delta(incident.event_ram_delta_sec)
        when_line = when + (f"  ({delta})" if delta else "")
        return [
            "RAM at event (Task Manager view — EGM log snapshot, historical not live):",
            f"  In use:      {self._fmt_gb(used)} / {self._fmt_gb(total)}{pct_s}",
            f"  Available:   {self._fmt_gb(free)}",
            f"  OneHand:     {self._proc_mem_display(incident.event_process_mb, total)}",
            f"  Sampled:     {when_line}",
        ]

    def _event_ram_log_banner(self, incident: object) -> str | None:
        """One-line RAM summary prepended to the log-context panel for CRITICAL incidents."""
        if not isinstance(incident, Incident):
            return None
        if (incident.severity or "").strip().upper() != "CRITICAL":
            return None
        if getattr(incident, "live_ram_captured_ts", None) and incident.live_ram_total_mb is not None:
            total = incident.live_ram_total_mb
            used = incident.live_ram_used_mb
            free = (total - used) if (used is not None) else None
            proc = incident.live_ram_process_mb
            pct = incident.live_ram_used_pct
            pct_s = f" ({pct:.0f}%)" if pct is not None else ""
            when = self._clean_sample_ts(incident.live_ram_captured_ts)
            delta = self._humanize_ram_delta(incident.live_ram_delta_sec)
            when_s = when + (f", {delta}" if delta else "")
            proc_s = (
                f"{self._fmt_gb(proc)} working set" if proc is not None else "—"
            )
            return (
                f"[LIVE RAM at error, captured {when_s}]  "
                f"In use {self._fmt_gb(used)} / {self._fmt_gb(total)}{pct_s}"
                f"  ·  Available {self._fmt_gb(free)}"
                f"  ·  OneHand {proc_s}"
            )
        if getattr(incident, "event_ram_status", None) != "ok" or incident.event_ram_total_mb is None:
            return None
        total = incident.event_ram_total_mb
        used = incident.event_ram_used_mb
        free = incident.event_ram_free_mb
        proc = incident.event_process_mb
        pct = (used / total * 100.0) if (used is not None and total) else None
        pct_s = f" ({pct:.0f}%)" if pct is not None else ""
        when = self._clean_sample_ts(incident.event_ram_sample_ts)
        delta = self._humanize_ram_delta(incident.event_ram_delta_sec)
        when_s = when + (f", {delta}" if delta else "")
        return (
            f"[Historical RAM (EGM log) sampled {when_s}]  "
            f"In use {self._fmt_gb(used)} / {self._fmt_gb(total)}{pct_s}"
            f"  ·  Available {self._fmt_gb(free)}"
            f"  ·  OneHand {self._proc_mem_display(proc, total)}"
        )

    def _render_inspector_log(self) -> None:
        banner = self._event_ram_log_banner(self._vm.selected_incident())
        base = self._inspector_log_base
        if banner:
            self._inspector_log.setPlainText(f"{banner}\n{'─' * 60}\n{base}")
        else:
            self._inspector_log.setPlainText(base)

    def _on_incident_event_ram_updated(self, incident_id: str) -> None:
        sel = self._vm.selected_incident()
        if not isinstance(sel, Incident) or sel.id != incident_id:
            return
        inc = self._vm.incident_by_id(incident_id) or sel
        self._inspector_meta.setPlainText(self._build_meta_text(inc))
        self._render_inspector_log()

    def _on_stack_loaded(self, seq: int, text: str) -> None:
        if seq != self._stack_seq:
            return
        self._inspector_log_base = text
        self._render_inspector_log()

    def _on_ai_enhance_clicked(self) -> None:
        inc = self._vm.selected_incident()
        if not isinstance(inc, Incident):
            return
        api_key = SettingsManager.get_gemini_api_key()
        ai_provider = SettingsManager.get_ai_provider()
        groq_api_key = SettingsManager.get_groq_api_key()
        openrouter_api_key = SettingsManager.get_openrouter_api_key()
        venice_api_key = SettingsManager.get_venice_api_key()
        software_version = self._vm.software_version_for_ai()
        # Error details: current inspector meta + loaded stack/context view.
        inspector_log_text = self._inspector_log.toPlainText()
        error_details = (self._inspector_meta.toPlainText() + "\n\n" + inspector_log_text).strip()
        if (self._last_sas_verification_summary or "").strip():
            error_details = (
                "SAS / accounting verification (latest):\n"
                f"{self._last_sas_verification_summary.strip()}\n\n---\n\n"
                + error_details
            )

        # Include the first 10 lines of the log file so analysis can extract SlotMachine version headers.
        header_lines: list[str] = []
        log_path_raw = (getattr(inc, "log_file_path", "") or "").strip()
        if log_path_raw:
            try:
                p = Path(os.path.normpath(log_path_raw))
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    for _ in range(10):
                        line = f.readline()
                        if not line:
                            break
                        header_lines.append(line.rstrip("\n\r"))
            except Exception:
                header_lines = []

        # Preceding timeline events: take up to N state nodes strictly before the incident timestamp.
        nodes = self._vm.state_nodes_for_timeline()
        target = getattr(inc, "timestamp_sort_key", None)
        if target is None and inc.timestamp is not None:
            try:
                target = inc.timestamp.timestamp()
            except Exception:
                target = None
        preceding: list[str] = []
        if target is not None:
            # Walk from the end (nodes are already chronologically sorted)
            for n in reversed(nodes):
                try:
                    sk = float(getattr(n, "timestamp_sort_key", 0.0) or 0.0)
                except Exception:
                    continue
                if sk >= float(target):
                    continue
                ts = getattr(n, "timestamp", None) or ""
                prev = getattr(n, "previous_state", None)
                state = getattr(n, "state_name", "") or ""
                line = f"{ts} | {prev or '—'} -> {state}"
                preceding.append(line.strip())
                if len(preceding) >= 18:
                    break
            preceding.reverse()

        # Keep payload bounded.
        if len(error_details) > 9000:
            error_details = error_details[:9000] + "\n…(truncated)…"
        if preceding:
            preceding = [x[:500] for x in preceding]

        # "Context" lines: use the loaded log context view (typically contains ~50 lines around incident).
        context_lines = [ln.rstrip() for ln in inspector_log_text.splitlines() if ln.strip()]
        context_lines = context_lines[-60:]  # keep bounded; emphasis is "immediately preceding"

        # Combine: header + gap + context + gap + state timeline (if any)
        context_for_ai: list[str] = []
        if header_lines:
            context_for_ai.extend(header_lines)
            context_for_ai.append("... [gap] ...")
        if context_lines:
            context_for_ai.extend(context_lines)
        if preceding:
            context_for_ai.append("... [gap] ...")
            context_for_ai.extend(preceding)

        self._ai_enhance_btn.setEnabled(False)
        self._ai_enhance_btn.setText("Processing…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        dlg = QDialog(self)
        dlg.setWindowTitle("Defect Ticket")
        dlg.resize(900, 680)
        v = QVBoxLayout(dlg)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_host = QWidget()
        fields_layout = QVBoxLayout(scroll_host)
        fields_layout.setContentsMargins(10, 10, 10, 10)
        fields_layout.setSpacing(10)
        scroll.setWidget(scroll_host)
        v.addWidget(scroll, stretch=1)

        placeholder = CopyableFieldWidget(
            "Status",
            "Processing Session Data (please wait)…",
            parent=scroll_host,
        )
        placeholder.copy_btn.setEnabled(False)
        fields_layout.addWidget(placeholder)
        fields_layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = QPushButton("Copy full ticket")
        copy_btn.setEnabled(False)
        full_ticket_text_box: dict[str, str] = {"text": ""}
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(full_ticket_text_box["text"])
        )
        close_btn = QPushButton("Close")
        close_btn.setEnabled(False)
        close_btn.clicked.connect(dlg.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        v.addLayout(row)

        # Parent on main window so closing the dialog does not delete the emitter while
        # ``QThreadPool`` work is still running (avoids RuntimeError: Signal source has been deleted).
        emitter = AiEnhanceEmitter(self)

        def _on_done(_ok: bool, text: str) -> None:
            try:
                if not Shiboken.isValid(dlg):
                    return
                raw = strip_markdown_bolding(text or "").strip()
                parts = parse_ai_response(raw)

                # Clear existing widgets (including stretch)
                while fields_layout.count():
                    item = fields_layout.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)

                ordered = [
                    ("Title", parts.get("Title", "")),
                    ("Key details", parts.get("Key details", "")),
                    ("Actual result", parts.get("Actual result", "")),
                    ("Expected result", parts.get("Expected result", "")),
                ]
                any_content = any((c or "").strip() for _, c in ordered)
                if not any_content:
                    ordered = [("Technical Summary", raw)]

                tracking = _collect_related_tracking([inc])
                if tracking.strip():
                    ordered.append(("Related tracking", tracking))

                # Render fields + build full text
                full_lines: list[str] = []
                for name, content in ordered:
                    fields_layout.addWidget(
                        CopyableFieldWidget(name, content, parent=scroll_host)
                    )
                    full_lines.append(f"{name}:\n{(content or '').strip()}\n")
                fields_layout.addStretch(1)

                full_ticket_text_box["text"] = ("\n".join(full_lines)).strip() or raw
            finally:
                QApplication.restoreOverrideCursor()
                self._ai_enhance_btn.setEnabled(True)
                self._ai_enhance_btn.setText("Generate Technical Summary")
                if Shiboken.isValid(close_btn):
                    close_btn.setEnabled(True)
                if Shiboken.isValid(copy_btn):
                    copy_btn.setEnabled(True)
                emitter.deleteLater()

        emitter.finished.connect(_on_done, Qt.ConnectionType.SingleShotConnection)
        schedule_ai_enhance(
            self._vm.thread_pool(),
            error_details=error_details,
            preceding=context_for_ai,
            software_version=software_version,
            api_key=api_key,
            ai_provider=ai_provider,
            groq_api_key=groq_api_key,
            openrouter_api_key=openrouter_api_key,
            venice_api_key=venice_api_key,
            emitter=emitter,
        )
        dlg.exec()

    def _on_full_audit_clicked(self) -> None:
        incidents = list(self._vm.all_incidents or [])
        if not incidents:
            QMessageBox.information(self, "Full Session Audit", "No errors to audit.")
            return
        software_version = self._vm.software_version_for_ai()

        lines: list[str] = []
        for inc in incidents:
            try:
                ts = getattr(inc, "timestamp_display", None) or getattr(inc, "timestamp", None)
            except Exception:
                ts = None
            ts_s = str(ts) if ts is not None else "—"
            game = (getattr(inc, "game", "") or "—").strip()
            sev = (getattr(inc, "severity", "") or "—").strip()
            et = (getattr(inc, "error_type", "") or "—").strip()
            lines.append(f"[{ts_s}] | {game} | {sev} | {et}")

        api_key = SettingsManager.get_gemini_api_key()
        ai_provider = SettingsManager.get_ai_provider()
        groq_api_key = SettingsManager.get_groq_api_key()
        openrouter_api_key = SettingsManager.get_openrouter_api_key()
        venice_api_key = SettingsManager.get_venice_api_key()
        payload = "\n".join(lines[:4000])

        default_text = self._full_audit_btn.text()
        self._full_audit_btn.setEnabled(False)
        self._full_audit_btn.setText("\u23f3 Processing Session Data…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

        dlg = QDialog(self)
        dlg.setWindowTitle("Defect Ticket")
        dlg.resize(900, 680)
        v = QVBoxLayout(dlg)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_host = QWidget()
        fields_layout = QVBoxLayout(scroll_host)
        fields_layout.setContentsMargins(10, 10, 10, 10)
        fields_layout.setSpacing(10)
        scroll.setWidget(scroll_host)
        v.addWidget(scroll, stretch=1)

        placeholder = CopyableFieldWidget(
            "Status",
            "Processing Session Data (please wait)…",
            parent=scroll_host,
        )
        placeholder.copy_btn.setEnabled(False)
        fields_layout.addWidget(placeholder)
        fields_layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = QPushButton("Copy full ticket")
        copy_btn.setEnabled(False)
        full_ticket_text_box: dict[str, str] = {"text": ""}
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(full_ticket_text_box["text"])
        )
        close_btn = QPushButton("Close")
        close_btn.setEnabled(False)
        close_btn.clicked.connect(dlg.accept)
        row.addWidget(copy_btn)
        row.addWidget(close_btn)
        v.addLayout(row)

        emitter = FullAuditEmitter(self)
        self._register_ai_task(emitter)

        def _on_done(_ok: bool, text: str) -> None:
            self._unregister_ai_task(emitter)
            try:
                if not Shiboken.isValid(dlg):
                    return
                raw = strip_markdown_bolding(text or "").strip()
                parts = parse_ai_response(raw)

                while fields_layout.count():
                    item = fields_layout.takeAt(0)
                    w = item.widget()
                    if w is not None:
                        w.setParent(None)

                ordered = [
                    ("Title", parts.get("Title", "")),
                    ("Key details", parts.get("Key details", "")),
                    ("Actual result", parts.get("Actual result", "")),
                    ("Expected result", parts.get("Expected result", "")),
                ]
                any_content = any((c or "").strip() for _, c in ordered)
                if not any_content:
                    ordered = [("Technical Summary", raw)]

                tracking = _collect_related_tracking(incidents)
                if tracking.strip():
                    ordered.append(("Related tracking", tracking))

                full_lines: list[str] = []
                for name, content in ordered:
                    fields_layout.addWidget(
                        CopyableFieldWidget(name, content, parent=scroll_host)
                    )
                    full_lines.append(f"{name}:\n{(content or '').strip()}\n")
                fields_layout.addStretch(1)

                full_ticket_text_box["text"] = ("\n".join(full_lines)).strip() or raw
            finally:
                QApplication.restoreOverrideCursor()
                self._full_audit_btn.setEnabled(True)
                self._full_audit_btn.setText(default_text)
                if Shiboken.isValid(close_btn):
                    close_btn.setEnabled(True)
                if Shiboken.isValid(copy_btn):
                    copy_btn.setEnabled(True)
                emitter.deleteLater()

        emitter.finished.connect(_on_done, Qt.ConnectionType.SingleShotConnection)
        schedule_full_audit(
            self._vm.thread_pool(),
            incidents_data=payload,
            software_version=software_version,
            api_key=api_key,
            ai_provider=ai_provider,
            groq_api_key=groq_api_key,
            openrouter_api_key=openrouter_api_key,
            venice_api_key=venice_api_key,
            emitter=emitter,
        )
        dlg.exec()

    def _on_open_notepad_clicked(self) -> None:
        inc = self._vm.selected_incident()
        if not isinstance(inc, Incident):
            return
        raw = (inc.log_file_path or "").strip()
        if not raw:
            return
        normalized = os.path.normpath(raw)
        p = Path(normalized)
        try:
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Open in Notepad",
                    f"The file does not exist or is not reachable:\n{normalized}",
                )
                return
        except OSError as e:
            QMessageBox.warning(
                self,
                "Open in Notepad",
                f"Could not access the path:\n{normalized}\n\n{e}",
            )
            return
        if sys.platform != "win32":
            QMessageBox.information(
                self,
                "Open in Notepad",
                "Launching Notepad is only implemented on Windows.",
            )
            return
        try:
            subprocess.Popen(["notepad.exe", normalized], shell=False)
        except OSError as e:
            QMessageBox.warning(
                self,
                "Open in Notepad",
                f"Could not start Notepad:\n{e}",
            )

    def _on_create_case_pack_clicked(self) -> None:
        rows = self._vm.filtered_incidents_flat()
        if not rows:
            QMessageBox.information(
                self,
                "Create Case Pack",
                "There are no incidents in the current filtered view.",
            )
            return
        default_name = suggest_case_pack_zip_name(self._vm.logged_machine_id())
        desk = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
        start_path = str(Path(desk) / default_name) if desk else default_name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Create Case Pack",
            start_path,
            "Zip archives (*.zip);;All files (*.*)",
        )
        if not path:
            return
        sas_file_path: str | None = None
        reply = QMessageBox.question(
            self,
            "Attach SAS Log?",
            "Would you like to attach and decode a SAS log (.dat) to this case pack?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            sas_pick, _ = QFileDialog.getOpenFileName(
                self,
                "Select SAS Log File",
                "",
                _LOG_OPEN_FILTER,
            )
            if sas_pick:
                sas_file_path = sas_pick
        machine_info = self._machine_info_for_case_pack()
        shot: str | None = None
        if self._last_remote_screenshot_path:
            sp = Path(self._last_remote_screenshot_path)
            if sp.is_file():
                shot = str(sp.resolve())
        self._case_pack_btn.setEnabled(False)
        self._case_pack_btn.setText("\U0001f4e6 Packing...")
        sb = self.statusBar()
        sb.showMessage("Creating case pack…", 0)
        self._status.setText("Creating case pack…")
        worker = _NetworkCasePackWorker(
            path,
            machine_info,
            self._vm.bookmark_notes_map(),
            list(rows),
            shot,
            self._get_remote_ip(),
            self._case_pack_worker_signals,
            sas_file_path=sas_file_path,
            software_version=self._vm.software_version_for_ai(),
        )
        self._vm.thread_pool().start(worker)

    def _on_case_pack_success(self, zip_path: str) -> None:
        self._case_pack_btn.setEnabled(True)
        self._case_pack_btn.setText(self._case_pack_btn_default_text)
        sb = self.statusBar()
        sb.showMessage(f"Case pack saved: {Path(zip_path).name}", 8000)
        self._status.setText(f"Case pack created: {Path(zip_path).name}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Create Case Pack")
        box.setText("Case pack created successfully.")
        box.setInformativeText(zip_path)
        open_btn = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() == open_btn:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(zip_path).resolve().parent))
            )

    def _on_case_pack_error(self, error_msg: str) -> None:
        self._case_pack_btn.setEnabled(True)
        self._case_pack_btn.setText(self._case_pack_btn_default_text)
        sb = self.statusBar()
        sb.clearMessage()
        self._status.setText("Ready")
        QMessageBox.critical(
            self,
            "Create Case Pack",
            f"Could not create case pack:\n{error_msg}",
        )

    def _on_history_sync_scan(self) -> None:
        self._start_scan(persist_to_db=True)

    def _on_history_search(self) -> None:
        self._history_tab.set_busy("Searching database…")
        schedule_db_query(
            self._vm.thread_pool(),
            self._history_tab.database_manager(),
            self._history_tab.build_filters(),
            self._db_query_emitter,
        )

    def _on_db_query_finished(self, rows: object) -> None:
        self._history_tab.set_busy("")
        self._history_tab.apply_query_results(rows)

    def _on_history_count_refresh(self) -> None:
        schedule_db_count(
            self._vm.thread_pool(),
            self._history_tab.database_manager(),
            self._db_count_emitter,
        )

    def _refresh_history_db_count(self) -> None:
        self._on_history_count_refresh()

    def _on_db_count_finished(self, n: int) -> None:
        self._history_tab.set_busy("")
        self._history_tab.update_db_count(n)

    def _schedule_persist_scan_to_db(self) -> None:
        tip, _, _remote = self._gather_path_resolve_args()
        machine_ip = (tip or "").strip() or "local"
        name: str | None = None
        if self._last_scan_roots:
            try:
                name = Path(self._last_scan_roots[0]).name
            except OSError:
                name = None
        self._status.setText("Saving scan to database…")
        schedule_db_persist(
            self._vm.thread_pool(),
            self._history_tab.database_manager(),
            machine_ip,
            name,
            self._vm.all_incidents,
            self._vm.state_nodes(),
            self._db_persist_emitter,
            logged_machine_id=self._vm.logged_machine_id(),
            env_fingerprint=self._vm.scan_environment_fingerprint(),
        )

    def _on_db_persist_finished(self, ok: bool, msg: str) -> None:
        if ok:
            self._status.setText(msg)
            self._refresh_history_db_count()
        else:
            self._status.setText("Database save failed.")
            QMessageBox.warning(self, "Database", msg)

    def _update_secondary_actions(self) -> None:
        scanning = self._vm.is_scanning()
        self._history_tab.set_sync_enabled(not scanning)
        busy = scanning or self._fleet_op_active
        self._fleet_tab.set_discovery_enabled(not busy)

    def _begin_fleet_op(self) -> None:
        self._fleet_op_active = True
        self._fleet_tab.set_discovery_enabled(False)

    def _end_fleet_op(self) -> None:
        self._fleet_op_active = False
        self._update_secondary_actions()

    def _fleet_load_from_db_sync(self) -> None:
        mgr = self._fleet_tab.database_manager()
        eng = mgr.create_engine()
        try:
            sf = mgr.session_factory(eng)
            with sf() as session:
                rows = mgr.fleet_snapshots_as_dicts(session)
            self._fleet_tab.set_fleet_rows(rows)
        except Exception as e:  # noqa: BLE001
            self._fleet_tab.set_busy_text(f"Fleet load error: {e}")
        finally:
            eng.dispose()

    def _schedule_fleet_startup_refresh(self) -> None:
        if self._vm.is_scanning():
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Refreshing host reachability…")
        schedule_fleet_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            max_workers=12,
            emitter=self._fleet_emitter,
        )

    def _on_fleet_subnet_scan(self, spec: str, workers: int) -> None:
        if self._vm.is_scanning():
            QMessageBox.warning(self, "Fleet", "Finish the log scan first.")
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Scanning subnet…")
        schedule_fleet_subnet_scan(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            spec,
            max_workers=workers,
            emitter=self._fleet_emitter,
        )

    def _on_fleet_manual_refresh(self, workers: int) -> None:
        if self._vm.is_scanning():
            QMessageBox.warning(self, "Fleet", "Finish the log scan first.")
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Refreshing known hosts…")
        schedule_fleet_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            max_workers=workers,
            emitter=self._fleet_emitter,
        )

    def _on_fleet_cabinet_added(self, workers: int) -> None:
        self._fleet_load_from_db_sync()
        if self._vm.is_scanning():
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Probing new cabinet…")
        schedule_fleet_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            max_workers=workers,
            emitter=self._fleet_emitter,
        )

    def _on_fleet_finished(self, data: object) -> None:
        self._end_fleet_op()
        self._fleet_tab.set_busy_text("")
        if isinstance(data, dict) and "__error__" in data:
            self._fleet_tab.set_fleet_rows(data)
            return
        if isinstance(data, list):
            QTimer.singleShot(0, lambda rows=data: self._safe_set_fleet_rows(rows))
        else:
            self._fleet_tab.set_fleet_rows(data)

    def _on_fleet_drift_refresh_finished(self, data: object) -> None:
        if isinstance(data, dict) and "__error__" in data:
            logger.warning("Clock drift refresh: %s", data["__error__"])
            fleet_timesync_logger().warning(
                "drift_refresh finished with error: %s", data.get("__error__")
            )
            return
        if isinstance(data, list):
            fleet_timesync_logger().info(
                "drift_refresh finished OK, scheduling fleet grid update (%s row(s))",
                len(data),
            )
            # Defer to the next event-loop tick: avoids re-entrancy right after the Sync Time
            # dialog closes while background probe results are delivered.
            QTimer.singleShot(0, lambda rows=data: self._safe_set_fleet_rows(rows))

    def _safe_set_fleet_rows(self, rows: object) -> None:
        if not isinstance(rows, list):
            fleet_timesync_logger().debug("_safe_set_fleet_rows skip (not a list): %r", type(rows))
            return
        fleet_timesync_logger().info(
            "_safe_set_fleet_rows begin: %s machine row(s)", len(rows)
        )
        try:
            self._fleet_tab.set_fleet_rows(rows)
            fleet_timesync_logger().info("_safe_set_fleet_rows completed OK")
        except Exception:
            fleet_timesync_logger().exception(
                "_safe_set_fleet_rows failed (fleet grid / MachineCard)"
            )
            logger.exception("Fleet grid refresh failed after drift or heartbeat update")

    def _on_remote_time_sync_finished(self, ip: str, ok: bool, msg: str) -> None:
        """PsExec completed on a worker thread; re-enable the card and show result on the GUI thread."""
        self._fleet_tab.set_sync_time_busy(ip, False)
        fleet_timesync_logger().info(
            "time_sync UI: worker finished ip=%s ok=%s msg_preview=%r",
            ip,
            ok,
            (msg or "")[:240],
        )
        if ok:
            QMessageBox.information(self, "Sync Time", msg)
        else:
            QMessageBox.warning(self, "Sync Time", msg)
        fleet_timesync_logger().info(
            "time_sync UI: after message box, scheduling drift_refresh ip=%s", ip
        )
        schedule_single_host_drift_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            ip,
            emitter=self._fleet_drift_emitter,
        )

    def _on_heartbeat_fleet(self, data: object) -> None:
        if self._fleet_op_active:
            return
        if isinstance(data, list):
            QTimer.singleShot(0, lambda rows=data: self._safe_set_fleet_rows(rows))

    def _on_heartbeat_failed(self, msg: str) -> None:
        logger.warning("Fleet heartbeat: %s", msg)
        self._status.setText(f"Fleet heartbeat error: {msg}")

    def _apply_fleet_machine_ip(self, ip: str) -> None:
        ip = ip.strip()
        if not ip:
            return
        self._radio_remote.blockSignals(True)
        self._radio_remote.setChecked(True)
        self._radio_local.setChecked(False)
        self._radio_remote.blockSignals(False)
        self._ip_edit.setText(ip)
        try:
            self._path_edit.setText(format_unc_log_root(ip))
        except ValueError:
            pass
        self._schedule_path_probe()
        self._save_connection_settings()

    def _on_fleet_card_action(self, action: str, ip: str) -> None:
        if action == "live":
            self._apply_fleet_machine_ip(ip)
            self._tabs.setCurrentIndex(0)
            QTimer.singleShot(500, self._fleet_start_live_deferred)
        elif action == "scan":
            self._apply_fleet_machine_ip(ip)
            self._tabs.setCurrentIndex(0)
            QTimer.singleShot(400, lambda: self._start_scan(False))
        elif action == "case_pack":
            self._apply_fleet_machine_ip(ip)
            self._tabs.setCurrentIndex(0)
            QTimer.singleShot(500, self._on_create_case_pack_clicked)
        elif action == "janitor":
            self._start_fleet_janitor(ip)
        elif action == "drift_refresh":
            fleet_timesync_logger().info(
                "scheduling single-host drift_refresh thread for ip=%s", ip
            )
            schedule_single_host_drift_refresh(
                self._vm.thread_pool(),
                self._fleet_tab.database_manager(),
                ip,
                emitter=self._fleet_drift_emitter,
            )
        elif action == "sync_time":
            fleet_timesync_logger().info(
                "scheduling remote time sync (background) for ip=%s", ip
            )
            schedule_remote_time_sync(
                self._vm.thread_pool(),
                ip,
                emitter=self._time_sync_emitter,
            )
        elif action == "remove_cabinet":
            self._remove_fleet_cabinet(ip)
        elif action == "capture_screen":
            self._begin_remote_screen_capture(ip)

    def _on_incidents_capture_screen_clicked(self) -> None:
        ip = (self._ip_edit.text() or "").strip()
        if not ip:
            QMessageBox.information(
                self,
                "Capture Screen",
                "Enter a remote host IP in Connection settings first.",
            )
            return
        self._begin_remote_screen_capture(ip)

    def _begin_remote_screen_capture(self, ip: str) -> None:
        ip = (ip or "").strip()
        if not ip:
            return
        if self._remote_capture_busy:
            return
        if os.name != "nt":
            QMessageBox.information(
                self,
                "Capture Screen",
                "Remote screen capture is only supported on Windows.",
            )
            return
        self._remote_capture_busy = True
        self._fleet_tab.set_all_capture_screen_busy(True)
        self._update_incidents_capture_button_enabled()
        safe = re.sub(r"[^\w.-]+", "_", ip)[:40]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        local_path = str(Path(tempfile.gettempdir()) / f"glci_screen_{safe}_{stamp}.jpg")
        sb = self.statusBar()
        sb.showMessage(f"Capturing remote screen ({ip})…", 0)
        self._status.setText(f"Capturing remote screen ({ip})…")
        QApplication.processEvents()
        schedule_remote_screen_capture(
            self._vm.thread_pool(),
            ip,
            local_path,
            self._screen_capture_emitter,
        )

    def _on_screen_capture_finished(self, ok: bool, msg: str) -> None:
        self._remote_capture_busy = False
        self._fleet_tab.set_all_capture_screen_busy(False)
        self._update_incidents_capture_button_enabled()
        sb = self.statusBar()
        sb.clearMessage()
        self._status.setText("Ready")
        if ok:
            self._last_remote_screenshot_path = msg
            ScreenshotPreviewDialog(msg, self).exec()
        else:
            QMessageBox.warning(self, "Capture Screen", msg)

    def _on_ram_clear_clicked(self) -> None:
        if self._ram_clear_busy:
            return
        if os.name != "nt":
            QMessageBox.information(
                self,
                "RAM Clear",
                "RAM Clear is only supported on Windows.",
            )
            return

        from network.ram_clear import (
            ram_clear_summary_for_confirm,
            resolve_ram_clear_plan,
        )

        remote = self._radio_remote.isChecked()
        ip = (self._ip_edit.text() or "").strip()
        plan = None if remote else resolve_ram_clear_plan()

        if remote:
            if not ip:
                QMessageBox.information(
                    self,
                    "RAM Clear",
                    "Enter a remote host IP in Connection settings first.",
                )
                return
            confirm_text = (
                f"Remote host: {ip}\n\n"
                "The cabinet will be scanned for slot or roulette RAM-clear layout.\n\n"
                "This will:\n"
                "• Close the running game (Ruleta / OneHand / game-start)\n"
                "• Stop all GoldClub services and related processes\n"
                "• Run the RAM-clear maintenance chain (backup + cleanup)\n"
                "• Restart GoldClub services and auto-start the game (Ruleta / OneHand)\n\n"
                "State folders may be wiped after backup. This cannot be undone easily."
            )
        else:
            if plan is None:
                QMessageBox.information(
                    self,
                    "RAM Clear",
                    "No slot or roulette RAM-clear layout found on this machine.",
                )
                return
            confirm_text = ram_clear_summary_for_confirm(plan)

        reply = QMessageBox.warning(
            self,
            "Confirm RAM Clear",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._live_toggle.isChecked():
            self._live_toggle.setChecked(False)

        self._ram_clear_busy = True
        self._ram_clear_btn.setEnabled(False)
        self._ram_clear_btn.setText("RAM Clear…")
        sb = self.statusBar()
        target = ip if remote else (plan.game_kind if plan else "local")
        sb.showMessage(f"RAM Clear running ({target})…", 0)
        self._status.setText(f"RAM Clear running ({target})…")
        QApplication.processEvents()
        schedule_ram_clear(
            self._vm.thread_pool(),
            local=not remote,
            ip=ip if remote else None,
            plan=plan,
            emitter=self._ram_clear_emitter,
        )

    def _on_ram_clear_finished(self, ok: bool, msg: str) -> None:
        self._ram_clear_busy = False
        self._ram_clear_btn.setText("RAM Clear")
        self._update_ram_clear_button_enabled()
        sb = self.statusBar()
        sb.clearMessage()
        self._status.setText("Ready")
        if ok:
            QMessageBox.information(self, "RAM Clear", msg)
        else:
            QMessageBox.critical(self, "RAM Clear", msg)

    def _on_verify_sas_clicked(self) -> None:
        path = (self._path_edit.text() or "").strip()
        if not path:
            QMessageBox.warning(self, "Path Required", "Please set a Scan Root path first.")
            return
        dlg = SasVerifyDialog(
            self._vm,
            self._vm.thread_pool(),
            scan_root=path,
            parent=None,
        )
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        if self.isVisible():
            center = self.frameGeometry().center()
            dlg.move(
                center.x() - max(dlg.width(), dlg.minimumWidth()) // 2,
                center.y() - max(dlg.height(), dlg.minimumHeight()) // 2,
            )
        dlg.exec()
        if dlg.mismatch_detected():
            self._last_sas_verification_summary = (
                "SAS/Accounting mismatch detected in one or more meters."
            )

    def _on_sas_sync_finished(self, qr_text: str, state_text: str) -> None:
        _ = qr_text
        self._btn_verify_sas.setEnabled(True)
        self._btn_verify_sas.setText(self._btn_verify_sas_default_text)
        comparison = compare_sas_and_xml(self._pending_sas_file, state_text)
        raw_sas = parse_sas_log_file(self._pending_sas_file)
        try:
            sas_body = Path(self._pending_sas_file).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            sas_body = ""
        slot_report = self._vm.compare_sas_with_accounting(
            sas_body,
            log_root=self._path_edit.text().strip(),
        )
        dlg = SASVerificationReportDialog(
            gm2au_report_plain=comparison,
            slotlog_report_plain=slot_report,
            raw_decode_plain=raw_sas,
            sas_file_path=self._pending_sas_file,
            parent=self,
        )
        self._last_sas_verification_plaintext = dlg.full_report_plain()
        self._last_sas_verification_summary = self._vm.summarize_sas_accounting_comparison(
            slot_report
        )
        if self._last_sas_verification_summary:
            # Include gm2au hint if table shows mismatch emoji
            if "❌ MISMATCH" in comparison:
                self._last_sas_verification_summary += (
                    "; gm2au table contains SAS vs XML mismatches."
                )
        dlg.exec()

    def _remove_fleet_cabinet(self, ip: str) -> None:
        ip = (ip or "").strip()
        if not ip:
            return
        label = self._fleet_machine_display_name(ip)
        ans = QMessageBox.question(
            self,
            "Remove Cabinet",
            f"Remove {label} from the fleet database?\n\n"
            "Stored incidents and timeline data for this cabinet will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        ok = self._fleet_tab.database_manager().delete_machine_by_ip(ip)
        if not ok:
            QMessageBox.warning(
                self,
                "Remove Cabinet",
                f"No machine record found for {ip}.",
            )
            return
        if self._vm.is_scanning():
            self._fleet_load_from_db_sync()
            return
        self._begin_fleet_op()
        self._fleet_tab.set_busy_text("Refreshing fleet…")
        schedule_fleet_refresh(
            self._vm.thread_pool(),
            self._fleet_tab.database_manager(),
            max_workers=12,
            emitter=self._fleet_emitter,
        )

    @staticmethod
    def _format_freed_bytes(num_bytes: int) -> str:
        if num_bytes <= 0:
            return "0 MB"
        gb = num_bytes / (1024**3)
        if gb >= 1.0:
            return f"{gb:.2f} GB"
        mb = num_bytes / (1024**2)
        return f"{mb:.2f} MB"

    def _fleet_machine_display_name(self, ip: str) -> str:
        ip = (ip or "").strip()
        if not ip:
            return "—"
        mgr = self._fleet_tab.database_manager()
        eng = mgr.create_engine()
        try:
            sf = mgr.session_factory(eng)
            with sf() as session:
                m = mgr.get_machine_by_ip(session, ip)
                if m is not None:
                    return format_machine_label(m.name, m.ip_address)
        except OSError:
            pass
        finally:
            eng.dispose()
        return ip

    def _start_fleet_janitor(self, ip: str) -> None:
        ip = (ip or "").strip()
        if not ip:
            return
        if self._janitor_worker is not None and self._janitor_worker.isRunning():
            QMessageBox.information(
                self,
                "Log Janitor",
                "A janitor run is already in progress. Wait for it to finish.",
            )
            return
        try:
            root = format_unc_log_root(ip)
        except ValueError as e:
            QMessageBox.warning(self, "Log Janitor", str(e))
            return
        try:
            reachable = Path(root).is_dir()
        except OSError:
            reachable = False
        if not reachable:
            QMessageBox.warning(
                self,
                "Log Janitor",
                f"Log root is not reachable:\n{root}",
            )
            return
        label = self._fleet_machine_display_name(ip)
        arch_d = SettingsManager.get_log_archive_days()
        del_d = SettingsManager.get_log_delete_days()
        ans = QMessageBox.question(
            self,
            "Log Janitor",
            f"Archive .log files older than {arch_d} days into .zip "
            f"and permanently delete .log / .zip files older than {del_d} "
            f"days under:\n{root}\n\n"
            f"Target: {label}\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        worker = LogJanitorWorker(root, self)
        self._janitor_worker = worker
        self._janitor_busy_ip = ip
        self._fleet_tab.set_janitor_busy(ip, True)
        worker.progress.connect(self._on_janitor_progress)
        worker.finished.connect(self._on_janitor_finished)
        worker.start()

    def _on_janitor_progress(self, pct: int, message: str) -> None:
        self._fleet_tab.set_busy_text(f"Janitor {pct}% — {message}")

    def _on_janitor_finished(self, freed_bytes: int, files_processed: int) -> None:
        ip = self._janitor_busy_ip
        self._fleet_tab.set_janitor_busy(ip or "", False)
        self._janitor_busy_ip = None
        self._fleet_tab.set_busy_text(self._fleet_tab.default_fleet_status_text())
        w = self._janitor_worker
        self._janitor_worker = None
        if w is not None:
            w.deleteLater()
        human = self._format_freed_bytes(freed_bytes)
        QMessageBox.information(
            self,
            "Log Janitor",
            f"Finished.\n\n"
            f"Files archived or deleted: {files_processed}\n"
            f"Estimated space recovered: {human} ({freed_bytes:,} bytes).",
        )

    def _fleet_start_live_deferred(self) -> None:
        root = self._path_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "Live Watch", "Could not set scan root for this host.")
            return
        try:
            ok = Path(root).exists()
        except OSError:
            ok = False
        if not ok:
            QMessageBox.warning(self, "Live Watch", f"Scan root is not reachable:\n{root}")
            return
        if not self._live_toggle.isChecked():
            self._live_toggle.setChecked(True)

    def _machine_registry_for_snapshot(self) -> dict | None:
        tip, _, remote = self._gather_path_resolve_args()
        if not remote or not (tip or "").strip():
            return None
        mgr = self._history_tab.database_manager()
        eng = mgr.create_engine()
        try:
            sf = mgr.session_factory(eng)
            with sf() as session:
                return mgr.machine_registry_dict(session, tip.strip())
        finally:
            eng.dispose()

    def _clock_drift_seconds_for_ip(self, ip: str) -> float | None:
        tip = (ip or "").strip()
        if not tip:
            return None
        mgr = self._history_tab.database_manager()
        eng = mgr.create_engine()
        try:
            sf = mgr.session_factory(eng)
            with sf() as session:
                rows = mgr.fleet_snapshots_as_dicts(session)
        except Exception:
            return None
        finally:
            eng.dispose()
        for r in rows:
            if r.get("ip_address") == tip:
                d = r.get("clock_drift_seconds")
                if d is None:
                    return None
                try:
                    return float(d)
                except (TypeError, ValueError):
                    return None
        return None

    def _get_remote_ip(self) -> str | None:
        """Host IP when Remote IP mode is active; ``None`` for local file scan."""
        if not self._radio_remote.isChecked():
            return None
        tip = (self._ip_edit.text() or "").strip()
        return tip or None

    def _machine_info_for_case_pack(self) -> dict:
        vm = self._vm.get_current_machine_info()
        remote = self._radio_remote.isChecked()
        tip = (self._ip_edit.text() or "").strip() if remote else ""
        hostname_remote: str | None = None
        drift: float | None = None
        if remote and tip:
            reg = self._machine_registry_for_snapshot()
            if reg:
                hostname_remote = reg.get("machine_name")
            drift = self._clock_drift_seconds_for_ip(tip)
        return {
            "IP": tip or None,
            "Hostname": hostname_remote or socket.gethostname(),
            "Drift": drift,
            "cabinet_id": vm.get("cabinet_id"),
            "active_game": vm.get("active_game"),
            "session_active": vm.get("session_active"),
            "connection_mode": "remote" if remote else "local",
            "scan_root_path": (self._path_edit.text() or "").strip(),
        }

    def _restore_window_geometry(self) -> None:
        SettingsManager.restore_main_window_geometry(self)

    def showEvent(self, event: QShowEvent) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._window_geometry_restored:
            return
        self._window_geometry_restored = True
        self._restore_window_geometry()

    def _try_restore_last_session_snapshot(self) -> None:
        meta = self._vm.load_session_state()
        if not meta:
            return
        ld = (meta.get("log_directory") or "").strip()
        if ld:
            self._last_scan_roots = [ld]
        cm = (meta.get("connection_mode") or "local").strip().lower()
        if cm == "remote":
            self._radio_remote.setChecked(True)
            rip = meta.get("remote_ip")
            if rip:
                self._ip_edit.setText(str(rip).strip())
        else:
            self._radio_local.setChecked(True)
            if ld:
                self._path_edit.blockSignals(True)
                self._path_edit.setText(ld)
                self._path_edit.blockSignals(False)
        self._apply_mode_to_widgets()
        self._refresh_dashboard()
        if ld:
            self._queue_version_fetch(ld)

    def _refresh_forensic_status_if_busy(self) -> None:
        sb = self.statusBar()
        if sb is not None and self._pending_ai_tasks > 0:
            sb.showMessage("Processing forensic data...", 0)

    def _register_ai_task(self, emitter: QObject) -> None:
        self._pending_ai_tasks += 1
        self._active_ai_emitters.append(emitter)
        sb = self.statusBar()
        if sb is not None and self._pending_ai_tasks == 1:
            sb.showMessage("Analysis Worker started", 3_000)
            QTimer.singleShot(3_000, self._refresh_forensic_status_if_busy)

    def _unregister_ai_task(self, emitter: QObject) -> None:
        try:
            self._active_ai_emitters.remove(emitter)
        except ValueError:
            pass
        self._pending_ai_tasks = max(0, self._pending_ai_tasks - 1)
        sb = self.statusBar()
        if sb is not None:
            if self._pending_ai_tasks > 0:
                sb.showMessage("Processing forensic data...", 0)
            else:
                sb.clearMessage()

    def _disconnect_active_ai_emitters(self) -> None:
        for obj in list(self._active_ai_emitters):
            try:
                obj.disconnect()
            except Exception:
                pass
        self._active_ai_emitters.clear()
        self._pending_ai_tasks = 0

    def _dismiss_open_dialogs(self) -> None:
        """Close visible modal/top-level dialogs so taskbar Close can exit ``exec()`` loops."""
        app = QApplication.instance()
        if app is None:
            return
        for widget in list(app.topLevelWidgets()):
            if widget is self:
                continue
            if widget.isWindow() and widget.isVisible():
                widget.close()
        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not self and modal.isVisible():
            modal.close()

    @staticmethod
    def _detach_running_thread(thread: QThread | None, *, wait_ms: int = 400) -> None:
        """Stop a worker thread; orphan it if blocking I/O prevents a timely quit."""
        if thread is None:
            return
        try:
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(wait_ms):
                    thread.setParent(None)
        except Exception:
            pass

    def _persist_window_state_only(self) -> None:
        SettingsManager.save_main_window_geometry(self)
        self._settings.setValue(
            "mainwin/last_log_path",
            (self._path_edit.text() or "").strip(),
        )
        self._settings.sync()

    def _start_async_session_save(self) -> None:
        remote = self._radio_remote.isChecked()
        emitter = _SessionSaveEmitter(self)
        self._shutdown_save_emitter = emitter
        emitter.finished.connect(self._complete_async_shutdown)
        self._vm.thread_pool().start(
            _SessionSaveRunnable(
                self._vm,
                log_directory=(self._path_edit.text() or "").strip(),
                connection_mode="remote" if remote else "local",
                remote_ip=(self._ip_edit.text() or "").strip() if remote else None,
                emitter=emitter,
            )
        )

    def _begin_app_shutdown(self) -> None:
        """Tear down workers and exit the process (title-bar X, taskbar Close, Alt+F4)."""
        if self._shutdown_cleanup_started:
            return
        self._shutdown_cleanup_started = True
        self._shutdown_force_quit = True

        self._dismiss_open_dialogs()

        tray = self._tray
        if tray is not None:
            tray.hide()

        self._disconnect_active_ai_emitters()
        self._stop_live_watch_ui()
        self._detach_running_thread(self._worker)
        self._worker = None
        if self._janitor_worker is not None and self._janitor_worker.isRunning():
            self._janitor_worker.requestInterruption()
            if self._janitor_busy_ip:
                self._fleet_tab.set_janitor_busy(self._janitor_busy_ip, False)
                self._janitor_busy_ip = None
            self._fleet_tab.set_busy_text(self._fleet_tab.default_fleet_status_text())
            self._detach_running_thread(self._janitor_worker)
            self._janitor_worker = None
        if self._fleet_heartbeat and self._fleet_heartbeat.isRunning():
            self._fleet_heartbeat.requestInterruption()
            self._detach_running_thread(self._fleet_heartbeat)

        SettingsManager.save_table_state(
            bytes(self._table.horizontalHeader().saveState())
        )
        self._persist_window_state_only()
        self._save_connection_settings()
        self._start_async_session_save()

        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
            app.quit()
        QTimer.singleShot(750, lambda: os._exit(0))

    def _complete_async_shutdown(self) -> None:
        self._shutdown_save_emitter = None
        app = QApplication.instance()
        if app is not None:
            app.quit()
        QTimer.singleShot(100, lambda: os._exit(0))

    def nativeEvent(self, eventType, message):  # type: ignore[override]
        # Hidden windows may not receive a second closeEvent; hard-exit on WM_CLOSE.
        if sys.platform == "win32" and self._shutdown_started:
            try:
                if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
                    from ctypes import wintypes

                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == 0x10:  # WM_CLOSE
                        os._exit(0)
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shutdown_started:
            event.accept()
            os._exit(0)
            return

        self._shutdown_started = True
        event.accept()
        self._begin_app_shutdown()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)


def run_app() -> int:
    fleet_timesync_logger()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Log Investigator")
    app.setApplicationDisplayName("Log Investigator")
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")
    from gui.app_branding import apply_app_icon
    from gui.win_title_bar import apply_title_bar_theme, install_title_bar_theme_filter

    apply_app_icon(app)

    install_title_bar_theme_filter(app, SettingsManager.get_theme)
    apply_theme(app, SettingsManager.get_theme())

    win = MainWindow()
    win.show()
    # Native HWND is reliable after the first event-loop tick.
    QTimer.singleShot(0, lambda: apply_title_bar_theme(win, SettingsManager.get_theme()))
    return app.exec()
