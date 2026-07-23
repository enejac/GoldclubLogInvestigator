"""HTML report generation for config scan comparisons."""

from __future__ import annotations

import html
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from config_scanner.build_version import BuildInfo, format_build_info_log_line
from config_scanner.xml_diff import (
    ContentChange,
    FileDiff,
    change_summary,
    is_encrypted_origin_config_path,
)

_FILE_HINTS: dict[str, str] = {
    "mgconfig.xml": "multigamer settings",
    "texts.xml": "game text strings",
    "hwsubsys.xml": "hardware subsystem",
    "gameconfig.xml": "game configuration",
}

_STATUS_LABELS = {
    "modified": "Modified",
    "added": "Added",
    "removed": "Removed",
}

_FILE_LINE_RE = re.compile(
    r"^(?:Modified|Added|Removed):\s+(.+?)(?:\s+\([^)]+\))?\s*$",
    re.IGNORECASE,
)


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
    encrypted = is_encrypted_origin_config_path(file_diff.relative_path)
    origin_class = " encrypted-origin" if encrypted else " plain-origin"
    origin_badge = (
        ' <span class="badge badge-encrypted">encrypted on disk</span>'
        if encrypted
        else ' <span class="badge badge-plain">plain file</span>'
    )
    return (
        f'<section class="file-diff status-{html.escape(file_diff.status)}'
        f'{origin_class}" id="{anchor}">'
        f"<h3>{html.escape(file_diff.relative_path)} "
        f'<span class="badge">{html.escape(file_diff.status)}</span>'
        f"{origin_badge}</h3>"
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


def format_diff_cell_value(value: str | None, *, max_len: int = 40) -> str:
    """Compact baseline/target cell text for the compare panel.

    Distinguishes missing keys from present-but-blank values so Apply is unambiguous.
    """
    if value is None:
        return "not present"
    stripped = value.strip()
    if not stripped:
        return '"" (blank)'
    if len(stripped) > max_len:
        return stripped[: max_len - 1] + "…"
    return stripped


def format_apply_value_detail(value: str | None) -> str:
    """Full value text for Apply confirmations (not truncated)."""
    if value is None:
        return "not present (setting missing in that snapshot)"
    stripped = value.strip()
    if not stripped:
        return '"" (blank / empty string — the setting exists but has no text)'
    return value


def _format_plain_value(value: str | None) -> str:
    if value is None:
        return "not present"
    stripped = value.strip()
    if not stripped:
        return '"" (blank)'
    return stripped


def apply_button_label(value: str | None) -> str:
    """Short button caption: blank values say Write blank so the action is obvious."""
    if value is None:
        return "Write"
    if not value.strip():
        return "Write blank"
    return "Write"


def _setting_name(path: str) -> str:
    segment = path.rsplit("/", 1)[-1]
    name_match = re.search(r"\[@name='([^']+)'\]", segment)
    if name_match:
        return name_match.group(1)
    element_match = re.match(r"^([A-Za-z_][\w]*)", segment)
    if element_match:
        return element_match.group(1)
    if path.startswith("line:"):
        line_no = path.split(":", 1)[-1]
        return f"Line {line_no}"
    return path


def _file_label(relative_path: str) -> str:
    hint = _FILE_HINTS.get(Path(relative_path).name.casefold())
    if hint:
        return f"{relative_path} ({hint})"
    return relative_path


def file_diff_header_label(relative_path: str, status: str) -> str:
    """Return a changed-file header line for UI panels."""
    status_label = _STATUS_LABELS.get(status, status.title())
    label = f"{status_label}: {_file_label(relative_path)}"
    if is_encrypted_origin_config_path(relative_path):
        return f"{label}  [encrypted on disk]"
    return label


def compare_panel_encrypted_origin_styles() -> dict[str, str]:
    """Qt stylesheets for encrypted-origin vs plain config rows in the compare panel."""
    return {
        "encrypted_header": "font-weight: bold; color: #B35C00;",
        "encrypted_setting": "color: #B35C00;",
        "encrypted_value": "color: #8A4500;",
        "encrypted_value_muted": "color: #A67C52;",
        "plain_header": "font-weight: bold; color: #1F4E79;",
        "plain_setting": "color: #1A1A1A;",
        "plain_value": "color: #1A1A1A;",
        "plain_value_muted": "color: #858585;",
        "legend": "color: #666666; font-size: 11px;",
    }


def setting_display_name(path: str) -> str:
    """Return the human-readable setting name from a diff path."""
    return _setting_name(path)


def format_setting_change_description(change: ContentChange) -> str:
    """Return a one-line setting change description without leading indent."""
    return _format_content_change(change).strip()


_HEX_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]+$")
_XML_HEX_TAG_RE = re.compile(r"^_x[0-9A-Fa-f]{4}_[0-9A-Fa-fA-F]*$", re.IGNORECASE)


