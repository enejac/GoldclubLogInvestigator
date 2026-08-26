"""Parser integration: previous-game context after multigame / unload."""

from datetime import datetime, timezone
from pathlib import Path

from parser import (
    MULTIGAME_SELECTOR_GAME,
    extract_datetime_from_line,
    parse_log_file,
    read_log_context,
)


def test_incident_game_includes_previous_theme_after_unload(tmp_path: Path) -> None:
    log = tmp_path / "slot.log"
    log.write_text(
        "2026-01-01T00:00:00.000+00:00  INFO  [Loading Game] ThemeName [Link2Win]\n"
        "2026-01-01T00:00:01.000+00:00  INFO  [SlotMachine] Start: UnloadTheme\n"
        "2026-01-01T00:00:02.000+00:00  ERROR  [Preload Link2WinHoldAndSpinTextures] "
        "System.ArgumentOutOfRangeException: boom\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    exc_incidents = [i for i in result.incidents if "ArgumentOutOfRangeException" in i.line_snippet]
    assert exc_incidents, "expected incident on ArgumentOutOfRangeException line"
    assert exc_incidents[0].game == f"{MULTIGAME_SELECTOR_GAME} (Prev: Link2Win)"


def test_parse_log_file_respects_time_bounds(tmp_path: Path) -> None:
    log = tmp_path / "bounded.log"
    log.write_text(
        "2026-01-01T10:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: early\n"
        "2026-01-01T11:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: mid\n"
        "2026-01-01T12:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: late\n",
        encoding="utf-8",
    )
    t0 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 30, 0, tzinfo=timezone.utc)
    r = parse_log_file(log, scan_start_time=t0, scan_end_time=t1)
    snippets = [i.line_snippet for i in r.incidents]
    assert any("mid" in s for s in snippets)
    assert not any("early" in s for s in snippets)
    assert not any("late" in s for s in snippets)


def test_time_bounded_scan_survives_one_out_of_order_line(tmp_path: Path) -> None:
    """A single clock-skewed line must not discard the rest of the file."""
    log = tmp_path / "skew.log"
    log.write_text(
        "2026-01-01T11:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: first\n"
        "2026-01-05T09:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: skewed\n"
        "2026-01-01T11:10:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: second\n",
        encoding="utf-8",
    )
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    r = parse_log_file(log, scan_start_time=t0, scan_end_time=t1)
    snippets = [i.line_snippet for i in r.incidents]
    assert any("first" in s for s in snippets)
    assert any("second" in s for s in snippets)
    assert not any("skewed" in s for s in snippets)


def test_time_bounded_scan_stops_after_a_run_of_late_lines(tmp_path: Path) -> None:
    """Chronological logs still stop early instead of reading to the end."""
    from parser import _PAST_SCAN_END_STREAK_LIMIT

    lines = ["2026-01-01T11:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: inside\n"]
    lines += [
        f"2026-01-02T00:00:{i % 60:02d}.000+00:00  ERROR  "
        f"System.ArgumentOutOfRangeException: after{i}\n"
        for i in range(_PAST_SCAN_END_STREAK_LIMIT + 5)
    ]
    lines.append(
        "2026-01-01T11:30:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: unreachable\n"
    )
    log = tmp_path / "ordered.log"
    log.write_text("".join(lines), encoding="utf-8")
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    r = parse_log_file(log, scan_start_time=t0, scan_end_time=t1)
    snippets = [i.line_snippet for i in r.incidents]
    assert any("inside" in s for s in snippets)
    assert not any("after" in s for s in snippets)
    assert not any("unreachable" in s for s in snippets)


def test_time_bounded_scan_inherits_timestamp_for_continuation_lines(tmp_path: Path) -> None:
    """Stack lines without their own clock still respect the window via last seen timestamp."""
    log = tmp_path / "inherit.log"
    log.write_text(
        "2026-01-01T10:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: early\n"
        "   at OrphanBeforeWindow()\n"
        "2026-01-01T11:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: mid\n"
        "   NullReferenceException in continuation line\n"
        "2026-01-01T12:00:00.000+00:00  ERROR  System.ArgumentOutOfRangeException: late\n"
        "   at AfterEnd()\n",
        encoding="utf-8",
    )
    t0 = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 11, 30, 0, tzinfo=timezone.utc)
    r = parse_log_file(log, scan_start_time=t0, scan_end_time=t1)
    snippets = [i.line_snippet for i in r.incidents]
    assert any("mid" in s for s in snippets)
    assert any("continuation" in s for s in snippets)
    assert not any("early" in s for s in snippets)
    assert not any("OrphanBeforeWindow" in s for s in snippets)
    assert not any("late" in s for s in snippets)
    assert not any("AfterEnd" in s for s in snippets)
    cont = [i for i in r.incidents if "continuation" in i.line_snippet]
    assert cont and cont[0].timestamp is not None
    assert cont[0].timestamp.hour == 11 and cont[0].timestamp.minute == 0
    assert cont[0].timestamp_display().endswith(" UTC")


