"""
Live Godot↔ruleta middleware helpers (HTTP :8090 on the cabinet).

Action contract: ``docs/action-endpoint-commands.md``

  PUT ``/api/action/{playerId}``  body ``{ "Type": "<Command>", "Data": <payload> }``
  GET  ``/api/data/{playerId}``   ground truth (``PlayerDataBets.Bets``, chip, credits)

19 release commands + optional ``Dallas`` (DEBUG builds only).
``Success: true`` means *accepted/queued* — confirm via :func:`fetch_player_state`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from automation.remote_exec import winrm_run_inline

_CREDIT_RE = re.compile(r'"Credit"\s*:\s*"(?P<c>\d+)"')
_CHIP_ID_RE = re.compile(r'"ChipId"\s*:\s*(?P<id>\d+)')
_CVD_RE = re.compile(r'"CreditValueDenom"\s*:\s*"(?P<v>\d+)"')


def _money_block_cvd(block: dict[str, Any] | None) -> int | None:
    if not isinstance(block, dict):
        return None
    for key in ("CreditValueDenom", "ValueDenom"):
        raw = block.get(key)
        if raw is None or raw == "":
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Command catalog (release + debug)
# ---------------------------------------------------------------------------

RELEASE_ACTIONS: tuple[str, ...] = (
    "PlaceBet",
    "CancelBet",
    "CancelAllBets",
    "CancelLastBet",
    "RepeatBet",
    "SetChip",
    "SetNeighboursPower",
    "ChangeDenom",
    "StartGame",
    "Collect",
    "CallAttendant",
    "Paytable",
    "AdminMenu",
    "MenuCommands",
    "UserLock",
    "Pin",
    "Keyboard",
    "SpanishGameChange",
    "SpanishGameplay",
)

DEBUG_ACTIONS: tuple[str, ...] = ("Dallas",)

ALL_ACTIONS: tuple[str, ...] = RELEASE_ACTIONS + DEBUG_ACTIONS

# PlaceBet named-bet keywords (exact, case-sensitive).
NAMED_BET_KEYWORDS: tuple[str, ...] = (
    "Neighbours",
    "Finales",
    "MaxBet",
    "Series58",
    "Orphans",
    "CloseToZero",
    "CloseToDoubleZero",
    "ZeroSpiel",
)

# Appendix C — AdminMenu lowercase keywords.
ADMIN_MENU_KEYWORDS: tuple[str, ...] = (
    # Named-command
    "emptyhopper",
    "historygame",
    "dropcurrenttokens",
    "hoppercurrenttokens",
    "emptystacker",
    # Field-navigation (sent as a Bet)
    "emptydispenser",
    "historyjackpot",
    "historyerror",
    "historyhandpay",
    "historydispenser",
    "historywat",
    "historybill",
    "historyhopper",
    "historyticket",
    "gameinfo",
    "logs",
    "volumeup",
    "volumedown",
    "kbreset",
    "lock",
    "buycredits",
    "financial",
    "stop",
    "eventviewer",
    "payout",
    "crclist",
    "testmode",
    "bonuslogs",
)

# Appendix D — Pin numpad
PIN_DIGIT_0_TO_9 = tuple(range(10))
PIN_CANCEL_CLEAR = 10
PIN_EXIT = 11
PIN_OK = 12
PIN_KEY_MAX = 13  # sentinel / no-op

# Common MenuCommands imageCodes (Appendix B) — context-dependent in core.
MENU_CODE_OPEN_USER_MENU = 178
MENU_CODE_CANCEL_ALL_BETS = 14
MENU_CODE_START_GAME = 190
MENU_CODE_CHANGE_DENOM = 277
MENU_CODE_CHIP_BASE = 193  # + index
MENU_CODE_NEIGHBOURS_POWER_BASE = 231  # + index
MENU_CODE_PAYTABLE_BASE = 3590  # + id
MENU_CODE_RESET_DENOM = "3570 0:0"


def parse_player_payload(text: str) -> dict[str, Any]:
    """Parse a ``/api/data`` response body into bets / credits / chip id."""
    bets: list[Any] = []
    credits = None
    credit_value_denom = None
    last_win_cvd = None
    current_bet_cvd = None
    win_cvd = None
    chip_id = None

    body = text.lstrip("\ufeff").lstrip("?")
    try:
        payload = json.loads(body)
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("Type") or item.get("$type") or "")
                if "PlayerDataBets" in t or (
                    isinstance(item.get("Bets"), list) and "Bets" in item
                ):
                    bets = list(item.get("Bets") or [])
                if t.endswith("PlayerData") or t == "PlayerData":
                    credits_block = item.get("Credits")
                    if isinstance(credits_block, dict):
                        credit_value_denom = _money_block_cvd(credits_block)
                        c_raw = credits_block.get("Credit") or credits_block.get(
                            "CreditValue"
                        )
                        try:
                            credits = int(c_raw)
                        except (TypeError, ValueError):
                            pass
                if "PlayerDataGame" in t or t == "PlayerDataGame":
                    last_win_cvd = _money_block_cvd(item.get("LastWin"))
                    current_bet_cvd = _money_block_cvd(item.get("CurrentBet"))
                    win_cvd = _money_block_cvd(item.get("Win"))
                if "PlayerDataAdditional" in t and "ChipId" in item:
                    try:
                        chip_id = int(item.get("ChipId"))
                    except (TypeError, ValueError):
                        pass
    except json.JSONDecodeError:
        m = re.search(r'"Bets"\s*:\s*(\[[^\]]*\])', text)
        if m:
            try:
                bets = json.loads(m.group(1))
            except json.JSONDecodeError:
                bets = [] if m.group(1).strip() == "[]" else [{"raw": m.group(1)[:200]}]

    if credits is None:
        cm = _CREDIT_RE.search(text)
        if cm:
            credits = int(cm.group("c"))
    if chip_id is None:
        kim = _CHIP_ID_RE.search(text)
        if kim:
            chip_id = int(kim.group("id"))
    if credit_value_denom is None:
        vm = _CVD_RE.search(text)
        if vm:
            credit_value_denom = int(vm.group("v"))

    return {
        "ok": True,
        "error": "",
        "credits": credits,
        "credit_value_denom": credit_value_denom,
        "last_win_cvd": last_win_cvd,
        "current_bet_cvd": current_bet_cvd,
        "win_cvd": win_cvd,
        "chip_id": chip_id,
        "bets": bets,
        "bets_count": len(bets),
        "raw": text[:4000],
    }


def read_state_and_cancel(ip: str, *, player_id: int = 0, timeout: int = 25) -> dict[str, Any]:
    """
    Read player state and clear the cloth in **one** WinRM round trip.

    Bet verification runs inside a 22 s betting window, so the read and the
    refunding ``CancelAllBets`` must not cost two separate remote calls.
    """
    script = f"""
