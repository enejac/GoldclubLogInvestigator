"""Tests for elevated WinRM script runner (GoldClub SYSTEM task)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from automation.remote_exec import winrm_run_elevated_script


@dataclass
class _FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def test_winrm_run_elevated_uses_goldclub_self_elevate(monkeypatch) -> None:
    captured: dict = {}

    def _fake_inline(**kwargs):
        captured.update(kwargs)
        return _FakeResult(0, stdout="ELEVATED_OK\n")

    monkeypatch.setattr("automation.remote_exec.winrm_run_inline", _fake_inline)
    monkeypatch.setattr(
        "automation.remote_exec.require_lab_fleet_ip",
        lambda ip: ip,
    )
    monkeypatch.setattr(
        "automation.remote_exec.ensure_lab_winrm_trusted_hosts",
        lambda **_: None,
    )

    result = winrm_run_elevated_script(
        ip="10.0.0.111",
        remote_script_path=r"D:\usb_scripts\roulette\Run-FullStack.ps1",
        script_args=["-AlreadyElevated"],
        timeout=120,
    )

    assert result.returncode == 0
    assert captured["ip"] == "10.0.0.111"
    script = captured["script"]
    assert "Invoke-GoldClubSelfElevate" in script
    assert "GoldClubElevate.ps1" in script
    assert "$scriptPath" in script


def test_winrm_run_elevated_kill_all_delegates(monkeypatch) -> None:
    monkeypatch.setattr(
        "automation.cabinet_elevate.run_remote_kill_all_elevated",
        lambda host, timeout=180: (True, "Kill-All OK"),
    )
    monkeypatch.setattr(
        "automation.remote_exec.require_lab_fleet_ip",
        lambda ip: ip,
    )
    monkeypatch.setattr(
        "automation.remote_exec.ensure_lab_winrm_trusted_hosts",
        lambda **_: None,
    )

    result = winrm_run_elevated_script(
        ip="10.0.0.111",
        remote_script_path=r"D:\usb_scripts\roulette\Kill-All.ps1",
        timeout=120,
    )
    assert result.returncode == 0
    assert "Kill-All OK" in result.stdout


def test_stack_restart_remote_kill_uses_elevated(monkeypatch) -> None:
    from config_scanner.stack_restart import _run_remote_one

    monkeypatch.setattr(
        "automation.cabinet_elevate.run_remote_kill_all_elevated",
        lambda host, timeout=200: (True, "Kill-All OK"),
    )

    ok, detail = _run_remote_one(
        "10.0.0.111",
        r"D:\usb_scripts\roulette\Kill-All.ps1",
        label="Kill-All",
        allow_exit_1=True,
        timeout=90,
    )

    assert ok is True
    assert "Kill-All OK" in detail


def test_force_stop_remote_delegates_to_kill_all(monkeypatch) -> None:
    from network.ruleta_stack_probe import force_stop_blocking_processes

    monkeypatch.setattr(
        "network.ruleta_stack_probe._remote_kill_all",
        lambda host, timeout=180: (True, "Kill-All OK"),
    )

    ok, msg = force_stop_blocking_processes("10.0.0.111")
    assert ok is True
    assert msg == "Kill-All OK"
