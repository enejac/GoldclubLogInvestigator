"""Slot / USB onlogon helpers must not leave a 'Press any key' console open."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARE_BAT = ROOT / "cabinet_tools" / "shared" / "_share.bat"
SLOT_ONLOGON = ROOT / "cabinet_tools" / "slot" / "slot1onlogon.ps1"
ONLOGON = ROOT / "cabinet_tools" / "shared" / "onlogon.ps1"
RESTORE_TV = ROOT / "cabinet_tools" / "restore_tv_login.cmd"
EXTRAS = ROOT / "cabinet_tools" / "shared" / "Invoke-UsbOnlogonExtras.ps1"


def test_share_bat_defaults_to_no_pause() -> None:
    text = SHARE_BAT.read_text(encoding="utf-8")
    assert 'PAUSE_MODE=auto' in text
    assert 'if /i "%~1"=="-pause" set "PAUSE_MODE=always"' in text
    assert 'if /i "%~1"=="-nopause" set "PAUSE_MODE=never"' in text
    assert 'SHARE_OK' in text
    assert 'USB_Remote' in text


def test_slot1onlogon_passes_nopause_to_share() -> None:
    text = SLOT_ONLOGON.read_text(encoding="utf-8")
    assert "_share.bat" in text
    assert "-nopause" in text


def test_onlogon_passes_nopause_to_share() -> None:
    text = ONLOGON.read_text(encoding="utf-8")
    assert "-nopause" in text


def test_usb_extras_passes_nopause_to_share() -> None:
    text = EXTRAS.read_text(encoding="utf-8")
    assert "ExtraArgs" in text
    assert "-nopause" in text


def test_restore_tv_login_has_no_pause() -> None:
    text = RESTORE_TV.read_text(encoding="utf-8")
    code_lines = [
        ln
        for ln in text.splitlines()
        if ln.strip()
        and not ln.lstrip().lower().startswith("::")
        and not ln.lstrip().lower().startswith("rem")
    ]
    joined = "\n".join(code_lines).lower()
    assert "pause" not in joined
    assert "exit /b 0" in text.lower()
