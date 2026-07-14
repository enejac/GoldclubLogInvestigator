"""
Lightweight case pack zip for developer handoff (metadata, bookmarks, incident list).

For full log excerpts and clustered context, see the root ``case_packer`` module.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parser import Incident

from parser_rules import format_related_tracking, match_known_issue

from config_manager import SettingsManager
from product_version import PRODUCT_VERSION_PLACEHOLDER
from network.accounting_scanner import fetch_accounting_data
from network.ai_summarizer import generate_incident_summary
from network.meter_comparator import compare_sas_and_xml
from network.sas_decoder import parse_sas_log_file


def _cabinet_tag(cabinet_id: str | None) -> str:
    if not cabinet_id or not str(cabinet_id).strip():
        return "UNKNOWN"
    s = str(cabinet_id).strip()
    u = s.upper()
    if u.startswith("GST"):
        return re.sub(r"[^A-Z0-9]", "", u)[:16] or "UNKNOWN"
    safe = re.sub(r'[^A-Za-z0-9._-]+', "_", s).strip("._") or "UNKNOWN"
    return safe[:24]


def suggest_case_pack_zip_name(
    cabinet_id: str | None,
    when: datetime | None = None,
) -> str:
    """``Case_GST#####_YYYYMMDD_HHMM.zip`` (or ``Case_UNKNOWN_…``)."""
    dt = when or datetime.now(timezone.utc)
    stamp = dt.strftime("%Y%m%d_%H%M")
    tag = _cabinet_tag(cabinet_id)
    return f"Case_{tag}_{stamp}.zip"


