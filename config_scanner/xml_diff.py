"""XML and text diff helpers for config comparison."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContentChange:
    change_type: str
    path: str
    old_value: str | None
    new_value: str | None


@dataclass(frozen=True)
class FileDiff:
    relative_path: str
    status: str
    baseline_sha1: str | None
    target_sha1: str | None
    baseline_size_bytes: int | None
    target_size_bytes: int | None
    content_diff: list[ContentChange]


def _element_local_name(element: ET.Element) -> str:
    tag = element.tag
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    parent_map: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in list(parent):
            parent_map[child] = parent
    return parent_map


def _element_path_with_map(element: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> str:
    segments: list[str] = []
    current: ET.Element | None = element
    while current is not None:
        local_name = _element_local_name(current)
        name_attr = current.attrib.get("name")
        if name_attr:
            segments.insert(0, f"{local_name}[@name='{name_attr}']")
        else:
            segments.insert(0, local_name)
        current = parent_map.get(current)
    return "/".join(segments)


def _element_text_value(element: ET.Element) -> str:
    if list(element):
        text_parts = [node.text or "" for node in element.iter() if node is element or node.tag is not None]
        if element.text:
            return element.text.strip()
        joined = "".join(part for part in (element.text, element.tail) if part)
        if joined.strip():
            return joined.strip()
        if element.text is None and not list(element):
            return (element.text or "").strip()
        direct = (element.text or "").strip()
        if direct:
            return direct
        return "".join(element.itertext()).strip()
    return (element.text or "").strip()


def flat_xml_map(document: ET.ElementTree) -> dict[str, str]:
    root = document.getroot()
    parent_map = _build_parent_map(root)
    result: dict[str, str] = {}
    for element in root.iter():
        if list(element):
            continue
        path = _element_path_with_map(element, parent_map)
        result[path] = _element_text_value(element)
    return result


def texts_xml_map(document: ET.ElementTree) -> dict[str, str]:
    root = document.getroot()
    result: dict[str, str] = {}
    for node in root.iter():
        id_node = next((child for child in list(node) if _element_local_name(child) == "id"), None)
        text_node = next((child for child in list(node) if _element_local_name(child) == "text"), None)
        if id_node is not None and text_node is not None:
            key = (id_node.text or "").strip()
            if key:
                result[key] = text_node.text or ""
    return result


def compare_keyed_maps(
    baseline_map: dict[str, str],
    target_map: dict[str, str],
) -> list[ContentChange]:
    changes: list[ContentChange] = []
    for key in sorted(set(baseline_map) | set(target_map)):
        baseline_exists = key in baseline_map
        target_exists = key in target_map
        if baseline_exists and target_exists:
            if baseline_map[key] != target_map[key]:
                changes.append(
                    ContentChange("modified", key, baseline_map[key], target_map[key])
                )
        elif not baseline_exists and target_exists:
            changes.append(ContentChange("added", key, None, target_map[key]))
        elif baseline_exists and not target_exists:
            changes.append(ContentChange("removed", key, baseline_map[key], None))
    return changes


def line_diff(baseline_lines: list[str], target_lines: list[str]) -> list[ContentChange]:
    changes: list[ContentChange] = []
    max_len = max(len(baseline_lines), len(target_lines))
    for index in range(max_len):
        baseline_line = baseline_lines[index] if index < len(baseline_lines) else None
        target_line = target_lines[index] if index < len(target_lines) else None
        if baseline_line == target_line:
            continue
        if baseline_line is None:
            change_type = "added"
        elif target_line is None:
            change_type = "removed"
        else:
            change_type = "modified"
        changes.append(
            ContentChange(
                change_type,
                f"line:{index + 1}",
                baseline_line,
                target_line,
            )
        )
    return changes


def file_content_diff(
    relative_path: str,
    baseline_path: Path | None,
    target_path: Path | None,
) -> list[ContentChange]:
    if baseline_path is None and target_path is None:
        return []

    if relative_path.lower().endswith(".xml") and baseline_path and target_path:
        if baseline_path.is_file() and target_path.is_file():
            try:
                baseline_doc = ET.parse(baseline_path)
                target_doc = ET.parse(target_path)
                if Path(relative_path).name == "texts.xml":
                    return compare_keyed_maps(
                        texts_xml_map(baseline_doc),
                        texts_xml_map(target_doc),
                    )
                return compare_keyed_maps(
                    flat_xml_map(baseline_doc),
                    flat_xml_map(target_doc),
                )
            except ET.ParseError:
                pass

    baseline_lines = (
        baseline_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if baseline_path and baseline_path.is_file()
        else []
    )
    target_lines = (
        target_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if target_path and target_path.is_file()
        else []
    )
    return line_diff(baseline_lines, target_lines)


def compare_manifests(
    baseline_manifest,
    target_manifest,
    *,
    baseline_content_root: Path | None = None,
    target_content_root: Path | None = None,
    baseline_game_drive: str = "",
    target_game_drive: str = "",
) -> list[FileDiff]:
    baseline_map = {entry.relative_path: entry for entry in baseline_manifest.files}
    target_map = {entry.relative_path: entry for entry in target_manifest.files}
    results: list[FileDiff] = []

    def _resolve_path(
        content_root: Path | None,
        game_drive: str,
        relative_path: str,
    ) -> Path | None:
        rel = Path(relative_path.replace("\\", "/"))
        if content_root is not None:
            candidate = content_root / rel
            if candidate.is_file():
                return candidate
        if game_drive:
            drive = game_drive.rstrip("\\") + "\\"
            candidate = Path(drive) / rel
            if candidate.is_file():
                return candidate
        return None

    for relative_path in sorted(set(baseline_map) | set(target_map)):
        baseline_entry = baseline_map.get(relative_path)
        target_entry = target_map.get(relative_path)
        if baseline_entry and target_entry:
            status = (
                "unchanged"
                if baseline_entry.sha1 == target_entry.sha1
                else "modified"
            )
        elif baseline_entry is None:
            status = "added"
        else:
            status = "removed"

        baseline_full = (
            _resolve_path(baseline_content_root, baseline_game_drive, relative_path)
            if baseline_entry
            else None
        )
        target_full = (
            _resolve_path(target_content_root, target_game_drive, relative_path)
            if target_entry
            else None
        )
        content_diff: list[ContentChange] = []
        if status != "unchanged":
            content_diff = file_content_diff(relative_path, baseline_full, target_full)

        results.append(
            FileDiff(
                relative_path=relative_path,
                status=status,
                baseline_sha1=baseline_entry.sha1 if baseline_entry else None,
                target_sha1=target_entry.sha1 if target_entry else None,
                baseline_size_bytes=baseline_entry.size_bytes if baseline_entry else None,
                target_size_bytes=target_entry.size_bytes if target_entry else None,
                content_diff=content_diff,
            )
        )
    return results


def change_summary(file_diffs: list[FileDiff]) -> dict[str, int]:
    return {
        "added": sum(1 for item in file_diffs if item.status == "added"),
        "removed": sum(1 for item in file_diffs if item.status == "removed"),
        "modified": sum(1 for item in file_diffs if item.status == "modified"),
        "unchanged": sum(1 for item in file_diffs if item.status == "unchanged"),
    }
