"""SAS TXRX timeline ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from network.sas_decoder import extract_sas_timeline_events
from parser import parse_log_file


def test_extract_sas_timeline_events_yields_packets(tmp_path: Path) -> None:
    p = tmp_path / "TXRXData.dat"
    p.write_text(
        "Fri Mar 27 15:56:10 2026\n"
        "RX<= 01 6F 01 02\n"
        "Fri Mar 27 15:56:11 2026\n"
        "TX>= 01 00\n",
        encoding="utf-8",
    )
    rows = list(extract_sas_timeline_events(str(p)))
    assert len(rows) == 2
    assert rows[0][0].year == 2026
    assert "RX<=" in rows[0][1]
    assert "TX>=" in rows[1][1]


def test_parse_txrxdata_dat_produces_nodes_and_incidents(tmp_path: Path) -> None:
    p = tmp_path / "TXRXData.dat"
    p.write_text(
        "Fri Mar 27 15:56:10 2026\n"
        "RX<= 01 6F 01 02\n",
        encoding="utf-8",
    )
    r = parse_log_file(p)
    assert len(r.state_nodes) == 1
    assert len(r.incidents) == 1
    assert r.incidents[0].severity == "INFO"
    assert r.incidents[0].game == "[SAS Protocol]"
    n = r.state_nodes[0]
    assert "[SAS PACKET]" in n.state_name
    assert n.previous_state == "[SAS Protocol]"


def test_parse_txrx_respects_scan_window(tmp_path: Path) -> None:
    p = tmp_path / "TXRXData.dat"
    p.write_text(
        "Fri Mar 27 15:56:10 2026\n"
        "RX<= A\n"
        "Fri Mar 27 15:56:11 2026\n"
        "RX<= B\n",
        encoding="utf-8",
    )
    lo = datetime(2026, 3, 27, 15, 56, 10, tzinfo=timezone.utc)
    hi = datetime(2026, 3, 27, 15, 56, 10, 500000, tzinfo=timezone.utc)
    r = parse_log_file(p, scan_start_time=lo, scan_end_time=hi)
    assert len(r.incidents) == 1
    assert "A" in r.incidents[0].line_snippet
