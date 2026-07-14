from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from config_scanner.build_version import (
    BuildInfo,
    baseline_folder_name,
    discover_game_repo,
    discover_scan_target,
    normalize_game_drive,
    normalize_scan_target,
    parse_build_version_file,
    profile_scan_candidates,
    scan_target_is_valid,
    resolve_scan_for_target,
    snapshot_folder_name,
)
from config_scanner.paths import _migrate_legacy_nested_layout
from config_scanner.profiles import get_profile, load_profiles
from config_scanner.scanner import (
    FileEntry,
    Manifest,
    archive_manifest_files,
    build_info_to_dict,
    collect_scan_files,
    load_manifest,
    restore_manifest_files,
    snapshot_content_root,
)
from config_scanner.service import ConfigScannerService, allocate_snapshot_dir, scan_scope_zero_diff_hint
from config_scanner.xml_diff import compare_keyed_maps, compare_manifests, file_content_diff

BUILD_VERSION_TEXT = """Source Version: 31099
Branch        : $/Certified/Ruleta/GC/10.1.0.0
Build Number  : 37556
Trigger       : Continuous Integration (on commit)
Requested By  : Luka Gustin
Date          : 2025-11-19 12:13:19
"""


def test_normalize_game_drive() -> None:
    assert normalize_game_drive("D:") == "D:\\"
    assert normalize_game_drive("D:\\.") == "D:\\"
    assert normalize_game_drive("D:\\") == "D:\\"


def test_normalize_scan_target_unc() -> None:
    unc = r"\\10.0.0.90\c$\Goldclub\slot"
    assert normalize_scan_target(unc) == unc
    assert normalize_scan_target(unc + "\\") == unc


def test_slot_profile_definition() -> None:
    profile = get_profile("slot_lab_90")
    assert profile.default_target == r"C:\Goldclub\slot"
    assert "G:" in profile.discover_targets
    assert any(root.path == "themes" and not root.recursive for root in profile.scan_roots)


def test_unified_scan_candidates_local_before_unc() -> None:
    from config_scanner.build_version import unified_scan_candidates

    profiles = load_profiles()
    ordered = unified_scan_candidates(profiles, preferred=r"\\10.0.0.90\c$\Goldclub\slot")
    unc_index = next(
        i for i, path in enumerate(ordered) if path.startswith("\\\\10.0.0.90")
    )
    local_index = next(
        i for i, path in enumerate(ordered) if "goldclub\\slot" in path.casefold() and not path.startswith("\\\\")
    )
    assert local_index < unc_index
    assert ordered.index("G:\\") < unc_index


def test_scan_target_is_valid_slot_fingerprint(tmp_path: Path) -> None:
    profile = get_profile("slot_lab_90")
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    for name in ("OneHand.exe", "game-start.exe", "GoldClub.Settings.dll"):
        (slot_root / name).write_bytes(b"data")
    assert scan_target_is_valid(profile, str(slot_root)) is True
    assert scan_target_is_valid(profile, str(tmp_path / "missing")) is False


def test_discover_scan_target_slot_falls_back_from_bad_drive() -> None:
    profile = get_profile("slot_lab_90")
    target = discover_scan_target(profile, preferred="D:")
    assert "Goldclub" in target
    assert target.casefold().endswith("slot") or target.endswith("slot")


def test_profile_scan_candidates_prefers_hint() -> None:
    profile = get_profile("slot_lab_90")
    candidates = profile_scan_candidates(profile, preferred="Z:")
    assert candidates[0] == "Z:\\"
    assert any("Goldclub\\slot" in item for item in candidates)


def test_discover_game_repo_slot_on_drive_root(tmp_path: Path) -> None:
    slot_root = tmp_path / "usb_d"
    slot_root.mkdir()
    for name in ("OneHand.exe", "game-start.exe", "GoldClub.Settings.dll"):
        (slot_root / name).write_bytes(b"slot-data")
    result = discover_game_repo(load_profiles(), preferred=str(slot_root))
    assert result.profile_id == "slot_lab_90"
    assert Path(result.target) == slot_root


