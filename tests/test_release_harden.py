"""Release-blocker regression coverage for harden Investigator release."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from network.accounting_state_loader import (
    flatten_xml_file_to_norm_map,
    load_cabinet_game_catalog,
    parse_game_catalog_entries,
)
from network.lab_access import (
    FleetAllowlistError,
    assert_ruleta_dest_unc,
    get_lab_credential,
    lab_username_for_host,
    require_lab_fleet_ip,
    safe_join_under,
)


def test_require_lab_fleet_ip_accepts_known_hosts() -> None:
    assert require_lab_fleet_ip("10.0.0.90") == "10.0.0.90"
    assert require_lab_fleet_ip(" 10.0.0.171 ") == "10.0.0.171"


def test_require_lab_fleet_ip_rejects_non_fleet() -> None:
    with pytest.raises(FleetAllowlistError):
        require_lab_fleet_ip("10.0.0.1")
    with pytest.raises(FleetAllowlistError):
        require_lab_fleet_ip("8.8.8.8")
    with pytest.raises(FleetAllowlistError):
        require_lab_fleet_ip("")


def test_safe_join_under_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "cfg"
    root.mkdir()
    (root / "ok.xml").write_text("<a/>", encoding="utf-8")
    assert safe_join_under(root, "ok.xml").name == "ok.xml"
    with pytest.raises(ValueError):
        safe_join_under(root, "../escape.xml")
    with pytest.raises(ValueError):
        safe_join_under(root, r"C:\Windows\system.ini")
    with pytest.raises(ValueError):
        safe_join_under(root, "/etc/passwd")


def test_assert_ruleta_dest_unc_confined() -> None:
    ok = assert_ruleta_dest_unc("10.0.0.90", r"\\10.0.0.90\c$\goldclub\ruleta")
    assert "ruleta" in str(ok).lower()
    slot = assert_ruleta_dest_unc("10.0.0.111", r"\\10.0.0.111\slot\ruleta")
    assert "slot" in str(slot).casefold()
    with pytest.raises(ValueError):
        assert_ruleta_dest_unc("10.0.0.90", r"\\10.0.0.90\c$\Windows")
    with pytest.raises(ValueError):
        assert_ruleta_dest_unc("10.0.0.111", r"\\10.0.0.111\USB_Remote\ruleta")
    with pytest.raises(FleetAllowlistError):
        assert_ruleta_dest_unc("1.2.3.4", r"\\1.2.3.4\c$\goldclub\ruleta")


def test_lab_username_workgroup_vs_domain() -> None:
    assert lab_username_for_host("10.0.0.111") == r"10.0.0.111\test"
    assert lab_username_for_host("10.0.0.90") == r"GOLD-CLUB\test"


def test_lab_winrm_authentication_uses_default_for_fleet() -> None:
    from network.lab_access import lab_winrm_authentication

    assert lab_winrm_authentication("10.0.0.111") == "Default"
    assert lab_winrm_authentication("10.0.0.90") == "Default"
    assert lab_winrm_authentication("10.0.0.1") == "Negotiate"


def test_get_lab_credential_rejects_domain_user_on_111(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOLDCLUB_LAB_PASSWORD", raising=False)
    monkeypatch.setattr(
        "network.lab_access._lab_credential_from_manager",
        lambda _ip: (r"GOLD-CLUB\test", "test"),
    )
    user, _pw = get_lab_credential("10.0.0.111")
    assert user.casefold() == r"10.0.0.111\test".casefold()


def test_parse_game_catalog_themepath_only() -> None:
    xml = """
<Catalog>
  <GameSelectorButton>
    <ThemePath>LotusPrincess</ThemePath>
  </GameSelectorButton>
