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
            stats = health_monitor.get_remote_memory_stats("10.0.0.90")
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
            stats = health_monitor.get_remote_memory_stats("10.0.0.90")
    assert stats is not None
    assert stats["process_mb"] is None


def test_get_remote_memory_stats_skips_non_fleet_host() -> None:
    """A stray IP must not cost a 45 s WMIC timeout per incident row."""
    with patch.object(health_monitor.subprocess, "run") as run:
        with patch.object(health_monitor.os, "name", "nt"):
            assert health_monitor.get_remote_memory_stats("203.0.113.7") is None
    run.assert_not_called()


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
        with patch.object(health_monitor, "_game_client_via_winrm", return_value=None):
            with patch.object(health_monitor, "_game_client_via_wmic", return_value=None):
                with patch.object(
                    health_monitor, "_game_client_via_psexec", return_value=True
                ):
                    status = health_monitor.check_onehand_status("10.0.0.90")
    assert status == health_monitor.OneHandStatus(running=True, smb_reachable=True)


def test_check_onehand_status_unreachable_without_smb() -> None:
    with patch.object(health_monitor, "cabinet_smb_reachable", return_value=False):
        with patch.object(health_monitor, "_game_client_via_winrm", return_value=None):
            with patch.object(health_monitor, "_game_client_via_wmic", return_value=None):
                with patch.object(
                    health_monitor, "_game_client_via_psexec", return_value=None
                ):
                    status = health_monitor.check_onehand_status("10.0.0.90")
    assert status == health_monitor.OneHandStatus(running=None, smb_reachable=False)


def test_check_game_client_status_reachable_via_winrm_when_smb_fails() -> None:
    """Workgroup roulette (.111): WinRM answers even when C$ log tree is absent."""
    with patch.object(health_monitor, "cabinet_smb_reachable", return_value=False):
        with patch.object(health_monitor, "_game_client_via_winrm", return_value=False):
            status = health_monitor.check_game_client_status(
                "10.0.0.111",
                kind="roulette",
                allow_psexec=False,
                scan_root=r"\\10.0.0.111\c$\Goldclub\var",
            )
    assert status == health_monitor.OneHandStatus(running=False, smb_reachable=True)


def test_cabinet_smb_reachable_honours_scan_root() -> None:
    with patch(
        "network.goldclub_paths.unc_share_scan_root_reachable", return_value=True
    ):
        with patch.object(health_monitor, "ensure_lab_smb_credential"):
            with patch("network.scanner_utils.is_smb_alive", return_value=True):
                assert health_monitor.cabinet_smb_reachable(
                    "10.0.0.111",
                    scan_root=r"\\10.0.0.111\c$\Goldclub\var",
                )


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
    assert "reachable" in health_monitor.onehand_warning_text("10.0.0.90", running=False)
    assert "not running" in health_monitor.onehand_warning_text("10.0.0.90", running=False)
    assert health_monitor.onehand_warning_text("10.0.0.90", running=True) == ""
    assert "SMB/log share unreachable" in health_monitor.onehand_warning_text(
        "10.0.0.90", running=None, smb_reachable=False
    )
    # Inconclusive remote probe stays silent until COM/MUX also failed.
    assert (
        health_monitor.onehand_warning_text(
            "10.0.0.90", running=None, smb_reachable=True
        )
        == ""
    )
    assert "all failed" in health_monitor.onehand_warning_text(
        "10.0.0.90",
        running=None,
        smb_reachable=True,
        com_meters_failed=True,
    ).lower() or "could not verify" in health_monitor.onehand_warning_text(
        "10.0.0.90",
        running=None,
        smb_reachable=True,
        com_meters_failed=True,
    ).lower()
    assert health_monitor.onehand_warning_text(
        "10.0.0.90", running=None, smb_reachable=True, com_meters_ok=True
    ) == ""
    assert health_monitor.is_valid_remote_cabinet_ip("0.0.0.0") is False
    assert health_monitor.is_valid_remote_cabinet_ip("10.0.0.90") is True


