"""Fleet Overview: machine cards with health colors and context actions."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEvent, QEasingCurve, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config_manager import SettingsManager
from diag_logging import fleet_timesync_logger
from database.manager import DatabaseManager, format_machine_label
from gui.add_machine_dialog import AddMachineDialog
from gui.palette_adapt import (
    health_border_color,
    led_color,
    text_success,
    text_warning,
)


def _drift_seconds_is_displayable(cd: object) -> bool:
    """Reject NaN/inf and non-numeric values before formatting drift text."""
    if isinstance(cd, bool):
        return False
    if isinstance(cd, int):
        return True
    if isinstance(cd, float):
        return math.isfinite(cd)
    return False


def _format_clock_drift_warning(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "⚠️ Drift: (invalid reading)"
    sign = "+" if seconds >= 0 else "-"
    sec_abs = abs(int(round(seconds)))
    m, s_rem = divmod(sec_abs, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"⚠️ Drift: {sign}{h}h {m:02d}m {s_rem:02d}s"
    if m > 0:
        return f"⚠️ Drift: {sign}{m}m {s_rem:02d}s"
    return f"⚠️ Drift: {sign}{s_rem}s"


class StatusLed(QWidget):
    """Small round indicator; optional opacity pulse for healthy online hosts."""

    def __init__(self, mode: str, pulse: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if pulse and mode == "green":
            eff = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(1_400)
            anim.setStartValue(0.4)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.InOutSine)
            anim.setLoopCount(-1)
            anim.start()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.palette().color(QPalette.ColorRole.Mid)
        painter.setPen(QPen(mid, 1))
        painter.setBrush(led_color(self.palette(), self._mode))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


def _fp_short(val: object, max_len: int = 24) -> str:
    if val is None:
        return "—"
    s = str(val).strip()
    if not s:
        return "—"
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


class MachineCard(QFrame):
    """One host summary with health border and right-click actions."""

    action_requested = Signal(str, str)
    """``(action_id, ip_address)`` — includes ``remove_cabinet``, ``janitor``, ``drift_refresh``, …."""

    def __init__(self, row: dict[str, Any], parent: QWidget | None = None) -> None:
        # Before super(): QFrame.__init__ can emit PaletteChange → changeEvent before the
        # rest of this method runs; these attributes must exist first.
        self._clock_kind: str | None = None
        self._config_warning_lbl: QLabel | None = None
        self._badge_lbl: QLabel | None = None
        self._ip = str(row.get("ip_address", ""))
        self._health = str(row.get("health", "grey"))
        super().__init__(parent)
        fleet_timesync_logger().debug("MachineCard.__init__: start ip=%s", self._ip)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        fleet_timesync_logger().debug(
            "MachineCard.__init__: after setBackgroundRole ip=%s", self._ip
        )
        self.setMinimumWidth(200)
        self.setMaximumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        lay.setContentsMargins(10, 10, 10, 10)
        head = QHBoxLayout()
        head.setSpacing(8)
        led_mode = str(row.get("led_mode", "grey"))
        head.addWidget(
            StatusLed(led_mode, bool(row.get("led_pulse")), self),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        title = QLabel(f"<b>{format_machine_label(row.get('name'), self._ip)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        head.addWidget(title, stretch=1)
        lay.addLayout(head)
        es = row.get("enrollment_status")
        if es:
            en = QLabel(str(es))
            en.setForegroundRole(QPalette.ColorRole.Link)
            en.setStyleSheet("font-size: 11px; font-weight: 600;")
            lay.addWidget(en)
        aid = row.get("asset_id")
        if aid:
            lay.addWidget(QLabel(f"Asset id: {aid}"))
        av, cv, ov = row.get("app_version"), row.get("clr_version"), row.get("os_version")
        if av or cv or ov:
            fp = QLabel(
                "Fingerprint · "
                f"App {_fp_short(av, 18)} · "
                f"CLR {_fp_short(cv, 14)} · "
                f"OS {_fp_short(ov, 20)}"
            )
            fp.setWordWrap(True)
            fp.setForegroundRole(QPalette.ColorRole.PlaceholderText)
            fp.setStyleSheet("font-size: 11px;")
            lay.addWidget(fp)
        smb = row.get("last_smb_open")
        smb_txt = "SMB 445: yes" if smb is True else ("SMB 445: no" if smb is False else "SMB 445: —")
        lay.addWidget(QLabel(smb_txt))
        log_ok = row.get("last_admin_log_ok")
        log_txt = (
            "Admin log root: reachable"
            if log_ok is True
            else ("unreachable" if log_ok is False else "Admin log root: —")
        )
        lay.addWidget(QLabel(log_txt))
        self._clock_lbl = QLabel("")
        self._clock_lbl.setWordWrap(True)
        self._sync_time_btn = QPushButton("🔄 Sync Time")
        self._sync_time_btn.setToolTip(
            "PsExec (tools\\\\psexec.exe): remote PowerShell Set-TimeZone to match this PC, "
            "re-enable automatic DST, restart W32Time, w32tm /resync, then verify with "
            "remote tzutil /g (runs as SYSTEM)."
        )
        self._sync_time_btn.clicked.connect(self._on_sync_time_clicked)
        self._sync_time_default = "🔄 Sync Time"

        self._capture_screen_btn = QPushButton("\U0001f4f8 Capture Screen")
        self._capture_screen_btn.setToolTip(
            "PsExec (tools\\\\psexec.exe): capture the remote primary display as SYSTEM, "
            "then copy the PNG via the admin share (c$)."
        )
        self._capture_screen_btn.clicked.connect(
            lambda: self.action_requested.emit("capture_screen", self._ip)
        )
        self._capture_default_text = "\U0001f4f8 Capture Screen"

        clock_row = QHBoxLayout()
        clock_row.setSpacing(6)
        clock_row.addWidget(self._clock_lbl, stretch=1)
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.addWidget(self._sync_time_btn, alignment=Qt.AlignmentFlag.AlignRight)
        btn_col.addWidget(self._capture_screen_btn, alignment=Qt.AlignmentFlag.AlignRight)
        clock_row.addLayout(btn_col)
        lay.addLayout(clock_row)

        cd = row.get("clock_drift_seconds")
        drift_threshold = SettingsManager.get_clock_drift_threshold()
        show_clock = _drift_seconds_is_displayable(cd)
        if show_clock:
            d = float(cd)
            if abs(d) <= drift_threshold:
                self._clock_lbl.setText("✓ Clock OK")
                self._clock_kind = "ok"
                self._clock_lbl.setToolTip(
                    "Newest log line timestamp is within "
                    f"{drift_threshold}s of this PC (UTC)."
                )
            else:
                self._clock_lbl.setText(_format_clock_drift_warning(d))
                self._clock_kind = "warn"
                self._clock_lbl.setToolTip(
                    "Compare newest log line timestamp on the cabinet to this machine’s UTC. "
                    "Positive → cabinet clock appears slow or stale log; negative → ahead."
                )
        else:
            self._clock_lbl.hide()
            self._clock_kind = None
            clock_row.insertStretch(0, 1)

        stats = (
            f"24h: CRIT {row.get('critical_24h', 0)} · "
            f"MATH {row.get('math_fail_24h', 0)} · "
            f"WARN {row.get('warn_24h', 0)}"
        )
        lay.addWidget(QLabel(stats))
        if row.get("config_warning"):
            drift = QLabel("\u2699 \u26A0  Config warning — baseline drift")
            drift.setToolTip("Environment fingerprint changed vs last known good (see DB / sync scan).")
            self._config_warning_lbl = drift
            lay.addWidget(drift)
        self._badge_lbl = QLabel(self._health.upper())
        self._badge_lbl.setStyleSheet("font-weight: 700;")
        lay.addWidget(self._badge_lbl)

        self._reapply_semantic_label_colors()

        fleet_timesync_logger().debug(
            "MachineCard.__init__: complete ip=%s clock_kind=%r drift_s=%r",
            self._ip,
            self._clock_kind,
            row.get("clock_drift_seconds"),
        )

        self._janitor_btn = QPushButton("Run Janitor")
        self._janitor_btn.setToolTip(
            "Archive .log files older than the configured age into .zip, "
            "and delete very old .log / .zip files on this cabinet (UNC log root)."
        )
        self._janitor_btn.clicked.connect(
            lambda: self.action_requested.emit("janitor", self._ip)
        )
        lay.addWidget(self._janitor_btn)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def _reapply_semantic_label_colors(self) -> None:
        p = self.palette()
        # PaletteChange can fire during construction before _clock_lbl exists; _clock_kind is
        # initialized before setBackgroundRole, but guard widgets for any ordering edge case.
        clock_lbl = getattr(self, "_clock_lbl", None)
        ck = getattr(self, "_clock_kind", None)
        if clock_lbl is not None:
            if ck == "ok":
                clock_lbl.setStyleSheet(
                    f"color: {text_success(p).name()}; font-size: 11px;"
                )
            elif ck == "warn":
                clock_lbl.setStyleSheet(
                    f"color: {text_warning(p).name()}; font-weight: 700; font-size: 12px;"
                )
        if self._badge_lbl is not None:
            br = health_border_color(p, self._health)
            self._badge_lbl.setStyleSheet(
                f"color: {br.name()}; font-weight: 700;"
            )
        if self._config_warning_lbl is not None:
            self._config_warning_lbl.setStyleSheet(
                f"color: {text_warning(p).name()}; font-weight: 700; font-size: 12px;"
            )

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(health_border_color(self.palette(), self._health))
        pen.setWidth(3)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawRoundedRect(rect, 8, 8)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            fleet_timesync_logger().debug(
                "MachineCard.changeEvent: PaletteChange ip=%s clock_kind=%r "
                "has_clock_lbl=%s",
                getattr(self, "_ip", ""),
                getattr(self, "_clock_kind", None),
                getattr(self, "_clock_lbl", None) is not None,
            )
            self._reapply_semantic_label_colors()
        super().changeEvent(event)

    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        for aid, label in (
            ("live", "Start Live Watch"),
            ("scan", "Run Full Scan"),
            ("case_pack", "Create Case Pack…"),
            ("capture_screen", "Capture Screen…"),
            ("janitor", "Run Log Janitor…"),
            ("remove_cabinet", "Remove Cabinet…"),
        ):
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, a=aid: self.action_requested.emit(a, self._ip))
            menu.addAction(act)
        menu.exec(self.mapToGlobal(pos))

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.action_requested.emit("scan", self._ip)
        super().mouseDoubleClickEvent(event)

    def ip_address(self) -> str:
        return self._ip

    def set_janitor_busy(self, busy: bool) -> None:
        self._janitor_btn.setEnabled(not busy)
        self._janitor_btn.setText("Cleaning…" if busy else "Run Janitor")

    def set_capture_screen_busy(self, busy: bool) -> None:
        self._capture_screen_btn.setEnabled(not busy)
        self._capture_screen_btn.setText("Capturing…" if busy else self._capture_default_text)

    def set_sync_time_busy(self, busy: bool) -> None:
        self._sync_time_btn.setEnabled(not busy)
        self._sync_time_btn.setText("Syncing…" if busy else self._sync_time_default)

    def _on_sync_time_clicked(self) -> None:
        if not self._ip.strip():
            return
        fleet_timesync_logger().info(
            "Sync Time UI: clicked ip=%s (PsExec runs on worker thread)", self._ip
        )
        self.set_sync_time_busy(True)
        self.action_requested.emit("sync_time", self._ip)


class FleetTabWidget(QWidget):
    """
    Subnet scan + machine grid.

    Signals: ``request_refresh`` — manual refresh; ``cabinet_added`` — DB row added, optional probe pass.
    """

    request_subnet_scan = Signal(str, int)
    request_refresh = Signal(int)
    cabinet_added = Signal(int)
    card_action = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db = DatabaseManager()
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(12)
        self._sort_by_health = QCheckBox("Sort by health (worst first)")
        self._sort_by_health.setChecked(True)
        self._sort_by_health.setToolTip(
            "Red (critical) cards first, then orange (warnings / drift), green, then offline."
        )
        self._last_fleet_rows: list[dict[str, Any]] = []
        self._cards_by_ip: dict[str, MachineCard] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "<b>Fleet Overview</b> — ICMP ping, TCP 445, and <code>c$\\Goldclub\\var\\log</code> checks. "
            "Subnet scan <b>silently enrolls</b> new hosts only when ping and the admin log path succeed. "
            "A background heartbeat re-probes known machines on an interval (see <code>config.py</code>)."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        sort_row = QHBoxLayout()
        sort_row.addWidget(self._sort_by_health)
        sort_row.addStretch(1)
        root.addLayout(sort_row)

        scan_box = QGroupBox("Subnet discovery")
        sr = QHBoxLayout(scan_box)
        self._range_edit = QLineEdit()
        self._range_edit.setPlaceholderText("e.g. 10.0.0.0/24 or 10.0.0.1-10.0.0.80")
        sr.addWidget(self._range_edit, stretch=1)
        sr.addWidget(QLabel("Workers:"))
        self._workers = QSpinBox()
        self._workers.setRange(4, 32)
        self._workers.setValue(12)
        sr.addWidget(self._workers)
        self._btn_scan = QPushButton("Scan range")
        self._btn_scan.clicked.connect(self._emit_subnet_scan)
        sr.addWidget(self._btn_scan)
        self._btn_ref = QPushButton("Refresh known hosts")
        self._btn_ref.setToolTip("Re-ping every IPv4 row in the machines table")
        self._btn_ref.clicked.connect(self._emit_refresh)
        sr.addWidget(self._btn_ref)
        self._btn_add_cabinet = QPushButton("Add Cabinet ➕")
        self._btn_add_cabinet.setToolTip(
            "Add a single cabinet by IP; hostname is resolved for the GST-style label."
        )
        self._btn_add_cabinet.clicked.connect(self._on_add_cabinet_clicked)
        sr.addWidget(self._btn_add_cabinet)
        root.addWidget(scan_box)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        root.addWidget(self._status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._grid_host)
        root.addWidget(scroll, stretch=1)

        self._sort_by_health.stateChanged.connect(self._on_sort_toggle)

    def database_manager(self) -> DatabaseManager:
        return self._db

    def _emit_subnet_scan(self) -> None:
        spec = self._range_edit.text().strip()
        if not spec:
            QMessageBox.warning(self, "Fleet", "Enter an IP range or CIDR.")
            return
        self.request_subnet_scan.emit(spec, self._workers.value())

    def _emit_refresh(self) -> None:
        self.request_refresh.emit(self._workers.value())

    def _on_add_cabinet_clicked(self) -> None:
        dlg = AddMachineDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ip = dlg.resolved_ip().strip()
        name = dlg.resolved_name().strip()
        if not ip:
            return
        eng = self._db.create_engine()
        try:
            sf = self._db.session_factory(eng)
            with sf() as session:
                if self._db.get_machine_by_ip(session, ip) is not None:
                    QMessageBox.warning(
                        self,
                        "Add Cabinet",
                        f"{ip} is already in the fleet.",
                    )
                    return
                self._db.get_or_create_machine(
                    session,
                    ip,
                    name or None,
                    logged_machine_id=None,
                )
                session.commit()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Add Cabinet", f"Could not save machine:\n{e}")
            return
        finally:
            eng.dispose()
        self.cabinet_added.emit(self._workers.value())

    def set_scan_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress.setVisible(False)
            return
        self._progress.setVisible(True)
        self._progress.setMaximum(total)
        self._progress.setValue(done)
        self._status.setText(f"Pinging… {done}/{total}")

    def set_fleet_rows(self, rows: list[dict[str, Any]] | object) -> None:
        self._progress.setVisible(False)
        if isinstance(rows, dict) and "__error__" in rows:
            QMessageBox.warning(self, "Fleet", str(rows["__error__"]))
            self._status.setText("Fleet update failed.")
            return
        if not isinstance(rows, list):
            rows = []
        self._last_fleet_rows = list(rows)
        view_rows = list(rows)
        if self._sort_by_health.isChecked():

            def _fleet_row_sort_key(r: dict[str, Any]) -> tuple[int, str]:
                lr = r.get("led_rank", 99)
                try:
                    rank = int(lr)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    rank = 99
                return (rank, str(r.get("ip_address", "")))

            view_rows = sorted(view_rows, key=_fleet_row_sort_key)
        fleet_timesync_logger().info(
            "set_fleet_rows: clearing grid, building %s MachineCard(s)",
            len(view_rows),
        )
        self._clear_grid()
        cols = 4
        for i, row in enumerate(view_rows):
            ip_r = str(row.get("ip_address", "")).strip()
            fleet_timesync_logger().debug(
                "set_fleet_rows: MachineCard %s/%s ip=%s",
                i + 1,
                len(view_rows),
                ip_r or "?",
            )
            card = MachineCard(row)
            card.action_requested.connect(self._relay_card_action)
            ip_key = str(row.get("ip_address", "")).strip()
            if ip_key:
                self._cards_by_ip[ip_key] = card
            self._grid.addWidget(card, i // cols, i % cols)
        fleet_timesync_logger().info(
            "set_fleet_rows: finished layout for %s card(s)", len(view_rows)
        )
        self._status.setText(f"{len(view_rows)} machine(s) in database.")

    def _on_sort_toggle(self, _state: int = 0) -> None:
        if self._last_fleet_rows:
            self.set_fleet_rows(self._last_fleet_rows)

    def _relay_card_action(self, action: str, ip: str) -> None:
        self.card_action.emit(action, ip)

    def _clear_grid(self) -> None:
        self._cards_by_ip.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_janitor_busy(self, ip: str, busy: bool) -> None:
        card = self._cards_by_ip.get((ip or "").strip())
        if card is not None:
            card.set_janitor_busy(busy)

    def set_sync_time_busy(self, ip: str, busy: bool) -> None:
        card = self._cards_by_ip.get((ip or "").strip())
        if card is not None:
            card.set_sync_time_busy(busy)

    def set_all_capture_screen_busy(self, busy: bool) -> None:
        for card in self._cards_by_ip.values():
            card.set_capture_screen_busy(busy)

    def default_fleet_status_text(self) -> str:
        return f"{len(self._last_fleet_rows)} machine(s) in database."

    def set_busy_text(self, text: str) -> None:
        self._status.setText(text)

    def set_discovery_enabled(self, enabled: bool) -> None:
        self._range_edit.setEnabled(enabled)
        self._workers.setEnabled(enabled)
        self._btn_scan.setEnabled(enabled)
        self._btn_ref.setEnabled(enabled)
        self._btn_add_cabinet.setEnabled(enabled)
        self._sort_by_health.setEnabled(enabled)
