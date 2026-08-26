"""Tests for GUI file logging beside the executable."""

from __future__ import annotations

from gui.app_logging import app_log_dir, log_file_path


def test_log_file_path_is_under_app_log_dir() -> None:
    assert log_file_path().parent == app_log_dir()
    assert log_file_path().name == "LogInvestigator.log"
