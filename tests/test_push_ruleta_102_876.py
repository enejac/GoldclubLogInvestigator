"""USB push of Ruleta 10.2.0.876 keeps live licence files."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Push-Ruleta102876.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "PUSH-RULETA-102-876.cmd"
USB = ROOT / "cabinet_tools" / "usb_root" / "PUSH-RULETA-102-876.cmd"


def test_push_876_keeps_licence_and_clears_heap() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "Ruleta_v10.2.0.876_build40119" in text
    assert "GoldClubElevate.ps1" in text
    assert "Start-Process -Verb" not in text
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in text
    assert "licence.dll" in text
    assert "HeapDataDateTime.dat" in text
    assert "Password.dat" in text
    assert "RouletteActivate.dat" in text
    assert "HeapDataFinanceStamps.dat" in text
    assert "Kill-All" in text
    assert "Run-FullStack" in text
    assert "Copy-Item" in text
    assert "BuildVersion.txt" in text
    assert "AxProtector" in text


def test_push_876_does_not_copy_licence_dll() -> None:
    text = PS1.read_text(encoding="utf-8")
    copy_block = text.split("foreach ($rel in $files)", 1)[1].split("foreach ($name in", 1)[0]
    assert "licence.dll" not in copy_block
    files_block = text.split("$files = @(", 1)[1].split(")", 1)[0]
    assert "licence.dll" not in files_block
    assert "config\\licences" not in files_block


def test_push_876_cmd_calls_helper() -> None:
    text = CMD.read_text(encoding="utf-8")
    assert "Push-Ruleta102876.ps1" in text
    assert "Does NOT overwrite config\\licences" in text
    assert "Verb RunAs" not in text


def test_push_876_usb_root_shortcut() -> None:
    text = USB.read_text(encoding="utf-8")
    assert "usb_scripts\\roulette\\PUSH-RULETA-102-876.cmd" in text
