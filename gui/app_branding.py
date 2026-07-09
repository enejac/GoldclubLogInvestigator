"""Shared Log Investigator branding: window icon and status-bar badge."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ICON_PNG = "log_investigator_icon.png"
_ICON_ICO = "log_investigator.ico"
_cached_icon: QIcon | None = None


def resource_root() -> Path:
    """Repository root in dev; PyInstaller ``_MEIPASS`` when frozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return _REPO_ROOT


def icon_png_path() -> Path:
    return resource_root() / "assets" / _ICON_PNG


def icon_ico_path() -> Path:
    return resource_root() / "assets" / _ICON_ICO


def app_icon() -> QIcon:
    """Application icon (multi-size when PNG is available)."""
    global _cached_icon
    if _cached_icon is not None:
        return _cached_icon

    png = icon_png_path()
    if png.is_file():
        base = QPixmap(str(png))
        if not base.isNull():
            from PySide6.QtCore import Qt

            icon = QIcon()
            for size in (16, 24, 32, 48, 64, 128, 256):
                icon.addPixmap(
                    base.scaled(
                        size,
                        size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            _cached_icon = icon
            return icon

    ico = icon_ico_path()
    if ico.is_file():
        _cached_icon = QIcon(str(ico))
        return _cached_icon

    _cached_icon = QIcon()
    return _cached_icon


def app_icon_pixmap(size: int) -> QPixmap:
    return app_icon().pixmap(size, size)


def apply_app_icon(app: QApplication | None = None) -> None:
    target = app or QApplication.instance()
    if target is not None:
        target.setWindowIcon(app_icon())


def apply_window_branding(window: QWidget) -> None:
    window.setWindowIcon(app_icon())


def status_bar_brand_pixmap(*, size: int = 18) -> QPixmap:
    return app_icon_pixmap(size)