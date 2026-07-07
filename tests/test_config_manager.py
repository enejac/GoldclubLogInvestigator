"""SettingsManager QSettings wrapper (isolated keys)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from config_manager import (
    THEME_DARK,
    THEME_SYSTEM,
    SettingsManager,
    _KEY_ARCHIVE,
    _KEY_DELETE,
    _KEY_DRIFT,
    _KEY_MAINWIN_GEOMETRY,
    _KEY_MAINWIN_H,
    _KEY_MAINWIN_W,
    _KEY_MAINWIN_WINDOW_STATE,
    _KEY_MAINWIN_X,
    _KEY_MAINWIN_Y,
    _KEY_SAS_VERIFY_GEOMETRY,
    _KEY_SAS_VERIFY_H,
    _KEY_SAS_VERIFY_W,
    _KEY_SAS_VERIFY_X,
    _KEY_SAS_VERIFY_Y,
    _KEY_THEME,
)


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a separate QSettings org/app so tests do not touch user prefs."""

    def fake_s() -> QSettings:
        return QSettings(QSettings.Scope.UserScope, "GoldclubTest", "LogInvestigatorCM")

    monkeypatch.setattr("config_manager.SettingsManager._s", staticmethod(fake_s))
    s = fake_s()
    s.remove(_KEY_ARCHIVE)
    s.remove(_KEY_DELETE)
    s.remove(_KEY_DRIFT)
    s.remove(_KEY_THEME)
    s.remove(_KEY_MAINWIN_GEOMETRY)
    s.remove(_KEY_MAINWIN_WINDOW_STATE)
    s.remove(_KEY_MAINWIN_X)
    s.remove(_KEY_MAINWIN_Y)
    s.remove(_KEY_MAINWIN_W)
    s.remove(_KEY_MAINWIN_H)
    s.remove(_KEY_SAS_VERIFY_GEOMETRY)
    s.remove(_KEY_SAS_VERIFY_X)
    s.remove(_KEY_SAS_VERIFY_Y)
    s.remove(_KEY_SAS_VERIFY_W)
    s.remove(_KEY_SAS_VERIFY_H)
    s.sync()
    yield
    s.remove(_KEY_ARCHIVE)
    s.remove(_KEY_DELETE)
    s.remove(_KEY_DRIFT)
    s.remove(_KEY_THEME)
    s.remove(_KEY_MAINWIN_GEOMETRY)
    s.remove(_KEY_MAINWIN_WINDOW_STATE)
    s.remove(_KEY_MAINWIN_X)
    s.remove(_KEY_MAINWIN_Y)
    s.remove(_KEY_MAINWIN_W)
    s.remove(_KEY_MAINWIN_H)
    s.remove(_KEY_SAS_VERIFY_GEOMETRY)
    s.remove(_KEY_SAS_VERIFY_X)
    s.remove(_KEY_SAS_VERIFY_Y)
    s.remove(_KEY_SAS_VERIFY_W)
    s.remove(_KEY_SAS_VERIFY_H)
    s.sync()


def test_settings_manager_defaults(isolated_settings: None) -> None:
    assert SettingsManager.get_log_archive_days() == 7
    assert SettingsManager.get_log_delete_days() == 30
    assert SettingsManager.get_clock_drift_threshold() == 60


def test_settings_manager_roundtrip(isolated_settings: None) -> None:
    SettingsManager.set_log_archive_days(14)
    SettingsManager.set_log_delete_days(90)
    SettingsManager.set_clock_drift_threshold(120)
    assert SettingsManager.get_log_archive_days() == 14
    assert SettingsManager.get_log_delete_days() == 90
    assert SettingsManager.get_clock_drift_threshold() == 120


def test_settings_manager_clamps(isolated_settings: None) -> None:
    SettingsManager.set_log_archive_days(0)
    SettingsManager.set_log_delete_days(999)
    SettingsManager.set_clock_drift_threshold(1)
    assert SettingsManager.get_log_archive_days() == 1
    assert SettingsManager.get_log_delete_days() == 365
    assert SettingsManager.get_clock_drift_threshold() == 10


def test_settings_manager_theme_default(isolated_settings: None) -> None:
    assert SettingsManager.get_theme() == THEME_SYSTEM


def test_settings_manager_theme_roundtrip(isolated_settings: None) -> None:
    SettingsManager.set_theme(THEME_DARK)
    assert SettingsManager.get_theme() == THEME_DARK


def test_settings_manager_theme_invalid_reverts_on_set(isolated_settings: None) -> None:
    SettingsManager.set_theme("NotATheme")
    assert SettingsManager.get_theme() == THEME_SYSTEM


def test_main_window_geometry_roundtrip(isolated_settings: None) -> None:
    import sys

    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    win = QMainWindow()
    win.setGeometry(120, 80, 960, 640)
    SettingsManager.save_main_window_geometry(win)
    win.setGeometry(0, 0, 800, 600)
    assert SettingsManager.restore_main_window_geometry(win)
    geo = win.geometry()
    assert geo.x() == 120
    assert geo.y() == 80
    assert geo.width() == 960
    assert geo.height() == 640
    app.processEvents()


def test_sas_verify_dialog_geometry_roundtrip(isolated_settings: None) -> None:
    import sys

    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = QDialog()
    dlg.setGeometry(220, 140, 1180, 780)
    SettingsManager.save_sas_verify_dialog_geometry(dlg)
    dlg.setGeometry(0, 0, 800, 600)
    assert SettingsManager.restore_sas_verify_dialog_geometry(dlg)
    geo = dlg.geometry()
    assert geo.x() == 220
    assert geo.y() == 140
    assert geo.width() == 1180
    assert geo.height() == 780
    app.processEvents()
