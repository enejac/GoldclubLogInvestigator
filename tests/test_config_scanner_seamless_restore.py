"""Seamless Config Scanner restore: paytable compat, version warn, stack plan."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config_scanner.build_version import BuildInfo
from config_scanner.paytable_compat import (
    choose_compatible_paytable,
    filter_incompatible_paytable_restore_paths,
    known_paytable_ids,
    reconcile_processor_status_paytable,
    remap_live_ruleta_paytables,
    split_device_manager_bytes,
)
from config_scanner.ruleta_compat import (
    apply_ruleta_compat_after_restore,
    plan_ruleta_compat,
)
from config_scanner.software_compat import (
    SoftwarePushError,
    cabinet_host_for_scan_target,
    find_ruleta_software_pack,
    pack_refuse_reason,
    software_version_mismatch_warning,
)
from config_scanner.stack_restart import (
    plan_stack_restart,
    run_full_stack_restart,
    should_autostart_after_write,
)


_PROCESSOR = (
    '<processorStatus d4p1:themeId="futura_doublezero" '
    'd4p1:paytableId="paytable_elite_double_zero" d4p1:denomId="10000" />'
)
_PERF = (
    '<d4p1:perfMeter d4p1:meterName="coinIn" d4p1:themeId="futura_doublezero" '
    'd4p1:paytableId="paytable_elite_double_zero" d4p1:meterValue="540000" />'
)
_XML = (
    '<?xml version="1.0"?>\n<root>\n      '
    + _PROCESSOR
    + "\n      "
    + _PERF
    + "\n</root>\n"
)


def _build_info(**kwargs: object) -> BuildInfo:
    base = dict(
        source_version=None,
        branch=None,
        product_version="10.2.0.684",
        build_number="40114",
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp="t",
        game_drive=r"\\10.0.0.111\slot",
        profile_id="roulette_usb",
        profile_label="Ruleta Alegro Wing",
        exe_product_version="10.2.0.684",
        exe_file_version=None,
        exe_product_name="Ruleta Module",
        machine_serial="GRT330106",
    )
    base.update(kwargs)
    return BuildInfo(**base)  # type: ignore[arg-type]


def test_choose_compatible_paytable_maps_10_2_name_on_doublezero(tmp_path: Path) -> None:
    known = known_paytable_ids(tmp_path, "10.1")
    assert "paytable_elite_double_zero" not in known
    assert (
        choose_compatible_paytable(
            "paytable_elite_double_zero",
            "futura_doublezero",
            known,
        )
        == "paytable_double_zero"
    )
    assert (
        choose_compatible_paytable(
            "paytable_double_zero",
            "futura_doublezero",
            known,
        )
        is None
    )


def test_choose_compatible_paytable_keeps_10_2_name_when_live_is_10_2(tmp_path: Path) -> None:
    known = known_paytable_ids(tmp_path, "10.2")
    assert "paytable_elite_double_zero" in known
    assert (
        choose_compatible_paytable(
            "paytable_elite_double_zero",
            "futura_doublezero",
            known,
        )
        is None
    )


def test_reconcile_does_not_rewrite_signed_device_manager(tmp_path: Path) -> None:
    header = bytes(range(16))
    dest = tmp_path / "goldclub"
    sas = dest / "ruleta" / "var" / "SASControler1"
    sas.mkdir(parents=True)
    path = sas / "DeviceManagerData.xml_1"
    original = header + _XML.encode("utf-8")
    path.write_bytes(original)

    notes = reconcile_processor_status_paytable(dest)
    assert path.read_bytes() == original
    text = split_device_manager_bytes(path.read_bytes())[1].decode("utf-8")
    assert 'paytableId="paytable_elite_double_zero"' in text
    assert "processorStatus" in text
    assert notes == []


def test_plan_holds_10_1_when_signed_sas_has_10_2_paytable(tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    sas = dest / "ruleta" / "var" / "SASControler1"
    sas.mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )
    xml = (
        '<?xml version="1.0"?><root>'
        '<processorStatus paytableId="paytable_elite_double_zero" '
        'themeId="futura_doublezero" />'
        "</root>"
    )
    (sas / "DeviceManagerData.xml_1").write_bytes(bytes(range(16)) + xml.encode("utf-8"))
    plan = plan_ruleta_compat(dest)
    assert plan.hold_start is True
    assert "paytable_elite_double_zero" in plan.signed_sas_10_2_ids
    notes = apply_ruleta_compat_after_restore(dest)
    hold = dest / "var" / "state" / "ruleta-compat-hold.json"
    assert hold.is_file()
    assert any("bad conversion" in n or "cannot load" in n for n in notes)
    assert (sas / "DeviceManagerData.xml_1").read_bytes()[:16] == bytes(range(16))


def test_plan_ok_when_live_is_10_2(monkeypatch, tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    dest.mkdir()
    (dest / "ruleta").mkdir()
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        "config_scanner.ruleta_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )
    monkeypatch.setattr(
        "config_scanner.ruleta_compat.live_exe_is_ruleta_10_2",
        lambda _mm: True,
    )
    monkeypatch.setattr(
        "config_scanner.ruleta_compat.signed_device_manager_10_2_paytable_ids",
        lambda _root: ("paytable_elite_double_zero",),
    )
    plan = plan_ruleta_compat(dest)
    assert plan.hold_start is False
    assert plan.action == "ok"


def test_software_version_mismatch_warning(monkeypatch, tmp_path: Path) -> None:
    live_root = tmp_path / "live"
    (live_root / "ruleta").mkdir(parents=True)
    (live_root / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")

    monkeypatch.setattr(
        "config_scanner.software_compat.detect_roulette_exe_version",
        lambda _root: SimpleNamespace(
            product_version="10.1.8.0",
            display_version="10.1.8.0",
            file_version="10.1.8.0",
        ),
    )
    warning = software_version_mismatch_warning(_build_info(), str(live_root))
    assert warning is not None
    assert "10.2" in warning and "10.1" in warning
    assert "Restore this snapshot" in warning or "full snapshot" in warning

    monkeypatch.setattr(
        "config_scanner.software_compat.detect_roulette_exe_version",
        lambda _root: SimpleNamespace(
            product_version="10.2.0.684",
            display_version="10.2.0.684",
            file_version="10.2.0.684",
        ),
    )
    same = software_version_mismatch_warning(_build_info(), str(live_root))
    assert same is not None
    assert "ERROR 30" in same


def test_cabinet_host_for_scan_target() -> None:
    assert cabinet_host_for_scan_target(r"\\10.0.0.111\slot") == "10.0.0.111"
    assert cabinet_host_for_scan_target(r"C:\goldclub") == "local"


def test_find_ruleta_software_pack_skips_locked_10_2_0_0(
    monkeypatch, tmp_path: Path
) -> None:
    locked = tmp_path / "Ruleta_v10.2.0.0_build40097"
    wanted = tmp_path / "Ruleta_v10.2.0.684_build40097"
    locked.mkdir()
    wanted.mkdir()
    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [wanted, locked],
    )
    found = find_ruleta_software_pack(_build_info())
    assert found is not None
    assert found.name == "Ruleta_v10.2.0.684_build40097"


def test_find_ruleta_software_pack_refuses_silent_827_to_684(
    monkeypatch, tmp_path: Path
) -> None:
    pack_684 = tmp_path / "Ruleta_v10.2.0.684_build40097"
    pack_684.mkdir()
    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack_684],
    )
    found = find_ruleta_software_pack(
        _build_info(product_version="10.2.0.827", exe_product_version="10.2.0.827")
    )
    assert found is None


def test_software_mismatch_skipped_for_slot() -> None:
    info = _build_info(profile_id="slot_lab_90", product_version="1.0", exe_product_version="1.0")
    assert software_version_mismatch_warning(info, r"C:\Goldclub\slot") is None


def test_should_autostart_after_write() -> None:
    assert should_autostart_after_write(
        auto_start=True, stack_killed=True, has_plan=True
    )
    assert not should_autostart_after_write(
        auto_start=False, stack_killed=True, has_plan=True
    )
    assert not should_autostart_after_write(
        auto_start=True, stack_killed=False, has_plan=True
    )
    assert not should_autostart_after_write(
        auto_start=True, stack_killed=True, has_plan=False
    )


def test_plan_stack_restart_skips_offline_letter(monkeypatch) -> None:
    from config_scanner import stack_restart as sr

    monkeypatch.setattr(sr, "running_on_egm", lambda: False)
    monkeypatch.setattr(
        sr,
        "find_stack_scripts",
        lambda: (Path("Kill-All.ps1"), Path("Run-FullStack.ps1")),
    )
    assert plan_stack_restart("E:\\") is None
    assert plan_stack_restart("G:\\") is None


def test_run_full_stack_restart_kill_then_start(monkeypatch) -> None:
    from config_scanner import stack_restart as sr
    from config_scanner.stack_restart import StackRestartPlan

    calls: list[str] = []

    def fake_local(script: str, args: list[str], *, timeout: int, label: str):
        calls.append(label)
        return True, f"{label} OK"

    monkeypatch.setattr(sr, "_run_powershell_file", fake_local)
    monkeypatch.setattr(sr, "sys", SimpleNamespace(platform="win32"))
    plan = StackRestartPlan(
        mode="local",
        kill_ps1="Kill-All.ps1",
        run_ps1="Run-FullStack.ps1",
    )
    ok, detail = run_full_stack_restart(plan)
    assert ok
    assert calls == ["Kill-All", "Run-FullStack"]
    assert "Kill-All" in detail


def test_apply_snapshot_reconciles_paytable(tmp_path: Path) -> None:
    from config_scanner.service import ConfigScannerService

    dest = tmp_path / "goldclub"
    ruleta = dest / "ruleta"
    ruleta.mkdir(parents=True)
    (ruleta / "Ruleta.exe").write_bytes(b"MZ")
    (ruleta / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    cfg.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<setup/>", encoding="utf-8")
    sas = dest / "ruleta" / "var" / "SASControler1"
    sas.mkdir(parents=True)
    (sas / "DeviceManagerData.xml_2").write_bytes(
        bytes(range(16)) + _XML.encode("utf-8")
    )
    (sas / "GCC_RT_330106_01_combo.dat").write_text(
        "<gameV><PaytableId>GR0072</PaytableId>"
        "<EgmPaytableId>paytable_elite_double_zero</EgmPaytableId></gameV>\n",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    (tool_root / "snapshots").mkdir()
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    snap = tool_root / "snapshots" / "r_snap"
    archived = snap / "files" / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    archived.parent.mkdir(parents=True)
    archived.write_text("<setup/>", encoding="utf-8")
    (snap / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "38884",
                "productVersion": "10.1.8.0",
                "scanTimestamp": "t",
                "gameDrive": str(dest),
                "profileId": "roulette_usb",
                "profileLabel": "Ruleta Alegro Wing",
            }
        ),
        encoding="utf-8",
    )
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "fileCount": 1,
                "elapsedSeconds": 0.1,
                "files": [
                    {
                        "relativePath": "config/etc/application/ruleta/setup.xml",
                        "sha1": "AAA",
                        "sizeBytes": 8,
                        "lastWriteUtc": "t",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    (cfg / "setup.xml").write_text("<mutated/>", encoding="utf-8")
    paytables = cfg / "paytables"
    paytables.mkdir(parents=True)
    (paytables / "paytable_elite_double_zero.json").write_text(
        '{"name": "paytable_elite_double_zero"}',
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("r_snap", str(dest))
    assert result.written_count == 1
    assert any("paytable_double_zero" in note for note in result.notes)
    text = (sas / "DeviceManagerData.xml_2").read_bytes().decode("latin1")
    assert 'paytableId="paytable_elite_double_zero"' in text
    combo = (sas / "GCC_RT_330106_01_combo.dat").read_text(encoding="utf-8")
    assert "<PaytableId>GR0072</PaytableId>" in combo
    assert "<EgmPaytableId>paytable_double_zero</EgmPaytableId>" in combo
    assert not (paytables / "paytable_elite_double_zero.json").is_file()
    assert (paytables / "paytable_elite_double_zero.json.disabled-101").is_file()


def test_known_paytable_ids_ignores_10_2_json_on_live_10_1(tmp_path: Path) -> None:
    folder = tmp_path / "config" / "etc" / "application" / "ruleta" / "paytables"
    folder.mkdir(parents=True)
    (folder / "paytable_elite_double_zero.json").write_text(
        '{"name": "paytable_elite_double_zero"}',
        encoding="utf-8",
    )
    (folder / "paytable_double_zero.json").write_text(
        '{"name": "paytable_double_zero"}',
        encoding="utf-8",
    )
    known = known_paytable_ids(tmp_path, "10.1")
    assert "paytable_double_zero" in known
    assert "paytable_elite_double_zero" not in known
    assert (
        choose_compatible_paytable(
            "paytable_premium_double_zero",
            "futura_doublezero",
            known,
        )
        == "paytable_premium"
    )


def test_remap_combo_dynamic_standard_on_10_2(monkeypatch, tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    combo_dir = dest / "ruleta" / "var" / "SASControler1"
    combo_dir.mkdir(parents=True)
    (combo_dir / "GCC_RT_330106_01_combo.dat").write_text(
        "<gameV><EgmId>GCC_RT_330106_01</EgmId>"
        "<PaytableId>GR0072</PaytableId>"
        "<EgmPaytableId>dynamic_standard</EgmPaytableId></gameV>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )
    notes = remap_live_ruleta_paytables(dest)
    assert any("dynamic_standard" in n for n in notes)
    combo = (combo_dir / "GCC_RT_330106_01_combo.dat").read_text(encoding="utf-8")
    assert "<PaytableId>GR0072</PaytableId>" in combo
    assert "<EgmPaytableId>paytable_double_zero</EgmPaytableId>" in combo
    assert "dynamic_standard" not in combo


def test_remap_combo_and_aurum_setup_keeps_identity(tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    combo_dir = dest / "ruleta" / "var" / "SASControler1"
    combo_dir.mkdir(parents=True)
    (combo_dir / "GCC_RT_330106_01_combo.dat").write_text(
        "<gameV><EgmId>GCC_RT_330106_01</EgmId>"
        "<PaytableId>GR0072</PaytableId>"
        "<EgmPaytableId>paytable_elite_double_zero</EgmPaytableId></gameV>\n",
        encoding="utf-8",
    )
    aurum = dest / "services" / "aurum" / "config"
    aurum.mkdir(parents=True)
    (aurum / "AurumSetup.xml").write_text(
        '<root><EgmId>GCC_RT_330106_01</EgmId>'
        '<CabinetSerialNumber>330106</CabinetSerialNumber>'
        '<combo paytableId="paytable_elite_double_zero" />'
        '<combo paytableId="paytable_premium_double_zero" /></root>\n',
        encoding="utf-8",
    )
    notes = remap_live_ruleta_paytables(dest)
    assert notes
    combo = (combo_dir / "GCC_RT_330106_01_combo.dat").read_text(encoding="utf-8")
    assert "<PaytableId>GR0072</PaytableId>" in combo
    assert "<EgmId>GCC_RT_330106_01</EgmId>" in combo
    assert "<EgmPaytableId>paytable_double_zero</EgmPaytableId>" in combo
    assert "paytable_elite_double_zero" not in combo
    setup = (aurum / "AurumSetup.xml").read_text(encoding="utf-8")
    assert "<EgmId>GCC_RT_330106_01</EgmId>" in setup
    assert "<CabinetSerialNumber>330106</CabinetSerialNumber>" in setup
    assert 'paytableId="paytable_double_zero"' in setup
    assert 'paytableId="paytable_premium"' in setup
    assert "paytable_elite_double_zero" not in setup
    assert "paytable_premium_double_zero" not in setup


def test_filter_skips_10_2_json_when_live_is_not_10_2(tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    dest.mkdir()
    (dest / "ruleta").mkdir()
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    paths = [
        "config/etc/application/ruleta/setup.xml",
        "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json",
        "config/etc/application/ruleta/paytables/paytable_double_zero.json",
    ]
    kept, notes = filter_incompatible_paytable_restore_paths(paths, dest)
    assert "config/etc/application/ruleta/setup.xml" in kept
    assert "config/etc/application/ruleta/paytables/paytable_double_zero.json" in kept
    assert (
        "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json"
        not in kept
    )
    assert notes


def _write_minimal_snapshot(
    tool_root: Path,
    dest: Path,
    *,
    product_version: str,
    files: dict[str, str],
) -> None:
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "scanRoots": ["config"],
                "includePatterns": ["*.xml", "*.json"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    (tool_root / "snapshots").mkdir(exist_ok=True)
    (tool_root / "reports").mkdir(exist_ok=True)
    (tool_root / "templates").mkdir(exist_ok=True)
    snap = tool_root / "snapshots" / "r_snap"
    snap.mkdir(exist_ok=True)
    manifest_files = []
    for rel, text in files.items():
        archived = snap / "files" / Path(rel)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text(text, encoding="utf-8")
        manifest_files.append(
            {
                "relativePath": rel,
                "sha1": "AAA",
                "sizeBytes": len(text.encode("utf-8")),
                "lastWriteUtc": "t",
            }
        )
    (snap / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "40097",
                "productVersion": product_version,
                "scanTimestamp": "t",
                "gameDrive": str(dest),
                "profileId": "roulette_usb",
                "profileLabel": "Ruleta Alegro Wing",
            }
        ),
        encoding="utf-8",
    )
    (snap / "manifest.json").write_text(
        json.dumps(
            {
                "fileCount": len(manifest_files),
                "elapsedSeconds": 0.1,
                "files": manifest_files,
            }
        ),
        encoding="utf-8",
    )


def test_apply_snapshot_no_paytable_skips_json(tmp_path: Path) -> None:
    from config_scanner.service import ConfigScannerService

    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    paytables = cfg / "paytables"
    paytables.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<mutated/>", encoding="utf-8")
    (paytables / "paytable_double_zero.json").write_text(
        '{"name": "live"}', encoding="utf-8"
    )
    (dest / "ruleta").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    _write_minimal_snapshot(
        tool_root,
        dest,
        product_version="10.2.0.684",
        files={
            "config/etc/application/ruleta/setup.xml": "<setup/>",
            "config/etc/application/ruleta/paytables/paytable_double_zero.json": (
                '{"name": "snap"}'
            ),
        },
    )
    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "r_snap", str(dest), write_scope="no_paytable"
    )
    assert result.write_scope == "no_paytable"
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<setup/>"
    assert (paytables / "paytable_double_zero.json").read_text(
        encoding="utf-8"
    ) == '{"name": "live"}'


def test_apply_snapshot_full_software_pushes_pack(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    paytables = cfg / "paytables"
    paytables.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<mutated/>", encoding="utf-8")
    (dest / "ruleta").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    _write_minimal_snapshot(
        tool_root,
        dest,
            product_version="10.2.0.876",
            files={
                "config/etc/application/ruleta/setup.xml": "<setup/>",
                "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json": (
                    '{"name": "snap"}'
                ),
            },
        )
    pack = tmp_path / "Ruleta_v10.2.0.876_build40119"
    pack.mkdir()
    calls: list[Path] = []

    def fake_swap(host, source, **kwargs):
        calls.append(Path(source))
        return SimpleNamespace(ok=True, log="ok")

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr("network.software_version_swap.run_swap", fake_swap)
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "r_snap", str(dest), write_scope="full_software"
    )
    assert result.write_scope == "full_software"
    assert calls == [pack]
    assert any("Pushed Ruleta software" in note for note in result.notes)
    assert (paytables / "paytable_elite_double_zero.json").read_text(
        encoding="utf-8"
    ) == '{"name": "snap"}'


def test_apply_snapshot_full_software_stops_before_config(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    paytables = cfg / "paytables"
    paytables.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<mutated/>", encoding="utf-8")
    (dest / "ruleta").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    _write_minimal_snapshot(
        tool_root,
        dest,
            product_version="10.2.0.876",
        files={
            "config/etc/application/ruleta/setup.xml": "<setup/>",
            "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json": (
                '{"name": "snap"}'
            ),
        },
    )

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [],
    )

    service = ConfigScannerService(tool_root)
    with pytest.raises(SoftwarePushError, match="software pack|software_versions"):
        service.apply_snapshot_to_target(
            "r_snap", str(dest), write_scope="full_software"
        )
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<mutated/>"
    assert not (paytables / "paytable_elite_double_zero.json").is_file()


def test_push_matching_refuses_684_pack(tmp_path: Path, monkeypatch) -> None:
    from config_scanner.build_version import BuildInfo
    from config_scanner.software_compat import push_matching_ruleta_software

    dest = tmp_path / "goldclub"
    (dest / "ruleta").mkdir(parents=True)
    pack = tmp_path / "Ruleta_v10.2.0.684_build40097"
    pack.mkdir()
    (pack / "Ruleta.exe").write_bytes(b"MZ")
    info = BuildInfo(
        source_version="876",
        branch="Development",
        product_version="10.2.0.876",
        build_number="40119",
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp="t",
        game_drive=str(dest),
        exe_product_version="10.2.0.876",
    )
    monkeypatch.setattr(
        "config_scanner.software_compat.resolve_software_pack_for_snapshot",
        lambda *args, **kwargs: pack,
    )
    with pytest.raises(SoftwarePushError, match="ERROR 30"):
        push_matching_ruleta_software(info, str(dest))


def test_pack_refuse_reason_blocks_trial_and_downloads() -> None:
    assert pack_refuse_reason(Path("Ruleta_v10.2.0.684_build40097")) is not None
    assert "ERROR 30" in (pack_refuse_reason(Path("Ruleta_v10.2.0.684_build40097")) or "")
    assert pack_refuse_reason(Path("Ruleta_v10.2.0.0_build40097")) is not None
    assert pack_refuse_reason(Path("Ruleta_v10.2.0.827_build40114")) is None
    assert pack_refuse_reason(Path("Ruleta_v10.1.8.0_build40114")) is None


def test_freeze_reapplies_stomped_setup(tmp_path: Path) -> None:
    from config_scanner.cabinet_profile import (
        extract_hw_leaves_from_setup,
        freeze_cabinet_profile,
        reapply_frozen_cabinet_files,
    )

    dest = tmp_path / "goldclub"
    setup = dest / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    setup.parent.mkdir(parents=True)
    setup.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="hardware settings">\n'
        '    <node name="additional">\n'
        '      <node name="wheel">double zero with serie</node>\n'
        "    </node>\n"
        "  </node>\n"
        '  <node name="game settings">\n'
        '    <node name="testmode"><node name="forcerng">1</node></node>\n'
        '    <node name="additional">\n'
        '      <node name="number of player stations">1</node>\n'
        '      <node name="ballread"><node name="sensor type">2</node></node>\n'
        "    </node>\n"
        "  </node>\n"
        "</config>\n",
        encoding="utf-8",
    )
    leaves = extract_hw_leaves_from_setup(setup)
    assert leaves["wheel"] == "double zero with serie"
    assert leaves["sensorType"] == "2"
    assert leaves["forceRng"] == "1"
    assert leaves["playerStations"] == "1"

    tool = tmp_path / "tool"
    tool.mkdir()
    freeze_cabinet_profile(dest, tool_root=tool, serial="GRT330106")
    setup.write_text("<foreign-setup/>", encoding="utf-8")
    notes = reapply_frozen_cabinet_files(dest, tool_root=tool, serial="GRT330106")
    assert any("reapplied" in note for note in notes)
    assert "double zero with serie" in setup.read_text(encoding="utf-8")


def _binaries_only_dest(tmp_path: Path) -> tuple[Path, Path, Path]:
    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    cfg.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<live-setup/>", encoding="utf-8")
    (dest / "ruleta").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    return dest, cfg, tool_root


def test_apply_snapshot_binaries_only_keeps_setup(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    dest, cfg, tool_root = _binaries_only_dest(tmp_path)
    _write_minimal_snapshot(
        tool_root,
        dest,
        product_version="10.2.0.827",
        files={
            "config/etc/application/ruleta/setup.xml": "<snap-setup/>",
            "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json": (
                '{"name": "paytable_elite_double_zero"}'
            ),
        },
    )
    pack = tmp_path / "Ruleta_v10.2.0.827_build40114"
    pack.mkdir()
    (pack / "setup.xml").write_text("<foreign-setup/>", encoding="utf-8")
    calls: list[Path] = []

    def fake_swap(host, source, dest_unc=None, **kwargs):
        calls.append(Path(source))
        dest_ruleta = Path(dest_unc) if dest_unc else dest / "ruleta"
        dest_ruleta.mkdir(parents=True, exist_ok=True)
        (dest_ruleta / "Ruleta.exe").write_bytes(b"MZ-10.2")
        # Simulate a sloppy pack that also overwrote setup.
        cfg.joinpath("setup.xml").write_text("<foreign-setup/>", encoding="utf-8")
        return SimpleNamespace(ok=True, log="ok")

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr("network.software_version_swap.run_swap", fake_swap)
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "r_snap", str(dest), write_scope="binaries_only"
    )
    assert result.write_scope == "binaries_only"
    assert calls == [pack]
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<live-setup/>"
    assert not (cfg / "paytables" / "paytable_elite_double_zero.json").is_file() or (
        cfg / "paytables" / "paytable_elite_double_zero.json"
    ).read_text(encoding="utf-8") == '{"name": "paytable_elite_double_zero"}'
    assert any("Pushed Ruleta binaries" in note for note in result.notes)
    assert any("reapplied cabinet profile" in note for note in result.notes)


def test_apply_snapshot_binaries_only_refuses_684(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    dest, cfg, tool_root = _binaries_only_dest(tmp_path)
    _write_minimal_snapshot(
        tool_root,
        dest,
        product_version="10.2.0.684",
        files={"config/etc/application/ruleta/setup.xml": "<snap-setup/>"},
    )
    pack = tmp_path / "Ruleta_v10.2.0.684_build40097"
    pack.mkdir()
    called = []

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr(
        "network.software_version_swap.run_swap",
        lambda *args, **kwargs: called.append(True) or SimpleNamespace(ok=True, log="ok"),
    )

    service = ConfigScannerService(tool_root)
    with pytest.raises(SoftwarePushError, match="ERROR 30"):
        service.apply_snapshot_to_target(
            "r_snap", str(dest), write_scope="binaries_only"
        )
    assert called == []
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<live-setup/>"


def test_apply_snapshot_binaries_only_refuses_10_2_0_0(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    dest, cfg, tool_root = _binaries_only_dest(tmp_path)
    _write_minimal_snapshot(
        tool_root,
        dest,
        product_version="10.2.0.0",
        files={"config/etc/application/ruleta/setup.xml": "<snap-setup/>"},
    )
    pack = tmp_path / "Ruleta_v10.2.0.0_build40097"
    pack.mkdir()
    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    service = ConfigScannerService(tool_root)
    with pytest.raises(SoftwarePushError, match="silent ProcessExit"):
        service.apply_snapshot_to_target(
            "r_snap", str(dest), write_scope="binaries_only"
        )
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<live-setup/>"


def test_apply_snapshot_binaries_only_holds_10_1_on_10_2_sas(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.ruleta_compat import HOLD_RELATIVE
    from config_scanner.service import ConfigScannerService

    dest, cfg, tool_root = _binaries_only_dest(tmp_path)
    sas = dest / "ruleta" / "var" / "SASControler1"
    sas.mkdir(parents=True)
    header = bytes(range(16))
    (sas / "DeviceManagerData.xml_1").write_bytes(header + _XML.encode("utf-8"))
    _write_minimal_snapshot(
        tool_root,
        dest,
        product_version="10.1.8.0",
        files={"config/etc/application/ruleta/setup.xml": "<snap-setup/>"},
    )
    pack = tmp_path / "Ruleta_v10.1.8.0_build40114"
    pack.mkdir()

    def fake_swap(host, source, dest_unc=None, **kwargs):
        dest_ruleta = Path(dest_unc) if dest_unc else dest / "ruleta"
        dest_ruleta.mkdir(parents=True, exist_ok=True)
        (dest_ruleta / "Ruleta.exe").write_bytes(b"MZ-10.1")
        return SimpleNamespace(ok=True, log="ok")

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr("network.software_version_swap.run_swap", fake_swap)
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.1",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "r_snap", str(dest), write_scope="binaries_only"
    )
    assert (cfg / "setup.xml").read_text(encoding="utf-8") == "<live-setup/>"
    hold = dest.joinpath(*HOLD_RELATIVE.split("/"))
    assert hold.is_file()
    assert any("hold" in note.lower() or "bad conversion" in note for note in result.notes)


def test_snapshot_apply_refuses_serial_mismatch(tmp_path: Path) -> None:
    from config_scanner.service import ConfigScannerService

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps({"gameDrive": None, "profileId": "roulette_usb"}),
        encoding="utf-8",
    )
    dest = tmp_path / "goldclub"
    (dest / "ruleta").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    serial = dest / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    serial.parent.mkdir(parents=True)
    serial.write_text('{"MachineName":"GRT0157"}', encoding="utf-8")

    snap_dir = tool_root / "snapshots" / "bad_serial"
    snap_dir.mkdir(parents=True)
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "machine_serial": "GRT330106",
                "game_drive": r"\\10.0.0.111\slot",
                "exe_product_version": "10.2.0.876",
                "product_version": "10.2.0.876",
                "build_number": "40119",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"files": []}),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    refuses = service.snapshot_apply_refuses(
        "bad_serial", str(dest), write_scope="full_software"
    )
    assert any("serial mismatch" in item.casefold() for item in refuses)
    assert any("10.0.0.111" in item for item in refuses)


def test_snapshot_apply_refuses_defers_local_dll_lock(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.service import ConfigScannerService

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps({"gameDrive": None, "profileId": "roulette_usb"}),
        encoding="utf-8",
    )
    dest = tmp_path / "goldclub"
    (dest / "ruleta" / "lib").mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "lib" / "RouletteWebApiModels.dll").write_bytes(b"MZ")
    snap_dir = tool_root / "snapshots" / "local_lock"
    snap_dir.mkdir(parents=True)
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "machine_serial": "GRT330106",
                "game_drive": r"C:\Goldclub",
                "exe_product_version": "10.1.8.0",
                "product_version": "10.1.8.0",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"files": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "network.ruleta_stack_probe.dest_ruleta_file_locked",
        lambda _dest: True,
    )
    service = ConfigScannerService(tool_root)
    blocked = service.snapshot_apply_refuses(
        "local_lock", str(dest), write_scope="full_software"
    )
    assert any("locked" in item.casefold() for item in blocked)
    deferred = service.snapshot_apply_refuses(
        "local_lock",
        str(dest),
        write_scope="full_software",
        defer_lock_check=True,
    )
    assert not any("locked" in item.casefold() for item in deferred)


def test_run_swap_refuses_read_only_local_dest(monkeypatch, tmp_path: Path) -> None:
    from network.software_version_swap import SOFTWARE_VERSION_FILES, run_swap

    src = tmp_path / "pack"
    src.mkdir()
    for _layer, rel in SOFTWARE_VERSION_FILES:
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ" if rel.lower().endswith(".exe") else b"x")

    dest = tmp_path / "goldclub" / "ruleta"
    dest.mkdir(parents=True)

    monkeypatch.setattr(
        "config_scanner.dest_preflight.goldclub_dest_writable",
        lambda _root: (False, "Scan target is read-only."),
    )
    monkeypatch.setattr(
        "network.software_version_swap.is_local_cabinet_host",
        lambda: True,
    )

    result = run_swap("local", src, dest_unc=dest, skip_kill=True)
    assert not result.ok
    assert "read-only" in result.log.casefold()


def test_seamless_restore_cli_no_llave_and_clock() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_seamless_restore.py"
    ).read_text(encoding="utf-8")
    assert '"--no-llave"' in src
    assert "snapshot_should_auto_enter_llave" in src
    assert "auto_llave" in src
    assert '"sync_password": auto_llave' in src
    assert "ensure_llave=auto_llave" in src
    assert '"--clock"' in src
    assert 'prep_kw["clock"]' in src
    assert "snapshot_has_bound_activate" in src
    assert "Keep snapshot LLAVE bind" in src

