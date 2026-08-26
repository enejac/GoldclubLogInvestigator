from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from config_scanner.snapshot_clock import (
    apply_snapshot_clock,
    pe_link_datetime,
    resolve_snapshot_datetime,
)


CEST = timezone(timedelta(hours=2))


def test_apply_snapshot_clock_keeps_local_when_refs_are_same_day() -> None:
    local = datetime(2026, 8, 21, 9, 40, 0, tzinfo=CEST)
    floor = datetime(2026, 8, 21, 8, 0, 0, tzinfo=CEST)
    assert apply_snapshot_clock(local, date_floors=[floor]) == local


def test_apply_snapshot_clock_moves_rolled_back_cabinet_to_exe_date() -> None:
    local = datetime(2026, 8, 11, 6, 13, 9, tzinfo=CEST)
    exe = datetime(2026, 8, 21, 9, 21, 0, tzinfo=CEST)
    fixed = apply_snapshot_clock(local, date_floors=[exe])
    assert fixed.date().isoformat() == "2026-08-21"
    assert fixed.hour == 6 and fixed.minute == 13 and fixed.second == 9


def test_apply_snapshot_clock_live_now_wins_full_datetime() -> None:
    local = datetime(2026, 8, 11, 6, 13, 9, tzinfo=CEST)
    live = datetime(2026, 8, 21, 9, 43, 0, tzinfo=CEST)
    fixed = apply_snapshot_clock(local, live_now=live)
    assert fixed == live


def test_apply_snapshot_clock_does_not_move_date_backward() -> None:
    local = datetime(2026, 8, 21, 10, 0, 0, tzinfo=CEST)
    old_exe = datetime(2026, 8, 11, 5, 0, 0, tzinfo=CEST)
    assert apply_snapshot_clock(local, date_floors=[old_exe]) == local


def test_resolve_snapshot_datetime_uses_injected_clock() -> None:
    local = datetime(2026, 8, 11, 12, 0, 0, tzinfo=CEST)
    floor = datetime(2026, 8, 21, 8, 0, 0, tzinfo=CEST)
    got = resolve_snapshot_datetime(
        local=local,
        live_now=None,
        date_floors=[floor],
        probe_network=False,
    )
    assert got.strftime("%Y-%m-%d_%H%M%S") == "2026-08-21_120000"


def _write_pe(path: Path, timestamp: int) -> None:
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (0x80).to_bytes(4, "little")
    pe = bytearray(12)
    pe[0:4] = b"PE\0\0"
    pe[8:12] = timestamp.to_bytes(4, "little")
    path.write_bytes(bytes(dos) + bytes(pe))


def test_pe_link_datetime_reads_coff_stamp(tmp_path: Path) -> None:
    stamp = int(datetime(2026, 8, 21, 7, 20, tzinfo=timezone.utc).timestamp())
    pe = tmp_path / "app.exe"
    _write_pe(pe, stamp)
    got = pe_link_datetime(pe)
    assert got is not None
    assert got.astimezone(timezone.utc).date().isoformat() == "2026-08-21"


def test_pe_link_datetime_ignores_zero_stamp(tmp_path: Path) -> None:
    pe = tmp_path / "repro.exe"
    _write_pe(pe, 0)
    assert pe_link_datetime(pe) is None
