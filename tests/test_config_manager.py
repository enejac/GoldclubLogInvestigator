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
    s.sync()
    yield
    s.remove(_KEY_ARCHIVE)
    s.remove(_KEY_DELETE)
    s.remove(_KEY_DRIFT)
    s.remove(_KEY_THEME)
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
