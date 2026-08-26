"""Ensure WinRM auto-enable helpers exist and are wired into callers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_labaccess_ensure_winrm_ready_wired() -> None:
    src = _read("LabAccess.ps1")
    assert "function Ensure-LabWinRmReady" in src
    assert "function Get-LabPsExecPath" in src
    assert "function Get-LabUserForHost" in src
    assert "10.0.0.111\\test" in src
    assert "Enable-PSRemoting -Force -SkipNetworkProfileCheck" in src
    # Invoke must auto-enable instead of only throwing "not reachable".
    invoke = src.split("function Invoke-LabWinRmCommand", 1)[1].split("function ", 1)[0]
    assert "Ensure-LabWinRmReady" in invoke
    assert "Get-LabCredential -ComputerName" in invoke
    assert "Run D:\\_ENABLE_WINRM.bat on the cabinet" not in invoke
    smb = src.split("function Test-LabSmbAccess", 1)[1].split("function ", 1)[0]
    assert r"\slot" in smb
    assert "USB_Remote" in src


def test_initialize_lab_access_includes_workgroup_111() -> None:
    src = _read("Initialize-LabAccess.ps1")
    assert "10.0.0.111" in src
    assert "Get-LabUserForHost" in src


def test_lab_winrm_ensure_auto_enables() -> None:
    src = _read("lab/LabWinRm.ps1")
    assert "function Ensure-LabWinRm" in src
    assert "function Invoke-LabWinRmEnableRemote" in src
    assert "function Get-LabWinRmUserForHost" in src
    assert "10.0.0.111\\test" in src
    assert "USB_Remote" in src
    assert "auto-enabling via PsExec" in src
    invoke = src.split("function Invoke-LabWinRm ", 1)[1].split("function ", 1)[0]
    assert "Ensure-LabWinRm" in invoke
    assert "Run D:\\_ENABLE_WINRM.bat on the cabinet first." not in invoke


def test_open_remote_scripts_call_ensure() -> None:
    ttcmd = _read("lab/Open_TTCMD_Remote.ps1")
    cmd = _read("lab/Open_CMD_Remote.ps1")
    assert "Ensure-LabWinRm" in ttcmd
    assert "Ensure-LabWinRm" in cmd
    assert "Run D:\\_ENABLE_WINRM.bat on the cabinet." not in ttcmd


def test_deploy_egm_winrm_uses_ensure() -> None:
    src = _read("scripts/deploy_egm_winrm.ps1")
    assert "Ensure-LabWinRmReady" in src
    assert "WinRM 5985 not reachable" not in src


def test_lab_remote_transport_wait_uses_ensure() -> None:
    src = _read("lab/LabRemoteTransport.ps1")
    assert "[switch]   $Wait" in src or "[switch]$Wait" in src.replace(" ", "")
    assert "Ensure-LabWinRmReady" in src


def test_lab_remote_transport_aft_uses_workgroup_111() -> None:
    src = _read("lab/LabRemoteTransport.ps1")
    assert "'10.0.0.111'" in src
    assert "Get-LabCredential -ComputerName $ComputerName" in src
    assert "Get-LabPsExecArgs -ComputerName $ComputerName" in src
    diag = _read("lab/LabSasPollDiagnostics.ps1")
    assert "Get-LabPsExecArgs -ComputerName $Computer" in diag
    fwd = _read("Invoke-WinDivertAftRoulette.ps1")
    assert "[pscredential] $Credential" in fwd
    assert "$fwd.Credential = $Credential" in fwd


def test_aft_dated_log_follows_cabinet_clock() -> None:
    src = _read("lab/Invoke-WinDivertAft.ps1")
    assert "function Get-CabinetClockDate" in src
    assert "function Convert-WorkstationUtcToCabinetLogUtc" in src
    dated = src.split("function Get-DatedLogPath", 1)[1].split("function Get-SasmsgrLogPath", 1)[0]
    assert "Get-CabinetClockDate -Computer $Computer" in dated
    assert "Sort-Object LastWriteTimeUtc" not in dated
    assert "[datetime]::UtcNow" in src.split("function Get-CabinetClockDate", 1)[1].split(
        "function Get-DatedLogPath", 1
    )[0]
    fsm = src.split("function Get-AurumAftFsmState", 1)[1].split("function Wait-AurumAftReady", 1)[0]
    assert "Convert-WorkstationUtcToCabinetLogUtc" in fsm
    wat = src.split("function Wait-AurumWat2AftWarmup", 1)[1].split("function Wait-AurumMappingReady", 1)[0]
    assert "Convert-WorkstationUtcToCabinetLogUtc" in wat
    ingest = src.split("function Test-SasMessengerIngested", 1)[1].split("function Find-CreditEvidence", 1)[0]
    assert "Convert-WorkstationUtcToCabinetLogUtc" in ingest
    assert "Post-wake: skipping WAT2AFT wait" in src
    assert "$deferWat2AftForPollSim = -not $pollStrategy.PollsActive" in src
