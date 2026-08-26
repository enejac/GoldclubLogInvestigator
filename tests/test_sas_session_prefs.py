"""SAS Verify UI ticks persist across launches on the same machine."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from gui.sas_verify_dialog import (
    TAB_COINS,
    _KEY_METER_TAB,
    _KEY_SCAN_ROOT,
    _KEY_SHOW_DOLLARS,
    _METER_TAB_NAMES,
    SasVerifyDialog,
    load_persisted_scan_root,
    save_persisted_scan_root,
)


def _clear_session_prefs() -> None:
    from config_manager import SettingsManager

    s = SettingsManager._s()
    for key in (_KEY_SHOW_DOLLARS, _KEY_METER_TAB, _KEY_SCAN_ROOT):
        s.remove(f"sasVerify/{key}")
    s.sync()


def _dlg(scan_root: str = "") -> SasVerifyDialog:
    return SasVerifyDialog(
        SimpleNamespace(current_product_name="GUI-Test"),
        QThreadPool.globalInstance(),
        scan_root=scan_root,
    )


def test_show_dollars_and_tab_and_scan_root_persist() -> None:
    _clear_session_prefs()
    app = QApplication.instance() or QApplication([])

    dlg = _dlg(scan_root=r"\\10.0.0.90\c$\Goldclub\var\log")
    dlg._prefetch_started = True
    assert dlg._show_dollars is True
    dlg._dollar_toggle.setChecked(False)
    assert dlg._show_dollars is False
    dlg._meter_tabs.setCurrentIndex(TAB_COINS)
    dlg._save_session_prefs()
    dlg.deleteLater()
    app.processEvents()

    assert load_persisted_scan_root() == r"\\10.0.0.90\c$\Goldclub\var\log"

    dlg2 = _dlg(scan_root="")
    assert dlg2._show_dollars is False
    assert dlg2._dollar_toggle.isChecked() is False
    assert dlg2._meter_tabs.currentIndex() == TAB_COINS
    assert dlg2._meter_tabs.tabText(dlg2._meter_tabs.currentIndex()) == _METER_TAB_NAMES[TAB_COINS]
    dlg2.deleteLater()
    app.processEvents()
    _clear_session_prefs()


def test_persisted_scan_root_helpers_round_trip() -> None:
    _clear_session_prefs()
    save_persisted_scan_root(r"\\10.0.0.171\c$\Goldclub\var\log")
    assert load_persisted_scan_root() == r"\\10.0.0.171\c$\Goldclub\var\log"
    save_persisted_scan_root("")
    assert load_persisted_scan_root() == ""
    _clear_session_prefs()