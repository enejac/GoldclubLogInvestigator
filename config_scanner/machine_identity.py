"""Machine-identity guards for Config Scanner write-back.

Cabinet serial, EGM id, host names, and signed licence serials must never be
overwritten when pushing snapshot config to a live EGM. Bulk writes skip
identity-only files; other XML merges preserve live identity leaf values.

Licences: a live licence is never overwritten. 10.1 ↔ 10.2 config restore
must not refresh or replace XML (that is what looked like corruption / ERROR
30 leftover). A missing licence is restored only when the snapshot serial
matches. A different WIBU (LicenseeId) is never written. licence.dll is
restored only when it is missing on that same serial (software pack may
still pair the runtime).
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_LICENCE_DIR_RE = re.compile(r"(^|/)licen[cs]es(/|$)", re.IGNORECASE)
_LICENCE_DLL_RE = re.compile(r"(^|/)licen[cs]e\.dll$", re.IGNORECASE)
_LICENSING_RE = re.compile(r"(^|/)licensing(/|$)", re.IGNORECASE)
_GCBACKUP_LICENCE_RE = re.compile(
    r"(^|/)gcbackup/.*/licen[cs]e(s)?(/|$)",
    re.IGNORECASE,
)
# USB leftover that hid / confused ERROR 30 on this dongle (WIBU 12-12327444).
_FOREIGN_LICENCE_STEMS = frozenset({"6051106A90E561F9F08CDEEBB5C3F6E2"})
_FOREIGN_LICENSEE_IDS = frozenset({"12327444"})
# Live WIBU on GRT330106 — never deleted by leftover-clear, even if listed last.
_KNOWN_LIVE_LICENCE_STEMS = frozenset({"37A55022DCBEF351AE27471D181B1EF5"})
_KNOWN_LIVE_LICENSEE_IDS = frozenset({"12262688"})
_SERIAL_DIGITS_RE = re.compile(r"(\d{4,})")
_LICENCE_XML_SERIAL_RE = re.compile(
    rb"<SerialNumber>\s*([^<]+)\s*</SerialNumber>",
    re.IGNORECASE,
)
_LICENCE_XML_LICENSEE_RE = re.compile(
    rb"<LicenseeId>\s*([^<]+)\s*</LicenseeId>",
    re.IGNORECASE,
)

# Whole files never written from snapshots (compare-only), except missing
# licences on the same EGM serial — see decide_licence_write().
_PROTECTED_FILE_RES: tuple[re.Pattern[str], ...] = (
    _LICENCE_DIR_RE,
    _LICENCE_DLL_RE,
    _LICENSING_RE,
    _GCBACKUP_LICENCE_RE,
    re.compile(
        r"(^|/)var/state/maintenance/ProductSerialNumber\.(json|conf)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|/)var/state/maintenance/ConfigureAurum\.json$",
        re.IGNORECASE,
    ),
    re.compile(r"(^|/)var/state/maintenance/HostName\.conf$", re.IGNORECASE),
    re.compile(r"(^|/)services/aurum(/|$)", re.IGNORECASE),
    re.compile(r"(^|/)services/aurum/config/AurumSetup\.xml$", re.IGNORECASE),
    re.compile(
        r"(^|/)services/aurum/config/SASControler1/ClientsSet\.xml$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(^|/)services/aurum/config/gm2au/RuletaToMonitorSetup\.xml$",
        re.IGNORECASE,
    ),
)

# XML element names and gcxml ``node[@name='…']`` leaves that identify the EGM.
_IDENTITY_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "SerialNumber",
        "CabinetSerialNumber",
        "AurumEgmId",
        "EgmId",
        "NetworkHostName",
        "ServiceURI",
        "MessengerURI",
        "GMID",
        "ProductSerialNumber",
        "MachineName",
        "ConfigurationId",
        "HostName",
    }
)

_ATTR_SEGMENT_RE = re.compile(r"^([^[]+)\[@name='((?:\\'|[^'])*)'\]$")
_ITEM_IDENTITY_SEGMENT_RE = re.compile(
    r"^item\[@(\w+)='((?:\\'|[^'])*)'\]$", re.IGNORECASE
)


def normalize_rel_path(relative_path: str) -> str:
    return (relative_path or "").replace("\\", "/").strip("/")


def is_licence_path(relative_path: str) -> bool:
    """True for licence XML trees, licensing settings, and licence.dll."""
    norm = normalize_rel_path(relative_path)
    if not norm:
        return False
    return bool(
        _LICENCE_DIR_RE.search(norm)
        or _LICENCE_DLL_RE.search(norm)
        or _LICENSING_RE.search(norm)
        or _GCBACKUP_LICENCE_RE.search(norm)
    )


def is_licence_dll_path(relative_path: str) -> bool:
    """True for licence.dll / license.dll (WIBU runtime next to Ruleta)."""
    return bool(_LICENCE_DLL_RE.search(normalize_rel_path(relative_path)))


def canonical_egm_serial(value: str | None) -> str | None:
    """GRT330106 / ALLEGRO330106 / 330106 → the numeric cabinet serial."""
    raw = (value or "").strip().upper()
    if not raw:
        return None
    groups = _SERIAL_DIGITS_RE.findall(raw)
    if groups:
        return groups[-1].lstrip("0") or "0"
    return raw


def egm_serials_match(left: str | None, right: str | None) -> bool:
    """True only when both values resolve to the same numeric EGM serial."""
    a = canonical_egm_serial(left)
    b = canonical_egm_serial(right)
    return bool(a and b and a == b)


def serial_from_licence_bytes(raw: bytes) -> str | None:
    match = _LICENCE_XML_SERIAL_RE.search(raw or b"")
    if not match:
        return None
    text = match.group(1).decode("utf-8", "replace")
    return canonical_egm_serial(text)


def canonical_licensee_id(value: str | None) -> str | None:
    """12-12262688 / 12262688 → the WIBU numeric id."""
    raw = (value or "").strip().upper()
    if not raw:
        return None
    groups = _SERIAL_DIGITS_RE.findall(raw)
    if groups:
        return groups[-1].lstrip("0") or "0"
    return raw


def licensee_ids_match(left: str | None, right: str | None) -> bool:
    a = canonical_licensee_id(left)
    b = canonical_licensee_id(right)
    return bool(a and b and a == b)


def licensee_id_from_licence_bytes(raw: bytes) -> str | None:
    match = _LICENCE_XML_LICENSEE_RE.search(raw or b"")
    if not match:
        return None
    text = match.group(1).decode("utf-8", "replace")
    return canonical_licensee_id(text)


def live_licence_files(dest_root: Path) -> list[Path]:
    """Licence files present on the live EGM (any spelling / slot|config tree)."""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            if not path.is_file():
                return
        except OSError:
            return
        key = str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    parents = (
        dest_root,
        dest_root / "config",
        dest_root / "slot",
        dest_root / "config" / "etc",
        dest_root / "bios",
    )
    for base in parents:
        for name in ("licences", "licenses"):
            folder = base / name
            try:
                if not folder.is_dir():
                    continue
                for child in folder.iterdir():
                    _add(child)
            except OSError:
                continue
    for rel in (
        "slot/licence.dll",
        "slot/license.dll",
        "ruleta/licence.dll",
        "ruleta/license.dll",
        "licence.dll",
        "license.dll",
    ):
        _add(dest_root / rel)
    return found


def live_has_licence(dest_root: Path) -> bool:
    return bool(live_licence_files(dest_root))


def live_licence_licensee_id(dest_root: Path) -> str | None:
    """WIBU LicenseeId from the first live (non-USB-leftover) licence XML."""
    for path in live_licence_files(dest_root):
        suffix = path.suffix.casefold()
        if suffix == ".dll":
            continue
        if is_foreign_licence_xml(path):
            continue
        try:
            found = licensee_id_from_licence_bytes(path.read_bytes())
        except OSError:
            continue
        if found:
            return found
    return None


def decide_licence_write(
    *,
    dest_exists: bool,
    live_has_any: bool,
    live_serial: str | None,
    snapshot_serial: str | None,
    incoming_licence_serial: str | None = None,
    incoming_licensee_id: str | None = None,
    live_licensee_id: str | None = None,
    is_dll: bool = False,
) -> tuple[bool, str]:
    """Whether a snapshot licence may be written onto this EGM.

    A live licence XML or licence.dll is never overwritten (so 10.1 ↔ 10.2
    config restore cannot corrupt the dongle file). A different WIBU
    LicenseeId is never written. A second XML filename is never added
    beside a live licence. Missing XML / dll may be restored only when the
    snapshot serial matches. Unknown serials fail closed.
    """
    if not egm_serials_match(live_serial, snapshot_serial):
        return (
            False,
            "Blocked: cannot restore a licence unless the snapshot serial "
            f"matches this EGM (live {live_serial!r} vs snapshot "
            f"{snapshot_serial!r}).",
        )
    if incoming_licence_serial and not egm_serials_match(
        live_serial, incoming_licence_serial
    ):
        return (
            False,
            "Blocked: licence XML serial does not match this EGM "
            f"({live_serial!r} vs licence {incoming_licence_serial!r}).",
        )
    if incoming_licensee_id and live_licensee_id and not licensee_ids_match(
        incoming_licensee_id, live_licensee_id
    ):
        return (
            False,
            "Blocked: licence LicenseeId does not match the WIBU on this EGM "
            f"({live_licensee_id!r} vs incoming {incoming_licensee_id!r}).",
        )

    if dest_exists:
        return (
            False,
            "Blocked: a licence already exists on this EGM — Config Scanner "
            "never overwrites it (10.1/10.2 config restore leaves the live "
            "licence unchanged).",
        )
    if is_dll:
        return True, "Restore missing licence.dll on this EGM serial."
    if live_has_any:
        return (
            False,
            "Blocked: a licence already exists on this EGM — Config Scanner "
            "does not add another licence file.",
        )
    return True, "Restore missing licence onto the same EGM serial."


def is_protected_live_licence_xml(path: Path) -> bool:
    """True for the known-good dongle file — leftover-clear must never delete it."""
    stem = path.stem.strip().upper()
    if stem in _KNOWN_LIVE_LICENCE_STEMS:
        return True
    try:
        incoming = licensee_id_from_licence_bytes(path.read_bytes())
    except OSError:
        return False
    return bool(incoming and incoming in _KNOWN_LIVE_LICENSEE_IDS)


def is_foreign_licence_xml(
    path: Path, live_licensee_id: str | None = None
) -> bool:
    """True only for the USB leftover (6051106 / 12-12327444).

    ``live_licensee_id`` is ignored: leftover-clear must not delete an
    unknown third file just because it is not the first XML we saw.
    """
    del live_licensee_id
    if is_protected_live_licence_xml(path):
        return False
    stem = path.stem.strip().upper()
    if stem in _FOREIGN_LICENCE_STEMS:
        return True
    try:
        incoming = licensee_id_from_licence_bytes(path.read_bytes())
    except OSError:
        return False
    return bool(incoming and incoming in _FOREIGN_LICENSEE_IDS)


def extra_error30_licence_roots(dest_root: Path) -> list[Path]:
    """USB GoldClub/Licenses folders that can still hold the leftover XML.

    Only a cabinet game root is considered (UNC share or ``C:\\goldclub``).
    A workstation temp folder must not scan ``D:\\GoldClub\\Licenses``.
    """
    roots: list[Path] = []
    text = str(dest_root).replace("/", "\\")
    if text.startswith("\\\\"):
        parts = [p for p in text.strip("\\").split("\\") if p]
        if parts:
            host = parts[0]
            for share in ("USB", "USB_Remote"):
                for name in ("Licenses", "Licences"):
                    roots.append(Path(rf"\\{host}\{share}\GoldClub\{name}"))
        return roots
    norm = text.rstrip("\\").casefold()
    if norm in {r"c:\goldclub", r"c:\goldclub\slot"} or norm.endswith(r"\goldclub"):
        for drive in ("D:", "E:"):
            for name in ("Licenses", "Licences"):
                roots.append(Path(drive) / "GoldClub" / name)
    return roots


def error30_licence_leftovers(
    dest_root: Path,
    extra_roots: list[Path] | None = None,
) -> list[Path]:
    """Licence XMLs that must be removed so ERROR 30 can show the live dongle."""
    leftovers: list[Path] = []
    seen: set[str] = set()

    def _consider(path: Path) -> None:
        try:
            if not path.is_file():
                return
        except OSError:
            return
        if path.suffix.casefold() == ".dll":
            return
        key = str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        if is_foreign_licence_xml(path):
            leftovers.append(path)

    for path in live_licence_files(dest_root):
        _consider(path)
    folders = list(extra_roots or [])
    folders.extend(extra_error30_licence_roots(dest_root))
    for folder in folders:
        try:
            if not folder.is_dir():
                continue
            for child in folder.iterdir():
                if child.suffix.casefold() == ".xml":
                    _consider(child)
        except OSError:
            continue
    return leftovers


def clear_error30_licence_leftovers(
    dest_root: Path,
    extra_roots: list[Path] | None = None,
) -> list[str]:
    """Delete foreign/USB licence XML only. Never the live WIBU file or dll."""
    removed: list[str] = []
    for path in error30_licence_leftovers(dest_root, extra_roots=extra_roots):
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def is_protected_machine_identity_path(relative_path: str) -> bool:
    """True when the whole file must never be written from a snapshot."""
    norm = normalize_rel_path(relative_path)
    if not norm:
        return False
    return any(rx.search(norm) for rx in _PROTECTED_FILE_RES)


def protected_machine_identity_reason(relative_path: str) -> str:
    norm = normalize_rel_path(relative_path)
    low = norm.casefold()
    if is_licence_path(norm):
        return (
            "Live licence XML is never overwritten. A different dongle is "
            "never written. Missing licence.dll may be restored on this EGM "
            "serial; software push may still pair the runtime."
        )
    if "productserialnumber" in low:
        return (
            "Blocked: ProductSerialNumber state is per-EGM — never overwrite "
            "from Config Scanner."
        )
    if "configureaurum" in low or "hostname.conf" in low:
        return "Blocked: Aurum/host identity state — never overwrite from Config Scanner."
    if low.startswith("services/aurum") or "/services/aurum/" in f"/{low}/":
        return (
            "Blocked: Aurum service config (EgmId, CabinetSerialNumber, URIs) "
            "is machine-specific."
        )
    return (
        "Blocked: file contains machine identity — never overwrite from "
        "Config Scanner."
    )


def _unescape_attr_value(raw: str) -> str:
    return raw.replace("\\'", "'").replace("\\\\", "\\")


def _is_identity_field_name(name: str) -> bool:
    return name in _IDENTITY_FIELD_NAMES


def _flat_segment_identity_name(segment: str) -> str | None:
    seg = segment.strip()
    if not seg:
        return None
    if _is_identity_field_name(seg):
        return seg
    m = _ATTR_SEGMENT_RE.match(seg)
    if m and m.group(1) == "node":
        node_name = _unescape_attr_value(m.group(2))
        if _is_identity_field_name(node_name):
            return node_name
    return None


def is_protected_identity_field(flat_path: str, relative_path: str = "") -> bool:
    """True when a per-field Write would change machine identity."""
    if relative_path and is_protected_machine_identity_path(relative_path):
        return True
    norm = (flat_path or "").replace("\\", "/").strip("/")
    if not norm:
        return False
    for segment in norm.split("/"):
        if _flat_segment_identity_name(segment) is not None:
            return True
    leaf = norm.rsplit("/", 1)[-1]
    return _flat_segment_identity_name(leaf) is not None


def protected_identity_field_reason(flat_path: str) -> str:
    norm = (flat_path or "").replace("\\", "/")
    for segment in norm.split("/"):
        name = _flat_segment_identity_name(segment)
        if name is not None:
            return (
                f"Blocked: {name!r} is machine identity on this EGM — "
                "Config Scanner never writes it."
            )
    return "Blocked: machine identity field — Config Scanner never writes it."


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element | None]:
    parent_map: dict[ET.Element, ET.Element | None] = {root: None}
    for parent in root.iter():
        for child in list(parent):
            parent_map[child] = parent
    return parent_map


def _element_path_key(
    element: ET.Element,
    parent_map: dict[ET.Element, ET.Element | None],
) -> tuple[str, ...]:
    parts: list[str] = []
    current: ET.Element | None = element
    while current is not None:
        local = _local_tag(current.tag)
        if local == "node" and current.attrib.get("name"):
            parts.append(f"node[@name='{current.attrib['name']}']")
        else:
            parts.append(local)
        current = parent_map.get(current)
    parts.reverse()
    return tuple(parts)


def _identity_leaf_map(root: ET.Element) -> dict[tuple[str, ...], str]:
    parent_map = _build_parent_map(root)
    out: dict[tuple[str, ...], str] = {}
    for element in root.iter():
        if list(element):
            continue
        local = _local_tag(element.tag)
        key: tuple[str, ...] | None = None
        if _is_identity_field_name(local):
            key = _element_path_key(element, parent_map)
        elif local == "node":
            node_name = (element.attrib.get("name") or "").strip()
            if _is_identity_field_name(node_name):
                key = _element_path_key(element, parent_map)
        if key is not None:
            out[key] = element.text or ""
    return out


def _find_element_for_path_key(
    root: ET.Element,
    path_key: tuple[str, ...],
) -> ET.Element | None:
    if not path_key:
        return None
    start_index = 0
    if _local_tag(root.tag) == path_key[0]:
        if len(path_key) == 1:
            return root if not list(root) else None
        start_index = 1
    candidates = [root]
    for segment in path_key[start_index:]:
        next_candidates: list[ET.Element] = []
        for parent in candidates:
            for child in list(parent):
                local = _local_tag(child.tag)
                if segment.startswith("node[@name='") and segment.endswith("']"):
                    node_name = segment[len("node[@name='") : -2]
                    if local == "node" and child.attrib.get("name") == node_name:
                        next_candidates.append(child)
                elif local == segment:
                    next_candidates.append(child)
        if not next_candidates:
            return None
        candidates = next_candidates
    return candidates[0] if len(candidates) == 1 else None


def merge_xml_bytes_preserving_identity(
    live_bytes: bytes,
    incoming_bytes: bytes,
) -> bytes:
    """Return incoming XML with live identity leaf values overlaid."""
    live_root = ET.fromstring(live_bytes)
    incoming_root = ET.fromstring(incoming_bytes)
    merged = copy.deepcopy(incoming_root)
    live_values = _identity_leaf_map(live_root)
    changed = False
    for path_key, live_text in live_values.items():
        target_el = _find_element_for_path_key(merged, path_key)
        if target_el is None or list(target_el):
            continue
        if (target_el.text or "") == live_text:
            continue
        target_el.text = live_text
        changed = True
    if not changed:
        # Reserializing rewrites comments, whitespace and attribute quoting, so a
        # write-back that changes no identity value must stay byte-for-byte.
        return incoming_bytes
    return ET.tostring(merged, encoding="utf-8", xml_declaration=True)


def prepare_restore_bytes_preserving_identity(
    dest: Path,
    incoming_raw: bytes,
    relative_path: str,
) -> bytes:
    """Merge identity fields from live file into snapshot bytes before write."""
    rel = normalize_rel_path(relative_path)
    if is_protected_machine_identity_path(rel):
        raise ValueError(protected_machine_identity_reason(rel))
    if not rel.lower().endswith(".xml") or not dest.is_file():
        return incoming_raw

    from config_scanner.xml_diff import is_encrypted_origin_config_path

    live_raw = dest.read_bytes()
    if is_encrypted_origin_config_path(rel):
        try:
            from ai_helper.gcxml_decrypt import (
                decrypt_gcxml_setup_to_plain_xml,
                plain_bytes_for_config_scan,
            )
        except ImportError:
            return incoming_raw
        live_plain = plain_bytes_for_config_scan(dest)
        if live_plain is None:
            live_plain = decrypt_gcxml_setup_to_plain_xml(dest).encode("utf-8")
        incoming_plain = incoming_raw
        if not incoming_plain.lstrip().startswith(b"<"):
            return incoming_raw
        merged_plain = merge_xml_bytes_preserving_identity(live_plain, incoming_plain)
        return merged_plain

    if not live_raw.lstrip().startswith(b"<") or not incoming_raw.lstrip().startswith(
        b"<"
    ):
        return incoming_raw
    return merge_xml_bytes_preserving_identity(live_raw, incoming_raw)
