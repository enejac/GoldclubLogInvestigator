"""Roulette / GoldClub log layout: CRIT blocks, ERRO, subsystem context."""

from pathlib import Path

from parser import (
    LiveFileParseState,
    create_live_state_after_rewind,
    create_live_state_at_eof,
    feed_live_byte_chunk,
    parse_log_file,
    subsystem_label_from_log_path,
)
from parser_rules import match_known_issue, reload_known_issues


def test_subsystem_label_from_folder() -> None:
    assert subsystem_label_from_log_path(Path(r"C:\log\ruleta\2025-11-24.log")) == "Roulette"
    assert subsystem_label_from_log_path(Path(r"C:\log\BiOS\2025-11-24.log")) == "BiOS"
    assert subsystem_label_from_log_path(Path(r"C:\log\GoldClub.Aurum.Services\a.log")) == "Aurum"
    assert subsystem_label_from_log_path(Path(r"C:\log\godot1\2025-11-24.txt")) == "Godot UI"


def test_goldclub_crit_exception_block_emits_one_primary_incident(tmp_path: Path) -> None:
    log = tmp_path / "BiOS" / "2025-11-24.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2025-11-24T09:02:49.761+00:00 CRIT [:461342] Exception: Could not find file "
        "'C:\\goldclub\\data\\bios\\plugins\\GoldClub.BiOS.Plugin.Ruleta.dll'.\n"
        "2025-11-24T09:02:49.761+00:00 CRIT [:461342] ExceptionType: System.IO.FileNotFoundException\n"
        "2025-11-24T09:02:49.775+00:00 CRIT [:461342] StackTrace:    at System.IO.__Error.WinIOError()\n"
        "   at System.IO.FileStream.Init(String path)\n"
        "2025-11-24T09:02:49.775+00:00 CRIT [:461342] Source: mscorlib\n"
        "2025-11-24T09:02:49.775+00:00 CRIT [:461342] TargetSite: Void WinIOError()\n"
        "2025-11-24T09:02:49.775+00:00 CRIT [:461342] InnerException: \n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    assert len(result.incidents) == 1
    inc = result.incidents[0]
    assert inc.game == "BiOS"
    assert inc.severity == "CRITICAL"
    assert inc.error_type == "Ruleta BiOS Plugin Missing"
    assert "Ruleta.dll" in inc.line_snippet


def test_erro_lines_are_medium_not_critical(tmp_path: Path) -> None:
    log = tmp_path / "BiOS" / "2025-11-24.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2025-11-24T09:02:51.156+00:00 ERRO [:63449985] Proxy error: channel_name=switch0\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    assert len(result.incidents) == 1
    assert result.incidents[0].severity == "MEDIUM"
    assert result.incidents[0].error_type == "Error Log Line"




def test_ruleta_number_on_screen_is_critical(tmp_path: Path) -> None:
    reload_known_issues()
    log = tmp_path / "ruleta Roulette" / "2026-07-23.log"
    log.parent.mkdir(parents=True)
    line = "2026-07-23T20:09:00.343+00:00 ERRO [:] ERR: number on screen: 78\n"
    log.write_text(line, encoding="utf-8")
    result = parse_log_file(log)
    assert len(result.incidents) == 1
    inc = result.incidents[0]
    assert inc.game == "Roulette"
    assert inc.severity == "CRITICAL"
    assert inc.error_type == "Roulette Number On Screen Mismatch"
    assert "number on screen: 78" in (inc.line_snippet or "")
    match = match_known_issue(inc.line_snippet, inc.severity)
    assert match is not None
    assert match.issue_id == "GCI-ROULETTE-006"


def test_critical_chip_matches_erro_medium_lines() -> None:
    from gui.filter_runnable import QuickFilterSnapshot, quick_filter_match
    from parser import Incident

    inc = Incident(
        timestamp=None,
        game="Roulette",
        severity="MEDIUM",
        error_type="Error Log Line",
        probable_cause="",
        log_file_path=r"\\x\\ruleta Roulette\\a.log",
        line_number=1,
        line_snippet="2026-07-23T20:09:00.343+00:00 ERRO [:] Proxy error: channel_name=switch0",
    )
    assert quick_filter_match(inc, QuickFilterSnapshot(critical=True)) is True
    assert quick_filter_match(inc, QuickFilterSnapshot(warn=True)) is True
    assert quick_filter_match(inc, QuickFilterSnapshot(critical=False, warn=False)) is True

    # Same message word "error" but WARN level — not Critical chip.
    warn_inc = Incident(
        timestamp=None,
        game="LogDaemon",
        severity="MEDIUM",
        error_type="Error Log Line",
        probable_cause="",
        log_file_path=r"\\x\\GoldClub.Logging.LogDaemon\\a.log",
        line_number=1,
        line_snippet="2026-07-23T21:06:49.078+00:00 WARN  [:] Proxy error: channel_name=switch0",
    )
    assert quick_filter_match(warn_inc, QuickFilterSnapshot(critical=True)) is False