def test_discover_game_repo_roulette_on_drive_root(tmp_path: Path) -> None:
    roulette_root = tmp_path / "usb_d"
    (roulette_root / "ruleta").mkdir(parents=True)
    (roulette_root / "ruleta" / "BuildVersion.txt").write_text(BUILD_VERSION_TEXT, encoding="utf-8")
    (roulette_root / "config").mkdir()
    result = discover_game_repo(load_profiles(), preferred=str(roulette_root))
    assert result.profile_id == "roulette_usb"
    assert Path(result.target) == roulette_root


def test_ramclear_script_marks_roulette_usb_without_build_version(tmp_path: Path) -> None:
    from config_scanner.build_version import (
        has_ramclear_script,
        resolve_build_info,
    )
    from config_scanner.scanner import effective_scan_roots

    roulette_root = tmp_path / "usb_ramclear"
    (roulette_root / "config").mkdir(parents=True)
    ramclear_dir = roulette_root / "maintenance" / "tasks"
    ramclear_dir.mkdir(parents=True)
    (ramclear_dir / "ramclear.ps1").write_text("# ramclear", encoding="utf-8")

    assert has_ramclear_script(roulette_root) is True

    profiles = load_profiles()
    result = resolve_scan_for_target(str(roulette_root), profiles)
    assert result.profile_id == "roulette_usb"

    roulette_profile = get_profile("roulette_usb")
    info = resolve_build_info(roulette_profile, str(roulette_root))
    assert info.build_number == "ramclear"
    assert info.trigger == "ramclear_script"

    roots = effective_scan_roots(roulette_root, roulette_profile)
    root_paths = {spec.path.replace("\\", "/") for spec in roots}
    assert "config" in root_paths
    assert "maintenance/config/ramclear" in root_paths


