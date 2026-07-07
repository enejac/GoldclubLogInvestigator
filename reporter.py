"""
Reporter: format incidents as Markdown or CSV for dashboards and tickets.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable, Literal

from parser import Incident

OutputFormat = Literal["markdown", "csv"]


def incidents_to_markdown(incidents: Iterable[Incident], title: str | None = None) -> str:
    """Build a Markdown report with a summary and a table of all incidents."""
    rows = list(incidents)
    lines: list[str] = []

    header = title or "Log Investigator — Report"
    lines.append(f"# {header}")
    lines.append("")
    lines.append(f"**Total findings:** {len(rows)}")
    lines.append("")

    if not rows:
        lines.append("_No incidents matched the configured severity patterns._")
        return "\n".join(lines) + "\n"

    lines.append(
        "| Timestamp | Game | Severity | Error Type | Validation | Probable Cause | Log File | Line | "
        "First Cause Line | First Cause Snippet |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )

    for inc in rows:
        vstat = getattr(inc, "validation_status", None) or "—"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(inc.timestamp_display()),
                    _md_cell(inc.game),
                    _md_cell(inc.severity),
                    _md_cell(inc.error_type),
                    _md_cell(vstat),
                    _md_cell(inc.probable_cause),
                    _md_cell(inc.log_file_path),
                    str(inc.line_number),
                    str(inc.first_cause_line or "—"),
                    _md_cell(inc.first_cause_snippet or "—"),
                ]
            )
            + " |"
        )

    lines.append("")
    return "\n".join(lines)


def _md_cell(value: str) -> str:
    s = value.replace("|", "\\|").replace("\n", " ").strip()
    return s if s else "—"


def incidents_to_csv(incidents: Iterable[Incident]) -> str:
    """Serialize incidents to CSV (UTF-8 with header)."""
    buf = io.StringIO(newline="")
    fieldnames = [
        "timestamp",
        "game",
        "severity",
        "error_type",
        "probable_cause",
        "log_file_path",
        "line_number",
        "line_snippet",
        "first_cause_line",
        "first_cause_snippet",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for inc in incidents:
        writer.writerow(
            {
                "timestamp": inc.timestamp.isoformat() if inc.timestamp is not None else "",
                "game": inc.game,
                "severity": inc.severity,
                "error_type": inc.error_type,
                "validation_status": getattr(inc, "validation_status", "") or "",
                "validation_detail": getattr(inc, "validation_detail", "") or "",
                "probable_cause": inc.probable_cause,
                "log_file_path": inc.log_file_path,
                "line_number": inc.line_number,
                "line_snippet": inc.line_snippet,
                "first_cause_line": inc.first_cause_line or "",
                "first_cause_snippet": inc.first_cause_snippet or "",
            }
        )
    return buf.getvalue()


def write_report(
    incidents: Iterable[Incident],
    output_path: Path,
    fmt: OutputFormat,
    title: str | None = None,
) -> None:
    """Write Markdown or CSV to ``output_path`` (UTF-8)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "markdown":
        text = incidents_to_markdown(incidents, title=title)
    else:
        text = incidents_to_csv(incidents)
    output_path.write_text(text, encoding="utf-8")