def test_warn_argument_exception_is_low_not_critical(tmp_path: Path) -> None:
    log = tmp_path / "HWSubsys" / "2025-11-24.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2025-11-24T10:22:28.187+00:00 WARN [:] Argument exception: Interface not found.\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    assert len(result.incidents) == 1
    assert result.incidents[0].severity == "LOW"
    assert result.incidents[0].error_type == "Argument / Interface Warning"


def test_message_dispatcher_classified_and_known_issue(tmp_path: Path) -> None:
    reload_known_issues()
    log = tmp_path / "ruleta" / "2025-11-24.log"
    log.parent.mkdir(parents=True)
    line = (
        "2025-11-24T10:01:19.689+00:00 CRIT [:] MessageDispatcher.PostMessage: "
        "System.InvalidOperationException: Sequence contains no elements\n"
    )
    log.write_text(line, encoding="utf-8")
    result = parse_log_file(log)
    assert len(result.incidents) == 1
    inc = result.incidents[0]
    assert inc.game == "Roulette"
    assert inc.error_type == "MessageDispatcher Fault"
    match = match_known_issue(inc.line_snippet, inc.severity)
    assert match is not None
    assert match.issue_id == "GCI-ROULETTE-003"


def test_live_watch_suppresses_exception_companion_lines(tmp_path: Path) -> None:
    log = tmp_path / "BiOS" / "live.log"
    log.parent.mkdir(parents=True)
    log.write_text("", encoding="utf-8")
    state = LiveFileParseState(
        path_str=str(log),
        byte_offset=0,
        pending_fragment="",
        next_line_number=1,
        current_game="BiOS",
    )
    chunk = (
        "2025-11-24T09:03:30.120+00:00 CRIT [:] Exception: IO failure on c:\\tmp\\\n"
        "2025-11-24T09:03:30.120+00:00 CRIT [:] ExceptionType: System.IO.IOException\n"
        "2025-11-24T09:03:30.121+00:00 CRIT [:] StackTrace:    at System.IO.Directory.Delete()\n"
    ).encode("utf-8")
    incidents, _ = feed_live_byte_chunk(state, chunk)
    assert len(incidents) == 1
    assert incidents[0].error_type == "Critical Log Exception"


def test_godot_unhandled_payout_exception(tmp_path: Path) -> None:
    reload_known_issues()
    log = tmp_path / "godot1" / "2025-11-24.txt"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2025-11-24 14:01:14.0057 WARN Missing node. /root/ScreenContainer/Node2D/TableLayout/TokenSpawner/TouchInner\n"
        "2025-11-24 14:01:14.9999 ERROR Unhandled exception: System.NullReferenceException: Object reference not set to an instance of an object\n"
        "  at MainScreen.PayoutPressed () [0x00139] in <c8b4c88763934f50888c3e53a77c9f3c>:0\n"
        "  at BarsButtonController.PayoutPressed () [0x0001f] in <c8b4c88763934f50888c3e53a77c9f3c>:0\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    types = [i.error_type for i in result.incidents]
    assert "Godot Unhandled Exception" in types
    assert len(result.incidents) == 2
    unhandled = next(i for i in result.incidents if i.error_type == "Godot Unhandled Exception")
    assert unhandled.game == "Godot UI"
    assert unhandled.severity == "CRITICAL"
    match = match_known_issue(unhandled.line_snippet, unhandled.severity)
    assert match is not None
    assert match.issue_id == "GCI-GODOT-001"


def test_ruleta_godot_kill_detected(tmp_path: Path) -> None:
    reload_known_issues()
    log = tmp_path / "ruleta" / "2025-11-24.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2025-11-24T14:01:41.523+00:00 WARN [:] Godot did not exit in expected time of 5 seconds, killing process 5580\n"
        "2025-11-24T14:01:19.114+00:00 WARN [:] Proces with ID 3564 exited unexpectedly.\n",
        encoding="utf-8",
    )
    result = parse_log_file(log)
    types = {i.error_type for i in result.incidents}
    assert "Godot Process Crash / Forced Exit" in types
    assert len(result.incidents) == 2

def test_live_state_uses_subsystem_label(tmp_path: Path) -> None:
    log = tmp_path / "godot1" / "live.txt"
    log.parent.mkdir(parents=True)
    log.write_text("2025-11-24 14:00:00.0000 INFO warmup\n", encoding="utf-8")
    state = create_live_state_at_eof(log)
    assert state is not None
    assert state.current_game == "Godot UI"


