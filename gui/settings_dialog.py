"""Global settings: log janitor retention and fleet clock drift threshold."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from config_manager import (
    AI_PROVIDER_CHOICES,
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_GROQ,
    AI_PROVIDER_OPENROUTER,
    AI_PROVIDER_VENICE,
    THEME_CHOICES,
    VENICE_MODEL_CHOICES,
    SettingsManager,
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(420, 400)

        root = QVBoxLayout(self)

        notif = QGroupBox("Notifications")
        notif_lay = QVBoxLayout(notif)
        self._chk_live_notif = QCheckBox("Enable live notifications (Windows toasts)")
        self._chk_live_notif.setToolTip(
            "While Live Watch is running, show a Windows balloon for FATAL and CRITICAL incidents."
        )
        self._chk_live_notif.setChecked(SettingsManager.get_notifications_enabled())
        notif_lay.addWidget(self._chk_live_notif)
        row_clr = QHBoxLayout()
        btn_clear_bl = QPushButton("Clear notification blacklist")
        btn_clear_bl.setToolTip(
            "Remove all ignored message patterns (re-enable toasts for lines you previously muted)."
        )
        btn_clear_bl.clicked.connect(self._on_clear_notification_blacklist)
        row_clr.addWidget(btn_clear_bl)
        row_clr.addStretch(1)
        notif_lay.addLayout(row_clr)
        root.addWidget(notif)

        appear = QGroupBox("Appearance")
        appear_lay = QVBoxLayout(appear)
        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("Application Theme:"))
        self._combo_theme = QComboBox()
        self._combo_theme.addItems(list(THEME_CHOICES))
        cur = SettingsManager.get_theme()
        if cur in THEME_CHOICES:
            self._combo_theme.setCurrentIndex(THEME_CHOICES.index(cur))
        row_t.addWidget(self._combo_theme)
        appear_lay.addLayout(row_t)
        root.addWidget(appear)

        jan = QGroupBox("Log Janitor")
        jan_lay = QVBoxLayout(jan)
        row_a = QHBoxLayout()
        row_a.addWidget(QLabel("Archive logs older than (days):"))
        self._spin_archive = QSpinBox()
        self._spin_archive.setRange(1, 365)
        self._spin_archive.setValue(SettingsManager.get_log_archive_days())
        row_a.addWidget(self._spin_archive)
        jan_lay.addLayout(row_a)
        row_d = QHBoxLayout()
        row_d.addWidget(QLabel("Delete logs older than (days):"))
        self._spin_delete = QSpinBox()
        self._spin_delete.setRange(1, 365)
        self._spin_delete.setValue(SettingsManager.get_log_delete_days())
        row_d.addWidget(self._spin_delete)
        jan_lay.addLayout(row_d)
        root.addWidget(jan)

        fleet = QGroupBox("Fleet Monitoring")
        fleet_lay = QVBoxLayout(fleet)
        row_c = QHBoxLayout()
        row_c.addWidget(QLabel("Clock drift warning threshold (seconds):"))
        self._spin_drift = QSpinBox()
        self._spin_drift.setRange(10, 600)
        self._spin_drift.setValue(SettingsManager.get_clock_drift_threshold())
        row_c.addWidget(self._spin_drift)
        fleet_lay.addLayout(row_c)
        root.addWidget(fleet)

        ai = QGroupBox("Cloud analysis (Optional)")
        ai_lay = QVBoxLayout(ai)
        row_prov = QHBoxLayout()
        row_prov.addWidget(QLabel("Provider:"))
        self._combo_ai_provider = QComboBox()
        self._combo_ai_provider.addItem("Gemini (Google)", AI_PROVIDER_GEMINI)
        self._combo_ai_provider.addItem("Groq", AI_PROVIDER_GROQ)
        self._combo_ai_provider.addItem("OpenRouter (Free models)", AI_PROVIDER_OPENROUTER)
        self._combo_ai_provider.addItem("Venice AI", AI_PROVIDER_VENICE)
        cur_prov = SettingsManager.get_ai_provider()
        if cur_prov in AI_PROVIDER_CHOICES:
            idx = AI_PROVIDER_CHOICES.index(cur_prov)
            self._combo_ai_provider.setCurrentIndex(idx)
        self._combo_ai_provider.setToolTip(
            "Gemini uses google-genai / google-generativeai.\n"
            "Groq uses llama-3.3-70b-versatile (free developer tier).\n"
            "OpenRouter supports openrouter/free and free model routing.\n"
            "Venice AI uses qwen-3-6-plus by default (OpenAI-compatible, privacy-focused)."
        )
        row_prov.addWidget(self._combo_ai_provider)
        ai_lay.addLayout(row_prov)
        row_vm = QHBoxLayout()
        row_vm.addWidget(QLabel("Venice model:"))
        self._combo_venice_model = QComboBox()
        for model_id in VENICE_MODEL_CHOICES:
            label = model_id
            if model_id == "qwen-3-6-plus":
                label = "Qwen 3.6 Plus Uncensored (qwen-3-6-plus)"
            elif model_id == "olafangensan-glm-4.7-flash-heretic":
                label = "GLM 4.7 Flash Heretic (olafangensan-glm-4.7-flash-heretic)"
            self._combo_venice_model.addItem(label, model_id)
        cur_vm = SettingsManager.get_venice_model()
        if cur_vm in VENICE_MODEL_CHOICES:
            self._combo_venice_model.setCurrentIndex(VENICE_MODEL_CHOICES.index(cur_vm))
        self._combo_venice_model.setToolTip(
            "Venice model id sent to https://api.venice.ai/api/v1/chat/completions"
        )
        row_vm.addWidget(self._combo_venice_model)
        ai_lay.addLayout(row_vm)
        row_k = QHBoxLayout()
        row_k.addWidget(QLabel("Google Gemini API Key:"))
        self._gemini_key = QLineEdit()
        self._gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_key.setPlaceholderText("Paste API key… (stored locally)")
        self._gemini_key.setText(SettingsManager.get_gemini_api_key())
        self._gemini_key.setToolTip(
            "Used for Gemini: case pack digest, Generate Technical Summary, Full Session Audit.\n"
            "Case pack sends only aggregated counts + audit status (no raw logs)."
        )
        row_k.addWidget(self._gemini_key)
        ai_lay.addLayout(row_k)
        row_g = QHBoxLayout()
        row_g.addWidget(QLabel("Groq API Key:"))
        self._groq_key = QLineEdit()
        self._groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_key.setPlaceholderText("Optional — required when provider is Groq")
        self._groq_key.setText(SettingsManager.get_groq_api_key())
        self._groq_key.setToolTip("Get a free key at https://console.groq.com/ (stored locally).")
        row_g.addWidget(self._groq_key)
        ai_lay.addLayout(row_g)
        row_or = QHBoxLayout()
        row_or.addWidget(QLabel("OpenRouter API Key:"))
        self._openrouter_key = QLineEdit()
        self._openrouter_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._openrouter_key.setPlaceholderText("Optional — used for OpenRouter provider/fallback")
        self._openrouter_key.setText(SettingsManager.get_openrouter_api_key())
        self._openrouter_key.setToolTip("Get a key at https://openrouter.ai/keys (stored locally).")
        row_or.addWidget(self._openrouter_key)
        ai_lay.addLayout(row_or)
        row_ve = QHBoxLayout()
        row_ve.addWidget(QLabel("Venice API Key:"))
        self._venice_key = QLineEdit()
        self._venice_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._venice_key.setPlaceholderText("Optional — required when provider is Venice")
        self._venice_key.setText(SettingsManager.get_venice_api_key())
        self._venice_key.setToolTip("Get a key at https://venice.ai/ (stored locally).")
        row_ve.addWidget(self._venice_key)
        ai_lay.addLayout(row_ve)
        root.addWidget(ai)

        local_ai = QGroupBox("Local AI Helper (Offline)")
        local_lay = QVBoxLayout(local_ai)
        row_m = QHBoxLayout()
        row_m.addWidget(QLabel("GGUF model path (optional):"))
        self._ai_helper_model = QLineEdit()
        self._ai_helper_model.setPlaceholderText(
            r"e.g. H:\ConfigScanner\models\Qwen3-4B-Q4_K_M.gguf"
        )
        self._ai_helper_model.setText(SettingsManager.get_ai_helper_model_path())
        self._ai_helper_model.setToolTip(
            "Override path to a local GGUF. Leave blank to auto-detect under "
            "models\\ next to LogInvestigator.exe. Requires llama-cpp-python."
        )
        row_m.addWidget(self._ai_helper_model)
        local_lay.addLayout(row_m)
        hint = QLabel(
            "No network — search always works. LLM answers need Qwen3-4B Q4_K_M "
            "and llama-cpp-python (see requirements-ai-helper.txt)."
        )
        hint.setWordWrap(True)
        local_lay.addWidget(hint)
        root.addWidget(local_ai)

        self._spin_archive.valueChanged.connect(self._on_archive_changed)
        self._spin_delete.valueChanged.connect(self._on_delete_changed)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save_clicked)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def _on_archive_changed(self, v: int) -> None:
        if self._spin_delete.value() < v:
            self._spin_delete.blockSignals(True)
            self._spin_delete.setValue(v)
            self._spin_delete.blockSignals(False)

    def _on_delete_changed(self, v: int) -> None:
        if v < self._spin_archive.value():
            self._spin_archive.blockSignals(True)
            self._spin_archive.setValue(v)
            self._spin_archive.blockSignals(False)

    def _on_save_clicked(self) -> None:
        a = self._spin_archive.value()
        d = self._spin_delete.value()
        if d < a:
            QMessageBox.warning(
                self,
                "Settings",
                "Delete age must be greater than or equal to archive age.",
            )
            return
        SettingsManager.set_log_archive_days(a)
        SettingsManager.set_log_delete_days(d)
        SettingsManager.set_clock_drift_threshold(self._spin_drift.value())
        SettingsManager.set_theme(self._combo_theme.currentText())
        SettingsManager.set_notifications_enabled(self._chk_live_notif.isChecked())
        prov = self._combo_ai_provider.currentData()
        if prov not in AI_PROVIDER_CHOICES:
            prov = AI_PROVIDER_GEMINI
        SettingsManager.set_ai_provider(str(prov))
        SettingsManager.set_gemini_api_key(self._gemini_key.text())
        SettingsManager.set_groq_api_key(self._groq_key.text())
        SettingsManager.set_openrouter_api_key(self._openrouter_key.text())
        SettingsManager.set_venice_api_key(self._venice_key.text())
        vm = self._combo_venice_model.currentData()
        if vm in VENICE_MODEL_CHOICES:
            SettingsManager.set_venice_model(str(vm))
        SettingsManager.set_ai_helper_model_path(self._ai_helper_model.text())
        self.accept()

    def _on_clear_notification_blacklist(self) -> None:
        SettingsManager.clear_notification_blacklist()
        QMessageBox.information(
            self,
            "Notifications",
            "The notification blacklist has been cleared.",
        )
