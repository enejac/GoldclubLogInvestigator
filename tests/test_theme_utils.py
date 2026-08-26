"""Theme applier: palette + stylesheet policy."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from config_manager import THEME_DARK, THEME_LIGHT, THEME_SYSTEM
from gui.theme_utils import apply_theme


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_apply_theme_dark_includes_global_stylesheet(qapp: QApplication) -> None:
    apply_theme(qapp, THEME_DARK)
    assert "QWidget" in qapp.styleSheet()


def test_apply_theme_dark_tooltip_stylesheet_is_readable(qapp: QApplication) -> None:
    """Dark QWidget color must not leave pale-yellow tooltips with light text."""
    apply_theme(qapp, THEME_DARK)
    sheet = qapp.styleSheet()
    assert "QToolTip" in sheet
    tip_base = qapp.palette().color(qapp.palette().ColorRole.ToolTipBase)
    tip_text = qapp.palette().color(qapp.palette().ColorRole.ToolTipText)
    assert tip_base.lightness() < 128
    assert abs(tip_base.lightness() - tip_text.lightness()) > 40


def test_apply_theme_system_clears_stylesheet(qapp: QApplication) -> None:
    apply_theme(qapp, THEME_DARK)
    apply_theme(qapp, THEME_SYSTEM)
    assert qapp.styleSheet() == ""


def test_apply_theme_dark_window_color(qapp: QApplication) -> None:
    apply_theme(qapp, THEME_DARK)
    c = qapp.palette().color(qapp.palette().ColorRole.Window)
    assert c.name().lower() == "#1e1e1e"


def test_apply_theme_light_window_color(qapp: QApplication) -> None:
    apply_theme(qapp, THEME_LIGHT)
    c = qapp.palette().color(qapp.palette().ColorRole.Window)
    assert c.name().lower() == "#f3f3f3"


def test_title_bar_is_dark_mapping() -> None:
    from gui.win_title_bar import title_bar_is_dark

    assert title_bar_is_dark(THEME_DARK) is True
    assert title_bar_is_dark(THEME_LIGHT) is False
