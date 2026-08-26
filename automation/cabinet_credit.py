"""
Cabinet credit balance helpers for LogInvestigator automation.

LogInvestigator must NEVER invoke Dallas splice, WinDivert, or AFT inject scripts.
Credit top-up via lab Send-TestAft / Invoke-WinDivertAft is intentionally unavailable
here — use standalone lab tools outside this app if needed.
"""

from __future__ import annotations

import time

from automation.cabinet_preflight import read_cabinet_balance_credits

# Historical default used by lab AFT scripts (documentation only; not injected here).
DEFAULT_AFT_TOP_UP_CREDITS = 100_000

_AFT_DISABLED_MSG = (
    "AFT inject is not available in LogInvestigator "
    "(Dallas/WinDivert/Send-TestAft scripts are excluded from this app)"
)


def run_aft_credit_transfer(
    ip: str,
    *,
    amount_credits: int = DEFAULT_AFT_TOP_UP_CREDITS,
    promo: bool = True,
    timeout_sec: float = 180.0,
) -> tuple[bool, str]:
    """Stub: AFT inject disabled in LogInvestigator."""
    _ = (ip, amount_credits, promo, timeout_sec)
    return False, _AFT_DISABLED_MSG


def wait_for_balance_at_least(
    ip: str,
    *,
    min_credits: int,
    timeout_sec: float = 60.0,
    poll_sec: float = 3.0,
) -> tuple[bool, int | None]:
    deadline = time.monotonic() + timeout_sec
    last: int | None = None
    while time.monotonic() < deadline:
        last = read_cabinet_balance_credits(ip)
        if last is not None and last >= min_credits:
            return True, last
        time.sleep(poll_sec)
    return False, last


def ensure_cabinet_balance(
    ip: str,
    *,
    min_credits: int,
    top_up_amount: int = DEFAULT_AFT_TOP_UP_CREDITS,
    poll_timeout_sec: float = 60.0,
) -> tuple[bool, str, int | None]:
    """
    Ensure SlotLog CreditStatus is at least *min_credits*.

    Does not inject credits. Returns failure if balance is missing or too low.
    """
    _ = (top_up_amount, poll_timeout_sec)
    balance = read_cabinet_balance_credits(ip)
    if balance is not None and balance >= min_credits:
        return True, f"balance ok ({balance} credits)", balance

    need = balance if balance is not None else 0
    return (
        False,
        f"balance {need} < {min_credits}; {_AFT_DISABLED_MSG}",
        balance,
    )