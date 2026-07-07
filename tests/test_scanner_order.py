"""Scan ordering: LogDaemon first under Goldclub\\var\\log."""

from __future__ import annotations

from pathlib import Path

from scanner import collect_log_files


def test_logdaemon_files_first_under_var_log(tmp_path: Path) -> None:
    root = tmp_path / "Goldclub" / "var" / "log"
    (root / "SlotLog").mkdir(parents=True)
    (root / "GoldClub.Logging.LogDaemon").mkdir(parents=True)
    (root / "SlotLog" / "z.log").write_text("z", encoding="utf-8")
    (root / "GoldClub.Logging.LogDaemon" / "a.log").write_text("a", encoding="utf-8")
    files = collect_log_files([root])
    assert len(files) == 2
    assert "GoldClub.Logging.LogDaemon" in str(files[0])
    assert "SlotLog" in str(files[1])


def test_collect_single_file_root(tmp_path: Path) -> None:
    f = tmp_path / "standalone.log"
    f.write_text("x", encoding="utf-8")
    files = collect_log_files([f])
    assert len(files) == 1
    assert files[0] == f.resolve()


def test_no_reorder_when_not_var_log_root(tmp_path: Path) -> None:
    root = tmp_path / "other" / "logs"
    (root / "GoldClub.Logging.LogDaemon").mkdir(parents=True)
    (root / "SlotLog").mkdir(parents=True)
    (root / "SlotLog" / "first.log").write_text("1", encoding="utf-8")
    (root / "GoldClub.Logging.LogDaemon" / "second.log").write_text("2", encoding="utf-8")
    files = collect_log_files([root])
    assert len(files) == 2
