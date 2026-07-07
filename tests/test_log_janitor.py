"""Log janitor archive/delete rules (no Qt thread)."""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

from network.log_janitor import run_janitor_pass


def _touch_old(path: Path, days: float) -> None:
    path.write_text("x" * 50, encoding="utf-8")
    old = time.time() - days * 86400.0
    os.utime(path, (old, old))


def test_janitor_deletes_ancient_log(tmp_path: Path) -> None:
    p = tmp_path / "old.log"
    _touch_old(p, 40)
    freed, n = run_janitor_pass(
        str(tmp_path), archive_days=7, delete_days=30, progress_callback=None
    )
    assert not p.exists()
    assert n == 1
    assert freed >= 50


def test_janitor_deletes_ancient_zip(tmp_path: Path) -> None:
    z = tmp_path / "arc.zip"
    z.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    _touch_old(z, 40)
    freed, n = run_janitor_pass(
        str(tmp_path), archive_days=7, delete_days=30, progress_callback=None
    )
    assert not z.exists()
    assert n == 1


def test_janitor_archives_mid_age_log(tmp_path: Path) -> None:
    p = tmp_path / "mid.log"
    _touch_old(p, 10)
    freed, n = run_janitor_pass(
        str(tmp_path), archive_days=7, delete_days=30, progress_callback=None
    )
    assert not p.exists()
    out = tmp_path / "mid.log.zip"
    assert out.is_file()
    with zipfile.ZipFile(out, "r") as zf:
        assert zf.namelist() == ["mid.log"]
    assert n == 1
    assert freed >= 0


def test_janitor_skips_recent_log(tmp_path: Path) -> None:
    p = tmp_path / "new.log"
    p.write_text("fresh", encoding="utf-8")
    freed, n = run_janitor_pass(
        str(tmp_path), archive_days=7, delete_days=30, progress_callback=None
    )
    assert p.exists()
    assert n == 0
    assert freed == 0


def test_janitor_subdirectories(tmp_path: Path) -> None:
    sub = tmp_path / "SlotLog"
    sub.mkdir()
    p = sub / "deep.log"
    _touch_old(p, 40)
    freed, n = run_janitor_pass(
        str(tmp_path), archive_days=7, delete_days=30, progress_callback=None
    )
    assert not p.exists()
    assert n == 1
