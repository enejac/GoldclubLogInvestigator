"""network.case_packer — lightweight developer handoff zip."""

from __future__ import annotations

import json
import zipfile

import pytest
from datetime import datetime, timezone
from pathlib import Path

from parser import Incident
from network.case_packer import create_case_pack, suggest_case_pack_zip_name


def _inc(n: int) -> Incident:
    return Incident(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        game="G",
        severity="CRITICAL",
        error_type=f"E{n}",
        probable_cause="c",
        log_file_path=f"/f{n}.log",
        line_number=n,
        line_snippet=f"line {n}",
    )


def test_suggest_case_pack_zip_name_gst() -> None:
    name = suggest_case_pack_zip_name("gst20664")
    assert name.startswith("Case_GST20664_")
    assert name.endswith(".zip")


def test_create_case_pack_writes_members(tmp_path: Path) -> None:
    zpath = tmp_path / "out.zip"
    incs = [_inc(1), _inc(2)]
    out = create_case_pack(
        zpath,
        {"IP": "10.0.0.1", "Hostname": "h", "Drift": 1.5},
        {"abc": "note"},
        incs,
    )
    assert Path(out) == zpath.resolve()
    with zipfile.ZipFile(zpath, "r") as zf:
        names = set(zf.namelist())
        assert names == {
            "metadata.json",
            "bookmarks.json",
            "investigation.log",
            "IncidentReport.html",
        }
        meta = json.loads(zf.read("metadata.json").decode("utf-8"))
        assert meta["machine_info"]["IP"] == "10.0.0.1"
        assert "exported_at_utc" in meta
        assert meta.get("product_build") is None
        bm = json.loads(zf.read("bookmarks.json").decode("utf-8"))
        assert bm["incident_id_to_note"]["abc"] == "note"
        log = zf.read("investigation.log").decode("utf-8")
        assert "line 1" in log and "line 2" in log


def test_create_case_pack_includes_accounting_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(ip: str, local_path: str) -> tuple[str, str]:
        assert ip == "10.0.0.5"
        assert local_path.endswith(".jpg")
        return "qr-payload-line", "state-body"

    monkeypatch.setattr("network.case_packer.fetch_accounting_data", fake_fetch)
    zpath = tmp_path / "with_acct.zip"
    create_case_pack(
        zpath,
        {"IP": "10.0.0.5"},
        {},
        [_inc(1)],
        remote_ip="10.0.0.5",
    )
    with zipfile.ZipFile(zpath, "r") as zf:
        names = set(zf.namelist())
        assert "accounting_summary.txt" in names
        txt = zf.read("accounting_summary.txt").decode("utf-8")
        assert "=== ACCOUNTING QR PAYLOAD ===" in txt
        assert "qr-payload-line" in txt
        assert "=== BACKEND STATE (gm2au) ===" in txt
        assert "state-body" in txt
        html = zf.read("IncidentReport.html").decode("utf-8")
        assert "Goldclub QA Incident Report" in html
        assert "accounting_summary.txt" in html


def test_create_case_pack_includes_sas_analysis(tmp_path: Path) -> None:
    sas = tmp_path / "cabinet.dat"
    sas.write_text(
        "Fri Mar 27 15:56:10 2026\n"
        "RX<= 01 6F 05 01 02 03 04 05 00 00\n",
        encoding="utf-8",
    )
    zpath = tmp_path / "with_sas.zip"
    create_case_pack(
        zpath,
        {"IP": "10.0.0.1"},
        {},
        [_inc(1)],
        sas_file_path=str(sas),
    )
    with zipfile.ZipFile(zpath, "r") as zf:
        assert "sas_analysis.txt" in zf.namelist()
        assert "IncidentReport.html" in zf.namelist()
        body = zf.read("sas_analysis.txt").decode("utf-8")
        assert "RAW:" in body or "6F" in body
        html = zf.read("IncidentReport.html").decode("utf-8")
        assert "sas_analysis.txt" in html


def test_create_case_pack_includes_evidence_png(tmp_path: Path) -> None:
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n\x00" + b"x" * 20)
    zpath = tmp_path / "c.zip"
    create_case_pack(
        zpath,
        {"IP": "1.1.1.1"},
        {},
        [_inc(1)],
        screenshot_path=str(png),
    )
    with zipfile.ZipFile(zpath, "r") as zf:
        assert "evidence.png" in zf.namelist()
        assert "IncidentReport.html" in zf.namelist()
        assert len(zf.read("evidence.png")) > 0
