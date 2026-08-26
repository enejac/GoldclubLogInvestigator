"""Unit tests for middleware action helpers (payload / validation; no cabinet)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from automation import roulette_middleware as mw


class _FakeWinRm:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, ip: str, script: str, timeout: int = 25) -> Any:
        # Extract JSON body from the PowerShell snippet.
        marker = "$body = '"
        start = script.find(marker)
        assert start >= 0
        start += len(marker)
        end = script.find("'", start)
        body = json.loads(script[start:end].replace("''", "'"))
        self.calls.append({"ip": ip, "body": body, "timeout": timeout})

        class R:
            returncode = 0
            stdout = '{"Success": true}'
            stderr = ""

        return R()


def test_release_actions_count() -> None:
    assert len(mw.RELEASE_ACTIONS) == 19
    assert "Dallas" in mw.DEBUG_ACTIONS
    assert len(mw.ALL_ACTIONS) == 20


def test_put_action_rejects_unknown() -> None:
    r = mw.put_action("10.0.0.90", "NotARealAction", None)
    assert r["ok"] is False
    assert "unknown action" in r["error"]


def test_all_no_data_actions() -> None:
    fake = _FakeWinRm()
    with patch.object(mw, "winrm_run_inline", fake):
        mw.cancel_all_bets("10.0.0.90")
        mw.cancel_last_bet("10.0.0.90")
        mw.repeat_bet("10.0.0.90")
        mw.change_denom("10.0.0.90")
        mw.start_game("10.0.0.90")
        mw.collect("10.0.0.90")
        mw.call_attendant("10.0.0.90")
        mw.user_lock("10.0.0.90")
        mw.spanish_game_change("10.0.0.90")
        mw.spanish_gameplay("10.0.0.90")
    types = [c["body"]["Type"] for c in fake.calls]
    assert types == [
        "CancelAllBets",
        "CancelLastBet",
        "RepeatBet",
        "ChangeDenom",
        "StartGame",
        "Collect",
        "CallAttendant",
        "UserLock",
        "SpanishGameChange",
        "SpanishGameplay",
    ]
    assert all("Data" not in c["body"] for c in fake.calls)


def test_place_and_cancel_bet_payload() -> None:
    fake = _FakeWinRm()
    with patch.object(mw, "winrm_run_inline", fake):
        mw.place_bet("10.0.0.90", ["1", "1+2"])
        mw.cancel_bet("10.0.0.90", ["1"])
    assert fake.calls[0]["body"] == {"Type": "PlaceBet", "Data": ["1", "1+2"]}
    assert fake.calls[1]["body"] == {"Type": "CancelBet", "Data": ["1"]}


def test_set_chip_and_neighbours() -> None:
    fake = _FakeWinRm()
    with patch.object(mw, "winrm_run_inline", fake):
        mw.set_chip("10.0.0.90", 2)
        mw.set_neighbours_power("10.0.0.90", 3)
    assert fake.calls[0]["body"] == {"Type": "SetChip", "Data": "2"}
    assert fake.calls[1]["body"] == {"Type": "SetNeighboursPower", "Data": "3"}


def test_paytable_formats() -> None:
    fake = _FakeWinRm()
    with patch.object(mw, "winrm_run_inline", fake):
        mw.paytable("10.0.0.90", 0)
        mw.paytable("10.0.0.90", 1, numbers=["7", "11"])
        mw.paytable("10.0.0.90", 2, clear=True)
    assert fake.calls[0]["body"]["Data"] == "0"
    assert fake.calls[1]["body"]["Data"] == '1:["7","11"]'
    assert fake.calls[2]["body"]["Data"] == "2:paytable"
    with pytest.raises(ValueError):
        mw.paytable("10.0.0.90", 8)


def test_admin_menu_and_pin_and_keyboard() -> None:
    fake = _FakeWinRm()
    with patch.object(mw, "winrm_run_inline", fake):
        mw.admin_menu("10.0.0.90", "buycredits")
        mw.menu_commands("10.0.0.90", 178)
        mw.pin("10.0.0.90", 12)
        mw.keyboard("10.0.0.90", ["Control", "C"])
        mw.keyboard_admin("10.0.0.90")
        mw.dallas("10.0.0.90", "ping")
    bodies = [c["body"] for c in fake.calls]
    assert bodies[0] == {"Type": "AdminMenu", "Data": "buycredits"}
    assert bodies[1] == {"Type": "MenuCommands", "Data": "178"}
    assert bodies[2] == {"Type": "Pin", "Data": "12"}
    assert bodies[3] == {"Type": "Keyboard", "Data": "Control,C"}
    assert bodies[4] == {"Type": "Keyboard", "Data": "F11"}
    assert bodies[5] == {"Type": "Dallas", "Data": "ping"}
    with pytest.raises(ValueError):
        mw.admin_menu("10.0.0.90", "not_a_keyword")
    with pytest.raises(ValueError):
        mw.pin("10.0.0.90", 13)


def test_action_helpers_cover_all() -> None:
    helpers = mw.action_helpers()
    assert set(helpers) == set(mw.ALL_ACTIONS)
