"""Tests for live Bug Detector session classify + reports."""

from __future__ import annotations

from network.bug_session_classify import classify_line
from network.bug_session_events import EventCategory
from network.bug_session_reports import render_developer_session, write_session_reports
from pathlib import Path


def test_classify_hardware_dallas():
    ev = classify_line(
        "2026-07-21T10:00:00.000+00:00 INFO [:] KEY=admin CODE=01 DALLAS=01D68A EVENT=IN",
        source="ruleta",
    )
    assert ev is not None
    assert ev.category == EventCategory.HARDWARE


def test_classify_human_touch():
    ev = classify_line(
        "2026-07-21T09:38:23.567+00:00 INFO [:] PLAYER0: _on_TouchZoneButTouchScreen_pressed",
        source="godot1",
    )
    assert ev is not None
    assert ev.category == EventCategory.HUMAN


def test_classify_critical_nre():
    ev = classify_line(
        "2026-07-21T09:38:23.635+00:00 ERROR [:] Unhandled exception: System.NullReferenceException: Object reference not set",
        source="godot1",
    )
    assert ev is not None
    assert ev.critical
    assert ev.category == EventCategory.CRITICAL


def test_classify_skips_sas_poll():
    ev = classify_line(
        "2026-07-21T12:00:00.000+00:00 INFO [:] qGMID1:80",
        source="sasmsgr",
    )
    assert ev is None


def test_classify_skips_wsrtl_subscribe():
    assert classify_line(
        "2026-07-21T15:19:02.280+00:00 WARN [:] Subscribing WSRTL WinValue",
        source="godot1",
    ) is None


def test_classify_stack_frame():
    ev = classify_line(
        "  at MainScreen.PayoutPressed () [0x00122] in <abc>:0",
        source="godot1",
    )
    assert ev is not None
    assert ev.category == EventCategory.CRITICAL
    assert not ev.critical


def test_developer_report_plain_markdown():
    from network.bug_session_events import SessionEvent

    events = [
        SessionEvent(
            ts="2026-07-21T15:19:05.221+00:00",
            category=EventCategory.HUMAN,
            source="godot1",
            summary="Checking OPF for 0: False",
        ),
        SessionEvent(
            ts="2026-07-21T15:19:05.283+00:00",
            category=EventCategory.CRITICAL,
            source="godot1",
            summary="Unhandled exception: System.NullReferenceException",
            critical=True,
        ),
        SessionEvent(
            ts="",
            category=EventCategory.CRITICAL,
            source="godot1",
            summary="at MainScreen.PayoutPressed ()",
            critical=False,
        ),
    ]
    md = render_developer_session(
        cabinet="10.0.0.90",
        events=events,
        started_utc="2026-07-21T13:18:37Z",
        stopped_reason="auto_critical",
        sniff_used=False,
    )
    assert "<span" not in md
    assert "## Incident" in md
    assert "Signal timeline" in md
    assert "MainScreen.PayoutPressed" in md


def test_write_session_reports(tmp_path: Path):
    from network.bug_session_events import SessionEvent

    events = [
        SessionEvent(
            ts="2026-07-21T09:38:23.567+00:00",
            category=EventCategory.HUMAN,
            source="godot1",
            summary="touch",
            commands=["PUT demo"],
        ),
        SessionEvent(
            ts="2026-07-21T09:38:23.635+00:00",
            category=EventCategory.CRITICAL,
            source="godot1",
            summary="NRE",
            critical=True,
        ),
    ]
    t, d = write_session_reports(
        cabinet="10.0.0.90",
        events=events,
        output_dir=tmp_path,
        started_utc="2026-07-21T09:00:00Z",
        stopped_reason="auto_critical",
        sniff_used=True,
    )
    assert t.is_file() and d.is_file()
    dev = d.read_text(encoding="utf-8")
    assert "8090" in dev
    assert "Commands" in render_developer_session(
        cabinet="x", events=events, started_utc="t", stopped_reason="s", sniff_used=False
    ) or "commands" in dev.lower() or "PUT demo" in dev
