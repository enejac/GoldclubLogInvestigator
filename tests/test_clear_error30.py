"""Clear-Error30 helper: no UAC RunAs, never writes licences."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Clear-Error30.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "CLEAR-ERROR30.cmd"


def test_clear_error30_skips_uac_runas() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "Start-Process -Verb" not in text
    assert "-Verb" not in text
    assert "will not be overwritten" in text
    assert "6051106A90E561F9F08CDEEBB5C3F6E2.xml" in text
    assert "licence.dll" in text.lower()
    assert "RouletteActivate.dat" in text
    assert "HeapDataFinanceStamps.dat" in text
    assert "Remove-TrialPersistent" in text
    assert "Ruleta.exe" in text
    assert "Stop-Process" in text
    assert "[switch] $Auto" in text
    assert "error30.password" in text
    assert "RULETA_ERROR30_PASSWORD" in text
    assert "Get-Error30State" in text
    assert "re-locked" in text
    assert "SendKeys" in text
    assert "No buttons enabled in stop dialog" in text
    assert "Newest event wins" in text
    assert '$disp -gt $lockAt' in text
    assert "Auto: leave persistent trial files" in text
    assert "if (-not $Auto)" in text


def test_clear_error30_cmd_calls_helper() -> None:
    text = CMD.read_text(encoding="utf-8")
    assert "Clear-Error30.ps1" in text
    assert "Verb RunAs" not in text
    assert "Kill-All.ps1" not in text
    assert "usb_scripts\\roulette\\Clear-Error30.ps1" in text
    assert "Does NOT swap in 10.1" in text
