"""Game analytics dashboard (RNG / feature frequencies from state timeline)."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget


def _bold_label(text: str) -> QLabel:
    lb = QLabel(text)
    f = QFont(lb.font())
    f.setBold(True)
    f.setPointSize(max(f.pointSize(), 11))
    lb.setFont(f)
    return lb


class GameAnalyticsWidget(QWidget):
    """Displays session metrics from :func:`analytics_engine.calculate_session_analytics`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        title = _bold_label("Game analytics (from state timeline)")
        root.addWidget(title)
        hint = QLabel(
            "Counts derive from MachineState segments in scanned logs "
            "(Change MachineState from … to …)."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(12)
        self._val_spins = QLabel("0")
        self._val_fg = QLabel("0")
        self._val_fg_freq = QLabel("—")
        self._val_bonus = QLabel("0")
        self._val_bonus_freq = QLabel("—")

        for w in (
            self._val_spins,
            self._val_fg,
            self._val_fg_freq,
            self._val_bonus,
            self._val_bonus_freq,
        ):
            f = QFont(w.font())
            f.setBold(True)
            f.setPointSize(max(f.pointSize(), 12))
            w.setFont(f)

        form.addRow(_bold_label("\U0001f3b0 Total Base Game Spins"), self._val_spins)
        form.addRow(_bold_label("\U0001f193 Free Games Triggered"), self._val_fg)
        form.addRow(_bold_label("\U0001f4ca Free Games Frequency"), self._val_fg_freq)
        form.addRow(_bold_label("\U0001f381 Bonuses Triggered"), self._val_bonus)
        form.addRow(_bold_label("\U0001f4c8 Bonus Frequency"), self._val_bonus_freq)
        root.addLayout(form)
        root.addStretch(1)

    def update_dashboard(self, stats: dict[str, Any]) -> None:
        self._val_spins.setText(str(int(stats.get("total_spins", 0))))
        self._val_fg.setText(str(int(stats.get("free_games_triggered", 0))))
        self._val_bonus.setText(str(int(stats.get("bonuses_triggered", 0))))
        self._val_fg_freq.setText(str(stats.get("fg_frequency", "—")))
        self._val_bonus_freq.setText(str(stats.get("bonus_frequency", "—")))
