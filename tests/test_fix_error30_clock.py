"""USB ERROR 30 clock rollback stays on C: and keeps the live licence."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Fix-Error30Clock.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "FIX-ERROR30-CLOCK.cmd"
USB = ROOT / "cabinet_tools" / "usb_root" / "FIX-ERROR30-CLOCK.cmd"


def test_clock_fix_is_cabinet_only() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "C:\\goldclub\\ruleta" in text
    assert "2026-08-10" in text
    assert "w32time" in text
    assert "Set-Date" in text
    assert "HeapDataDateTime.dat" in text
    assert "Password.dat" in text
    assert "HeapDataDynamicPaytable.dat" in text
    assert "RouletteActivate.dat" in text
    assert "RouletteStop.flag" in text
    assert "HeapDataFinanceStamps.dat" in text
    assert "persistent" in text
    assert "GoldClubElevate.ps1" in text
    assert "Start-Process -Verb" not in text
    assert "$parseLocal = $TargetLocal + ' 12:00:00'" in text
    assert "$argList += '-TargetLocal'" in text
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in text
    assert "will not write it" in text
    assert "goldclub.vhd" in text
    assert "Kill-All" in text
    assert "Run-FullStack" in text
    files_block = text.split("foreach ($name in", 1)[1].split(")", 1)[0]
    assert "licence.dll" not in files_block


def test_clock_fix_cmd_and_usb_root() -> None:
    cmd = CMD.read_text(encoding="utf-8")
    assert "Fix-Error30Clock.ps1" in cmd
    assert "Does NOT write G:\\" in cmd
    assert "Does NOT overwrite config\\licences" in cmd
    usb = USB.read_text(encoding="utf-8")
    assert "usb_scripts\\roulette\\FIX-ERROR30-CLOCK.cmd" in usb
