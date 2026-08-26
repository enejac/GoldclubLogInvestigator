"""Tests for WinRM output cleanup."""

from automation.winrm_output import meaningful_winrm_detail, meaningful_winrm_lines


def test_meaningful_winrm_lines_strips_remoting_noise() -> None:
    blob = """
    ERROR: Run-FullStack timed out waiting for CommCtrl
        + CategoryInfo          : NotSpecified: (:) [Write-Error], RemoteException
        + FullyQualifiedErrorId : NativeCommandError
        + PSComputerName        : 10.0.0.111
    Hint: fix HW/CommCtrl first
    """
    lines = meaningful_winrm_lines(blob)
    assert any("Run-FullStack timed out" in line for line in lines)
    assert any("Hint:" in line for line in lines)
    assert not any("PSComputerName" in line for line in lines)


def test_meaningful_winrm_detail_fallback() -> None:
    assert meaningful_winrm_detail("+ PSComputerName : 10.0.0.111", fallback="exit 1") == "exit 1"
