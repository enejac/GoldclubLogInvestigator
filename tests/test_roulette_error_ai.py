"""AI Helper Roulette ERROR N catalog answers."""

from ai_helper.agent import format_search_only_answer
from ai_helper.roulette_error_answer import (
    is_roulette_error_question,
    synthesize_roulette_error_answer,
)
from parser_rules import related_tracking_for_incident, reload_known_issues


def test_synthesize_roulette_error_99():
    text = synthesize_roulette_error_answer("What is Roulette ERROR 99?")
    assert text is not None
    assert "Roulette ERROR 99" in text
    assert "GCI-ROULETTE-007" in text


def test_is_roulette_error_question():
    assert is_roulette_error_question("What is Roulette ERROR 12?")
    assert is_roulette_error_question("trial expired error screen")
    assert not is_roulette_error_question("where is AurumSetup.xml")


def test_synthesize_roulette_error_12():
    text = synthesize_roulette_error_answer("Explain Roulette ERROR 12")
    assert text is not None
    assert "Roulette ERROR 12" in text
    assert "maximum number of turns" in text.lower()
    assert "GCI-ROULETTE-007" in text
    assert 'TRIAL error="12"' in text


def test_search_only_answers_error_without_hits():
    out = format_search_only_answer("What is Roulette ERROR 68?", [])
    assert "Finances mismatch" in out
    assert "RAM clear" in out
    assert "GCI-ROULETTE-007" in out


def test_related_tracking_includes_catalog_title():
    reload_known_issues()
    line = (
        '2026-07-23T13:05:47.232+00:00 INFO [:] '
        '<TRIAL error="30" type="DISPLAYED"> 15723436263159843451 </TRIAL>'
    )
    text = related_tracking_for_incident(line, "CRITICAL")
    assert text is not None
    assert "GCI-ROULETTE-007" in text
    assert "Roulette ERROR 30" in text
    assert "Program locks on a predefined date and hour" in text
