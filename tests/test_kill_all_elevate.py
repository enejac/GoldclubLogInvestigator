"""Kill-All / Run-FullStack must elevate without UAC RunAs when seclogon is off."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROULETTE = ROOT / "cabinet_tools" / "roulette"


def test_goldclub_elevate_uses_system_task() -> None:
    text = (ROULETTE / "GoldClubElevate.ps1").read_text(encoding="utf-8")
    assert "function Invoke-GoldClubSelfElevate" in text
    assert "function Test-GoldClubRunAsAvailable" in text
    assert "/RU SYSTEM" in text
    assert "GoldClub-ElevateOnce" in text
    assert "seclogon" in text.lower() or "Secondary Logon" in text
    assert "Skipping UAC RunAs" in text
    assert "GoldClub Admin Shell" in text
    assert 'schtasks /Run /TN `"$taskName`"' in text or "schtasks /Run" in text


def test_kill_all_bat_does_not_runas() -> None:
    bat = (ROULETTE / "Kill-All.bat").read_text(encoding="utf-8")
    assert "Verb RunAs" not in bat
    assert "Kill-All.ps1" in bat
    assert "-AlreadyElevated" not in bat


def test_kill_all_ps1_uses_elevate_helper() -> None:
    text = (ROULETTE / "Kill-All.ps1").read_text(encoding="utf-8")
    assert "GoldClubElevate.ps1" in text
    assert "Invoke-GoldClubSelfElevate" in text


def test_run_fullstack_bat_does_not_runas() -> None:
    bat = (ROULETTE / "Run-FullStack.bat").read_text(encoding="utf-8")
    assert "Verb RunAs" not in bat
    assert "Run-FullStack.ps1" in bat


def test_alias_bats_do_not_runas() -> None:
    for name in ("Kill-ActiveGame.bat", "Kill-GoldClubProcesses.bat"):
        bat = (ROULETTE / name).read_text(encoding="utf-8")
        assert "Verb RunAs" not in bat
        assert "Kill-All.ps1" in bat
