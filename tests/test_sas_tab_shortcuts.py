"""Keyboard navigation across the SAS verify meter tabs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    TAB_ACCOUNTING,
    TAB_BILLS,
    TAB_MAGICWHEEL,
    TAB_SECURITY,
    _METER_TAB_NAMES,
    SasVerifyDialog,
)
from gui.win_global_hotkey import TAB_HOTKEY_LABEL, TAB_HOTKEY_MODS, TAB_HOTKEY_VK


def _fake_vm() -> SimpleNamespace:
    return SimpleNamespace(
        current_product_name="GUI-Test",
        get_gm2u_value_for_sas_code=lambda code, state: "",
    )


@pytest.fixture
def dlg():
    app = QApplication.instance() or QApplication([])
    window = SasVerifyDialog(
        _fake_vm(), QThreadPool.globalInstance(), scan_root=str(
        Path(__file__).resolve().parent
        / "fixtures"
        / "magicwheel_cabinet"
        / "Goldclub"
        / "var"
        / "log"
    )
    )
    window._prefetch_started = True
    window._auto_fetch_toggle.setChecked(False)
    try:
        yield window
    finally:
        window.deleteLater()
        app.processEvents()


def test_every_named_tab_is_present_and_selectable(dlg) -> None:
    assert dlg._meter_tabs.count() == len(_METER_TAB_NAMES)
    for index, name in enumerate(_METER_TAB_NAMES):
        assert dlg._meter_tabs.tabText(index) == name
        dlg._goto_meter_tab(index)
        assert dlg._meter_tabs.currentIndex() == index
        assert dlg._meter_tabs.currentWidget() is not None


def test_stepping_forward_visits_each_tab_and_wraps(dlg) -> None:
    dlg._goto_meter_tab(TAB_ACCOUNTING)
    seen = [dlg._meter_tabs.currentIndex()]
    for _ in range(len(_METER_TAB_NAMES) - 1):
        dlg._step_meter_tab(1)
        seen.append(dlg._meter_tabs.currentIndex())
    assert seen == list(range(len(_METER_TAB_NAMES)))
    # One more press comes back round to the first tab.
    dlg._step_meter_tab(1)
    assert dlg._meter_tabs.currentIndex() == TAB_ACCOUNTING


def test_stepping_backward_wraps_to_the_last_tab(dlg) -> None:
    dlg._goto_meter_tab(TAB_ACCOUNTING)
    dlg._step_meter_tab(-1)
    assert dlg._meter_tabs.currentIndex() == TAB_MAGICWHEEL
    dlg._step_meter_tab(-1)
    assert dlg._meter_tabs.currentIndex() == TAB_MAGICWHEEL - 1


def test_goto_ignores_an_index_that_does_not_exist(dlg) -> None:
    dlg._goto_meter_tab(TAB_BILLS)
    for bad in (-1, len(_METER_TAB_NAMES), 999):
        dlg._goto_meter_tab(bad)
        assert dlg._meter_tabs.currentIndex() == TAB_BILLS


def test_global_tab_hotkey_advances_one_tab(dlg) -> None:
    dlg._goto_meter_tab(TAB_ACCOUNTING)
    dlg._on_tab_hotkey()
    assert dlg._meter_tabs.currentIndex() == TAB_ACCOUNTING + 1


def test_tab_hotkey_combo_is_ctrl_alt_shift_t() -> None:
    assert TAB_HOTKEY_MODS & 0x1  # ALT
    assert TAB_HOTKEY_MODS & 0x2  # CONTROL
    assert TAB_HOTKEY_MODS & 0x4  # SHIFT
    assert TAB_HOTKEY_MODS & 0x4000  # NOREPEAT
    assert TAB_HOTKEY_VK == 0x54  # 'T'
    assert TAB_HOTKEY_LABEL == "Ctrl+Alt+Shift+T"


def _shortcut_sequences(widget) -> set[str]:
    return {
        sc.key().toString()
        for sc in widget.findChildren(QShortcut)
        if not sc.key().isEmpty()
    }


def test_the_documented_tab_shortcuts_are_registered(dlg) -> None:
    keys = _shortcut_sequences(dlg)
    for seq in ("Ctrl+Tab", "Ctrl+Shift+Tab", "Ctrl+PgDown", "Ctrl+PgUp"):
        assert QKeySequence(seq).toString() in keys, f"{seq} missing"
    for index in range(len(_METER_TAB_NAMES)):
        seq = QKeySequence(f"Ctrl+{index + 1}").toString()
        assert seq in keys, f"Ctrl+{index + 1} missing"


def test_tab_shortcuts_work_from_anywhere_in_the_window(dlg) -> None:
    """Focus usually sits in a table or the paste box, not on the tab bar."""
    for sc in dlg.findChildren(QShortcut):
        if sc.key().toString() in {
            QKeySequence("Ctrl+Tab").toString(),
            QKeySequence("Ctrl+1").toString(),
        }:
            assert sc.context() == Qt.ShortcutContext.ApplicationShortcut


def test_help_documents_the_tab_shortcuts() -> None:
    from gui.sas_verify_dialog import SAS_VERIFY_HELP_HTML

    assert "Ctrl+Alt+Shift+T" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+Tab" in SAS_VERIFY_HELP_HTML
    assert "Ctrl+1" in SAS_VERIFY_HELP_HTML
