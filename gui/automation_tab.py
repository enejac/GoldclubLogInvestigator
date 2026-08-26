from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config_manager import SettingsManager

from gui.automation_worker import (
    AutomationEmitter,
    schedule_automation_run,
    schedule_jira_repro_run,
    schedule_roulette_automation_run,
)
from gui.notepad_pp import attach_open_with_npp_menu


class AutomationTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._pool = QThreadPool.globalInstance()
        self._emitter = AutomationEmitter(self)
        self._emitter.progress.connect(self._on_progress)
        self._emitter.finished.connect(self._on_finished)
        self._active_out_dir: Path | None = None
        self._running_kind: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel(
            "<b>Automated Tests</b>  "
            "<span style='color:#858585'>(slot forced-combo · roulette random bet+spin)</span>"
        )
        hdr.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(hdr)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_slot_page(), "Slot")
        self._tabs.addTab(self._build_roulette_page(), "Roulette")
        # Prefer controls over the run log so action buttons stay on-screen.
        root.addWidget(self._tabs, stretch=3)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Run log…")
        self._log.setMaximumBlockCount(4000)
        self._log.setMinimumHeight(96)
        self._log.setMaximumHeight(260)
        self._log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        root.addWidget(self._log, stretch=0)
        attach_open_with_npp_menu(
            self._log,
            path_provider=self._automation_results_path,
            parent=self,
            label="Open results with Notepad++",
        )

    def _build_slot_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)

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
        lay.addLayout(row)

        self._combos = QPlainTextEdit()
        from automation.tutankhamen_symbols import tutankhamen_symbol_sweep_combos_text

        self._combos.setPlaceholderText(
            "Tutankhamen F11 IDs are 0..14 (0=WILD). Three-row sweep example:\n"
            "0 0 0 0 0|1 1 1 1 1|2 2 2 2 2"
        )
        self._combos.setPlainText(tutankhamen_symbol_sweep_combos_text())
        self._combos.setMaximumBlockCount(2000)
        lay.addWidget(self._combos, stretch=1)

        btn_row = QHBoxLayout()
        self._load_sweep_btn = QPushButton("Load Tutankhamen 0–11 sweep")
        self._load_sweep_btn.clicked.connect(self._on_load_tutankhamen_sweep)
        btn_row.addWidget(self._load_sweep_btn)
        self._run_btn = QPushButton("Run slot automation")
        self._run_btn.clicked.connect(self._on_run_slot_clicked)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        return page

    def _build_roulette_page(self) -> QWidget:
        # Scrollable body + sticky action footer so Jira/run buttons stay reachable
        # when the window is maximized and the strategy list would otherwise clip them.
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 8, 0, 0)
        page_lay.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)

        info = QLabel(
            "Live <b>godot</b> roulette automation on the cabinet: wait for betting, "
            "place stakes from the selected <b>strategy</b>, then press <b>START</b>. "
            "Win/loss is inferred from RCM credit changes for progression systems."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cabinet IP:"))
        self._roulette_ip = QLineEdit("10.0.0.90")
        self._roulette_ip.setPlaceholderText("10.0.0.90 or local")
        self._roulette_ip.setToolTip(
            "A cabinet IP, or 'local' to drive the roulette client running on this "
            "machine (screen mapping and Jira/Xray recipes both accept it)."
        )
        self._roulette_ip.setMaximumWidth(220)
        row.addWidget(self._roulette_ip)
        row.addSpacing(12)
        row.addWidget(QLabel("Rounds:"))
        self._roulette_rounds = QSpinBox()
        self._roulette_rounds.setRange(1, 200)
        self._roulette_rounds.setValue(5)
        self._roulette_rounds.setMaximumWidth(70)
        row.addWidget(self._roulette_rounds)
        row.addSpacing(12)
        row.addWidget(QLabel("Base unit:"))
        self._roulette_base_unit = QSpinBox()
        self._roulette_base_unit.setRange(1, 50)
        self._roulette_base_unit.setValue(1)
        self._roulette_base_unit.setMaximumWidth(60)
        self._roulette_base_unit.setToolTip("Credit units for progression systems (chip_1 = 1).")
        row.addWidget(self._roulette_base_unit)
        row.addSpacing(12)
        row.addWidget(QLabel("Even-money:"))
        self._roulette_market = QComboBox()
        for label, key in (
            ("Red", "RED"),
            ("Black", "BLACK"),
            ("Odd (IMPAR)", "ODD"),
            ("Even (PAR)", "EVEN"),
            ("Low (1–18)", "LOW"),
            ("High (19–36)", "HIGH"),
        ):
            self._roulette_market.addItem(label, key)
        self._roulette_market.setMaximumWidth(140)
        row.addWidget(self._roulette_market)
        row.addStretch(1)
        lay.addLayout(row)

        strat_row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Strategies"))
        self._roulette_strategies = QListWidget()
        self._roulette_strategies.setMinimumWidth(280)
        self._roulette_strategies.setMaximumWidth(360)
        self._roulette_strategies.setMinimumHeight(140)
        self._roulette_strategies.setMaximumHeight(220)
        from automation.roulette_strategies import STRATEGIES

        for s in STRATEGIES:
            item = QListWidgetItem(s.name)
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            item.setToolTip(s.summary)
            self._roulette_strategies.addItem(item)
        self._roulette_strategies.setCurrentRow(0)
        self._roulette_strategies.currentItemChanged.connect(self._on_strategy_selected)
        left.addWidget(self._roulette_strategies, stretch=0)
        strat_row.addLayout(left, stretch=0)

        right = QVBoxLayout()
        right.addWidget(QLabel("Strategy details"))
        self._roulette_strategy_desc = QPlainTextEdit()
        self._roulette_strategy_desc.setReadOnly(True)
        self._roulette_strategy_desc.setPlaceholderText("Select a strategy…")
        self._roulette_strategy_desc.setMaximumHeight(100)
        right.addWidget(self._roulette_strategy_desc, stretch=0)

        self._bot_options = QGroupBox("Bot options")
        self._bot_options.setToolTip(
            "Full mapped catalog on layout1 + layout2 (outside bets, chrome, "
            "language / help / history / overlays). Cloth-only random betting "
            "remains CLI-only."
        )
        bot_form = QFormLayout(self._bot_options)
        bot_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        bot_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._bot_mode = QComboBox()
        self._bot_mode.addItem("Systematic (all mapped)", "systematic")
        self._bot_mode.addItem("Random shuffle", "random")
        self._bot_mode.setToolTip("Walk order through the mapped catalog.")
        bot_form.addRow("Order:", self._bot_mode)

        speed_row = QHBoxLayout()
        self._bot_profile = QComboBox()
        self._bot_profile.setMinimumWidth(140)
        self._bot_profile.setToolTip(
            "Timing profile from automation/bot_config.json. "
            "Emulation = snappy clicks (default)."
        )
        self._reload_bot_profiles()
        saved_profile = SettingsManager.get_bot_profile()
        idx = self._bot_profile.findData(saved_profile)
        if idx >= 0:
            self._bot_profile.setCurrentIndex(idx)
        self._bot_profile.currentIndexChanged.connect(self._on_bot_profile_changed)
        speed_row.addWidget(self._bot_profile)
        self._bot_config_btn = QPushButton("Edit timings…")
        self._bot_config_btn.setToolTip(
            "Open bot_config.json timings (click/gap/overlay settle, batch size, …)."
        )
        self._bot_config_btn.clicked.connect(self._on_edit_bot_config)
        speed_row.addWidget(self._bot_config_btn)
        speed_row.addStretch(1)
        bot_form.addRow("Speed:", speed_row)

        self._bot_max_clicks = QSpinBox()
        self._bot_max_clicks.setRange(0, 5000)
        self._bot_max_clicks.setValue(0)
        self._bot_max_clicks.setSpecialValueText("all")
        self._bot_max_clicks.setMaximumWidth(100)
        self._bot_max_clicks.setToolTip("0 = walk the full mapped catalog")
        bot_form.addRow("Max clicks:", self._bot_max_clicks)

        self._bot_client_id = QLineEdit("player0")
        self._bot_client_id.setMaximumWidth(120)
        self._bot_client_id.setToolTip(
            "Seat / client id written on every click_log line."
        )
        bot_form.addRow("Client id:", self._bot_client_id)

        bot_note = QLabel(
            "Always both skins · outside + chrome · logs → click_log.jsonl"
        )
        bot_note.setStyleSheet("color: #858585;")
        bot_note.setWordWrap(True)
        bot_form.addRow("", bot_note)

        right.addWidget(self._bot_options)
        right.addStretch(1)
        strat_row.addLayout(right, stretch=1)
        lay.addLayout(strat_row, stretch=0)
        lay.addStretch(1)

        scroll.setWidget(body)
        page_lay.addWidget(scroll, stretch=1)

        jira_box = QGroupBox("Jira / Xray repro")
        jira_box.setToolTip(
            "Run a local recipe from automation/xray_recipes/ keyed by an Xray Test "
            "(e.g. RSW-6534). Uses mapped clicks + :8090 asserts. Bill inject stays "
            "off unless you enable the lab-script checkbox (not in lite exe)."
        )
        jira_form = QFormLayout(jira_box)
        self._jira_key = QLineEdit("RSW-6534")
        self._jira_key.setPlaceholderText("RSW-6534 or browse URL")
        self._jira_key.setMaximumWidth(320)
        jira_form.addRow("Test key:", self._jira_key)
        self._jira_bill = QCheckBox("Allow lab bill inject (external script)")
        self._jira_bill.setChecked(False)
        self._jira_bill.setToolTip(
            "Calls Invoke-BillInjectRoulette.ps1 from the repo. "
            "Disabled inside LogInvestigator.exe; leave off for click-only runs."
        )
        jira_form.addRow("", self._jira_bill)
        jira_btns = QHBoxLayout()
        self._jira_resolve_btn = QPushButton("Resolve recipe")
        self._jira_resolve_btn.setToolTip("Show local recipe + optional Jira metadata")
        self._jira_resolve_btn.clicked.connect(self._on_jira_resolve_clicked)
        jira_btns.addWidget(self._jira_resolve_btn)
        self._jira_run_btn = QPushButton("Run Jira/Xray repro")
        self._jira_run_btn.clicked.connect(self._on_jira_repro_clicked)
        jira_btns.addWidget(self._jira_run_btn)
        jira_btns.addStretch(1)
        jira_form.addRow("", jira_btns)
        page_lay.addWidget(jira_box)

        btn_row = QHBoxLayout()
        self._roulette_run_btn = QPushButton("Run roulette automation")
        self._roulette_run_btn.clicked.connect(self._on_run_roulette_clicked)
        btn_row.addWidget(self._roulette_run_btn)
        self._roulette_map_btn = QPushButton("Map current screen")
        self._roulette_map_btn.setToolTip(
            "Map whatever screen the cabinet is showing: cloth, racetrack, statistics, "
            "help, menu or a screen the software team just added. Screenshots the open "
            "screen first, tells you which mapped screen it matches, re-finds controls "
            "that moved, and proves the result (middleware for bets, pixel diff for panels)."
        )
        self._roulette_map_btn.clicked.connect(self._on_map_screen_clicked)
        btn_row.addWidget(self._roulette_map_btn)
        self._map_picture_btn = QPushButton("Show map picture")
        self._map_picture_btn.setToolTip(
            "Open the last mapping run's picture: the capture with every mapped box "
            "drawn on it, so a wrong box is visible rather than only in the numbers."
        )
        self._map_picture_btn.setEnabled(False)
        self._map_picture_btn.clicked.connect(self._on_show_map_picture)
        btn_row.addWidget(self._map_picture_btn)
        btn_row.addStretch(1)
        page_lay.addLayout(btn_row)

        self._on_strategy_selected(
            self._roulette_strategies.currentItem(), None
        )
        return page

    def _selected_strategy_id(self) -> str:
        item = self._roulette_strategies.currentItem()
        if item is None:
            return "random"
        return str(item.data(Qt.ItemDataRole.UserRole) or "random")

    def _on_strategy_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        from automation.roulette_strategies import STRATEGY_BY_ID

        sid = "random"
        if current is not None:
            sid = str(current.data(Qt.ItemDataRole.UserRole) or "random")
        info = STRATEGY_BY_ID.get(sid)
        if info is None:
            self._roulette_strategy_desc.setPlainText("")
            return
        self._roulette_strategy_desc.setPlainText(
            f"{info.name}\n[{info.kind}]\n\n{info.summary}"
        )
        is_random = sid in ("random", "random_bot")
        self._bot_options.setVisible(is_random)
        needs_em = bool(info.needs_even_money)
        self._roulette_base_unit.setEnabled(
            sid not in ("random", "full_board", "random_bot")
        )
        self._roulette_market.setEnabled(
            needs_em and sid not in ("random", "full_board", "random_bot")
        )
        self._roulette_rounds.setEnabled(not is_random)

    def _reload_bot_profiles(self) -> None:
        from automation.bot_config import list_profiles, load_bot_config_file

        path = SettingsManager.get_bot_config_path() or None
        self._bot_profile.blockSignals(True)
        self._bot_profile.clear()
        for name in list_profiles(path):
            label = {
                "emulation": "Emulation (fast)",
                "safe": "Safe (slow)",
                "custom": "Custom",
            }.get(name, name)
            self._bot_profile.addItem(label, name)
        self._bot_profile.blockSignals(False)
        try:
            data = load_bot_config_file(path)
            active = str(data.get("active_profile") or "emulation")
        except Exception:  # noqa: BLE001
            active = "emulation"
        idx = self._bot_profile.findData(active)
        if idx < 0:
            idx = self._bot_profile.findData(SettingsManager.get_bot_profile())
        if idx >= 0:
            self._bot_profile.setCurrentIndex(idx)

    def _on_bot_profile_changed(self, _index: int = 0) -> None:
        from automation.bot_config import set_active_profile

        name = str(self._bot_profile.currentData() or "emulation")
        SettingsManager.set_bot_profile(name)
        path = SettingsManager.get_bot_config_path() or None
        try:
            set_active_profile(name, path)
        except OSError:
            pass
        self._on_progress(f"Bot speed profile -> {name}")

    def _on_edit_bot_config(self) -> None:
        from gui.bot_config_dialog import BotConfigDialog

        profile = str(self._bot_profile.currentData() or "custom")
        path_s = SettingsManager.get_bot_config_path()
        path = Path(path_s) if path_s else None
        dlg = BotConfigDialog(self, profile=profile, config_path=path)
        if dlg.exec():
            cfg = dlg.selected_config()
            SettingsManager.set_bot_profile(cfg.profile)
            self._reload_bot_profiles()
            idx = self._bot_profile.findData(cfg.profile)
            if idx >= 0:
                self._bot_profile.setCurrentIndex(idx)
            self._on_progress(
                f"Saved bot timings profile={cfg.profile} "
                f"click={cfg.click_ms}ms gap={cfg.gap_ms}ms batch={cfg.batch_size}"
            )

    def set_cabinet_ip(self, ip: str) -> None:
        text = (ip or "").strip()
        if text:
            self._ip.setText(text)
            self._roulette_ip.setText(text)

    def _automation_results_path(self) -> Path | None:
        if self._active_out_dir is None:
            return None
        for name in ("click_log.jsonl", "roulette_results.jsonl", "results.jsonl"):
            path = self._active_out_dir / name
            if path.is_file():
                return path
        return None

    def _set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._roulette_run_btn.setEnabled(not running)
        self._roulette_map_btn.setEnabled(not running)
        if not running:
            self._map_picture_btn.setEnabled(self._map_picture_path() is not None)
        else:
            self._map_picture_btn.setEnabled(False)
        self._load_sweep_btn.setEnabled(not running)
        self._jira_run_btn.setEnabled(not running)
        self._jira_resolve_btn.setEnabled(not running)

    def _on_jira_resolve_clicked(self) -> None:
        from automation.xray_recipe import list_recipe_keys, load_recipe, normalize_issue_key

        raw = (self._jira_key.text() or "").strip()
        if not raw:
            QMessageBox.warning(self, "Jira repro", "Enter an Xray Test key (e.g. RSW-6534).")
            return
        try:
            key = normalize_issue_key(raw)
            recipe = load_recipe(key)
        except (ValueError, FileNotFoundError) as e:
            avail = ", ".join(list_recipe_keys()) or "(none)"
            QMessageBox.warning(
                self,
                "Jira repro",
                f"{e}\n\nLocal recipes: {avail}",
            )
            return
        lines = [
            f"Key: {recipe.get('key')}",
            f"Summary: {recipe.get('summary')}",
            f"Bug: {recipe.get('jira_bug')}",
            f"Layout: {recipe.get('layout')}",
            f"Steps: {len(recipe.get('steps') or [])}",
            "",
            str(recipe.get("notes") or ""),
        ]
        try:
            from network.jira_client import fetch_issue_metadata

            meta = fetch_issue_metadata(key)
            if meta.get("ok"):
                lines.extend(
                    [
                        "",
                        "--- Jira ---",
                        f"Type: {meta.get('issuetype')}  Status: {meta.get('status')}",
                        f"Summary: {meta.get('summary')}",
                        f"Browse: {meta.get('browse')}",
                    ]
                )
            else:
                lines.extend(["", f"Jira meta: {meta.get('error')}"])
        except Exception as e:  # noqa: BLE001
            lines.extend(["", f"Jira meta error: {e}"])
        self._roulette_strategy_desc.setPlainText("\n".join(lines))
        self._on_progress(f"Resolved local recipe {key}")

    def _on_jira_repro_clicked(self) -> None:
        ip = (self._roulette_ip.text() or "").strip()
        if not ip:
            QMessageBox.warning(self, "Jira repro", "Enter a cabinet IP.")
            return
        raw = (self._jira_key.text() or "").strip()
        if not raw:
            QMessageBox.warning(self, "Jira repro", "Enter an Xray Test key (e.g. RSW-6534).")
            return
        allow_bill = bool(self._jira_bill.isChecked())
        self._set_running(True)
        self._running_kind = "jira_repro"
        self._log.clear()
        self._on_progress(f"Jira/Xray repro key={raw} bill_inject={allow_bill}")
        self._active_out_dir = schedule_jira_repro_run(
            self._pool,
            ip=ip,
            key=raw,
            emitter=self._emitter,
            allow_bill_inject=allow_bill,
        )
        self._on_progress(f"Output folder: {self._active_out_dir}")

    def _on_load_tutankhamen_sweep(self) -> None:
        from automation.tutankhamen_symbols import tutankhamen_symbol_sweep_combos_text

        self._combos.setPlainText(tutankhamen_symbol_sweep_combos_text())

    def _on_run_slot_clicked(self) -> None:
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

        self._set_running(True)
        self._running_kind = "slot"
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

    def _on_run_roulette_clicked(self) -> None:
        ip = (self._roulette_ip.text() or "").strip()
        if not ip:
            QMessageBox.warning(self, "Automation", "Enter a cabinet IP.")
            return
        strategy_id = self._selected_strategy_id()
        market = str(self._roulette_market.currentData() or "RED")
        base_unit = int(self._roulette_base_unit.value())

        self._set_running(True)
        self._running_kind = "roulette"
        self._log.clear()
        self._on_progress(
            f"Strategy={strategy_id} market={market} base_unit={base_unit}"
        )
        client_id = (self._bot_client_id.text() or "player0").strip() or "player0"
        self._active_out_dir = schedule_roulette_automation_run(
            self._pool,
            ip=ip,
            rounds=int(self._roulette_rounds.value()),
            min_bets=3,
            max_bets=7,
            include_outside=True,
            strategy_id=strategy_id,
            market=market,
            base_unit=base_unit,
            emitter=self._emitter,
            bot_mode=str(self._bot_mode.currentData() or "systematic"),
            bot_layouts="layout1,layout2",
            bot_max_clicks=int(self._bot_max_clicks.value()),
            bot_client_id=client_id,
            bot_profile=str(self._bot_profile.currentData() or "emulation"),
            bot_config_path=SettingsManager.get_bot_config_path(),
        )
        self._on_progress(f"Output folder: {self._active_out_dir}")

    def _on_map_screen_clicked(self) -> None:
        from gui.automation_worker import schedule_map_screen_run
        from gui.map_screen_dialog import MapScreenDialog

        # An empty IP is not an error here: the dialog can map this machine's own
        # screen instead, which is how a layout gets mapped with no cabinet in reach.
        ip = (self._roulette_ip.text() or "").strip()
        dialog = MapScreenDialog(ip, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            request = dialog.request()
        except ValueError as exc:
            QMessageBox.warning(self, "Map current screen", str(exc))
            return

        self._set_running(True)
        self._running_kind = "screen_map"
        self._log.clear()
        self._active_out_dir = schedule_map_screen_run(
            self._pool,
            ip=request.target or ip or "local",
            layout_id=request.layout_id,
            screen_key=request.screen_key,
            screen_name=request.screen_name,
            is_new=request.is_new,
            apply_registry=request.apply_registry,
            verify=request.verify,
            edge_test=request.edge_test,
            emitter=self._emitter,
        )
        self._on_progress(f"Output folder: {self._active_out_dir}")

    def _map_picture_path(self) -> Path | None:
        """The newest mapping run's review picture, wherever that run wrote it."""
        from app_paths import app_runs_dir

        candidates: list[Path] = []
        if self._active_out_dir is not None:
            candidates.append(self._active_out_dir / "map_review.png")
        try:
            packs = sorted(
                app_runs_dir().glob("*_screenmap_*/map_review.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            packs = []
        candidates.extend(packs)
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _on_show_map_picture(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        path = self._map_picture_path()
        if path is None:
            QMessageBox.information(
                self,
                "Map picture",
                "No mapping picture yet — run 'Map current screen' first.",
            )
            return
        self._on_progress(f"Map picture: {path}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_progress(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_finished(self, ok: bool, msg: str) -> None:
        self._set_running(False)
        kind = self._running_kind or "automation"
        self._running_kind = None
        self._map_picture_btn.setEnabled(self._map_picture_path() is not None)
        self._log.appendPlainText("")
        self._log.appendPlainText(
            (f"DONE OK ({kind}): " if ok else f"DONE FAIL ({kind}): ") + msg
        )
