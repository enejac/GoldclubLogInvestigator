"""Tests for automatic lab SMB credential registration (cmdkey)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from network.lab_access import ensure_lab_smb_credential


def test_ensure_lab_smb_skips_non_fleet_ip() -> None:
    with patch("subprocess.run") as run:
        assert ensure_lab_smb_credential("8.8.8.8") is False
        run.assert_not_called()


def test_ensure_lab_smb_skips_empty() -> None:
    with patch("subprocess.run") as run:
        assert ensure_lab_smb_credential("") is False
        run.assert_not_called()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="cmdkey is Windows-only")
def test_ensure_lab_smb_invokes_cmdkey_for_fleet() -> None:
    completed = MagicMock(returncode=0)
    with patch("subprocess.run", return_value=completed) as run:
        assert ensure_lab_smb_credential("10.0.0.90") is True
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "cmdkey"
    assert "/add:10.0.0.90" in argv
    assert any(a.startswith("/user:") for a in argv)
    assert any(a.startswith("/pass:") for a in argv)


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="cmdkey is Windows-only")
def test_compare_worker_registers_lab_smb_before_load() -> None:
    from gui.sas_verify_dialog import CompareWorker

    worker = CompareWorker("10.0.0.90", r"\\10.0.0.90\c$\Goldclub\var")
    with (
        patch("network.lab_access.ensure_lab_smb_credential") as ensure,
        patch("network.scanner_utils.is_smb_alive", return_value=True),
        patch(
            "network.goldclub_paths.resolve_goldclub_layout",
            return_value=MagicMock(kind=MagicMock(value="remote")),
        ),
        patch("network.goldclub_paths.layout_requires_smb", return_value=True),
        patch(
            "network.accounting_state_loader.load_machine_accounting_state_pure",
            return_value={"TotalCoinIn": "1"},
        ),
        patch.object(worker, "finished", create=True),
        patch.object(worker, "error", create=True),
    ):
        worker.finished = MagicMock()
        worker.error = MagicMock()
        worker.run()
        ensure.assert_called_with("10.0.0.90")
        worker.finished.emit.assert_called_once()