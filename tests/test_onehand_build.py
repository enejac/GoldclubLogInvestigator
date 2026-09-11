"""OneHand Debug vs Release from SlotLog ``MainFrm - DB`` (not PE / denoms)."""

from __future__ import annotations

from pathlib import Path

from gui.view_model import IncidentViewModel
from onehand_build import (
    BUILD_DEBUG,
    BUILD_RELEASE,
    classify_onehand_build_from_lines,
    detect_onehand_build_from_log_dir,
    format_onehand_build_suffix,
)

# Live 10.0.0.76 SlotLog (11 Sep 2026) — Debug SKU, DenominationList ends at 5000.
_DEBUG_BOOT = [
    "2026-09-11T08:07:48.391+01:00 INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
    "2026-09-11T08:07:48.391+01:00 INFO  [1] OneHand.MainFrm - Static initialization (i0)",
    "2026-09-11T08:07:48.391+01:00 INFO  [1] OneHand.MainFrm - DB",
    "2026-09-11T08:07:48.407+01:00 INFO  [1] OneHand.MainFrm - loaded assembly mscorlib",
    r"2026-09-11T08:07:48.450+01:00 INFO  [1] OneHand.MainFrm - loaded Themes\mgconfig.xml",
]

_RELEASE_BOOT = [
    "2026-09-11T09:00:00.000+01:00 INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
    "2026-09-11T09:00:00.000+01:00 INFO  [1] OneHand.MainFrm - Static initialization (i0)",
    "2026-09-11T09:00:00.010+01:00 INFO  [1] OneHand.MainFrm - loaded assembly mscorlib",
    r"2026-09-11T09:00:00.020+01:00 INFO  [1] OneHand.MainFrm - loaded Themes\mgconfig.xml",
]


def test_db_banner_after_static_init_is_debug() -> None:
    assert classify_onehand_build_from_lines(_DEBUG_BOOT) == BUILD_DEBUG


def test_mgconfig_without_db_banner_is_release() -> None:
    assert classify_onehand_build_from_lines(_RELEASE_BOOT) == BUILD_RELEASE


def test_known_release_banner_token() -> None:
    lines = [
        "INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
        "INFO  [1] OneHand.MainFrm - Static initialization (i0)",
        "INFO  [1] OneHand.MainFrm - RL",
    ]
    assert classify_onehand_build_from_lines(lines) == BUILD_RELEASE


def test_log_level_debug_is_not_the_banner() -> None:
    lines = [
        "2026-09-11T08:07:48.391+01:00 DEBUG [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
        "2026-09-11T08:07:48.391+01:00 DEBUG [1] OneHand.MainFrm - Static initialization (i0)",
        r"2026-09-11T08:07:48.450+01:00 INFO  [1] OneHand.MainFrm - loaded Themes\mgconfig.xml",
    ]
    assert classify_onehand_build_from_lines(lines) == BUILD_RELEASE


def test_db_substring_in_longer_message_is_ignored() -> None:
    lines = [
        "INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
        "INFO  [1] OneHand.MainFrm - Static initialization (i0)",
        "INFO  [1] OneHand.MainFrm - DB connection pool ready",
        r"INFO  [1] OneHand.MainFrm - loaded Themes\mgconfig.xml",
    ]
    assert classify_onehand_build_from_lines(lines) == BUILD_RELEASE


def test_pe_and_denom_noise_does_not_classify() -> None:
    lines = [
        "INFO  DebuggableAttribute JIT tracking",
        "INFO  CodeView path C:\\build\\Debug\\OneHand.pdb",
        "INFO  ProductVersion=Debug AssemblyConfiguration=Release",
        "INFO  DenominationList 1,2,5,10,20,25,50,100,200,250,500,1000,1500,2000,5000",
        "INFO  leftover screenshot <int>50000</int>",
        "INFO  Denom:1; Credits:980",
    ]
    assert classify_onehand_build_from_lines(lines) is None


def test_truncated_startup_stays_unknown() -> None:
    lines = [
        "INFO  [1] OneHand.MainFrm - SlotMachine v3.0.0.0",
        "INFO  [1] OneHand.MainFrm - Static initialization (i0)",
    ]
    assert classify_onehand_build_from_lines(lines) is None


def test_lone_db_line_is_debug() -> None:
    assert classify_onehand_build_from_lines(
        ["2026-09-11T08:07:48.391+01:00 INFO  [1] OneHand.MainFrm - DB"]
    ) == BUILD_DEBUG


def test_later_release_boot_wins_over_old_db() -> None:
    lines = _DEBUG_BOOT + ["INFO padding"] + _RELEASE_BOOT
    assert classify_onehand_build_from_lines(lines) == BUILD_RELEASE


def test_later_debug_boot_wins_over_old_release() -> None:
    lines = _RELEASE_BOOT + ["INFO padding"] + _DEBUG_BOOT
    assert classify_onehand_build_from_lines(lines) == BUILD_DEBUG


def test_detect_from_slotlog_folder(tmp_path: Path) -> None:
    slot = tmp_path / "Goldclub" / "slot"
    logs = tmp_path / "Goldclub" / "var" / "log" / "SlotLog"
    logs.mkdir(parents=True)
    slot.mkdir(parents=True)
    (logs / "2026-09-11.log").write_text("\n".join(_DEBUG_BOOT) + "\n", encoding="utf-8")
    assert detect_onehand_build_from_log_dir(slot) == BUILD_DEBUG
    assert detect_onehand_build_from_log_dir(logs.parent) == BUILD_DEBUG


def test_format_suffix() -> None:
    assert format_onehand_build_suffix(BUILD_DEBUG) == " (Debug)"
    assert format_onehand_build_suffix(BUILD_RELEASE) == " (Release)"
    assert format_onehand_build_suffix(None) == ""
    assert format_onehand_build_suffix("nope") == ""


def test_view_model_appends_debug_from_slotlog(tmp_path: Path) -> None:
    log_root = tmp_path / "Goldclub" / "var" / "log"
    slot_dir = log_root / "SlotLog"
    slot_dir.mkdir(parents=True)
    (slot_dir / "2026-09-11.log").write_text("\n".join(_DEBUG_BOOT) + "\n", encoding="utf-8")

    vm = IncidentViewModel()
    vm.refresh_current_software_version(log_root)
    assert vm.current_software_version == "v3.0.0.0"
    assert vm.onehand_build() == BUILD_DEBUG
    assert vm.software_version_for_ai().endswith(" (Debug)")


def test_resolve_slot_build_info_reads_db_banner(tmp_path: Path) -> None:
    from config_scanner.build_version import format_software_display, resolve_build_info
    from config_scanner.profiles import get_profile

    goldclub = tmp_path / "Goldclub"
    slot = goldclub / "slot"
    logs = goldclub / "var" / "log" / "SlotLog"
    slot.mkdir(parents=True)
    logs.mkdir(parents=True)
    for name in ("OneHand.exe", "game-start.exe", "GoldClub.Settings.dll"):
        (slot / name).write_bytes(b"stub")
    (logs / "2026-09-11.log").write_text("\n".join(_DEBUG_BOOT) + "\n", encoding="utf-8")

    info = resolve_build_info(get_profile("slot_lab_90"), str(slot))
    assert info.onehand_build == BUILD_DEBUG
    assert format_software_display(info).endswith(" (Debug)")
