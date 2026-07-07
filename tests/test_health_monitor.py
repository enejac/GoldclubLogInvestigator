"""WMIC memory stats parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from network import health_monitor
from PySide6.QtGui import QPalette

from gui.palette_adapt import is_probable_leak, memory_usage_color


def test_parse_wmic_value_block() -> None:
    text = """
FreePhysicalMemory=1048576

TotalVisibleMemorySize=2097152

"""
    d = health_monitor._parse_wmic_value_block(text)
    assert d["FreePhysicalMemory"] == 1_048_576
    assert d["TotalVisibleMemorySize"] == 2_097_152


def test_parse_working_set_bytes_sum() -> None:
    text = "WorkingSetSize=1048576\r\nWorkingSetSize=1048576\r\n"
    assert health_monitor._parse_working_set_bytes_sum(text) == 2_097_152
    assert health_monitor._parse_working_set_bytes_sum("No Instance(s) Available.\r\n") is None


def test_get_remote_memory_stats_from_stdout() -> None:
    out_os = "FreePhysicalMemory=512000\r\nTotalVisibleMemorySize=1024000\r\n\r\n"
    out_proc = "WorkingSetSize=2147483648\r\n"
    fake_os = MagicMock()
    fake_os.returncode = 0
    fake_os.stdout = out_os
    fake_os.stderr = ""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = out_proc
    fake_proc.stderr = ""

    def run_side_effect(cmd: list[str], **_kwargs: object) -> MagicMock:
        if len(cmd) >= 3 and cmd[2] == "OS":
            return fake_os
        return fake_proc

    with patch.object(health_monitor.subprocess, "run", side_effect=run_side_effect):
        with patch.object(health_monitor.os, "name", "nt"):
            stats = health_monitor.get_remote_memory_stats("10.0.0.1")
    assert stats is not None
    assert stats["total_mb"] == 1000.0
    assert stats["free_mb"] == 500.0
    assert stats["used_mb"] == 500.0
    assert stats["used_pct"] == 50.0
    assert stats["process_mb"] == round(2147483648 / (1024.0 * 1024.0), 2)


def test_get_remote_memory_stats_process_none_when_missing() -> None:
    out_os = "FreePhysicalMemory=512000\r\nTotalVisibleMemorySize=1024000\r\n\r\n"
    fake_os = MagicMock()
    fake_os.returncode = 0
    fake_os.stdout = out_os
    fake_os.stderr = ""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "No Instance(s) Available.\r\n"
    fake_proc.stderr = ""

    def run_side_effect(cmd: list[str], **_kwargs: object) -> MagicMock:
        if len(cmd) >= 3 and cmd[2] == "OS":
            return fake_os
        return fake_proc

    with patch.object(health_monitor.subprocess, "run", side_effect=run_side_effect):
        with patch.object(health_monitor.os, "name", "nt"):
            stats = health_monitor.get_remote_memory_stats("10.0.0.1")
    assert stats is not None
    assert stats["process_mb"] is None


def test_is_onehand_running_true_and_false() -> None:
    fake_ok = MagicMock()
    fake_ok.returncode = 0
    fake_ok.stdout = "ProcessId=4242\r\n\r\n"
    fake_missing = MagicMock()
    fake_missing.returncode = 0
    fake_missing.stdout = "No Instance(s) Available.\r\n"
    fake_err = MagicMock()
    fake_err.returncode = 1
    fake_err.stdout = ""

    status_ok = health_monitor.OneHandStatus(running=True, smb_reachable=True)
    status_missing = health_monitor.OneHandStatus(running=False, smb_reachable=True)
    status_unreachable = health_monitor.OneHandStatus(running=None, smb_reachable=False)

    with patch.object(health_monitor, "check_onehand_status", return_value=status_ok):
        assert health_monitor.is_onehand_running("10.0.0.90") is True
    with patch.object(health_monitor, "check_onehand_status", return_value=status_missing):
        assert health_monitor.is_onehand_running("10.0.0.90") is False
    with patch.object(health_monitor, "check_onehand_status", return_value=status_unreachable):
        assert health_monitor.is_onehand_running("10.0.0.90") is None


def test_check_onehand_status_uses_psexec_when_wmic_fails() -> None:
    with patch.object(health_monitor, "cabinet_smb_reachable", return_value=True):
        with patch.object(health_monitor, "_onehand_via_wmic", return_value=None):
            with patch.object(health_monitor, "_onehand_via_psexec", return_value=True):
                status = health_monitor.check_onehand_status("10.0.0.90")
    assert status == health_monitor.OneHandStatus(running=True, smb_reachable=True)


def test_check_onehand_status_unreachable_without_smb() -> None:
    with patch.object(health_monitor, "cabinet_smb_reachable", return_value=False):
        status = health_monitor.check_onehand_status("10.0.0.90")
    assert status == health_monitor.OneHandStatus(running=None, smb_reachable=False)


def test_onehand_via_psexec_parses_tasklist_output() -> None:
    from automation import remote_exec

    with patch.object(
        remote_exec,
        "psexec_run",
        return_value=remote_exec.RemoteRunResult(
            returncode=0,
            stdout="RUNNING\r\n",
            stderr="",
        ),
    ):
        with patch.object(remote_exec, "resolve_psexec_path", return_value="C:\\tools\\psexec.exe"):
            assert health_monitor._onehand_via_psexec("10.0.0.90") is True


def test_onehand_warning_text() -> None:
    assert "not running" in health_monitor.onehand_warning_text("10.0.0.90", running=False)
    assert health_monitor.onehand_warning_text("10.0.0.90", running=True) == ""
    assert "SMB/log share unreachable" in health_monitor.onehand_warning_text(
        "10.0.0.90", running=None, smb_reachable=False
    )
    assert "reachable but" in health_monitor.onehand_warning_text(
        "10.0.0.90", running=None, smb_reachable=True
    )


def test_is_probable_leak_threshold() -> None:
    assert is_probable_leak(None) is False
    assert is_probable_leak(2500.0) is False
    assert is_probable_leak(2500.01) is True


def test_memory_usage_color_thresholds() -> None:
    pal = QPalette()
    assert memory_usage_color(pal, None) == memory_usage_color(pal, None)
    c_low = memory_usage_color(pal, 50.0)
    c_mid = memory_usage_color(pal, 80.0)
    c_hi = memory_usage_color(pal, 95.0)
    assert c_low != c_hi
    assert c_mid != c_low or c_mid != c_hi
