"""
Case Snapshot / package: zip filtered incidents, log context, and metadata.

Context excerpts are deduplicated when multiple incidents share the same log file
and fall within a small line-number window (proxy for “same burst”).
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parser import Incident, read_log_context
from reporter import incidents_to_csv, incidents_to_markdown


# Lines between incidents on the same file that still share one context read
DEFAULT_LINE_CLUSTER_GAP = 15
CONTEXT_LINES_BEFORE = 50
CONTEXT_LINES_AFTER = 5


def _read_app_version() -> str:
    init_py = Path(__file__).resolve().parent / "__init__.py"
    if not init_py.is_file():
        return "1.0.0"
    for line in init_py.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("__version__"):
            _, _, rhs = s.partition("=")
            return rhs.strip().strip('"').strip("'").rstrip(",")
    return "1.0.0"


def _sanitize_zip_component(name: str, max_len: int = 80) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    s = re.sub(r"_+", "_", s).strip("._")
    return (s or "file")[:max_len]


def _dominant_game(incidents: list[Incident]) -> str:
    if not incidents:
        return "unknown"
    games = [i.game for i in incidents if i.game and i.game != "unknown"]
    if not games:
        return "unknown"
    return Counter(games).most_common(1)[0][0]


def cluster_incidents_by_line_proximity(
    incidents: list[Incident],
    line_gap: int = DEFAULT_LINE_CLUSTER_GAP,
) -> list[list[Incident]]:
    """Group incidents that share a path and have line numbers within ``line_gap``."""
    by_path: dict[str, list[Incident]] = {}
    for inc in incidents:
        by_path.setdefault(inc.log_file_path, []).append(inc)

    clusters: list[list[Incident]] = []
    for _path, group in by_path.items():
        group.sort(key=lambda x: x.line_number)
        cur = [group[0]]
        hi = group[0].line_number
        for inc in group[1:]:
            if inc.line_number - hi <= line_gap:
                cur.append(inc)
                hi = max(hi, inc.line_number)
            else:
                clusters.append(cur)
                cur = [inc]
                hi = inc.line_number
        clusters.append(cur)
    return clusters


def build_case_zip(
    output_zip: Path,
    incidents: list[Incident],
    *,
    scan_path: str,
    target_ip: str | None,
    remote_mode: bool,
    user_notes: str,
    app_version: str | None = None,
    validation_summary: dict[str, Any] | None = None,
    machine_registry: dict[str, Any] | None = None,
    session_active_duration: str | None = None,
) -> None:
    """
    Write a case package zip to ``output_zip`` (overwrites if present).

    Includes ``metadata.json``, ``snapshot_report.md``, ``snapshot_report.csv``,
    and ``logs/NNN_*.txt`` context excerpts (deduplicated per cluster).
    """
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    version = app_version or _read_app_version()
    now = datetime.now(timezone.utc)
    iso_ts = now.isoformat()
    dominant = _dominant_game(incidents)
    safe_game = _sanitize_zip_component(dominant, 48)

    clusters = cluster_incidents_by_line_proximity(incidents)
    cluster_meta: list[dict[str, Any]] = []

    with zipfile.ZipFile(
        output_zip,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        # Reports (filtered view only)
        md = incidents_to_markdown(
            incidents,
            title="Log Investigator — Case snapshot (filtered view)",
        )
        zf.writestr("snapshot_report.md", md.encode("utf-8"))
        csv_text = incidents_to_csv(incidents)
        zf.writestr("snapshot_report.csv", csv_text.encode("utf-8"))

        for idx, group in enumerate(clusters, start=1):
            path_str = group[0].log_file_path
            lines_sorted = sorted(i.line_number for i in group)
            lo, hi = lines_sorted[0], lines_sorted[-1]
            center = hi
            log_path = Path(path_str)
            body = read_log_context(
                log_path,
                center,
                context_before=CONTEXT_LINES_BEFORE,
                context_after=CONTEXT_LINES_AFTER,
            )
            stem = _sanitize_zip_component(log_path.name, 40)
            arcname = f"logs/{idx:03d}_{stem}_L{lo}-{hi}.txt"
            header = (
                f"# Context excerpt\n"
                f"# Source: {path_str}\n"
                f"# Incident line(s): {lines_sorted}\n"
                f"# Cluster center line (read window): {center}\n\n"
            )
            zf.writestr(arcname, (header + body).encode("utf-8", errors="replace"))
            cluster_meta.append(
                {
                    "log_file": path_str,
                    "zip_internal_path": arcname.replace("\\", "/"),
                    "line_range": [lo, hi],
                    "incident_line_numbers": lines_sorted,
                    "incident_count": len(group),
                }
            )

        manifest: dict[str, Any] = {
            "snapshot_timestamp_utc": iso_ts,
            "application_version": version,
            "user_notes": user_notes.strip(),
            "scan_path": scan_path,
            "target_ip": target_ip if remote_mode else None,
            "connection_mode": "remote" if remote_mode else "local",
            "active_theme_or_game": dominant,
            "filtered_incident_count": len(incidents),
            "context_line_cluster_gap": DEFAULT_LINE_CLUSTER_GAP,
            "context_lines_before": CONTEXT_LINES_BEFORE,
            "context_lines_after": CONTEXT_LINES_AFTER,
            "context_clusters": cluster_meta,
            "validation_summary": validation_summary,
            "machine_registry": machine_registry,
            "incidents": [
                {
                    "timestamp": i.timestamp.isoformat() if i.timestamp is not None else None,
                    "game": i.game,
                    "severity": i.severity,
                    "error_type": i.error_type,
                    "probable_cause": i.probable_cause,
                    "log_file_path": i.log_file_path,
                    "line_number": i.line_number,
                    "line_snippet": i.line_snippet,
                    "first_cause_line": i.first_cause_line,
                    "first_cause_snippet": i.first_cause_snippet,
                    "validation_status": getattr(i, "validation_status", None),
                    "validation_detail": getattr(i, "validation_detail", None),
                }
                for i in incidents
            ],
        }
        if session_active_duration:
            manifest["session_active_duration"] = session_active_duration
        zf.writestr(
            "metadata.json",
            json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
        )


def suggest_case_zip_name(incidents: list[Incident], when: datetime | None = None) -> str:
    """``Case_YYYYMMDD_HHMM_[Game].zip`` per product naming."""
    dt = when or datetime.now(timezone.utc)
    stamp = dt.strftime("%Y%m%d_%H%M")
    game = _dominant_game(incidents)
    g = _sanitize_zip_component(game, 40) if game != "unknown" else "Multi"
    return f"Case_{stamp}_{g}.zip"


class CasePacker:
    """Thin façade for GUI/CLI (same logic as module functions)."""

    @staticmethod
    def pack(
        output_zip: Path,
        incidents: list[Incident],
        *,
        scan_path: str,
        target_ip: str | None,
        remote_mode: bool,
        user_notes: str,
        app_version: str | None = None,
        validation_summary: dict[str, Any] | None = None,
        machine_registry: dict[str, Any] | None = None,
        session_active_duration: str | None = None,
    ) -> None:
        build_case_zip(
            output_zip,
            incidents,
            scan_path=scan_path,
            target_ip=target_ip,
            remote_mode=remote_mode,
            user_notes=user_notes,
            app_version=app_version,
            validation_summary=validation_summary,
            machine_registry=machine_registry,
            session_active_duration=session_active_duration,
        )

    @staticmethod
    def suggested_filename(incidents: list[Incident]) -> str:
        return suggest_case_zip_name(incidents)
