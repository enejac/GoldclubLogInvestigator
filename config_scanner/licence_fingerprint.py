"""Capture licence / trial / identity file state for version-transfer diffs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from config_scanner.build_version import detect_roulette_exe_version, short_product_version
from config_scanner.machine_identity import (
    licensee_id_from_licence_bytes,
    serial_from_licence_bytes,
)
from roulette_trial import PERSISTENT_TRIAL_NAMES, VAR_TRIAL_NAMES

_EXPLICIT_RELATIVE: tuple[str, ...] = (
    "ruleta/Ruleta.exe",
    "ruleta/BuildVersion.txt",
    "ruleta/licence.dll",
    "ruleta/DataBase.db",
    "config/etc/application/ruleta/setup.xml",
    "bios/etc/application/ruleta/setup.xml",
    "config/etc/application/ruleta/godot.xml",
    "bios/etc/application/ruleta/godot.xml",
    "config/etc/application/game/switches.xml",
    "services/aurum/config/AurumSetup.xml",
    "config/etc/application/aurum/AurumSetup.xml",
    "bios/etc/application/aurum/AurumSetup.xml",
    "services/aurum/config/SASControler1/ClientsSet.xml",
    "services/aurum/config/gm2au/RuletaToMonitorSetup.xml",
    "var/state/maintenance/ProductSerialNumber.json",
    "var/state/maintenance/ProductSerialNumber.conf",
    "var/state/maintenance/ConfigureAurum.json",
    "var/state/maintenance/HostName.conf",
    "ruleta/var/SASControler1/DeviceManagerData.xml_1",
    "ruleta/var/SASControler1/DeviceManagerData.xml_2",
    "ruleta/var/gm2au/DeviceManagerData.xml_1",
    "ruleta/var/gm2au/DeviceManagerData.xml_2",
    "services/aurum/var/MeterHost/DeviceManagerData.xml_1",
    "services/aurum/var/MeterHost/DeviceManagerData.xml_2",
)

_GLOB_PATTERNS: tuple[str, ...] = (
    "config/licences/**/*.xml",
    "config/licences/**/*.dll",
    "config/licensing/**/*",
    "ruleta/persistent/*",
    "ruleta/var/Password.dat",
    "ruleta/var/HeapDataDateTime.dat",
    "var/state/gci-backup-trial/**/*",
    "config/etc/application/ruleta/paytables/**/*.json",
    "bios/etc/application/ruleta/paytables/**/*.json",
    "ruleta/var/SASControler1/*combo*.dat",
    "ruleta/var/gm2au/*combo*.dat",
    "services/aurum/config/**/*.xml",
)

_HEX_PREVIEW_MAX = 64


def _normalize_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nonzero_count(data: bytes) -> int:
    return sum(1 for b in data if b != 0)


def _hex_preview(data: bytes, limit: int = _HEX_PREVIEW_MAX) -> str:
    chunk = data[:limit]
    text = chunk.hex()
    if len(data) > limit:
        text += f"...(+{len(data) - limit} bytes)"
    return text


def _licence_xml_meta(raw: bytes) -> dict[str, str | None]:
    return {
        "serial": serial_from_licence_bytes(raw),
        "licenseeId": licensee_id_from_licence_bytes(raw),
    }


def _file_entry(path: Path, root: Path) -> dict[str, object]:
    rel = _normalize_rel(path, root)
    stat = path.stat()
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"relativePath": rel, "present": False, "error": str(exc)}

    entry: dict[str, object] = {
        "relativePath": rel,
        "present": True,
        "size": len(data),
        "mtimeUtc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": _sha256(data),
        "nonZeroBytes": _nonzero_count(data),
    }
    if len(data) <= 512:
        entry["hexPreview"] = _hex_preview(data)

    lower = rel.casefold()
    if lower.endswith(".xml") and b"<" in data[:200]:
        if "licen" in lower or b"SerialNumber" in data or b"LicenseeId" in data:
            entry["licenceMeta"] = _licence_xml_meta(data)
        head = data[:8000].lower()
        if b"paytable" in head or b"processorstatus" in head:
            for pat, key in (
                (re.compile(rb'paytableId="([^"]+)"', re.I), "paytableId"),
                (re.compile(rb'themeId="([^"]+)"', re.I), "themeId"),
                (re.compile(rb'EgmPaytableId="([^"]+)"', re.I), "egmPaytableId"),
            ):
                match = pat.search(data)
                if match:
                    entry[key] = match.group(1).decode("utf-8", errors="replace")

    if path.name.casefold() == "ruleta.exe":
        ver = detect_roulette_exe_version(path.parent)
        if ver.product_version:
            entry["productVersion"] = short_product_version(ver.product_version)

    return entry


def _collect_paths(root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for rel in _EXPLICIT_RELATIVE:
        path = root.joinpath(*rel.split("/"))
        if path.is_file():
            found[_normalize_rel(path, root)] = path

    for pattern in _GLOB_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file():
                found[_normalize_rel(path, root)] = path

    for name in (*PERSISTENT_TRIAL_NAMES, *VAR_TRIAL_NAMES):
        for base in ("ruleta/persistent", "ruleta/var"):
            path = root.joinpath(base, name)
            if path.is_file():
                found[_normalize_rel(path, root)] = path

    return [found[key] for key in sorted(found)]


def capture_licence_fingerprint(
    root: Path,
    *,
    label: str,
    scan_target: str | None = None,
) -> dict[str, object]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"GoldClub root not found: {root}")

    files = [_file_entry(path, root) for path in _collect_paths(root)]

    build_ver = root / "ruleta" / "BuildVersion.txt"
    build_text = ""
    if build_ver.is_file():
        try:
            build_text = build_ver.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass

    product_version = None
    ruleta_exe = root / "ruleta" / "Ruleta.exe"
    if ruleta_exe.is_file():
        ver = detect_roulette_exe_version(root)
        if ver.product_version:
            product_version = short_product_version(ver.product_version)

    return {
        "schema": 1,
        "label": label,
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "goldclubRoot": str(root),
        "scanTarget": scan_target or str(root),
        "ruletaProductVersion": product_version,
        "buildVersionText": build_text or None,
        "fileCount": len(files),
        "files": files,
    }


def render_fingerprint_report(manifest: dict[str, object]) -> str:
    lines = [
        f"# Licence fingerprint - {manifest.get('label', '?')}",
        "",
        f"- Captured: {manifest.get('capturedAtUtc')}",
        f"- Root: `{manifest.get('goldclubRoot')}`",
        f"- Ruleta: **{manifest.get('ruletaProductVersion') or '?'}**",
    ]
    build = manifest.get("buildVersionText")
    if build:
        lines.append(f"- BuildVersion.txt: `{str(build).splitlines()[0]}`")
    lines.extend(["", "## Files", ""])

    files = manifest.get("files")
    if not isinstance(files, list):
        return "\n".join(lines) + "\n"

    for item in files:
        if not isinstance(item, dict):
            continue
        rel = item.get("relativePath", "?")
        if not item.get("present"):
            lines.append(f"- `{rel}` - **missing**")
            continue
        sha = str(item.get("sha256", ""))[:16]
        size = item.get("size")
        nz = item.get("nonZeroBytes")
        extra = ""
        meta = item.get("licenceMeta")
        if isinstance(meta, dict):
            extra = f" serial={meta.get('serial')} licensee={meta.get('licenseeId')}"
        for key in ("paytableId", "themeId", "egmPaytableId", "productVersion"):
            if key in item:
                extra += f" {key}={item[key]}"
        if "hexPreview" in item and str(rel).casefold().endswith(".dat"):
            extra += f" hex={item['hexPreview']}"
        lines.append(f"- `{rel}` - {size} B, nz={nz}, sha256={sha}...{extra}")

    return "\n".join(lines) + "\n"


def write_fingerprint(
    root: Path,
    out_dir: Path,
    *,
    label: str,
    scan_target: str | None = None,
) -> tuple[Path, Path]:
    manifest = capture_licence_fingerprint(root, label=label, scan_target=scan_target)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", label).strip("_") or "snapshot"
    json_path = out_dir / f"{safe}.json"
    md_path = out_dir / f"LICENCE-FINGERPRINT-{safe}.md"
    json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    md_path.write_text(render_fingerprint_report(manifest), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture licence/trial file fingerprint.")
    parser.add_argument("--root", required=True, help="GoldClub root")
    parser.add_argument("--label", required=True, help="Label (e.g. 10.2)")
    parser.add_argument(
        "--out",
        default="_tmp_logs/licence_fingerprint",
        help="Output directory",
    )
    args = parser.parse_args(argv)
    json_path, md_path = write_fingerprint(
        Path(args.root),
        Path(args.out),
        label=args.label,
        scan_target=args.root,
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    print(f"Files: {payload['fileCount']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
