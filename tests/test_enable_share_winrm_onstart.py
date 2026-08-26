"""Guards for 91-EnableShareAndWinRM.ps1 loop-prevention (share/TV/admin-shell)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "cabinet_tools" / "roulette" / "onstart.d" / "91-EnableShareAndWinRM.ps1"
SHARED = ROOT / "cabinet_tools" / "shared" / "onstart.d" / "91-EnableShareAndWinRM.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_roulette_and_shared_onstart_scripts_match() -> None:
    assert SCRIPT.read_bytes() == SHARED.read_bytes()


def test_share_paths_equal_drive_root_and_folder() -> None:
    # Mirrors Test-SharePathEqual in the ps1 (trim trailing slash, case-insensitive).
    def eq(left: str, right: str) -> bool:
        a = left.rstrip("\\") + "\\"
        b = right.rstrip("\\") + "\\"
        return a.upper() == b.upper()

    assert eq("D:\\", "D:")
    assert eq("C:\\goldclub", "C:\\goldclub\\")
    assert not eq("C:\\goldclub", "G:\\")


def test_onstart_share_script_has_single_instance_mutex() -> None:
    text = _text()
    assert "Global\\GoldClub91EnableShareAndWinRM" in text
    assert "function Enter-91Mutex" in text
    assert "already running - skip this instance" in text
    assert "function Exit-91Mutex" in text


def test_onstart_share_script_ensures_shares_without_blind_delete() -> None:
    text = _text()
    assert "function Ensure-NetShare" in text
    assert "share {0} already {1} - leave it" in text
    # Main body must not delete slot/USB shares before creating them.
    assert 'cmd /c "net share $shareName /delete /y"' not in text
    assert "net share USB_Remote /delete /y" not in text
    assert "net share ConfigScanner /delete /y" not in text


def test_onstart_share_script_does_not_restart_gui_when_already_up() -> None:
    text = _text()
    assert "Open-AdminShell already on console - not restarting" in text
    assert "GoldClub-TeamViewer-Start" in text
    assert "removed GoldClub-TeamViewer-USB" in text
    assert "share slot already {0} - leave it" in text
    assert "WinRM already listening on 5985 - skip Enable-PSRemoting -Force" in text
    assert 'net localgroup "Remote Management Users" test /add' in text
    assert "recent success" in text


def test_onstart_share_script_allows_empty_exe_paths() -> None:
    text = _text()
    assert "[string[]]$ExePaths = @()" in text
    assert "[Parameter(Mandatory = $true)][string[]]$ExePaths" not in text


def test_onstart_disables_teamviewer_portable_printer() -> None:
    text = _text()
    assert "function Disable-TeamViewerPortablePrinter" in text
    assert "Printer.disabled" in text
    assert "TeamViewerVPN.inf.disabled" in text
    assert "Disable-TeamViewerPortablePrinter -TvExe $tvExe" in text
    # Never set Print Spooler Automatic or net start it (ticket is COM).
    # demand-start is required so portable TV StartService(Spooler) is not 1058.
    lowered = text.lower()
    assert "sc.exe config spooler start= demand" in lowered
    assert "net start spooler" not in lowered
    assert "start-service spooler" not in lowered
    assert "sc.exe config spooler start= auto" not in lowered
    assert "START-TV-NOW.cmd" in text
    assert "RunLevelHighest" in text
    assert "no tv_w32/tv_x64" in text
    assert "Starting TeamViewer (START-TV-NOW)" in text
    assert "START-GAME-NOW.flag" in text
    assert "FIX-CRASH-NOW.flag" in text
    assert "ruleta-compat-hold.json" in text
    assert "hold ignored, Ruleta.exe is" in text
    assert "REBOOT-NOW.flag" in text
    assert "Appinfo" in text and "seclogon" in text
    assert "function Ensure-UacElevationServices" in text
    assert "function Register-GoldClubElevateOnceTask" in text
    assert "GoldClub-ElevateOnce" in text
    assert "UAC service {0}: demand + start status=" in text
    assert "-RunLevelHighest" in text
    assert "GoldClub-TotalCommander-USB" in text
    assert "RestartExisting" in text
    assert "inherits RunLevel Highest" in text


def test_onstart_share_script_utf8_not_utf16() -> None:
    raw = SCRIPT.read_bytes()[:2]
    assert raw != b"\xff\xfe" and raw != b"\xfe\xff"