def test_resolve_game_client_kind_from_scan_root() -> None:
    assert (
        health_monitor.resolve_game_client_kind(
            scan_root=r"\\10.0.0.90\c$\Goldclub\var\log\ruleta"
        )
        == "roulette"
    )
    assert (
        health_monitor.resolve_game_client_kind(
            scan_root=r"\\10.0.0.111\c$\Goldclub\var"
        )
        == "roulette"
    )
    assert (
        health_monitor.resolve_game_client_kind(
            hint="roulette",
            scan_root=r"\\10.0.0.90\c$\Goldclub\var\log",
        )
        == "roulette"
    )
    # Stale slot hint must not override a ruleta scan path (SAS dialog bug).
    assert (
        health_monitor.resolve_game_client_kind(
            hint="slot",
            scan_root=r"\\10.0.0.90\c$\Goldclub\var\log\ruleta",
        )
        == "roulette"
    )
    # Bare …\var\log with no install markers / unreachable share → slot default.
    assert (
        health_monitor.resolve_game_client_kind(
            scan_root=r"\\192.0.2.1\c$\Goldclub\var\log"
        )
        == "slot"
    )


def test_resolve_game_client_kind_ignores_substring_matches() -> None:
    """``roulette`` inside an unrelated folder name is not a roulette cabinet."""
    kind, determined = health_monitor.resolve_game_client_kind_detailed(
        hint="slot",
        scan_root=r"D:\exports\roulette-tickets\slotlogs",
    )
    assert kind == "slot"
    assert determined is True
    # A real path component still wins, suffixed variants included.
    assert (
        health_monitor.resolve_game_client_kind(
            scan_root=r"\\10.0.0.90\c$\Goldclub\ruleta2\var\log"
        )
        == "roulette"
    )


def test_resolve_game_client_kind_reports_undetermined_default() -> None:
    """An unreachable share must not masquerade as a confirmed slot cabinet."""
    kind, determined = health_monitor.resolve_game_client_kind_detailed(
        scan_root=r"\\192.0.2.1\c$\Goldclub\var\log"
    )
    assert kind == "slot"
    assert determined is False


def test_check_game_client_status_skips_foreign_hosts() -> None:
    """A non-fleet target must never be answered from local processes."""
    with patch.object(health_monitor.os, "name", "nt"):
        with patch.object(health_monitor, "is_this_host", return_value=False):
            with patch.object(
                health_monitor, "check_game_client_status_local"
            ) as local:
                assert health_monitor.check_game_client_status("203.0.113.7") is None
    local.assert_not_called()
    # This machine, addressed by name, still answers locally.
    status = health_monitor.OneHandStatus(running=True, smb_reachable=True)
    with patch.object(health_monitor.os, "name", "nt"):
        with patch.object(health_monitor, "is_this_host", return_value=True):
            with patch.object(
                health_monitor, "check_game_client_status_local", return_value=status
            ):
                assert health_monitor.check_game_client_status("GST20664") == status


def test_roulette_warning_names_godot() -> None:
    text = health_monitor.onehand_warning_text(
        "10.0.0.90",
        running=False,
        kind="roulette",
    )
    assert "godot.exe" in text
    assert "reachable" in text
    assert "Godot frontend" in text
    assert "Ruleta.exe" not in text
    assert "OneHand.exe" not in text
    assert health_monitor.game_client_process_basenames("roulette") == (
        "godot",
        "godot1",
    )
    assert "godot*" in health_monitor.game_client_process_match_script("roulette")


def test_local_egm_game_client_running(monkeypatch) -> None:
    monkeypatch.setattr(
        health_monitor.subprocess,
        "run",
        lambda *a, **k: MagicMock(
            stdout="OneHand.exe                    1234 Console                    1     10,000 K\n"
        ),
    )
    assert health_monitor.local_egm_game_client_running() is True

    monkeypatch.setattr(
        health_monitor.subprocess,
        "run",
        lambda *a, **k: MagicMock(
            stdout="Ruleta.exe                     1234 Console                    1     10,000 K\n"
        ),
    )
    assert health_monitor.local_egm_game_client_running() is True

    monkeypatch.setattr(
        health_monitor.subprocess,
        "run",
        lambda *a, **k: MagicMock(
            stdout="godot1.exe                     1234 Console                    1     10,000 K\n"
        ),
    )
    assert health_monitor.local_egm_game_client_running() is True

    monkeypatch.setattr(
        health_monitor.subprocess,
        "run",
        lambda *a, **k: MagicMock(
            stdout="notepad.exe                    1234 Console                    1      1,000 K\n"
        ),
    )
    assert health_monitor.local_egm_game_client_running() is False


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