def test_timestamp_display_matches_log_wall_clock_with_offset(tmp_path: Path) -> None:
    """Incident header should show the same clock as the log line (not normalized UTC only)."""
    log = tmp_path / "tz.log"
    line = (
        "2026-03-31T08:00:38.075+02:00 ERROR [Preload] OneHand.Utilities.RhCache - "
        "System.ArgumentOutOfRangeException: boom\n"
    )
    log.write_text(line, encoding="utf-8")
    r = parse_log_file(log)
    assert r.incidents
    inc = r.incidents[0]
    assert "08:00:38" in inc.timestamp_display()
    assert "+02:00" in inc.timestamp_display()
    assert inc.timestamp is not None
    assert inc.timestamp.hour == 6  # stored as UTC for sorting / bounds


def test_stack_line_inherits_parent_timestamp_without_time_bounds(tmp_path: Path) -> None:
    """Orphan ``System.Exception`` line gets the clock from the prior ERROR line."""
    log = tmp_path / "stack_inherit.log"
    parent_line = (
        "2026-01-15T10:05:30.000+00:00  ERROR  [SlotMachine] Game loop failed\n"
    )
    stack_line = "   System.Exception: Fatal Crash\n"
    log.write_text(parent_line + stack_line, encoding="utf-8")
    parent_dt = extract_datetime_from_line(parent_line)
    assert parent_dt is not None
    assert extract_datetime_from_line(stack_line) is None

    r = parse_log_file(log)
    fatal = [i for i in r.incidents if "Fatal Crash" in i.line_snippet]
    assert len(fatal) == 1
    assert fatal[0].timestamp is not None
    assert fatal[0].timestamp == parent_dt


def test_onehand_nested_socket_exception_is_detected_with_stack_context(
    tmp_path: Path,
) -> None:
    log = tmp_path / "slotlog_socket_exception.log"
    log.write_text(
        "2026-06-15T14:41:25.215+01:00 INFO  [190] OneHand.AurumEGM - "
        "Network address changed, restarting Aurum Messenger.\n"
        "2026-06-15T14:41:30.781+01:00 ERROR [146] OneHand.MainFrm - "
        "System.Net.Sockets.SocketException (0x80004005): The requested address "
        "is not valid in its context\n"
        "   at System.Runtime.Remoting.Channels.Http.HttpServerChannel.StartListening(Object data)\n"
        "   at GoldClub.Aurum.GCMessenger.StartSecure()\n"
        "   at OneHand.AurumEGM.OnNetworkChanged(Object sender, EventArgs e) "
        "in C:\\repos\\onehand\\src\\OneHand\\AurumEGM.cs:line 409\n",
        encoding="utf-8",
    )

    result = parse_log_file(log)
    socket_incidents = [
        i for i in result.incidents if "SocketException" in i.line_snippet
    ]

    assert len(socket_incidents) == 1
    assert socket_incidents[0].severity == "CRITICAL"
    assert socket_incidents[0].error_type == "Exception / Fatal"

    context = read_log_context(log, socket_incidents[0].line_number, context_after=4)
    assert "at System.Runtime.Remoting.Channels.Http.HttpServerChannel.StartListening" in context
    assert "at OneHand.AurumEGM.OnNetworkChanged" in context


def test_custom_compound_exception_is_detected_as_critical(tmp_path: Path) -> None:
    """Non-``System`` compound exception types must still classify as CRITICAL."""
    log = tmp_path / "slotlog_custom_exception.log"
    log.write_text(
        "2026-06-15T09:00:00.000+00:00 ERROR [42] MyApp.Orders - "
        "MyApp.Domain.OrderException: order rejected\n"
        "   at MyApp.Domain.OrderService.Place()\n"
        "2026-06-15T09:00:01.000+00:00 INFO  [42] MyApp.Orders - back to normal\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    hits = [i for i in result.incidents if "OrderException" in i.line_snippet]
    assert len(hits) == 1
    assert hits[0].severity == "CRITICAL"
