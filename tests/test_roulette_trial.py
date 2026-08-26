"""Persistent Ruleta trial tokens (ERROR 30 / 99) — not DataBase.db."""

from datetime import datetime, timezone
from pathlib import Path

from roulette_errors import format_roulette_error, reload_roulette_error_catalog

from roulette_trial import (
    clear_trial_persistent,
    finance_stamp_mismatches_clock,
    inspect_trial_persistent,
    is_trial_persist_rel_path,
    reset_trial_bind_if_needed,
    should_reset_trial_bind,
    trial_paths_to_clear,
)
from config_scanner.write_scope import is_protected_write_path, protected_write_block_reason


def _layout(tmp_path: Path) -> Path:
    persist = tmp_path / "ruleta" / "persistent"
    var = tmp_path / "ruleta" / "var"
    persist.mkdir(parents=True)
    var.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\xAF\x07\x77\x93")
    (persist / "RouletteStop.flag").write_bytes(b"")
    (persist / "HeapDataFinanceStamps.dat").write_bytes(b"\x01\x00\x00\x00\x16\xd5\x86\x6a")
    (persist / "HeapDataTitoPowerUp.dat").write_bytes(b"keep-tito")
    (persist / "HeapDataWatTransactions.dat").write_bytes(b"keep-wat")
    (var / "Password.dat").write_bytes(b"\x00" * 16)
    (var / "HeapDataDateTime.dat").write_text("20.08.2026;20.08.2026;20.08.2026")
    (tmp_path / "config" / "licences").mkdir(parents=True)
    (tmp_path / "config" / "licences" / "37A55022DCBEF351AE27471D181B1EF5.xml").write_text(
        "<licence/>"
    )
    (tmp_path / "ruleta" / "licence.dll").write_bytes(b"x" * 12288)
    (tmp_path / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (tmp_path / "ruleta" / "BuildVersion.txt").write_text("Source Version: 38884\n")
    return tmp_path


def test_inspect_detects_bound_activate(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    report = inspect_trial_persistent(dest)
    assert report.activate_bound is True
    names = {i.path.name for i in report.files}
    assert "RouletteActivate.dat" in names
    assert "HeapDataFinanceStamps.dat" in names


def test_clear_keeps_licence_tito_wat(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    removed = clear_trial_persistent(dest)
    assert any("RouletteActivate.dat" in p for p in removed)
    assert any("Password.dat" in p for p in removed)
    assert not any("licence.dll" in p for p in removed)
    assert not any("37A55022" in p for p in removed)
    assert (dest / "ruleta" / "licence.dll").is_file()
    assert (dest / "ruleta" / "persistent" / "HeapDataTitoPowerUp.dat").read_bytes() == b"keep-tito"
    assert (dest / "ruleta" / "persistent" / "HeapDataWatTransactions.dat").read_bytes() == b"keep-wat"
    assert not (dest / "ruleta" / "persistent" / "RouletteActivate.dat").exists()
    bak = dest / "var" / "state" / "gci-backup-trial"
    stamped = next(bak.iterdir())
    assert (stamped / "RouletteActivate.dat").is_file()


def test_empty_activate_is_not_bound(tmp_path: Path) -> None:
    persist = tmp_path / "ruleta" / "persistent"
    persist.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 32)
    report = inspect_trial_persistent(tmp_path)
    assert report.activate_bound is False
    assert trial_paths_to_clear(tmp_path)


def test_snapshot_must_not_restore_activate() -> None:
    assert is_trial_persist_rel_path("ruleta/persistent/RouletteActivate.dat")
    assert is_trial_persist_rel_path(r"var\state\ruleta\persistent\HeapDataFinanceStamps.dat")
    assert is_trial_persist_rel_path("ruleta/var/Password.dat")
    assert not is_trial_persist_rel_path("ruleta/persistent/HeapDataTitoPowerUp.dat")
    assert is_protected_write_path("ruleta/persistent/RouletteActivate.dat")
    reason = protected_write_block_reason("ruleta/persistent/RouletteActivate.dat")
    assert reason is not None
    assert "Activate" in reason


def test_service_clear_error30_also_wipes_activate(tmp_path: Path) -> None:
    from config_scanner.service import ConfigScannerService

    dest = _layout(tmp_path)
    svc = ConfigScannerService(tmp_path / "cs")
    removed = svc.clear_error30_leftovers(str(dest))
    assert any("RouletteActivate.dat" in p for p in removed)
    assert not (dest / "ruleta" / "persistent" / "RouletteActivate.dat").exists()
    assert (dest / "ruleta" / "licence.dll").is_file()


def test_version_cross_resets_bind_and_keeps_licence(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    needed, reason = should_reset_trial_bind(
        dest, snapshot_major_minor="10.2", live_major_minor="10.1"
    )
    assert needed
    assert "10.1" in reason and "10.2" in reason
    notes = reset_trial_bind_if_needed(
        dest, snapshot_major_minor="10.2", live_major_minor="10.1"
    )
    assert notes
    assert not (dest / "ruleta" / "persistent" / "RouletteActivate.dat").exists()
    assert (dest / "ruleta" / "licence.dll").is_file()
    assert (dest / "config" / "licences" / "37A55022DCBEF351AE27471D181B1EF5.xml").is_file()


def test_downgrade_10_2_to_10_1_resets_stale_bind(tmp_path: Path) -> None:
    """876 -> 10.1 after gci-backup restore must not keep ERROR 30 token."""
    dest = _layout(tmp_path)
    needed, reason = should_reset_trial_bind(
        dest,
        snapshot_major_minor="10.1",
        live_major_minor="10.1",
        previous_live_major_minor="10.2",
    )
    assert needed
    assert "10.2" in reason and "10.1" in reason
    notes = reset_trial_bind_if_needed(
        dest,
        snapshot_major_minor="10.1",
        live_major_minor="10.1",
        previous_live_major_minor="10.2",
    )
    assert notes
    assert not (dest / "ruleta" / "persistent" / "RouletteActivate.dat").exists()


def test_same_version_10_2_keeps_used_bind_when_clock_matches(
    tmp_path: Path,
) -> None:
    dest = _layout(tmp_path)
    persist = dest / "ruleta" / "persistent"
    stamp = int(datetime(2026, 8, 10, 12, 2, 53, tzinfo=timezone.utc).timestamp())
    persist.joinpath("HeapDataFinanceStamps.dat").write_bytes(
        b"\x01\x00\x00\x00" + stamp.to_bytes(4, "little") + b"\x00" * 20
    )
    now = datetime(2026, 8, 10, 0, 8, 0, tzinfo=timezone.utc)
    needed, _ = should_reset_trial_bind(
        dest,
        snapshot_major_minor="10.2",
        live_major_minor="10.2",
        now=now,
    )
    assert needed is False
    assert (persist / "RouletteActivate.dat").is_file()


def test_same_version_keeps_bind_when_clock_matches_stamp(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    persist = dest / "ruleta" / "persistent"
    stamp = int(datetime(2026, 8, 10, 12, 13, 7, tzinfo=timezone.utc).timestamp())
    persist.joinpath("HeapDataFinanceStamps.dat").write_bytes(
        b"\x01\x00\x00\x00" + stamp.to_bytes(4, "little") + b"\x00" * 20
    )
    now = datetime(2026, 8, 10, 14, 0, 0, tzinfo=timezone.utc)
    assert finance_stamp_mismatches_clock(dest, now) is False
    needed, _ = should_reset_trial_bind(
        dest,
        snapshot_major_minor="10.1",
        live_major_minor="10.1",
        now=now,
    )
    assert needed is False
    assert (persist / "RouletteActivate.dat").is_file()


def test_clock_vs_finance_stamp_resets_bind(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    persist = dest / "ruleta" / "persistent"
    stamp = int(datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    persist.joinpath("HeapDataFinanceStamps.dat").write_bytes(
        b"\x01\x00\x00\x00" + stamp.to_bytes(4, "little") + b"\x00" * 20
    )
    now = datetime(2026, 8, 10, 14, 0, 0, tzinfo=timezone.utc)
    assert finance_stamp_mismatches_clock(dest, now) is True
    needed, reason = should_reset_trial_bind(
        dest,
        snapshot_major_minor="10.1",
        live_major_minor="10.1",
        now=now,
    )
    assert needed
    assert "calendar day" in reason


def test_catalog_30_and_99_name_persistent() -> None:
    reload_roulette_error_catalog()
    info30 = format_roulette_error(30)
    assert "persistent" in info30.probable_cause
    assert "RouletteActivate" in info30.probable_cause
    info99 = format_roulette_error(99)
    assert info99.error_type == "Roulette ERROR 99"
    assert "Dongle" in info99.title or "Dongle" in info99.probable_cause
    assert "SUCCEEDED" in info99.probable_cause


def test_extract_trial_display_challenge() -> None:
    from roulette_trial import extract_trial_display_challenge

    hit = extract_trial_display_challenge(
        'INFO [:] <TRIAL error="99" type="DISPLAYED"> 16940973716442060005 </TRIAL>'
    )
    assert hit is not None
    assert hit.error_code == 99
    assert hit.ui_system_id == "1694097371-6442060005"


def test_find_latest_trial_displayed(tmp_path: Path) -> None:
    from roulette_trial import find_latest_trial_displayed

    log_dir = tmp_path / "var" / "log" / "ruleta Roulette"
    log_dir.mkdir(parents=True)
    log_dir.joinpath("2026-08-21.log").write_text(
        'INFO [:] <TRIAL error="99" type="DISPLAYED"> 16940973716442060005 </TRIAL>\n',
        encoding="utf-8",
    )
    hit = find_latest_trial_displayed(tmp_path)
    assert hit is not None
    assert hit.error_code == 99


def test_clear_stale_llave_after_software_swap(tmp_path: Path) -> None:
    from roulette_trial import clear_stale_llave_after_software_swap

    dest = _layout(tmp_path)
    removed = clear_stale_llave_after_software_swap(dest)
    assert any("RouletteActivate.dat" in p for p in removed)


def test_trial_paths_to_clear_includes_persistent_mirror(tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    mirror = dest / "var" / "state" / "ruleta" / "persistent"
    mirror.mkdir(parents=True)
    (mirror / "RouletteActivate.dat").write_bytes(b"\x01")
    paths = trial_paths_to_clear(dest)
    assert any(p.name == "RouletteActivate.dat" for p in paths)
    assert any("var\\state\\ruleta\\persistent" in str(p) for p in paths)


def test_clear_ruleta_var_arhiv(tmp_path: Path) -> None:
    from roulette_trial import clear_ruleta_var_arhiv

    dest = tmp_path / "goldclub"
    var = dest / "ruleta" / "var"
    var.mkdir(parents=True)
    (var / "Play1.db").write_bytes(b"old")
    (dest / "ruleta" / "DataBase.db").write_bytes(b"old")
    persist = dest / "ruleta" / "persistent"
    persist.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x01")
    removed = clear_ruleta_var_arhiv(dest)
    assert any("ruleta\\var" in p for p in removed)
    assert (persist / "RouletteActivate.dat").is_file()
    assert not var.exists()