def looks_like_ciphertext_token(value: str | None) -> bool:
    """True for GoldClub gcxml-style hex blobs (and XML ``_x00NN_`` encoded tags)."""
    if value is None:
        return False
    text = value.strip()
    if not text:
        return False
    if _XML_HEX_TAG_RE.fullmatch(text):
        return True
    # Encoded tag often appears as path segment without being a fullmatch above
    if text.startswith("_x") and len(text) >= 12:
        rest = text[2:]
        if "_" in rest:
            code, _, payload = rest.partition("_")
            if len(code) == 4 and _HEX_TOKEN_RE.fullmatch(code) and (
                not payload or _HEX_TOKEN_RE.fullmatch(payload)
            ):
                return True
    if len(text) >= 8 and _HEX_TOKEN_RE.fullmatch(text):
        return True
    # Long mostly-hex tokens (allow tiny non-hex noise)
    if len(text) >= 12:
        hex_chars = sum(1 for ch in text if ch in "0123456789abcdefABCDEF")
        if hex_chars / len(text) >= 0.9:
            return True
    return False


def looks_like_writable_setting_name(name: str) -> bool:
    """True for normal config identifiers (CamelCase / snake / spaced labels), not ciphertext IDs."""
    text = (name or "").strip()
    if not text or text.startswith("Line "):
        return False
    if looks_like_ciphertext_token(text):
        return False
    # Plain gcxml leaves use names like keepPaytableUserSelection, still time, board 01.
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./ \-]*", text) and re.search(r"[A-Za-z]", text):
        compact = re.sub(r"[\s_./\-]+", "", text)
        if looks_like_ciphertext_token(compact):
            return False
        return True
    return False


def is_actionable_content_change(change: ContentChange) -> bool:
    """True when a field-level Write is meaningful (readable setting, not gcxml churn)."""
    name = setting_display_name(change.path)
    if looks_like_writable_setting_name(name):
        return True
    # Plain path segment that is a writable name
    for segment in change.path.replace("\\", "/").split("/"):
        seg_name = _setting_name(segment) if segment else ""
        if looks_like_writable_setting_name(seg_name):
            # Still reject if both values are pure ciphertext (encrypted payload under a
            # misleading path). One plaintext side is enough to surface.
            values = [v for v in (change.old_value, change.new_value) if v is not None]
            if not values:
                return True
            if any(not looks_like_ciphertext_token(v) for v in values):
                return True
    return False


def prioritize_content_changes(changes: list[ContentChange]) -> list[ContentChange]:
    """Stable-sort: writable/readable settings first, opaque gcxml tokens last."""
    actionable = [c for c in changes if is_actionable_content_change(c)]
    opaque = [c for c in changes if not is_actionable_content_change(c)]
    return actionable + opaque


def content_change_panel_partition(
    changes: list[ContentChange],
) -> tuple[list[ContentChange], list[ContentChange]]:
    """Split into (actionable, opaque) preserving relative order within each group."""
    actionable = [c for c in changes if is_actionable_content_change(c)]
    opaque = [c for c in changes if not is_actionable_content_change(c)]
    return actionable, opaque


def file_diff_has_actionable_changes(file_diff: FileDiff) -> bool:
    if not file_diff.content_diff:
        # Whole-file add/remove can still be written via snapshot restore.
        return file_diff.status in {"added", "removed", "modified"}
    return any(is_actionable_content_change(c) for c in file_diff.content_diff)