def _format_investigation_log(incidents: list[Incident]) -> str:
    from collections import Counter

    from reporter import _normalize_signature

    lines: list[str] = [
        "Log Investigator — filtered incidents (case pack)",
        f"Count: {len(incidents)}",
        "",
    ]
    if incidents:
        sev = Counter((i.severity or "UNKNOWN").upper() for i in incidents)
        lines.append("Severity breakdown:")
        for level in ("CRITICAL", "MEDIUM", "LOW", "INFO"):
            if sev.get(level):
                lines.append(f"  {level}: {sev[level]}")
        type_counts = Counter(i.error_type for i in incidents if i.error_type)
        if type_counts:
            lines.append("")
            lines.append("Top error types:")
            for err_type, count in type_counts.most_common(8):
                lines.append(f"  {count}x {err_type}")
        crit_sigs = Counter(
            _normalize_signature(i)
            for i in incidents
            if (i.severity or "").upper() == "CRITICAL"
        )
        if crit_sigs:
            lines.append("")
            lines.append("Top critical signatures:")
            for sig, count in crit_sigs.most_common(5):
                lines.append(f"  {count}x {sig}")
        lines.append("")

    for i, inc in enumerate(incidents, start=1):
        lines.append(f"--- Incident {i} ---")
        lines.append(f"timestamp:     {inc.timestamp_display()}")
        lines.append(f"game:          {inc.game}")
        lines.append(f"severity:      {inc.severity}")
        lines.append(f"error_type:    {inc.error_type}")
        lines.append(f"log_file_path: {inc.log_file_path}")
        lines.append(f"line_number:   {inc.line_number}")
        vs = getattr(inc, "validation_status", None)
        if vs:
            lines.append(f"validation:    {vs}")
            vd = getattr(inc, "validation_detail", None)
            if vd:
                lines.append(f"validation_detail: {vd}")
        lines.append(f"line_snippet:  {inc.line_snippet or '—'}")
        lines.append(f"probable_cause:\n{inc.probable_cause or '—'}")
        km = match_known_issue(inc.line_snippet or "", inc.severity or "")
        if km is not None:
            lines.append(f"related_tracking:\n{format_related_tracking(km)}")
        lines.append(f"incident_id:   {inc.id}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _generate_html_report(
    timestamp: str,
    remote_ip: str,
    files_included: list[str],
    audit_text: str,
    ai_summary: str,
    *,
    severity_counts: dict[str, int] | None = None,
    top_error_types: list[tuple[str, int]] | None = None,
    top_subsystems: list[tuple[str, int]] | None = None,
    incident_count: int = 0,
) -> str:
    """
    Build a dark-mode HTML incident summary for inclusion in the case zip.

    ``audit_text`` is shown in a ``<pre>``; border is red if a mismatch marker is present.
    """
    body = (
        audit_text.strip()
        if audit_text.strip()
        else "No SAS Audit performed for this case."
    )
    border_color = "#ff4444" if "❌" in body else "#00C851"
    safe_ts = html.escape(timestamp, quote=False)
    ip_display = html.escape(
        remote_ip if (remote_ip or "").strip() else "Local/Offline",
        quote=False,
    )
    files_line = html.escape(", ".join(files_included) or "—", quote=False)
    safe_audit = html.escape(body, quote=False)
    safe_ai = html.escape((ai_summary or "").strip() or "—", quote=False)

    stats_rows = ""
    if severity_counts:
        for level in ("CRITICAL", "MEDIUM", "LOW", "INFO"):
            if severity_counts.get(level):
                stats_rows += (
                    f"<tr><th>{html.escape(level)}</th>"
                    f"<td>{severity_counts[level]}</td></tr>"
                )
    type_rows = ""
    for err_type, count in (top_error_types or [])[:8]:
        type_rows += (
            f"<tr><td>{html.escape(err_type)}</td><td>{count}</td></tr>"
        )
    subsystem_rows = ""
    for sub, count in (top_subsystems or [])[:8]:
        subsystem_rows += (
            f"<tr><td>{html.escape(sub)}</td><td>{count}</td></tr>"
        )

    stats_block = ""
    if stats_rows or type_rows or subsystem_rows:
        stats_block = (
            '<div class="card">\n'
            "  <h3>Incident Statistics</h3>\n"
            f'  <div class="muted">Total incidents in this pack: {incident_count}</div>\n'
            "  <table>\n"
            f"    {stats_rows}\n"
            "  </table>\n"
        )
        if type_rows:
            stats_block += (
                "  <h4>Top error types</h4>\n"
                "  <table><tr><th>Error type</th><th>Count</th></tr>\n"
                f"    {type_rows}\n"
                "  </table>\n"
            )
        if subsystem_rows:
            stats_block += (
                "  <h4>Top subsystems</h4>\n"
                "  <table><tr><th>Subsystem</th><th>Count</th></tr>\n"
                f"    {subsystem_rows}\n"
                "  </table>\n"
            )
        stats_block += "</div>\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Goldclub QA Incident Report</title>
<style>
body {{
  font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
  background-color: #1e1e1e;
  color: #e0e0e0;
  margin: 40px;
  line-height: 1.45;
}}
h1 {{
  color: #ffffff;
  border-bottom: 2px solid #333;
  padding-bottom: 10px;
}}
.card {{
  background-color: #2d2d2d;
  padding: 20px;
  border-radius: 8px;
  margin-bottom: 20px;
  box-shadow: 0 4px 6px rgba(0,0,0,0.3);
}}
.card h3 {{
  margin-top: 0;
  color: #cfcfcf;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-top: 10px;
}}
th, td {{
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid #444;
}}
th {{
  color: #88b8ff;
  width: 220px;
}}
pre.audit {{
  background-color: #121212;
  padding: 15px;
  border-radius: 5px;
  border-left: 5px solid {border_color};
  overflow-x: auto;
  color: #c8ffc8;
  font-family: Consolas, "Courier New", monospace;
  white-space: pre-wrap;
  word-break: break-word;
}}
.muted {{
  color: #a7a7a7;
  font-size: 12px;
}}
</style>
</head>
<body>
<h1>🎰 Goldclub QA Incident Report</h1>

<div class="card">
  <h3>System Information</h3>
  <table>
    <tr><th>Report Generated</th><td>{safe_ts}</td></tr>
    <tr><th>Cabinet IP</th><td>{ip_display}</td></tr>
    <tr><th>Attached Files</th><td>{files_line}</td></tr>
  </table>
