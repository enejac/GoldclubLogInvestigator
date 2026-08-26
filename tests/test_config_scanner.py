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
from config_scanner.paths import _migrate_legacy_nested_layout, _resolve_tool_root
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
from config_scanner.report import (
    compare_panel_file_slice,
    compare_panel_layout_metrics,
    content_change_panel_partition,
    estimate_compare_panel_height,
    extract_changed_file_path,
    filter_file_diffs_for_find,
    format_compare_changed_list,
    format_compare_summary,
    format_setting_change_description,
    is_actionable_content_change,
    looks_like_ciphertext_token,
    prioritize_file_diffs_for_panel,
    setting_display_name,
    smart_find_match,
)
from config_scanner.service import ConfigScannerService, allocate_snapshot_dir, scan_scope_zero_diff_hint
from config_scanner.write_scope import (
    WriteScope,
    classify_path,
    filter_relative_paths,
    is_hardware_path,
    is_paytable_config_path,
    is_protected_write_path,
    is_software_path,
    path_matches_write_scope,
)
from config_scanner.xml_diff import (
    ContentChange,
    FileDiff,
    apply_xml_value_at_path,
    compare_keyed_maps,
    compare_manifests,
    file_content_diff,
    find_element_for_flat_path,
    flat_xml_map,
    is_structural_item_change,
    resolve_apply_value,
)

BUILD_VERSION_TEXT = """Source Version: 31099
Branch        : $/Certified/Ruleta/GC/10.1.0.0
Build Number  : 37556
Trigger       : Continuous Integration (on commit)
Requested By  : Luka Gustin
Date          : 2025-11-19 12:13:19
"""


def _ensure_roulette_exe(ruleta_dir: Path) -> None:
    ruleta_dir.mkdir(parents=True, exist_ok=True)
    exe = ruleta_dir / "Ruleta.exe"
    if not exe.is_file():
        exe.write_bytes(b"MZ")


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


@pytest.mark.integration
def test_discover_scan_target_slot_falls_back_from_bad_drive() -> None:
    from config_scanner.build_version import has_slot_game_exe, scan_target_path

    profile = get_profile("slot_lab_90")
    try:
        target = discover_scan_target(profile, preferred="D:")
    except FileNotFoundError:
        pytest.skip("No local slot install with OneHand.exe/game-start.exe")
    assert "Goldclub" in target or has_slot_game_exe(scan_target_path(target))
    assert has_slot_game_exe(scan_target_path(target))


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
    _ensure_roulette_exe(roulette_root / "ruleta")
    (roulette_root / "ruleta" / "BuildVersion.txt").write_text(BUILD_VERSION_TEXT, encoding="utf-8")
    (roulette_root / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    _ensure_roulette_exe(roulette_root / "ruleta")
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

    (roulette_root / "ruleta").mkdir(parents=True, exist_ok=True)
    (roulette_root / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")

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



def test_empty_goldclub_slot_is_not_valid_scan_target(tmp_path: Path) -> None:
    """Folder-only C:\\Goldclub\\slot without binaries must not auto-detect as SW."""
    from config_scanner.build_version import (
        has_slot_game_exe,
        is_roulette_scan_target,
        match_profile_for_target,
    )

    empty = tmp_path / "Goldclub" / "slot"
    empty.mkdir(parents=True)
    assert has_slot_game_exe(empty) is False
    assert match_profile_for_target(str(empty), load_profiles()) is None

    bare = tmp_path / "usb"
    bare.mkdir()
    (bare / "maintenance" / "tasks").mkdir(parents=True)
    (bare / "maintenance" / "tasks" / "ramclear.ps1").write_text("# x", encoding="utf-8")
    assert is_roulette_scan_target(bare, build_version_relative_path="ruleta/BuildVersion.txt") is False
    assert match_profile_for_target(str(bare), load_profiles()) is None


def test_slot_settings_dll_alone_is_not_enough(tmp_path: Path) -> None:
    profile = get_profile("slot_lab_90")
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    assert scan_target_is_valid(profile, str(slot_root)) is False

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
        machine_serial="GST20664",
    )
    assert name == "2026-07-13_GST20664_Slot_bAB12CD34_104546"


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
    name = snapshot_folder_name(
        "37556",
        datetime(2026, 7, 13, 10, 45, 46),
        profile_id="roulette_usb",
        product_version="10.1.0.0",
        machine_serial="GRT330106",
    )
    assert name == "2026-07-13_GRT330106_Ruleta_Alegro_Wing_v10.1.0.0_b37556_104546"


def test_snapshot_folder_name_uses_exe_details_version() -> None:
    """PE Details ProductVersion wins over a stale BuildVersion.txt / branch."""
    name = snapshot_folder_name(
        "40119",
        datetime(2026, 8, 11, 5, 29, 5),
        profile_id="roulette_usb",
        product_version="10.1.8.0",
        exe_product_version="10.2.0.876",
        machine_serial="GRT330106",
    )
    assert name == (
        "2026-08-11_GRT330106_Ruleta_Alegro_Wing_v10.2.0.876_b40119_052905"
    )
    assert "v10.1" not in name
    assert "v10.2_b" not in name


def test_order_snapshots_newest_first_pins_just_created() -> None:
    from config_scanner.service import SnapshotInfo, order_snapshots_newest_first

    def _row(name: str, stamp: str) -> SnapshotInfo:
        return SnapshotInfo(
            name=name,
            build_number=None,
            product_version=None,
            source_version=None,
            exe_product_version=None,
            exe_product_name=None,
            branch=None,
            scan_timestamp=stamp,
            file_count=1,
            game_drive="",
            is_baseline=False,
        )

    older = _row("2026-08-19_v10.1", "2026-08-19T08:30:00")
    rolled = _row("2026-08-11_v10.2.0.876", "2026-08-11T05:43:00")
    ordered = order_snapshots_newest_first([older, rolled])
    assert [row.name for row in ordered] == [older.name, rolled.name]
    pinned = order_snapshots_newest_first(
        [older, rolled], pin_name=rolled.name
    )
    assert [row.name for row in pinned] == [rolled.name, older.name]


def test_snapshot_folder_name_without_serial_keeps_legacy_shape() -> None:
    name = snapshot_folder_name(
        "37556",
        datetime(2026, 7, 13, 10, 45, 46),
        profile_id="roulette_usb",
        product_version="10.1.0.0",
    )
    assert name == "2026-07-13_Ruleta_Alegro_Wing_v10.1.0.0_b37556_104546"


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


def test_compare_does_not_force_unchanged_when_setup_paths_missing() -> None:
    """SHA mismatch must stay modified even if archived plains are absent."""
    rel = "config/etc/application/ruleta/setup.xml"
    baseline = Manifest(
        scanned_at="t0",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry(rel, "AAAA", 10, "t")],
    )
    target = Manifest(
        scanned_at="t1",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry(rel, "BBBB", 11, "t")],
    )
    diffs = compare_manifests(
        baseline,
        target,
        baseline_game_drive="",
        target_game_drive="",
        baseline_content_root=None,
        target_content_root=None,
    )
    assert len(diffs) == 1
    assert diffs[0].status == "modified"
    assert diffs[0].content_diff == []


def test_etc_mirror_prefers_highest_full_path() -> None:
    from config_scanner.path_mirror import (
        highest_full_path,
        select_highest_etc_mirror_paths,
    )

    bios = "bios/etc/application/ruleta/setup.xml"
    config = "config/etc/application/ruleta/setup.xml"
    assert highest_full_path([bios, config]) == config
    kept = select_highest_etc_mirror_paths([bios, config, "data/foo.xml"])
    assert kept == [config, "data/foo.xml"]


def test_compare_dedupes_bios_and_config_etc_mirrors() -> None:
    """Same ruleta setup under bios/etc and config/etc → report config/ only."""
    bios = "bios/etc/application/ruleta/setup.xml"
    config = "config/etc/application/ruleta/setup.xml"
    baseline = Manifest(
        scanned_at="t0",
        file_count=2,
        elapsed_seconds=0.1,
        files=[
            FileEntry(bios, "OLD1", 10, "t"),
            FileEntry(config, "OLD1", 10, "t"),
        ],
    )
    target = Manifest(
        scanned_at="t1",
        file_count=2,
        elapsed_seconds=0.1,
        files=[
            FileEntry(bios, "NEW1", 11, "t"),
            FileEntry(config, "NEW1", 11, "t"),
        ],
    )
    diffs = compare_manifests(
        baseline,
        target,
        baseline_game_drive="",
        target_game_drive="",
    )
    changed = [item for item in diffs if item.status != "unchanged"]
    assert len(changed) == 1
    assert changed[0].relative_path == config
    assert changed[0].status == "modified"


def test_collect_scan_files_drops_bios_etc_when_config_etc_exists(tmp_path: Path) -> None:
    game = tmp_path / "Goldclub"
    for rel in (
        "bios/etc/application/ruleta/setup.xml",
        "config/etc/application/ruleta/setup.xml",
        "data/only_here.dat",
    ):
        path = game / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<x/>", encoding="utf-8")
    files = collect_scan_files(
        game,
        ["config", "bios/etc", "data"],
        ["*.xml", "*.dat"],
    )
    rels = sorted(p.relative_to(game).as_posix() for p in files)
    assert rels == [
        "config/etc/application/ruleta/setup.xml",
        "data/only_here.dat",
    ]


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


def test_compare_panel_file_slice_limits_large_diffs() -> None:
    file_diffs = [
        FileDiff(
            relative_path=f"config/file_{index}.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", f"Setting/{index}", "old", "new")
            ],
        )
        for index in range(40)
    ]
    visible, omitted = compare_panel_file_slice(file_diffs, max_files=25)
    assert len(visible) == 25
    assert omitted == 15