def prioritize_file_diffs_for_panel(file_diffs: list[FileDiff]) -> list[FileDiff]:
    """Changed files with writable settings first; encrypted-only churn last."""
    changed = changed_file_diffs(file_diffs)
    with_actionable: list[FileDiff] = []
    opaque_only: list[FileDiff] = []
    for item in changed:
        actionable, opaque = content_change_panel_partition(item.content_diff)
        if item.content_diff and not actionable and opaque:
            opaque_only.append(item)
        else:
            with_actionable.append(item)
    return with_actionable + opaque_only


def _changed_files_phrase(summary: dict[str, int]) -> str:
    added = summary.get("added", 0)
    removed = summary.get("removed", 0)
    modified = summary.get("modified", 0)
    changed = added + removed + modified
    unchanged = summary.get("unchanged", 0)

    if changed == 0:
        if unchanged == 1:
            return "no file changes (1 file matches)"
        return f"no file changes ({unchanged} files match)"

    type_parts: list[tuple[int, str]] = []
    if modified:
        type_parts.append((modified, "modified"))
    if added:
        type_parts.append((added, "added"))
    if removed:
        type_parts.append((removed, "removed"))

    if changed == 1:
        file_phrase = "1 file changed"
    elif len(type_parts) == 1:
        count, label = type_parts[0]
        file_phrase = f"{count} files {label}"
    else:
        type_detail = ", ".join(f"{count} {label}" for count, label in type_parts)
        file_phrase = f"{changed} files changed ({type_detail})"

    if unchanged == 0:
        return file_phrase
    if unchanged == 1:
        return f"{file_phrase} (1 unchanged)"
    return f"{file_phrase} ({unchanged} unchanged)"


def format_compare_summary(
    baseline_snapshot: str,
    target_snapshot: str,
    summary: dict[str, int],
    warnings: tuple[str, ...] | list[str] = (),
) -> str:
    """Return a one-line compare summary naming both snapshots."""
    changes = _changed_files_phrase(summary)
    text = f"Comparing {baseline_snapshot} \u2192 {target_snapshot}: {changes}"
    if warnings:
        text += "  |  " + "  |  ".join(warnings)
    return text


def _format_content_change(change: ContentChange) -> str:
    setting = _setting_name(change.path)
    if change.change_type == "added":
        return f"  + {setting}: {_format_plain_value(change.new_value)}"
    if change.change_type == "removed":
        return f"  - {setting}: was {_format_plain_value(change.old_value)}"
    return (
        f"  {setting}: {_format_plain_value(change.old_value)}"
        f" \u2192 {_format_plain_value(change.new_value)}"
    )


COMPARE_PANEL_MAX_FILES = 25
COMPARE_PANEL_MAX_CHANGES_PER_FILE = 12


def smart_find_match(needle: str, haystack: str) -> bool:
    """Case-insensitive subsequence match (``swi`` matches ``switches``)."""
    n = needle.strip()
    if not n:
        return True
    n_fold = n.casefold()
    h_fold = haystack.casefold()
    pos = 0
    for ch in h_fold:
        if pos < len(n_fold) and ch == n_fold[pos]:
            pos += 1
    return pos == len(n_fold)


def filter_file_diff_for_find(file_diff: FileDiff, needle: str) -> FileDiff | None:
    """Return a copy of ``file_diff`` narrowed to find matches, or None."""
    n = needle.strip()
    if not n:
        return file_diff
    if smart_find_match(n, file_diff.relative_path):
        return file_diff
    if not file_diff.content_diff:
        return None
    matching = [
        change
        for change in file_diff.content_diff
        if smart_find_match(n, change.path)
        or smart_find_match(n, setting_display_name(change.path))
        or smart_find_match(n, format_setting_change_description(change))
    ]
    if not matching:
        return None
    return replace(file_diff, content_diff=matching)


def filter_file_diffs_for_find(file_diffs: list[FileDiff], needle: str) -> list[FileDiff]:
    """Keep changed files (and settings within them) that match the find needle."""
    n = needle.strip()
    if not n:
        return file_diffs
    out: list[FileDiff] = []
    for file_diff in file_diffs:
        if file_diff.status == "unchanged":
            continue
        hit = filter_file_diff_for_find(file_diff, n)
        if hit is not None:
            out.append(hit)
    return out


