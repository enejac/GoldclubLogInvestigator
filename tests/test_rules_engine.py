"""Custom rules persistence and parser integration."""

from __future__ import annotations

import json
import uuid

import pytest

import parser as parser_mod
from rules_engine import CustomRule, RulesManager


def test_rules_manager_roundtrip(tmp_path) -> None:
    path = tmp_path / "custom_rules.json"
    mgr = RulesManager(path)
    assert mgr.get_active_rules() == []
    rid = str(uuid.uuid4())
    rules = [
        CustomRule(
            id=rid,
            name="Test",
            regex_pattern=r"foo\d+",
            severity="WARN",
            error_type="MyCategory",
        )
    ]
    mgr.save_rules(rules)
    mgr2 = RulesManager(path)
    loaded = mgr2.get_active_rules()
    assert len(loaded) == 1
    assert loaded[0].id == rid
    assert loaded[0].regex_pattern == r"foo\d+"
    assert loaded[0].normalized_severity() == "WARN"
    assert list(mgr2.iter_compiled())[0][1].pattern == r"foo\d+"


def test_rules_manager_skips_invalid_regex(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "bad",
                        "regex_pattern": "(",
                        "severity": "INFO",
                        "error_type": "x",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    mgr = RulesManager(path)
    assert list(mgr.iter_compiled()) == []


def test_custom_rule_match_before_builtin(monkeypatch, tmp_path) -> None:
    path = tmp_path / "r.json"
    mgr = RulesManager(path)
    mgr.save_rules(
        [
            CustomRule(
                id=str(uuid.uuid4()),
                name="sig",
                regex_pattern=r"ZZZ_UNIQUE_TOKEN_123",
                severity="WARN",
                error_type="CustomType",
            )
        ]
    )
    monkeypatch.setattr(parser_mod, "get_rules_manager", lambda: mgr)
    hit = parser_mod._try_custom_rule_match("prefix ZZZ_UNIQUE_TOKEN_123 suffix")
    assert hit == ("CustomType", "WARN")
