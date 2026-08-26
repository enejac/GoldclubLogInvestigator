"""Dialog + helpers for editing roulette bot timing profiles."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from automation.bot_config import (
    PROFILE_CUSTOM,
    PROFILE_EMULATION,
    PROFILE_SAFE,
    RECOMMENDED_OPTIONS,
    BotConfig,
    load_bot_config,
    save_bot_config,
    set_active_profile,
    user_bot_config_path,
)


class BotConfigDialog(QDialog):
    """Edit timing fields for the active / chosen profile and save to JSON."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        profile: str = PROFILE_CUSTOM,
        config_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Roulette bot timing")
        self._path = config_path or user_bot_config_path()
        self._profile = profile or PROFILE_CUSTOM
        self._cfg = load_bot_config(self._profile, self._path)

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                f"<b>Profile:</b> {self._cfg.profile} &nbsp; "
                f"<span style='color:#858585'>File: {self._path}</span>"
            )
        )

        form = QFormLayout()
        self._batch = self._spin(1, 40, self._cfg.batch_size)
        self._click = self._spin(0, 2000, self._cfg.click_ms)
        self._gap = self._spin(0, 2000, self._cfg.gap_ms)
        self._start_click = self._spin(0, 2000, self._cfg.start_click_ms)
        self._start_gap = self._spin(0, 2000, self._cfg.start_gap_ms)
        self._start_double = QCheckBox("Double-tap START")
        self._start_double.setChecked(bool(self._cfg.start_double_tap))
        self._start_double_gap = self._spin(0, 2000, self._cfg.start_double_gap_ms)
        self._ov_open = self._spin(0, 5000, self._cfg.overlay_open_ms)
        self._ov_close = self._spin(0, 5000, self._cfg.overlay_close_ms)
        self._layout_sw = self._spin(0, 5000, self._cfg.layout_switch_ms)
        self._focus = self._spin(0, 2000, self._cfg.focus_ms)
        self._focus_short = self._spin(0, 2000, self._cfg.focus_short_ms)
        self._key_delay = self._spin(0, 200, self._cfg.default_key_delay_ms)
        self._settle = self._dspin(0.0, 5.0, self._cfg.open_ui_settle_sec)
        self._settle_start = self._dspin(0.0, 5.0, self._cfg.open_ui_settle_start_sec)
        self._min_credits = self._spin(0, 1_000_000, self._cfg.min_credits)
        self._jitter = self._spin(0, 500, self._cfg.gap_jitter_ms)
        self._wait_betting = QCheckBox("Wait for open betting window")
        self._wait_betting.setChecked(bool(self._cfg.wait_betting))
        self._cancel_cloth = QCheckBox("Cancel cloth before first window bets")
        self._cancel_cloth.setChecked(bool(self._cfg.cancel_cloth_before_bets))
        self._include_unverified = QCheckBox("Include unverified hitboxes")
        self._include_unverified.setChecked(bool(self._cfg.include_unverified))

        form.addRow("Batch size", self._batch)
        form.addRow("Click hold (ms)", self._click)
        form.addRow("Inter-click gap (ms)", self._gap)
        form.addRow("START click (ms)", self._start_click)
        form.addRow("START gap (ms)", self._start_gap)
        form.addRow(self._start_double)
        form.addRow("START double gap (ms)", self._start_double_gap)
        form.addRow("Overlay open settle (ms)", self._ov_open)
        form.addRow("Overlay close settle (ms)", self._ov_close)
        form.addRow("Layout switch settle (ms)", self._layout_sw)
        form.addRow("Focus wait (ms)", self._focus)
        form.addRow("Focus short (ms)", self._focus_short)
        form.addRow("Key delay (ms)", self._key_delay)
        form.addRow("Open-UI settle (s)", self._settle)
        form.addRow("Open-UI settle START (s)", self._settle_start)
        form.addRow("Min credits", self._min_credits)
        form.addRow("Gap jitter ±ms", self._jitter)
        form.addRow(self._wait_betting)
        form.addRow(self._cancel_cloth)
        form.addRow(self._include_unverified)
        root.addLayout(form)

        tips = QLabel(
            "<b>Recommended next options</b> (not all wired yet):<br>"
            + "<br>".join(f"• <code>{k}</code> — {d}" for k, d in RECOMMENDED_OPTIONS[:6])
        )
        tips.setTextFormat(Qt.TextFormat.RichText)
        tips.setWordWrap(True)
        root.addWidget(tips)

        presets = QHBoxLayout()
        btn_em = QPushButton("Load emulation defaults")
        btn_em.clicked.connect(lambda: self._load_preset(PROFILE_EMULATION))
        btn_safe = QPushButton("Load safe defaults")
        btn_safe.clicked.connect(lambda: self._load_preset(PROFILE_SAFE))
        presets.addWidget(btn_em)
        presets.addWidget(btn_safe)
        presets.addStretch(1)
        root.addLayout(presets)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(520, 640)

    @staticmethod
    def _spin(lo: int, hi: int, val: int) -> QSpinBox:
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setValue(int(val))
        return w

    @staticmethod
    def _dspin(lo: float, hi: float, val: float) -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(2)
        w.setSingleStep(0.05)
        w.setValue(float(val))
        return w

    def _load_preset(self, name: str) -> None:
        cfg = load_bot_config(name, self._path)
        cfg.profile = PROFILE_CUSTOM if self._profile == PROFILE_CUSTOM else name
        self._apply_cfg(cfg)

    def _apply_cfg(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._batch.setValue(cfg.batch_size)
        self._click.setValue(cfg.click_ms)
        self._gap.setValue(cfg.gap_ms)
        self._start_click.setValue(cfg.start_click_ms)
        self._start_gap.setValue(cfg.start_gap_ms)
        self._start_double.setChecked(cfg.start_double_tap)
        self._start_double_gap.setValue(cfg.start_double_gap_ms)
        self._ov_open.setValue(cfg.overlay_open_ms)
        self._ov_close.setValue(cfg.overlay_close_ms)
        self._layout_sw.setValue(cfg.layout_switch_ms)
        self._focus.setValue(cfg.focus_ms)
        self._focus_short.setValue(cfg.focus_short_ms)
        self._key_delay.setValue(cfg.default_key_delay_ms)
        self._settle.setValue(cfg.open_ui_settle_sec)
        self._settle_start.setValue(cfg.open_ui_settle_start_sec)
        self._min_credits.setValue(cfg.min_credits)
        self._jitter.setValue(cfg.gap_jitter_ms)
        self._wait_betting.setChecked(cfg.wait_betting)
        self._cancel_cloth.setChecked(cfg.cancel_cloth_before_bets)
        self._include_unverified.setChecked(cfg.include_unverified)

    def _collect(self) -> BotConfig:
        # Edits from the dialog always land in the selected profile name;
        # if user opened from emulation/safe, keep that name unless custom.
        name = self._profile or PROFILE_CUSTOM
        return BotConfig(
            profile=name,
            batch_size=int(self._batch.value()),
            click_ms=int(self._click.value()),
            gap_ms=int(self._gap.value()),
            start_click_ms=int(self._start_click.value()),
            start_gap_ms=int(self._start_gap.value()),
            start_double_tap=self._start_double.isChecked(),
            start_double_gap_ms=int(self._start_double_gap.value()),
            overlay_open_ms=int(self._ov_open.value()),
            overlay_close_ms=int(self._ov_close.value()),
            layout_switch_ms=int(self._layout_sw.value()),
            focus_ms=int(self._focus.value()),
            focus_short_ms=int(self._focus_short.value()),
            default_key_delay_ms=int(self._key_delay.value()),
            open_ui_settle_sec=float(self._settle.value()),
            open_ui_settle_start_sec=float(self._settle_start.value()),
            min_credits=int(self._min_credits.value()),
            gap_jitter_ms=int(self._jitter.value()),
            wait_betting=self._wait_betting.isChecked(),
            cancel_cloth_before_bets=self._cancel_cloth.isChecked(),
            include_unverified=self._include_unverified.isChecked(),
            shuffle_layouts=self._cfg.shuffle_layouts,
            wait_betting_timeout_sec=self._cfg.wait_betting_timeout_sec,
        ).clamp()

    def _on_save(self) -> None:
        cfg = self._collect()
        try:
            path = save_bot_config(cfg, path=self._path, make_active=True)
            set_active_profile(cfg.profile, path)
        except OSError as e:
            QMessageBox.warning(self, "Bot config", f"Could not save:\n{e}")
            return
        self._cfg = cfg
        self.accept()

    def selected_config(self) -> BotConfig:
        return self._cfg
