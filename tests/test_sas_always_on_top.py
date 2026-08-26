"""Always-on-top toggle: View menu, right-click menu, and launch default."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from PySide6.QtCore import QSettings, QThreadPool, Qt
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    _KEY_ALWAYS_ON_TOP,
    _SAS_VERIFY_SETTINGS_GROUP,
    SasVerifyDialog,
)


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: "",
    )


@pytest.fixture
def dlg():
    """Dialog on a local root, with the real saved preference left untouched."""
    app = QApplication.instance() or QApplication([])
    # The dialog stores its prefs in the application-scoped QSettings; without a
    # name that store is unwritable and every read comes back as the default.
    app.setOrganizationName("Goldclub")
    app.setApplicationName("LogInvestigatorTests")
    s = QSettings()
    s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
    saved = s.value(_KEY_ALWAYS_ON_TOP, None)
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
            s.remove(_KEY_ALWAYS_ON_TOP)
        else:
            s.setValue(_KEY_ALWAYS_ON_TOP, saved)
        s.endGroup()
        s.sync()


def test_always_on_top_is_on_at_each_launch(dlg) -> None:
    assert dlg._always_on_top_action.isCheckable()
    assert dlg._always_on_top_action.isChecked() is True
    assert dlg._always_on_top is True


def test_right_click_offers_the_same_toggle_as_the_view_menu(dlg) -> None:
    """One action, two entry points — the check mark cannot disagree."""
    assert dlg._always_on_top_action in dlg._window_context_menu().actions()


def test_toggling_the_action_applies_and_remembers_it(dlg) -> None:
    dlg._always_on_top_action.setChecked(False)
    assert dlg._always_on_top is False
    assert dlg._read_always_on_top_pref() is False

    dlg._always_on_top_action.setChecked(True)
    assert dlg._always_on_top is True
    assert dlg._read_always_on_top_pref() is True


def test_launch_forces_on_even_when_pref_was_off(dlg) -> None:
    dlg._always_on_top_action.setChecked(False)
    assert dlg._read_always_on_top_pref() is False

    other = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=r"G:\Goldclub\var"
    )
    other._prefetch_started = True
    try:
        assert other._always_on_top_action.isChecked() is True
        assert other._always_on_top is True
    finally:
        other.deleteLater()


def test_qt_window_hint_is_the_fallback_when_the_native_call_fails(
    dlg, monkeypatch
) -> None:
    """On a non-Windows host (or before the HWND exists) the flag does the work."""
    import gui.sas_verify_dialog as mod

    monkeypatch.setattr(mod, "set_window_always_on_top", lambda widget, on: False)

    dlg._apply_always_on_top(True)
    assert bool(dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    dlg._apply_always_on_top(False)
    assert not bool(dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


def test_reapply_only_acts_when_the_toggle_is_on(dlg) -> None:
    """A show / restyle can recreate the HWND, but must not switch the band on."""
    calls: list[bool] = []
    dlg._apply_always_on_top = lambda on: calls.append(on)  # type: ignore[method-assign]

    dlg._always_on_top = False
    dlg._reapply_always_on_top()
    assert calls == []

    dlg._always_on_top = True
    dlg._reapply_always_on_top()
    assert calls == [True]