def test_compare_panel_file_slice_no_truncation() -> None:
    file_diffs = [
        FileDiff(
            relative_path="themes/mgconfig.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", "Multigamer/Language", None, "Spanish"),
            ],
        )
    ]
    visible, omitted = compare_panel_file_slice(file_diffs)
    assert len(visible) == 1
    assert omitted == 0


def test_smart_find_match_subsequence_case_insensitive() -> None:
    assert smart_find_match("swi", "switches.xml")
    assert smart_find_match("SWI", "Switches")
    assert smart_find_match("swi", "config/switches/futura.xml")
    assert not smart_find_match("swi", "language")
    assert smart_find_match("", "anything")
    assert smart_find_match("   ", "anything")


def test_filter_file_diffs_for_find_by_path_and_setting() -> None:
    file_diffs = [
        FileDiff(
            relative_path="config/switches.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", "Switches/EnableBonus", "false", "true"),
                ContentChange("modified", "Language", "en", "es"),
            ],
        ),
        FileDiff(
            relative_path="themes/mgconfig.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", "Multigamer/Language", None, "Spanish"),
            ],
        ),
        FileDiff(
            relative_path="config/removed_only.xml",
            status="removed",
            baseline_sha1="AAA",
            target_sha1=None,
            baseline_size_bytes=10,
            target_size_bytes=None,
            content_diff=[],
        ),
    ]
    filtered = filter_file_diffs_for_find(file_diffs, "swi")
    assert len(filtered) == 1
    assert filtered[0].relative_path == "config/switches.xml"
    assert len(filtered[0].content_diff) == 2

    narrowed = filter_file_diffs_for_find(file_diffs, "bonus")
    assert len(narrowed) == 1
    assert len(narrowed[0].content_diff) == 1
    assert narrowed[0].content_diff[0].path == "Switches/EnableBonus"

    assert filter_file_diffs_for_find(file_diffs, "zzz") == []
    assert filter_file_diffs_for_find(file_diffs, "") == file_diffs


def test_compare_panel_layout_metrics_single_change() -> None:
    file_diffs = [
        FileDiff(
            relative_path="themes/mgconfig.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", "Multigamer/Language", None, "Spanish"),
            ],
        )
    ]
    metrics = compare_panel_layout_metrics(file_diffs)
    assert metrics == {
        "apply_rows": 1,
        "file_headers": 1,
        "notes": 0,
        "empty": 0,
    }
    assert estimate_compare_panel_height(metrics) == 16 + 28 + 38


def test_compare_panel_layout_metrics_respects_truncation() -> None:
    file_diffs = [
        FileDiff(
            relative_path=f"config/file_{index}.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange("modified", f"Setting/{row}", "old", "new")
                for row in range(20)
            ],
        )
        for index in range(30)
    ]
    metrics = compare_panel_layout_metrics(file_diffs, max_files=25, max_changes_per_file=12)
    assert metrics["apply_rows"] == 25 * 12
    assert metrics["file_headers"] == 25
    assert metrics["notes"] == 26  # omitted files + per-file overflow notes


def test_compare_panel_layout_metrics_empty() -> None:
    metrics = compare_panel_layout_metrics([])
    assert metrics["empty"] == 1
    assert metrics["apply_rows"] == 0
    assert estimate_compare_panel_height(metrics) == 88


def test_ciphertext_token_detection() -> None:
    assert looks_like_ciphertext_token("4E7E698E8E")
    assert looks_like_ciphertext_token("D5AFC0AD5B2749492589D5A5DC2833CBE48B764C")
    assert looks_like_ciphertext_token(
        "_x0032_4D2CDA0499C9DF8486DB093FDF48110AA8A798F8"
    )
    assert not looks_like_ciphertext_token("keepPaytableUserSelection")
    assert not looks_like_ciphertext_token("0")
    assert not looks_like_ciphertext_token("true")


def test_actionable_change_prefers_readable_settings() -> None:
    readable = ContentChange(
        "modified",
        "Config/keepPaytableUserSelection",
        "1",
        "0",
    )
    spaced = ContentChange(
        "modified",
        "config/node[@name='game settings']/node[@name='timing']/node[@name='still time']",
        "30",
        "45",
    )
    opaque = ContentChange(
        "removed",
        "config/A4CF543B053D2D[@name='D5AFC0AD5B2749492589D5A5DC2833CB']",
        "4E7E698E8E",
        None,
    )
    assert is_actionable_content_change(readable)
    assert is_actionable_content_change(spaced)
    assert not is_actionable_content_change(opaque)
    actionable, opaque_list = content_change_panel_partition([opaque, readable])
    assert actionable == [readable]
    assert opaque_list == [opaque]


def test_scan_archives_decrypted_ruleta_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypted ruleta setup.xml is hashed/archived as plain settings XML."""
    import hashlib

    from ai_helper import gcxml_decrypt as gcd
    from config_scanner.scanner import _hash_file

    game_root = tmp_path / "Goldclub"
    setup = game_root / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    setup.parent.mkdir(parents=True)
    cipher = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        '  <_x0033_ABC name="ABC">DEADBEEF</_x0033_ABC>\n'
        "</config>\n"
    )
    setup.write_text(cipher, encoding="utf-8")
    plain = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<config content-type="gcxml-plain" manglerKeyword="settings" '
        'sourceEncrypted="C:\\temp\\setup.xml">\n'
        '  <node name="game settings">\n'
        '    <node name="timing">\n'
        '      <node name="numberofspins2activate">5</node>\n'
        "    </node>\n"
        "  </node>\n"
        "</config>\n"
    )
    normalized = gcd.normalize_gcxml_plain_for_compare(plain)
    monkeypatch.setattr(
        "config_scanner.scanner._plain_override_bytes",
        lambda path: normalized.encode("utf-8"),
    )

    entry, archive_bytes = _hash_file(setup)
    expected = hashlib.sha1(normalized.encode("utf-8")).hexdigest().upper()
    assert entry.sha1 == expected
    assert entry.size_bytes == len(normalized.encode("utf-8"))
    assert entry.size_bytes != len(cipher.encode("utf-8"))
    assert archive_bytes == normalized.encode("utf-8")
    assert entry.decrypt_ok is True
    assert entry.content_kind == "plain"

    rel = "config/etc/application/ruleta/setup.xml"
    manifest = Manifest(
        scanned_at="t",
        file_count=1,
        elapsed_seconds=0.1,
        files=[
            FileEntry(
                rel,
                entry.sha1,
                entry.size_bytes,
                "t",
                content_kind=entry.content_kind,
                decrypt_ok=entry.decrypt_ok,
            )
        ],
    )
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    archive_manifest_files(
        str(game_root),
        manifest,
        snap_dir,
        content_overrides={rel: archive_bytes or b""},
    )
    archived = snap_dir / "files" / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    text = archived.read_text(encoding="utf-8")
    assert 'content-type="gcxml-plain"' in text
    assert "numberofspins2activate" in text
    assert "DEADBEEF" not in text
    assert 'sourceEncrypted="setup.xml"' in text


def test_gcxml_decrypt_cache_invalidates_when_encrypted_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config Scanner must re-decrypt after BiOS writes a setting (not reuse stale plain)."""
    from ai_helper import gcxml_decrypt as gcd

    setup = tmp_path / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    setup.parent.mkdir(parents=True)
    setup.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        "  <_x0032_A name=\"A\">V0</_x0032_A>\n"
        "</config>\n",
        encoding="utf-8",
    )
    bios_dup = tmp_path / "bios" / "etc" / "application" / "ruleta" / "setup.xml"
    bios_dup.parent.mkdir(parents=True)
    bios_dup.write_bytes(setup.read_bytes())
    calls: list[str] = []

    def fake_run(cmd, **_kwargs):  # noqa: ANN001
        calls.append("run")
        enc_path = Path(cmd[cmd.index("-EncryptedPath") + 1])
        plain_path = Path(cmd[cmd.index("-PlainPath") + 1])
        marker = "V0" if b"V0" in enc_path.read_bytes() else "V1"
        plain_path.write_text(
            '<?xml version="1.0"?>\n'
            '<config content-type="gcxml-plain">\n'
            f'  <node name="payoutAutoConfirm">{marker}</node>\n'
            "</config>\n",
            encoding="utf-8",
        )

        class _Proc:
            returncode = 0
            stderr = b""

        return _Proc()

    monkeypatch.setattr(gcd, "_find_convert_script", lambda: Path("Convert-GcxmlSetup.ps1"))
    monkeypatch.setattr(
        gcd,
        "_find_settings_dll",
        lambda _p: tmp_path / "GoldClub.Settings.dll",
    )
    (tmp_path / "GoldClub.Settings.dll").write_bytes(b"MZ")
    monkeypatch.setattr(gcd, "ensure_local_settings_dll", lambda p: p)
    monkeypatch.setattr(gcd.subprocess, "run", fake_run)
    gcd.clear_gcxml_decrypt_cache()

    first = gcd.plain_bytes_for_config_scan(setup)
    assert first is not None
    assert b">V0</node>" in first
    assert len(calls) == 1

    # Same ciphertext via bios path → content-addressed cache hit (no second decrypt).
    dup = gcd.plain_bytes_for_config_scan(bios_dup)
    assert dup == first
    assert len(calls) == 1

    # Same process, second scan without file change → cache hit.
    second = gcd.plain_bytes_for_config_scan(setup)
    assert second == first
    assert len(calls) == 1

    # BiOS-style in-place edit of encrypted setup.xml.
    setup.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        "  <_x0032_A name=\"A\">V1</_x0032_A>\n"
        "</config>\n",
        encoding="utf-8",
    )
    third = gcd.plain_bytes_for_config_scan(setup)
    assert third is not None
    assert b">V1</node>" in third
    assert len(calls) == 2

    assert gcd.is_live_ruleta_settings_xml(setup)
    assert not gcd.is_live_ruleta_settings_xml(
        tmp_path / "config" / "etc" / "xml-configs" / "application" / "ruleta" / "setup.xml"
    )


