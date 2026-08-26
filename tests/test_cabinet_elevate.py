"""Tests for cabinet elevation bootstrap (workgroup / session-0 WinRM)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_push_elevate_scripts_copies_usb_files(tmp_path: Path, monkeypatch) -> None:
    from automation import cabinet_elevate as ce

    host = "10.0.0.111"
    usb = tmp_path / "USB" / "usb_scripts" / "roulette"
    usb.mkdir(parents=True)
    usb_root = tmp_path / "USB"
    onstart = tmp_path / "slot" / "platform" / "system" / "init" / "onstart.d"
    onstart.mkdir(parents=True)

    monkeypatch.setattr(ce, "_usb_roulette_unc", lambda _ip: usb)
    monkeypatch.setattr(ce, "_usb_root_unc", lambda _ip: usb_root)
    monkeypatch.setattr(ce, "_platform_onstart_unc", lambda _ip: onstart / "91-EnableShareAndWinRM.ps1")
    monkeypatch.setattr(
        "network.lab_access.ensure_lab_smb_credential",
        lambda _host: True,
    )
    monkeypatch.setattr(
        "network.lab_access.require_lab_fleet_ip",
        lambda ip: ip,
    )

    ce.push_elevate_scripts(host)
    assert (usb / "GoldClubElevate.ps1").is_file()
    assert (usb / "Kill-All.ps1").is_file()
    assert (usb / "GCI-ELEVATE-NOW.cmd").is_file()
    assert (usb / "Bootstrap-Elevate-111.ps1").is_file()
    assert (usb_root / "GCI-ELEVATE-NOW.cmd").is_file()
    assert (usb_root / "Bootstrap-Elevate-111.ps1").is_file()


def test_ensure_cabinet_kill_ready_returns_bootstrap_hint(monkeypatch) -> None:
    from automation import cabinet_elevate as ce

    monkeypatch.setattr(ce, "push_elevate_scripts", lambda _ip: Path("."))
    monkeypatch.setattr(ce, "elevate_task_registered", lambda _ip: False)

    ok, detail = ce.ensure_cabinet_kill_ready("10.0.0.111")
    assert ok is False
    assert "GCI-ELEVATE-NOW.cmd" in detail


def test_remote_kill_all_delegates_to_elevated(monkeypatch) -> None:
    from network.ruleta_stack_probe import _remote_kill_all

    monkeypatch.setattr(
        "automation.cabinet_elevate.run_remote_kill_all_elevated",
        lambda host, timeout=180: (True, "ok"),
    )
    ok, msg = _remote_kill_all("10.0.0.111")
    assert ok is True
    assert msg == "ok"
