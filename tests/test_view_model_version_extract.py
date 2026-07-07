"""SlotLog / LogDaemon path layout and version-line extraction on disk."""

from __future__ import annotations

from gui.view_model import IncidentViewModel


def test_slotlog_folder_daily_file_without_slotlog_in_name(tmp_path) -> None:
    log_root = tmp_path / "Goldclub" / "var" / "log"
    slot_dir = log_root / "SlotLog"
    slot_dir.mkdir(parents=True)
    line = (
        "2026-03-15T00:00:00.551+00:00  INFO  [SlotMachine] "
        "OneHand.MainFrm - SlotMachine v2.14.0 startup\n"
    )
    (slot_dir / "2026-03-15.log").write_text(line * 3, encoding="utf-8")

    vm = IncidentViewModel()
    core, prod = vm._extract_version_from_slotlog(log_root)
    assert core == "v2.14.0"
    assert prod is None


def test_logdaemon_folder_daily_file_spawning_deep(tmp_path) -> None:
    log_root = tmp_path / "Goldclub" / "var" / "log"
    daemon_dir = log_root / "GoldClub.Logging.LogDaemon"
    daemon_dir.mkdir(parents=True)
    filler = "INFO padding\n" * 120
    spawn = "Logging::Log() Spawning v3.2.1 , clr=foo\n"
    (daemon_dir / "2026-03-15.log").write_text(filler + spawn, encoding="utf-8")

    vm = IncidentViewModel()
    core, prod = vm._extract_full_version_from_logs(log_root)
    assert core == "v3.2.1"
