"""USB RAM-CLEAR helper: official ramclear.d, licences kept, no serialport edits."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Ram-Clear.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "RAM-CLEAR.cmd"
USB_CMD = ROOT / "cabinet_tools" / "usb_root" / "RAM-CLEAR.cmd"


def test_ram_clear_ps1_skips_wibu_and_protects_licences() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "GoldClubElevate.ps1" in text
    assert "Invoke-GoldClubSelfElevate" in text
    assert "Start-Process -Verb" not in text
    assert "ramclear.d" in text
    assert "01-StopServices.ps1" in text
    assert "bounded pre-stop already done" in text
    assert "preserve licences / Wibu keys" in text
    assert "[switch] $ClearWibu" in text
    assert "51-LogDaemon" in text
    assert "IdleMode.flag" in text
    assert "official targets only" in text
    assert "Never treat D:\\" in text
    assert "serialport" in text.lower()
    assert "licence.dll" in text.lower()
    assert "Run-FullStack" in text
    assert "[switch] $SkipRestart" in text
    assert "[switch] $WhatIf" in text
    assert "LogDaemonRamClear" in text
    assert "var\\state\\GoldClub.Aurum.Services" in text
    assert "ruleta\\var" in text
    assert "online_sas" in text
    assert "Kill-All.ps1" in text
    # Must not blank-wipe the whole state tree or live licence store.
    assert "Remove-Item -LiteralPath (Join-Path $gc 'var\\state')" not in text
    assert "config\\licences" in text.lower() or "licen" in text.lower()


def test_ram_clear_cmd_calls_helper() -> None:
    text = CMD.read_text(encoding="utf-8")
    assert "Ram-Clear.ps1" in text
    assert "Verb RunAs" not in text
    assert "usb_scripts\\roulette\\Ram-Clear.ps1" in text
    assert "Does NOT overwrite config\\licences" in text
    assert "Does NOT edit serialport" in text
    assert "Does NOT run ClearWibu" in text


def test_ram_clear_usb_root_shortcut() -> None:
    text = USB_CMD.read_text(encoding="utf-8")
    assert "usb_scripts\\roulette\\RAM-CLEAR.cmd" in text
    assert "Verb RunAs" not in text
