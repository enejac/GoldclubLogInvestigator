from pathlib import Path

from network.goldclub_paths import (
    GoldclubLayoutKind,
    discover_portable_scan_roots,
    resolve_goldclub_layout,
)


def test_resolve_local_cabinet_log_root(tmp_path: Path) -> None:
    log_root = tmp_path / "Goldclub" / "var" / "log"
    gcm = tmp_path / "Goldclub" / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger"
    (log_root / "SlotLog").mkdir(parents=True)
    (gcm / "gm2au").mkdir(parents=True)
    layout = resolve_goldclub_layout(str(log_root))
    assert layout is not None
    assert layout.kind == GoldclubLayoutKind.LOCAL_CABINET
    assert layout.state_gcmessenger == gcm


def test_resolve_usb_export_with_embedded_state(tmp_path: Path) -> None:
    export = tmp_path / "_LogFiles" / "log_08_07_2026"
    (export / "SlotLog").mkdir(parents=True)
    gcm = export / "state" / "GoldClub.Aurum.Services" / "GCMessenger" / "SASControler1"
    gcm.mkdir(parents=True)
    (export / "mgconfig.xml").write_text("<Multigamer></Multigamer>", encoding="utf-8")
    layout = resolve_goldclub_layout(str(export))
    assert layout is not None
    assert layout.kind == GoldclubLayoutKind.USB_EXPORT
    assert layout.state_gcmessenger == export / "state" / "GoldClub.Aurum.Services" / "GCMessenger"
    assert layout.themes_root == export


def test_discover_portable_scan_roots_finds_usb_export(tmp_path: Path) -> None:
    exe_dir = tmp_path / "LogInvestigator"
    exe_dir.mkdir()
    export = tmp_path / "_LogFiles" / "log_01_01_2026"
    (export / "SlotLog").mkdir(parents=True)
    roots = discover_portable_scan_roots(exe_dir=exe_dir)
    assert roots[0] == str(export)