def test_hash_file_skips_content_kind_for_xml_configs_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema mirrors must hash raw bytes; contentKind is live setup only."""
    import hashlib

    from config_scanner.scanner import _hash_file

    schema = (
        tmp_path
        / "config"
        / "etc"
        / "xml-configs"
        / "application"
        / "ruleta"
        / "setup.xml"
    )
    schema.parent.mkdir(parents=True)
    schema.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        "  <ITEM____DEAD name=\"DEAD\">BEEF</ITEM____DEAD>\n"
        "</config>\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "config_scanner.scanner._plain_override_bytes",
        lambda path: b"SHOULD_NOT_USE",
    )
    entry, archive_bytes = _hash_file(schema)
    assert entry.content_kind is None
    assert entry.decrypt_ok is None
    assert archive_bytes is None
    assert entry.sha1 == hashlib.sha1(schema.read_bytes()).hexdigest().upper()


def test_plain_setup_content_diff_is_readable(tmp_path: Path) -> None:
    baseline = tmp_path / "a" / "setup.xml"
    target = tmp_path / "b" / "setup.xml"
    baseline.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    baseline.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="timing">\n'
        '    <node name="numberofspins2activate">5</node>\n'
        "  </node>\n"
        "</config>\n",
        encoding="utf-8",
    )
    target.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="timing">\n'
        '    <node name="numberofspins2activate">0</node>\n'
        "  </node>\n"
        "</config>\n",
        encoding="utf-8",
    )
    changes = file_content_diff(
        "config/etc/application/ruleta/setup.xml",
        baseline,
        target,
    )
    assert len(changes) == 1
    assert changes[0].old_value == "5"
    assert changes[0].new_value == "0"
    assert "numberofspins2activate" in changes[0].path
    assert is_actionable_content_change(changes[0])



def test_encrypted_setup_content_diff_decrypts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Encrypted archived setup.xml is decrypted before field-level compare."""
    from ai_helper import gcxml_decrypt as gcd

    cipher = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        '  <_x0032_ABC name="ABC">DEADBEEF</_x0032_ABC>\n'
        "</config>\n"
    )
    plain_a = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="timing">\n'
        '    <node name="numberofspins2activate">5</node>\n'
        "  </node>\n"
        "</config>\n"
    )
    plain_b = plain_a.replace(">5<", ">0<")
    baseline = (
        tmp_path / "a" / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    )
    target = tmp_path / "b" / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    baseline.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    baseline.write_text(cipher, encoding="utf-8")
    target.write_text(cipher.replace("DEADBEEF", "CAFEBABE"), encoding="utf-8")

    def fake_decrypt(path, **_kwargs):
        return plain_a if Path(path).resolve() == baseline.resolve() else plain_b

    monkeypatch.setattr(gcd, "decrypt_gcxml_setup_to_plain_xml", fake_decrypt)
    gcd.clear_gcxml_decrypt_cache()

    changes = file_content_diff(
        "config/etc/application/ruleta/setup.xml",
        baseline,
        target,
    )
    assert len(changes) == 1
    assert changes[0].old_value == "5"
    assert changes[0].new_value == "0"
    assert is_actionable_content_change(changes[0])


def test_apply_value_to_live_ruleta_setup_reencrypts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_helper import gcxml_decrypt as gcd
    from config_scanner.xml_diff import apply_xml_value_at_path

    live = tmp_path / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    live.parent.mkdir(parents=True)
    live.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        '  <_x0032_X name="X">AA</_x0032_X>\n'
        "</config>\n",
        encoding="utf-8",
    )
    plain = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="timing">\n'
        '    <node name="numberofspins2activate">5</node>\n'
        "  </node>\n"
        "</config>\n"
    )
    monkeypatch.setattr(gcd, "decrypt_gcxml_setup_to_plain_xml", lambda *_a, **_k: plain)
    written: dict[str, str] = {}

    def fake_encrypt(plain_path, encrypted_path, **_kwargs):
        written["plain"] = Path(plain_path).read_text(encoding="utf-8")
        Path(encrypted_path).write_text(
            '<config content-type="gcxml">ENCRYPTED</config>', encoding="utf-8"
        )
        return True

    monkeypatch.setattr(gcd, "encrypt_gcxml_plain_file", fake_encrypt)
    gcd.clear_gcxml_decrypt_cache()
    path = "config/node[@name='timing']/node[@name='numberofspins2activate']"
    gcd.apply_value_to_live_ruleta_setup(
        live, path, "0", apply_xml_fn=apply_xml_value_at_path
    )
    assert ">0</node>" in written["plain"]
    assert 'content-type="gcxml"' in live.read_text(encoding="utf-8")

def test_prioritize_file_diffs_puts_opaque_setup_last() -> None:
    file_diffs = [
        FileDiff(
            relative_path="config/etc/application/ruleta/setup.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=10,
            target_size_bytes=12,
            content_diff=[
                ContentChange(
                    "removed",
                    "config/X[@name='D5AFC0AD5B2749492589D5A5DC2833CB']",
                    "4E7E698E8E",
                    None,
                ),
                ContentChange(
                    "added",
                    "config/Y[@name='4A0BE2C1ECEC0A6C8ACB77F1A031111']",
                    None,
                    "4B250F3C7B147110A6C8ACB77F1A031111",
                ),
            ],
        ),
        FileDiff(
            relative_path="config/switches.xml",
            status="modified",
            baseline_sha1="CCC",
            target_sha1="DDD",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange(
                    "modified",
                    "Switches/keepPaytableUserSelection",
                    "1",
                    "0",
                ),
            ],
        ),
    ]
    ordered = prioritize_file_diffs_for_panel(file_diffs)
    assert ordered[0].relative_path == "config/switches.xml"
    assert ordered[1].relative_path.endswith("setup.xml")

    metrics = compare_panel_layout_metrics(file_diffs)
    assert metrics["apply_rows"] == 1
    assert metrics["file_headers"] == 2
    # omitted-files note absent; opaque-only file note + optional opaque note under switches? 
    # switches has no opaque; setup is opaque-only -> 1 note
    assert metrics["notes"] == 1

    text = format_compare_changed_list(file_diffs)
    assert "keepPaytableUserSelection" in text
    assert text.index("switches.xml") < text.index("setup.xml")
    assert "Encrypted config churn" in text


def test_format_compare_summary_mgconfig_case() -> None:
    summary = format_compare_summary(
        "slot_baseline",
        "2026-07-14_slot8A9B9BE9_102713_728",
        {"added": 0, "removed": 0, "modified": 1, "unchanged": 40},
    )
    assert summary == (
        "Comparing slot_baseline \u2192 2026-07-14_slot8A9B9BE9_102713_728: "
        "1 file changed (40 unchanged)"
    )


def test_format_compare_summary_no_changes() -> None:
    summary = format_compare_summary("slot_baseline", "slot_baseline_copy", {"added": 0, "removed": 0, "modified": 0, "unchanged": 41})
    assert summary == "Comparing slot_baseline → slot_baseline_copy: no file changes (41 files match)"


def test_format_compare_summary_mixed_changes() -> None:
    summary = format_compare_summary(
        "baseline_a",
        "target_b",
        {"added": 1, "removed": 1, "modified": 2, "unchanged": 10},
    )
    assert "Comparing baseline_a \u2192 target_b:" in summary
    assert "4 files changed" in summary
    assert "2 modified" in summary
    assert "1 added" in summary
    assert "1 removed" in summary
    assert "(10 unchanged)" in summary


def test_format_compare_panel_summary_two_settings() -> None:
    from config_scanner.report import format_compare_panel_summary
    from config_scanner.xml_diff import ContentChange, FileDiff

    diffs = [
        FileDiff(
            relative_path="config/etc/application/ruleta/setup.xml",
            status="modified",
            baseline_sha1="a",
            target_sha1="b",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange(
                    change_type="modified",
                    path="config/node[@name='payoutAutoConfirm']",
                    old_value="1",
                    new_value="0",
                )
            ],
        ),
        FileDiff(
            relative_path="config/etc/application/config/appSettings.xml",
            status="modified",
            baseline_sha1="c",
            target_sha1="d",
            baseline_size_bytes=10,
            target_size_bytes=10,
            content_diff=[
                ContentChange(
                    change_type="added",
                    path="config/initPath",
                    old_value=None,
                    new_value="game settings.godot",
                )
            ],
        ),
    ]
    assert format_compare_panel_summary(diffs) == "2 files \u00b7 2 settings"


def test_compare_panel_styles_have_chip_keys() -> None:
    from config_scanner.report import compare_panel_encrypted_origin_styles

    styles = compare_panel_encrypted_origin_styles()
    for key in (
        "summary",
        "chip",
        "chip_muted",
        "arrow",
        "file_card",
        "encrypted_header",
        "plain_header",
    ):
        assert key in styles
        assert styles[key]


