"""3D-style Machine Yield pie chart for the SAS Verify Game tab."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

_LEGEND_WIDTH = 98.0
_ARC_STEPS = 48


@dataclass(frozen=True, slots=True)
class YieldChartSlice:
    label: str
    pct: float
    fraction: float
    top_color: str
    side_color: str


_YIELD_TOP = "#A8C8E8"
_YIELD_SIDE = "#5A8FBF"
_HOLD_TOP = "#E8B4B4"
_HOLD_SIDE = "#B56A6A"


def format_yield_chart_pct(pct: float) -> str:
    return f"{pct:.2f} %"


def build_yield_chart_slices(
    yield_pct: float | None,
    hold_pct: float | None,
) -> tuple[YieldChartSlice, ...]:
    if yield_pct is None:
        return ()
    hold = hold_pct if hold_pct is not None else max(0.0, 100.0 - float(yield_pct))
    y = max(0.0, float(yield_pct))
    h = max(0.0, float(hold))
    total = y + h
    if total <= 0:
        return ()
    return (
        YieldChartSlice("Yield", y, y / total, _YIELD_TOP, _YIELD_SIDE),
        YieldChartSlice("Hold", h, h / total, _HOLD_TOP, _HOLD_SIDE),
    )


def chart_layout(rect: QRectF, *, legend_width: float = _LEGEND_WIDTH) -> tuple[QRectF, QRectF]:
    """Split paint area into pie (left) and legend (right)."""
    inset = 6.0
    inner = rect.adjusted(inset, inset, -inset, -inset)
    pie_w = max(140.0, inner.width() - legend_width)
    pie_rect = QRectF(inner.left(), inner.top(), pie_w, inner.height())
    legend_rect = QRectF(inner.right() - legend_width + inset, inner.top(), legend_width - inset, inner.height())
    return pie_rect, legend_rect


def pie_geometry(
    pie_rect: QRectF,
    *,
    depth_x: float = 8.0,
    depth_y: float = 12.0,
) -> tuple[float, float, float, float, float, float]:
    """Center the pie in *pie_rect*, reserving vertical room for 3D depth."""
    cx = pie_rect.center().x()
    cy = pie_rect.center().y() - depth_y * 0.2
    usable = min(pie_rect.width(), pie_rect.height() - depth_y)
    rx = usable * 0.40
    ry = rx * 0.58
    return cx, cy, rx, ry, depth_x, depth_y


def _ellipse_point(cx: float, cy: float, rx: float, ry: float, deg_from_top_cw: float) -> QPointF:
    rad = math.radians(90.0 - deg_from_top_cw)
    return QPointF(cx + rx * math.cos(rad), cy - ry * math.sin(rad))


def _shift_depth(pt: QPointF, depth_x: float, depth_y: float) -> QPointF:
    return QPointF(pt.x() + depth_x, pt.y() + depth_y)


def _wedge_polygon(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start_deg: float,
    span_deg: float,
    *,
    steps: int = _ARC_STEPS,
) -> list[QPointF]:
    pts = [QPointF(cx, cy)]
    for i in range(steps + 1):
        deg = start_deg + span_deg * (i / steps)
        pts.append(_ellipse_point(cx, cy, rx, ry, deg))
    return pts


class MachineYieldChartWidget(QWidget):
    """3D cake pie chart: Machine Yield vs Machine Hold."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slices: tuple[YieldChartSlice, ...] = ()
        self.setMinimumSize(280, 220)
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding)

    def set_values(self, yield_pct: float | None, hold_pct: float | None) -> None:
        self._slices = build_yield_chart_slices(yield_pct, hold_pct)
        self.update()

    def has_data(self) -> bool:
        return bool(self._slices)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor("#ffffff"))
        if not self._slices:
            painter.setPen(QColor("#666666"))
            font = QFont()
            font.setPointSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "-")
            painter.end()
            return

        pie_rect, legend_rect = chart_layout(rect)
        self._draw_legend(painter, legend_rect)
        self._draw_pie(painter, pie_rect)
        painter.end()

    def _draw_legend(self, painter: QPainter, legend_rect: QRectF) -> None:
        box_w = min(_LEGEND_WIDTH - 8, legend_rect.width())
        box_h = 16 + 18 * len(self._slices)
        x = legend_rect.left()
        y = legend_rect.top() + 4
        legend = QRectF(x, y, box_w, box_h)
        painter.setPen(QPen(QColor("#808080"), 1))
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawRect(legend)
        row_y = y + 8
        swatch = 10
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        for sl in self._slices:
            painter.fillRect(QRectF(x + 6, row_y, swatch, swatch), QColor(sl.top_color))
            painter.setPen(QColor("#000000"))
            painter.drawText(
                QRectF(x + 20, row_y - 2, box_w - 24, swatch + 4),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                format_yield_chart_pct(sl.pct),
            )
            row_y += 18

    def _draw_pie(self, painter: QPainter, pie_rect: QRectF) -> None:
        cx, cy, rx, ry, depth_x, depth_y = pie_geometry(pie_rect)
        start = 25.0
        cursor = start
        slice_ranges: list[tuple[float, float, YieldChartSlice, int]] = []
        for idx, sl in enumerate(self._slices):
            span = sl.fraction * 360.0
            slice_ranges.append((cursor, span, sl, idx))
            cursor += span

        count = len(slice_ranges)
        draw_order = list(range(count))
        if count == 2:
            draw_order = [1, 0]

        for idx in draw_order:
            s_deg, span, sl, ord_idx = slice_ranges[idx]
            self._draw_slice_sides(
                painter,
                cx,
                cy,
                rx,
                ry,
                depth_x,
                depth_y,
                s_deg,
                span,
                QColor(sl.side_color),
                draw_start_radial=(ord_idx == 0),
                draw_end_radial=(ord_idx == count - 1),
            )

        for s_deg, span, sl, _ord_idx in slice_ranges:
            painter.setPen(QPen(QColor(sl.top_color).darker(108), 1))
            painter.setBrush(QBrush(QColor(sl.top_color)))
            painter.drawPolygon(_wedge_polygon(cx, cy, rx, ry, s_deg, span))

        for s_deg, span, sl, _ord_idx in slice_ranges:
            self._draw_callout(painter, cx, cy, rx, ry, s_deg, span, sl)

    def _draw_slice_sides(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        depth_x: float,
        depth_y: float,
        start_deg: float,
        span_deg: float,
        color: QColor,
        *,
        draw_start_radial: bool,
        draw_end_radial: bool,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        c_top = QPointF(cx, cy)
        c_bot = _shift_depth(c_top, depth_x, depth_y)

        if draw_start_radial:
            p_top = _ellipse_point(cx, cy, rx, ry, start_deg)
            p_bot = _shift_depth(p_top, depth_x, depth_y)
            painter.drawPolygon([c_top, p_top, p_bot, c_bot])

        if draw_end_radial:
            p_top = _ellipse_point(cx, cy, rx, ry, start_deg + span_deg)
            p_bot = _shift_depth(p_top, depth_x, depth_y)
            painter.drawPolygon([c_top, p_top, p_bot, c_bot])

        for i in range(_ARC_STEPS):
            deg_a = start_deg + span_deg * (i / _ARC_STEPS)
            deg_b = start_deg + span_deg * ((i + 1) / _ARC_STEPS)
            p_top_a = _ellipse_point(cx, cy, rx, ry, deg_a)
            p_top_b = _ellipse_point(cx, cy, rx, ry, deg_b)
            p_bot_a = _shift_depth(p_top_a, depth_x, depth_y)
            p_bot_b = _shift_depth(p_top_b, depth_x, depth_y)
            painter.drawPolygon([p_top_a, p_top_b, p_bot_b, p_bot_a])

    def _draw_callout(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        start_deg: float,
        span_deg: float,
        sl: YieldChartSlice,
    ) -> None:
        mid = start_deg + span_deg / 2.0
        rim = _ellipse_point(cx, cy, rx * 1.04, ry * 1.04, mid)
        label_pt = _ellipse_point(cx, cy, rx * 1.28, ry * 1.28, mid)
        box = QRectF(label_pt.x() - 26, label_pt.y() - 9, 52, 18)
        line_color = QColor(sl.side_color)
        painter.setPen(QPen(line_color, 1))
        painter.drawLine(rim, label_pt)
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawRect(box)
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#000000"))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), format_yield_chart_pct(sl.pct))