</Catalog>
"""
    entries = parse_game_catalog_entries(xml)
    assert entries == [("LotusPrincess", "LotusPrincess")]


def test_load_cabinet_game_catalog_absent_falls_back_to_folders(tmp_path: Path) -> None:
    themes = tmp_path / "Goldclub" / "slot" / "themes"
    (themes / "ThemeA").mkdir(parents=True)
    (themes / "ThemeA" / "MathSettings.xml").write_text("<Math/>", encoding="utf-8")
    (themes / "ThemeB").mkdir()
    (themes / "ThemeB" / "config_SetClear.xml").write_text("<cfg/>", encoding="utf-8")
    log_root = tmp_path / "Goldclub" / "var" / "log"
    (log_root / "SlotLog").mkdir(parents=True)
    entries = load_cabinet_game_catalog(str(log_root))
    names = {e[0] for e in entries}
    assert "ThemeA" in names and "ThemeB" in names


def _write_meter_xml(path: Path, *, master: int | None, theme: int | None) -> None:
    root = Element("root")
    if master is not None:
        proc = SubElement(root, "processorData")
        SubElement(proc, "m", meterName="CoinIn", meterValue=str(master))
    if theme is not None:
        theme_el = SubElement(root, "themeData")
        SubElement(
            theme_el, "m", meterName="CoinIn", meterValue=str(theme), themeId="T1"
        )
    path.write_text(tostring(root, encoding="unicode"), encoding="utf-8")


def test_master_zero_beats_theme_sum(tmp_path: Path) -> None:
    xml_path = tmp_path / "state.xml"
    _write_meter_xml(xml_path, master=0, theme=500)
    flat = flatten_xml_file_to_norm_map(xml_path)
    assert flat.get("coinin") == "0"


def test_theme_sum_used_when_no_master(tmp_path: Path) -> None:
    xml_path = tmp_path / "state.xml"
    _write_meter_xml(xml_path, master=None, theme=120)
    flat = flatten_xml_file_to_norm_map(xml_path)
    assert flat.get("coinin") == "120"


def test_remote_exec_uses_file_params_not_password_constant() -> None:
    import automation.remote_exec as rexe

    src = Path(rexe.__file__).read_text(encoding="utf-8")
    assert "_LAB_PASS" not in src
    assert 'password = "test"' not in src
    assert "params.json" in src
    assert "-File" in src


def test_software_version_scripts_resolvable() -> None:
    from network.software_version_swap import _find_local_roulette_script, _roulette_tools_dir

    d = _roulette_tools_dir()
    assert (d / "Kill-All.ps1").is_file()
    assert (d / "Run-FullStack.ps1").is_file()
    assert (d / "GoldClubServices.ps1").is_file()
    assert _find_local_roulette_script("Kill-All.ps1") is not None


def test_spec_declares_roulette_scripts_and_forbids_inject() -> None:
    spec = Path("LogInvestigator.spec").read_text(encoding="utf-8")
    for name in (
        "Kill-All.ps1",
        "Run-FullStack.ps1",
        "Invoke-SoftwareVersionSwap.ps1",
        "GoldClubServices.ps1",
    ):
        assert name in spec
    assert "_FORBIDDEN_DATA_SUBSTR" in spec
    assert "dallas" in spec.lower()
    assert "windivert" in spec.lower()


def test_packaged_modules_have_no_plaintext_lab_password() -> None:
    bad: list[str] = []
    for root in (Path("network"), Path("automation"), Path("gui")):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if '_LAB_PASS = "test"' in text or "ConvertTo-SecureString 'test'" in text:
                bad.append(str(path))
    assert bad == []


def test_ram_clear_rejects_non_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    from network import ram_clear

    monkeypatch.setattr("os.name", "nt")
    ok, msg = ram_clear.run_ram_clear_remote("10.0.0.1")
    assert ok is False
    assert "fleet" in msg.lower() or "refusing" in msg.lower()


def test_time_sync_rejects_non_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    from network import time_sync

    monkeypatch.setattr("os.name", "nt")
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.1")
    assert ok is False
    assert "fleet" in msg.lower() or "refusing" in msg.lower()


def test_dest_ruleta_unc_rejects_non_fleet() -> None:
    from network.software_version_swap import dest_ruleta_unc

    with pytest.raises(FleetAllowlistError):
        dest_ruleta_unc("192.168.1.1")


def test_main_window_scan_generation_attrs_exist() -> None:
    src = Path("gui/main_window.py").read_text(encoding="utf-8")
    assert "_scan_generation" in src
    assert "_active_scan_generation" in src
    assert "_shutdown_save_completed" in src
    assert "_on_session_save_finished" in src


def test_sas_verify_compare_job_guards_exist() -> None:
    src = Path("gui/sas_verify_dialog.py").read_text(encoding="utf-8")
    assert "_compare_job_id" in src
    assert "_active_compare_job_id" in src
    assert "_resolve_active_scan_root" in src


def test_main_window_remote_ops_use_effective_remote_gate() -> None:
    """Sub-tools must not treat Remote+self as off-box when on the cabinet."""
    src = Path("gui/main_window.py").read_text(encoding="utf-8")
    assert "def _get_remote_ip" in src
    assert "effective_remote_ip" in src
    assert "wants_remote_operations" in src
    # SAS Verify open path must use the gate, not raw radio+IP.
    assert "remote_ip = self._get_remote_ip()" in src
    assert "set_remote_ram_target_ip(self._get_remote_ip())" in src
    assert "bool(tip) and not self._remote_capture_busy" in src
    assert "is_this_host(ip)" in src
    assert "_on_incidents_capture_screen_clicked" in src
    # Capture from Connection must go through the gate.
    assert (
        "def _on_incidents_capture_screen_clicked" in src
        and "ip = self._get_remote_ip()" in src
    )
