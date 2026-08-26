"""USB push of Ruleta 10.2.0.684 keeps live licence files."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Push-Ruleta102684.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "PUSH-RULETA-102-684.cmd"
USB = ROOT / "cabinet_tools" / "usb_root" / "PUSH-RULETA-102-684.cmd"


def test_push_684_keeps_licence_and_clears_heap() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "Ruleta_v10.2.0.684_build40097" in text
    assert "GoldClubElevate.ps1" in text
    assert "Start-Process -Verb" not in text
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in text
    assert "licence.dll" in text
    assert "HeapDataDateTime.dat" in text
    assert "Password.dat" in text
    assert "Kill-All" in text
    assert "Run-FullStack" in text
    assert "Copy-Item" in text
    assert "config\\licences" not in text.lower() or "licence xml exists" in text


def test_push_684_cmd_calls_helper() -> None:
    text = CMD.read_text(encoding="utf-8")
    assert "Push-Ruleta102684.ps1" in text
    assert "Does NOT overwrite config\\licences" in text
    assert "Verb RunAs" not in text


def test_push_684_usb_root_shortcut() -> None:
    text = USB.read_text(encoding="utf-8")
    assert "usb_scripts\\roulette\\PUSH-RULETA-102-684.cmd" in text
