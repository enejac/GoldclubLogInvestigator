"""Guards for GRT330106 Fix-CrashLoop / FIX-111-NOW (no 10.2 swap)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "cabinet_tools" / "roulette" / "Fix-CrashLoop.ps1"
NOW = ROOT / "cabinet_tools" / "roulette" / "FIX-111-NOW.ps1"
ON91 = ROOT / "cabinet_tools" / "roulette" / "onstart.d" / "91-EnableShareAndWinRM.ps1"
RESTART = ROOT / "cabinet_tools" / "roulette" / "RESTART-STACK-NOW.cmd"
FULLSTACK = ROOT / "cabinet_tools" / "roulette" / "Run-FullStack.ps1"
REVERT = ROOT / "cabinet_tools" / "roulette" / "Revert-SasPaytables101.ps1"
REVERT_CMD = ROOT / "cabinet_tools" / "roulette" / "REVERT-SAS-101.cmd"
RESTORE = ROOT / "cabinet_tools" / "roulette" / "Restore-SasQuarantine.ps1"
RESTORE_CMD = ROOT / "cabinet_tools" / "roulette" / "RESTORE-SAS-QUARANTINE.cmd"
START_TV = ROOT / "cabinet_tools" / "roulette" / "START-TV-NOW.cmd"


def test_crashloop_does_not_swap_ruleta_102() -> None:
    text = FIX.read_text(encoding="utf-8")
    assert "Swap-LiveRuleta102" not in text
    assert "10.2.0.0" not in text
    assert "bak-gr0072" in text
    assert "LastGameNr" in text
    assert "AurumSetup" in text
    assert "disabled-101" in text
    assert "backup-devicemgr-20260818" in text
    assert "Patch-ProcessorStatus" not in text
    assert 'Replace("<PaytableId>GR0072</PaytableId>"' not in text
    assert "files still run" in text
    assert "[bool]$FilesOnly" in text
    assert "ruleta-compat-hold" in text
    assert "10.1 exe + 10.2 SAS paytable - hold start" in text
    assert "function Test-LiveRuletaIs10_2" in text
    assert "Ruleta.exe is already 10.2 - no hold" in text
    assert "HIH.exe" in text
    assert "paytable_elite_double_zero" in text
    assert "skip restore" in text
    assert "would crash 10.1" in text
    assert "function Restore-Ruleta102WritableConfig" in text
    assert "bak-20260818" in text
    assert "bak-102names" in text
    assert "skip 10.1 remap" in text
    assert "function Enable-Ruleta102PaytableJson" in text


def test_fix_111_now_starts_portable_tv_and_restores_combo() -> None:
    text = NOW.read_text(encoding="utf-8")
    assert "TeamViewerPortable\\TeamViewer.exe" in text
    assert "GoldClub-TeamViewer-USB" in text
    assert "bak-gr0072" in text
    assert "Run-FullStack" in text
    assert "lab-tampered-" in text
    assert "Remote Management Users" in text
    assert "Printer.disabled" in text
    assert "TeamViewerVPN.inf.disabled" in text
    assert "START-TV-NOW.cmd" in text
    assert "start= demand" in text
    text = ON91.read_text(encoding="utf-8")
    assert 'net localgroup "Remote Management Users" test /add' in text


def test_start_tv_now_hides_printer_and_does_not_kill() -> None:
    text = START_TV.read_text(encoding="utf-8")
    assert "Printer.disabled" in text
    assert "TeamViewerVPN.inf.disabled" in text
    assert "TeamViewerPortable\\TeamViewer.exe" in text
    assert "start= demand" in text.lower()
    assert "net start spooler" not in text.lower()
    assert "restore_tv_login_roulette" in text
    # Zombie TV (exe without tv_w32) is killed; healthy TV is left alone.
    assert "IMAGENAME eq tv_w32.exe" in text
    assert "taskkill /F /IM TeamViewer.exe" in text


def test_revert_sas_101_quarantines_without_rewriting_mac() -> None:
    text = REVERT.read_text(encoding="utf-8")
    assert "Move-Item" in text
    assert "AlreadyInteractive" in text
    assert "Session=" in text
    assert "treat as still 10.2" in text
    assert "quarantine" in text
    assert "serialport" in text
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in text
    assert "licence.dll" in text
    assert "Replace(" not in text
    assert "paytable_elite_double_zero" in text
    assert "ruleta-compat-hold" in text
    assert "GoldClub.Aurum.Services" in text
    cmd = REVERT_CMD.read_text(encoding="utf-8")
    assert "Revert-SasPaytables101.ps1" in cmd
    assert "serialport" in cmd
    assert "stopped for restore" in text
    rst = RESTORE.read_text(encoding="utf-8")
    assert "sas-101-quarantine-20260819-133649" in rst
    assert "Stop-Service" in rst
    assert "37A55022DCBEF351AE27471D181B1EF5.xml" in rst
    assert "serialport" in rst
    assert "Replace(" not in rst
    assert "RESTORE-SAS-QUARANTINE.cmd" in RESTORE_CMD.read_text(encoding="utf-8") or "Restore-SasQuarantine.ps1" in RESTORE_CMD.read_text(
        encoding="utf-8"
    )


def test_run_fullstack_honors_ruleta_compat_hold() -> None:
    text = FULLSTACK.read_text(encoding="utf-8")
    assert "ruleta-compat-hold.json" in text
    assert "not launching Ruleta/Godot" in text
    assert "HOLD ignored: Ruleta.exe is already" in text
    assert "skip: not starting ruleta.exe (compat hold)" in text
    assert "-not $script:RuletaCompatHold" in text
    assert "ruleta/Godot skipped (compat hold)" in text
    assert "HW stack HEALTHY. Ruleta/Godot held" in text
    assert "ERROR 30 auto-clear" in text
    assert "Clear-Error30.ps1" in text
    assert "-Auto" in text
    assert "Error30Relocked" in text
    assert "$started.ToArray()" in text
    assert "-Services @($started)" not in text
    assert "return @($started)" not in text


def test_restart_stack_now_reloads_without_meterhost_move() -> None:
    text = RESTART.read_text(encoding="utf-8")
    assert "Kill-All.ps1" in text
    assert "Run-FullStack.ps1" in text
    assert "Fix-CrashLoop.ps1" in text
    assert "lab-tampered-" not in text
    assert "MeterHost" not in text
