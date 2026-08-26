"""Freeze this cabinet's HW/setup/wheel profile; never take it from a donor.

Used by write scope ``binaries_only``: copy Ruleta binaries, then put back
any local setup/godot/switches the pack may have overwritten. Signed
DeviceManager and licences are hashed only — never rewritten here.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from config_scanner.build_version import read_machine_serial_from_target

PROFILE_SCHEMA = 1
PROFILE_DIRNAME = "cabinet_profiles"
DEST_STATE_RELATIVE = "var/state/cabinet-profile"
MANIFEST_NAME = "cabinet-profile.json"

# Live files that must stay on this EGM. Copied as-is (encrypted setup included).
FROZEN_FILE_RELATIVE: tuple[str, ...] = (
    "config/etc/application/ruleta/setup.xml",
    "bios/etc/application/ruleta/setup.xml",
    "config/etc/application/ruleta/godot.xml",
    "bios/etc/application/ruleta/godot.xml",
    "config/etc/application/game/switches.xml",
)

# Hashed only — never written back (MAC / identity).
DEVICE_MANAGER_HASH_RELATIVE: tuple[str, ...] = (
    "ruleta/var/SASControler1/DeviceManagerData.xml_1",
    "ruleta/var/SASControler1/DeviceManagerData.xml_2",
    "ruleta/var/gm2au/DeviceManagerData.xml_1",
    "ruleta/var/gm2au/DeviceManagerData.xml_2",
    "services/aurum/var/MeterHost/DeviceManagerData.xml_1",
    "services/aurum/var/MeterHost/DeviceManagerData.xml_2",
)

# Donor pack / tree paths that surgical copy must never apply.
DONOR_REFUSE_RELATIVE: tuple[str, ...] = (
    "setup.xml",
    "config/etc/application/ruleta/setup.xml",
    "bios/etc/application/ruleta/setup.xml",
    "godot.xml",
    "config/etc/application/ruleta/godot.xml",
    "serialport/layout.json",
    "serialport/locations.json",
    "licence.dll",
    "license.dll",
    "AurumSetup.xml",
    "combo.dat",
)

_HW_LEAF_KEYS: tuple[tuple[str, str, str], ...] = (
    ("wheel", "/node[@name='wheel']", "hardware settings"),
    ("validateWheel", "/node[@name='validate wheel compatibility']", ""),
    ("sensorType", "/node[@name='sensor type']", "ballread"),
    ("forceRng", "/node[@name='forcerng']", ""),
    ("playerStations", "/node[@name='number of player stations']", ""),
)


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _under(root: Path, relative: str) -> Path:
    return root.joinpath(*relative.split("/"))


def normalize_serial_token(serial: str | None) -> str:
    text = (serial or "").strip() or "unknown"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
    return safe or "unknown"


def cabinet_profile_dirs(
    dest_root: Path,
    tool_root: Path | None,
    serial: str | None,
) -> list[Path]:
    token = normalize_serial_token(serial)
    out: list[Path] = []
    if tool_root is not None:
        out.append(Path(tool_root) / PROFILE_DIRNAME / token)
    out.append(Path(dest_root) / Path(*DEST_STATE_RELATIVE.split("/")))
    return out


def extract_hw_leaves_from_setup(setup_path: Path) -> dict[str, str]:
    """Read wheel / sensor / RNG / station leaves from live or plain setup.xml."""
    raw = _setup_plain_bytes(setup_path)
    if not raw:
        return {}
    try:
        from config_scanner.xml_diff import flat_xml_map

        flat = flat_xml_map(ET.ElementTree(ET.fromstring(raw)))
    except (ET.ParseError, OSError, ValueError, TypeError):
        return {}
    leaves: dict[str, str] = {}
    for key, suffix, prefer in _HW_LEAF_KEYS:
        matches = [
            (path, value)
            for path, value in flat.items()
            if path.endswith(suffix)
        ]
        if not matches:
            continue
        if prefer:
            preferred = [item for item in matches if prefer in item[0]]
            path, value = (preferred or matches)[0]
        else:
            path, value = matches[0]
        del path
        leaves[key] = value
    return leaves


def _setup_plain_bytes(path: Path) -> bytes | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    head = raw.lstrip()[:400]
    if head.startswith(b"<") and (
        b"gcxml-plain" in head or b"<node name=" in head or b"<config" in head
    ):
        return raw
    try:
        from ai_helper.gcxml_decrypt import (
            decrypt_gcxml_setup_to_plain_xml,
            plain_bytes_for_config_scan,
        )
    except ImportError:
        return None
    try:
        plain = plain_bytes_for_config_scan(path)
        if plain:
            return plain
        return decrypt_gcxml_setup_to_plain_xml(path).encode("utf-8")
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def _licence_identity(dest_root: Path) -> tuple[str | None, str | None]:
    from config_scanner.machine_identity import (
        is_foreign_licence_xml,
        licensee_id_from_licence_bytes,
        live_licence_files,
    )

    for path in live_licence_files(dest_root):
        if path.suffix.casefold() == ".dll":
            continue
        if is_foreign_licence_xml(path):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        return path.stem.upper(), licensee_id_from_licence_bytes(raw)
    return None, None


def freeze_cabinet_profile(
    dest_root: Path,
    *,
    tool_root: Path | None = None,
    scan_target: str | None = None,
    serial: str | None = None,
) -> dict[str, object]:
    """Copy local setup/godot/switches and write a manifest. Returns the payload."""
    dest = Path(dest_root)
    if serial is None and scan_target:
        serial = read_machine_serial_from_target(scan_target)
    if serial is None:
        serial = read_machine_serial_from_target(str(dest))
    token = normalize_serial_token(serial)
    licence_stem, licensee_id = _licence_identity(dest)

    files_meta: dict[str, dict[str, object]] = {}
    copies: dict[str, bytes] = {}
    for rel in FROZEN_FILE_RELATIVE:
        path = _under(dest, rel)
        try:
            if not path.is_file():
                continue
            data = path.read_bytes()
        except OSError:
            continue
        copies[rel] = data
        files_meta[rel] = {"sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}

    dm_meta: dict[str, dict[str, object]] = {}
    for rel in DEVICE_MANAGER_HASH_RELATIVE:
        path = _under(dest, rel)
        try:
            if not path.is_file():
                continue
            digest = _sha1_file(path)
            dm_meta[rel] = {"sha1": digest, "size": path.stat().st_size}
        except OSError:
            continue

    hw_leaves: dict[str, str] = {}
    for rel in (
        "config/etc/application/ruleta/setup.xml",
        "bios/etc/application/ruleta/setup.xml",
    ):
        path = _under(dest, rel)
        try:
            if path.is_file():
                hw_leaves = extract_hw_leaves_from_setup(path)
                if hw_leaves:
                    break
        except OSError:
            continue

    payload: dict[str, object] = {
        "schema": PROFILE_SCHEMA,
        "machineSerial": token,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "licenceStem": licence_stem,
        "licenseeId": licensee_id,
        "hwLeaves": hw_leaves,
        "files": files_meta,
        "deviceManager": dm_meta,
    }

    written_to: list[str] = []
    for folder in cabinet_profile_dirs(dest, tool_root, token):
        try:
            files_dir = folder / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            for rel, data in copies.items():
                target = files_dir.joinpath(*rel.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (folder / MANIFEST_NAME).write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except OSError:
            continue
        written_to.append(str(folder))
    payload["writtenTo"] = written_to
    if not written_to:
        raise OSError("Could not write cabinet profile (tool root and dest state both failed).")
    return payload


def load_cabinet_profile(
    dest_root: Path,
    *,
    tool_root: Path | None = None,
    serial: str | None = None,
) -> tuple[Path, dict[str, object]] | None:
    for folder in cabinet_profile_dirs(dest_root, tool_root, serial):
        manifest = folder / MANIFEST_NAME
        try:
            if not manifest.is_file():
                continue
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return folder, payload
    return None


def reapply_frozen_cabinet_files(
    dest_root: Path,
    *,
    tool_root: Path | None = None,
    serial: str | None = None,
) -> list[str]:
    """Put frozen setup/godot/switches back when live bytes no longer match."""
    loaded = load_cabinet_profile(dest_root, tool_root=tool_root, serial=serial)
    if loaded is None:
        return ["no cabinet profile to reapply"]
    folder, payload = loaded
    files = payload.get("files")
    if not isinstance(files, dict):
        return ["cabinet profile has no frozen files"]
    notes: list[str] = []
    dest = Path(dest_root)
    for rel, meta in files.items():
        if not isinstance(rel, str):
            continue
        frozen = folder.joinpath("files", *rel.split("/"))
        live = _under(dest, rel)
        try:
            if not frozen.is_file():
                continue
            frozen_bytes = frozen.read_bytes()
            live_bytes = live.read_bytes() if live.is_file() else b""
        except OSError as exc:
            notes.append(f"could not reapply {rel}: {exc}")
            continue
        expected = ""
        if isinstance(meta, dict):
            expected = str(meta.get("sha1") or "")
        frozen_sha = hashlib.sha1(frozen_bytes).hexdigest()
        if expected and frozen_sha != expected:
            notes.append(f"skip {rel}: frozen copy sha1 mismatch")
            continue
        if live_bytes == frozen_bytes:
            continue
        try:
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_bytes(frozen_bytes)
        except OSError as exc:
            notes.append(f"could not write {rel}: {exc}")
            continue
        notes.append(f"reapplied cabinet profile {rel}")
    return notes


def donor_refuse_paths_present(pack_root: Path) -> list[str]:
    """Relative paths in a donor tree that surgical copy must not apply."""
    root = Path(pack_root)
    found: list[str] = []
    for rel in DONOR_REFUSE_RELATIVE:
        path = root.joinpath(*rel.split("/"))
        try:
            if path.is_file():
                found.append(rel.replace("\\", "/"))
        except OSError:
            continue
    return found