def test_collect_scan_files_non_recursive_themes(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    (slot_root / "themes" / "nested").mkdir(parents=True)
    (slot_root / "themes" / "mgconfig.xml").write_text("<root/>", encoding="utf-8")
    (slot_root / "themes" / "nested" / "skip.xml").write_text("<root/>", encoding="utf-8")
    (slot_root / "HWSubsys.xml").write_text("<root/>", encoding="utf-8")
    profile = get_profile("slot_lab_90")
    files = collect_scan_files(slot_root, profile.scan_roots, profile.include_patterns)
    rels = {path.relative_to(slot_root).as_posix() for path in files}
    assert "themes/mgconfig.xml" in rels
    assert "HWSubsys.xml" in rels
    assert "themes/nested/skip.xml" not in rels


def test_snapshot_folder_name_slot_prefix() -> None:
    name = snapshot_folder_name(
        "AB12CD34",
        datetime(2026, 7, 13, 10, 45, 46),
        profile_id="slot_lab_90",
    )
    assert name == "2026-07-13_slotAB12CD34_104546_000"


def test_parse_build_version_file(tmp_path: Path) -> None:
    path = tmp_path / "BuildVersion.txt"
    path.write_text(BUILD_VERSION_TEXT, encoding="utf-8")
    info = parse_build_version_file(
        path,
        game_drive="D:",
        scan_timestamp=datetime(2026, 7, 13, 10, 45, 46),
    )
    assert info.build_number == "37556"
    assert info.product_version == "10.1.0.0"
    assert info.branch == "$/Certified/Ruleta/GC/10.1.0.0"
    assert info.game_drive == "D:\\"


def test_snapshot_folder_name() -> None:
    name = snapshot_folder_name("37556", datetime(2026, 7, 13, 10, 45, 46))
    assert name == "2026-07-13_build37556_104546_000"


def test_compare_manifests_counts() -> None:
    baseline = Manifest(
        scanned_at="t0",
        file_count=2,
        elapsed_seconds=1.0,
        files=[
            FileEntry("config/a.xml", "AAA", 10, "t"),
            FileEntry("config/b.xml", "BBB", 10, "t"),
        ],
    )
    target = Manifest(
        scanned_at="t1",
        file_count=3,
        elapsed_seconds=1.0,
        files=[
            FileEntry("config/a.xml", "AAA", 10, "t"),
            FileEntry("config/b.xml", "CCC", 10, "t"),
            FileEntry("config/c.xml", "DDD", 10, "t"),
        ],
    )
    diffs = compare_manifests(
        baseline,
        target,
        baseline_game_drive="D:\\",
        target_game_drive="D:\\",
    )
    by_path = {item.relative_path: item.status for item in diffs}
    assert by_path["config/a.xml"] == "unchanged"
    assert by_path["config/b.xml"] == "modified"
    assert by_path["config/c.xml"] == "added"


def test_texts_xml_keyed_diff() -> None:
    baseline = {"IDS_STRING1": "HELLO", "IDS_STRING2": "WORLD"}
    target = {"IDS_STRING1": "HELLO", "IDS_STRING2": "EARTH"}
    changes = compare_keyed_maps(baseline, target)
    assert len(changes) == 1
    assert changes[0].path == "IDS_STRING2"
    assert changes[0].change_type == "modified"


def test_file_content_diff_texts_xml(tmp_path: Path) -> None:
    baseline_xml = """<?xml version="1.0"?>
<root>
  <string><id>IDS_A</id><text>Hello</text></string>
  <string><id>IDS_B</id><text>World</text></string>
</root>"""
    target_xml = """<?xml version="1.0"?>
<root>
  <string><id>IDS_A</id><text>Hello</text></string>
  <string><id>IDS_B</id><text>Earth</text></string>
</root>"""
    baseline_path = tmp_path / "texts.xml"
    target_path = tmp_path / "texts_target.xml"
    baseline_path.write_text(baseline_xml, encoding="utf-8")
    target_path.write_text(target_xml, encoding="utf-8")
    changes = file_content_diff("config/texts.xml", baseline_path, target_path)
    assert len(changes) == 1
    assert changes[0].path == "IDS_B"
    assert changes[0].change_type == "modified"


def test_build_info_to_dict_uses_camel_case() -> None:
    from config_scanner.build_version import BuildInfo

    info = BuildInfo(
        source_version="31099",
        branch="$/Certified/Ruleta/GC/10.1.0.0",
        product_version="10.1.0.0",
        build_number="37556",
        build_date="2025-11-19 12:13:19",
        trigger="CI",
        requested_by="tester",
        scan_timestamp="2026-07-13T10:00:00",
        game_drive="D:\\",
    )
    payload = build_info_to_dict(info)
    assert payload["buildNumber"] == "37556"
    assert payload["scanTimestamp"] == "2026-07-13T10:00:00"
    assert payload["gameDrive"] == "D:\\"
    assert "build_number" not in payload


def test_scan_scope_zero_diff_hint_slot_vs_roulette() -> None:
    slot_hint = scan_scope_zero_diff_hint("slot_lab_90", "Slot lab")
    roulette_hint = scan_scope_zero_diff_hint("roulette_usb", "Roulette USB")
    assert "top-level slot XML/INI" in slot_hint
    assert "roulette config" in roulette_hint
    assert "top-level slot XML/INI" not in roulette_hint


def test_list_snapshots_includes_rich_version_fields(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_root.mkdir(parents=True)
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = snap_root / "roulette_scan"
    snap_dir.mkdir()
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "37556",
                "productVersion": "10.1.0.0",
                "sourceVersion": "31099",
                "exeProductVersion": "10.1.0.0",
                "exeProductName": "Ruleta Module",
                "scanTimestamp": "2026-07-13T10:00:00",
                "gameDrive": "D:\\",
                "profileId": "roulette_usb",
                "profileLabel": "Roulette USB",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"fileCount": 1, "elapsedSeconds": 0.1, "files": []}),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    rows = service.list_snapshots()
    assert len(rows) == 1
    row = rows[0]
    assert row.source_version == "31099"
    assert row.exe_product_version == "10.1.0.0"
    assert row.exe_product_name == "Ruleta Module"
    assert row.profile_id == "roulette_usb"


def test_service_lists_existing_snapshots() -> None:
    service = ConfigScannerService()
    snapshots = service.list_snapshots()
    assert len(snapshots) >= 1
    assert snapshots[0].name


