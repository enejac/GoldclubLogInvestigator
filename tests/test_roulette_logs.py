"""Roulette / GoldClub log layout: CRIT blocks, ERRO, subsystem context."""

from pathlib import Path

from parser import (
    LiveFileParseState,
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
