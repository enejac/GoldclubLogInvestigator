"""Windows 10/11 native caption bar (title bar) dark/light sync."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from config_manager import THEME_DARK, THEME_LIGHT

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19


def os_prefers_dark_theme() -> bool:
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
        except Exception:
            pass
    return False


def title_bar_is_dark(theme_name: str) -> bool:
    t = (theme_name or "").strip()
    if t == THEME_DARK:
        return True
    if t == THEME_LIGHT:
        return False
    return os_prefers_dark_theme()


def _set_dwm_dark_title_bar(hwnd: int, *, dark: bool) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    dwmapi = ctypes.windll.dwmapi
    value = ctypes.c_int(1 if dark else 0)
    for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            hr = dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(attr),
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if hr == 0:
                return
        except Exception:
            continue


def apply_title_bar_theme(widget: QWidget, theme_name: str) -> None:
    if sys.platform != "win32" or not widget.isWindow():
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    _set_dwm_dark_title_bar(hwnd, dark=title_bar_is_dark(theme_name))


def sync_all_title_bars(app: QApplication | None, theme_name: str) -> None:
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget.isWindow() and widget.isVisible():
            apply_title_bar_theme(widget, theme_name)


class _TitleBarNotifyHook(QObject):
    """Wrap QApplication.notify so every top-level Show gets a caption sync."""

    def __init__(self, app: QApplication, theme_resolver: Callable[[], str]) -> None:
        super().__init__(app)
        self._app = app
        self._theme_resolver = theme_resolver
        self._original_notify = app.notify

        def notify(receiver: QObject, event: QEvent) -> bool:
            if (
                event.type() == QEvent.Type.Show
                and isinstance(receiver, QWidget)
                and receiver.isWindow()
            ):
                apply_title_bar_theme(receiver, self._theme_resolver())
            return self._original_notify(receiver, event)

        app.notify = notify  # type: ignore[method-assign]


def install_title_bar_theme_filter(
    app: QApplication, theme_resolver: Callable[[], str]
) -> _TitleBarNotifyHook:
    return _TitleBarNotifyHook(app, theme_resolver)