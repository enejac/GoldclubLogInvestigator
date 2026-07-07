"""Remote screen capture (mocked PsExec / UNC)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from network import screen_capture


def test_capture_remote_screen_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screen_capture.os, "name", "posix")
    ok, msg = screen_capture.capture_remote_screen("10.0.0.1", "/tmp/x.jpg")
    assert ok is False
    assert "Windows" in msg


def test_capture_remote_screen_missing_psexec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(screen_capture.os, "name", "nt")
    monkeypatch.setattr(screen_capture, "_resolve_psexec_path", lambda: None)
    out = tmp_path / "o.jpg"
    ok, msg = screen_capture.capture_remote_screen("10.0.0.1", str(out))
    assert ok is False
    assert "PsExec" in msg


def test_capture_remote_screen_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(screen_capture.os, "name", "nt")
    fake_psexec = str(tmp_path / "psexec.exe")
    Path(fake_psexec).write_text("x")
    monkeypatch.setattr(screen_capture, "_resolve_psexec_path", lambda: fake_psexec)

    unc = tmp_path / "unc"
    unc.mkdir()
    remote_file = unc / "temp_screenshot.jpg"
    remote_file.write_bytes(b"fakejpg")

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        m = MagicMock()
        if "powershell" in " ".join(cmd).lower():
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        m.returncode = 0
        return m

    def fake_unc(ip: str) -> str:
        return str(remote_file)

    monkeypatch.setattr(screen_capture, "_remote_unc_path", fake_unc)

    with patch.object(screen_capture.subprocess, "run", side_effect=fake_run):
        local = tmp_path / "local.jpg"
        ok, msg = screen_capture.capture_remote_screen("10.0.0.1", str(local))
    assert ok is True
    assert Path(msg) == local.resolve()
    assert local.read_bytes() == b"fakejpg"
