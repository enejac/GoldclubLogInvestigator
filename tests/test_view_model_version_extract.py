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


def test_ruleta_roulette_log_native_version(tmp_path) -> None:
    log_root = tmp_path / "log_bundle"
    rr = log_root / "ruleta Roulette"
    rr.mkdir(parents=True)
    text = (
        "2025-11-24T09:15:19.764+00:00 INFO [:] Roulette Initialized\n"
        "2025-11-24T09:15:19.764+00:00 INFO [:] < name > LUMINA330106 < / name >\n"
        "2025-11-24T09:15:19.764+00:00 INFO [:] < version > 10.1.0.0 clone: LuxuriousIII64 beta < /version >\n"
    )
    (rr / "2025-11-24.log").write_text(text, encoding="utf-8")

    vm = IncidentViewModel()
    assert vm._looks_like_roulette_logs(log_root)
    core, product = vm._extract_version_from_ruleta_roulette_log(log_root)
    assert core == "v10.1.0.0"
    assert product == "LuxuriousIII64"

    vm.refresh_current_software_version(log_root)
    assert vm.software_version_for_ai() == "LuxuriousIII64_v10.1.0.0"


def test_roulette_log_beats_logdaemon(tmp_path) -> None:
    log_root = tmp_path / "log_bundle"
    rr = log_root / "ruleta Roulette"
    rr.mkdir(parents=True)
    (rr / "2025-11-24.log").write_text(
        "INFO < version > 10.1.0.0 clone: LuxuriousIII64 beta < /version >\n",
        encoding="utf-8",
    )
    daemon = log_root / "GoldClub.Logging.LogDaemon"
    daemon.mkdir(parents=True)
    (daemon / "2025-11-24.log").write_text(
        "Logging::Log() Spawning v2.9.9454.21067, clr=4.0\n",
        encoding="utf-8",
    )

    vm = IncidentViewModel()
    vm.refresh_current_software_version(log_root)
    assert "10.1.0.0" in vm.software_version_for_ai()
    assert "9454" not in vm.software_version_for_ai()