$ErrorActionPreference = 'Continue'
$data = ''
try {{
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8090/api/data/{player_id}' -UseBasicParsing -TimeoutSec 8
  $data = $r.Content
}} catch {{
  $data = 'ERROR: ' + $_.Exception.Message
}}
try {{
  Invoke-WebRequest -Uri 'http://127.0.0.1:8090/api/action/{player_id}' -Method PUT `
    -Body '{{"Type":"CancelAllBets"}}' -ContentType 'application/json' `
    -UseBasicParsing -TimeoutSec 8 | Out-Null
}} catch {{ }}
Write-Output $data
"""
    res = winrm_run_inline(ip=ip, script=script, timeout=timeout)
    text = (res.stdout or "").strip()
    if res.returncode != 0 or text.startswith("ERROR:"):
        return {
            "ok": False,
            "error": text or res.stderr,
            "credits": None,
            "chip_id": None,
            "bets": [],
            "bets_count": 0,
            "raw": text[:4000],
        }
    return parse_player_payload(text)


def fetch_player_state(ip: str, *, player_id: int = 0, timeout: int = 25) -> dict[str, Any]:
    """
    GET ``http://127.0.0.1:8090/api/data/{player}`` on the cabinet via WinRM.

    Ground truth for cloth bets is ``PlayerDataBets.Bets`` (empty list = no chips on table).
    """
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8090/api/data/{player_id}' -UseBasicParsing -TimeoutSec 8
  $r.Content
}} catch {{
  Write-Output ('ERROR: ' + $_.Exception.Message)
  exit 2
}}
"""
    res = winrm_run_inline(ip=ip, script=script, timeout=timeout)
    text = (res.stdout or "").strip()
    if res.returncode != 0 or text.startswith("ERROR:"):
        return {
            "ok": False,
            "error": text or res.stderr,
            "credits": None,
            "chip_id": None,
            "bets": [],
            "bets_count": 0,
            "raw": text[:4000],
        }
    return parse_player_payload(text)