def test_format_compare_changed_list_mgconfig_case() -> None:
    file_diffs = [
        FileDiff(
            relative_path="themes/mgconfig.xml",
            status="modified",
            baseline_sha1="AAA",
            target_sha1="BBB",
            baseline_size_bytes=100,
            target_size_bytes=110,
            content_diff=[
                ContentChange("modified", "Multigamer/Language", None, "Spanish"),
                ContentChange(
                    "modified",
                    "Multigamer/TransferParameters/CashoutButtonMode",
                    "Cashless",
                    "Handpay",
                ),
            ],
        )
    ]
    text = format_compare_changed_list(file_diffs)
    assert "Modified: themes/mgconfig.xml (multigamer settings)" in text
    assert "Language: not present \u2192 Spanish" in text
    assert "CashoutButtonMode: Cashless \u2192 Handpay" in text


def test_extract_changed_file_path_new_format() -> None:
    assert (
        extract_changed_file_path("Modified: themes/mgconfig.xml (multigamer settings)")
        == "themes/mgconfig.xml"
    )
    assert extract_changed_file_path("Added: config/setup.xml") == "config/setup.xml"
    assert extract_changed_file_path("  Language: not present \u2192 Spanish") is None


def test_format_diff_cell_value_distinguishes_missing_and_blank() -> None:
    from config_scanner.report import (
        apply_button_label,
        format_apply_value_detail,
        format_diff_cell_value,
    )

    assert format_diff_cell_value(None) == "missing"
    assert format_diff_cell_value("") == "(empty)"
    assert format_diff_cell_value("   ") == "(empty)"
    assert format_diff_cell_value("game") == "game"
    assert apply_button_label("") == "Apply empty"
    assert apply_button_label("game") == "Apply"
    assert "empty" in format_apply_value_detail("").lower()
    assert "missing" in format_apply_value_detail(None).lower()


def test_encrypted_origin_path_and_header_label() -> None:
    from config_scanner.report import file_diff_header_label
    from config_scanner.xml_diff import is_encrypted_origin_config_path

    assert is_encrypted_origin_config_path(
        r"bios\etc\application\ruleta\setup.xml"
    )
    assert is_encrypted_origin_config_path("config/etc/application/ruleta/setup.xml")
    assert not is_encrypted_origin_config_path(
        "config/etc/xml-configs/application/ruleta/setup.xml"
    )
    assert not is_encrypted_origin_config_path("config/etc/application/mgconfig.xml")
    assert not is_encrypted_origin_config_path("data/foo/bar.xml")
    header = file_diff_header_label(
        r"bios\etc\application\ruleta\setup.xml", "modified"
    )
    assert "[encrypted on disk]" in header
    plain = file_diff_header_label("config/mgconfig.xml", "modified")
    assert "[encrypted on disk]" not in plain


def test_service_lists_existing_snapshots() -> None:
    service = ConfigScannerService()
    snapshots = service.list_snapshots()
    if not snapshots:
        pytest.skip("No config-scanner snapshots on this machine")
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
        machine_serial="GST20664",
    )
    assert baseline_folder_name(info) == "GST20664_slot_baseline"


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
        machine_serial="GRT330106",
    )
    assert baseline_folder_name(info) == "GRT330106_Ruleta_Alegro_Wing_v10.1.0.0_b37556_baseline"


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


def test_resolve_tool_root_uses_exe_dir_not_usb_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    usb_dir = tmp_path / "usb"
    (usb_dir / "snapshots" / "2026-07-14_build40097_123401_098").mkdir(parents=True)

    monkeypatch.setattr("config_scanner.paths._default_exe_dir", lambda: exe_dir)

    assert _resolve_tool_root() == exe_dir


def test_resolve_tool_root_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    override = tmp_path / "custom_root"
    override.mkdir()
    monkeypatch.setattr("config_scanner.paths._default_exe_dir", lambda: exe_dir)
    monkeypatch.setenv("LOGINV_CONFIG_SCANNER_ROOT", str(override))

    assert _resolve_tool_root() == override.resolve()


def test_resolve_tool_root_keeps_exe_dir_when_it_has_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe_dir = tmp_path / "dist"
    (exe_dir / "snapshots" / "local_snap").mkdir(parents=True)
    usb_dir = tmp_path / "usb"
    (usb_dir / "snapshots" / "usb_snap").mkdir(parents=True)

    monkeypatch.setattr("config_scanner.paths._default_exe_dir", lambda: exe_dir)
    monkeypatch.setattr(
        "config_scanner.paths._portable_data_candidates",
        lambda _exe: [usb_dir, exe_dir],
    )

    assert _resolve_tool_root() == exe_dir


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


def test_live_ruleta_exe_version_and_banner(tmp_path: Path) -> None:
    from config_scanner.software_compat import (
        format_live_ruleta_sw_banner,
        live_ruleta_exe_version_for_target,
    )

    assert format_live_ruleta_sw_banner(None) == "Running: Ruleta.exe unknown"
    assert format_live_ruleta_sw_banner("  ") == "Running: Ruleta.exe unknown"
    assert format_live_ruleta_sw_banner("10.2.0.876") == (
        "Running: Ruleta.exe 10.2.0.876"
    )
    ruleta_dir = tmp_path / "ruleta"
    ruleta_dir.mkdir()
    (ruleta_dir / "Ruleta.exe").write_bytes(b"\x00" * 100 + "10.1.8.0".encode("utf-16le"))
    assert live_ruleta_exe_version_for_target(str(tmp_path)) == "10.1.8.0"
    assert live_ruleta_exe_version_for_target(str(tmp_path / "missing")) is None


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
    # The software display already carries build/product version, so the line must
    # state each of them exactly once.
    assert "build 37556" in line
    assert "build=37556" not in line
    assert line.count("10.1.0.0") == 2
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
    _ensure_roulette_exe(ruleta)
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


def test_resolve_scan_target_from_configscanner_subfolder(tmp_path: Path) -> None:
    """Portable exe on D:\\ConfigScanner should still scan roulette game at D:\\."""
    roulette_root = tmp_path / "usb_d"
    (roulette_root / "ruleta").mkdir(parents=True)
    _ensure_roulette_exe(roulette_root / "ruleta")
    (roulette_root / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.0.0\nBuild Number: 37556\n",
        encoding="utf-8",
    )
    (roulette_root / "config").mkdir()
    (roulette_root / "config" / "setup.xml").write_text("<root/>", encoding="utf-8")
    tool_dir = roulette_root / "ConfigScanner"
    tool_dir.mkdir()

    profiles = load_profiles()
    result = resolve_scan_for_target(str(tool_dir), profiles)
    assert result.profile_id == "roulette_usb"
    assert Path(result.target) == roulette_root


def test_is_scan_target_valid_rejects_stale_slot_path(tmp_path: Path) -> None:
    roulette_root = tmp_path / "usb_d"
    (roulette_root / "ruleta").mkdir(parents=True)
    _ensure_roulette_exe(roulette_root / "ruleta")
    (roulette_root / "ruleta" / "BuildVersion.txt").write_text(
        "Build Number: 1\n", encoding="utf-8"
    )
    (roulette_root / "config").mkdir()

    tool_root = _minimal_tool_root(tmp_path)
    service = ConfigScannerService(tool_root)
    assert service.is_scan_target_valid(str(roulette_root)) is True
    # Empty / non-slot C:\Goldclub\slot must not walk up to a parent roulette tree.
    assert service.is_scan_target_valid(r"C:\Goldclub\slot") is False
    assert service.is_scan_target_valid(str(tmp_path / "missing_scan_root")) is False


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


def test_goldclub_share_named_slot_is_roulette_when_ruleta_present(tmp_path: Path) -> None:
    """\\\\cabinet\\slot is C:\\goldclub on roulette EGMs, not Goldclub\\slot."""
    from config_scanner.build_version import is_slot_cabinet_scan_target

    share = tmp_path / "slot"
    ruleta = share / "ruleta"
    ruleta.mkdir(parents=True)
    _ensure_roulette_exe(ruleta)
    (ruleta / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.1.0.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )
    (share / "config").mkdir()
    (share / "config" / "setup.xml").write_text("<root/>", encoding="utf-8")
    profiles = load_profiles()
    assert is_slot_cabinet_scan_target(str(share)) is False
    result = resolve_scan_for_target(str(share), profiles)
    assert result.profile_id == "roulette_usb"


