"""Tests for Notepad++ path resolution and text-file detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from gui import notepad_pp as npp


def test_is_text_file_path_recognizes_common_suffixes() -> None:
    assert npp.is_text_file_path("cabinet.log")
    assert npp.is_text_file_path(Path("report.TXT"))
    assert npp.is_text_file_path("config.xml")
    assert npp.is_text_file_path("results.jsonl")
    assert npp.is_text_file_path("notes")
    assert not npp.is_text_file_path("image.png")
    assert not npp.is_text_file_path(None)
    assert not npp.is_text_file_path("")


def test_resolve_prefers_native_when_not_on_usb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_dir = tmp_path / "native" / "Notepad++"
    native_dir.mkdir(parents=True)
    native_exe = native_dir / "notepad++.exe"
    native_exe.write_text("", encoding="utf-8")

    portable_dir = tmp_path / "portable"
    portable_dir.mkdir()
    portable_exe = portable_dir / "notepad++.exe"
    portable_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(npp, "NPP_PORTABLE_ROOT", portable_dir)
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: False)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [native_dir])
    monkeypatch.setattr(npp, "_find_portable_npp_exe", lambda: portable_exe)

    assert npp.resolve_notepad_pp_exe() == native_exe


def test_resolve_prefers_portable_when_on_usb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_dir = tmp_path / "native" / "Notepad++"
    native_dir.mkdir(parents=True)
    native_exe = native_dir / "notepad++.exe"
    native_exe.write_text("", encoding="utf-8")

    portable_dir = tmp_path / "portable"
    portable_dir.mkdir()
    portable_exe = portable_dir / "notepad++.exe"
    portable_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(npp, "NPP_PORTABLE_ROOT", portable_dir)
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: True)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [native_dir])
    monkeypatch.setattr(npp, "_find_portable_npp_exe", lambda: portable_exe)

    assert npp.resolve_notepad_pp_exe() == portable_exe


def test_resolve_falls_back_to_portable_when_native_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portable_dir = tmp_path / "portable"
    portable_dir.mkdir()
    portable_exe = portable_dir / "notepad++.exe"
    portable_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(npp, "NPP_PORTABLE_ROOT", portable_dir)
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: False)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [])
    monkeypatch.setattr(npp, "_find_portable_npp_exe", lambda: portable_exe)

    assert npp.resolve_notepad_pp_exe() == portable_exe


def test_resolve_returns_none_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: False)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [])
    monkeypatch.setattr(npp, "_find_portable_npp_exe", lambda: None)

    assert npp.resolve_notepad_pp_exe() is None


def test_portable_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom_npp"
    custom.mkdir()
    exe = custom / "notepad++.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.setenv(npp.NPP_PORTABLE_ROOT_ENV, str(custom))
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: True)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [])

    assert npp.resolve_notepad_pp_exe() == exe


def test_is_windows_true_on_nt_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(npp.os, "name", "nt")
    monkeypatch.setattr(npp.sys, "platform", "win32")
    assert npp._is_windows() is True


def test_resolve_finds_d_drive_portable_on_egm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    portable_dir = tmp_path / "D_npp"
    portable_dir.mkdir()
    portable_exe = portable_dir / "notepad++.exe"
    portable_exe.write_text("", encoding="utf-8")

    def fake_roots() -> list[Path]:
        return [tmp_path / "missing_h", portable_dir]

    monkeypatch.setattr(npp, "_portable_search_roots", fake_roots)
    monkeypatch.setattr(npp, "_running_from_removable_drive", lambda: False)
    monkeypatch.setattr(npp, "_native_install_dirs", lambda: [])

    assert npp.resolve_notepad_pp_exe() == portable_exe


def test_open_with_notepad_pp_not_blocked_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(npp.os, "name", "nt")
    monkeypatch.setattr(npp.sys, "platform", "win32")
    monkeypatch.setattr(npp, "resolve_notepad_pp_exe", lambda: None)

    target = tmp_path / "report.html"
    target.write_text("<html></html>", encoding="utf-8")
    ok, msg = npp.open_with_notepad_pp(target)
    assert ok is False
    assert "only supported on Windows" not in msg


def test_open_with_notepad_or_npp_uses_npp_when_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(npp.os, "name", "nt")
    monkeypatch.setattr(npp.sys, "platform", "win32")
    npp_exe = tmp_path / "notepad++.exe"
    npp_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(npp, "resolve_notepad_pp_exe", lambda: npp_exe)

    calls: list[list[str]] = []
    monkeypatch.setattr(
        npp.subprocess, "Popen", lambda args, **kw: calls.append(args)
    )

    target = tmp_path / "report.log"
    target.write_text("hello", encoding="utf-8")
    ok, msg = npp.open_with_notepad_or_npp(target)

    assert ok is True
    assert "Notepad++" in msg
    assert calls and calls[0][0] == str(npp_exe)


def test_open_with_notepad_or_npp_falls_back_to_notepad(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(npp.os, "name", "nt")
    monkeypatch.setattr(npp.sys, "platform", "win32")
    monkeypatch.setattr(npp, "resolve_notepad_pp_exe", lambda: None)

    calls: list[list[str]] = []
    monkeypatch.setattr(
        npp.subprocess, "Popen", lambda args, **kw: calls.append(args)
    )

    target = tmp_path / "report.log"
    target.write_text("hello", encoding="utf-8")
    ok, msg = npp.open_with_notepad_or_npp(target)

    assert ok is True
    assert "Notepad" in msg and "Notepad++" not in msg
    assert calls and calls[0] == ["notepad.exe", str(target)]


def test_open_with_notepad_or_npp_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(npp.os, "name", "nt")
    monkeypatch.setattr(npp.sys, "platform", "win32")

    ok, msg = npp.open_with_notepad_or_npp(tmp_path / "does_not_exist.log")

    assert ok is False
    assert "not found" in msg.lower()