def test_live_state_after_rewind_reads_truncated_file_from_start(tmp_path: Path) -> None:
    """A truncated / recreated log must be re-read, not skipped to EOF."""
    log = tmp_path / "godot1" / "live.txt"
    log.parent.mkdir(parents=True)
    log.write_text("2025-11-24 14:00:00.0000 INFO old and long warmup line\n", encoding="utf-8")
    state = create_live_state_at_eof(log)
    assert state is not None
    assert state.byte_offset > 0

    # Writer truncates and starts over; the new content is shorter than our offset.
    log.write_text("2025-11-24 14:05:00.0000 INFO fresh\n", encoding="utf-8")
    fresh = create_live_state_after_rewind(log)
    assert fresh is not None
    assert fresh.byte_offset == 0
    assert fresh.next_line_number == 1
    assert fresh.current_game == "Godot UI"


def test_live_state_after_rewind_caps_backfill_at_a_line_boundary(tmp_path: Path) -> None:
    """A huge replacement is capped, and resumes on a line start (no partial line)."""
    log = tmp_path / "SlotLog" / "live.log"
    log.parent.mkdir(parents=True)
    lines = [f"2025-11-24 14:00:{i % 60:02d}.0000 INFO filler line {i}\n" for i in range(400)]
    log.write_text("".join(lines), encoding="utf-8")
    size = log.stat().st_size

    fresh = create_live_state_after_rewind(log, max_backfill_bytes=1024)
    assert fresh is not None
    assert 0 < fresh.byte_offset < size
    with open(log, "rb") as handle:
        handle.seek(fresh.byte_offset - 1)
        assert handle.read(1) == b"\n"


def test_roulette_error_30_trial_displayed(tmp_path: Path) -> None:
    """ERROR N screen: Godot closes; <TRIAL … DISPLAYED> is CRITICAL with catalog text."""
    from roulette_errors import reload_roulette_error_catalog

    reload_roulette_error_catalog()
    reload_known_issues()
    log = tmp_path / "ruleta Roulette" / "2026-07-23.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        '2026-07-23T13:02:56.232+00:00 ERRO [:] Trial expired!!!\n'
        '2026-07-23T13:05:47.232+00:00 INFO [:] '
        '<TRIAL error="30" type="DISPLAYED"> 15723436263159843451 </TRIAL>\n'
        '2026-07-23T13:13:52.842+00:00 INFO [:] '
        '<TRIAL error="30" type="SUCCEEDED"> 00724825049383501916 </TRIAL>\n',
        encoding="utf-8",
    )
    result = parse_log_file(log)
    # SUCCEEDED must not create an incident; expired + DISPLAYED do.
    assert len(result.incidents) == 2
    for inc in result.incidents:
        assert inc.severity == "CRITICAL"
        assert inc.error_type == "Roulette ERROR 30"
        assert inc.game == "Roulette"
        assert "Program locks on a predefined date and hour" in (inc.probable_cause or "")
        assert "Godot UI closes" in (inc.probable_cause or "")
        assert "persistent" in (inc.probable_cause or "")
    match = match_known_issue(result.incidents[1].line_snippet, "CRITICAL")
    assert match is not None
    assert match.issue_id == "GCI-ROULETTE-007"


def test_roulette_error_99_dongle_mismatch(tmp_path: Path) -> None:
    from roulette_errors import reload_roulette_error_catalog

    reload_roulette_error_catalog()
    reload_known_issues()
    log = tmp_path / "ruleta Roulette" / "2026-08-10.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        "2026-08-10T12:11:34.564+02:00 ERRO [:] Dongle mismatch!!!\n"
        "2026-08-10T12:11:34.601+02:00 INFO [:] "
        '<TRIAL error="99" type="DISPLAYED"> 16940973716442060005 </TRIAL>\n',
        encoding="utf-8",
    )
    result = parse_log_file(log)
    displayed = [i for i in result.incidents if i.error_type == "Roulette ERROR 99"]
    assert displayed
    assert "Dongle" in (displayed[0].probable_cause or "")


def test_roulette_error_screen_not_on_slot_logs(tmp_path: Path) -> None:
    """Same TRIAL line under SlotLog must not become a Roulette ERROR incident."""
    from roulette_errors import reload_roulette_error_catalog

    reload_roulette_error_catalog()
    log = tmp_path / "SlotLog" / "2026-07-23.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        '2026-07-23T13:05:47.232+00:00 INFO [:] '
        '<TRIAL error="30" type="DISPLAYED"> 15723436263159843451 </TRIAL>\n',
        encoding="utf-8",
    )
    result = parse_log_file(log)
    assert result.incidents == []


def test_roulette_error_12_catalog_description() -> None:
    from roulette_errors import format_roulette_error, reload_roulette_error_catalog

    reload_roulette_error_catalog()
    info = format_roulette_error(12)
    assert info.error_type == "Roulette ERROR 12"
    assert "maximum number of turns" in info.title.lower() or "maximum number of turns" in info.probable_cause
    assert "sensors" in info.probable_cause.lower()


def test_roulette_error_68_ram_clear_solution() -> None:
    from roulette_errors import format_roulette_error, reload_roulette_error_catalog

    reload_roulette_error_catalog()
    info = format_roulette_error(68)
    assert "Finances mismatch" in info.probable_cause
    assert "RAM clear" in info.probable_cause
