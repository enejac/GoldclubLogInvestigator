from pathlib import Path

from network.goldclub_paths import (
    GoldclubLayoutKind,
    discover_portable_scan_roots,
    discover_startup_scan_target,
    is_usb_log_export_path,
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


def test_is_usb_log_export_path() -> None:
    assert is_usb_log_export_path(r"H:\tools\_LogFiles\log_14_07_2026")
    assert not is_usb_log_export_path(r"C:\Goldclub\var\log")
    assert not is_usb_log_export_path("")


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
    log_parent = tmp_path / "var" / "log"
    log_ruleta = log_parent / "ruleta"
    log_ruleta.mkdir(parents=True)
    # Sibling folders that must be covered by preferring parent var\log.
    (log_parent / "ruleta Roulette").mkdir()
    (log_parent / "godot1").mkdir()

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
    assert Path(discovery.scan_root) == log_parent


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
    # Full var\log so siblings (ruleta Roulette, godot*) are included.
    assert Path(discovery.scan_root).name.lower() == "log"


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


def test_roulette_ruleta_var_meters_preferred(tmp_path: Path) -> None:
    """Roulette: DeviceManagerData under ruleta\\var\\gm2au (not GCMessenger)."""
    goldclub = tmp_path / "Goldclub"
    log_root = goldclub / "var" / "log" / "ruleta"
    log_root.mkdir(parents=True)
    gm2au = goldclub / "ruleta" / "var" / "gm2au"
    gm2au.mkdir(parents=True)
    (gm2au / "DeviceManagerData.xml_1").write_text(
        '<?xml version="1.0"?><root/>', encoding="utf-8"
    )
    # Empty slot-style GCMessenger must not win over ruleta\\var
    (
        goldclub / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger" / "gm2au"
    ).mkdir(parents=True)

    layout = resolve_goldclub_layout(str(log_root))
    assert layout is not None
    assert layout.state_gcmessenger == goldclub / "ruleta" / "var"
    assert (layout.state_gcmessenger / "gm2au" / "DeviceManagerData.xml_1").is_file()


def test_resolve_layout_from_bare_ruleta_var(tmp_path: Path) -> None:
    """Scan root may be the meters folder itself (placeholder: ruleta\\var\\gm2au)."""
    goldclub = tmp_path / "Goldclub"
    ruleta_var = goldclub / "ruleta" / "var"
    gm2au = ruleta_var / "gm2au"
    gm2au.mkdir(parents=True)
    (gm2au / "DeviceManagerData.xml_1").write_text("<root/>", encoding="utf-8")

    layout = resolve_goldclub_layout(str(ruleta_var))
    assert layout is not None
    assert layout.state_gcmessenger == ruleta_var

    layout_child = resolve_goldclub_layout(str(gm2au))
    assert layout_child is not None
    assert layout_child.state_gcmessenger == ruleta_var


def test_roulette_hybrid_resolves_meters_from_c_goldclub(tmp_path: Path, monkeypatch) -> None:
    """Logs on drive-root G:\\var\\log\\ruleta; Aurum meters under C:\\Goldclub\\var\\state."""
    from network.goldclub_paths import resolve_sas_verify_scan_root

    g_drive = tmp_path / "G"
    log_ruleta = g_drive / "var" / "log" / "ruleta"
    log_ruleta.mkdir(parents=True)
    (g_drive / "ruleta").mkdir()
    (g_drive / "ruleta" / "Ruleta.exe").write_text("", encoding="utf-8")

    c_goldclub = tmp_path / "C" / "Goldclub"
    gcm = c_goldclub / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger" / "gm2au"
    gcm.mkdir(parents=True)
    (gcm / "DeviceManagerData.xml_1").write_text("<root/>", encoding="utf-8")
    (c_goldclub / "var" / "log").mkdir(parents=True)

    monkeypatch.setattr(
        "network.goldclub_paths.LOCAL_GOLDCLUB_ROOT", c_goldclub
    )
    monkeypatch.setattr(
        "network.goldclub_paths.LOCAL_STATE_GCM",
        c_goldclub / "var" / "state" / "GoldClub.Aurum.Services" / "GCMessenger",
    )
    monkeypatch.setattr(
        "network.goldclub_paths.LOCAL_LOG_ROOT", c_goldclub / "var" / "log"
    )
    monkeypatch.setattr(
        "network.goldclub_paths.discover_portable_scan_roots", lambda **kw: ()
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: g_drive
    )

    layout = resolve_goldclub_layout(str(log_ruleta))
    assert layout is not None
    assert layout.state_gcmessenger is not None
    assert "Goldclub" in str(layout.state_gcmessenger) or "goldclub" in str(
        layout.state_gcmessenger
    ).casefold()
    assert (layout.state_gcmessenger / "gm2au" / "DeviceManagerData.xml_1").is_file()

    discovery = resolve_sas_verify_scan_root(str(log_ruleta))
    assert discovery.game_kind == "roulette"
    remapped_layout = resolve_goldclub_layout(discovery.scan_root)
    assert remapped_layout is not None
    assert remapped_layout.state_gcmessenger is not None
    assert (
        remapped_layout.state_gcmessenger / "gm2au" / "DeviceManagerData.xml_1"
    ).is_file()


def test_resolve_gm2au_dir_roulette_local(tmp_path: Path, monkeypatch) -> None:
    from network.accounting_scanner import resolve_gm2au_dir

    goldclub = tmp_path / "Goldclub"
    gm2au = goldclub / "ruleta" / "var" / "gm2au"
    gm2au.mkdir(parents=True)
    (gm2au / "DeviceManagerData.xml_1").write_text("<root/>", encoding="utf-8")
    (goldclub / "var" / "log" / "ruleta").mkdir(parents=True)

    monkeypatch.setattr("network.goldclub_paths.LOCAL_GOLDCLUB_ROOT", goldclub)
    monkeypatch.setattr(
        "network.goldclub_paths.LOCAL_LOG_ROOT", goldclub / "var" / "log"
    )

    found = resolve_gm2au_dir(str(goldclub / "var" / "log" / "ruleta"))
    assert found == gm2au


def test_roulette_same_drive_goldclub_state(tmp_path: Path) -> None:
    """Alegro-style: G:\\var\\log\\ruleta logs + G:\\Goldclub\\var\\state meters."""
    g_drive = tmp_path / "G"
    log_ruleta = g_drive / "var" / "log" / "ruleta"
    log_ruleta.mkdir(parents=True)
    gcm = (
        g_drive
        / "Goldclub"
        / "var"
        / "state"
        / "goldclub.aurum.services"
        / "GCMessenger"
        / "SASControler1"
    )
    gcm.mkdir(parents=True)
    (gcm / "DeviceManagerData.xml_1").write_text("<root/>", encoding="utf-8")

    layout = resolve_goldclub_layout(str(log_ruleta))
    assert layout is not None
    assert layout.state_gcmessenger is not None
    meter_file = layout.state_gcmessenger / "SASControler1" / "DeviceManagerData.xml_1"
    assert meter_file.is_file()

def test_standalone_prefers_remote_over_d_when_smb_alive(monkeypatch, tmp_path: Path) -> None:
    from network.goldclub_paths import resolve_sas_verify_standalone_scan_root

    monkeypatch.setattr(
        "network.goldclub_paths._remote_cabinet_reachable", lambda ip, **kw: True
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm", lambda: False
    )
    monkeypatch.setattr(
        "network.goldclub_paths._sas_host_com_present", lambda: True
    )
    monkeypatch.setattr(
        "network.goldclub_paths.resolve_sas_verify_scan_root",
        lambda hint, remote_ip=None, exe_dir=None: __import__(
            "network.goldclub_paths", fromlist=["StartupScanDiscovery"]
        ).StartupScanDiscovery(
            mode="remote" if remote_ip else "local",
            scan_root=(
                rf"\\10.0.0.90\c$\Goldclub\var\log"
                if remote_ip
                else r"G:\var\log"
            ),
            game_kind="slot",
            remote_ip=remote_ip,
        ),
    )
    monkeypatch.setattr(
        "network.goldclub_paths._local_d_drive_scan_candidates",
        lambda exe_dir=None: (str(tmp_path / "D" / "Goldclub" / "var" / "log"),),
    )

    discovery = resolve_sas_verify_standalone_scan_root(remote_ip="10.0.0.90")
    assert discovery.mode == "remote"
    assert "10.0.0.90" in discovery.scan_root


def test_standalone_host_com_on_workstation_still_uses_remote(monkeypatch, tmp_path: Path) -> None:
    """Host cable on PC must not select local G:\\ for Machine column."""
    from network.goldclub_paths import resolve_sas_verify_standalone_scan_root

    monkeypatch.setattr(
        "network.goldclub_paths._remote_cabinet_reachable", lambda ip, **kw: True
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm", lambda: False
    )
    monkeypatch.setattr(
        "network.goldclub_paths._sas_host_com_present", lambda: True
    )

    def fake_resolve(hint, remote_ip=None, exe_dir=None):
        from network.goldclub_paths import StartupScanDiscovery

        if remote_ip or (hint and "10.0.0.90" in str(hint)):
            return StartupScanDiscovery(
                mode="remote",
                scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
                game_kind="roulette",
                remote_ip="10.0.0.90",
            )
        return StartupScanDiscovery(
            mode="local",
            scan_root=r"G:\var\log",
            game_kind="roulette",
            remote_ip=None,
        )

    monkeypatch.setattr(
        "network.goldclub_paths.resolve_sas_verify_scan_root", fake_resolve
    )
    monkeypatch.setattr(
        "network.goldclub_paths._local_d_drive_scan_candidates",
        lambda exe_dir=None: (),
    )

    discovery = resolve_sas_verify_standalone_scan_root(remote_ip="10.0.0.90")
    assert discovery.mode == "remote"
    assert discovery.scan_root.startswith(r"\\10.0.0.90")


def test_standalone_falls_back_to_d_when_remote_down(monkeypatch, tmp_path: Path) -> None:
    from network.goldclub_paths import resolve_sas_verify_standalone_scan_root

    d_log = tmp_path / "D" / "Goldclub" / "var" / "log"
    (d_log / "SlotLog").mkdir(parents=True)

    monkeypatch.setattr(
        "network.goldclub_paths._remote_cabinet_reachable", lambda ip, **kw: False
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm", lambda: False
    )
    monkeypatch.setattr(
        "network.goldclub_paths._local_d_drive_scan_candidates",
        lambda exe_dir=None: (str(d_log),),
    )
    monkeypatch.setattr(
        "network.goldclub_paths.resolve_sas_verify_scan_root",
        lambda hint, remote_ip=None, exe_dir=None: __import__(
            "network.goldclub_paths", fromlist=["StartupScanDiscovery"]
        ).StartupScanDiscovery(
            mode="local",
            scan_root=str(hint or ""),
            game_kind="slot",
            remote_ip=None,
        ),
    )

    discovery = resolve_sas_verify_standalone_scan_root(remote_ip="10.0.0.90")
    assert discovery.mode == "local"
    assert discovery.scan_root == str(d_log)


def _standalone_offline_no_d(monkeypatch, *, host_com: bool):
    """Remote down, no D:\\ candidates — only the discoverable G:\\ fallback left."""
    from network.goldclub_paths import StartupScanDiscovery

    monkeypatch.setattr(
        "network.goldclub_paths._remote_cabinet_reachable", lambda ip, **kw: False
    )
    monkeypatch.setattr(
        "network.health_monitor.is_running_on_local_egm", lambda: False
    )
    monkeypatch.setattr(
        "network.goldclub_paths._sas_host_com_present", lambda: host_com
    )
    monkeypatch.setattr(
        "network.goldclub_paths._local_d_drive_scan_candidates",
        lambda exe_dir=None: (),
    )
    monkeypatch.setattr(
        "network.goldclub_paths.resolve_sas_verify_scan_root",
        lambda hint, remote_ip=None, exe_dir=None: StartupScanDiscovery(
            mode="local",
            scan_root=r"G:\var\log",
            game_kind="slot",
            remote_ip=None,
        ),
    )


def test_standalone_skips_local_g_when_host_com_attached(monkeypatch) -> None:
    """Host SAS cable on a PC: G:\\ is not the EGM on the cable — never use it."""
    from network.goldclub_paths import resolve_sas_verify_standalone_scan_root

    _standalone_offline_no_d(monkeypatch, host_com=True)
    discovery = resolve_sas_verify_standalone_scan_root(remote_ip="10.0.0.90")
    assert discovery.scan_root == ""


def test_standalone_never_auto_picks_g_drive(monkeypatch) -> None:
    """G:\\ is never auto-selected — the dialog asks for permission instead."""
    from network.goldclub_paths import resolve_sas_verify_standalone_scan_root

    _standalone_offline_no_d(monkeypatch, host_com=False)
    discovery = resolve_sas_verify_standalone_scan_root(remote_ip="10.0.0.90")
    assert discovery.scan_root == ""


def test_discover_local_game_image_scan_root(monkeypatch) -> None:
    from network.goldclub_paths import discover_local_game_image_scan_root

    # Direct G:\ probe — independent of the filtered auto-discovery path.
    monkeypatch.setattr(
        "network.goldclub_paths._path_exists_dir",
        lambda p: str(p).replace("/", "\\").lower() in {
            r"g:\var\log",
            r"g:\var\log\ruleta",
        },
    )
    found = discover_local_game_image_scan_root()
    assert found.replace("/", "\\").lower().startswith(r"g:\var\log")


def test_is_game_image_drive_path() -> None:
    from network.goldclub_paths import is_game_image_drive_path

    assert is_game_image_drive_path(r"G:\var\log\ruleta")
    assert is_game_image_drive_path("g:/var/log")
    assert not is_game_image_drive_path(r"C:\Goldclub\var\log")
    assert not is_game_image_drive_path(r"\\10.0.0.90\c$\Goldclub\var")
    assert not is_game_image_drive_path("")


def test_local_filesystem_path_rewrites_self_unc() -> None:
    from network.goldclub_paths import (
        local_filesystem_path_for_scan_root,
        path_is_local_filesystem,
    )

    assert local_filesystem_path_for_scan_root(r"\\127.0.0.1\c$\Goldclub\var\log") == (
        r"C:\Goldclub\var\log"
    )
    assert local_filesystem_path_for_scan_root(r"\\localhost\g$\var\log") == r"G:\var\log"
    assert local_filesystem_path_for_scan_root(r"C:\Goldclub\var\log") == r"C:\Goldclub\var\log"
    # Remote cabinet must stay on the share.
    remote = r"\\10.0.0.90\c$\Goldclub\var\log"
    assert local_filesystem_path_for_scan_root(remote) == remote
    assert path_is_local_filesystem(r"\\127.0.0.1\c$\Goldclub\var\log")
    assert not path_is_local_filesystem(remote)


def test_prefer_var_root_when_meters_under_state(tmp_path: Path) -> None:
    from network.goldclub_paths import prefer_var_root_when_meters_under_state

    log_root = tmp_path / "Goldclub" / "var" / "log"
    log_root.mkdir(parents=True)
    gcm = (
        tmp_path
        / "Goldclub"
        / "var"
        / "state"
        / "GoldClub.Aurum.Services"
        / "GCMessenger"
    )
    (gcm / "gm2au").mkdir(parents=True)
    (gcm / "gm2au" / "DeviceManagerData.xml_1").write_text("<x/>", encoding="utf-8")

    assert prefer_var_root_when_meters_under_state(str(log_root)) == str(
        tmp_path / "Goldclub" / "var"
    )
    # Non-log leaf unchanged.
    assert prefer_var_root_when_meters_under_state(str(tmp_path / "Goldclub" / "var")) == str(
        tmp_path / "Goldclub" / "var"
    )


def test_prefer_var_root_keeps_log_when_no_state(tmp_path: Path) -> None:
    from network.goldclub_paths import prefer_var_root_when_meters_under_state

    log_root = tmp_path / "Goldclub" / "var" / "log"
    log_root.mkdir(parents=True)
    assert prefer_var_root_when_meters_under_state(str(log_root)) == str(log_root)

