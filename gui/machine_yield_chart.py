"""Machine Yield 3D donut chart (extruded ring, chamfered edges) for the Game tab."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPalette,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

_LEGEND_WIDTH = 112.0
_ARC_STEPS = 96
# 3D look: vertical squash of the ellipse, wall depth and bevel width vs radius.
_SQUASH = 0.55
_DEPTH_FRAC = 0.30
_CHAMFER_FRAC = 0.12
_INNER_FRAC = 0.52
# Empty Game-tab chart: no bet yet means no Yield/Hold ring to draw.
EMPTY_YIELD_CHART_TEXT = "no games yet"


@dataclass(frozen=True, slots=True)
class YieldChartSlice:
    label: str
    pct: float
    fraction: float
    top_color: str
    side_color: str


# Light mint (yield) + soft gold (hold) — black % labels stay readable on the ring.
_YIELD_TOP = "#99F6E4"
_YIELD_SIDE = "#5EEAD4"
_HOLD_TOP = "#FDE68A"
_HOLD_SIDE = "#FCD34D"


def format_yield_chart_pct(pct: float) -> str:
    if not math.isfinite(pct):
        return "—"
    return f"{pct:.2f} %"


def _finite_pct(value: float | int | None) -> float | None:
    """Coerce a chart percentage; reject None / bool / NaN / Inf."""
    if value is None or isinstance(value, bool):
        return None
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pct):
        return None
    return pct


def build_yield_chart_slices(
    yield_pct: float | None,
    hold_pct: float | None,
) -> tuple[YieldChartSlice, ...]:
    """Legend keeps *real* percentages (hold may be negative when yield > 100%).

    Ring geometry always uses non-negative weights that sum to a part-to-whole:
    - Normal (0 ≤ Y ≤ 100): draw Yield + Hold as their positive % of (Y+H).
    - Yield > 100% (negative hold / house loss): weight by |Yield| and |Hold|
      so the teal Yield wedge stays the majority (Y = 100+|H|). The old
      "100 + |hold|" weights inverted on a massive loss — amber filled ~92%
      of the ring while the legend said Hold −1097%.
    - Yield < 0% (hold > 100%): weight by |Yield| and Hold (symmetric).
    """
    y_real = _finite_pct(yield_pct)
    if y_real is None:
        return ()
    h_real = _finite_pct(hold_pct)
    if h_real is None:
        h_real = 100.0 - y_real
    if h_real < 0.0:
        # House loss: Y and |H| magnitudes; Yield always slightly larger.
        y_draw = abs(y_real)
        h_draw = abs(h_real)
    elif y_real < 0.0:
        y_draw = abs(y_real)
        h_draw = abs(h_real)
    else:
        y_draw = y_real
        h_draw = max(0.0, h_real)
    total = y_draw + h_draw
    if total <= 0.0:
        return ()
    return (
        YieldChartSlice("Yield", y_real, y_draw / total, _YIELD_TOP, _YIELD_SIDE),
        YieldChartSlice("Hold", h_real, h_draw / total, _HOLD_TOP, _HOLD_SIDE),
    )


def chart_layout(rect: QRectF, *, legend_width: float = _LEGEND_WIDTH) -> tuple[QRectF, QRectF]:
    """Split paint area into donut (left) and legend (right)."""
    inset = 10.0
    inner = rect.adjusted(inset, inset, -inset, -inset)
    pie_w = max(140.0, inner.width() - legend_width)
    pie_rect = QRectF(inner.left(), inner.top(), pie_w, inner.height())
    legend_rect = QRectF(
        inner.right() - legend_width + 4.0,
        inner.top(),
        legend_width - 4.0,
        inner.height(),
    )
    return pie_rect, legend_rect


def pie_geometry(
    pie_rect: QRectF,
    *,
    depth_x: float = 0.0,
    depth_y: float = 0.0,
) -> tuple[float, float, float, float, float, float]:
    """Center the squashed 3D donut; returns ``(cx, cy, rx, ry, depth, chamfer)``."""
    del depth_x, depth_y
    cx = pie_rect.center().x()
    usable = min(pie_rect.width(), pie_rect.height())
    rx = usable * 0.46
    ry = rx * _SQUASH
    depth = rx * _DEPTH_FRAC
    # Shift up half the extrusion so ring + wall sit centered in the rect.
    cy = pie_rect.center().y() - depth * 0.5
    chamfer = rx * _CHAMFER_FRAC
    return cx, cy, rx, ry, depth, chamfer


def _ellipse_point(cx: float, cy: float, rx: float, ry: float, deg_from_top_cw: float) -> QPointF:
    rad = math.radians(90.0 - deg_from_top_cw)
    return QPointF(cx + rx * math.cos(rad), cy - ry * math.sin(rad))


_OUTSIDE_LABEL_EXTEND = 1.34
_OUTSIDE_LEADER_START = 1.05


def outside_label_anchor(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    mid_angle_deg: float,
    *,
    extend: float = _OUTSIDE_LABEL_EXTEND,
) -> tuple[QPointF, QPointF, bool]:
    """Leader-line start / label point for a slice's value, drawn *outside* the ring.

    The hub (inside the hole) is not a safe place for a plain dark percentage:
    the far inner wall shows through the hole in its upper half (see
    ``_BACK_WINDOW`` below), so black text landing there sits on a dark
    teal/amber gradient and disappears -- exactly the "not visible in black"
    report. Anchoring the label along the same radial direction as the
    slice's own rim point (scaled outward) keeps it over the widget's flat
    background instead, where the text color always contrasts. Returns
    ``(leader_start, label_point, on_right_half)`` -- the third value picks
    left- vs right-aligned text so the label runs away from the ring.
    """
    rim = _ellipse_point(cx, cy, rx, ry, mid_angle_deg)
    dx, dy = rim.x() - cx, rim.y() - cy
    leader_start = QPointF(cx + dx * _OUTSIDE_LEADER_START, cy + dy * _OUTSIDE_LEADER_START)
    label_point = QPointF(cx + dx * extend, cy + dy * extend)
    return leader_start, label_point, dx >= 0.0


def _slice_segments(start: float, span: float) -> list[tuple[float, float]]:
    """Normalize a slice into non-wrapping [0, 360) segments."""
    a0 = start % 360.0
    a1 = a0 + span
    if a1 <= 360.0:
        return [(a0, a1)]
    return [(a0, 360.0), (0.0, a1 - 360.0)]


def _intersect_ranges(
    start: float, span: float, windows: tuple[tuple[float, float], ...]
) -> list[tuple[float, float]]:
    """Angular intersection of a slice with visibility *windows* (deg from top, cw)."""
    out: list[tuple[float, float]] = []
    for s0, s1 in _slice_segments(start, span):
        for w0, w1 in windows:
            lo = max(s0, w0)
            hi = min(s1, w1)
            if hi - lo > 0.05:
                out.append((lo, hi))
    return out


# Outer wall faces the viewer on the lower half; the inner wall of the far side
# is what shows through the hole (upper half).
_FRONT_WINDOW = ((90.0, 270.0),)
_BACK_WINDOW = ((270.0, 360.0), (0.0, 90.0))


def _arc_path(
    cx: float, cy: float, rx: float, ry: float, a0: float, a1: float
) -> QPainterPath:
    path = QPainterPath()
    steps = max(6, int(_ARC_STEPS * (a1 - a0) / 360.0) + 1)
    for i in range(steps + 1):
        d = a0 + (a1 - a0) * (i / steps)
        pt = _ellipse_point(cx, cy, rx, ry, d)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    return path


def _wall_path(
    cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, depth: float
) -> QPainterPath:
    """Extruded side wall: arc at surface level joined to the same arc *depth* lower."""
    path = QPainterPath()
    steps = max(6, int(_ARC_STEPS * (a1 - a0) / 360.0) + 1)
    for i in range(steps + 1):
        d = a0 + (a1 - a0) * (i / steps)
        pt = _ellipse_point(cx, cy, rx, ry, d)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    for i in range(steps, -1, -1):
        d = a0 + (a1 - a0) * (i / steps)
        pt = _ellipse_point(cx, cy, rx, ry, d)
        path.lineTo(pt.x(), pt.y() + depth)
    path.closeSubpath()
    return path


def _ring_slice_path(
    cx: float,
    cy: float,
    orx: float,
    ory: float,
    irx: float,
    iry: float,
    start: float,
    span: float,
) -> QPainterPath:
    path = QPainterPath()
    steps = max(10, int(_ARC_STEPS * (span / 360.0)))
    for i in range(steps + 1):
        deg = start + span * (i / steps)
        pt = _ellipse_point(cx, cy, orx, ory, deg)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    for i in range(steps, -1, -1):
        deg = start + span * (i / steps)
        path.lineTo(_ellipse_point(cx, cy, irx, iry, deg))
    path.closeSubpath()
    return path


def _surface_is_light(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() >= 140


class MachineYieldChartWidget(QWidget):
    """3D extruded donut chart: Machine Yield vs Machine Hold."""

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
        light = _surface_is_light(self.palette())
        bg = QColor("#F8FAFC" if light else "#1E293B")
        fg_muted = QColor("#64748B" if light else "#94A3B8")
        painter.fillRect(rect, bg)

        if not self._slices:
            painter.setPen(fg_muted)
            font = QFont(self.font())
            font.setPointSize(13)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), EMPTY_YIELD_CHART_TEXT)
            painter.end()
            return

        pie_rect, legend_rect = chart_layout(rect)
        self._draw_donut(painter, pie_rect, light=light)
        self._draw_legend(painter, legend_rect, light=light)
        painter.end()

    def _draw_legend(self, painter: QPainter, legend_rect: QRectF, *, light: bool) -> None:
        text = QColor("#0F172A" if light else "#F1F5F9")
        muted = QColor("#64748B" if light else "#94A3B8")
        card = QColor("#FFFFFF" if light else "#0F172A")
        border = QColor("#E2E8F0" if light else "#334155")

        row_h = 36.0
        box_h = 14.0 + row_h * len(self._slices)
        box_w = min(_LEGEND_WIDTH - 4.0, legend_rect.width())
        y0 = legend_rect.center().y() - box_h / 2.0
        legend = QRectF(legend_rect.left(), y0, box_w, box_h)

        path = QPainterPath()
        path.addRoundedRect(legend, 8.0, 8.0)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(card))
        painter.drawPath(path)

        title_font = QFont(self.font())
        title_font.setPointSize(8)
        title_font.setWeight(QFont.Weight.DemiBold)
        value_font = QFont(self.font())
        value_font.setPointSize(9)
        value_font.setWeight(QFont.Weight.Bold)

        row_y = legend.top() + 10.0
        for sl in self._slices:
            swatch = QRectF(legend.left() + 10.0, row_y + 4.0, 10.0, 10.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(sl.top_color)))
            painter.drawEllipse(swatch)

            painter.setFont(title_font)
            painter.setPen(muted)
            painter.drawText(
                QRectF(legend.left() + 26.0, row_y - 1.0, box_w - 34.0, 14.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                sl.label,
            )
            painter.setFont(value_font)
            painter.setPen(text)
            painter.drawText(
                QRectF(legend.left() + 26.0, row_y + 14.0, box_w - 34.0, 16.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                format_yield_chart_pct(sl.pct),
            )
            row_y += row_h

    def _visible_slice_angles(self) -> list[tuple[YieldChartSlice, float, float]]:
        """(slice, start_deg, span_deg) for slices with a drawable fraction."""
        # Keep any positive draw weight (0.004 hid ~0.3% over-yield wedges).
        visible = [sl for sl in self._slices if sl.fraction > 0.0]
        if not visible:
            return []
        gap = 2.0 if len(visible) > 1 else 0.0
        usable = max(0.0, 360.0 - gap * len(visible))
        out: list[tuple[YieldChartSlice, float, float]] = []
        total = sum(sl.fraction for sl in visible) or 1.0
        cursor = gap * 0.5
        for sl in visible:
            span = (sl.fraction / total) * usable
            out.append((sl, cursor, span))
            cursor += span + gap
        return out

    def _draw_donut(self, painter: QPainter, pie_rect: QRectF, *, light: bool) -> None:
        cx, cy, rx, ry, depth, chamfer = pie_geometry(pie_rect)
        irx = rx * _INNER_FRAC
        iry = ry * _INNER_FRAC
        hub_muted = QColor("#64748B" if light else "#94A3B8")
        angles = self._visible_slice_angles()
        if not angles:
            return

        # Ambient shadow on the "table" under the extruded ring.
        shadow = QRadialGradient(QPointF(cx, cy + depth + ry * 0.30), rx * 1.15)
        shadow.setColorAt(0.35, QColor(15, 23, 42, 60 if light else 110))
        shadow.setColorAt(1.0, QColor(15, 23, 42, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow))
        painter.drawEllipse(QPointF(cx, cy + depth + ry * 0.22), rx * 1.06, ry * 0.85)

        # 1) Inner walls seen through the hole (far side, upper half).
        for sl, start, span in angles:
            side = QColor(sl.side_color)
            for a0, a1 in _intersect_ranges(start, span, _BACK_WINDOW):
                wall = _wall_path(cx, cy, irx, iry, a0, a1, depth)
                grad = QLinearGradient(QPointF(cx, cy - iry), QPointF(cx, cy + depth))
                grad.setColorAt(0.0, side.darker(150))
                grad.setColorAt(1.0, side.darker(185))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPath(wall)

        # 2) Outer walls facing the viewer (lower half), lit from above.
        for sl, start, span in angles:
            side = QColor(sl.side_color)
            for a0, a1 in _intersect_ranges(start, span, _FRONT_WINDOW):
                wall = _wall_path(cx, cy, rx, ry, a0, a1, depth)
                grad = QLinearGradient(QPointF(cx, cy + ry), QPointF(cx, cy + ry + depth))
                grad.setColorAt(0.0, side)
                grad.setColorAt(0.15, side.darker(112))
                grad.setColorAt(1.0, side.darker(150))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(grad))
                painter.drawPath(wall)
                # Chamfered bottom rim: thin dark line closing the extrusion.
                base = _arc_path(cx, cy + depth, rx, ry, a0, a1)
                painter.setPen(QPen(side.darker(175), 1.1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(base)

        # 3) Top surfaces.
        for sl, start, span in angles:
            fill = QColor(sl.top_color)
            side = QColor(sl.side_color)
            top = _ring_slice_path(cx, cy, rx, ry, irx, iry, start, span)
            grad = QLinearGradient(QPointF(cx - rx, cy - ry), QPointF(cx + rx * 0.4, cy + ry))
            grad.setColorAt(0.0, fill.lighter(122 if light else 130))
            grad.setColorAt(0.6, fill)
            grad.setColorAt(1.0, side)
            painter.setPen(QPen(side.darker(120), 0.6))
            painter.setBrush(QBrush(grad))
            painter.drawPath(top)

        # 4) Chamfer (bevel) bands: bright where the edge faces the light (top),
        # shaded at the front — this is what sells the rounded machined rim.
        for sl, start, span in angles:
            fill = QColor(sl.top_color)
            bevel = _ring_slice_path(
                cx,
                cy,
                rx,
                ry,
                rx - chamfer,
                ry - chamfer * _SQUASH,
                start,
                span,
            )
            grad = QConicalGradient(QPointF(cx, cy), 90.0)
            hi = QColor(fill.lighter(150))
            hi.setAlpha(210)
            lo = QColor(fill.darker(112))
            lo.setAlpha(170)
            grad.setColorAt(0.0, hi)
            grad.setColorAt(0.5, lo)
            grad.setColorAt(1.0, hi)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPath(bevel)
            # Inner chamfer: light catches the hole rim on the lower inside.
            inner_arcs = _intersect_ranges(start, span, _FRONT_WINDOW)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(fill.lighter(140), 1.4))
            for a0, a1 in inner_arcs:
                painter.drawPath(_arc_path(cx, cy, irx, iry, a0, a1))

        # 5) Hub label only (kept inside the hole's lower half, which never has
        # the back wall drawn behind it -- see step 1 / _BACK_WINDOW). The
        # numeric value used to live here too, and landed partly on the dark
        # inner-wall gradient from step 1: exactly the "not visible in black"
        # report. Values now live outside the ring instead (step 6), where
        # the background is always flat.
        primary = self._slices[0]
        label_pt_size = max(8, min(11, int(iry * 0.34)))
        label_font = QFont(self.font())
        label_font.setPointSize(label_pt_size)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        label_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(label_font)
        painter.setPen(hub_muted)
        painter.drawText(
            QRectF(cx - rx, cy + iry * 0.02, rx * 2.0, iry * 0.96),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            primary.label.upper(),
        )

        # 6) Value callouts outside the ring, one per slice -- see
        # _draw_outside_value_labels below.
        self._draw_outside_value_labels(painter, pie_rect, cx, cy, rx, ry, angles, light=light)

    def _draw_outside_value_labels(
        self,
        painter: QPainter,
        pie_rect: QRectF,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        angles: list[tuple[YieldChartSlice, float, float]],
        *,
        light: bool,
    ) -> None:
        """Per-slice percentage, anchored outside the ring with a leader line."""
        text_color = QColor("#0F172A" if light else "#F1F5F9")
        line_color = QColor("#94A3B8" if light else "#64748B")
        font = QFont(self.font())
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        fm = QFontMetricsF(font)
        text_h = fm.height()
        gap = 6.0
        left_bound = pie_rect.left() + 2.0
        right_bound = pie_rect.right() - 2.0
        top_bound = pie_rect.top() + 2.0
        bottom_bound = pie_rect.bottom() - 2.0

        for sl, start, span in angles:
            mid = start + span / 2.0
            leader_start, label_pt, on_right = outside_label_anchor(cx, cy, rx, ry, mid)
            text = format_yield_chart_pct(sl.pct)
            text_w = fm.horizontalAdvance(text)

            if on_right:
                text_rect = QRectF(
                    label_pt.x() + gap, label_pt.y() - text_h / 2.0, text_w + 2.0, text_h
                )
                if text_rect.right() > right_bound:
                    text_rect.translate(right_bound - text_rect.right(), 0.0)
            else:
                text_rect = QRectF(
                    label_pt.x() - gap - text_w - 2.0,
                    label_pt.y() - text_h / 2.0,
                    text_w + 2.0,
                    text_h,
                )
                if text_rect.left() < left_bound:
                    text_rect.translate(left_bound - text_rect.left(), 0.0)
            if text_rect.top() < top_bound:
                text_rect.translate(0.0, top_bound - text_rect.top())
            elif text_rect.bottom() > bottom_bound:
                text_rect.translate(0.0, bottom_bound - text_rect.bottom())

            near_x = text_rect.left() - 3.0 if on_right else text_rect.right() + 3.0
            painter.setPen(QPen(line_color, 1.1))
            painter.drawLine(leader_start, QPointF(near_x, text_rect.center().y()))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(sl.top_color)))
            painter.drawEllipse(leader_start, 2.6, 2.6)

            painter.setFont(font)
            painter.setPen(text_color)
            align = Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignLeft if on_right else Qt.AlignmentFlag.AlignRight
            )
            painter.drawText(text_rect, int(align), text)
