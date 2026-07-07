"""
Semantic colors derived from the active QPalette so widgets stay readable in light/dark/system themes.

Use these instead of hardcoded ``gui.theme`` hex for labels, LEDs, and dynamic stylesheets.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette


def surface_is_light(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() > 127


def led_color(palette: QPalette, mode: str) -> QColor:
    """Status LED fill (green / orange / red / grey)."""
    light = surface_is_light(palette)
    if mode == "green":
        return QColor(22, 121, 63) if light else QColor(122, 195, 130)
    if mode == "orange":
        return QColor(179, 95, 0) if light else QColor(232, 165, 75)
    if mode == "red":
        return QColor(183, 28, 28) if light else QColor(255, 107, 107)
    return QColor(90, 90, 90) if light else QColor(150, 150, 150)


def health_border_color(palette: QPalette, health: str) -> QColor:
    """Machine card health accent (green / yellow / red / grey)."""
    light = surface_is_light(palette)
    if health == "green":
        return QColor(22, 121, 63) if light else QColor(122, 195, 130)
    if health in ("yellow", "orange"):
        return QColor(179, 95, 0) if light else QColor(232, 165, 75)
    if health == "red":
        return QColor(183, 28, 28) if light else QColor(255, 107, 107)
    return QColor(120, 120, 120) if light else QColor(142, 142, 142)


# State timeline row tint: keep alpha in 40–60 so Text stays readable on light/dark.
_STATE_TIMELINE_FILL_ALPHA = 52


def state_timeline_block_fill(palette: QPalette, health: str) -> QColor:
    """Pastel health background for state-machine timeline blocks."""
    if health == "error":
        c = QColor(text_danger(palette))
    elif health == "warn":
        c = QColor(text_warning(palette))
    else:
        c = QColor(text_success(palette))
    c.setAlpha(_STATE_TIMELINE_FILL_ALPHA)
    return c


def state_timeline_block_border(palette: QPalette, health: str) -> QColor:
    """Saturated border for state timeline blocks (matches machine-card health hues)."""
    if health == "error":
        return health_border_color(palette, "red")
    if health == "warn":
        return health_border_color(palette, "yellow")
    return health_border_color(palette, "green")


def text_success(palette: QPalette) -> QColor:
    light = surface_is_light(palette)
    return QColor(22, 121, 63) if light else QColor(122, 195, 130)


def text_warning(palette: QPalette) -> QColor:
    light = surface_is_light(palette)
    return QColor(153, 87, 0) if light else QColor(232, 165, 75)


def text_danger(palette: QPalette) -> QColor:
    light = surface_is_light(palette)
    return QColor(183, 28, 28) if light else QColor(255, 107, 107)


def muted_text(palette: QPalette) -> QColor:
    """Secondary text; prefers PlaceholderText, falls back to Mid."""
    c = palette.color(QPalette.ColorRole.PlaceholderText)
    if c.alpha() == 0:
        c = palette.color(QPalette.ColorRole.Mid)
    return c


def memory_usage_color(palette: QPalette, pct: float | None) -> QColor:
    """RAM %% column: danger / warning / muted by threshold."""
    if pct is None or pct != pct:  # NaN guard
        return muted_text(palette)
    if pct > 90:
        return text_danger(palette)
    if pct > 75:
        return text_warning(palette)
    return muted_text(palette)


def is_probable_leak(process_mb: float | None) -> bool:
    """Heuristic: OneHand working set unusually high (MB)."""
    if process_mb is None or process_mb != process_mb:
        return False
    return float(process_mb) > 2500.0


def chip_math_tint(palette: QPalette) -> QColor:
    """Gold/amber accent for math chip (readable on light and dark surfaces)."""
    light = surface_is_light(palette)
    return QColor(145, 104, 22) if light else QColor(210, 175, 65)


def blend_colors(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
    )


def rgba_css(c: QColor, alpha: float) -> str:
    a = max(0.0, min(1.0, alpha))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a:.3f})"


def syntax_timestamp(palette: QPalette) -> QColor:
    """Timestamp rail: dark gray on light surfaces, muted light gray on dark."""
    return QColor(85, 85, 85) if surface_is_light(palette) else QColor(168, 168, 168)


def syntax_critical(palette: QPalette) -> QColor:
    """ERROR / FATAL / CRITICAL tokens."""
    return QColor("#b30000") if surface_is_light(palette) else QColor("#ff6b6b")


def syntax_warning(palette: QPalette) -> QColor:
    """WARN tokens: dark orange-brown / pastel amber."""
    return QColor(168, 90, 0) if surface_is_light(palette) else QColor(240, 192, 128)


def syntax_info(palette: QPalette) -> QColor:
    """INFO level: deep blue / pastel sky."""
    return QColor(0, 90, 158) if surface_is_light(palette) else QColor(126, 184, 212)


def syntax_debug(palette: QPalette) -> QColor:
    """DEBUG level: forest / muted sage."""
    return QColor(46, 107, 50) if surface_is_light(palette) else QColor(168, 201, 160)


def syntax_trace_id(palette: QPalette) -> QColor:
    """Bracketed tags / trace-style tokens: dark teal / pastel teal."""
    return QColor(0, 105, 92) if surface_is_light(palette) else QColor(107, 196, 184)


def syntax_exception_foreground(palette: QPalette) -> QColor:
    return QColor(139, 0, 0) if surface_is_light(palette) else QColor(240, 176, 176)


def syntax_exception_background(palette: QPalette) -> QColor:
    return QColor(252, 232, 232) if surface_is_light(palette) else QColor(61, 40, 40)


def syntax_gold(palette: QPalette) -> QColor:
    """Game / credit highlight lines."""
    return QColor(139, 105, 20) if surface_is_light(palette) else QColor(212, 184, 150)


def incident_timeline_tick_color(palette: QPalette, severity: str | None) -> QColor:
    """Mini-timeline tick color from incident severity."""
    s = (severity or "").strip().upper()
    if s in ("CRITICAL", "FATAL"):
        return text_danger(palette)
    if s in ("MEDIUM", "WARN", "WARNING"):
        return text_warning(palette)
    if s == "LOW":
        return text_success(palette)
    if s in ("INFO", "DEBUG"):
        return syntax_info(palette)
    return muted_text(palette)


def error_border_color(palette: QPalette) -> QColor:
    """Validation error outline (e.g. invalid regex field)."""
    return QColor(176, 0, 32) if surface_is_light(palette) else QColor(244, 71, 71)


def filter_chip_stylesheet(palette: QPalette, accent: QColor) -> str:
    """
    QToolButton chip: structure + palette-based neutrals; checked state tinted with ``accent``.
    """
    base = palette.color(QPalette.ColorRole.Base)
    muted = palette.color(QPalette.ColorRole.PlaceholderText)
    text = palette.color(QPalette.ColorRole.Text)
    mid = palette.color(QPalette.ColorRole.Mid)
    hover_bg = blend_colors(base, accent, 0.22)
    checked_fill = rgba_css(accent, 0.38)
    border = accent.name()
    return f"""
        QToolButton {{
            background-color: {base.name()};
            color: {muted.name()};
            border: 1px solid {mid.name()};
            border-radius: 14px;
            padding: 4px 12px;
            font-size: 12px;
            font-weight: 500;
        }}
        QToolButton:hover {{
            background-color: {hover_bg.name()};
            color: {text.name()};
        }}
        QToolButton:checked {{
            background-color: {checked_fill};
            color: {text.name()};
            border: 2px solid {border};
            font-weight: 600;
        }}
        QToolButton:checked:hover {{
            background-color: {checked_fill};
            border: 2px solid {border};
        }}
    """
