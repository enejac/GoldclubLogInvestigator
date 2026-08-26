"""Clear-TrialPersistent stays on C: and only drops trial tokens."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "Clear-TrialPersistent.ps1"


def test_clear_trial_persistent_scope() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "C:\\goldclub\\ruleta" in text
    assert "RouletteActivate.dat" in text
    assert "RouletteStop.flag" in text
    assert "HeapDataFinanceStamps.dat" in text
    assert "Password.dat" in text
    assert "will not write it" in text
    assert "goldclub.vhd" in text
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in text
    assert "Start-Process -Verb" not in text
    assert "licence.dll" not in text.split("$names = @(", 1)[1].split(")", 1)[0]
    assert "HeapDataTitoPowerUp.dat" not in text
    assert "HeapDataWatTransactions.dat" not in text
    assert "serialport" not in text.lower()
