"""START_ONEHAND soft RAM-clear must late-stamp LogDaemonRamClear for SAS 0x7A."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOT = ROOT / "cabinet_tools" / "slot"
PS1 = SLOT / "START_ONEHAND.ps1"
BAT = SLOT / "START_ONEHAND.bat"


def test_start_onehand_ps1_runs_logdaemon_ramclear() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "LogDaemonRamClear.exe" in text
    assert r"services\logdaemon\var" in text
    assert r"services\aurum\var" in text
    assert r"var\state\OneHand" in text
    assert "Wait-CabinetDeviceOnline" in text
    assert "Stop-LogDaemonUnclean" in text
    assert "goldclub*" in text


def test_start_onehand_stamps_after_onehand_and_cabinet() -> None:
    text = PS1.read_text(encoding="utf-8")
    # Use section markers so function *definitions* do not confuse order.
    idx_svc = text.index("# --- 5) Services + Bootstrap bootloader")
    idx_boot = text.index("Starting bootloader ")
    idx_wait = text.index("Wait-CabinetDeviceOnline -Root $root")
    idx_stamp = text.index("Invoke-LogDaemonRamClearStamp -Root $root")
    assert idx_svc < idx_boot < idx_wait < idx_stamp


def test_start_onehand_kills_bootstrap_before_onehand() -> None:
    text = PS1.read_text(encoding="utf-8")
    kill_block = text.split("# --- 2)")[0]
    assert kill_block.index('"Bootstrap"') < kill_block.index('"OneHand"')
    assert "bootloader" in text.lower()
    assert "BiOS2" in text
    assert "Bootstrap.exe" in text


def test_start_onehand_bat_launches_ps1() -> None:
    bat = BAT.read_text(encoding="utf-8")
    assert "START_ONEHAND.ps1" in bat
    assert "ExecutionPolicy Bypass" in bat


def test_start_onehand_files_are_utf8_not_utf16() -> None:
    for path in (PS1, BAT):
        raw = path.read_bytes()[:4]
        assert not (raw[0] == 0xFF and raw[1] == 0xFE), f"{path} is UTF-16 LE"
        assert raw[1] != 0, f"{path} looks UTF-16"
