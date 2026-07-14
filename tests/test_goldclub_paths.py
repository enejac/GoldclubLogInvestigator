from pathlib import Path

from network.goldclub_paths import (
    GoldclubLayoutKind,
    discover_portable_scan_roots,
    discover_startup_scan_target,
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


def test_discover_startup_slot_install(tmp_path: Path, monkeypatch) -> None:
    slot_root = tmp_path / "Goldclub" / "slot"
    slot_root.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_text("", encoding="utf-8")
    log_root = tmp_path / "Goldclub" / "var" / "log"
    (log_root / "SlotLog").mkdir(parents=True)

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: slot_root
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )

    discovery = discover_startup_scan_target()
    assert discovery.mode == "local"
    assert discovery.game_kind == "slot"
    assert Path(discovery.scan_root) == log_root


def test_discover_startup_roulette_install(tmp_path: Path, monkeypatch) -> None:
    roulette_root = tmp_path
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir()
    (ruleta / "Ruleta.exe").write_text("", encoding="utf-8")
    log_root = tmp_path / "var" / "log" / "ruleta"
    log_root.mkdir(parents=True)

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: roulette_root
    )

    discovery = discover_startup_scan_target()
    assert discovery.mode == "local"
    assert discovery.game_kind == "roulette"
    assert Path(discovery.scan_root) == log_root


def test_discover_startup_usb_export_priority(tmp_path: Path, monkeypatch) -> None:
    export = tmp_path / "log_01_01_2026"
    (export / "SlotLog").mkdir(parents=True)
    slot_root = tmp_path / "Goldclub" / "slot"
    slot_root.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots",
        lambda **kw: (str(export),),
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: slot_root
    )

    discovery = discover_startup_scan_target()
    assert discovery.mode == "local"
    assert discovery.game_kind == "export"
    assert discovery.scan_root == str(export)


def test_discover_startup_remote_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )

    discovery = discover_startup_scan_target(remote_ip="10.0.0.90")
    assert discovery.mode == "remote"
    assert discovery.game_kind is None
    assert discovery.remote_ip == "10.0.0.90"
    assert "10.0.0.90" in discovery.scan_root
