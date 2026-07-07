"""
Lab cabinet credit top-up via AFT (Send-TestAft1000.ps1 / WinDivert inject).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from automation.cabinet_preflight import read_cabinet_balance_credits

# Default promo transfer: $1,000 (100000 cent-units) - matches Send-TestAft1000.ps1.
DEFAULT_AFT_TOP_UP_CREDITS = 100_000


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_aft_credit_transfer(
    ip: str,
    *,
    amount_credits: int = DEFAULT_AFT_TOP_UP_CREDITS,
    promo: bool = True,
    timeout_sec: float = 180.0,
) -> tuple[bool, str]:
    """Inject AFT credits on *ip* using the repo Send-TestAft1000 wrapper."""
    script = _repo_root() / "Send-TestAft1000.ps1"
    if not script.is_file():
        return False, f"missing {script}"

    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Send",
        "-IP",
        ip,
        "-Amount",
        str(int(amount_credits)),
    ]
    if promo:
        args.append("-nr")
    else:
        args.append("-c")

    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "timeout": int(timeout_sec),
        "cwd": str(_repo_root()),
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        r = subprocess.run(args, **run_kw)
    except subprocess.TimeoutExpired:
        return False, f"AFT transfer timed out after {timeout_sec:.0f}s"
    except OSError as e:
        return False, f"AFT transfer failed to start: {e}"

    tail = (r.stdout or "")[-2000:] + (r.stderr or "")[-1000:]
    if r.returncode != 0:
        return False, f"AFT transfer exit {r.returncode}: {tail.strip() or 'no output'}"
    return True, f"AFT transfer sent ({amount_credits} credits)"


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

    When balance is missing or below minimum (including 0), run lab AFT top-up
    then poll until credits land or timeout.
    """
    balance = read_cabinet_balance_credits(ip)
    if balance is not None and balance >= min_credits:
        return True, f"balance ok ({balance} credits)", balance

    need = balance if balance is not None else 0
    ok, xfer_msg = run_aft_credit_transfer(ip, amount_credits=top_up_amount)
    if not ok:
        return False, f"balance {need} < {min_credits}; {xfer_msg}", balance

    landed, after = wait_for_balance_at_least(
        ip,
        min_credits=min_credits,
        timeout_sec=poll_timeout_sec,
    )
    if landed and after is not None:
        return True, f"{xfer_msg}; balance now {after} credits", after
    return (
        False,
        f"{xfer_msg}; balance still below {min_credits} (last={after})",
        after,
    )