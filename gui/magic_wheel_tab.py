"""Magic Wheel meter submenu - EGM-matching layout for SAS Verify."""

from __future__ import annotations

import math
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from network.magic_wheel_loader import MagicWheelData

WHEEL_SEGMENT_COLORS: tuple[QColor, ...] = (
    QColor("#6B2D8B"),
    QColor("#2A9D8F"),
    QColor("#1B3A6B"),
    QColor("#8B5A2B"),
    QColor("#C2185B"),
    QColor("#8B0000"),
    QColor("#2E7D32"),
    QColor("#1A1A1A"),
    QColor("#1B5E20"),
    QColor("#C62828"),
)


class MagicWheelGraphic(QWidget):
    """Paint the prize wheel sectors from config credit values."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: tuple[int, ...] = ()
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_segments(self, segments: Iterable[int]) -> None:
        self._segments = tuple(int(v) for v in segments)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height()) - 8
        if side < 40:
            return
        cx = self.width() / 2.0
        cy = self.height() / 2.0 + 6
        radius = side / 2.0
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        segs = self._segments or (0,)
        n = len(segs)
        sweep = 360.0 / n
        start = -90.0 - sweep / 2.0

        for i, value in enumerate(segs):
            color = WHEEL_SEGMENT_COLORS[i % len(WHEEL_SEGMENT_COLORS)]
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#D4AF37"), 2))
            painter.drawPie(rect, int(start * 16), int(-sweep * 16))
            mid = math.radians(start - sweep / 2.0)
            lx = cx + math.cos(mid) * radius * 0.62
            ly = cy - math.sin(mid) * radius * 0.62
            painter.setPen(QColor("#FFFFFF"))
            font = QFont(self.font())
            font.setBold(True)
            font.setPointSize(max(8, int(radius / 14)))
            painter.setFont(font)
            painter.drawText(
                QRectF(lx - 28, ly - 12, 56, 24),
                int(Qt.AlignmentFlag.AlignCenter),
                str(value),
            )
            start -= sweep

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#D4AF37"), 5))
        painter.drawEllipse(rect)

        tip = QPointF(cx, cy - radius + 2)
        left = QPointF(cx - 10, cy - radius - 14)
        right = QPointF(cx + 10, cy - radius - 14)
        painter.setBrush(QBrush(QColor("#C62828")))
        painter.setPen(QPen(QColor("#7F0000"), 1))
        painter.drawPolygon(QPolygonF([tip, left, right]))


def _field_edit(*, read_only: bool = True, pink: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setReadOnly(read_only)
    edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if pink:
        edit.setStyleSheet(
            "QLineEdit { background: #f7c6c6; border: 1px solid #c98989; "
            "padding: 2px 6px; min-width: 72px; }"
        )
    else:
        edit.setStyleSheet(
            "QLineEdit { background: #ffffff; border: 1px solid #b0b0b0; "
            "padding: 2px 6px; min-width: 72px; }"
        )
    return edit


def build_magic_wheel_tab() -> tuple[QWidget, dict]:
    """Build the Magic Wheel Data panel; return (widget, handle dict)."""
    body = QWidget()
    outer = QVBoxLayout(body)
    outer.setContentsMargins(12, 8, 12, 8)
    outer.setSpacing(8)

    title = QLabel("Magic Wheel Data")
    title_font = QFont(title.font())
    title_font.setPointSize(max(11, title_font.pointSize() + 2))
    title_font.setBold(True)
    title.setFont(title_font)
    title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    outer.addWidget(title)

    top = QHBoxLayout()
    top.setSpacing(16)
    money_limit = _field_edit(pink=True)
    money_avg = _field_edit()
    game = QComboBox()
    game.setMinimumWidth(140)
    for label, widget in (
        ("Money Limit", money_limit),
        ("Money Wheel Avg", money_avg),
        ("Game", game),
    ):
        col = QVBoxLayout()
        col.setSpacing(2)
        cap = QLabel(label)
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(cap)
        col.addWidget(widget)
        top.addLayout(col)
    top.addStretch(1)
    outer.addLayout(top)

    mid = QHBoxLayout()
    mid.setSpacing(16)

    stats_box = QGroupBox()
    stats_form = QFormLayout(stats_box)
    stats_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    times = _field_edit()
    spins = _field_edit()
    total_win = _field_edit()
    avg_win = _field_edit()
    stats_form.addRow("Times Triggered", times)
    stats_form.addRow("Total Num Spins", spins)
    stats_form.addRow("Total Win Amount", total_win)
    stats_form.addRow("Avg. Win Amount", avg_win)
    mid.addWidget(stats_box, 0)

    credits_box = QGroupBox("Magic Wheel Credits")
    credits_layout = QVBoxLayout(credits_box)
    credits_list = QListWidget()
    credits_list.setStyleSheet(
        "QListWidget { background: #f3ead6; border: 1px solid #c4b48a; }"
    )
    credits_list.setMinimumWidth(150)
    credits_list.setMinimumHeight(180)
    credits_layout.addWidget(credits_list)
    mid.addWidget(credits_box, 0)

    wheel = MagicWheelGraphic()
    mid.addWidget(wheel, 1)

    outer.addLayout(mid, 1)

    handles = {
        "money_limit": money_limit,
        "money_avg": money_avg,
        "game": game,
        "times": times,
        "spins": spins,
        "total_win": total_win,
        "avg_win": avg_win,
        "credits_list": credits_list,
        "wheel": wheel,
        "title": title,
    }
    return body, handles


def apply_magic_wheel_data(
    handles: dict,
    data: MagicWheelData,
    *,
    game_themes: Iterable[str] | None = None,
) -> None:
    """Paint data into widgets created by build_magic_wheel_tab()."""
    money_limit: QLineEdit = handles["money_limit"]
    money_avg: QLineEdit = handles["money_avg"]
    game: QComboBox = handles["game"]
    times: QLineEdit = handles["times"]
    spins: QLineEdit = handles["spins"]
    total_win: QLineEdit = handles["total_win"]
    avg_win: QLineEdit = handles["avg_win"]
    credits_list: QListWidget = handles["credits_list"]
    wheel: MagicWheelGraphic = handles["wheel"]

    money_limit.setText(data.money_limit_display)
    money_avg.setText(data.money_wheel_avg_display)

    themes = list(game_themes or [])
    if data.game_theme_id and data.game_theme_id not in themes:
        themes.insert(0, data.game_theme_id)
    if not themes and data.game_theme_id:
        themes = [data.game_theme_id]
    blocked = game.blockSignals(True)
    game.clear()
    game.addItems(themes)
    if data.game_theme_id:
        idx = game.findText(data.game_theme_id)
        if idx >= 0:
            game.setCurrentIndex(idx)
    game.blockSignals(blocked)

    def _num(v: int | None) -> str:
        return "-" if v is None else str(v)

    times.setText(_num(data.times_triggered))
    spins.setText(_num(data.total_num_spins))
    total_win.setText(_num(data.total_win_amount))
    avg_win.setText(_num(data.avg_win_amount))

    credits_list.clear()
    for credit in data.credit_list:
        credits_list.addItem(QListWidgetItem(f"Credits: {credit}"))

    wheel.set_segments(data.wheel_segments)