def put_action(
    ip: str,
    action_type: str,
    data: Any = None,
    *,
    player_id: int = 0,
    timeout: int = 25,
    allow_unknown: bool = False,
) -> dict[str, Any]:
    """
    PUT ``/api/action/{player}`` with ``{Type, Data}``.

    Unknown ``Type`` values are rejected locally unless *allow_unknown* is true
    (middleware would ignore them with a WARN log).
    """
    atype = str(action_type)
    if not allow_unknown and atype not in ALL_ACTIONS:
        return {
            "ok": False,
            "success": False,
            "error": f"unknown action Type={atype!r}; expected one of {ALL_ACTIONS}",
            "raw": "",
        }
    body: dict[str, Any] = {"Type": atype}
    if data is not None:
        body["Data"] = data
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=True)
    ps_body = body_json.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $body = '{ps_body}'
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8090/api/action/{player_id}' -Method PUT -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 8
  Write-Output $r.Content
}} catch {{
  Write-Output ('ERROR: ' + $_.Exception.Message)
  exit 2
}}
"""
    res = winrm_run_inline(ip=ip, script=script, timeout=timeout)
    text = (res.stdout or "").strip()
    if res.returncode != 0 or text.startswith("ERROR:"):
        return {"ok": False, "success": False, "error": text or res.stderr, "raw": text[:2000]}
    success = False
    try:
        parsed = json.loads(text)
        success = bool(parsed.get("Success")) if isinstance(parsed, dict) else False
    except json.JSONDecodeError:
        success = "true" in text.lower()
    return {
        "ok": True,
        "success": success,
        "error": "",
        "raw": text[:2000],
        "type": atype,
        "data": data,
    }


# ---------------------------------------------------------------------------
# Betting
# ---------------------------------------------------------------------------


def place_bet(ip: str, bet_strings: Sequence[str], *, player_id: int = 0) -> dict[str, Any]:
    """``PlaceBet`` — Data = JSON list of bet strings (no amounts)."""
    return put_action(ip, "PlaceBet", [str(s) for s in bet_strings], player_id=player_id)


def cancel_bet(ip: str, bet_strings: Sequence[str], *, player_id: int = 0) -> dict[str, Any]:
    """``CancelBet`` — same grammar as PlaceBet."""
    return put_action(ip, "CancelBet", [str(s) for s in bet_strings], player_id=player_id)


def cancel_all_bets(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``CancelAllBets`` — no Data."""
    return put_action(ip, "CancelAllBets", None, player_id=player_id)


