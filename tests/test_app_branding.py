"""Tests for Log Investigator branding assets."""

from __future__ import annotations


def test_app_icon_assets_exist() -> None:
    from gui.app_branding import icon_ico_path, icon_png_path

    assert icon_png_path().is_file(), "assets/log_investigator_icon.png missing"
    assert icon_ico_path().is_file(), "assets/log_investigator.ico missing (run scripts/ensure_app_icon.py)"


def test_app_icon_loads_in_qt() -> None:
    import sys

    from PySide6.QtWidgets import QApplication

    from gui.app_branding import app_icon, app_icon_pixmap, icon_png_path

    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    icon = app_icon()
    assert not icon.isNull()
    pix = app_icon_pixmap(32)
    assert not pix.isNull()
    assert pix.width() >= 16
    assert icon_png_path().name == "log_investigator_icon.png"