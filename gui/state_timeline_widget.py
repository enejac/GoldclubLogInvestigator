"""
Scrollable painted state-machine timeline (MachineState transitions + incident health).

Optimized for large segment counts: caps drawn rows, clips painting to the visible
viewport, and avoids full-widget repaints of tens of thousands of rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QWidget

from config import TIMELINE_MAX_SEGMENTS_DISPLAY
from gui.palette_adapt import (
    muted_text,
    state_timeline_block_border,
    state_timeline_block_fill,
    text_danger,
    text_warning,
)

if TYPE_CHECKING:
    from timeline_engine import StateNode


class StateTimelineCanvas(QWidget):
    """Vertical flow: older states at top, arrows between blocks; click selects time window."""

    nodeClicked = Signal(object)

    ROW_H = 76
    MARGIN = 10
    BLOCK_W = 520
    BANNER_H = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: list[StateNode] = []
        self._source_total = 0
        self._trimmed = False
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )
        self.setMinimumWidth(self.BLOCK_W + 2 * self.MARGIN + 40)

    def set_nodes(self, nodes: list[StateNode], *, source_total: int | None = None) -> None:
        """``source_total`` is pre-cap count (for banner); displayed list may be trimmed."""
        self._source_total = int(source_total) if source_total is not None else len(nodes)
        raw = list(nodes)
        if len(raw) > TIMELINE_MAX_SEGMENTS_DISPLAY:
            self._nodes = raw[-TIMELINE_MAX_SEGMENTS_DISPLAY:]
            self._trimmed = True
        else:
            self._nodes = raw
            self._trimmed = self._source_total > len(self._nodes)
        banner = self.BANNER_H if self._trimmed else 0
        n = len(self._nodes)
        h = max(320, banner + n * self.ROW_H + 80)
        self.setMinimumHeight(h)
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        dirty = event.rect()
        w = max(self.width() - 2 * self.MARGIN, self.BLOCK_W)
        x0 = self.MARGIN
        banner = self.BANNER_H if self._trimmed else 0
        y_base = self.MARGIN + banner

        if self._trimmed:
            painter.setPen(muted_text(pal))
            painter.setFont(QFont())
            msg = (
                f"Showing newest {len(self._nodes)} of {self._source_total} state segments "
                f"(cap {TIMELINE_MAX_SEGMENTS_DISPLAY}; tune TIMELINE_MAX_SEGMENTS_DISPLAY in config.py)."
            )
            painter.drawText(
                QRect(x0, self.MARGIN, w, banner),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                msg,
            )

        font_title = QFont()
        font_title.setBold(True)
        font_small = QFont()
        font_small.setPointSize(9)

        first = (dirty.top() - y_base) // self.ROW_H
        last = (dirty.bottom() - y_base) // self.ROW_H
        first = max(0, first)
        last = min(len(self._nodes) - 1, last)
        if not self._nodes:
            return
        if last < first:
            last = first

        title_pen = pal.color(QPalette.ColorRole.Text)

        for i in range(first, last + 1):
            node = self._nodes[i]
            y = y_base + i * self.ROW_H
            health = node.health
            fill = state_timeline_block_fill(pal, health)
            border = state_timeline_block_border(pal, health)

            rect = QRect(x0, y, w, self.ROW_H - 8)

            painter.setPen(QPen(border, 2))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 6, 6)

            painter.setPen(title_pen)
            painter.setFont(font_title)
            title = node.state_name[:64] + ("…" if len(node.state_name) > 64 else "")
            painter.drawText(rect.adjusted(10, 8, -10, -28), Qt.AlignmentFlag.AlignLeft, title)

            painter.setFont(font_small)
            painter.setPen(muted_text(pal))
            dur = (
                f"{node.duration_sec:.2f}s"
                if node.duration_sec is not None
                else "open-ended"
            )
            sub = f"{node.timestamp or '—'}  ·  {dur}"
            if node.bonus_label:
                sub += f"  ·  ★ {node.bonus_label}"
            painter.drawText(rect.adjusted(10, 30, -10, -12), Qt.AlignmentFlag.AlignLeft, sub)

            trig = (node.trigger_event or "—")[:90]
            painter.drawText(rect.adjusted(10, 48, -10, -6), Qt.AlignmentFlag.AlignLeft, trig)

            if node.incident_count:
                painter.setPen(
                    text_danger(pal) if health == "error" else text_warning(pal)
                )
                painter.drawText(
                    rect.adjusted(w - 120, 8, -10, -28),
                    Qt.AlignmentFlag.AlignRight,
                    f"⚑ {node.incident_count} incident(s)",
                )

            if i < len(self._nodes) - 1:
                ax = x0 + w // 2
                y_arrow = y + self.ROW_H - 4
                painter.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 2))
                painter.drawLine(QPoint(ax, y_arrow), QPoint(ax, y_arrow + 10))
                painter.drawLine(QPoint(ax - 5, y_arrow + 6), QPoint(ax, y_arrow + 10))
                painter.drawLine(QPoint(ax + 5, y_arrow + 6), QPoint(ax, y_arrow + 10))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        banner = self.BANNER_H if self._trimmed else 0
        by = pos.y() - self.MARGIN - banner
        if by < 0:
            super().mousePressEvent(event)
            return
        idx = by // self.ROW_H
        if 0 <= idx < len(self._nodes):
            self.nodeClicked.emit(self._nodes[idx])
        super().mousePressEvent(event)


def build_timeline_scroll_area() -> tuple[QScrollArea, StateTimelineCanvas]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    canvas = StateTimelineCanvas()
    scroll.setWidget(canvas)
    return scroll, canvas
