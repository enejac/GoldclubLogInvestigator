"""Tests for app-local install / _tmp_logs paths."""

from __future__ import annotations

from pathlib import Path

from app_paths import (
    app_install_dir,
    app_local_data_dir,
    app_runs_dir,
    app_writable_dir,
    bug_detector_output_base,
    is_network_path,
)
from network.bug_detector import default_output_dir
from network.bug_session_engine import default_session_dir


def test_bug_detector_paths_under_install_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app_paths.app_install_dir", lambda: tmp_path)
    base = bug_detector_output_base()
    assert base == tmp_path / "_tmp_logs" / "bug-detector"
    assert base.is_dir()
    session = default_session_dir("10.0.0.90")
    assert session.parent == base
    assert session.name.startswith("session_10.0.0.90_")
    past = default_output_dir("10.0.0.90")
    assert past.parent == base


def test_app_install_dir_is_repo_in_dev() -> None:
    root = app_install_dir()
    assert (root / "app_paths.py").is_file()


def test_run_packs_land_beside_the_exe_not_in_the_working_directory(
    tmp_path, monkeypatch
) -> None:
    # Started from a shortcut, the working directory can be C:\Windows\System32,
    # which is where a relative "automation_runs" would put an operator's evidence.
    monkeypatch.setattr("app_paths.app_install_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path / "..")
    runs = app_runs_dir()
    assert runs == tmp_path / "automation_runs"
    assert runs.is_dir()


def test_is_network_path_detects_unc() -> None:
    assert is_network_path(r"\\10.0.0.90\USB_Remote\ConfigScanner")
    assert is_network_path("//10.0.0.90/USB_Remote")
    assert is_network_path(r"\\?\UNC\10.0.0.90\USB_Remote")
    assert not is_network_path(r"C:\Goldclub")
    assert not is_network_path(r"\\?\C:\Goldclub")
    assert not is_network_path("")


def test_writable_dir_redirects_unc_install_to_local_appdata(
    tmp_path, monkeypatch
) -> None:
    unc = Path(r"\\10.0.0.90\USB_Remote\ConfigScanner")
    monkeypatch.setattr("app_paths.app_install_dir", lambda: unc)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    writable = app_writable_dir()
    assert writable == tmp_path / "Local" / "LogInvestigator"
    assert writable.is_dir()
    assert not is_network_path(writable)
    runs = app_runs_dir()
    assert runs.parent == writable
    local = app_local_data_dir()
    assert local == writable