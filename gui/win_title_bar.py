"""Windows 10/11 native caption bar (title bar) dark/light sync."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
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


_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def set_window_always_on_top(widget: QWidget, on: bool) -> bool:
    """Move a window in or out of the Windows topmost band; False = unavailable.

    Preferred over ``Qt.WindowStaysOnTopHint``, which recreates the native
    window: that drops keyboard focus, re-runs the caption theming and
    repaints the whole dialog on every toggle. ``SetWindowPos`` only changes
    the z-order band.
    """
    if sys.platform != "win32" or widget is None:
        return False
    try:
        if not widget.isWindow():
            return False
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        hwnd = int(widget.winId())
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        return bool(
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                ctypes.c_void_p(_HWND_TOPMOST if on else _HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
        )
    except Exception:
        return False


def apply_title_bar_theme(widget: QWidget, theme_name: str) -> None:
    try:
        from shiboken6 import isValid

        if widget is None or not isValid(widget):
            return
    except Exception:
        if widget is None:
            return
    try:
        if sys.platform != "win32" or not widget.isWindow():
            return
    except RuntimeError:
        # C++ QWidget already deleted (common after closing a transient window).
        return
    try:
        # Ensure a native window exists before querying HWND.
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        hwnd = int(widget.winId())
    except Exception:
        return
    _set_dwm_dark_title_bar(hwnd, dark=title_bar_is_dark(theme_name))


def sync_all_title_bars(app: QApplication | None, theme_name: str) -> None:
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if not widget.isWindow():
            continue
        # Apply even before first show so the caption is correct on open.
        apply_title_bar_theme(widget, theme_name)


def schedule_title_bar_theme(widget: QWidget, theme_name: str) -> None:
    """Re-apply after the event loop tick (HWND may be recreated by setStyleSheet)."""
    apply_title_bar_theme(widget, theme_name)

    def _safe_apply() -> None:
        apply_title_bar_theme(widget, theme_name)

    QTimer.singleShot(0, _safe_apply)
    QTimer.singleShot(50, _safe_apply)

class _TitleBarNotifyHook(QObject):
    """Wrap QApplication.notify so Show / WinIdChange re-sync the caption."""

    def __init__(self, app: QApplication, theme_resolver: Callable[[], str]) -> None:
        super().__init__(app)
        self._app = app
        self._theme_resolver = theme_resolver
        self._original_notify = app.notify

        def notify(receiver: QObject, event: QEvent) -> bool:
            et = event.type()
            if (
                et in (QEvent.Type.Show, QEvent.Type.WinIdChange, QEvent.Type.Polish)
                and isinstance(receiver, QWidget)
            ):
                try:
                    from shiboken6 import isValid

                    alive = isValid(receiver)
                except Exception:
                    alive = True
                if alive:
                    try:
                        if receiver.isWindow():
                            apply_title_bar_theme(receiver, self._theme_resolver())
                    except RuntimeError:
                        pass
            return self._original_notify(receiver, event)

        app.notify = notify  # type: ignore[method-assign]


def install_title_bar_theme_filter(
    app: QApplication, theme_resolver: Callable[[], str]
) -> _TitleBarNotifyHook:
    return _TitleBarNotifyHook(app, theme_resolver)
