"""Move-to-Monitor-2 view toggle and the global kill hotkey."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from PySide6.QtCore import QSettings, QThreadPool
from PySide6.QtWidgets import QApplication

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QGuiApplication

from gui.sas_verify_dialog import (
    _KEY_ON_SECONDARY_MONITOR,
    _SAS_VERIFY_SETTINGS_GROUP,
    SasVerifyDialog,
    fitted_client_rect,
    pick_secondary_screen_index,
)
from gui.win_global_hotkey import (
    KILL_HOTKEY_LABEL,
    KILL_HOTKEY_MODS,
    KILL_HOTKEY_VK,
    MOVE_HOTKEY_LABEL,
    MOVE_HOTKEY_MODS,
    MOVE_HOTKEY_VK,
    GlobalHotkeyManager,
)


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: "",
    )


@pytest.fixture
def dlg():
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("Goldclub")
    app.setApplicationName("LogInvestigatorTests")
    s = QSettings()
    s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
    saved = s.value(_KEY_ON_SECONDARY_MONITOR, None)
    s.endGroup()

    window = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"G:\Goldclub\var"
    )
    window._prefetch_started = True
    window._auto_fetch_toggle.setChecked(False)
    try:
        yield window
    finally:
        window.deleteLater()
        app.processEvents()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        if saved is None:
            s.remove(_KEY_ON_SECONDARY_MONITOR)
        else:
            s.setValue(_KEY_ON_SECONDARY_MONITOR, saved)
        s.endGroup()
        s.sync()


# --- pure screen picker --------------------------------------------------


def test_pick_secondary_screen_index_single_monitor() -> None:
    assert pick_secondary_screen_index(1, 0) == -1
    assert pick_secondary_screen_index(0, 0) == -1


def test_pick_secondary_screen_index_prefers_first_non_primary() -> None:
    assert pick_secondary_screen_index(2, 0) == 1
    assert pick_secondary_screen_index(2, 1) == 0
    assert pick_secondary_screen_index(3, 1) == 0


# --- kill hotkey ---------------------------------------------------------


def test_kill_hotkey_combo_is_ctrl_alt_shift_k() -> None:
    # Ctrl+Alt+Shift = 0x2 | 0x1 | 0x4, plus NOREPEAT (0x4000), VK 'K' = 0x4B.
    assert KILL_HOTKEY_MODS & 0x1  # ALT
    assert KILL_HOTKEY_MODS & 0x2  # CONTROL
    assert KILL_HOTKEY_MODS & 0x4  # SHIFT
    assert KILL_HOTKEY_MODS & 0x4000  # NOREPEAT
    assert KILL_HOTKEY_VK == 0x4B
    assert KILL_HOTKEY_LABEL == "Ctrl+Alt+Shift+K"


def test_move_hotkey_combo_is_ctrl_alt_shift_m() -> None:
    assert MOVE_HOTKEY_MODS & 0x1  # ALT
    assert MOVE_HOTKEY_MODS & 0x2  # CONTROL
    assert MOVE_HOTKEY_MODS & 0x4  # SHIFT
    assert MOVE_HOTKEY_MODS & 0x4000  # NOREPEAT
    assert MOVE_HOTKEY_VK == 0x4D  # 'M'
    assert MOVE_HOTKEY_LABEL == "Ctrl+Alt+Shift+M"


def test_hotkey_manager_install_is_noop_off_windows(monkeypatch) -> None:
    import gui.win_global_hotkey as m

    monkeypatch.setattr(m.sys, "platform", "linux")
    fired: list[str] = []
    mgr = GlobalHotkeyManager(widget=None)
    mgr.add(hotkey_id=1, mods=0, vk=0, on_fired=lambda: fired.append("k"))
    assert mgr.install() is False
    mgr.remove()  # must not raise
    assert fired == []


def test_kill_hotkey_action_closes_dialog(dlg) -> None:
    closed: list[bool] = []
    dlg.close = lambda: closed.append(True)  # type: ignore[method-assign]
    dlg._on_kill_hotkey()
    assert closed == [True]


def test_move_hotkey_sends_the_window_to_the_other_monitor(dlg, monkeypatch) -> None:
    fake_screen = object()
    monkeypatch.setattr(dlg, "_secondary_screen", lambda: fake_screen)
    placed: list[object] = []
    restored: list[bool] = []
    monkeypatch.setattr(dlg, "_move_to_screen", lambda s: placed.append(s))
    monkeypatch.setattr(dlg, "_restore_from_secondary", lambda: restored.append(True))

    # On the primary now -> the press must send it to Monitor 2.
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: False)
    dlg._on_toggle_monitor_hotkey()
    assert placed == [fake_screen] and restored == []
    assert dlg._move_monitor2_action.isChecked() is True

    # On Monitor 2 now -> the press must bring it back.
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: True)
    dlg._on_toggle_monitor_hotkey()
    assert restored == [True]
    assert dlg._move_monitor2_action.isChecked() is False


def test_move_hotkey_moves_even_when_the_menu_check_disagrees(dlg, monkeypatch) -> None:
    """The bug: the window sat on Monitor 2 with the setting off.

    Toggling the action then "moved" it to the monitor it was already on, so
    the press only resized the window instead of hopping screens.
    """
    fake_screen = object()
    monkeypatch.setattr(dlg, "_secondary_screen", lambda: fake_screen)
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: True)  # really on #2
    placed: list[object] = []
    restored: list[bool] = []
    monkeypatch.setattr(dlg, "_move_to_screen", lambda s: placed.append(s))
    monkeypatch.setattr(dlg, "_restore_from_secondary", lambda: restored.append(True))
    dlg._set_move_action_checked(False)  # ...but the setting says otherwise

    dlg._on_toggle_monitor_hotkey()

    assert restored == [True], "must leave the monitor the window is really on"
    assert placed == []


# --- move to monitor 2 ---------------------------------------------------


def test_move_to_monitor2_without_second_screen_reverts(dlg, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(dlg, "_secondary_screen", lambda: None)
    boxes: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: boxes.append("info"))
    )
    dlg._move_monitor2_action.setChecked(True)
    # No second monitor: the check bounces back off and nothing is remembered.
    assert dlg._move_monitor2_action.isChecked() is False
    assert dlg._on_secondary_monitor is False
    assert dlg._read_on_secondary_pref() is False
    assert boxes == ["info"]


def test_move_to_monitor2_places_and_remembers(dlg, monkeypatch) -> None:
    placed: list[object] = []
    fake_screen = object()
    monkeypatch.setattr(dlg, "_secondary_screen", lambda: fake_screen)
    monkeypatch.setattr(dlg, "_move_to_screen", lambda s: placed.append(s))

    dlg._move_monitor2_action.setChecked(True)
    assert placed == [fake_screen]
    assert dlg._on_secondary_monitor is True
    assert dlg._read_on_secondary_pref() is True

    restored: list[bool] = []
    monkeypatch.setattr(dlg, "_restore_from_secondary", lambda: restored.append(True))
    dlg._move_monitor2_action.setChecked(False)
    assert restored == [True]
    assert dlg._on_secondary_monitor is False
    assert dlg._read_on_secondary_pref() is False


def test_move_action_is_in_context_menu(dlg) -> None:
    assert dlg._move_monitor2_action in dlg._window_context_menu().actions()


def test_filling_a_screen_keeps_the_title_bar_on_it() -> None:
    """The bug: setGeometry places the client rect, so the caption went off-screen.

    Filling monitor 2 with its available geometry put the frame at y=-31 — the
    title bar above the top edge, unreachable on a non-touch second monitor.
    """
    avail = QRect(1920, 0, 1920, 1040)  # second monitor to the right
    margins = (8, 31, 8, 8)  # left, top, right, bottom of the native frame
    client = fitted_client_rect(avail, margins)

    frame = QRect(
        client.x() - margins[0],
        client.y() - margins[1],
        client.width() + margins[0] + margins[2],
        client.height() + margins[1] + margins[3],
    )
    assert avail.contains(frame), f"frame {frame} escapes the monitor {avail}"
    assert frame.topLeft() == avail.topLeft()
    assert frame.size() == avail.size()


def test_fitted_rect_centres_and_caps_a_sized_window() -> None:
    avail = QRect(1920, 0, 1920, 1080)
    margins = (8, 31, 8, 8)
    r = fitted_client_rect(avail, margins, QSize(800, 600))
    assert avail.contains(r)
    # An oversized window (it was filling the other monitor) is capped, not clipped.
    big = fitted_client_rect(avail, margins, QSize(5000, 5000))
    assert avail.contains(big)
    assert big.width() <= int(avail.width() * 0.8)


def test_no_frame_means_the_rect_simply_fills(dlg=None) -> None:
    avail = QRect(0, 0, 1920, 1040)
    assert fitted_client_rect(avail, (0, 0, 0, 0)) == avail


def test_move_to_screen_does_not_save_geometry_when_already_there(dlg, monkeypatch) -> None:
    """A session reopened on Monitor 2 must not record Monitor 2 as 'where it was'.

    Otherwise the return trip restores Monitor 2 onto itself and the hotkey
    looks dead.
    """
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: True)
    dlg._pre_move_geometry = None
    dlg._move_to_screen(QGuiApplication.primaryScreen())
    assert dlg._pre_move_geometry is None


def test_restore_from_secondary_always_lands_on_primary(dlg, monkeypatch) -> None:
    """Even when the saved geometry is itself on Monitor 2, come back to Monitor 1."""
    primary = QGuiApplication.primaryScreen()
    # Saved geometry exists but restoring it leaves the window off the primary.
    dlg._pre_move_geometry = dlg.saveGeometry()
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: False)

    dlg._restore_from_secondary()

    assert dlg._pre_move_geometry is None
    assert primary.availableGeometry().contains(dlg.geometry().center())


def test_startup_placement_skips_when_no_second_screen(dlg, monkeypatch) -> None:
    """A saved 'on monitor 2' with only one screen now must not move anything."""
    blocked = dlg._move_monitor2_action.blockSignals(True)
    dlg._move_monitor2_action.setChecked(True)
    dlg._move_monitor2_action.blockSignals(blocked)
    monkeypatch.setattr(dlg, "_secondary_screen", lambda: None)
    moved: list[bool] = []
    monkeypatch.setattr(dlg, "_move_to_screen", lambda s: moved.append(True))

    dlg._apply_startup_monitor_placement()
    assert moved == []
    assert dlg._move_monitor2_action.isChecked() is False


def test_startup_believes_the_window_not_the_setting(dlg, monkeypatch) -> None:
    """Restored session geometry can land on Monitor 2 with the setting off.

    Startup must then report "on Monitor 2", or the first hotkey press tries to
    move it there again and only resizes it.
    """
    fake_screen = object()
    monkeypatch.setattr(dlg, "_secondary_screen", lambda: fake_screen)
    monkeypatch.setattr(dlg, "_window_is_on_screen", lambda s: True)
    moved: list[bool] = []
    monkeypatch.setattr(dlg, "_move_to_screen", lambda s: moved.append(True))
    dlg._set_move_action_checked(False)

    dlg._apply_startup_monitor_placement()

    assert moved == [], "already there — nothing to move"
    assert dlg._on_secondary_monitor is True
    assert dlg._move_monitor2_action.isChecked() is True
