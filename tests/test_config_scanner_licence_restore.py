"""Licence restore: live XML never overwritten; other dongles blocked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_scanner.machine_identity import (
    canonical_egm_serial,
    canonical_licensee_id,
    clear_error30_licence_leftovers,
    decide_licence_write,
    egm_serials_match,
    extra_error30_licence_roots,
    is_foreign_licence_xml,
    is_licence_dll_path,
    is_licence_path,
    licensee_id_from_licence_bytes,
    licensee_ids_match,
    live_has_licence,
    live_licence_licensee_id,
    serial_from_licence_bytes,
)
from config_scanner.scanner import (
    FileEntry,
    Manifest,
    archive_manifest_files,
    restore_manifest_files,
    restore_single_archived_file,
)
from config_scanner.service import ConfigScannerService


def _write_live_serial(root: Path, machine_name: str, serial: str) -> None:
    maint = root / "var" / "state" / "maintenance"
    maint.mkdir(parents=True)
    (maint / "ProductSerialNumber.json").write_text(
        json.dumps(
            {
                "MachineName": machine_name,
                "ProductSerialNumber": serial,
                "ProductKind": "RT",
            }
        ),
        encoding="utf-8",
    )


def _write_build_info(snap_dir: Path, *, machine_serial: str, game_drive: str) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "37556",
                "scanTimestamp": "2026-08-17T10:00:00",
                "gameDrive": game_drive,
                "profileId": "roulette_usb",
                "profileLabel": "Ruleta Alegro Wing",
                "machineSerial": machine_serial,
            }
        ),
        encoding="utf-8",
    )


def _licence_manifest(rel: str) -> Manifest:
    return Manifest(
        scanned_at="2026-08-17T10:00:00Z",
        file_count=1,
        elapsed_seconds=0.1,
        files=[FileEntry(rel, "a" * 40, 10, "2026-08-17T10:00:00Z")],
    )


def test_canonical_egm_serial_aliases() -> None:
    assert canonical_egm_serial("GRT330106") == "330106"
    assert canonical_egm_serial("ALLEGRO330106") == "330106"
    assert canonical_egm_serial("330106") == "330106"
    assert canonical_egm_serial("GST20664") == "20664"
    assert egm_serials_match("GRT330106", "ALLEGRO330106")
    assert egm_serials_match("GRT330106", "330106")
    assert not egm_serials_match("GRT330106", "GST20664")
    assert not egm_serials_match("GRT330106", None)
    assert not egm_serials_match("", "330106")


def test_is_licence_path_uk_and_us_spelling() -> None:
    assert is_licence_path("config/licences/abc.xml")
    assert is_licence_path("config/licenses/abc.xml")
    assert is_licence_path("slot/licenses/foo.xml")
    assert is_licence_path("slot/licence.dll")
    assert is_licence_path(
        "config/etc/application/licensing/xmlLicenceStorageSettings.xml"
    )
    assert is_licence_path("data/gcbackup/items/licence/licences.xml")
    assert is_licence_dll_path("ruleta/licence.dll")
    assert not is_licence_dll_path("config/licences/abc.xml")
    assert not is_licence_path("config/etc/application/ruleta/setup.xml")


def test_licensee_id_aliases() -> None:
    assert canonical_licensee_id("12-12262688") == "12262688"
    assert licensee_ids_match("12-12262688", "12262688")
    assert not licensee_ids_match("12-12262688", "12-12327444")
    assert (
        licensee_id_from_licence_bytes(
            b"<Licence><LicenseeId>12-12262688</LicenseeId></Licence>"
        )
        == "12262688"
    )


def test_decide_licence_write_never_overwrites_without_matching_wibu() -> None:
    allow, reason = decide_licence_write(
        dest_exists=True,
        live_has_any=True,
        live_serial="GRT330106",
        snapshot_serial="GRT330106",
        incoming_licence_serial="330106",
    )
    assert allow is False
    assert "already exists" in reason


def test_decide_licence_write_never_overwrites_same_wibu() -> None:
    allow, reason = decide_licence_write(
        dest_exists=True,
        live_has_any=True,
        live_serial="GRT330106",
        snapshot_serial="ALLEGRO330106",
        incoming_licence_serial="330106",
        incoming_licensee_id="12-12262688",
        live_licensee_id="12262688",
    )
    assert allow is False
    assert "never overwrites" in reason


def test_decide_licence_write_blocks_other_wibu() -> None:
    allow, reason = decide_licence_write(
        dest_exists=True,
        live_has_any=True,
        live_serial="GRT330106",
        snapshot_serial="GRT330106",
        incoming_licence_serial="330106",
        incoming_licensee_id="12-12327444",
        live_licensee_id="12-12262688",
    )
    assert allow is False
    assert "LicenseeId" in reason


def test_decide_licence_write_never_overwrites_existing_dll() -> None:
    allow, reason = decide_licence_write(
        dest_exists=True,
        live_has_any=True,
        live_serial="GRT330106",
        snapshot_serial="GRT330106",
        is_dll=True,
    )
    assert allow is False
    assert "never overwrites" in reason


def test_decide_licence_write_allows_missing_dll_same_serial() -> None:
    allow, _reason = decide_licence_write(
        dest_exists=False,
        live_has_any=True,
        live_serial="GRT330106",
        snapshot_serial="GRT330106",
        is_dll=True,
    )
    assert allow is True


def test_decide_licence_write_allows_missing_same_serial() -> None:
    allow, _reason = decide_licence_write(
        dest_exists=False,
        live_has_any=False,
        live_serial="GRT330106",
        snapshot_serial="ALLEGRO330106",
        incoming_licence_serial="330106",
    )
    assert allow is True


def test_decide_licence_write_refuses_unknown_or_other_serial() -> None:
    allow, _reason = decide_licence_write(
        dest_exists=False,
        live_has_any=False,
        live_serial="GRT330106",
        snapshot_serial="GST20664",
        incoming_licence_serial="330106",
    )
    assert allow is False
    allow, _reason = decide_licence_write(
        dest_exists=False,
        live_has_any=False,
        live_serial=None,
        snapshot_serial="GRT330106",
    )
    assert allow is False


def test_restore_missing_licence_same_serial(tmp_path: Path) -> None:
    target = tmp_path / "Goldclub"
    target.mkdir()
    _write_live_serial(target, "GRT330106", "330106")
    rel = "config/licences/test-licence.xml"
    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = _licence_manifest(rel)
    payload = b"<Licence><SerialNumber>330106</SerialNumber></Licence>"
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={rel: payload},
    )
    _write_build_info(snap_dir, machine_serial="GRT330106", game_drive=str(target))

    live = target / Path(rel)
    assert not live.is_file()
    assert not live_has_licence(target)

    result = restore_manifest_files(snap_dir, manifest, str(target))
    assert result.written_count == 1
    assert live.is_file()
    assert b"330106" in live.read_bytes()


def test_restore_missing_licence_other_serial_skipped(tmp_path: Path) -> None:
    target = tmp_path / "Goldclub"
    target.mkdir()
    _write_live_serial(target, "GST20664", "20664")
    rel = "config/licences/test-licence.xml"
    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = _licence_manifest(rel)
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={
            rel: b"<Licence><SerialNumber>330106</SerialNumber></Licence>"
        },
    )
    _write_build_info(snap_dir, machine_serial="GRT330106", game_drive=str(target))

    result = restore_manifest_files(snap_dir, manifest, str(target))
    assert result.written_count == 0
    assert result.skipped_count == 1
    assert not (target / Path(rel)).is_file()


def test_restore_does_not_refresh_same_wibu_licence(tmp_path: Path) -> None:
    target = tmp_path / "Goldclub"
    target.mkdir()
    _write_live_serial(target, "GRT330106", "330106")
    rel = "config/licences/37A55022DCBEF351AE27471D181B1EF5.xml"
    live = target / Path(rel)
    live.parent.mkdir(parents=True)
    live.write_bytes(
        b"<Licence><SerialNumber>330106</SerialNumber>"
        b"<LicenseeId>12-12262688</LicenseeId><Keep>old</Keep></Licence>"
    )
    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = _licence_manifest(rel)
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={
            rel: (
                b"<Licence><SerialNumber>330106</SerialNumber>"
                b"<LicenseeId>12-12262688</LicenseeId><Keep>new</Keep></Licence>"
            )
        },
    )
    _write_build_info(snap_dir, machine_serial="GRT330106", game_drive=str(target))

    result = restore_manifest_files(snap_dir, manifest, str(target))
    assert result.written_count == 0
    assert b"<Keep>old</Keep>" in live.read_bytes()
    assert b"<Keep>new</Keep>" not in live.read_bytes()


def test_restore_does_not_add_second_licence_when_one_exists(tmp_path: Path) -> None:
    target = tmp_path / "Goldclub"
    target.mkdir()
    _write_live_serial(target, "GRT330106", "330106")
    existing = target / "config" / "licences" / "live.xml"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "<Licence><SerialNumber>330106</SerialNumber></Licence>",
        encoding="utf-8",
    )
    rel = "config/licences/other.xml"
    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = _licence_manifest(rel)
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={
            rel: b"<Licence><SerialNumber>330106</SerialNumber></Licence>"
        },
    )
    _write_build_info(snap_dir, machine_serial="GRT330106", game_drive=str(target))

    result = restore_manifest_files(snap_dir, manifest, str(target))
    assert result.written_count == 0
    assert not (target / Path(rel)).is_file()
    assert existing.is_file()


def test_restore_single_licence_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "Goldclub"
    rel = "config/licences/test-licence.xml"
    live = target / Path(rel)
    live.parent.mkdir(parents=True)
    live.write_text(
        "<Licence><SerialNumber>330106</SerialNumber></Licence>",
        encoding="utf-8",
    )
    _write_live_serial(target, "GRT330106", "330106")
    snap_dir = tmp_path / "snapshots" / "snap_a"
    manifest = _licence_manifest(rel)
    archive_manifest_files(
        str(target),
        manifest,
        snap_dir,
        content_overrides={
            rel: b"<Licence><SerialNumber>19737</SerialNumber></Licence>"
        },
    )
    _write_build_info(snap_dir, machine_serial="GRT330106", game_drive=str(target))

    with pytest.raises(ValueError, match="serial does not match"):
        restore_single_archived_file(snap_dir, rel, str(target))
    assert "330106" in live.read_text(encoding="utf-8")
    assert "19737" not in live.read_text(encoding="utf-8")


def test_apply_snapshot_restores_missing_licence_same_serial(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    (slot_root / "mgconfig.xml").write_text("<mutated/>", encoding="utf-8")
    _write_live_serial(slot_root, "GST20664", "20664")

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = tool_root / "snapshots" / "slot_snap"
    licence_rel = "config/licences/cab.xml"
    mg_rel = "mgconfig.xml"
    files_root = snap_dir / "files"
    (files_root / "mgconfig.xml").parent.mkdir(parents=True)
    (files_root / "mgconfig.xml").write_text("<original/>", encoding="utf-8")
    lic_path = files_root / Path(licence_rel)
    lic_path.parent.mkdir(parents=True, exist_ok=True)
    lic_path.write_bytes(b"<Licence><SerialNumber>20664</SerialNumber></Licence>")
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "2026-08-17T10:00:00",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot lab",
                "machineSerial": "GST20664",
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
                        "relativePath": mg_rel,
                        "sha1": "AAA",
                        "sizeBytes": 11,
                        "lastWriteUtc": "t",
                    },
                    {
                        "relativePath": licence_rel,
                        "sha1": "BBB",
                        "sizeBytes": 20,
                        "lastWriteUtc": "t",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("slot_snap", str(slot_root))
    assert result.written_count == 2
    assert (slot_root / "mgconfig.xml").read_text(encoding="utf-8") == "<original/>"
    live_lic = slot_root / Path(licence_rel)
    assert live_lic.is_file()
    assert serial_from_licence_bytes(live_lic.read_bytes()) == "20664"


def test_apply_snapshot_does_not_refresh_same_wibu_licence(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    (slot_root / "mgconfig.xml").write_text("<mutated/>", encoding="utf-8")
    _write_live_serial(slot_root, "GST20664", "20664")
    licence_rel = "config/licences/cab.xml"
    live_lic = slot_root / Path(licence_rel)
    live_lic.parent.mkdir(parents=True)
    live_lic.write_text(
        "<Licence><SerialNumber>20664</SerialNumber>"
        "<LicenseeId>12-12262688</LicenseeId><Keep>yes</Keep></Licence>",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = tool_root / "snapshots" / "slot_snap"
    files_root = snap_dir / "files"
    (files_root / "mgconfig.xml").parent.mkdir(parents=True)
    (files_root / "mgconfig.xml").write_text("<original/>", encoding="utf-8")
    snap_lic = files_root / Path(licence_rel)
    snap_lic.parent.mkdir(parents=True, exist_ok=True)
    snap_lic.write_bytes(
        b"<Licence><SerialNumber>20664</SerialNumber>"
        b"<LicenseeId>12-12262688</LicenseeId><Keep>no</Keep></Licence>"
    )
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "2026-08-17T10:00:00",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot lab",
                "machineSerial": "GST20664",
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
                        "relativePath": "mgconfig.xml",
                        "sha1": "AAA",
                        "sizeBytes": 11,
                        "lastWriteUtc": "t",
                    },
                    {
                        "relativePath": licence_rel,
                        "sha1": "BBB",
                        "sizeBytes": 20,
                        "lastWriteUtc": "t",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("slot_snap", str(slot_root))
    assert result.written_count == 1
    assert "<Keep>yes</Keep>" in live_lic.read_text(encoding="utf-8")
    assert "<Keep>no</Keep>" not in live_lic.read_text(encoding="utf-8")


def test_apply_snapshot_does_not_overwrite_existing_licence(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    (slot_root / "mgconfig.xml").write_text("<mutated/>", encoding="utf-8")
    _write_live_serial(slot_root, "GST20664", "20664")
    licence_rel = "config/licences/cab.xml"
    live_lic = slot_root / Path(licence_rel)
    live_lic.parent.mkdir(parents=True)
    live_lic.write_text(
        "<Licence><SerialNumber>20664</SerialNumber><Keep>yes</Keep></Licence>",
        encoding="utf-8",
    )
    # existing live XML must stay byte-identical after restore

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = tool_root / "snapshots" / "slot_snap"
    files_root = snap_dir / "files"
    (files_root / "mgconfig.xml").parent.mkdir(parents=True)
    (files_root / "mgconfig.xml").write_text("<original/>", encoding="utf-8")
    snap_lic = files_root / Path(licence_rel)
    snap_lic.parent.mkdir(parents=True, exist_ok=True)
    snap_lic.write_bytes(b"<Licence><SerialNumber>20664</SerialNumber><Keep>no</Keep></Licence>")
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "2026-08-17T10:00:00",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot lab",
                "machineSerial": "GST20664",
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
                        "relativePath": "mgconfig.xml",
                        "sha1": "AAA",
                        "sizeBytes": 11,
                        "lastWriteUtc": "t",
                    },
                    {
                        "relativePath": licence_rel,
                        "sha1": "BBB",
                        "sizeBytes": 20,
                        "lastWriteUtc": "t",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target("slot_snap", str(slot_root))
    assert result.written_count == 1
    assert "<Keep>yes</Keep>" in live_lic.read_text(encoding="utf-8")
    assert "<Keep>no</Keep>" not in live_lic.read_text(encoding="utf-8")


_LIVE_LIC = (
    b"<Licence><SerialNumber>330106</SerialNumber>"
    b"<LicenseeId>12-12262688</LicenseeId></Licence>"
)
_USB_LIC = (
    b"<Licence><SerialNumber>330106</SerialNumber>"
    b"<LicenseeId>12-12327444</LicenseeId></Licence>"
)


def test_foreign_licence_is_usb_leftover_only() -> None:
    assert is_foreign_licence_xml(Path("6051106A90E561F9F08CDEEBB5C3F6E2.xml"))
    assert not is_foreign_licence_xml(Path("37A55022DCBEF351AE27471D181B1EF5.xml"))


def test_extra_error30_licence_roots_unc() -> None:
    roots = extra_error30_licence_roots(Path(r"\\10.0.0.111\slot"))
    texts = [str(p) for p in roots]
    assert any("USB" in t and "GoldClub" in t for t in texts)
    assert any("USB_Remote" in t for t in texts)


def test_extra_error30_licence_roots_skip_workstation_temp(tmp_path: Path) -> None:
    assert extra_error30_licence_roots(tmp_path) == []


def test_clear_error30_keeps_live_when_leftover_listed_first(tmp_path: Path) -> None:
    licences = tmp_path / "config" / "licences"
    licences.mkdir(parents=True)
    leftover = licences / "6051106A90E561F9F08CDEEBB5C3F6E2.xml"
    live = licences / "37A55022DCBEF351AE27471D181B1EF5.xml"
    leftover.write_bytes(_USB_LIC)
    live.write_bytes(_LIVE_LIC)
    bios = tmp_path / "bios" / "licences"
    bios.mkdir(parents=True)
    bios_leftover = bios / "6051106A90E561F9F08CDEEBB5C3F6E2.xml"
    bios_leftover.write_bytes(_USB_LIC)
    dll = tmp_path / "ruleta" / "licence.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"wibu")

    assert live_licence_licensee_id(tmp_path) == "12262688"
    extra = tmp_path / "GoldClub" / "Licenses"
    extra.mkdir(parents=True)
    usb_copy = extra / "6051106A90E561F9F08CDEEBB5C3F6E2.xml"
    usb_copy.write_bytes(_USB_LIC)

    removed = clear_error30_licence_leftovers(tmp_path, extra_roots=[extra])
    assert leftover.is_file() is False
    assert bios_leftover.is_file() is False
    assert usb_copy.is_file() is False
    assert live.is_file()
    assert live.read_bytes() == _LIVE_LIC
    assert dll.is_file()
    assert len(removed) == 3


def test_clear_error30_does_not_delete_live_only(tmp_path: Path) -> None:
    licences = tmp_path / "config" / "licences"
    licences.mkdir(parents=True)
    live = licences / "37A55022DCBEF351AE27471D181B1EF5.xml"
    live.write_bytes(_LIVE_LIC)
    removed = clear_error30_licence_leftovers(tmp_path)
    assert removed == []
    assert live.is_file()


def test_apply_snapshot_warnings_say_licence_not_written(tmp_path: Path) -> None:
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    (slot_root / "OneHand.exe").write_bytes(b"onehand")
    (slot_root / "game-start.exe").write_bytes(b"start")
    (slot_root / "GoldClub.Settings.dll").write_bytes(b"dll")
    (slot_root / "mgconfig.xml").write_text("<mutated/>", encoding="utf-8")
    _write_live_serial(slot_root, "GST20664", "20664")
    licence_rel = "config/licences/cab.xml"
    live_lic = slot_root / Path(licence_rel)
    live_lic.parent.mkdir(parents=True)
    live_lic.write_text(
        "<Licence><SerialNumber>20664</SerialNumber>"
        "<LicenseeId>12-12262688</LicenseeId></Licence>",
        encoding="utf-8",
    )

    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    (tool_root / "config.json").write_text(
        json.dumps(
            {
                "gameDrive": str(slot_root),
                "buildVersionRelativePath": "ruleta/BuildVersion.txt",
                "scanRoots": ["."],
                "includePatterns": ["*.xml"],
                "parallelWorkers": 1,
                "snapshotsDir": "snapshots",
                "reportsDir": "reports",
            }
        ),
        encoding="utf-8",
    )
    snap_dir = tool_root / "snapshots" / "slot_snap"
    files_root = snap_dir / "files"
    (files_root / "mgconfig.xml").parent.mkdir(parents=True)
    (files_root / "mgconfig.xml").write_text("<original/>", encoding="utf-8")
    snap_lic = files_root / Path(licence_rel)
    snap_lic.parent.mkdir(parents=True, exist_ok=True)
    snap_lic.write_bytes(
        b"<Licence><SerialNumber>20664</SerialNumber>"
        b"<LicenseeId>12-12262688</LicenseeId></Licence>"
    )
    (snap_dir / "build-info.json").write_text(
        json.dumps(
            {
                "buildNumber": "1",
                "scanTimestamp": "2026-08-17T10:00:00",
                "gameDrive": str(slot_root),
                "profileId": "slot_lab_90",
                "profileLabel": "Slot lab",
                "machineSerial": "GST20664",
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
                        "relativePath": "mgconfig.xml",
                        "sha1": "AAA",
                        "sizeBytes": 11,
                        "lastWriteUtc": "t",
                    },
                    {
                        "relativePath": licence_rel,
                        "sha1": "BBB",
                        "sizeBytes": 20,
                        "lastWriteUtc": "t",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    service = ConfigScannerService(tool_root)
    warnings = service.snapshot_apply_warnings("slot_snap", str(slot_root))
    assert any("will not be written" in item for item in warnings)
    assert not any("will refresh" in item.lower() for item in warnings)
