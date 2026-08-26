"""Bug detector unit tests (Collect/Payout NRE reports)."""

from __future__ import annotations

from pathlib import Path

from network.bug_detector import (
    detect_collect_payout_incidents,
    render_developer_report,
    render_tester_report,
    scan_and_write_reports,
)


SAMPLE_GODOT = """2026-07-21T09:27:20.671+00:00 INFO [:] This process' PID:4076
2026-07-21T09:27:24.386+00:00 INFO [:] Skin: new
2026-07-21T09:27:24.396+00:00 INFO [:] Starting new gui as per --skin argument
2026-07-21T09:27:26.510+00:00 INFO [:] Initiating get with age
2026-07-21T09:30:14.816+00:00 INFO [:] Sending put action Paytable data 2:paytable
2026-07-21T09:30:14.816+00:00 INFO [:] Sending put action MenuCommands data 193
2026-07-21T09:30:14.816+00:00 INFO [:] Sending put action SetChip data 0
2026-07-21T09:30:14.816+00:00 INFO [:] Sending put action SetNeighboursPower data 0
2026-07-21T09:38:21.793+00:00 INFO [:] trying to subscribe
2026-07-21T09:38:21.793+00:00 INFO [:] trying to subscribe
2026-07-21T09:38:21.800+00:00 INFO [:] trying to subscribe CreditsValue
2026-07-21T09:38:21.800+00:00 WARN [:] Subscribing WSRTL CreditsValue
2026-07-21T09:38:21.911+00:00 INFO [:] Running base setup L
2026-07-21T09:38:21.911+00:00 INFO [:] Id is 0
2026-07-21T09:38:21.911+00:00 INFO [:] Running base setup S
2026-07-21T09:38:22.724+00:00 WARN [:] Missing node. /root/ScreenContainer/Node2D/TableLayout/TokenSpawner/TouchInner
2026-07-21T09:38:23.215+00:00 INFO [:] trying to subscribe Eraser
2026-07-21T09:38:23.567+00:00 INFO [:] PLAYER0: _on_TouchZoneButTouchScreen_pressed
2026-07-21T09:38:23.567+00:00 INFO [:] Checking OPF for 0: False
2026-07-21T09:38:23.635+00:00 ERROR [:] Unhandled exception: System.NullReferenceException: Object reference not set to an instance of an object
  at MainScreen.PayoutPressed () [0x00122] in <abc>:0
  at BarsButtonController.PayoutPressed () [0x0001f] in <abc>:0
  at WinSysButtonBase.ButtonUp () [0x00001] in <abc>:0
"""

SAMPLE_RULETA = """2026-07-21T09:38:23.753+00:00 WARN [:] Proces with ID 4076 exited unexpectedly.
"""


def test_detect_collect_payout_with_layout():
    incidents = detect_collect_payout_incidents(
        cabinet="10.0.0.90",
        godot_text=SAMPLE_GODOT,
        ruleta_text=SAMPLE_RULETA,
        godot_log=r"C:\Goldclub\var\log\godot1\2026-07-21.log",
        ruleta_log=r"C:\Goldclub\var\log\ruleta\2026-07-21.log",
    )
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.layout_before_crash is True
    assert "PayoutPressed" in inc.stack_excerpt
    assert inc.crash_time.startswith("2026-07-21T09:38:23")


def test_render_reports_contain_repro_and_middleware(tmp_path: Path):
    incidents = detect_collect_payout_incidents(
        cabinet="local",
        godot_text=SAMPLE_GODOT,
        ruleta_text=SAMPLE_RULETA,
        godot_log="godot1.log",
        ruleta_log="ruleta.log",
    )
    inc = incidents[0]
    tester = render_tester_report(inc)
    dev = render_developer_report(inc)
    assert "How to reproduce" in tester
    assert "Collect" in tester
    assert "Pass / fail" in tester
    assert "MainScreen.PayoutPressed" in dev
    assert "Middleware" in dev
    assert "layout" in tester.lower() or "Layout" in tester
    assert "Flow (UTC) + Godot commands" in dev
    assert "Running base setup L" in dev
    assert "TouchZone" in dev or "Collect press" in dev
    assert "Subscribing WSRTL CreditsValue" in dev or "CreditsValue" in dev
    assert len(inc.flow_steps) >= 5


def test_scan_writes_files_from_fixture_tree(tmp_path: Path, monkeypatch):
    log_root = tmp_path / "var" / "log"
    gdir = log_root / "godot1"
    rdir = log_root / "ruleta"
    gdir.mkdir(parents=True)
    rdir.mkdir(parents=True)
    (gdir / "2026-07-21.log").write_text(SAMPLE_GODOT, encoding="utf-8")
    (rdir / "2026-07-21.log").write_text(SAMPLE_RULETA, encoding="utf-8")

    monkeypatch.setattr(
        "network.bug_detector.resolve_log_roots",
        lambda _cab: [log_root],
    )
    out = tmp_path / "out"
    result = scan_and_write_reports("fixture", output_dir=out)
    assert result.ok
    assert len(result.incidents) == 1
    assert len(result.tester_paths) == 1
    assert len(result.developer_paths) == 1
    assert Path(result.tester_paths[0]).is_file()
    assert "tester guide" in Path(result.tester_paths[0]).read_text(encoding="utf-8").lower()
    assert "developer notes" in Path(result.developer_paths[0]).read_text(encoding="utf-8").lower()