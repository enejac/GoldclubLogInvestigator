"""Keep Ruleta paytable names loadable after a config restore.

Config Scanner archives ``config/`` (including 10.2 paytable JSON) but not
``ruleta\\var``. Restoring a 10.2 snapshot onto Ruleta 10.1 used to crash the
game (``PutRemoteThemeAndCombo`` / ``bad conversion``) because:

* JSON filenames were treated as names the live exe can load
* ``AurumSetup.xml`` still advertised ``paytable_elite_double_zero``
* combo.dat ``EgmPaytableId`` was left on the 10.2 name

This remaps 10.2-only names to 10.1 equivalents. Perf-meter history rows and
licences are never touched. Signed DeviceManagerData (16-byte MAC header,
including MeterHost / gm2au / SAS) is never rewritten — that is DATA TAMPERED.
AurumSetup is patched in place (paytableId only; EgmId / serial / URIs stay).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from config_scanner.build_version import detect_roulette_exe_version, short_product_version

# Names Ruleta 10.1 logs as dynamic paytables on GRT330106, plus single-zero.
RULETA_10_1_PAYTABLE_IDS: frozenset[str] = frozenset(
    {
        "paytable_double_zero",
        "paytable_premium",
        "paytable_elite",
        "paytable_player_select",
        "paytable_single_zero",
    }
)

# 10.2-only names → 10.1 names Ruleta.exe can enum-parse.
RULETA_10_2_TO_10_1_ALIASES: dict[str, str] = {
    "paytable_elite_double_zero": "paytable_double_zero",
    "paytable_premium_double_zero": "paytable_premium",
}

# 10.1 dynamic enum leftovers → 10.2 signed paytable ids (10.2 dropped these).
RULETA_10_1_DYNAMIC_TO_10_2: dict[str, str] = {
    "dynamic_standard": "paytable_double_zero",
    "dynamic_premium": "paytable_premium",
    "dynamic_elite": "paytable_elite",
    "dynamic_playerselect": "paytable_player_select",
    "dynamic_player_select": "paytable_player_select",
}

RULETA_10_2_EXTRA_PAYTABLE_IDS: frozenset[str] = frozenset(RULETA_10_2_TO_10_1_ALIASES)

_DEVICE_MANAGER_RELATIVE = (
    "ruleta/var/SASControler1/DeviceManagerData.xml_1",
    "ruleta/var/SASControler1/DeviceManagerData.xml_2",
    "ruleta/var/gm2au/DeviceManagerData.xml_1",
    "ruleta/var/gm2au/DeviceManagerData.xml_2",
)

_COMBO_GLOBS = (
    "ruleta/var/SASControler1/*combo*.dat",
    "ruleta/var/gm2au/*combo*.dat",
)

_AURUM_SETUP_RELATIVE = (
    "services/aurum/config/AurumSetup.xml",
    "config/etc/application/aurum/AurumSetup.xml",
    "bios/etc/application/aurum/AurumSetup.xml",
)

_PAYTABLE_JSON_DIRS = (
    "config/etc/application/ruleta/paytables",
    "bios/etc/application/ruleta/paytables",
)

_PROCESSOR_STATUS_RE = re.compile(
    r"<processorStatus\b[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)
_PAYTABLE_ATTR_RE = re.compile(
    r'(paytableId=")([^"]*)(")',
    re.IGNORECASE,
)
_THEME_ATTR_RE = re.compile(
    r'themeId="([^"]*)"',
    re.IGNORECASE,
)

def _alias_map_regex(aliases: dict[str, str], prefix: str, suffix: str) -> re.Pattern[str]:
    keys = "|".join(re.escape(name) for name in aliases)
    return re.compile(prefix + "(" + keys + ")" + suffix, re.IGNORECASE)


_ALIAS_PAYTABLE_ATTR_RE = _alias_map_regex(
    RULETA_10_2_TO_10_1_ALIASES, r'(paytableId=")', r'(")'
)
_ALIAS_EGM_PAYTABLE_RE = _alias_map_regex(
    RULETA_10_2_TO_10_1_ALIASES, r"(<EgmPaytableId>)", r"(</EgmPaytableId>)"
)


def ten_two_paytable_ids_in_text(text: str) -> tuple[str, ...]:
    """10.2-only paytable names mentioned in XML/text (order preserved)."""
    found: list[str] = []
    seen: set[str] = set()
    blob = text or ""
    for name in RULETA_10_2_TO_10_1_ALIASES:
        if name.casefold() in blob.casefold() and name not in seen:
            seen.add(name)
            found.append(name)
    return tuple(found)


def signed_device_manager_10_2_paytable_ids(dest_root: Path) -> tuple[str, ...]:
    """Read-only: 10.2 paytable ids inside MAC-signed DeviceManager XML.

    Those files are never rewritten here. Ruleta 10.1 cannot enum-parse them
    (``PutRemoteThemeAndCombo`` / ``bad conversion``).
    """
    found: list[str] = []
    seen: set[str] = set()
    for rel in _DEVICE_MANAGER_RELATIVE:
        path = _under(dest_root, rel)
        try:
            if not path.is_file():
                continue
            data = path.read_bytes()
        except OSError:
            continue
        header, xml_bytes = split_device_manager_bytes(data)
        if not header:
            continue
        try:
            body = xml_bytes.decode("utf-8", errors="replace")
        except Exception:
            continue
        for name in ten_two_paytable_ids_in_text(body):
            if name not in seen:
                seen.add(name)
                found.append(name)
    return tuple(found)


def split_device_manager_bytes(data: bytes) -> tuple[bytes, bytes]:
    """Split the optional binary header from the XML body."""
    marker = b"<?xml"
    idx = data.find(marker)
    if idx < 0:
        idx = data.find(b"<")
    if idx <= 0:
        return b"", data
    return data[:idx], data[idx:]


def _under(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def _alias_for(name: str, aliases: dict[str, str] | None = None) -> str | None:
    current = (name or "").strip()
    if not current:
        return None
    table = aliases if aliases is not None else RULETA_10_2_TO_10_1_ALIASES
    direct = table.get(current)
    if direct:
        return direct
    lowered = current.casefold()
    for src, dest in table.items():
        if src.casefold() == lowered:
            return dest
    return None


def paytable_ids_from_config(dest_root: Path) -> set[str]:
    """Paytable ids from JSON filenames and optional ``name`` fields."""
    ids: set[str] = set()
    for rel in _PAYTABLE_JSON_DIRS:
        folder = _under(dest_root, rel)
        try:
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                stem = path.stem.strip()
                if stem:
                    ids.add(stem)
                try:
                    payload = json.loads(path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                if isinstance(payload, dict):
                    name = str(payload.get("name") or "").strip()
                    if name:
                        ids.add(name)
        except OSError:
            continue
    return ids


def live_exe_is_ruleta_10_2(live_major_minor: str | None) -> bool:
    return (live_major_minor or "").strip().startswith("10.2")


def known_paytable_ids(dest_root: Path, live_major_minor: str | None) -> set[str]:
    """Names the *running* Ruleta.exe can load — not every JSON on disk.

    A 10.2 snapshot drops ``paytable_elite_double_zero.json`` onto a 10.1 tree.
    Those files are not 10.1 enum values, so they must not count as known.
    """
    ids = set(RULETA_10_1_PAYTABLE_IDS)
    extras = {name.casefold() for name in RULETA_10_2_EXTRA_PAYTABLE_IDS}
    for name in paytable_ids_from_config(dest_root):
        if name.casefold() in extras:
            continue
        ids.add(name)
    if live_exe_is_ruleta_10_2(live_major_minor):
        ids.update(RULETA_10_2_EXTRA_PAYTABLE_IDS)
    return ids


def live_ruleta_major_minor(dest_root: Path) -> str | None:
    info = detect_roulette_exe_version(dest_root)
    return short_product_version(
        info.product_version or info.display_version or info.file_version
    )


def choose_compatible_paytable(
    current_id: str,
    theme_id: str,
    known: set[str],
) -> str | None:
    """Return a replacement paytableId, or None when the current id is fine."""
    current = (current_id or "").strip()
    if not current:
        return None
    if current in known:
        return None
    aliased = _alias_for(current)
    if aliased and aliased != current:
        return aliased
    theme = (theme_id or "").casefold()
    if "doublezero" in theme or "double_zero" in theme:
        return "paytable_double_zero"
    return None


def is_10_2_only_paytable_json(relative_path: str) -> bool:
    """True for archived 10.2-only paytable JSON that 10.1 cannot load."""
    norm = (relative_path or "").replace("\\", "/").strip("/")
    if not norm.casefold().endswith(".json"):
        return False
    if "/paytables/" not in f"/{norm.casefold()}/":
        return False
    return _alias_for(Path(norm).stem) is not None


def filter_incompatible_paytable_restore_paths(
    relative_paths: list[str] | tuple[str, ...],
    dest_root: Path,
) -> tuple[list[str], list[str]]:
    """Drop 10.2-only paytable JSON when the live exe is not 10.2."""
    live = live_ruleta_major_minor(dest_root)
    if live_exe_is_ruleta_10_2(live):
        return list(relative_paths), []
    kept: list[str] = []
    notes: list[str] = []
    for rel in relative_paths:
        if is_10_2_only_paytable_json(rel):
            notes.append(
                f"skipped {rel.replace(chr(92), '/')}: 10.2 paytable JSON not loadable by live Ruleta"
            )
            continue
        kept.append(rel)
    return kept, notes


def _patch_processor_status_xml(body: str, known: set[str]) -> tuple[str, list[str]]:
    notes: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        chunk = match.group(0)
        pay_m = _PAYTABLE_ATTR_RE.search(chunk)
        if pay_m is None:
            return chunk
        current = pay_m.group(2)
        theme_m = _THEME_ATTR_RE.search(chunk)
        theme = theme_m.group(1) if theme_m else ""
        replacement = choose_compatible_paytable(current, theme, known)
        if replacement is None or replacement == current:
            return chunk
        notes.append(f"{current} -> {replacement}")
        return _PAYTABLE_ATTR_RE.sub(
            rf"\1{replacement}\3",
            chunk,
            count=1,
        )

    return _PROCESSOR_STATUS_RE.sub(_sub, body), notes


def patch_device_manager_file(path: Path, known: set[str]) -> str | None:
    """Patch unsigned DeviceManager XML only. A 16-byte MAC header is never rewritten."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"could not read {path.name}: {exc}"
    header, xml_bytes = split_device_manager_bytes(data)
    if header:
        return f"skip {path.parent.name}/{path.name}: signed header (MAC) — not rewritten"
    try:
        body = xml_bytes.decode("utf-8")
    except UnicodeDecodeError:
        body = xml_bytes.decode("utf-8", errors="replace")
    patched, changes = _patch_processor_status_xml(body, known)
    if not changes:
        return None
    try:
        path.write_bytes(patched.encode("utf-8"))
    except OSError as exc:
        return f"could not write {path.name}: {exc}"
    return f"{path.parent.name}/{path.name}: " + ", ".join(changes)


