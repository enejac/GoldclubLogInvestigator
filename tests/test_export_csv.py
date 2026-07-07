"""CSV export for filtered incidents."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from gui.export_utils import export_incidents_to_csv, _clean_snippet
from parser import Incident


def test_clean_snippet_collapses_whitespace() -> None:
    assert _clean_snippet("  a\n\tb  c  ") == "a b c"


def test_export_incidents_to_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    incs = [
        Incident(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            game="RouletteGame",
            severity="CRITICAL",
            error_type="Exception",
            probable_cause="—",
            log_file_path=r"C:\logs\slot.log",
            line_number=42,
            line_snippet="  NullRef\n  line2  ",
        )
    ]
    export_incidents_to_csv(str(path), incs, machine_id="gst12345")
    text = path.read_text(encoding="utf-8")
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == [
        "Timestamp",
        "Severity",
        "Game / State",
        "Error Type",
        "Machine ID",
        "Log File",
        "Line Snippet",
        "User Note",
    ]
    assert rows[1][0] == "2026-01-01T00:00:00+00:00"
    assert rows[1][1] == "CRITICAL"
    assert rows[1][2] == "RouletteGame"
    assert rows[1][3] == "Exception"
    assert rows[1][4] == "gst12345"
    assert rows[1][5] == r"C:\logs\slot.log"
    assert rows[1][6] == "NullRef line2"
    assert rows[1][7] == ""


def test_export_csv_bookmark_note_column(tmp_path: Path) -> None:
    path = tmp_path / "marked.csv"
    inc = Incident(
        timestamp=datetime(2000, 1, 2, tzinfo=timezone.utc),
        game="g",
        severity="LOW",
        error_type="e",
        probable_cause="",
        log_file_path="f.log",
        line_number=1,
        line_snippet="x",
    )
    export_incidents_to_csv(
        str(path),
        [inc],
        machine_id=None,
        bookmark_notes={inc.id: "  QA repro  "},
    )
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    assert rows[1][-1] == "QA repro"
