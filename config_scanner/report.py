"""HTML report generation for config scan comparisons."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from config_scanner.build_version import BuildInfo, format_build_info_log_line
from config_scanner.xml_diff import ContentChange, FileDiff, change_summary


def _encode(value: str | None) -> str:
    if value is None:
        return '<span class="null">(none)</span>'
    return html.escape(value)


def _content_diff_html(content_diff: list[ContentChange]) -> str:
    if not content_diff:
        return '<p class="muted">No detailed diff available.</p>'
    rows = []
    for change in content_diff:
        rows.append(
            "<tr "
            f'class="change-{html.escape(change.change_type)}">'
            f"<td class=\"change-type\">{html.escape(change.change_type)}</td>"
            f"<td class=\"change-path\"><code>{html.escape(change.path)}</code></td>"
            f"<td class=\"change-old\">{_encode(change.old_value)}</td>"
            f"<td class=\"change-new\">{_encode(change.new_value)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"content-diff\">"
        "<thead><tr><th>Type</th><th>Path / Key</th><th>Baseline</th><th>Target</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _file_diff_section(file_diff: FileDiff) -> str:
    anchor = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in file_diff.relative_path)
    return (
        f'<section class="file-diff status-{html.escape(file_diff.status)}" id="{anchor}">'
        f"<h3>{html.escape(file_diff.relative_path)} "
        f'<span class="badge">{html.escape(file_diff.status)}</span></h3>'
        '<div class="hash-row">'
        f"<div><strong>Baseline SHA1:</strong> <code>{_encode(file_diff.baseline_sha1)}</code></div>"
        f"<div><strong>Target SHA1:</strong> <code>{_encode(file_diff.target_sha1)}</code></div>"
        "</div>"
        f"{_content_diff_html(file_diff.content_diff)}"
        "</section>"
    )


def _warnings_html(warnings: tuple[str, ...] | list[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{html.escape(text)}</li>" for text in warnings)
    return (
        '<section class="compare-warnings panel">'
        "<h2>Comparison warnings</h2>"
        "<ul>"
        f"{items}"
        "</ul>"
        "</section>"
    )


def write_comparison_report(
    *,
    baseline_info: BuildInfo,
    target_info: BuildInfo,
    baseline_snapshot: str,
    target_snapshot: str,
    file_diffs: list[FileDiff],
    template_path: Path,
    output_path: Path,
    warnings: tuple[str, ...] | list[str] = (),
) -> Path:
    if not template_path.is_file():
        raise FileNotFoundError(f"Report template not found: {template_path}")

    summary = change_summary(file_diffs)
    changed_files = [item for item in file_diffs if item.status != "unchanged"]

    table_rows = []
    for file_diff in file_diffs:
        anchor = "".join(
            ch if ch.isalnum() or ch in "-_" else "-" for ch in file_diff.relative_path
        )
        path = html.escape(file_diff.relative_path)
        table_rows.append(
            f'<tr class="status-{html.escape(file_diff.status)}" '
            f'data-status="{html.escape(file_diff.status)}" data-path="{path}">'
            f'<td><a href="#{anchor}">{path}</a></td>'
            f"<td>{html.escape(file_diff.status)}</td>"
            f"<td><code>{_encode(file_diff.baseline_sha1)}</code></td>"
            f"<td><code>{_encode(file_diff.target_sha1)}</code></td>"
            "</tr>"
        )

    nav_links = [
        (
            f'<li><a href="#{"".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in item.relative_path)}">'
            f"{html.escape(item.relative_path)}</a> "
            f'<span class="badge">{html.escape(item.status)}</span></li>'
        )
        for item in changed_files
    ]
    detail_sections = [_file_diff_section(item) for item in changed_files]

    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "__REPORT_TITLE__": f"Config Scan Diff: {baseline_snapshot} vs {target_snapshot}",
        "__BASELINE_SNAPSHOT__": html.escape(baseline_snapshot),
        "__TARGET_SNAPSHOT__": html.escape(target_snapshot),
        "__BASELINE_BUILD__": html.escape(baseline_info.build_number or ""),
        "__TARGET_BUILD__": html.escape(target_info.build_number or ""),
        "__BASELINE_BRANCH__": html.escape(baseline_info.branch or ""),
        "__TARGET_BRANCH__": html.escape(target_info.branch or ""),
        "__BASELINE_PRODUCT_VERSION__": html.escape(baseline_info.product_version or ""),
        "__TARGET_PRODUCT_VERSION__": html.escape(target_info.product_version or ""),
        "__BASELINE_SOURCE_VERSION__": html.escape(baseline_info.source_version or ""),
        "__TARGET_SOURCE_VERSION__": html.escape(target_info.source_version or ""),
        "__BASELINE_EXE_VERSION__": html.escape(
            baseline_info.exe_product_version or baseline_info.exe_file_version or ""
        ),
        "__TARGET_EXE_VERSION__": html.escape(
            target_info.exe_product_version or target_info.exe_file_version or ""
        ),
        "__BASELINE_EXE_NAME__": html.escape(baseline_info.exe_product_name or ""),
        "__TARGET_EXE_NAME__": html.escape(target_info.exe_product_name or ""),
        "__BASELINE_VERSION_LINE__": html.escape(format_build_info_log_line(baseline_info)),
        "__TARGET_VERSION_LINE__": html.escape(format_build_info_log_line(target_info)),
        "__BASELINE_SCAN_TIME__": html.escape(baseline_info.scan_timestamp),
        "__TARGET_SCAN_TIME__": html.escape(target_info.scan_timestamp),
        "__BASELINE_GAME_DRIVE__": html.escape(baseline_info.game_drive),
        "__TARGET_GAME_DRIVE__": html.escape(target_info.game_drive),
        "__COUNT_ADDED__": str(summary["added"]),
        "__COUNT_REMOVED__": str(summary["removed"]),
        "__COUNT_MODIFIED__": str(summary["modified"]),
        "__COUNT_UNCHANGED__": str(summary["unchanged"]),
        "__FILE_TABLE_ROWS__": "\n".join(table_rows),
        "__CHANGED_NAV__": (
            "\n".join(nav_links)
            if nav_links
            else '<li class="muted">No changes detected.</li>'
        ),
        "__FILE_DIFF_SECTIONS__": (
            "\n".join(detail_sections)
            if detail_sections
            else '<p class="muted">No file-level differences to show.</p>'
        ),
        "__GENERATED_AT__": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "__COMPARE_WARNINGS__": _warnings_html(warnings),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")
    return output_path