def test_service_baseline_roundtrip(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    (tool_root / "snapshots").mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = tool_root / "snapshots" / "2026-07-13_build37556_100000"
    snap_dir.mkdir()
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "37556",
                "scanTimestamp": "2026-07-13T10:00:00",
                "gameDrive": "D:\\",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"fileCount": 0, "elapsedSeconds": 0, "files": []}),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    service.set_baseline_name("2026-07-13_build37556_100000")
    assert service.get_baseline_name() == "2026-07-13_build37556_100000"
    rows = service.list_snapshots()
    assert rows[0].is_baseline is True


def test_baseline_folder_name_slot() -> None:
    info = BuildInfo(
        source_version=None,
        branch=None,
        product_version=None,
        build_number="8A9B9BE9",
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp="2026-07-13T13:55:27",
        game_drive=r"\\10.0.0.90\c$\Goldclub\slot",
        profile_id="slot_lab_90",
    )
    assert baseline_folder_name(info) == "slot_baseline"


def test_baseline_folder_name_roulette() -> None:
    info = BuildInfo(
        source_version="31099",
        branch="$/Certified/Ruleta/GC/10.1.0.0",
        product_version="10.1.0.0",
        build_number="37556",
        build_date="2025-11-19 12:13:19",
        trigger="CI",
        requested_by="test",
        scan_timestamp="2026-07-13T10:00:00",
        game_drive="D:\\",
        profile_id="roulette_usb",
    )
    assert baseline_folder_name(info) == "roulette_baseline_build37556_10_1_0_0"


def test_migrate_legacy_nested_usb_layout(tmp_path: Path) -> None:
    exe_dir = tmp_path / "usb"
    exe_dir.mkdir()
    legacy_snap = exe_dir / "config-scanner" / "snapshots" / "2026-07-13_build37556_100000"
    legacy_snap.mkdir(parents=True)
    (legacy_snap / "manifest.json").write_text("{}", encoding="utf-8")
    (exe_dir / "config-scanner" / "baseline.json").write_text(
        '{"snapshotName":"2026-07-13_build37556_100000"}', encoding="utf-8"
    )
    _migrate_legacy_nested_layout(exe_dir)
    assert (exe_dir / "snapshots" / "2026-07-13_build37556_100000").is_dir()
    assert (exe_dir / "baseline.json").is_file()
    assert not (exe_dir / "config-scanner" / "snapshots").exists()


def test_set_baseline_snapshot_renames_folder(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_root.mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = snap_root / "2026-07-13_slot8A9B9BE9_135527"
    snap_dir.mkdir()
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "8A9B9BE9",
                "scanTimestamp": "2026-07-13T13:55:27",
                "gameDrive": r"\\10.0.0.90\c$\Goldclub\slot",
                "profileId": "slot_lab_90",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"fileCount": 1, "elapsedSeconds": 0.1, "files": []}),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root, profile_id="slot_lab_90")
    new_name = service.set_baseline_snapshot("2026-07-13_slot8A9B9BE9_135527")
    assert new_name == "slot_baseline"
    assert service.get_baseline_name() == "slot_baseline"
    assert (snap_root / "slot_baseline").is_dir()
    assert not (snap_root / "2026-07-13_slot8A9B9BE9_135527").exists()
    rows = service.list_snapshots()
    assert len(rows) == 1
    assert rows[0].name == "slot_baseline"
    assert rows[0].is_baseline is True


def test_baseline_backfills_version_from_sibling_snapshot(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_root.mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )

    def write_snap(name: str, *, sparse: bool) -> None:
        folder = snap_root / name
        folder.mkdir()
        payload = {
            "buildNumber": "8A9B9BE9",
            "scanTimestamp": "2026-07-13T13:55:27" if sparse else "2026-07-13T15:35:37",
            "gameDrive": r"C:\Goldclub\slot",
            "profileId": "slot_lab_90",
            "profileLabel": "Slot (lab 10.0.0.90)",
        }
        if not sparse:
            payload.update(
                {
                    "productVersion": "v2.0.1-rc2",
                    "sourceVersion": "2.0.1+RC2",
                    "exeProductVersion": "2.0.1+RC2",
                    "exeProductName": "Gamestar",
                }
            )
        (folder / "build-info.json").write_text(json.dumps(payload), encoding="utf-8")
        (folder / "manifest.json").write_text(
            json.dumps({"fileCount": 41, "elapsedSeconds": 0.1, "files": []}),
            encoding="utf-8",
        )

    write_snap("slot_baseline", sparse=True)
    write_snap("2026-07-13_slot8A9B9BE9_153537_401", sparse=False)

    service = ConfigScannerService(tool_root, profile_id="slot_lab_90")
    rows = service.list_snapshots()
    baseline = next(row for row in rows if row.name == "slot_baseline")
    assert baseline.product_version == "v2.0.1-rc2"
    assert baseline.source_version == "2.0.1+RC2"
    assert baseline.exe_product_version == "2.0.1+RC2"
    assert baseline.exe_product_name == "Gamestar"


