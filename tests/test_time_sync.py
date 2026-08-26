"""Remote time sync via WinRM (preferred) / PsExec (mocked)."""

from __future__ import annotations

import base64
import subprocess

import pytest

from network import time_sync


def _patch_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        time_sync,
        "preflight_remote_time_sync",
        lambda ip: (True, ""),
    )


def _patch_winrm_fail(monkeypatch: pytest.MonkeyPatch, detail: str = "WinRM unavailable") -> None:
    monkeypatch.setattr(
        time_sync,
        "_try_winrm_sync",
        lambda ip, script: (False, detail, None),
    )


def test_force_remote_time_sync_missing_psexec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: None)
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    _patch_winrm_fail(monkeypatch)
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "not found" in msg.lower()
    assert "sysinternals" in msg.lower() or "winrm" in msg.lower()


def test_force_remote_time_sync_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "posix")
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "windows" in msg.lower()


def test_force_remote_time_sync_winrm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    monkeypatch.setattr(
        time_sync,
        "_try_winrm_sync",
        lambda ip, script: (
            True,
            "SYNC_OK zone=Central Europe Standard Time drift_sec=0.2 clock_source=GoldClub.NTP+Set-Date",
            {
                "zone": "Central Europe Standard Time",
                "drift": "0.2",
                "source": "GoldClub.NTP+Set-Date",
            },
        ),
    )
    # PsExec must not be required when WinRM works.
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: None)
    ok, msg, drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is True
    assert drift == pytest.approx(0.2)
    assert "winrm" in msg.lower()
    assert "goldclub.ntp" in msg.lower()


def test_force_remote_time_sync_psexec_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    _patch_winrm_fail(monkeypatch)

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        class R:
            returncode = 0
            stderr = ""
            stdout = (
                "SYNC_OK zone=Central Europe Standard Time "
                "drift_sec=0.1 clock_source=GoldClub.NTP+Set-Date\n"
            )

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is True
    assert "psexec" in msg.lower()
    assert "synchronized" in msg.lower()


def test_tzutil_zone_from_psexec_output_skips_banner() -> None:
    noisy = (
        "PsExec v2.43 - Execute processes remotely\r\n"
        "Connecting to 10.0.0.90...\r\n"
        "Central Europe Standard Time\r\n"
    )
    assert (
        time_sync._tzutil_zone_from_psexec_output(noisy, "")
        == "Central Europe Standard Time"
    )
    assert (
        time_sync._tzutil_zone_from_psexec_output("", noisy)
        == "Central Europe Standard Time"
    )


def test_parse_sync_ok_line() -> None:
    meta = time_sync._parse_sync_ok(
        "SYNC_OK zone=Central Europe Standard Time drift_sec=-0.3 clock_source=GoldClub.NTP+Set-Date\n"
    )
    assert meta is not None
    assert meta["zone"] == "Central Europe Standard Time"
    assert meta["drift"] == "-0.3"
    assert meta["source"] == "GoldClub.NTP+Set-Date"


def test_local_windows_timezone_id_parses_tzutil(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "nt")

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        assert cmd[:2] == ["tzutil", "/g"]

        class R:
            returncode = 0
            stdout = "Central Europe Standard Time\r\n"
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert time_sync._local_windows_timezone_id() == "Central Europe Standard Time"


def test_force_remote_time_sync_uses_set_date_and_goldclub_ntp(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    _patch_winrm_fail(monkeypatch)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        captured.append(cmd)

        class R:
            returncode = 0
            stderr = ""
            stdout = (
                "SYNC_OK zone=Central Europe Standard Time "
                "drift_sec=0.0 clock_source=GoldClub.NTP+Set-Date\n"
            )

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is True
    assert len(captured) == 1
    ps_cmd = captured[0]
    assert "-nobanner" in ps_cmd
    assert "-u" in ps_cmd
    assert "powershell.exe" in ps_cmd
    i = ps_cmd.index("-EncodedCommand")
    enc = ps_cmd[i + 1]
    script = base64.b64decode(enc).decode("utf-16-le")
    assert "Set-TimeZone" in script
    assert "Set-Date" in script
    assert "GoldClub.NTP" in script
    assert "Central Europe Standard Time" in script
    assert "DynamicDaylightTimeDisabled" in script
    assert "-Type DWord" in script
    assert "w32tm /resync /force" in script


def test_winrm_failure_hint_logon_session() -> None:
    detail = time_sync._winrm_failure_hint(
        "0x8009030e logon session does not exist"
    )
    assert "Initialize-LabAccess" in detail


def test_winrm_failure_hint_registry() -> None:
    detail = time_sync._winrm_failure_hint("Requested registry access is not allowed")
    assert "administrator" in detail.lower()


def test_preflight_remote_time_sync_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "nt")
    import network.fleet_scanner as fs

    monkeypatch.setattr(fs, "ping_host", lambda ip: False)
    monkeypatch.setattr(fs, "probe_smb_port", lambda ip: False)
    monkeypatch.setattr(time_sync, "probe_tcp_port", lambda ip, port, **kw: False)
    monkeypatch.setattr(time_sync, "ensure_lab_smb_credential", lambda ip: True)

    ok, msg = time_sync.preflight_remote_time_sync("10.0.0.111")
    assert ok is False
    assert "lab lan" in msg.lower() or "lab LAN" in msg
    assert "internet" in msg.lower()


def test_preflight_remote_time_sync_winrm_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "nt")
    import network.fleet_scanner as fs

    monkeypatch.setattr(fs, "ping_host", lambda ip: True)
    monkeypatch.setattr(fs, "probe_smb_port", lambda ip: True)
    monkeypatch.setattr(time_sync, "probe_tcp_port", lambda ip, port, **kw: True)
    monkeypatch.setattr(time_sync, "ensure_lab_smb_credential", lambda ip: True)

    ok, msg = time_sync.preflight_remote_time_sync("10.0.0.111")
    assert ok is True
    assert msg == ""


def test_force_remote_time_sync_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    _patch_winrm_fail(monkeypatch)

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        class R:
            returncode = 1
            stderr = "Access denied"
            stdout = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "denied" in msg.lower()


def test_force_remote_time_sync_verify_mismatch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("os.name", "nt")
    _patch_preflight_ok(monkeypatch)
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    _patch_winrm_fail(monkeypatch)
    n = [0]

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        n[0] += 1
        # First call: sync script with no SYNC_OK; second: tzutil /g mismatch
        out = "GMT Standard Time\n" if n[0] >= 2 else "done\n"

        class R:
            returncode = 0
            stderr = ""
            stdout = out

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg, _drift = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "gmt standard time" in msg.lower() or "different" in msg.lower()
