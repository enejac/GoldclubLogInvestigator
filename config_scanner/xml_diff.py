"""XML and text diff helpers for config comparison."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_ATTR_SEGMENT_RE = re.compile(r"^([^[]+)\[@name='((?:\\'|[^'])*)'\]$")
# Indexed list items (GoldClub HW driverssetup item0/item1/…).
_ITEM_INDEX_RE = re.compile(r"^item(\d+)$", re.IGNORECASE)
# Identity-keyed item segment produced by flat_xml_map.
_ITEM_IDENTITY_SEGMENT_RE = re.compile(
    r"^item\[@(\w+)='((?:\\'|[^'])*)'\]$", re.IGNORECASE
)
_ITEM_IDENTITY_LEAF_NAMES: tuple[str, ...] = (
    "aliasName",
    "alias",
    "id",
    "name",
)
_ITEM_PATH_PREFIX_RE = re.compile(
    r"^(.*?/item\[@\w+='(?:\\'|[^'])*'\])(?:/.*)?$"
)


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


def _unescape_attr_value(raw: str) -> str:
    return raw.replace("\\'", "'").replace("\\\\", "\\")


def _escape_attr_value(raw: str) -> str:
    return raw.replace("\\", "\\\\").replace("'", "\\'")


def _item_identity(item_el: ET.Element) -> tuple[str, str] | None:
    """Pick a stable identity leaf under an indexed ``itemN`` element."""
    for leaf_name in _ITEM_IDENTITY_LEAF_NAMES:
        for child in list(item_el):
            if list(child):
                continue
            if _element_local_name(child) != leaf_name:
                continue
            value = _element_text_value(child).strip()
            if value:
                return leaf_name, value
    return None


def _path_segment_for_element(element: ET.Element) -> str:
    """Path segment for one element; index items prefer ``item[@aliasName='…']``."""
    local_name = _element_local_name(element)
    name_attr = element.attrib.get("name")
    if name_attr:
        return f"{local_name}[@name='{_escape_attr_value(name_attr)}']"
    if _ITEM_INDEX_RE.match(local_name):
        ident = _item_identity(element)
        if ident is not None:
            attr, value = ident
            return f"item[@{attr}='{_escape_attr_value(value)}']"
    return local_name


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
        segments.insert(0, _path_segment_for_element(current))
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


def _homogeneous_leaf_tag(element: ET.Element) -> str | None:
    """Tag name when every child is a same-name leaf with no ``name`` attr."""
    children = list(element)
    if not children:
        return None
    names = {_element_local_name(child) for child in children}
    if len(names) != 1:
        return None
    for child in children:
        if list(child):
            return None
        if child.attrib.get("name"):
            return None
    return next(iter(names))


def _is_joined_list_parent(element: ET.Element) -> bool:
    """True when this node is stored as one comma-separated catalog/list value."""
    if _homogeneous_leaf_tag(element) is None:
        return False
    from config_scanner.denom_compare import is_denom_list_parent_name

    if is_denom_list_parent_name(_element_local_name(element)):
        return True
    return len(list(element)) >= 2


def _join_leaf_list(element: ET.Element) -> str:
    return ",".join(_element_text_value(child) for child in list(element))


def _replace_leaf_list(element: ET.Element, value: str, child_tag: str) -> None:
    for child in list(element):
        element.remove(child)
    parts = [part.strip() for part in value.split(",") if part.strip()]
    for part in parts:
        child = ET.SubElement(element, child_tag)
        child.text = part


def flat_xml_map(document: ET.ElementTree) -> dict[str, str]:
    root = document.getroot()
    parent_map = _build_parent_map(root)
    joined_parents: set[ET.Element] = set()
    result: dict[str, str] = {}
    for element in root.iter():
        if not _is_joined_list_parent(element):
            continue
        path = _element_path_with_map(element, parent_map)
        result[path] = _join_leaf_list(element)
        joined_parents.add(element)
    for element in root.iter():
        if list(element):
            continue
        if parent_map.get(element) in joined_parents:
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
    from config_scanner.denom_compare import classify_denom_changes

    return classify_denom_changes(collapse_identity_item_changes(changes))


def _item_identity_prefix(path: str) -> str | None:
    match = _ITEM_PATH_PREFIX_RE.match(path.replace("\\", "/"))
    if not match:
        return None
    return match.group(1)


def _summarize_item_leaf_map(leaf_values: dict[str, str]) -> str:
    """Human summary for a whole HW driver / indexed item entry."""
    alias = leaf_values.get("aliasName") or leaf_values.get("alias") or leaf_values.get("name")
    endpoint = leaf_values.get("endpointaddress") or leaf_values.get("endpointAddress")
    raw = leaf_values.get("driverRawName") or ""
    driver_short = ""
    if raw:
        # …].[GoldClub.HW.Subsys.Driver.FutureLogic.PSA66ST2] → FutureLogic.PSA66ST2
        parts = [p for p in raw.replace("[", "").replace("]", "").split(".") if p]
        if parts:
            # Keep last 2–3 meaningful tokens
            driver_short = ".".join(parts[-3:]) if len(parts) >= 3 else parts[-1]
    bits: list[str] = []
    if alias:
        bits.append(alias)
    if driver_short:
        bits.append(driver_short)
    if endpoint:
        bits.append(endpoint)
    if not bits:
        return "(entry)"
    if alias and len(bits) > 1:
        return f"{alias}: " + ", ".join(bits[1:])
    return ", ".join(bits)


def collapse_identity_item_changes(changes: list[ContentChange]) -> list[ContentChange]:
    """Collapse whole indexed-item add/remove into one structural row.

    Avoids flooding the UI with every leaf under a removed ``tito`` driver, and
    prevents ``item1`` path churn from looking like field renames.
    """
    by_prefix: dict[str, list[ContentChange]] = {}
    passthrough: list[ContentChange] = []
    for change in changes:
        prefix = _item_identity_prefix(change.path)
        if prefix is None or prefix == change.path:
            passthrough.append(change)
            continue
        by_prefix.setdefault(prefix, []).append(change)

    collapsed: list[ContentChange] = []
    for prefix, group in by_prefix.items():
        types = {c.change_type for c in group}
        # Only collapse pure add or pure remove groups (whole entry appeared/disappeared).
        if types == {"removed"} or types == {"added"}:
            change_type = next(iter(types))
            leaf_map: dict[str, str] = {}
            for c in group:
                leaf = c.path.rsplit("/", 1)[-1]
                val = c.old_value if change_type == "removed" else c.new_value
                if val is not None:
                    leaf_map[leaf] = val
            summary = _summarize_item_leaf_map(leaf_map)
            if change_type == "removed":
                collapsed.append(ContentChange("removed", prefix, summary, None))
            else:
                collapsed.append(ContentChange("added", prefix, None, summary))
        else:
            collapsed.extend(group)

    # Stable-ish order: structural item rows first (by path), then other changes.
    structural = [c for c in collapsed if _item_identity_prefix(c.path) == c.path]
    leaves = [c for c in collapsed if _item_identity_prefix(c.path) != c.path]
    structural.sort(key=lambda c: c.path)
    leaves.sort(key=lambda c: c.path)
    passthrough.sort(key=lambda c: c.path)
    return structural + leaves + passthrough


def is_structural_item_change(change: ContentChange) -> bool:
    """True for whole ``item[@aliasName=…]`` add/remove (not a single leaf field)."""
    path = change.path.replace("\\", "/")
    if _ITEM_IDENTITY_SEGMENT_RE.search(path.rsplit("/", 1)[-1]):
        # Path ends with identity item segment → whole entry.
        return change.change_type in {"added", "removed"}
    return False


def is_catalog_content_change(change: ContentChange) -> bool:
    """True for factory denom-menu drift that is not an active-rate fault."""
    from config_scanner.denom_compare import is_catalog_content_change as _is_catalog

    return _is_catalog(change)


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


def is_encrypted_origin_config_path(relative_path: str) -> bool:
    """True when the live file is BiOS settings ``application/ruleta/setup.xml``.

    Scans/archives store decrypted plain settings for compare, but the on-cabinet
    file remains encrypted — UI uses this to color those settings distinctly.
    Excludes ``xml-configs`` schema mirrors.
    """
    low = (relative_path or "").replace("\\", "/").lower().strip("/")
    if not low.endswith("setup.xml"):
        return False
    if "/xml-configs/" in f"/{low}":
        return False
    return "/application/ruleta/setup.xml" in f"/{low}"


def _is_ruleta_setup_relative(relative_path: str) -> bool:
    return is_encrypted_origin_config_path(relative_path)


def file_content_diff(
    relative_path: str,
    baseline_path: Path | None,
    target_path: Path | None,
) -> list[ContentChange]:
    if baseline_path is None and target_path is None:
        return []

    # Encrypted ruleta setup.xml → compare decrypted plain settings, not token churn.
    if _is_ruleta_setup_relative(relative_path):
        try:
            from ai_helper.gcxml_decrypt import plain_setup_path_for_diff
        except ImportError:
            plain_setup_path_for_diff = None  # type: ignore[assignment]
        if plain_setup_path_for_diff is not None:
            with plain_setup_path_for_diff(baseline_path) as base_plain:
                with plain_setup_path_for_diff(target_path) as tgt_plain:
                    return _file_content_diff_resolved(
                        relative_path, base_plain, tgt_plain
                    )

    return _file_content_diff_resolved(relative_path, baseline_path, target_path)


def _file_content_diff_resolved(
    relative_path: str,
    baseline_path: Path | None,
    target_path: Path | None,
) -> list[ContentChange]:
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

        # Old snapshots hashed ciphertext; new ones hash plain. Prefer plain
        # content equality for ruleta setup.xml so compare is meaningful.
        # Never force "unchanged" when plains/paths are missing (empty diff ≠ equal).
        content_diff: list[ContentChange] = []
        if (
            status == "modified"
            and _is_ruleta_setup_relative(relative_path)
            and baseline_full
            and target_full
        ):
            try:
                from ai_helper.gcxml_decrypt import plain_text_for_compare
            except ImportError:
                plain_text_for_compare = None  # type: ignore[assignment]
            if plain_text_for_compare is not None:
                base_plain = plain_text_for_compare(baseline_full)
                tgt_plain = plain_text_for_compare(target_full)
                if base_plain is not None and tgt_plain is not None:
                    if base_plain == tgt_plain:
                        status = "unchanged"
                    else:
                        content_diff = file_content_diff(
                            relative_path, baseline_full, target_full
                        )
                else:
                    content_diff = file_content_diff(
                        relative_path, baseline_full, target_full
                    )
            else:
                content_diff = file_content_diff(
                    relative_path, baseline_full, target_full
                )
        elif status != "unchanged":
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

    # Existing snapshots may still contain both bios/etc and config/etc mirrors.
    from config_scanner.path_mirror import dedupe_etc_mirror_items

    return dedupe_etc_mirror_items(
        results,
        path_of=lambda item: item.relative_path,
        fingerprint_of=lambda item: (
            item.status,
            item.baseline_sha1,
            item.target_sha1,
            tuple(
                (change.path, change.old_value, change.new_value, change.change_type)
                for change in item.content_diff
            ),
        ),
    )


def change_summary(file_diffs: list[FileDiff]) -> dict[str, int]:
    return {
        "added": sum(1 for item in file_diffs if item.status == "added"),
        "removed": sum(1 for item in file_diffs if item.status == "removed"),
        "modified": sum(1 for item in file_diffs if item.status == "modified"),
        "unchanged": sum(1 for item in file_diffs if item.status == "unchanged"),
    }


_GENERIC_ATTR_SEGMENT_RE = re.compile(
    r"^([^[]+)\[@(\w+)='((?:\\'|[^'])*)'\]$"
)


def _child_for_segment(parent: ET.Element, segment: str) -> ET.Element | None:
    """Resolve one path segment under ``parent`` (name / identity / indexed tag)."""
    # Identity-keyed HW driverssetup items: item[@aliasName='tito'] → itemN by leaf.
    id_item = _ITEM_IDENTITY_SEGMENT_RE.match(segment)
    if id_item:
        attr_name, attr_value = id_item.group(1), _unescape_attr_value(id_item.group(2))
        for child in parent:
            child_local = _element_local_name(child)
            if not (_ITEM_INDEX_RE.match(child_local) or child_local.casefold() == "item"):
                continue
            ident = _item_identity(child)
            if ident is not None and ident[0] == attr_name and ident[1] == attr_value:
                return child
        return None

    gen = _GENERIC_ATTR_SEGMENT_RE.match(segment)
    if gen:
        local_name, attr_name, attr_value = (
            gen.group(1),
            gen.group(2),
            _unescape_attr_value(gen.group(3)),
        )
        for child in parent:
            if (
                _element_local_name(child) == local_name
                and child.attrib.get(attr_name) == attr_value
            ):
                return child
        return None

    attr_match = _ATTR_SEGMENT_RE.match(segment)
    if attr_match:
        local_name, name_attr = attr_match.group(1), _unescape_attr_value(attr_match.group(2))
        for child in parent:
            if _element_local_name(child) == local_name and child.attrib.get("name") == name_attr:
                return child
        return None

    # GoldClub uses literal tags item0/item1 — match exact local name first.
    for child in parent:
        if _element_local_name(child) == segment:
            return child
    return None


def _root_matches_first_segment(root: ET.Element, segment: str) -> bool:
    gen = _GENERIC_ATTR_SEGMENT_RE.match(segment)
    if gen:
        local_name, attr_name, attr_value = (
            gen.group(1),
            gen.group(2),
            _unescape_attr_value(gen.group(3)),
        )
        if _element_local_name(root) != local_name:
            return False
        if root.attrib.get(attr_name) == attr_value:
            return True
        if attr_name == "name" and root.attrib.get("name") == attr_value:
            return True
        return False
    attr_match = _ATTR_SEGMENT_RE.match(segment)
    if attr_match:
        local_name, name_attr = attr_match.group(1), _unescape_attr_value(attr_match.group(2))
        return (
            _element_local_name(root) == local_name
            and root.attrib.get("name") == name_attr
        )
    return _element_local_name(root) == segment


def find_element_for_flat_path(root: ET.Element, path: str) -> ET.Element | None:
    """Navigate slash-separated flat_xml_map paths (name / identity / indexed)."""
    if not path:
        return None
    segments = path.split("/")
    current: ET.Element = root
    start_index = 0
    if segments and _root_matches_first_segment(current, segments[0]):
        start_index = 1
    if start_index == len(segments):
        return current
    for segment in segments[start_index:]:
        found = _child_for_segment(current, segment)
        if found is None:
            return None
        current = found
    return current


def apply_xml_value_at_path(file_path: Path, flat_path: str, value: str | None) -> None:
    """Set a leaf element's text at a flat_xml_map path and write the file back.

    Whole ``item[@aliasName=…]`` driver rows are structural — refuse leaf Write.
    """
    item_prefix = _item_identity_prefix(flat_path)
    if item_prefix is not None and item_prefix == flat_path.replace("\\", "/"):
        raise ValueError(
            f"Cannot Write a whole HW driver/item block via leaf apply: {flat_path}. "
            "Restore the full configuration.xml from a known-good backup instead."
        )

    try:
        tree = ET.parse(file_path)
    except ET.ParseError as exc:
        raise ValueError(f"Could not parse XML: {file_path}") from exc

    root = tree.getroot()
    element = find_element_for_flat_path(root, flat_path)
    if element is None:
        raise ValueError(f"XML path not found in {file_path.name}: {flat_path}")
    if list(element):
        child_tag = _homogeneous_leaf_tag(element)
        if child_tag is None:
            raise ValueError(f"Cannot set text on non-leaf element: {flat_path}")
        _replace_leaf_list(element, "" if value is None else value, child_tag)
    else:
        element.text = "" if value is None else value
    tree.write(file_path, encoding="utf-8", xml_declaration=True)


def resolve_apply_value(change: ContentChange, side: str) -> str | None:
    """Return the value for baseline or target side of a content change."""
    if side not in ("baseline", "target"):
        raise ValueError(f"side must be 'baseline' or 'target', got {side!r}")
    if side == "baseline":
        return change.old_value
    return change.new_value
