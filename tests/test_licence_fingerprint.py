"""Licence fingerprint capture for 10.1 <-> 10.2 transfer diffs."""

from __future__ import annotations

import json
from pathlib import Path

from config_scanner.licence_fingerprint import (
    capture_licence_fingerprint,
    render_fingerprint_report,
    write_fingerprint,
)


def _write_licence_xml(path: Path, serial: str, licensee: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<root><SerialNumber>{serial}</SerialNumber>"
        f"<LicenseeId>{licensee}</LicenseeId></root>",
        encoding="utf-8",
    )


def test_capture_licence_and_trial_files(tmp_path: Path) -> None:
    root = tmp_path / "goldclub"
    _write_licence_xml(
        root / "config" / "licences" / "37A55022DCBEF351AE27471D181B1EF5.xml",
        "330106",
        "12262688",
    )
    (root / "ruleta" / "licence.dll").parent.mkdir(parents=True)
    (root / "ruleta" / "licence.dll").write_bytes(b"\x00" * 12)
    persist = root / "ruleta" / "persistent"
    persist.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 28 + b"\x01\x02")
    (root / "ruleta" / "var").mkdir(parents=True)
    (root / "ruleta" / "var" / "Password.dat").write_bytes(b"pw")

    manifest = capture_licence_fingerprint(root, label="test")
    assert manifest["fileCount"] >= 4
    files = {f["relativePath"]: f for f in manifest["files"]}
    lic = files["config/licences/37A55022DCBEF351AE27471D181B1EF5.xml"]
    assert lic["licenceMeta"]["serial"] == "330106"
    assert lic["licenceMeta"]["licenseeId"] == "12262688"
    activate = files["ruleta/persistent/RouletteActivate.dat"]
    assert activate["nonZeroBytes"] == 2
    assert "hexPreview" in activate


def test_write_fingerprint_outputs(tmp_path: Path) -> None:
    root = tmp_path / "gc"
    (root / "ruleta" / "licence.dll").parent.mkdir(parents=True)
    (root / "ruleta" / "licence.dll").write_bytes(b"x")
    out = tmp_path / "out"
    json_path, md_path = write_fingerprint(root, out, label="10.2")
    assert json_path.is_file()
    assert md_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["label"] == "10.2"
    report = render_fingerprint_report(payload)
    assert "Licence fingerprint" in report
    assert "licence.dll" in report
