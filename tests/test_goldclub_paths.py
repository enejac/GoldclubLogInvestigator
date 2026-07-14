from pathlib import Path

from network.goldclub_paths import (
    GoldclubLayoutKind,
    discover_portable_scan_roots,
    discover_startup_scan_target,
    resolve_goldclub_layout,
    resolve_log_scan_root,
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
    monkeypatch.setattr(
        "network.goldclub_paths._refine_log_scan_root_from_install",
        lambda **kw: None,
    )

    discovery = discover_startup_scan_target(remote_ip="10.0.0.90")
    assert discovery.mode == "remote"
    assert discovery.game_kind is None
    assert discovery.remote_ip == "10.0.0.90"
    assert "10.0.0.90" in discovery.scan_root


def test_discover_startup_remote_refines_when_reachable(monkeypatch) -> None:
    from network.goldclub_paths import StartupScanDiscovery

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._refine_log_scan_root_from_install",
        lambda **kw: StartupScanDiscovery(
            mode="remote",
            scan_root=r"\\10.0.0.90\c$\Goldclub\var\log\ruleta",
            game_kind="roulette",
            remote_ip="10.0.0.90",
        ),
    )

    discovery = discover_startup_scan_target(remote_ip="10.0.0.90")
    assert discovery.game_kind == "roulette"
    assert discovery.scan_root.endswith("ruleta")


def test_resolve_log_scan_root_refines_generic_unc_to_roulette(tmp_path: Path, monkeypatch) -> None:
    goldclub = tmp_path / "Goldclub"
    ruleta = goldclub / "ruleta"
    ruleta.mkdir(parents=True)
    (ruleta / "Ruleta.exe").write_text("", encoding="utf-8")
    log_ruleta = goldclub / "var" / "log" / "ruleta"
    log_ruleta.mkdir(parents=True)

    unc_goldclub = Path(rf"\\10.0.0.90\c$\Goldclub")

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )

    def exists_dir(p: Path) -> bool:
        s = str(p).replace("/", "\\").casefold()
        unc = str(unc_goldclub).casefold()
        if s.startswith(unc):
            return "slot" not in s or not s.endswith(r"\slot")
        return str(p).startswith(str(tmp_path))

    monkeypatch.setattr("network.goldclub_paths._path_exists_dir", exists_dir)
    monkeypatch.setattr(
        "network.goldclub_paths._path_exists_file",
        lambda p: p.name.lower() == "ruleta.exe",
    )

    hint = r"\\10.0.0.90\c$\Goldclub\var\log"
    discovery = resolve_log_scan_root(hint, remote_ip="10.0.0.90")
    assert discovery.game_kind == "roulette"
    assert discovery.scan_root.endswith("ruleta")


def test_resolve_log_scan_root_keeps_slot_unc(tmp_path: Path, monkeypatch) -> None:
    goldclub = tmp_path / "Goldclub"
    slot = goldclub / "slot"
    slot.mkdir(parents=True)
    (slot / "OneHand.exe").write_text("", encoding="utf-8")
    log_root = goldclub / "var" / "log"
    (log_root / "SlotLog").mkdir(parents=True)

    unc_goldclub = Path(rf"\\10.0.0.90\c$\Goldclub")

    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )

    def exists_dir(p: Path) -> bool:
        s = str(p).replace("/", "\\").casefold()
        unc = str(unc_goldclub).casefold()
        if s.startswith(unc):
            return True
        return str(p).startswith(str(tmp_path))

    monkeypatch.setattr("network.goldclub_paths._path_exists_dir", exists_dir)
    monkeypatch.setattr(
        "network.goldclub_paths._path_exists_file",
        lambda p: p.name.lower() == "onehand.exe",
    )

    hint = r"\\10.0.0.90\c$\Goldclub\var\log"
    discovery = resolve_log_scan_root(hint, remote_ip="10.0.0.90")
    assert discovery.game_kind == "slot"
    assert Path(discovery.scan_root).name.lower() == "log"


def test_resolve_log_scan_root_local_generic_uses_startup(tmp_path: Path, monkeypatch) -> None:
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

    discovery = resolve_log_scan_root(r"C:\Goldclub\var\log")
    assert discovery.game_kind == "slot"
    assert Path(discovery.scan_root) == log_root
