"""Roulette / GoldClub log layout: CRIT blocks, ERRO, subsystem context."""

from pathlib import Path

from parser import (
    LiveFileParseState,
    feed_live_byte_chunk,
    parse_log_file,
    subsystem_label_from_log_path,
)
from parser_rules import match_known_issue, reload_known_issues


def test_subsystem_label_from_folder() -> None:
    assert subsystem_label_from_log_path(Path(r"C:\log\ruleta\2025-11-24.log")) == "Roulette"
    assert subsystem_label_from_log_path(Path(r"C:\log\BiOS\2025-11-24.log")) == "BiOS"
    assert subsystem_label_from_log_path(Path(r"C:\log\GoldClub.Aurum.Services\a.log")) == "Aurum"


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
