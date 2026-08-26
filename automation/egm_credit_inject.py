"""
Credit top-up for lab EGMs: slot (OneHand) vs roulette (ruleta / :8090).

Script paths differ by product:

======= Slot (OneHand / Slot Roulette theme) =======
  AFT     : lab\\Invoke-WinDivertAft.ps1  (or lab\\Send-TestAft1000.ps1)
  Bill    : lab\\roulette\\Invoke-BillInjectRouletteRemote.ps1 -GameKind Slot
            (KeyCtrl :30800 force-inject)
  Dallas  : lab\\Invoke-DallasSpliceRemote.ps1  (:30800 ROM splice; not a cash top-up)

======= Roulette (Godot + middleware :8090) =======
  AFT     : Invoke-WinDivertAftRoulette.ps1  (repo root or lab\\roulette\\)
  Bill    : same BillInject script with -GameKind Auto|Roulette (:30300)
  Dallas  : Invoke-DallasSpliceRouletteRemote.ps1 / lab\\roulette\\...

LogInvestigator GUI must not call these (see cabinet_credit.py). Automation /
lab soak bots may.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]

GameKind = Literal["slot", "roulette", "unknown"]

SLOT_AFT = ROOT / "lab" / "Invoke-WinDivertAft.ps1"
ROULETTE_AFT = ROOT / "Invoke-WinDivertAftRoulette.ps1"
ROULETTE_AFT_LAB = ROOT / "lab" / "roulette" / "Invoke-WinDivertAftRoulette.ps1"
BILL_INJECT = ROOT / "lab" / "roulette" / "Invoke-BillInjectRouletteRemote.ps1"
DALLAS_SLOT = ROOT / "lab" / "Invoke-DallasSpliceRemote.ps1"
DALLAS_ROULETTE = ROOT / "Invoke-DallasSpliceRouletteRemote.ps1"
DALLAS_ROULETTE_LAB = ROOT / "lab" / "roulette" / "Invoke-DallasSpliceRouletteRemote.ps1"

MIN_CREDITS_DEFAULT = 50_000


@dataclass(frozen=True)
class CreditRead:
    kind: GameKind
    credits: int | None
    source: str


def detect_live_egm_kind(ip: str) -> GameKind:
    """Prefer running process (OneHand vs godot*), then install markers."""
    ip = (ip or "").strip()
    if not ip:
        return "unknown"
    try:
        from network.health_monitor import check_game_client_status

        slot = check_game_client_status(ip, kind="slot", allow_psexec=False)
        if slot is not None and slot.running is True:
            return "slot"
        roulette = check_game_client_status(ip, kind="roulette", allow_psexec=False)
        if roulette is not None and roulette.running is True:
            return "roulette"
    except Exception:
        pass
    try:
        from network.goldclub_paths import detect_game_kind_from_scan_root

        detected = detect_game_kind_from_scan_root(rf"\\{ip}\c$\Goldclub\var\log")
        if detected in ("slot", "roulette"):
            return detected  # type: ignore[return-value]
    except Exception:
        pass
    # Middleware up strongly implies roulette product.
    try:
        from automation.roulette_middleware import fetch_player_state

        st = fetch_player_state(ip)
        if st.get("ok") and st.get("credits") is not None:
            return "roulette"
    except Exception:
        pass
    return "unknown"


def read_credits(ip: str, *, kind: GameKind | None = None) -> CreditRead:
    """Read live credits using the meter appropriate for the product."""
    k = kind or detect_live_egm_kind(ip)
    if k in ("slot", "unknown"):
        try:
            from automation.roulette_slot_hotcold_run import read_credit_meter

            c = read_credit_meter(ip)
            if c is not None:
                return CreditRead(kind=k if k != "unknown" else "slot", credits=c, source="device_manager")
        except Exception:
            pass
        try:
            from automation.cabinet_preflight import read_cabinet_balance_credits

            c = read_cabinet_balance_credits(ip)
            if c is not None:
                return CreditRead(kind=k if k != "unknown" else "slot", credits=c, source="slotlog")
        except Exception:
            pass
    if k in ("roulette", "unknown"):
        try:
            from automation.roulette_middleware import fetch_player_state

            st = fetch_player_state(ip)
            if st.get("ok"):
                try:
                    return CreditRead(
                        kind="roulette",
                        credits=int(st.get("credits")),
                        source="middleware_8090",
                    )
                except (TypeError, ValueError):
                    return CreditRead(kind="roulette", credits=None, source="middleware_8090")
        except Exception:
            pass
    return CreditRead(kind=k, credits=None, source="none")


def _run_ps(script: Path, args: list[str], log: Path | None, timeout: float) -> int:
    if not script.is_file():
        raise FileNotFoundError(str(script))
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    if log is None:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return int(proc.returncode)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== {script.name} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===\n")
        fh.write(" ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    return int(proc.returncode)


def inject_aft(
    ip: str,
    *,
    kind: GameKind,
    amount_cents: int,
    transfer_type: Literal["cashable", "restricted", "non-restricted"] = "non-restricted",
    no_auto_bootstrap: bool = True,
    no_auto_wake: bool = False,
    log: Path | None = None,
    timeout: float = 900.0,
) -> tuple[bool, str]:
    """WinDivert AFT path for the given product.

    ``transfer_type`` maps to Invoke-WinDivertAft ``-c`` / ``-r`` / ``-nr`` and
    drives Accounting cashless-in buckets 00A0 / 00A2 / 00A4 (+ 0017 aggregate).
    """
    if kind == "roulette":
        script = ROULETTE_AFT if ROULETTE_AFT.is_file() else ROULETTE_AFT_LAB
    else:
        script = SLOT_AFT
    type_flag = {
        "cashable": "-c",
        "restricted": "-r",
        "non-restricted": "-nr",
    }.get(transfer_type)
    if not type_flag:
        return False, f"aft bad transfer_type={transfer_type!r}"
    args = [
        "-ComputerName",
        ip,
        "-AmountCents",
        str(int(amount_cents)),
        type_flag,
        "-Send",
    ]
    if no_auto_bootstrap:
        args.append("-NoAutoBootstrap")
    if no_auto_wake:
        args.append("-NoAutoWake")
    try:
        code = _run_ps(
            script,
            args,
            log,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"aft {kind} {transfer_type} error: {exc}"
    return code == 0, f"aft {kind} {transfer_type} exit={code} script={script.name}"


def inject_bill(
    ip: str,
    *,
    kind: GameKind,
    credits: int,
    log: Path | None = None,
    timeout: float = 600.0,
) -> tuple[bool, str]:
    """Bill-acceptor KeyCtrl inject (slot :30800 / roulette :30300)."""
    game = "Slot" if kind == "slot" else ("Roulette" if kind == "roulette" else "Auto")
    try:
        code = _run_ps(
            BILL_INJECT,
            [
                "-ComputerName",
                ip,
                "-GameKind",
                game,
                "-Credits",
                str(int(credits)),
                "-Send",
            ],
            log,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"bill {game} error: {exc}"
    return code == 0, f"bill {game} exit={code}"


def inject_dallas(
    ip: str,
    *,
    kind: GameKind,
    mode: str = "inject",
    run_seconds: int = 30,
    log: Path | None = None,
    timeout: float = 180.0,
) -> tuple[bool, str]:
    """Dallas ROM splice (lab only). Slot uses :30800; roulette uses its splice script."""
    if kind == "roulette":
        script = DALLAS_ROULETTE if DALLAS_ROULETTE.is_file() else DALLAS_ROULETTE_LAB
    else:
        script = DALLAS_SLOT
    try:
        code = _run_ps(
            script,
            [
                "-ComputerName",
                ip,
                "-Mode",
                mode,
                "-RunSeconds",
                str(int(run_seconds)),
            ],
            log,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"dallas {kind} error: {exc}"
    return code == 0, f"dallas {kind} mode={mode} exit={code}"


def ensure_credits(
    ip: str,
    *,
    min_credits: int = MIN_CREDITS_DEFAULT,
    aft_cents: int = 2_000_000,
    bill_credits: int | None = None,
    prefer: str = "auto",
    try_dallas: bool = False,
    log: Path | None = None,
    settle_sec: float = 4.0,
) -> tuple[bool, str, CreditRead]:
    """
    Ensure the cabinet has enough credits for automation.

    prefer:
      auto  — slot: bill then AFT; roulette: AFT then bill
      aft   — AFT only
      bill  — bill inject only
    """
    kind = detect_live_egm_kind(ip)
    before = read_credits(ip, kind=kind if kind != "unknown" else None)
    if before.credits is not None and before.credits >= min_credits:
        return True, f"balance ok ({before.credits})", before

    # Resolve effective kind for inject when probe was ambiguous.
    effective: GameKind = kind
    if effective == "unknown":
        effective = "slot" if before.source in ("device_manager", "slotlog") else "roulette"

    bill_amt = int(bill_credits if bill_credits is not None else max(aft_cents, min_credits))
    attempts: list[str] = []

    order: list[str]
    if prefer == "aft":
        order = ["aft"]
    elif prefer == "bill":
        order = ["bill"]
    elif effective == "slot":
        # Slot roulette: BillInject on :30800 is the proven cash path; AFT second.
        order = ["bill", "aft"]
    else:
        order = ["aft", "bill"]

    for method in order:
        if method == "aft":
            ok, msg = inject_aft(ip, kind=effective, amount_cents=aft_cents, log=log)
        else:
            ok, msg = inject_bill(ip, kind=effective, credits=bill_amt, log=log)
        attempts.append(msg)
        time.sleep(settle_sec)
        after = read_credits(ip, kind=effective)
        if after.credits is not None and after.credits >= min_credits:
            return True, "; ".join(attempts) + f"; credits={after.credits}", after

    if try_dallas:
        ok, msg = inject_dallas(ip, kind=effective, log=log)
        attempts.append(msg)
        time.sleep(settle_sec)
        after = read_credits(ip, kind=effective)
        if after.credits is not None and after.credits >= min_credits:
            return True, "; ".join(attempts) + f"; credits={after.credits}", after

    after = read_credits(ip, kind=effective)
    return (
        False,
        "; ".join(attempts) + f"; still credits={after.credits}",
        after,
    )
