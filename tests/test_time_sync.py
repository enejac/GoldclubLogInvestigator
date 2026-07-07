"""Remote time sync via PsExec (mocked)."""

from __future__ import annotations

import base64
import subprocess

import pytest

from network import time_sync


def test_force_remote_time_sync_missing_psexec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: None)
    monkeypatch.setattr("os.name", "nt")
    ok, msg = time_sync.force_remote_time_sync("10.0.0.1")
    assert ok is False
    assert "not found" in msg.lower()
    assert "sysinternals" in msg.lower()


def test_force_remote_time_sync_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "posix")
    ok, msg = time_sync.force_remote_time_sync("10.0.0.1")
    assert ok is False
    assert "windows" in msg.lower()


def test_force_remote_time_sync_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("os.name", "nt")
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    n = [0]

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        n[0] += 1
        class R:
            returncode = 0
            stderr = ""
            stdout = "Central Europe Standard Time\n" if n[0] >= 2 else ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is True
    assert "success" in msg.lower()
    assert n[0] == 2


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


def test_force_remote_time_sync_uses_powershell_set_timezone(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("os.name", "nt")
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    captured: list[list[str]] = []
    n = [0]

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        captured.append(cmd)
        n[0] += 1
        class R:
            returncode = 0
            stderr = ""
            stdout = "Central Europe Standard Time\n" if n[0] >= 2 else ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is True
    assert len(captured) == 2
    ps_cmd = captured[0]
    assert "-nobanner" in ps_cmd
    assert "powershell.exe" in ps_cmd
    i = ps_cmd.index("-EncodedCommand")
    enc = ps_cmd[i + 1]
    script = base64.b64decode(enc).decode("utf-16-le")
    assert "Set-TimeZone" in script
    assert "Central Europe Standard Time" in script
    assert "DynamicDaylightTimeDisabled" in script
    assert "-Type DWord" in script
    assert "w32tm /resync /force" in script
    assert "tzutil /g" in " ".join(captured[1])


def test_force_remote_time_sync_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("os.name", "nt")
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        class R:
            returncode = 1
            stderr = "Access denied"
            stdout = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "denied" in msg.lower()


def test_force_remote_time_sync_verify_mismatch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("os.name", "nt")
    exe = tmp_path / "psexec.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(time_sync, "_resolve_psexec_path", lambda: str(exe))
    monkeypatch.setattr(
        time_sync,
        "_local_windows_timezone_id",
        lambda: "Central Europe Standard Time",
    )
    n = [0]

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        n[0] += 1
        out = "GMT Standard Time\n" if n[0] >= 2 else ""

        class R:
            returncode = 0
            stderr = ""
            stdout = out

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, msg = time_sync.force_remote_time_sync("10.0.0.90")
    assert ok is False
    assert "gmt standard time" in msg.lower() or "different" in msg.lower()
