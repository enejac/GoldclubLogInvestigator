"""Auto-triage rules for probable-cause assignment."""

import parser_rules
import pytest

from parser_rules import TriageRule, apply_triage_rules, format_related_tracking, match_known_issue


def test_apply_triage_rules_matches_rhcache_critical() -> None:
    snippet = (
        "2026-01-01T12:00:00+00:00 ERROR  at Game.RhCache.cs:line 542 in MoveNext"
    )
    out = apply_triage_rules(snippet, "CRITICAL")
    assert out is not None
    assert "UnloadTheme" in out
    assert "texture cache" in out


def test_apply_triage_rules_wrong_severity_returns_none() -> None:
    snippet = "RhCache.cs:line 542"
    assert apply_triage_rules(snippet, "MEDIUM") is None


def test_apply_triage_rules_missing_trigger_returns_none() -> None:
    assert apply_triage_rules("Some other stack trace", "CRITICAL") is None


def test_apply_triage_rules_none_severity_filter_matches_any_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parser_rules,
        "KNOWN_RULES",
        [
            TriageRule(
                name="Test any severity",
                trigger_text="UNIQUE_TRIGGER_XYZ",
                severity_filter=None,
                probable_cause="Generic cause",
            )
        ],
    )
    assert apply_triage_rules("prefix UNIQUE_TRIGGER_XYZ suffix", "LOW") == "Generic cause"
    assert apply_triage_rules("UNIQUE_TRIGGER_XYZ", "CRITICAL") == "Generic cause"


def test_match_known_issue_aft_cashable_bucket_regex() -> None:
    line = "Withdraw successful GCC_ST_20664_01 in 100000(cashable:0); 0(cashable:0)"
    match = match_known_issue(line, "INFO")
    assert match is not None
    assert match.issue_id == "GCI-AFT-001"
    assert "GCI-AFT-001" in match.probable_cause


def test_match_known_issue_amounts_mismatch() -> None:
    line = "AFT REQUESTED AND OBTAINED TRANSFER AMOUNTS MISMATCH"
    match = match_known_issue(line, "WARN")
    assert match is not None
    assert match.issue_id == "GCI-AFT-001"


def test_format_related_tracking_placeholder_jira_keys() -> None:
    match = match_known_issue("TRANSFER AMOUNTS MISMATCH", "INFO")
    assert match is not None
    text = format_related_tracking(match)
    assert "GCI-AFT-001" in text
    assert "TBD-GC-XXXX" in text
    assert "issues/GCI-AFT-001" in text


def test_related_tracking_for_incident_returns_none_when_no_match() -> None:
    from parser_rules import related_tracking_for_incident

    assert related_tracking_for_incident("benign info line", "INFO") is None