def changed_file_diffs(file_diffs: list[FileDiff]) -> list[FileDiff]:
    """Return file diffs whose status is not unchanged."""
    return [item for item in file_diffs if item.status != "unchanged"]


def compare_panel_file_slice(
    file_diffs: list[FileDiff],
    *,
    max_files: int = COMPARE_PANEL_MAX_FILES,
) -> tuple[list[FileDiff], int]:
    """Return up to max_files changed diffs and a count of omitted files.

    Writable/readable setting files are ordered before opaque gcxml churn.
    """
    changed = prioritize_file_diffs_for_panel(file_diffs)
    if len(changed) <= max_files:
        return changed, 0
    return changed[:max_files], len(changed) - max_files


def compare_panel_layout_metrics(
    file_diffs: list[FileDiff],
    *,
    max_files: int = COMPARE_PANEL_MAX_FILES,
    max_changes_per_file: int = COMPARE_PANEL_MAX_CHANGES_PER_FILE,
) -> dict[str, int]:
    """Count compare-panel UI rows after the same truncation caps as the GUI."""
    changed, omitted_files = compare_panel_file_slice(file_diffs, max_files=max_files)
    if not changed and omitted_files == 0:
        return {
            "apply_rows": 0,
            "file_headers": 0,
            "notes": 1,
            "empty": 1,
        }

    apply_rows = 0
    notes = 1 if omitted_files else 0
    for file_diff in changed:
        if not file_diff.content_diff:
            notes += 1
            continue
        actionable, opaque = content_change_panel_partition(file_diff.content_diff)
        if actionable:
            apply_rows += min(len(actionable), max_changes_per_file)
            if len(actionable) > max_changes_per_file:
                notes += 1
            if opaque:
                notes += 1
        else:
            # Opaque-only file: one summary note, no apply rows
            notes += 1

    return {
        "apply_rows": apply_rows,
        "file_headers": len(changed),
        "notes": notes,
        "empty": 0,
    }


def estimate_compare_panel_height(metrics: dict[str, int]) -> int:
    """Rough pixel height for the compare-panel scroll area content."""
    margins = 16
    if metrics.get("empty"):
        return 88
    height = margins
    height += metrics["file_headers"] * 28
    height += metrics["apply_rows"] * 38
    height += metrics["notes"] * 26
    if metrics["file_headers"] > 1:
        height += (metrics["file_headers"] - 1) * 6
    return height


def format_compare_changed_list(file_diffs: list[FileDiff], *, max_details: int = 8) -> str:
    """Return plain-text changed-file details for the compare panel."""
    changed = prioritize_file_diffs_for_panel(file_diffs)
    if not changed:
        return "(no changes)"

    lines: list[str] = []
    for item in changed:
        status = _STATUS_LABELS.get(item.status, item.status.title())
        lines.append(f"{status}: {_file_label(item.relative_path)}")
        if item.content_diff:
            actionable, opaque = content_change_panel_partition(item.content_diff)
            for line in actionable[:max_details]:
                lines.append(_format_content_change(line))
            if len(actionable) > max_details:
                remaining = len(actionable) - max_details
                lines.append(
                    f"  \u2026 {remaining} more writable change(s); open report for full diff"
                )
            if opaque:
                if actionable:
                    lines.append(
                        f"  \u2026 {len(opaque)} encrypted/opaque token change(s) "
                        f"(not listed; open report)"
                    )
                else:
                    lines.append(
                        f"  Encrypted config churn: {len(opaque)} token change(s) "
                        f"(not writable as settings; open report)"
                    )
        elif item.status == "added":
            lines.append("  New file in target snapshot")
        elif item.status == "removed":
            lines.append("  File removed from target snapshot")
    return "\n".join(lines)


def extract_changed_file_path(line: str) -> str | None:
    """Parse a changed-list header line and return the relative file path."""
    stripped = line.strip()
    if not stripped or stripped.startswith("(") or stripped.startswith("\u2026"):
        return None
    if stripped.startswith("  "):
        return None

    match = _FILE_LINE_RE.match(stripped)
    if match:
        return match.group(1).strip()

    parts = stripped.split("\t", 1)
    if len(parts) == 2:
        return parts[1].strip() or None
    return None
