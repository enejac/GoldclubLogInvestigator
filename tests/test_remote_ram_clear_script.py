"""remote_ram_clear.ps1 must drive the newest START_ONEHAND soft RAM-clear."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "lab" / "remote_ram_clear.ps1"
START = ROOT / "cabinet_tools" / "slot" / "START_ONEHAND.ps1"


def test_remote_ram_clear_uploads_newest_start_onehand() -> None:
    text = REMOTE.read_text(encoding="utf-8")
    assert "START_ONEHAND.ps1" in text
    assert "Wait-CabinetDeviceOnline" in text
    assert "LogDaemonRamClear" in text
    assert "Stop-LogDaemonUnclean" in text
    assert "Start-LabInteractiveRemote" in text
    assert "-UseUsbBat" in text
    assert r"C:\Windows\Temp\START_ONEHAND.ps1" in text
    raw = REMOTE.read_bytes()[:2]
    assert raw != b"\xff\xfe" and raw != b"\xfe\xff"


def test_start_onehand_sidecar_markers_match_remote_gate() -> None:
    text = START.read_text(encoding="utf-8")
    for needle in ("Wait-CabinetDeviceOnline", "LogDaemonRamClear", "Stop-LogDaemonUnclean"):
        assert needle in text