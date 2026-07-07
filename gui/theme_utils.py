"""
Apply System / Light / Dark appearance via QPalette and optional global stylesheet.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from config_manager import THEME_CHOICES, THEME_DARK, THEME_LIGHT, THEME_SYSTEM
from gui.theme import STYLESHEET
from gui.win_title_bar import sync_all_title_bars

_ALLOWED = frozenset(THEME_CHOICES)


def _normalize(theme_name: str) -> str:
    t = (theme_name or "").strip()
    return t if t in _ALLOWED else THEME_SYSTEM


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
    p.setColor(QPalette.ColorRole.Base, QColor("#121212"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e1e"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
    p.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Button, QColor("#333333"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor("#4daaf9"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2a82da"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return p


def _light_palette() -> QPalette:
    """Explicit light palette so «Light» stays light even on a dark OS theme."""
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#f3f3f3"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
    p.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#e8e8e8"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffe1"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
    p.setColor(QPalette.ColorRole.Text, QColor("#1a1a1a"))
    p.setColor(QPalette.ColorRole.Button, QColor("#e8e8e8"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#1a1a1a"))
    p.setColor(QPalette.ColorRole.Link, QColor("#0066cc"))
    p.setColor(QPalette.ColorRole.Highlight, QColor("#2a82da"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return p


def apply_theme(app: QApplication | None, theme_name: str) -> None:
    """
    - *System*: style standard palette, no app stylesheet (OS / Qt defaults).
    - *Light*: fixed light palette, no app stylesheet.
    - *Dark*: custom dark palette plus existing ``gui.theme`` stylesheet for controls.
    """
    if app is None:
        return
    style = app.style()
    if style is None:
        return
    mode = _normalize(theme_name)
    if mode == THEME_SYSTEM:
        app.setPalette(style.standardPalette())
        app.setStyleSheet("")
    elif mode == THEME_LIGHT:
        app.setPalette(_light_palette())
        app.setStyleSheet("")
    else:
        app.setPalette(_dark_palette())
        app.setStyleSheet(STYLESHEET)
    # Ensure widgets pick up the new palette/stylesheet without a nested refresh storm.
    app.style().unpolish(app)
    app.style().polish(app)
    sync_all_title_bars(app, theme_name)