def test_prepare_for_target_honors_explicit_drive(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")

    roulette_root = tmp_path / "roulette"
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir(parents=True)
    _ensure_roulette_exe(ruleta)
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
    _ensure_roulette_exe(ruleta)
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


def test_write_scope_classifies_hw_sw_and_protected() -> None:
    assert is_hardware_path("HW/driverssetup/configuration.xml")
    assert is_hardware_path("config/etc/application/CommCtrl/settings.xml")
    assert is_hardware_path("config/etc/application/CommCtrlSAS/settings.xml")
    # Live cabinet paths (trailing SAS instance digit + system/hardware tree).
    assert is_hardware_path(
        "config/etc/application/aurum/SASControler1/options.xml"
    )
    assert is_hardware_path(
        "config/etc/application/system/hardware/monitor/monitors.json"
    )
    ruleta_json = (
        "config/etc/application/system/hardware/serialport/serialports/ruleta.json"
    )
    assert is_protected_write_path(ruleta_json)
    assert classify_path(ruleta_json) == "protected"
    assert not is_hardware_path(ruleta_json)
    assert is_hardware_path("data/XYNTService2/CommCtrl.xml")
    assert is_hardware_path("data/maintenance/config/service.d/CommCtrl.json")
    # Device configs outside …/hardware/ (bill, ticket, switches, LEDs, counters).
    assert is_hardware_path("config/etc/application/game/switches.xml")
    assert is_hardware_path("config/etc/application/gcbackup/items/config/game-switches.xml")
    assert is_hardware_path("config/plugins/hwsubsys-switchconfigintelligent.xml")
    assert is_hardware_path(
        "data/GoldClub.HW.Subsys.Driver.DriverLoader/receipt/"
        "GoldClub.HW.Subsys.Driver.GoldClub.Light.xml"
    )
    assert is_hardware_path(
        "data/GoldClub.HW.Subsys.Driver.DriverLoader/receipt/"
        "GoldClub.HW.Subsys.Driver.Egasa.ElectronicCounter.xml"
    )
    assert is_hardware_path(
        "data/GoldClub.HW.Subsys.Driver.DriverLoader/receipt/"
        "GoldClub.HW.Subsys.Driver.FutureLogic.PSA66ST2.xml"
    )
    assert is_hardware_path(
        "data/GoldClub.HW.Subsys.Driver.FutureLogic.PSA66ST2/tickets/ticket0.dat"
    )
    assert is_hardware_path("data/setup.2/Additional/ExternalGameBillDeviceInfo/device.xml")
    assert is_hardware_path(
        "data/setup.2/Additional/ExternalTicketPrinterDeviceInfo/device.xml"
    )
    assert is_hardware_path(
        "config/etc/application/HW/driverssetup/configuration.xml"
    )
    # Game UI / ruleta layout — not cabinet HW devices.
    assert not is_hardware_path(
        "config/etc/application/ruleta/layouts/singlewheel/common/jackpotCountersScreen.xml"
    )
    assert not is_hardware_path("config/etc/xml-configs/application/ruleta/Mechanical.xml")
    assert is_software_path("config/etc/application/ruleta/setup.xml")
    assert is_software_path("mgconfig.xml")
    assert is_software_path("slot/themes/mgconfig.xml")
    assert is_protected_write_path(
        "config/etc/application/system/hardware/serialport/layout.json"
    )
    assert is_protected_write_path(
        "config/etc/application/hardware/serialport/layout.json"
    )
    assert is_protected_write_path(
        "config/etc/application/system/hardware/serialport/serialports/ruleta.json"
    )
    assert classify_path(
        "config/etc/application/system/hardware/serialport/locations.json"
    ) == "protected"
    assert not path_matches_write_scope(
        "config/etc/application/system/hardware/serialport/layout.json",
        WriteScope.HARDWARE,
    )
    assert not path_matches_write_scope(
        "config/etc/application/system/hardware/serialport/layout.json",
        WriteScope.FULL,
    )
    # Misclassification guard: hardware tree must not fall through to software.
    assert not is_software_path(
        "config/etc/application/system/hardware/monitor/monitors.json"
    )
    assert not is_software_path(
        "config/etc/application/aurum/SASControler1/options.xml"
    )
    hw = filter_relative_paths(
        [
            "HW/driverssetup/configuration.xml",
            "config/etc/application/aurum/SASControler1/options.xml",
            "config/etc/application/system/hardware/monitor/monitors.json",
            "mgconfig.xml",
            "config/etc/application/ruleta/setup.xml",
            "config/etc/application/system/hardware/serialport/layout.json",
        ],
        WriteScope.HARDWARE,
    )
    assert hw == [
        "HW/driverssetup/configuration.xml",
        "config/etc/application/aurum/SASControler1/options.xml",
        "config/etc/application/system/hardware/monitor/monitors.json",
    ]
    sw = filter_relative_paths(
        [
            "HW/driverssetup/configuration.xml",
            "config/etc/application/aurum/SASControler1/options.xml",
            "mgconfig.xml",
            "config/etc/application/ruleta/setup.xml",
        ],
        WriteScope.SOFTWARE,
    )
    assert sw == ["mgconfig.xml", "config/etc/application/ruleta/setup.xml"]
    paytable = "config/etc/application/ruleta/paytables/paytable_elite_double_zero.json"
    assert is_paytable_config_path(paytable)
    assert not is_paytable_config_path("config/etc/application/ruleta/setup.xml")
    assert path_matches_write_scope(paytable, WriteScope.FULL)
    assert path_matches_write_scope(paytable, WriteScope.FULL_SOFTWARE)
    assert not path_matches_write_scope(paytable, WriteScope.BINARIES_ONLY)
    assert not path_matches_write_scope(
        "config/etc/application/ruleta/setup.xml", WriteScope.BINARIES_ONLY
    )
    assert filter_relative_paths(
        [paytable, "config/etc/application/ruleta/setup.xml"],
        WriteScope.BINARIES_ONLY,
    ) == []
    assert not path_matches_write_scope(paytable, WriteScope.NO_PAYTABLE)
    assert path_matches_write_scope(
        "config/etc/application/ruleta/setup.xml", WriteScope.NO_PAYTABLE
    )
    assert filter_relative_paths(
        [paytable, "config/etc/application/ruleta/setup.xml"],
        WriteScope.NO_PAYTABLE,
    ) == ["config/etc/application/ruleta/setup.xml"]


def test_restore_manifest_files_respects_path_allow(tmp_path: Path) -> None:
    game_root = tmp_path / "game"
    hw = game_root / "HW" / "driverssetup"
    hw.mkdir(parents=True)
    sw = game_root / "config" / "etc" / "application" / "ruleta"
    sw.mkdir(parents=True)
    (hw / "configuration.xml").write_text("<hw-live/>", encoding="utf-8")
    (sw / "setup.xml").write_text("<sw-live/>", encoding="utf-8")

    manifest = Manifest(
        scanned_at="t",
        file_count=2,
        elapsed_seconds=0.1,
        files=[
            FileEntry("HW/driverssetup/configuration.xml", "A", 8, "t"),
            FileEntry("config/etc/application/ruleta/setup.xml", "B", 8, "t"),
        ],
    )
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "files" / "HW" / "driverssetup").mkdir(parents=True)
    (snap_dir / "files" / "config" / "etc" / "application" / "ruleta").mkdir(
        parents=True
    )
    (snap_dir / "files" / "HW" / "driverssetup" / "configuration.xml").write_text(
        "<hw-snap/>", encoding="utf-8"
    )
    (snap_dir / "files" / "config" / "etc" / "application" / "ruleta" / "setup.xml").write_text(
        "<sw-snap/>", encoding="utf-8"
    )

    result = restore_manifest_files(
        snap_dir,
        manifest,
        str(game_root),
        relative_path_allow={"HW/driverssetup/configuration.xml"},
    )
    assert result.written_count == 1
    assert result.skipped_count == 1
    assert (hw / "configuration.xml").read_text(encoding="utf-8") == "<hw-snap/>"
    assert (sw / "setup.xml").read_text(encoding="utf-8") == "<sw-live/>"