def cancel_last_bet(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``CancelLastBet`` — no Data."""
    return put_action(ip, "CancelLastBet", None, player_id=player_id)


def repeat_bet(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``RepeatBet`` — no Data."""
    return put_action(ip, "RepeatBet", None, player_id=player_id)


# ---------------------------------------------------------------------------
# Chips / denom / neighbours
# ---------------------------------------------------------------------------


def set_chip(ip: str, chip_index: int = 0, *, player_id: int = 0) -> dict[str, Any]:
    """``SetChip`` — Data = 0-based chip index as a string."""
    idx = int(chip_index)
    if idx < 0:
        raise ValueError(f"chip_index must be >= 0, got {idx}")
    return put_action(ip, "SetChip", str(idx), player_id=player_id)


def set_neighbours_power(
    ip: str, power_index: int = 0, *, player_id: int = 0
) -> dict[str, Any]:
    """``SetNeighboursPower`` — Data = 0-based neighbours-power index as a string."""
    idx = int(power_index)
    if idx < 0:
        raise ValueError(f"power_index must be >= 0, got {idx}")
    return put_action(ip, "SetNeighboursPower", str(idx), player_id=player_id)


def change_denom(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``ChangeDenom`` — no Data; cycles denomination list."""
    return put_action(ip, "ChangeDenom", None, player_id=player_id)


# ---------------------------------------------------------------------------
# Game flow
# ---------------------------------------------------------------------------


def start_game(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``StartGame`` — no Data (spin / start)."""
    return put_action(ip, "StartGame", None, player_id=player_id)


def collect(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``Collect`` — no Data."""
    return put_action(ip, "Collect", None, player_id=player_id)


def call_attendant(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``CallAttendant`` — no Data."""
    return put_action(ip, "CallAttendant", None, player_id=player_id)


def paytable(
    ip: str,
    paytable_id: int,
    *,
    numbers: Sequence[str] | None = None,
    clear: bool = False,
    player_id: int = 0,
) -> dict[str, Any]:
    """
    ``Paytable`` — Data = ``"<id>"`` | ``"<id>:paytable"`` | ``"<id>:<numbersJson>"``.

    *paytable_id* must be 0..7. *clear* sends the ``paytable`` sentinel.
    *numbers* is an optional JSON array of number strings (UI caps 5).
    """
    pid = int(paytable_id)
    if pid < 0 or pid >= 8:
        raise ValueError(f"paytable_id must be 0..7, got {pid}")
    if clear:
        data = f"{pid}:paytable"
    elif numbers:
        nums = [str(n) for n in numbers]
        if len(nums) > 5:
            raise ValueError("paytable numbers capped at 5")
        data = f"{pid}:{json.dumps(nums, separators=(',', ':'), ensure_ascii=True)}"
    else:
        data = str(pid)
    return put_action(ip, "Paytable", data, player_id=player_id)


# ---------------------------------------------------------------------------
# Admin / menu / lock
# ---------------------------------------------------------------------------


def admin_menu(ip: str, keyword: str, *, player_id: int = 0) -> dict[str, Any]:
    """``AdminMenu`` — Data = lowercase keyword (Appendix C)."""
    kw = str(keyword).strip().lower()
    if kw not in ADMIN_MENU_KEYWORDS:
        raise ValueError(
            f"unknown AdminMenu keyword {keyword!r}; expected one of {ADMIN_MENU_KEYWORDS}"
        )
    return put_action(ip, "AdminMenu", kw, player_id=player_id)


def menu_commands(ip: str, keycode: str | int, *, player_id: int = 0) -> dict[str, Any]:
    """
    ``MenuCommands`` — Data = raw keycode string (optionally ``\"code arg\"``).

    Forwarded verbatim to ``ruleta.exe``. See Appendix B for common codes.
    """
    code = str(keycode).strip()
    if not code:
        raise ValueError("MenuCommands keycode must be non-empty")
    return put_action(ip, "MenuCommands", code, player_id=player_id)


def user_lock(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``UserLock`` — no Data (throttled 2000 ms server-side)."""
    return put_action(ip, "UserLock", None, player_id=player_id)


# ---------------------------------------------------------------------------
# Direct input
# ---------------------------------------------------------------------------


def pin(ip: str, key_id: int, *, player_id: int = 0) -> dict[str, Any]:
    """``Pin`` — Data = integer string ``\"0\"..\"12\"`` (13 is no-op sentinel)."""
    kid = int(key_id)
    if kid < 0 or kid > 12:
        raise ValueError(f"Pin key_id must be 0..12, got {kid}")
    return put_action(ip, "Pin", str(kid), player_id=player_id)


def keyboard(
    ip: str,
    keys: str | Sequence[str],
    *,
    player_id: int = 0,
) -> dict[str, Any]:
    """
    ``Keyboard`` — Data = comma-separated key names (e.g. ``\"Control,C\"`` or ``\"F11\"``).

    Only ``Control+C`` (stop) and ``F11`` (admin/Dallas) are acted on by the core.
    """
    if isinstance(keys, str):
        data = keys.strip()
    else:
        data = ",".join(str(k).strip() for k in keys if str(k).strip())
    if not data:
        raise ValueError("Keyboard keys must be non-empty")
    return put_action(ip, "Keyboard", data, player_id=player_id)


def keyboard_stop(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """Convenience: ``Keyboard`` ``Control,C`` (stop roulette)."""
    return keyboard(ip, "Control,C", player_id=player_id)


def keyboard_admin(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """Convenience: ``Keyboard`` ``F11`` (open admin / Dallas)."""
    return keyboard(ip, "F11", player_id=player_id)


# ---------------------------------------------------------------------------
# Spanish bonus
# ---------------------------------------------------------------------------


def spanish_game_change(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``SpanishGameChange`` — no Data (ChangeBonusBet)."""
    return put_action(ip, "SpanishGameChange", None, player_id=player_id)


def spanish_gameplay(ip: str, *, player_id: int = 0) -> dict[str, Any]:
    """``SpanishGameplay`` — no Data (PlayBonus)."""
    return put_action(ip, "SpanishGameplay", None, player_id=player_id)


# ---------------------------------------------------------------------------
# Debug only
# ---------------------------------------------------------------------------


def dallas(ip: str, command: str, *, player_id: int = 0) -> dict[str, Any]:
    """
    ``Dallas`` — DEBUG builds only; absent in release/production.

    Data = test command string.
    """
    cmd = str(command)
    if not cmd:
        raise ValueError("Dallas command must be non-empty")
    return put_action(ip, "Dallas", cmd, player_id=player_id)


# ---------------------------------------------------------------------------
# Verify helpers
# ---------------------------------------------------------------------------


def bet_matches_expect(
    bets: list[Any],
    *,
    expect_type: str,
    expect_id: str,
    require_single: bool = True,
) -> bool:
    """Match middleware ``PlayerDataBets`` entry (Fields ≈ straight-up)."""
    if require_single and len(bets) != 1:
        return False
    if not bets:
        return False
    b0 = bets[0] if isinstance(bets[0], dict) else {}
    bt = str(b0.get("BetType") or "")
    bid = str(b0.get("Id") or "")
    type_ok = (
        expect_type.lower() in bt.lower()
        or (expect_type.lower() == "straight" and "fields" in bt.lower())
        or (expect_type.lower() == "fields" and "straight" in bt.lower())
    )
    return type_ok and bid == str(expect_id)


def action_helpers() -> dict[str, Any]:
    """Map action Type → callable for introspection / CLI."""
    return {
        "PlaceBet": place_bet,
        "CancelBet": cancel_bet,
        "CancelAllBets": cancel_all_bets,
        "CancelLastBet": cancel_last_bet,
        "RepeatBet": repeat_bet,
        "SetChip": set_chip,
        "SetNeighboursPower": set_neighbours_power,
        "ChangeDenom": change_denom,
        "StartGame": start_game,
        "Collect": collect,
        "CallAttendant": call_attendant,
        "Paytable": paytable,
        "AdminMenu": admin_menu,
        "MenuCommands": menu_commands,
        "UserLock": user_lock,
        "Pin": pin,
        "Keyboard": keyboard,
        "SpanishGameChange": spanish_game_change,
        "SpanishGameplay": spanish_gameplay,
        "Dallas": dallas,
    }


__all__ = [
    "RELEASE_ACTIONS",
    "DEBUG_ACTIONS",
    "ALL_ACTIONS",
    "NAMED_BET_KEYWORDS",
    "ADMIN_MENU_KEYWORDS",
    "fetch_player_state",
    "parse_player_payload",
    "read_state_and_cancel",
    "put_action",
    "place_bet",
    "cancel_bet",
    "cancel_all_bets",
    "cancel_last_bet",
    "repeat_bet",
    "set_chip",
    "set_neighbours_power",
    "change_denom",
    "start_game",
    "collect",
    "call_attendant",
    "paytable",
    "admin_menu",
    "menu_commands",
    "user_lock",
    "pin",
    "keyboard",
    "keyboard_stop",
    "keyboard_admin",
    "spanish_game_change",
    "spanish_gameplay",
    "dallas",
    "bet_matches_expect",
    "action_helpers",
]
