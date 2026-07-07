"""
Export filtered incidents for QA / bug tickets (CSV).
"""

from __future__ import annotations

import csv

from parser import Incident


def _clean_snippet(text: str) -> str:
    """Collapse whitespace and strip newlines for a single CSV field."""
    return " ".join((text or "").split())


def export_incidents_to_csv(
    file_path: str,
    incidents: list[Incident],
    *,
    machine_id: str | None = None,
    bookmark_notes: dict[str, str] | None = None,
) -> None:
    """
    Write incidents as UTF-8 CSV with headers matching the investigator table context.

    ``machine_id`` is the session cabinet id (gst…); repeated per row when known.
    """
    headers = [
        "Timestamp",
        "Severity",
        "Game / State",
        "Error Type",
        "Machine ID",
        "Log File",
        "Line Snippet",
        "User Note",
    ]
    mid = (machine_id or "").strip()
    marks = bookmark_notes or {}
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for inc in incidents:
            raw = marks.get(inc.id, "")
            note = raw.strip() if isinstance(raw, str) else ""
            w.writerow(
                [
                    inc.timestamp.isoformat() if inc.timestamp is not None else "",
                    inc.severity,
                    inc.game,
                    inc.error_type,
                    mid,
                    inc.log_file_path,
                    _clean_snippet(inc.line_snippet),
                    note,
                ]
            )
