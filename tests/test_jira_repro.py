"""Unit tests for Jira/Xray recipe loader and soft API clients."""

from __future__ import annotations

from pathlib import Path

import pytest

from automation.xray_recipe import (
    list_recipe_keys,
    load_recipe,
    normalize_issue_key,
    recipe_path_for,
)
from network import jira_client, xray_client


def test_normalize_issue_key_from_url() -> None:
    assert (
        normalize_issue_key("https://winsytemsintl.atlassian.net/browse/RSW-6534")
        == "RSW-6534"
    )
    assert normalize_issue_key("rsw-6534") == "RSW-6534"


def test_normalize_issue_key_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_issue_key("   ")


def test_load_rsw_6534_recipe() -> None:
    recipe = load_recipe("RSW-6534")
    assert recipe["key"] == "RSW-6534"
    assert recipe["jira_bug"] == "RSW-6452"
    assert recipe["layout"] == "layout1"
    ops = [s["op"] for s in recipe["steps"]]
    assert "bill_inject" in ops
    assert "click_bet" in ops
    assert "assert_bets" in ops
    assert "COBRAR" in {
        s.get("id") for s in recipe["steps"] if s.get("op") == "click_ui"
    }
    assert recipe_path_for("RSW-6534") is not None
    assert "RSW-6534" in list_recipe_keys()


def test_jira_fetch_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.setattr(jira_client, "CONFIG_JIRA_BASE", "")
    meta = jira_client.fetch_issue_metadata("RSW-6534")
    assert meta["ok"] is False
    assert "credentials" in meta["error"].lower()


def test_xray_fetch_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XRAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("XRAY_CLIENT_SECRET", raising=False)
    meta = xray_client.fetch_test_steps("RSW-6534")
    assert meta["ok"] is False
    assert "credentials" in meta["error"].lower()


def test_xray_submit_disabled() -> None:
    r = xray_client.submit_test_execution("RSW-6534", status="PASS")
    assert r["ok"] is False
    assert "disabled" in r["error"].lower()


def test_bill_inject_blocked_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    from automation import run_jira_repro

    monkeypatch.setattr(run_jira_repro, "_is_frozen", lambda: True)
    ok, detail = run_jira_repro.run_bill_inject("10.0.0.90", credits=1000)
    assert ok is False
    assert "exe" in detail.lower() or "unavailable" in detail.lower()