</div>
{stats_block}
<div class="card">
  <h3>Technical Executive Summary</h3>
  <div class="muted">Privacy-safe: generated from aggregated stats + audit status (no raw logs).</div>
  <pre class="audit" style="border-left-color:#88b8ff; color:#e0e0e0;">{safe_ai}</pre>
</div>

<div class="card">
  <h3>Automated Meter Audit (SAS vs XML)</h3>
  <pre class="audit">{safe_audit}</pre>
</div>
</body>
</html>
"""


def _audit_text_for_html(
    meter_comparison: str,
    *,
    wrote_sas: bool,
    wrote_accounting: bool,
) -> str:
    """Human-readable audit block for HTML (comparison output or explanation)."""
    if meter_comparison.strip():
        return meter_comparison
    if wrote_sas and not wrote_accounting:
        return (
            "SAS analysis is in sas_analysis.txt. "
            "Meter comparison was not run (no remote cabinet / gm2au XML in this pack)."
        )
    if wrote_accounting and not wrote_sas:
        return (
            "Accounting summary is in accounting_summary.txt. "
            "No SAS file was attached for automated SAS vs XML comparison."
        )
    if wrote_sas:
        return (
            "SAS decode is in sas_analysis.txt. "
            "Meter comparison was not run (gm2au state missing or empty)."
        )
    return ""


def create_case_pack(
    target_path: str | os.PathLike[str],
    machine_info: dict[str, Any],
    bookmarks: dict[str, str],
    incidents: list[Incident],
    *,
    screenshot_path: str | None = None,
    remote_ip: str | None = None,
    sas_file_path: str | None = None,
    software_version: str | None = None,
) -> str:
    """
    Write ``metadata.json``, ``bookmarks.json``, and ``investigation.log`` into a zip.

    If ``screenshot_path`` points to an existing file, it is stored as ``evidence.png``.
    If ``remote_ip`` is set, ``accounting_summary.txt`` is added (remote capture + gm2au).
    If ``sas_file_path`` points to an existing file, ``sas_analysis.txt`` is added.
    ``IncidentReport.html`` (dark-mode summary) is always included.

    Parent directories are created as needed. Returns the resolved path to the zip file.
    """
    out = Path(os.fspath(target_path)).expanduser()
    if out.suffix.lower() != ".zip":
        out = out.with_suffix(".zip")
    parent = out.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        usage = shutil.disk_usage(parent)
    except OSError:
        pass
    else:
        if usage.free < 64_000:
            raise OSError("Insufficient free disk space to create case pack.")

    exported_at = datetime.now(timezone.utc).isoformat()
    pb_raw = (software_version or "").strip()
    product_build = (
        pb_raw
        if pb_raw and pb_raw != PRODUCT_VERSION_PLACEHOLDER
        else None
    )
    metadata: dict[str, Any] = {
        "machine_info": dict(machine_info),
        "exported_at_utc": exported_at,
        "product_build": product_build,
    }
    meta_bytes = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
    bm_payload = {
        "incident_id_to_note": dict(bookmarks),
        "entries": [
            {"incident_id": iid, "note": note}
            for iid, note in sorted(bookmarks.items(), key=lambda x: x[0])
        ],
    }
    bm_bytes = json.dumps(bm_payload, indent=2, ensure_ascii=False).encode("utf-8")
    log_body = _format_investigation_log(incidents)
    log_bytes = log_body.encode("utf-8", errors="replace")

    accounting_bytes: bytes | None = None
    state_text_for_compare = ""
    ip_for_accounting = (remote_ip or "").strip()
    if ip_for_accounting:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in ip_for_accounting)[
            :40
        ]
        stamp = int(time.time() * 1000) % 1_000_000_000_000
        cap_path = Path(tempfile.gettempdir()) / f"glci_casepack_{safe}_{stamp}.jpg"
        cap_str = str(cap_path)
        try:
            qr_text, state_text = fetch_accounting_data(ip_for_accounting, cap_str)
        except Exception as e:  # noqa: BLE001 — keep case pack usable if accounting fails
            qr_text = f"(accounting fetch raised an exception: {e})"
            state_text = ""
        finally:
            try:
                if cap_path.is_file():
                    cap_path.unlink()
            except OSError:
                pass
        content = (
            f"=== ACCOUNTING QR PAYLOAD ===\n{qr_text}\n\n"
            f"=== BACKEND STATE (gm2au) ===\n{state_text}"
        )
        accounting_bytes = content.encode("utf-8", errors="replace")
        state_text_for_compare = state_text

    shot: Path | None = None
    if screenshot_path:
        p = Path(os.fspath(screenshot_path)).expanduser()
        if p.is_file():
            shot = p.resolve()

    meter_comparison = ""
    wrote_sas = False
    wrote_accounting = accounting_bytes is not None

    with zipfile.ZipFile(
        out,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as zf:
        files_included: list[str] = []
        zf.writestr("metadata.json", meta_bytes)
        files_included.append("metadata.json")
        zf.writestr("bookmarks.json", bm_bytes)
        files_included.append("bookmarks.json")
        zf.writestr("investigation.log", log_bytes)
        files_included.append("investigation.log")
        if accounting_bytes is not None:
            zf.writestr("accounting_summary.txt", accounting_bytes)
            files_included.append("accounting_summary.txt")
        if shot is not None:
            zf.write(shot, arcname="evidence.png")
            files_included.append("evidence.png")
        sas_path = (sas_file_path or "").strip()
        if sas_path:
            p_sas = Path(os.fspath(sas_path)).expanduser()
            if p_sas.is_file():
                wrote_sas = True
                parsed_sas = parse_sas_log_file(str(p_sas))
                if ip_for_accounting and state_text_for_compare.strip():
                    meter_comparison = compare_sas_and_xml(str(p_sas), state_text_for_compare)
                    parsed_sas = meter_comparison + "\n" + parsed_sas
                zf.writestr(
                    "sas_analysis.txt",
                    parsed_sas.encode("utf-8", errors="replace"),
                )
                files_included.append("sas_analysis.txt")

        audit_html_body = _audit_text_for_html(
            meter_comparison,
            wrote_sas=wrote_sas,
            wrote_accounting=wrote_accounting,
        )

        # --- Privacy-safe AI summary (aggregated counts only; no raw logs) ---
        sev_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        games: dict[str, int] = {}
        for inc in incidents:
            sev = (inc.severity or "").strip() or "—"
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
            et = (inc.error_type or "").strip() or "—"
            type_counts[et] = type_counts.get(et, 0) + 1
            g = (inc.game or "").strip() or "—"
            games[g] = games.get(g, 0) + 1
        top_error_types = sorted(type_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
        top_games = sorted(games.items(), key=lambda x: (-x[1], x[0]))[:5]
        stats = {
            "exported_at_utc": exported_at,
            "cabinet_ip": ip_for_accounting or None,
            "incident_count": len(incidents),
            "severity_counts": dict(sorted(sev_counts.items(), key=lambda x: x[0])),
            "top_error_types": top_error_types,
            "top_games": top_games,
            "machine_info": {k: machine_info.get(k) for k in ("cabinet_id", "Hostname", "active_game", "Drift")},
            "product_build": product_build,
        }
        api_key = SettingsManager.get_gemini_api_key()
        ai_text = generate_incident_summary(
            stats,
            audit_html_body,
            api_key,
            software_version=software_version,
            ai_provider=SettingsManager.get_ai_provider(),
            groq_api_key=SettingsManager.get_groq_api_key(),
            openrouter_api_key=SettingsManager.get_openrouter_api_key(),
            venice_api_key=SettingsManager.get_venice_api_key(),
        )
        html_report = _generate_html_report(
            exported_at,
            ip_for_accounting,
            files_included,
            audit_html_body,
            ai_text,
            severity_counts=dict(sorted(sev_counts.items(), key=lambda x: x[0])),
            top_error_types=top_error_types,
            top_subsystems=top_games,
            incident_count=len(incidents),
        )
        zf.writestr("IncidentReport.html", html_report.encode("utf-8"))

    return str(out.resolve())