def reconcile_processor_status_paytable(dest_root: Path) -> list[str]:
    """Align live processorStatus paytableId with names this Ruleta.exe can load."""
    notes: list[str] = []
    try:
        if not dest_root.exists():
            return notes
    except OSError:
        return notes
    live = live_ruleta_major_minor(dest_root)
    known = known_paytable_ids(dest_root, live)
    for rel in _DEVICE_MANAGER_RELATIVE:
        path = _under(dest_root, rel)
        try:
            if not path.is_file():
                continue
        except OSError as exc:
            notes.append(f"skip {rel}: {exc}")
            continue
        note = patch_device_manager_file(path, known)
        if note and not note.startswith("skip "):
            notes.append(note)
    return notes


def _replace_alias_attrs(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        current = match.group(2)
        replacement = _alias_for(current)
        if not replacement or replacement == current:
            return match.group(0)
        notes.append(f"{current} -> {replacement}")
        return f"{match.group(1)}{replacement}{match.group(3)}"

    return _ALIAS_PAYTABLE_ATTR_RE.sub(_sub, text), notes


def _replace_mapped_groups(
    text: str,
    regex: re.Pattern[str],
    aliases: dict[str, str],
) -> tuple[str, list[str]]:
    notes: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        current = match.group(2)
        replacement = _alias_for(current, aliases)
        if not replacement or replacement == current:
            return match.group(0)
        notes.append(f"{current} -> {replacement}")
        return f"{match.group(1)}{replacement}{match.group(3)}"

    return regex.sub(_sub, text), notes


def _replace_egm_paytable_tags(
    text: str,
    aliases: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    table = aliases if aliases is not None else RULETA_10_2_TO_10_1_ALIASES
    regex = (
        _ALIAS_EGM_PAYTABLE_RE
        if table is RULETA_10_2_TO_10_1_ALIASES
        else _alias_map_regex(table, r"(<EgmPaytableId>)", r"(</EgmPaytableId>)")
    )
    return _replace_mapped_groups(text, regex, table)


def _write_text_if_changed(path: Path, original: str, patched: str) -> bool:
    if patched == original:
        return False
    path.write_bytes(patched.encode("utf-8"))
    return True


def remap_combo_paytables(
    dest_root: Path,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Map combo.dat EgmPaytableId values. Host PaytableId (GR####) is kept."""
    table = aliases if aliases is not None else RULETA_10_2_TO_10_1_ALIASES
    notes: list[str] = []
    seen: set[str] = set()
    for pattern in _COMBO_GLOBS:
        parent = _under(dest_root, pattern.rsplit("/", 1)[0])
        glob_name = pattern.rsplit("/", 1)[1]
        try:
            if not parent.is_dir():
                continue
            matches = list(parent.glob(glob_name))
        except OSError as exc:
            notes.append(f"skip combo {pattern}: {exc}")
            continue
        for path in matches:
            key = str(path).casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                notes.append(f"could not read {path.name}: {exc}")
                continue
            patched, changes = _replace_egm_paytable_tags(text, table)
            if not changes:
                continue
            try:
                if _write_text_if_changed(path, text, patched):
                    notes.append(f"{path.parent.name}/{path.name}: " + ", ".join(changes))
            except OSError as exc:
                notes.append(f"could not write {path.name}: {exc}")
    return notes


def remap_aurum_setup_paytables(dest_root: Path) -> list[str]:
    """Rewrite 10.2 paytableId attributes in live AurumSetup (identity fields kept)."""
    notes: list[str] = []
    for rel in _AURUM_SETUP_RELATIVE:
        path = _under(dest_root, rel)
        try:
            if not path.is_file():
                continue
        except OSError as exc:
            notes.append(f"skip {rel}: {exc}")
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            notes.append(f"could not read {path.name}: {exc}")
            continue
        patched, changes = _replace_alias_attrs(text)
        if not changes:
            continue
        try:
            if _write_text_if_changed(path, text, patched):
                unique = list(dict.fromkeys(changes))
                notes.append(
                    f"{rel}: remapped {len(changes)} paytableId(s) "
                    + ", ".join(unique[:4])
                )
        except OSError as exc:
            notes.append(f"could not write {rel}: {exc}")
    return notes


def quarantine_incompatible_paytable_json(dest_root: Path) -> list[str]:
    """Rename 10.2-only paytable JSON so SAS/Ruleta 10.1 cannot advertise them."""
    notes: list[str] = []
    for rel in _PAYTABLE_JSON_DIRS:
        folder = _under(dest_root, rel)
        try:
            if not folder.is_dir():
                continue
            children = list(folder.glob("*.json"))
        except OSError as exc:
            notes.append(f"skip {rel}: {exc}")
            continue
        for path in children:
            if _alias_for(path.stem) is None:
                continue
            dest = path.with_name(path.name + ".disabled-101")
            try:
                if dest.exists():
                    dest.unlink()
                path.replace(dest)
                notes.append(f"quarantined {path.name}")
            except OSError as exc:
                notes.append(f"could not quarantine {path.name}: {exc}")
    return notes


def remap_live_ruleta_paytables(dest_root: Path) -> list[str]:
    """Make a restored config loadable by the Ruleta.exe that is on disk now."""
    notes: list[str] = []
    try:
        if not dest_root.exists():
            return notes
    except OSError:
        return notes
    live = live_ruleta_major_minor(dest_root)
    notes.extend(reconcile_processor_status_paytable(dest_root))
    if live_exe_is_ruleta_10_2(live):
        notes.extend(
            remap_combo_paytables(dest_root, aliases=RULETA_10_1_DYNAMIC_TO_10_2)
        )
        return notes
    notes.extend(remap_combo_paytables(dest_root))
    notes.extend(remap_aurum_setup_paytables(dest_root))
    notes.extend(quarantine_incompatible_paytable_json(dest_root))
    return notes