def test_set_baseline_enriches_version_from_game_drive(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"\x00" * 100 + "2.0.1+RC2".encode("utf-16le"))
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")

    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_dir = snap_root / "2026-07-13_slot8A9B9BE9_135527"
    snap_dir.mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "8A9B9BE9",
                "scanTimestamp": "2026-07-13T13:55:27",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"fileCount": 1, "elapsedSeconds": 0.1, "files": []}),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root, profile_id="slot_lab_90")
    new_name = service.set_baseline_snapshot("2026-07-13_slot8A9B9BE9_135527")
    assert new_name == "slot_baseline"
    rows = service.list_snapshots()
    assert rows[0].product_version == "v2.0.1-rc2"
    assert rows[0].source_version == "2.0.1+RC2"
    assert rows[0].exe_product_version == "2.0.1+RC2"


def test_allocate_snapshot_dir_avoids_collision(tmp_path: Path) -> None:
    snap_root = tmp_path / "snapshots"
    snap_root.mkdir()
    base = "2026-07-13_slotAB12CD34_104546_000"
    (snap_root / base).mkdir()
    name, path = allocate_snapshot_dir(snap_root, base)
    assert name == f"{base}_01"
    assert path == snap_root / f"{base}_01"
    assert not path.exists()


def test_list_snapshots_marks_baseline_folder_without_registry(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_root.mkdir(parents=True)
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = snap_root / "slot_baseline"
    snap_dir.mkdir()
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "8A9B9BE9",
                "scanTimestamp": "2026-07-13T13:55:27",
                "gameDrive": r"\\10.0.0.90\c$\Goldclub\slot",
                "profileId": "slot_lab_90",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps({"fileCount": 1, "elapsedSeconds": 0.1, "files": []}),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    rows = service.list_snapshots()
    assert len(rows) == 1
    assert rows[0].name == "slot_baseline"
    assert rows[0].is_baseline is True


def test_compare_warnings_cross_profile(tmp_path: Path) -> None:
    tool_root = tmp_path / "config-scanner"
    snap_root = tool_root / "snapshots"
    snap_root.mkdir(parents=True)
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": None,
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )

    def write_snapshot(name: str, profile_id: str, profile_label: str, rel_path: str) -> None:
        snap_dir = snap_root / name
        snap_dir.mkdir()
        (snap_dir / "build-info.json").write_text(
            json.dumps(
                {
                    "buildNumber": "1",
                    "scanTimestamp": "2026-07-13T10:00:00",
                    "gameDrive": "D:\\",
                    "profileId": profile_id,
                    "profileLabel": profile_label,
                }
            ),
            encoding="utf-8",
        )
        (snap_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "fileCount": 1,
                    "elapsedSeconds": 0.1,
                    "files": [
                        {
                            "relativePath": rel_path,
                            "sha1": "AAA",
                            "sizeBytes": 1,
                            "lastWriteUtc": "t",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_snapshot("slot_baseline", "slot_lab_90", "Slot lab", "slot/mgconfig.xml")
    write_snapshot("roulette_scan", "roulette_usb", "Roulette USB", "config/setup.xml")
    service = ConfigScannerService(tool_root)
    warnings = service.compare_warnings("slot_baseline", "roulette_scan")
    assert warnings
    assert "Different scan profiles" in warnings[0]
    assert any("No overlapping scanned files" in item for item in warnings)


def test_load_manifest_accepts_camel_case(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scannedAt": "t",
                "fileCount": 1,
                "elapsedSeconds": 0.5,
                "files": [
                    {
                        "relativePath": "config/setup.xml",
                        "sha1": "ABC",
                        "sizeBytes": 12,
                        "lastWriteUtc": "t",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_obj = load_manifest(snap_dir)
    assert manifest_obj.files[0].relative_path == "config/setup.xml"


def test_slot_version_sniff_from_onehand_bytes(tmp_path: Path) -> None:
    from config_scanner.build_version import _extract_version_from_onehand_exe

    exe = tmp_path / "OneHand.exe"
    exe.write_bytes(b"\x00" * 100 + "2.0.1+RC2".encode("utf-16le"))
    info = _extract_version_from_onehand_exe(exe)
    assert info.display_version == "v2.0.1-rc2"
    assert info.product_version == "2.0.1+RC2"


def test_roulette_exe_version_sniff(tmp_path: Path) -> None:
    ruleta_dir = tmp_path / "ruleta"
    ruleta_dir.mkdir()
    exe = ruleta_dir / "Ruleta.exe"
    exe.write_bytes(b"\x00" * 100 + "10.1.0.0".encode("utf-16le"))
    from config_scanner.build_version import detect_roulette_exe_version

    info = detect_roulette_exe_version(tmp_path)
    assert info.display_version == "10.1.0.0"
    assert info.product_version == "10.1.0.0"


def test_parse_build_version_enriches_from_ruleta_exe(tmp_path: Path) -> None:
    ruleta_dir = tmp_path / "ruleta"
    ruleta_dir.mkdir()
    (ruleta_dir / "BuildVersion.txt").write_text(
        "Source Version: 31099\n"
        "Branch        : $/Certified/Ruleta/GC/10.1.0.0\n"
        "Build Number  : 37556\n",
        encoding="utf-8-sig",
    )
    (ruleta_dir / "Ruleta.exe").write_bytes(b"\x00" * 100 + "10.1.0.0".encode("utf-16le"))
    info = parse_build_version_file(
        ruleta_dir / "BuildVersion.txt",
        game_drive=str(tmp_path),
    )
    assert info.product_version == "10.1.0.0"
    assert info.exe_product_version == "10.1.0.0"
    assert info.source_version == "31099"


def test_format_build_info_log_line_roulette() -> None:
    from config_scanner.build_version import format_build_info_log_line

    info = BuildInfo(
        source_version="31099",
        branch="$/Certified/Ruleta/GC/10.1.0.0",
        product_version="10.1.0.0",
        build_number="37556",
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp="2026-07-13T10:00:00",
        game_drive="D:\\",
        exe_product_version="10.1.0.0",
        exe_file_version="10.1.0.0",
        exe_product_name="Ruleta Module",
    )
    line = format_build_info_log_line(info)
    assert "build=37556" in line
    assert "productVersion=10.1.0.0" in line
    assert "Ruleta Module=10.1.0.0" in line


def test_slot_fingerprint_populates_source_version(tmp_path: Path) -> None:
    from config_scanner.build_version import resolve_build_info

    profile = get_profile("slot_lab_90")
    for name in ["OneHand.exe", "game-start.exe", "GoldClub.Settings.dll"]:
        path = tmp_path / name
        if name == "OneHand.exe":
            path.write_bytes(b"\x00" * 100 + "2.0.1+RC2".encode("utf-16le"))
        else:
            path.write_bytes(b"stub")

    info = resolve_build_info(profile, str(tmp_path))
    assert info.source_version == "2.0.1+RC2"
    assert info.product_version == "v2.0.1-rc2"
    assert info.exe_product_version == "2.0.1+RC2"
    assert info.build_number
    assert len(info.build_number) == 8


def test_resolve_scan_for_target_explicit_path_only(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")

    roulette_root = tmp_path / "roulette"
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir(parents=True)
    (ruleta / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.0.0\nBuild Number: 1\n",
        encoding="utf-8",
    )
    config = roulette_root / "config"
    config.mkdir()
    (config / "setup.xml").write_text("<root/>", encoding="utf-8")

    profiles = load_profiles()
    slot_result = resolve_scan_for_target(str(slot_root), profiles)
    assert slot_result.profile_id == "slot_lab_90"

    roulette_result = resolve_scan_for_target(str(roulette_root), profiles)
    assert roulette_result.profile_id == "roulette_usb"

    with pytest.raises(FileNotFoundError, match="No slot or roulette repo"):
        resolve_scan_for_target(str(tmp_path / "empty"), profiles)


def test_slot_cabinet_path_ignores_ruleta_build_version(tmp_path: Path) -> None:
    """Slot cabinets must not use ruleta/BuildVersion.txt even if a ruleta folder exists."""
    from config_scanner.build_version import is_slot_cabinet_scan_target, resolve_build_info

    slot_root = tmp_path / "Goldclub" / "slot"
    slot_root.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    ruleta = slot_root / "ruleta"
    ruleta.mkdir()
    # No BuildVersion.txt — roulette marker must not be required.

    profiles = load_profiles()
    assert is_slot_cabinet_scan_target(str(slot_root)) is True
    result = resolve_scan_for_target(str(slot_root), profiles)
    assert result.profile_id == "slot_lab_90"

    roulette_profile = get_profile("roulette_usb")
    with pytest.raises(FileNotFoundError, match="roulette USB images only"):
        resolve_build_info(roulette_profile, str(slot_root))

    empty_slot = tmp_path / "cabinet" / "slot"
    empty_slot.mkdir(parents=True)
    assert is_slot_cabinet_scan_target(str(empty_slot)) is True
    with pytest.raises(FileNotFoundError, match="No slot repo"):
        resolve_scan_for_target(str(empty_slot), profiles)


def test_prepare_for_target_honors_explicit_drive(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")

    roulette_root = tmp_path / "roulette"
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir(parents=True)
    (ruleta / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.0.0\nBuild Number: 1\n",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
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

    service = ConfigScannerService(tool_root)
    resolved = service.prepare_for_target(str(roulette_root))
    assert resolved.replace("\\", "/").rstrip("/") == str(roulette_root).replace("\\", "/")
    assert service.profile.id == "roulette_usb"


def test_delete_snapshot_removes_folder_and_clears_baseline(tmp_path: Path) -> None:
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    snap_root = tool_root / "snapshots"
    snap_root.mkdir()
    target = snap_root / "scan_a"
    baseline = snap_root / "slot_baseline"
    for folder in (target, baseline):
        folder.mkdir()
        (folder / "build-info.json").write_text("{}", encoding="utf-8")
        (folder / "manifest.json").write_text(
            json.dumps({"fileCount": 1, "elapsedSeconds": 0.1, "files": []}),
            encoding="utf-8",
        )

    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": "D:",
                "scanRoots": ["config"],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    (tool_root / "reports").mkdir()
    (tool_root / "templates").mkdir()
    (tool_root / "baseline.json").write_text(
        json.dumps({"snapshotName": "slot_baseline"}),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    assert len(service.list_snapshots()) == 2

    service.delete_snapshot("scan_a")
    assert not target.exists()
    assert baseline.exists()
    assert service.get_baseline_name() == "slot_baseline"

    service.delete_snapshot("slot_baseline")
    assert not baseline.exists()
    assert service.get_baseline_name() is None
    assert service.list_snapshots() == []


def test_compare_uses_archived_snapshot_files(tmp_path: Path) -> None:
    baseline_snap = tmp_path / "baseline"
    target_snap = tmp_path / "target"
    for snap in (baseline_snap, target_snap):
        (snap / "files" / "config").mkdir(parents=True)

    (baseline_snap / "files" / "config" / "texts.xml").write_text(
        """<?xml version="1.0"?>
<root><string><id>IDS_A</id><text>Hello</text></string></root>""",
        encoding="utf-8",
    )
    (target_snap / "files" / "config" / "texts.xml").write_text(
        """<?xml version="1.0"?>
<root><string><id>IDS_A</id><text>World</text></string></root>""",
        encoding="utf-8",
    )

    baseline = Manifest(
        scanned_at="t0",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry("config/texts.xml", "AAA", 10, "t")],
    )
    target = Manifest(
        scanned_at="t1",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry("config/texts.xml", "BBB", 10, "t")],
    )
    diffs = compare_manifests(
        baseline,
        target,
        baseline_content_root=snapshot_content_root(baseline_snap),
        target_content_root=snapshot_content_root(target_snap),
        baseline_game_drive=str(tmp_path / "missing_baseline"),
        target_game_drive=str(tmp_path / "missing_target"),
    )
    modified = next(item for item in diffs if item.relative_path == "config/texts.xml")
    assert modified.status == "modified"
    assert len(modified.content_diff) == 1
    assert modified.content_diff[0].path == "IDS_A"


def test_archive_manifest_files_copies_tree(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    config = game_root / "config"
    config.mkdir(parents=True)
    xml = config / "setup.xml"
    xml.write_text("<root/>", encoding="utf-8")

    manifest = Manifest(
        scanned_at="t",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry("config/setup.xml", "ABC", 8, "t")],
    )
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    archive_manifest_files(str(game_root), manifest, snap_dir)
    archived = snap_dir / "files" / "config" / "setup.xml"
    assert archived.is_file()
    assert archived.read_text(encoding="utf-8") == "<root/>"


def test_restore_manifest_files_overwrites_live(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    config = game_root / "config"
    config.mkdir(parents=True)
    xml = config / "setup.xml"
    xml.write_text("<original/>", encoding="utf-8")

    manifest = Manifest(
        scanned_at="t",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry("config/setup.xml", "ABC", 12, "t")],
    )
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    archive_manifest_files(str(game_root), manifest, snap_dir)

    xml.write_text("<mutated/>", encoding="utf-8")
    result = restore_manifest_files(snap_dir, manifest, str(game_root))
    assert result.written_count == 1
    assert result.missing_count == 0
    assert result.errors == ()
    assert xml.read_text(encoding="utf-8") == "<original/>"


def test_restore_manifest_files_requires_archive(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    manifest = Manifest(
        scanned_at="t",
        file_count=0,
        elapsed_seconds=0.0,
        files=[],
    )
    with pytest.raises(FileNotFoundError, match="no archived files"):
        restore_manifest_files(snap_dir, manifest, str(tmp_path / "game"))


def _write_archived_snapshot(
    snap_dir: Path,
    *,
    profile_id: str,
    profile_label: str,
    game_drive: str,
    rel_path: str,
    content: str,
) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    archived = snap_dir / "files" / Path(rel_path.replace("\\", "/"))
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_text(content, encoding="utf-8")
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "37556",
                "scanTimestamp": "2026-07-13T10:00:00",
                "gameDrive": game_drive,
                "profileId": profile_id,
                "profileLabel": profile_label,
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps(
            {
                "fileCount": 1,
                "elapsedSeconds": 0.1,
                "files": [
                    {
                        "relativePath": rel_path,
                        "sha1": "AAA",
                        "sizeBytes": len(content.encode("utf-8")),
                        "lastWriteUtc": "t",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _minimal_tool_root(tmp_path: Path) -> Path:
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
    return tool_root


def test_apply_snapshot_to_target_profile_mismatch(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")

    roulette_root = tmp_path / "roulette"
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir(parents=True)
    (ruleta / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.0.0\nBuild Number: 1\n",
        encoding="utf-8",
    )

    tool_root = _minimal_tool_root(tmp_path)
    _write_archived_snapshot(
        tool_root / "snapshots" / "slot_snap",
        profile_id="slot_lab_90",
        profile_label="Slot lab",
        game_drive=str(slot_root),
        rel_path="mgconfig.xml",
        content="<slot/>",
    )

    service = ConfigScannerService(tool_root)
    with pytest.raises(ValueError, match="profile mismatch"):
        service.apply_snapshot_to_target("slot_snap", str(roulette_root))


def test_apply_snapshot_to_target_success(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    mgconfig = slot_root / "mgconfig.xml"
    mgconfig.write_text("<original/>", encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    _write_archived_snapshot(
        tool_root / "snapshots" / "slot_snap",
        profile_id="slot_lab_90",
        profile_label="Slot lab",
        game_drive=str(slot_root),
        rel_path="mgconfig.xml",
        content="<original/>",
    )

    mgconfig.write_text("<mutated/>", encoding="utf-8")
    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("slot_snap", str(slot_root))
    assert result.written_count == 1
    assert result.missing_count == 0
    assert result.errors == ()
    assert mgconfig.read_text(encoding="utf-8") == "<original/>"
