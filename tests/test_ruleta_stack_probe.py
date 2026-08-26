"""Tests for Ruleta stack probes used before software swap."""

from __future__ import annotations

from pathlib import Path

import pytest

from network.ruleta_stack_probe import (
    dest_ruleta_file_locked,
    dest_ruleta_swap_writable,
    preflight_remote_software_swap,
    verify_stack_clear_for_swap,
)


def test_dest_ruleta_swap_writable_ok(tmp_path: Path) -> None:
    ruleta = tmp_path / "goldclub" / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    ok, msg = dest_ruleta_swap_writable(ruleta)
    assert ok is True
    assert msg is None


def test_dest_ruleta_file_locked_when_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    ruleta = tmp_path / "ruleta"
    lib = ruleta / "lib"
    lib.mkdir(parents=True)
    dll = lib / "RouletteWebApiModels.dll"
    dll.write_bytes(b"MZ")

    def _boom(_src: Path, _dst: Path) -> None:
        raise OSError(32, "The process cannot access the file")

    monkeypatch.setattr("network.ruleta_stack_probe.shutil.copy2", _boom)
    assert dest_ruleta_file_locked(ruleta) is True


def test_verify_stack_clear_fails_when_dll_locked(tmp_path: Path, monkeypatch) -> None:
    ruleta = tmp_path / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    monkeypatch.setattr(
        "network.ruleta_stack_probe.probe_blocking_processes",
        lambda _host: ("nginx",),
    )
    monkeypatch.setattr(
        "network.ruleta_stack_probe.dest_ruleta_file_locked",
        lambda _dest: True,
    )
    ok, detail = verify_stack_clear_for_swap("10.0.0.111", ruleta)
    assert ok is False
    assert "locked" in detail.casefold() or "Still running" in detail


def test_dest_ruleta_swap_writable_skips_lock_when_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    ruleta = tmp_path / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    monkeypatch.setattr(
        "network.ruleta_stack_probe.dest_ruleta_file_locked",
        lambda _dest: True,
    )
    ok, msg = dest_ruleta_swap_writable(ruleta, check_dll_lock=False)
    assert ok is True
    assert msg is None


def test_preflight_remote_refuses_locked_dll(tmp_path: Path, monkeypatch) -> None:
    ruleta = tmp_path / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    monkeypatch.setattr(
        "network.lab_access.require_lab_fleet_ip",
        lambda host: host,
    )
    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential",
        lambda _host: True,
    )
    monkeypatch.setattr(
        "network.ruleta_stack_probe.dest_ruleta_file_locked",
        lambda _dest: True,
    )

    refuses = preflight_remote_software_swap("10.0.0.111", ruleta)
    assert refuses
    assert any("locked" in item.casefold() for item in refuses)


def test_preflight_remote_allows_when_lock_deferred(tmp_path: Path, monkeypatch) -> None:
    ruleta = tmp_path / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    monkeypatch.setattr(
        "network.lab_access.require_lab_fleet_ip",
        lambda host: host,
    )
    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential",
        lambda _host: True,
    )
    monkeypatch.setattr(
        "network.ruleta_stack_probe.dest_ruleta_file_locked",
        lambda _dest: True,
    )
    refuses = preflight_remote_software_swap(
        "10.0.0.111", ruleta, defer_lock_check=True
    )
    assert refuses == ()


def test_preflight_remote_allows_when_clear(tmp_path: Path, monkeypatch) -> None:
    ruleta = tmp_path / "ruleta"
    (ruleta / "lib").mkdir(parents=True)
    monkeypatch.setattr(
        "network.lab_access.require_lab_fleet_ip",
        lambda host: host,
    )
    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential",
        lambda _host: True,
    )
    monkeypatch.setattr(
        "network.ruleta_stack_probe.probe_blocking_processes",
        lambda _host: (),
    )
    refuses = preflight_remote_software_swap("10.0.0.111", ruleta)
    assert refuses == ()
