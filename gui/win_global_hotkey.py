"""System-wide hotkeys for the SAS Verify window.

The meters window is designed to live on a second, non-touch monitor while the
operator plays the game on monitor 1. There is no mouse there, so keyboard
combinations must work even while the game window (OneHand / Godot) holds
focus. A plain ``QShortcut`` only fires when this app is active, so the primary
mechanism is Windows ``RegisterHotKey`` system-wide hotkeys delivered through a
single Qt native event filter.

Chosen combos (three modifiers + a letter — not bound by OneHand or the Godot
roulette client, verified against their debug key maps, so they never collide
with gameplay input):

* ``Ctrl+Alt+Shift+K`` — kill / close the app.
* ``Ctrl+Alt+Shift+M`` — move the window to the other monitor and back.
* ``Ctrl+Alt+Shift+T`` — step to the next meter tab (wraps at the end).

Tab stepping is one-directional on purpose: the base combo already holds Shift,
so there is no ``+Shift`` left to mean "backwards", and cycling seven tabs
forwards reaches any of them. Precise jumps (``Ctrl+1``..``Ctrl+7``) and
backwards stepping are plain in-app shortcuts, which is enough when the window
has focus.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtWidgets import QApplication, QWidget

# WinUser.h modifier + message constants.
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_VK_K = 0x4B  # virtual-key code for 'K'
_VK_M = 0x4D  # virtual-key code for 'M'
_VK_T = 0x54  # virtual-key code for 'T'

# Ctrl+Alt+Shift+<key>. NOREPEAT so holding the combo fires once, not a storm.
_BASE_MODS = _MOD_CONTROL | _MOD_ALT | _MOD_SHIFT | _MOD_NOREPEAT

KILL_HOTKEY_MODS = _BASE_MODS
KILL_HOTKEY_VK = _VK_K
KILL_HOTKEY_LABEL = "Ctrl+Alt+Shift+K"

MOVE_HOTKEY_MODS = _BASE_MODS
MOVE_HOTKEY_VK = _VK_M
MOVE_HOTKEY_LABEL = "Ctrl+Alt+Shift+M"

TAB_HOTKEY_MODS = _BASE_MODS
TAB_HOTKEY_VK = _VK_T
TAB_HOTKEY_LABEL = "Ctrl+Alt+Shift+T"

# Per-window ids; only have to be unique within this thread's hotkeys.
KILL_HOTKEY_ID = 0xB0B0
MOVE_HOTKEY_ID = 0xB0B1
TAB_HOTKEY_ID = 0xB0B2


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Register one or more system-wide hotkeys, delivered even out of focus.

    Add definitions with :meth:`add`, then call :meth:`install` once the window
    has a native HWND (after ``show``). ``install`` returns True when at least
    one hotkey was registered (Windows only). Callers should keep in-app
    ``QShortcut`` fallbacks for non-Windows and for the rare case where the OS
    refuses a combo another process already owns.
    """

    def __init__(self, widget: QWidget) -> None:
        super().__init__()
        self._widget = widget
        self._defs: list[tuple[int, int, int, Callable[[], None]]] = []
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._registered_ids: list[int] = []
        self._hwnd = 0
        self._installed = False

    def add(
        self, *, hotkey_id: int, mods: int, vk: int, on_fired: Callable[[], None]
    ) -> None:
        self._defs.append((int(hotkey_id), int(mods), int(vk), on_fired))

    def install(self) -> bool:
        if sys.platform != "win32" or self._widget is None or self._installed:
            return False
        try:
            self._widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
            hwnd = int(self._widget.winId())
        except Exception:
            return False
        if not hwnd:
            return False
        for hotkey_id, mods, vk, cb in self._defs:
            try:
                ok = bool(
                    ctypes.windll.user32.RegisterHotKey(
                        wintypes.HWND(hwnd), hotkey_id, mods, vk
                    )
                )
            except Exception:
                ok = False
            if ok:
                self._callbacks[hotkey_id] = cb
                self._registered_ids.append(hotkey_id)
        if not self._registered_ids:
            return False
        self._hwnd = hwnd
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self)
        self._installed = True
        return True

    def remove(self) -> None:
        if self._hwnd and sys.platform == "win32":
            for hotkey_id in self._registered_ids:
                try:
                    ctypes.windll.user32.UnregisterHotKey(
                        wintypes.HWND(self._hwnd), hotkey_id
                    )
                except Exception:
                    pass
        self._registered_ids.clear()
        self._callbacks.clear()
        self._hwnd = 0
        if self._installed:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self)
            self._installed = False

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        if sys.platform != "win32":
            return False, 0
        try:
            if bytes(event_type) != b"windows_generic_MSG":
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_HOTKEY:
                cb = self._callbacks.get(int(msg.wParam))
                if cb is not None:
                    cb()
                    return True, 0
        except Exception:
            return False, 0
        return False, 0
