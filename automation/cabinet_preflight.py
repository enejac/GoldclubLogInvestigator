"""
Cabinet preflight: multigamer game select + balance checks via SlotLog.

Balance is read from SlotLog ``CreditStatus:`` on ``GAME ENDED`` lines (matches the
multigamer footer balance, e.g. 588845 credits). Game-selector state uses
``TextGameSelect`` add/remove markers.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from automation.tutankhamen_paytable import THEME_ID, TOTAL_BET_CREDITS

ONEHAND_WINDOW_TITLE = "OneHand"

# Multigamer TOP row — 4 cols @ 1920x1080 (gameselector_GSC_config margins).
# Col-1 center ≈ (120 + 1.5×420) / 1920 = 39.1%; row center ≈ (100 + 410) / 1080 = 47.2%.
MULTIGAMER_TUTANKHAMEN_TILE_PCT = (39.0, 47.0)

_CREDIT_STATUS_RE = re.compile(r"CreditStatus:\s*(\d+)")
_AURUM_BUCKET_RE = re.compile(
    r"Aurum (cashable|promo|nonCash) credit state (?:increased|decreased) to (\d+)",
    re.IGNORECASE,
)
_AURUM_END_RE = re.compile(
    r"Aurum end data.*\[(\d+) \(cashable:(\d+) nonCash:(\d+) promo:(\d+)\)\]"
)
_TEXT_GAME_SELECT_ADDED_RE = re.compile(
    r"Information item added:.*TextGameSelect.*Select A Game"
)
_TEXT_GAME_SELECT_REMOVED_RE = re.compile(
    r"Information item removed:.*TextGameSelect.*Select A Game"
)
_THEME_LOAD_RE = re.compile(
    rf"LoadTheme take:.*ThemeName\s*\[\s*{re.escape(THEME_ID)}\s*\]",
    re.IGNORECASE,
)
_GAME_ACTIVE_THEME_RE = re.compile(
    rf"GAME STARTED.*\({re.escape(THEME_ID)}\)",
    re.IGNORECASE,
)


def _latest_slotlog(ip: str) -> Path:
    slotlog_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\SlotLog")
    log_files = [p for p in slotlog_dir.iterdir() if p.is_file()]
    if not log_files:
        raise FileNotFoundError(f"No SlotLog files under {slotlog_dir}")
    return max(log_files, key=lambda p: p.stat().st_mtime)


def _tail_lines(path: Path, *, max_lines: int = 400) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


def read_cabinet_balance_credits(ip: str) -> int | None:
    """
    Best-effort balance from SlotLog tail.

    Prefer ``CreditStatus`` on ``GAME ENDED``; fall back to Aurum end-data totals
    or the latest per-bucket credit state (cashable + nonCash + promo).
    """
    tail = _tail_lines(_latest_slotlog(ip))
    credit_status: int | None = None
    end_total: int | None = None
    buckets: dict[str, int] = {}

    for line in tail:
        m = _CREDIT_STATUS_RE.search(line)
        if m:
            credit_status = int(m.group(1))
        m = _AURUM_END_RE.search(line)
        if m:
            end_total = int(m.group(1))
        m = _AURUM_BUCKET_RE.search(line)
        if m:
            buckets[m.group(1).lower()] = int(m.group(2))

    if credit_status is not None:
        return credit_status
    if end_total is not None:
        return end_total
    if buckets:
        return sum(buckets.values())
    return None


def check_cabinet_balance(
    ip: str,
    *,
    min_credits: int = TOTAL_BET_CREDITS,
) -> tuple[bool, str, int | None]:
    balance = read_cabinet_balance_credits(ip)
    if balance is None:
        return False, "cannot read balance from SlotLog (no CreditStatus line)", None
    if balance < min_credits:
        return False, f"insufficient credits: balance={balance} need>={min_credits}", balance
    return True, f"balance ok ({balance} credits)", balance


def cabinet_on_game_selector(ip: str) -> bool:
    """True when ``TextGameSelect`` was added and not yet removed in recent SlotLog."""
    tail = _tail_lines(_latest_slotlog(ip))
    last_add = -1
    last_remove = -1
    for i, line in enumerate(tail):
        if _TEXT_GAME_SELECT_ADDED_RE.search(line):
            last_add = i
        if _TEXT_GAME_SELECT_REMOVED_RE.search(line):
            last_remove = i
    return last_add >= 0 and last_add > last_remove


def cabinet_has_tutankhamen_loaded(ip: str) -> bool:
    tail = _tail_lines(_latest_slotlog(ip))
    if cabinet_on_game_selector(ip):
        return False
    last_theme = -1
    last_select = -1
    last_active = -1
    for i, line in enumerate(tail):
        if _TEXT_GAME_SELECT_ADDED_RE.search(line):
            last_select = i
        if _THEME_LOAD_RE.search(line):
            last_theme = i
        if _GAME_ACTIVE_THEME_RE.search(line):
            last_active = i
    if last_active >= 0 and last_active > last_select:
        return True
    return last_theme >= 0 and last_theme > last_select


def cabinet_preflight_status(ip: str) -> tuple[str, str]:
    """
    Return (state, detail) where state is one of:
    selector | in_tutankhamen | other_game | unknown
    """
    if cabinet_on_game_selector(ip):
        return "selector", "multigamer game selector (TextGameSelect active)"
    if cabinet_has_tutankhamen_loaded(ip):
        return "in_tutankhamen", f"{THEME_ID} loaded"
    tail = _tail_lines(_latest_slotlog(ip))
    for line in reversed(tail):
        if "LoadTheme take:" in line and "ThemeName" in line:
            return "other_game", line.strip()
    return "unknown", "could not determine theme from SlotLog tail"


def onehand_click_target(x_pct: float, y_pct: float) -> str:
    return f"{ONEHAND_WINDOW_TITLE}@{x_pct:.1f},{y_pct:.1f}"


def tutankhamen_tile_click_target() -> str:
    x, y = MULTIGAMER_TUTANKHAMEN_TILE_PCT
    return onehand_click_target(x, y)


def wait_for_tutankhamen_loaded(
    ip: str,
    *,
    timeout_sec: float = 20.0,
    poll_sec: float = 0.5,
) -> tuple[bool, str]:
    """Poll SlotLog until TutankhamenGSBHW load is seen after game-selector dismiss."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if cabinet_has_tutankhamen_loaded(ip):
            return True, f"{THEME_ID} loaded"
        time.sleep(poll_sec)
    state, detail = cabinet_preflight_status(ip)
    return False, f"timeout waiting for {THEME_ID} (state={state}: {detail})"


def script_for_multigamer_select_tutankhamen() -> dict:
    """
    Click Tutankhamen on multigamer TOP row (2nd tile) via OneHand client-area PostMessage.

    Never sends E or ESC.
    """
    tile = tutankhamen_tile_click_target()
    steps: list[dict] = [
        {"type": "focus_process", "value": ONEHAND_WINDOW_TITLE, "ms": 400},
        {"type": "click_window", "value": tile, "ms": 250},
        {"type": "sleep", "ms": 500},
        {"type": "focus_process", "value": ONEHAND_WINDOW_TITLE, "ms": 200},
    ]
    return {"defaultKeyDelayMs": 35, "steps": steps}


def merge_input_scripts(*scripts: dict) -> dict:
    """Concatenate step lists; first script's defaultKeyDelayMs wins."""
    if not scripts:
        return {"defaultKeyDelayMs": 35, "steps": []}
    steps: list[dict] = []
    delay = 35
    for script in scripts:
        if script.get("defaultKeyDelayMs"):
            delay = int(script["defaultKeyDelayMs"])
        steps.extend(script.get("steps") or [])
    return {"defaultKeyDelayMs": delay, "steps": steps}
