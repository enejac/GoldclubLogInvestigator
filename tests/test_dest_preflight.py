"""Tests for scan-target writability preflight."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from config_scanner.dest_preflight import (
    cabinet_ip_from_game_drive,
    goldclub_dest_writable,
    restore_target_hint,
)


def test_goldclub_dest_writable_ok(tmp_path: Path) -> None:
    root = tmp_path / "goldclub"
    (root / "ruleta").mkdir(parents=True)
    ok, msg = goldclub_dest_writable(root)
    assert ok is True
    assert msg is None


def test_goldclub_dest_writable_missing(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    ok, msg = goldclub_dest_writable(root)
    assert ok is False
    assert msg is not None
    assert "does not exist" in msg


def test_goldclub_dest_writable_read_only(tmp_path: Path) -> None:
    root = tmp_path / "goldclub"
    root.mkdir()
    if os.name != "nt":
        root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    else:
        pytest.skip("Windows ACL read-only probe is environment-specific")

    ok, msg = goldclub_dest_writable(root)
    assert ok is False
    assert msg is not None
    assert "read-only" in msg.casefold() or "Cannot write" in msg


def test_goldclub_dest_writable_errno_19(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "goldclub"
    root.mkdir()

    def _boom(_self: Path, *args: object, **kwargs: object) -> None:
        raise OSError(19, "The media is write protected")

    monkeypatch.setattr(Path, "write_text", _boom)
    ok, msg = goldclub_dest_writable(root)
    assert ok is False
    assert msg is not None
    assert "read-only" in msg.casefold()
    assert "10.0.0.111" in msg


def test_cabinet_ip_from_game_drive() -> None:
    assert cabinet_ip_from_game_drive(r"\\10.0.0.111\slot") == "10.0.0.111"
    assert cabinet_ip_from_game_drive("G:\\Goldclub") is None


def test_restore_target_hint_serial_mismatch() -> None:
    hint = restore_target_hint(
        snapshot_game_drive=r"\\10.0.0.111\slot",
        live_serial="GRT0157",
        snapshot_serial="GRT330106",
    )
    assert hint is not None
    assert "10.0.0.111" in hint


def test_restore_target_hint_same_serial() -> None:
    assert (
        restore_target_hint(
            snapshot_game_drive=r"\\10.0.0.111\slot",
            live_serial="GRT330106",
            snapshot_serial="GRT330106",
        )
        is None
    )


def test_restore_scan_target_candidates() -> None:
    from config_scanner.dest_preflight import restore_scan_target_candidates

    cands = restore_scan_target_candidates(r"\\10.0.0.111\slot")
    assert cands[0].casefold().endswith(r"\slot")
    assert any("10.0.0.111" in item for item in cands)


def test_resolve_restore_scan_target_prefers_cabinet(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.dest_preflight import resolve_restore_scan_target

    local = tmp_path / "goldclub"
    (local / "ruleta").mkdir(parents=True)
    serial = local / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    serial.parent.mkdir(parents=True)
    serial.write_text('{"MachineName":"GRT0157"}', encoding="utf-8")

    remote = tmp_path / "remote330106"
    (remote / "ruleta").mkdir(parents=True)
    remote_serial = remote / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    remote_serial.parent.mkdir(parents=True)
    remote_serial.write_text('{"MachineName":"GRT330106"}', encoding="utf-8")

    monkeypatch.setattr(
        "config_scanner.dest_preflight.restore_scan_target_candidates",
        lambda game_drive, cabinet_ip=None: (str(remote),),
    )
    monkeypatch.setattr(
        "config_scanner.dest_preflight.ensure_lab_smb_credential",
        lambda _ip: True,
        raising=False,
    )

    resolved, note = resolve_restore_scan_target(
        str(local),
        snapshot_game_drive=r"\\10.0.0.111\slot",
        snapshot_serial="GRT330106",
    )
    assert resolved == str(remote)
    assert note is not None
    assert "switched" in note.casefold()


def test_resolve_restore_keeps_local_when_running_on_that_egm(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.dest_preflight import resolve_restore_scan_target

    local = tmp_path / "goldclub"
    (local / "ruleta").mkdir(parents=True)
    serial = local / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    serial.parent.mkdir(parents=True)
    serial.write_text('{"MachineName":"GRT330106"}', encoding="utf-8")
    monkeypatch.setenv("COMPUTERNAME", "GRT330106")
    resolved, note = resolve_restore_scan_target(
        str(local),
        snapshot_game_drive=r"\\10.0.0.111\slot",
        snapshot_serial="GRT330106",
    )
    assert resolved.rstrip("\\") == str(local).rstrip("\\")
    assert note is None


def test_resolve_restore_switches_workstation_goldclub_matching_serial(
    monkeypatch, tmp_path: Path
) -> None:
    from config_scanner.dest_preflight import resolve_restore_scan_target

    local = tmp_path / "goldclub"
    (local / "ruleta").mkdir(parents=True)
    serial = local / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    serial.parent.mkdir(parents=True)
    serial.write_text('{"MachineName":"GRT330106"}', encoding="utf-8")
    remote = tmp_path / "remote330106"
    (remote / "ruleta").mkdir(parents=True)
    remote_serial = remote / "var" / "state" / "maintenance" / "ProductSerialNumber.json"
    remote_serial.parent.mkdir(parents=True)
    remote_serial.write_text('{"MachineName":"GRT330106"}', encoding="utf-8")
    monkeypatch.setenv("COMPUTERNAME", "ENEJZBOGAR")
    monkeypatch.setattr(
        "config_scanner.dest_preflight.restore_scan_target_candidates",
        lambda game_drive, cabinet_ip=None: (str(remote),),
    )
    monkeypatch.setattr(
        "config_scanner.dest_preflight.ensure_lab_smb_credential",
        lambda _ip: True,
        raising=False,
    )
    resolved, note = resolve_restore_scan_target(
        str(local),
        snapshot_game_drive=r"\\10.0.0.111\slot",
        snapshot_serial="GRT330106",
    )
    assert resolved == str(remote)
    assert note is not None
    assert "switched" in note.casefold()
