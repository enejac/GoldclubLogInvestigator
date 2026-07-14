"""
Reporter: format incidents as Markdown or CSV for dashboards and tickets.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Literal

from parser import Incident

OutputFormat = Literal["markdown", "csv"]

_SEVERITY_ORDER = ("CRITICAL", "MEDIUM", "LOW", "INFO")


def _severity_rank(severity: str) -> int:
    s = (severity or "").strip().upper()
    try:
        return _SEVERITY_ORDER.index(s)
    except ValueError:
        return len(_SEVERITY_ORDER)


def _strip_log_line_prefix(text: str) -> str:
    """Remove timestamp and log-level tokens for signature / collapse grouping."""
    s = text.strip()
    s = re.sub(
        r"^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*",
        "",
        s,
    )
    s = re.sub(r"\b(?:INFO|WARN|ERRO|CRIT|ERROR|DEBUG)\b\s*", "", s, flags=re.I)
    s = re.sub(r"\[:[^\]]*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_signature(inc: Incident) -> str:
    """Collapse noisy snippets into a stable crash signature for summary tables."""
    text = _strip_log_line_prefix(inc.line_snippet or inc.error_type or "")
    if len(text) > 120:
        text = text[:117] + "..."
    label = (inc.error_type or "Unknown").strip()
    return f"{label}: {text}" if text else label


def incident_group_key(inc: Incident) -> tuple[str, str, str, str]:
    """Key for collapsing consecutive rows with the same underlying fault."""
    body = _strip_log_line_prefix(inc.line_snippet or "")
    if len(body) > 100:
        body = body[:97] + "..."
    return (
        (inc.severity or "").strip(),
        (inc.error_type or "").strip(),
        (inc.game or "").strip(),
        body,
    )


def _incident_summary_sections(rows: list[Incident]) -> list[str]:
    """Markdown blocks: severity counts, subsystems, top error types, top signatures."""
    if not rows:
        return []

    lines: list[str] = []
    sev = Counter((i.severity or "UNKNOWN").upper() for i in rows)
    lines.append("## Summary")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("| --- | ---: |")
    for level in _SEVERITY_ORDER:
        if sev.get(level):
            lines.append(f"| {level} | {sev[level]} |")
    for level, count in sorted(sev.items()):
        if level not in _SEVERITY_ORDER:
            lines.append(f"| {level} | {count} |")
    lines.append("")

    game_counts = Counter(i.game for i in rows if (i.game or "").strip())
    if game_counts:
        lines.append("### By subsystem / game")
        lines.append("")
        lines.append("| Subsystem | Count |")
        lines.append("| --- | ---: |")
        for game, count in game_counts.most_common(12):
            lines.append(f"| {_md_cell(game)} | {count} |")
        lines.append("")

    type_counts = Counter(i.error_type for i in rows if (i.error_type or "").strip())
    if type_counts:
        lines.append("### By error type")
        lines.append("")
        lines.append("| Error type | Count |")
        lines.append("| --- | ---: |")
        for err_type, count in type_counts.most_common(15):
            lines.append(f"| {_md_cell(err_type)} | {count} |")
        lines.append("")

    crit = [i for i in rows if (i.severity or "").upper() == "CRITICAL"]
    if crit:
        sig_counts = Counter(_normalize_signature(i) for i in crit)
        lines.append("### Top critical signatures")
        lines.append("")
        lines.append("| Signature | Count |")
        lines.append("| --- | ---: |")
        for sig, count in sig_counts.most_common(10):
            lines.append(f"| {_md_cell(sig)} | {count} |")
        lines.append("")

    return lines


def incidents_to_markdown(incidents: Iterable[Incident], title: str | None = None) -> str:
    """Build a Markdown report with summary sections and a table of all incidents."""
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

    lines.extend(_incident_summary_sections(rows))

    lines.append("## Incidents")
    lines.append("")
    lines.append(
        "| Timestamp | Subsystem | Severity | Error Type | Validation | Probable Cause | Log File | Line | "
        "First Cause Line | First Cause Snippet |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )

    sorted_rows = sorted(
        rows,
        key=lambda i: (
            _severity_rank(i.severity or ""),
            i.timestamp or "",
            i.log_file_path,
            i.line_number,
        ),
    )

    for inc in sorted_rows:
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
        "validation_status",
        "validation_detail",
        "signature",
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
                "signature": _normalize_signature(inc),
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
