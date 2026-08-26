"""Tests for Config Scanner HTML report browser launch."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_scanner import html_open as html_open


def _touch_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def test_resolve_prefers_portable_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ConfigScanner"
    portable = _touch_exe(root / "browser" / "chrome-win64" / "chrome.exe")
    installed = _touch_exe(tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe")

    monkeypatch.setenv("LOGINV_CONFIG_SCANNER_ROOT", str(root))
    monkeypatch.delenv("LOGINV_HTML_BROWSER", raising=False)
    monkeypatch.setattr(html_open, "installed_browser_candidates", lambda: [installed])

    assert html_open.resolve_html_browser() == portable


def test_resolve_honors_browser_env_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = _touch_exe(tmp_path / "custom" / "chrome.exe")
    monkeypatch.setenv("LOGINV_HTML_BROWSER", str(override))
    monkeypatch.setenv("LOGINV_CONFIG_SCANNER_ROOT", str(tmp_path / "empty"))
    monkeypatch.setattr(html_open, "installed_browser_candidates", lambda: [])
    monkeypatch.setattr(html_open, "portable_browser_roots", lambda: [])

    assert html_open.resolve_html_browser() == override


def test_resolve_falls_back_to_installed_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edge = _touch_exe(tmp_path / "msedge.exe")
    monkeypatch.delenv("LOGINV_HTML_BROWSER", raising=False)
    monkeypatch.setenv("LOGINV_CONFIG_SCANNER_ROOT", str(tmp_path / "empty"))
    monkeypatch.setattr(html_open, "portable_browser_roots", lambda: [])
    monkeypatch.setattr(html_open, "installed_browser_candidates", lambda: [edge])

    assert html_open.resolve_html_browser() == edge


def test_portable_launch_args_include_user_data(tmp_path: Path) -> None:
    exe = tmp_path / "browser" / "chrome-win64" / "chrome.exe"
    html = tmp_path / "report.html"
    args = html_open.browser_launch_args(exe, html)
    assert args[0] == str(exe)
    assert "--user-data-dir=" + str(tmp_path / "browser" / "user-data") in args
    assert "--no-first-run" in args
    assert args[-1] == str(html)


def test_installed_launch_args_do_not_override_profile(tmp_path: Path) -> None:
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    html = tmp_path / "report.html"
    args = html_open.browser_launch_args(exe, html)
    assert args == [str(exe), str(html)]


def test_open_html_file_launches_portable_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(html_open, "_is_windows", lambda: True)
    root = tmp_path / "ConfigScanner"
    exe = _touch_exe(root / "browser" / "chrome-win64" / "chrome.exe")
    report = tmp_path / "diff.html"
    report.write_text("<html></html>", encoding="utf-8")

    monkeypatch.setenv("LOGINV_CONFIG_SCANNER_ROOT", str(root))
    monkeypatch.delenv("LOGINV_HTML_BROWSER", raising=False)
    monkeypatch.setattr(html_open, "installed_browser_candidates", lambda: [])

    calls: list[list[str]] = []
    monkeypatch.setattr(
        html_open.subprocess, "Popen", lambda args, **_kw: calls.append(list(args))
    )

    ok, message = html_open.open_html_file(report)
    assert ok is True
    assert "chrome.exe" in message
    assert calls and calls[0][0] == str(exe)
    assert str(report.resolve()) == calls[0][-1]


def test_open_html_file_falls_back_to_startfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(html_open, "_is_windows", lambda: True)
    monkeypatch.setattr(html_open, "iter_html_browsers", lambda: [])
    report = tmp_path / "diff.html"
    report.write_text("<html></html>", encoding="utf-8")

    started: list[Path] = []
    monkeypatch.setattr(html_open, "_start_file", lambda path: started.append(path))

    ok, message = html_open.open_html_file(report)
    assert ok is True
    assert "default app" in message
    assert started == [report.resolve()]


def test_open_html_file_missing_report(tmp_path: Path) -> None:
    ok, message = html_open.open_html_file(tmp_path / "missing.html")
    assert ok is False
    assert "not found" in message.lower()
