"""Compact session timeline: ticks, click-to-jump, and brush time-range filter."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from gui.palette_adapt import incident_timeline_tick_color, muted_text
from parser import Incident
from timeline_engine import parse_iso_timestamp_sort_key


def _is_critical_severity(severity: str | None) -> bool:
    s = (severity or "").strip().upper()
    return s in ("CRITICAL", "FATAL")


class SessionTimelineWidget(QWidget):
    """Horizontal strip of severity-colored ticks; brush selects a time range."""

    incident_clicked = Signal(int)
    time_range_selected = Signal(object, object)

    _CLICK_DRAG_THRESHOLD_PX = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._incidents: list[Incident] = []
        self._drag_start_x: float | None = None
        self._drag_current_x: float | None = None
        self._active_range_x: tuple[float, float] | None = None
        self._dragging = False
        self.setFixedHeight(30)
        self.setMouseTracking(True)

    def set_incidents(self, incidents: list[Incident]) -> None:
        self._incidents = list(incidents)
        self.update()

    def clear_brush_visual(self) -> None:
        self._active_range_x = None
        self._drag_start_x = None
        self._drag_current_x = None
        self._dragging = False
        self.update()

    def _epoch_rows(self) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        for i, inc in enumerate(self._incidents):
            sk = parse_iso_timestamp_sort_key(inc.timestamp)
            if sk is not None:
                out.append((i, float(sk)))
        return out

    def _span_geometry(
        self,
    ) -> tuple[float, float, float, float, int] | None:
        if len(self._incidents) < 2:
            return None
        epochs = self._epoch_rows()
        if len(epochs) < 2:
            return None
        w = self.width()
        t_min = min(t for _, t in epochs)
        t_max = max(t for _, t in epochs)
        span = t_max - t_min
        if span <= 0:
            span = 1.0
        tick_w = 2
        usable = float(max(w - tick_w, 1))
        return t_min, t_max, span, usable, w

    def _x_to_time(self, x: float) -> float | None:
        g = self._span_geometry()
        if g is None:
            return None
        t_min, _t_max, span, usable, _w = g
        cx = max(0.0, min(float(x), usable))
        return t_min + (cx / usable) * span

    def _paint_ticks(self, painter: QPainter) -> bool:
        w, h = self.width(), self.height()
        pal = self.palette()
        mid_y = h // 2

        g = self._span_geometry()
        if g is None:
            painter.setPen(muted_text(pal))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Not enough data for timeline",
            )
            return False
        t_min, t_max, span, usable, _w = g

        track = QColor(muted_text(pal))
        track.setAlpha(90)
        painter.setPen(track)
        painter.drawLine(0, mid_y, max(w - 1, 0), mid_y)

        tick_w = 2
        tick_h = min(20, h - 4)
        top = mid_y - tick_h // 2

        def x_for_t(t: float) -> int:
            x = int((t - t_min) / span * usable)
            return max(0, min(x, w - tick_w))

        epochs = self._epoch_rows()
        non_crit: list[tuple[int, float]] = []
        crit: list[tuple[int, float]] = []
        for row_i, t in epochs:
            inc = self._incidents[row_i]
            if _is_critical_severity(inc.severity):
                crit.append((row_i, t))
            else:
                non_crit.append((row_i, t))

        def draw_tick(row_i: int, t: float) -> None:
            inc = self._incidents[row_i]
            c = incident_timeline_tick_color(pal, inc.severity)
            painter.fillRect(x_for_t(t), top, tick_w, tick_h, c)

        for row_i, t in non_crit:
            draw_tick(row_i, t)
        for row_i, t in crit:
            draw_tick(row_i, t)
        return True

    def _paint_selection_overlay(self, painter: QPainter) -> None:
        x0: float | None = None
        x1: float | None = None
        if self._dragging and self._drag_start_x is not None and self._drag_current_x is not None:
            x0 = min(self._drag_start_x, self._drag_current_x)
            x1 = max(self._drag_start_x, self._drag_current_x)
        elif self._active_range_x is not None:
            x0, x1 = self._active_range_x
        if x0 is None or x1 is None:
            return
        h = self.height()
        w = self.width()
        left = int(max(0, min(x0, x1)))
        right = int(min(w, max(x0, x1)))
        if right <= left:
            return
        c = QColor(self.palette().color(self.palette().ColorRole.Highlight))
        c.setAlpha(60)
        painter.fillRect(left, 0, right - left, h, c)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._paint_ticks(painter):
            return
        self._paint_selection_overlay(painter)

    def _nearest_row_for_time(self, t_click: float) -> int | None:
        epochs = self._epoch_rows()
        if len(epochs) < 2:
            return None
        best_row = epochs[0][0]
        best_d = abs(epochs[0][1] - t_click)
        for row_i, t in epochs[1:]:
            d = abs(t - t_click)
            if d < best_d:
                best_d = d
                best_row = row_i
        return best_row

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if self._span_geometry() is None:
            super().mousePressEvent(event)
            return
        x = float(event.pos().x())
        self._drag_start_x = x
        self._drag_current_x = x
        self._dragging = True
        self.grabMouse()
        self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and self._drag_start_x is not None:
            self._drag_current_x = float(event.pos().x())
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.releaseMouse()
            start = self._drag_start_x
            cur = self._drag_current_x
            self._dragging = False
            if start is not None and cur is not None:
                if abs(start - cur) < self._CLICK_DRAG_THRESHOLD_PX:
                    t_click = self._x_to_time(start)
                    if t_click is not None:
                        row = self._nearest_row_for_time(t_click)
                        if row is not None:
                            self.incident_clicked.emit(row)
                    self._drag_start_x = None
                    self._drag_current_x = None
                else:
                    lo = min(start, cur)
                    hi = max(start, cur)
                    self._active_range_x = (lo, hi)
                    self._drag_start_x = None
                    self._drag_current_x = None
                    t0 = self._x_to_time(lo)
                    t1 = self._x_to_time(hi)
                    if t0 is not None and t1 is not None:
                        a = min(t0, t1)
                        b = max(t0, t1)
                        dt0 = datetime.fromtimestamp(a, tz=timezone.utc)
                        dt1 = datetime.fromtimestamp(b, tz=timezone.utc)
                        self.time_range_selected.emit(dt0, dt1)
            self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_range_x = None
            self._drag_start_x = None
            self._drag_current_x = None
            self._dragging = False
            self.time_range_selected.emit(None, None)
            self.update()
        super().mouseDoubleClickEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self.update()
        super().changeEvent(event)
