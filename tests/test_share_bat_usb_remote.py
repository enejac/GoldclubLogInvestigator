"""_share.bat creates USB_Remote and only pauses on failure (never hard-fails)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "cabinet_tools" / "shared" / "_share.bat"


def test_share_bat_creates_usb_remote_and_slot() -> None:
    text = SHARE.read_text(encoding="utf-8")
    assert "USB_Remote" in text
    assert "net share slot=" in text or 'net share slot="' in text
    assert "SHARE_OK" in text
    assert "exit /b 0" in text


def test_share_bat_pauses_only_on_failure_by_default() -> None:
    text = SHARE.read_text(encoding="utf-8")
    assert 'PAUSE_MODE=auto' in text
    assert 'if /i "%~1"=="-pause" set "PAUSE_MODE=always"' in text
    assert 'if /i "%~1"=="-nopause" set "PAUSE_MODE=never"' in text
    assert 'if /i "%PAUSE_MODE%"=="auto" if "%SHARE_OK%"=="0" set "DO_PAUSE=1"' in text
    # Must not default to always-pause (old USB copy had PAUSE_AT_END=1).
    assert 'PAUSE_AT_END=1' not in text


def test_share_bat_utf8_not_utf16() -> None:
    raw = SHARE.read_bytes()[:2]
    assert raw != b"\xff\xfe" and raw != b"\xfe\xff"