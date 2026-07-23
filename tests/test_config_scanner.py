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
    is_actionable_content_change,
    looks_like_ciphertext_token,
    prioritize_file_diffs_for_panel,
    smart_find_match,
)
from config_scanner.service import ConfigScannerService, allocate_snapshot_dir, scan_scope_zero_diff_hint
from config_scanner.xml_diff import (
    ContentChange,
    FileDiff,
    apply_xml_value_at_path,
    compare_keyed_maps,
    compare_manifests,
    file_content_diff,
    find_element_for_flat_path,
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

    assert format_diff_cell_value(None) == "not present"
    assert format_diff_cell_value("") == '"" (blank)'
    assert format_diff_cell_value("   ") == '"" (blank)'
    assert format_diff_cell_value("game") == "game"
    assert apply_button_label("") == "Write blank"
    assert apply_button_label("game") == "Write"
    assert "blank" in format_apply_value_detail("").lower()
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
    assert service.is_scan_target_valid(r"C:\Goldclub\slot") is False


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