def test_apply_snapshot_hardware_scope_skips_software(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    hw = slot_root / "HW" / "driverssetup"
    hw.mkdir(parents=True)
    (hw / "configuration.xml").write_text("<hw-live/>", encoding="utf-8")
    mgconfig = slot_root / "mgconfig.xml"
    mgconfig.write_text("<sw-live/>", encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    snap_dir = tool_root / "snapshots" / "slot_snap"
    snap_dir.mkdir(parents=True)
    (snap_dir / "files" / "HW" / "driverssetup").mkdir(parents=True)
    (snap_dir / "files" / "HW" / "driverssetup" / "configuration.xml").write_text(
        "<hw-snap/>", encoding="utf-8"
    )
    (snap_dir / "files" / "mgconfig.xml").write_text("<sw-snap/>", encoding="utf-8")
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "37556",
                "scanTimestamp": "2026-07-13T10:00:00",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot lab",
            }
        ),
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps(
            {
                "fileCount": 2,
                "elapsedSeconds": 0.1,
                "files": [
                    {
                        "relativePath": "HW/driverssetup/configuration.xml",
                        "sha1": "AAA",
                        "sizeBytes": 9,
                        "lastWriteUtc": "t",
                    },
                    {
                        "relativePath": "mgconfig.xml",
                        "sha1": "BBB",
                        "sizeBytes": 9,
                        "lastWriteUtc": "t",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    assert service.count_scoped_snapshot_files("slot_snap", "hardware") == 1
    assert service.count_scoped_snapshot_files("slot_snap", "software") == 1
    assert service.count_scoped_snapshot_files("slot_snap", "full") == 2

    result = service.apply_snapshot_to_target(
        "slot_snap", str(slot_root), write_scope="hardware"
    )
    assert result.written_count == 1
    assert result.write_scope == "hardware"
    assert result.scoped_file_count == 1
    assert (hw / "configuration.xml").read_text(encoding="utf-8") == "<hw-snap/>"
    assert mgconfig.read_text(encoding="utf-8") == "<sw-live/>"


def test_apply_archived_file_rejects_protected_serialport(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    rel = "config/etc/application/system/hardware/serialport/layout.json"
    live = slot_root / Path(rel)
    live.parent.mkdir(parents=True)
    live.write_text('{"live":true}', encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    _write_archived_snapshot(
        tool_root / "snapshots" / "slot_snap",
        profile_id="slot_lab_90",
        profile_label="Slot lab",
        game_drive=str(slot_root),
        rel_path=rel,
        content='{"snap":true}',
    )
    service = ConfigScannerService(tool_root)
    with pytest.raises(ValueError, match="serialport"):
        service.apply_archived_file_to_target(str(slot_root), "slot_snap", rel)
    assert live.read_text(encoding="utf-8") == '{"live":true}'


def test_restore_manifest_files_skips_serialport_even_when_allowed(
    tmp_path: Path,
) -> None:
    """Low-level restore must refuse COM maps even if allow-list includes them."""
    from config_scanner.scanner import FileEntry, Manifest, restore_single_archived_file

    game_root = tmp_path / "game"
    rel = "config/etc/application/system/hardware/serialport/layout.json"
    live = game_root / Path(rel)
    live.parent.mkdir(parents=True)
    live.write_text('{"live":true}', encoding="utf-8")
    ok_rel = "config/setup.xml"
    ok_live = game_root / "config" / "setup.xml"
    ok_live.write_text("<live/>", encoding="utf-8")

    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    for path, body in ((rel, '{"snap":true}'), (ok_rel, "<snap/>")):
        archived = snap_dir / "files" / Path(path)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text(body, encoding="utf-8")

    manifest = Manifest(
        scanned_at="t",
        file_count=2,
        elapsed_seconds=0.1,
        files=[
            FileEntry(rel, "A" * 40, 12, "t"),
            FileEntry(ok_rel, "B" * 40, 7, "t"),
        ],
    )
    # Deliberately allow serialport — scanner must still skip it.
    result = restore_manifest_files(
        snap_dir,
        manifest,
        str(game_root),
        relative_path_allow={rel, ok_rel},
    )
    assert result.written_count == 1
    assert result.skipped_count == 1
    assert result.errors == ()
    assert live.read_text(encoding="utf-8") == '{"live":true}'
    assert ok_live.read_text(encoding="utf-8") == "<snap/>"

    with pytest.raises(ValueError, match="serialport"):
        restore_single_archived_file(snap_dir, rel, str(game_root))
    assert live.read_text(encoding="utf-8") == '{"live":true}'


def test_count_scoped_only_counts_archived_files(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"x")
    (slot_root / "game-start.exe").write_bytes(b"x")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"x")
    tool_root = _minimal_tool_root(tmp_path)
    snap_dir = tool_root / "snapshots" / "slot_snap"
    snap_dir.mkdir(parents=True)
    (snap_dir / "files").mkdir()
    # Manifest lists mgconfig but archive copy is missing.
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "t",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot",
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
                        "relativePath": "mgconfig.xml",
                        "sha1": "A",
                        "sizeBytes": 1,
                        "lastWriteUtc": "t",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    assert service.count_scoped_snapshot_files("slot_snap", "software") == 0
    assert service.count_scoped_snapshot_files("slot_snap", "full") == 0


def test_apply_snapshot_fails_when_zero_files_written(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"x")
    (slot_root / "game-start.exe").write_bytes(b"x")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"x")
    (slot_root / "mgconfig.xml").write_text("<live/>", encoding="utf-8")
    tool_root = _minimal_tool_root(tmp_path)
    snap_dir = tool_root / "snapshots" / "slot_snap"
    snap_dir.mkdir(parents=True)
    (snap_dir / "files").mkdir()
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "t",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot",
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
                        "relativePath": "mgconfig.xml",
                        "sha1": "A",
                        "sizeBytes": 1,
                        "lastWriteUtc": "t",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    with pytest.raises(OSError, match="Wrote 0 files"):
        service.apply_snapshot_to_target(
            "slot_snap", str(slot_root), write_scope="software"
        )


def test_apply_snapshot_to_target_success_sets_full_scope(tmp_path: Path) -> None:
    """Regression: default apply still restores software paths like mgconfig."""
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    mgconfig = slot_root / "mgconfig.xml"
    mgconfig.write_text("<mutated/>", encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    _write_archived_snapshot(
        tool_root / "snapshots" / "slot_snap",
        profile_id="slot_lab_90",
        profile_label="Slot lab",
        game_drive=str(slot_root),
        rel_path="mgconfig.xml",
        content="<original/>",
    )
    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("slot_snap", str(slot_root))
    assert result.write_scope == "full"
    assert result.written_count == 1
    assert mgconfig.read_text(encoding="utf-8") == "<original/>"


MGCONFIG_XML = """<?xml version="1.0"?>
<Multigamer>
  <TransferParameters>
    <CashoutButtonMode>Cashless</CashoutButtonMode>
  </TransferParameters>
</Multigamer>
"""


def test_find_element_for_flat_path_mgconfig() -> None:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(MGCONFIG_XML)
    element = find_element_for_flat_path(root, "Multigamer/TransferParameters/CashoutButtonMode")
    assert element is not None
    assert element.text == "Cashless"


def test_apply_xml_value_at_path_mgconfig(tmp_path: Path) -> None:
    mgconfig = tmp_path / "mgconfig.xml"
    mgconfig.write_text(MGCONFIG_XML, encoding="utf-8")
    apply_xml_value_at_path(
        mgconfig,
        "Multigamer/TransferParameters/CashoutButtonMode",
        "Handpay",
    )
    updated = mgconfig.read_text(encoding="utf-8")
    assert "Handpay" in updated
    assert "Cashless" not in updated


def test_resolve_apply_value_sides() -> None:
    modified = ContentChange(
        "modified",
        "Multigamer/TransferParameters/CashoutButtonMode",
        "Cashless",
        "Handpay",
    )
    assert resolve_apply_value(modified, "baseline") == "Cashless"
    assert resolve_apply_value(modified, "target") == "Handpay"

    added = ContentChange("added", "Multigamer/Language", None, "Spanish")
    assert resolve_apply_value(added, "baseline") is None
    assert resolve_apply_value(added, "target") == "Spanish"

    removed = ContentChange("removed", "Multigamer/Language", "English", None)
    assert resolve_apply_value(removed, "baseline") == "English"
    assert resolve_apply_value(removed, "target") is None


def test_apply_content_change_to_target_writes_live_file(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    themes = slot_root / "themes"
    themes.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    mgconfig = themes / "mgconfig.xml"
    mgconfig.write_text(MGCONFIG_XML, encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    service = ConfigScannerService(tool_root)
    change = ContentChange(
        "modified",
        "Multigamer/TransferParameters/CashoutButtonMode",
        "Cashless",
        "Handpay",
    )
    result = service.apply_content_change_to_target(
        str(slot_root),
        "themes/mgconfig.xml",
        change,
        "target",
    )
    assert result.value == "Handpay"
    assert result.relative_path == "themes/mgconfig.xml"
    assert "Handpay" in mgconfig.read_text(encoding="utf-8")

def test_unified_candidates_include_lab_unc_before_late_drives() -> None:
    from config_scanner.build_version import unified_scan_candidates

    cands = unified_scan_candidates(load_profiles())
    unc = [c for c in cands if "10.0.0.90" in c and c.rstrip("\\").endswith("Goldclub")]
    assert unc, "lab roulette UNC missing"
    i_idx = next((i for i, c in enumerate(cands) if c.rstrip("\\") == "I:"), len(cands))
    unc_idx = min(cands.index(u) for u in unc)
    assert unc_idx < i_idx


_DRIVERS_WITH_TITO = """<?xml version="1.0" encoding="utf-8"?>
<config xmlns="config">
  <drivers>
    <item0>
      <aliasName>switch</aliasName>
      <driverRawName>.[GoldClub.HW.Subsys.Driver.GoldClub.SecuritySwitch]</driverRawName>
      <enabled>True</enabled>
      <options>
        <endpointaddress>tcp://127.0.0.1:30600</endpointaddress>
      </options>
    </item0>
    <item1>
      <aliasName>tito</aliasName>
      <driverRawName>.[GoldClub.HW.Subsys.Driver.FutureLogic.PSA66ST2]</driverRawName>
      <enabled>True</enabled>
      <options>
        <endpointaddress>tcp://127.0.0.1:30400</endpointaddress>
      </options>
    </item1>
    <item2>
      <aliasName>light</aliasName>
      <driverRawName>.[GoldClub.HW.Subsys.Driver.GoldClub.Light]</driverRawName>
      <enabled>True</enabled>
      <options>
        <endpointaddress>tcp://127.0.0.1:30700</endpointaddress>
      </options>
    </item2>
  </drivers>
</config>
"""

_DRIVERS_WITHOUT_TITO = """<?xml version="1.0" encoding="utf-8"?>
<config xmlns="config">
  <drivers>
    <item0>
      <aliasName>switch</aliasName>
      <driverRawName>.[GoldClub.HW.Subsys.Driver.GoldClub.SecuritySwitch]</driverRawName>
      <enabled>True</enabled>
      <options>
        <endpointaddress>tcp://127.0.0.1:30600</endpointaddress>
      </options>
    </item0>
    <item1>
      <aliasName>light</aliasName>
      <driverRawName>.[GoldClub.HW.Subsys.Driver.GoldClub.Light]</driverRawName>
      <enabled>True</enabled>
      <options>
        <endpointaddress>tcp://127.0.0.1:30700</endpointaddress>
      </options>
    </item1>
  </drivers>
</config>
"""


def test_driverssetup_tito_remove_is_not_rename_to_light(tmp_path: Path) -> None:
    """Removing tito must NOT look like tito→light / 30400→30700 field mutations."""
    import xml.etree.ElementTree as ET

    baseline = tmp_path / "baseline.xml"
    target = tmp_path / "target.xml"
    baseline.write_text(_DRIVERS_WITH_TITO, encoding="utf-8")
    target.write_text(_DRIVERS_WITHOUT_TITO, encoding="utf-8")

    changes = file_content_diff("HW/driverssetup/configuration.xml", baseline, target)

    # One structural remove for tito — not a flood of leaf removes + false modifies.
    assert len(changes) == 1
    change = changes[0]
    assert change.change_type == "removed"
    assert "aliasName='tito'" in change.path
    assert "light" not in change.path
    assert is_structural_item_change(change)
    assert change.old_value is not None
    assert "tito" in change.old_value.casefold()
    assert "30400" in change.old_value

    # No misleading modified rows that look like tito became light.
    assert not any(c.change_type == "modified" for c in changes)
    assert not any(
        c.old_value == "tito" and c.new_value == "light" for c in changes
    )
    assert not any(
        c.old_value and "30400" in c.old_value and c.new_value and "30700" in c.new_value
        for c in changes
    )

    # Labels must be unambiguous; Write must be blocked.
    assert "HW driver «tito»" == setting_display_name(change.path)
    desc = format_setting_change_description(change)
    assert "entire driver entry removed" in desc.casefold() or "entire driver entry removed" in desc
    assert "not a rename" in desc.casefold()
    assert not is_actionable_content_change(change)

    primary, opaque = content_change_panel_partition(changes)
    assert change in primary
    assert change not in opaque

    # Identity path still resolves light under the live file.
    root = ET.parse(target).getroot()
    light_ep = find_element_for_flat_path(
        root,
        "config/drivers/item[@aliasName='light']/options/endpointaddress",
    )
    assert light_ep is not None
    assert light_ep.text == "tcp://127.0.0.1:30700"

    with pytest.raises(ValueError, match="whole HW driver"):
        apply_xml_value_at_path(target, change.path, change.old_value)


def test_driverssetup_identity_paths_stable_across_index_shift() -> None:
    import xml.etree.ElementTree as ET

    with_tito = flat_xml_map(ET.ElementTree(ET.fromstring(_DRIVERS_WITH_TITO)))
    without = flat_xml_map(ET.ElementTree(ET.fromstring(_DRIVERS_WITHOUT_TITO)))
    assert any("item[@aliasName='tito']" in k for k in with_tito)
    assert any("item[@aliasName='light']" in k for k in with_tito)
    assert any("item[@aliasName='light']" in k for k in without)
    # Same identity key for light despite item2 → item1 index shift.
    light_keys_a = {k for k in with_tito if "aliasName='light'" in k}
    light_keys_b = {k for k in without if "aliasName='light'" in k}
    assert light_keys_a == light_keys_b
    for key in light_keys_a:
        assert with_tito[key] == without[key]


def test_machine_identity_paths_blocked() -> None:
    from config_scanner.machine_identity import is_protected_machine_identity_path
    from config_scanner.write_scope import is_protected_write_path

    assert is_protected_machine_identity_path("config/licences/6051106A90E561F9F08CDEEBB5C3F6E2.xml")
    assert is_protected_machine_identity_path("config/licenses/foo.xml")
    assert is_protected_machine_identity_path("slot/licence.dll")
    assert is_protected_machine_identity_path(
        "config/etc/application/licensing/xmlLicenceStorageSettings.xml"
    )
    assert is_protected_machine_identity_path(
        "var/state/maintenance/ProductSerialNumber.json"
    )
    assert is_protected_machine_identity_path("services/aurum/config/AurumSetup.xml")
    assert is_protected_write_path("config/licences/foo.xml")
    assert not is_protected_machine_identity_path(
        "config/etc/application/ruleta/setup.xml"
    )


def test_machine_identity_field_not_actionable() -> None:
    from config_scanner.machine_identity import is_protected_identity_field
    from config_scanner.report import is_actionable_content_change
    from config_scanner.xml_diff import ContentChange

    change = ContentChange(
        change_type="modified",
        path="Licence/SerialNumber",
        old_value="330106",
        new_value="19737",
    )
    assert is_protected_identity_field(change.path)
    assert not is_actionable_content_change(change)

    egm = ContentChange(
        change_type="modified",
        path="root/EgmId",
        old_value="GCC_RT_330106_01",
        new_value="GCC_RT_19737_01",
    )
    assert is_protected_identity_field(egm.path)
    assert not is_actionable_content_change(egm)


def test_merge_xml_preserves_live_serial() -> None:
    from config_scanner.machine_identity import merge_xml_bytes_preserving_identity

    live = b"""<?xml version='1.0'?>
<Licence xmlns="http://tempuri.org/AurumLicence.xsd">
  <SerialNumber>330106</SerialNumber>
  <LicenseeId>12-12327444</LicenseeId>
</Licence>"""
    incoming = b"""<?xml version='1.0'?>
<Licence xmlns="http://tempuri.org/AurumLicence.xsd">
  <SerialNumber>19737</SerialNumber>
  <LicenseeId>12-99999999</LicenseeId>
</Licence>"""
    merged = merge_xml_bytes_preserving_identity(live, incoming)
    assert b">330106</" in merged
    assert b"12-99999999" in merged
    assert b">19737</" not in merged


def test_restore_skips_licence_file(tmp_path: Path) -> None:
    from config_scanner.scanner import (
        FileEntry,
        Manifest,
        archive_manifest_files,
        restore_manifest_files,
        snapshot_content_root,
    )

    target = tmp_path / "Goldclub"
    target.mkdir()
    rel = "config/licences/test-licence.xml"
    live_file = target / rel.replace("/", "\\")
    live_file.parent.mkdir(parents=True)
    live_file.write_text(
        "<Licence><SerialNumber>330106</SerialNumber></Licence>",
        encoding="utf-8",
    )

    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = Manifest(
        scanned_at="2026-07-27T12:00:00Z",
        file_count=1,
        elapsed_seconds=0.1,
        files=[
            FileEntry(rel, "a" * 40, 10, "2026-07-27T12:00:00Z"),
        ],
    )
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={
            rel: b"<Licence><SerialNumber>19737</SerialNumber></Licence>",
        },
    )
    assert snapshot_content_root(snap_dir) is not None

    result = restore_manifest_files(snap_dir, manifest, str(target))
    assert result.written_count == 0
    assert result.skipped_count == 1
    assert "330106" in live_file.read_text(encoding="utf-8")


def test_apply_content_change_rejects_identity_field(tmp_path: Path) -> None:
    from config_scanner.machine_identity import is_protected_identity_field
    from config_scanner.xml_diff import ContentChange

    change = ContentChange(
        change_type="modified",
        path="root/EgmId",
        old_value="GCC_RT_330106_01",
        new_value="GCC_RT_19737_01",
    )
    assert is_protected_identity_field(change.path)
    assert not is_actionable_content_change(change)

def test_read_machine_serial_from_target(tmp_path: Path) -> None:
    from config_scanner.build_version import read_machine_serial_from_target

    maint = tmp_path / "var" / "state" / "maintenance"
    maint.mkdir(parents=True)
    (maint / "ProductSerialNumber.json").write_text(
        '{"MachineName": "GRT330106", "ProductSerialNumber": "330106", "ProductKind": "RT"}',
        encoding="utf-8",
    )
    assert read_machine_serial_from_target(str(tmp_path)) == "GRT330106"

def test_write_verify_detects_protected_change(tmp_path: Path) -> None:
    from config_scanner.write_verify import (
        PreWriteCapture,
        capture_pre_write_state,
        verify_snapshot_restore,
    )

    game_root = tmp_path / "game"
    hw = game_root / "HW" / "driverssetup"
    hw.mkdir(parents=True)
    serial = game_root / "config/etc/application/system/hardware/serialport"
    serial.mkdir(parents=True)
    layout = serial / "layout.json"
    layout.write_text('{"live":true}', encoding="utf-8")
    (hw / "configuration.xml").write_text("<live/>", encoding="utf-8")

    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    rel_hw = "HW/driverssetup/configuration.xml"
    rel_layout = "config/etc/application/system/hardware/serialport/layout.json"
    for rel, body in ((rel_hw, "<snap/>"), (rel_layout, '{"snap":true}')):
        archived = snap_dir / "files" / Path(rel)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text(body, encoding="utf-8")

    manifest = Manifest(
        scanned_at="t",
        file_count=2,
        elapsed_seconds=0.1,
        files=[
            FileEntry(rel_hw, "A" * 40, 8, "t"),
            FileEntry(rel_layout, "B" * 40, 12, "t"),
        ],
    )
    pre = capture_pre_write_state(
        game_root, manifest, allowed_paths={rel_hw}
    )
    assert rel_layout in pre.protected_sha1
    layout.write_text('{"tampered":true}', encoding="utf-8")
    result = verify_snapshot_restore(
        snap_dir,
        manifest,
        game_root,
        written_paths=(rel_hw,),
        pre_write=pre,
    )
    assert not result.ok
    assert rel_layout in result.protected_changed


def test_apply_snapshot_verifies_written_files(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    hw = slot_root / "HW" / "driverssetup"
    hw.mkdir(parents=True)
    (hw / "configuration.xml").write_text("<live/>", encoding="utf-8")
    serial = slot_root / "config/etc/application/system/hardware/serialport"
    serial.mkdir(parents=True)
    (serial / "layout.json").write_text('{"ports":[]}', encoding="utf-8")

    tool_root = _minimal_tool_root(tmp_path)
    snap_dir = tool_root / "snapshots" / "slot_snap"
    _write_archived_snapshot(
        snap_dir,
        profile_id="slot_lab_90",
        profile_label="Slot lab",
        game_drive=str(slot_root),
        rel_path="HW/driverssetup/configuration.xml",
        content="<snap/>",
    )
    layout_rel = "config/etc/application/system/hardware/serialport/layout.json"
    layout_arch = snap_dir / "files" / Path(layout_rel)
    layout_arch.parent.mkdir(parents=True, exist_ok=True)
    layout_arch.write_text('{"snap":true}', encoding="utf-8")
    manifest = load_manifest(snap_dir)
    manifest = Manifest(
        scanned_at=manifest.scanned_at,
        file_count=manifest.file_count + 1,
        elapsed_seconds=manifest.elapsed_seconds,
        files=[
            *manifest.files,
            FileEntry(layout_rel, "B" * 40, 12, "t"),
        ],
        warnings=manifest.warnings,
    )
    (snap_dir / "manifest.json").write_text(
        json.dumps(
            {
                "scannedAt": manifest.scanned_at,
                "fileCount": manifest.file_count,
                "elapsedSeconds": manifest.elapsed_seconds,
                "files": [
                    {
                        "relativePath": e.relative_path,
                        "sha1": e.sha1,
                        "sizeBytes": e.size_bytes,
                        "lastWriteUtc": e.last_write_utc,
                    }
                    for e in manifest.files
                ],
            }
        ),
        encoding="utf-8",
    )
    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "slot_snap", str(slot_root), write_scope="hardware"
    )
    assert result.verify_ok is True
    assert result.written_count == 1
    assert result.protected_verified >= 1
    assert (hw / "configuration.xml").read_text(encoding="utf-8") == "<snap/>"
    assert (serial / "layout.json").read_text(encoding="utf-8") == '{"ports":[]}'

def test_format_compare_panel_header_shows_snapshot_pair() -> None:
    from config_scanner.report import format_compare_panel_header
    from config_scanner.xml_diff import FileDiff

    diffs = [
        FileDiff(
            relative_path="config/a.xml",
            status="modified",
            baseline_sha1="a",
            target_sha1="b",
            baseline_size_bytes=1,
            target_size_bytes=1,
            content_diff=[],
        ),
        FileDiff(
            relative_path="config/b.xml",
            status="added",
            baseline_sha1=None,
            target_sha1="c",
            baseline_size_bytes=None,
            target_size_bytes=1,
            content_diff=[],
        ),
    ]
    text = format_compare_panel_header("ref_snap", "new_snap", diffs)
    assert "ref_snap" in text and "new_snap" in text
    assert "2 files" in text
    tagged = format_compare_panel_header(
        "ref_snap", "new_snap", diffs, session_reference="ref_snap"
    )
    assert "reference snapshot" in tagged

def test_rollback_save_load_and_availability(tmp_path: Path) -> None:
    from config_scanner.rollback import (
        clear_rollback,
        load_rollback,
        make_rollback_info,
        rollback_is_available,
        save_rollback,
    )

    root = tmp_path / "tool"
    (root / "snapshots" / "rollback_snap").mkdir(parents=True)
    (root / "snapshots" / "rollback_snap" / "files").mkdir()
    (root / "snapshots" / "rollback_snap" / "files" / "a.xml").write_text("<a/>", encoding="utf-8")

    info = make_rollback_info(
        snapshot_name="rollback_snap",
        restored_from="baseline_snap",
        write_scope="full",
        scan_target=r"\\10.0.0.90\c$\Goldclub",
    )
    save_rollback(info, root)
    loaded = load_rollback(root)
    assert loaded is not None
    assert loaded.snapshot_name == "rollback_snap"
    assert loaded.restored_from == "baseline_snap"
    assert rollback_is_available(root, loaded)

    clear_rollback(root)
    assert load_rollback(root) is None


def test_service_records_and_clears_rollback(tmp_path: Path) -> None:
    tool_root = tmp_path / "tool"
    (tool_root / "snapshots").mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "config.json").write_text(
        '{"gameDrive": null, "buildVersionRelativePath": "ruleta/BuildVersion.txt", '
        '"scanRoots": ["config"], "includePatterns": ["*.xml"], "parallelWorkers": 1, '
        '"snapshotsDir": "snapshots", "reportsDir": "reports"}',
        encoding="utf-8",
    )
    snap = tool_root / "snapshots" / "live_before"
    (snap / "files").mkdir(parents=True)
    (snap / "files" / "x.xml").write_text("<x/>", encoding="utf-8")

    service = ConfigScannerService(tool_root)
    assert not service.rollback_is_available()
    service.record_rollback_snapshot(
        snapshot_name="live_before",
        restored_from="ref_snap",
        write_scope="hardware",
        scan_target="D:\\Goldclub",
    )
    assert service.rollback_is_available()
    info = service.get_rollback_info()
    assert info is not None and info.snapshot_name == "live_before"
    service.clear_rollback_snapshot()
    assert service.get_rollback_info() is None


def _write_rollback_snapshot(
    root: Path,
    name: str,
    *,
    exe_product_version: str,
    with_software: bool,
) -> None:
    snap = root / "snapshots" / name
    (snap / "files").mkdir(parents=True)
    (snap / "files" / "x.xml").write_text("<x/>", encoding="utf-8")
    (snap / "build-info.json").write_text(
        json.dumps(
            {
                "productVersion": exe_product_version,
                "exeProductVersion": exe_product_version,
                "buildNumber": "40119",
                "scanTimestamp": "2026-08-21T08:00:00+00:00",
                "gameDrive": "C:\\Goldclub",
                "profileId": "roulette_usb",
                "profileLabel": "Ruleta Alegro Wing",
            }
        ),
        encoding="utf-8",
    )
    if with_software:
        (snap / "software").mkdir()
        (snap / "software" / "Ruleta.exe").write_bytes(b"MZ")


def test_describe_rollback_reports_version_and_software(tmp_path: Path) -> None:
    from config_scanner.rollback import (
        describe_rollback,
        make_rollback_info,
    )

    root = tmp_path / "tool"
    _write_rollback_snapshot(
        root, "live_before", exe_product_version="10.1.8.0", with_software=True
    )
    info = make_rollback_info(
        snapshot_name="live_before",
        restored_from="ref_snap",
        write_scope="full_software",
        scan_target="C:\\Goldclub",
    )
    details = describe_rollback(info, root)
    assert details is not None
    assert details.version == "10.1.8.0"
    assert details.has_software is True
    assert details.is_self_contained is True
    assert "10.1.8.0" in details.summary()
    assert "Ruleta binaries" in details.summary()


def test_describe_rollback_flags_config_only_undo_point(tmp_path: Path) -> None:
    from config_scanner.rollback import describe_rollback, make_rollback_info

    root = tmp_path / "tool"
    _write_rollback_snapshot(
        root, "live_before", exe_product_version="10.2.0.876", with_software=False
    )
    details = describe_rollback(
        make_rollback_info(
            snapshot_name="live_before",
            restored_from="ref_snap",
            write_scope="full",
            scan_target="C:\\Goldclub",
        ),
        root,
    )
    assert details is not None
    assert details.has_software is False
    assert details.is_self_contained is False
    assert "config only" in details.summary()


def test_describe_rollback_none_without_snapshot(tmp_path: Path) -> None:
    from config_scanner.rollback import describe_rollback, make_rollback_info

    assert describe_rollback(None, tmp_path) is None
    missing = make_rollback_info(
        snapshot_name="gone",
        restored_from="ref",
        write_scope="full",
        scan_target="C:\\Goldclub",
    )
    assert describe_rollback(missing, tmp_path) is None


def test_service_exposes_rollback_details(tmp_path: Path) -> None:
    tool_root = tmp_path / "tool"
    (tool_root / "snapshots").mkdir(parents=True)
    (tool_root / "reports").mkdir()
    (tool_root / "config.json").write_text(
        '{"gameDrive": null, "buildVersionRelativePath": "ruleta/BuildVersion.txt", '
        '"scanRoots": ["config"], "includePatterns": ["*.xml"], "parallelWorkers": 1, '
        '"snapshotsDir": "snapshots", "reportsDir": "reports"}',
        encoding="utf-8",
    )
    _write_rollback_snapshot(
        tool_root, "live_before", exe_product_version="10.1.8.0", with_software=True
    )

    service = ConfigScannerService(tool_root)
    assert service.get_rollback_details() is None
    service.record_rollback_snapshot(
        snapshot_name="live_before",
        restored_from="ref_snap",
        write_scope="full_software",
        scan_target="C:\\Goldclub",
    )
    details = service.get_rollback_details()
    assert details is not None
    assert details.version == "10.1.8.0"
    assert details.is_self_contained is True


def test_stack_restart_finds_repo_scripts() -> None:
    from config_scanner.stack_restart import find_stack_scripts

    scripts = find_stack_scripts()
    assert scripts is not None
    kill, run = scripts
    assert kill.name == "Kill-All.ps1"
    assert run.name == "Run-FullStack.ps1"


def test_plan_stack_restart_local_drive_on_egm(monkeypatch) -> None:
    from config_scanner import stack_restart as sr

    monkeypatch.setattr(sr, "running_on_egm", lambda: True)
    monkeypatch.setattr(
        sr,
        "find_stack_scripts",
        lambda: (Path("Kill-All.ps1"), Path("Run-FullStack.ps1")),
    )
    plan = sr.plan_stack_restart(r"C:\Goldclub")
    assert plan is not None
    assert plan.mode == "local"
    assert plan.host is None


def test_plan_stack_restart_unc_remote() -> None:
    from config_scanner.stack_restart import plan_stack_restart, unc_host_from_target

    assert unc_host_from_target(r"\\10.0.0.111\c$\Goldclub") == "10.0.0.111"
    plan = plan_stack_restart(r"\\10.0.0.111\c$\Goldclub")
    assert plan is not None
    assert plan.mode == "remote"
    assert plan.host == "10.0.0.111"
    assert plan.run_ps1.endswith("Run-FullStack.ps1")

