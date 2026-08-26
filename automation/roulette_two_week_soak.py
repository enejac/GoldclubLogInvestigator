"""Two-week persistent soak: slot roulette (OneHand) or Godot roulette.

Auto-detects the live product on the cabinet each cycle:

* **slot** — OneHand Slot Roulette: BillInject (:30800) / slot AFT, click bot via
  ``roulette_slot_hotcold_run`` (mapped ``slot`` hitboxes, focus OneHand).
* **roulette** — Godot + :8090: roulette AFT / bill :30300, ``roulette_random_bot``.

  python -m automation.roulette_two_week_soak --ip 10.0.0.90 --until 2026-08-17T07:30:00
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from automation.egm_credit_inject import (
    detect_live_egm_kind,
    ensure_credits as egm_ensure_credits,
    read_credits,
)
from automation.roulette_middleware import cancel_all_bets, set_chip
from automation.roulette_random_bot import (
    MIN_CREDITS_TO_RUN,
    dismiss_stuck_overlays,
    run_random_bot,
)

# Slot Roulette burns ~100 credits/spin (chip_20 x 5). Keep a real float, not the
# roulette bot's bare 100-credit "can place one chip" floor.
SLOT_MIN_CREDITS_TO_RUN = 50_000
from automation.roulette_runner import run_roulette_random, write_roulette_results_jsonl

ROOT = Path(__file__).resolve().parents[1]
# Europe/Ljubljana is CEST (UTC+2) in August — avoid zoneinfo/tzdata dependency.
SI = timezone(timedelta(hours=2))
DEFAULT_UNTIL = "2026-08-17T07:30:00"


def parse_until(text: str) -> datetime:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("until is required")
    if raw.endswith("Z"):
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SI)
    return dt.astimezone(SI)


def seconds_until(deadline: datetime) -> float:
    return (deadline - datetime.now(SI)).total_seconds()


def write_heartbeat(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["heartbeat_si"] = datetime.now(SI).isoformat(timespec="seconds")
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")


def _credits(ip: str) -> int | None:
    return read_credits(ip).credits


def recover_main(ip: str, layout_id: str, kind: str, log) -> None:
    if kind == "slot":
        return
    try:
        dismiss_stuck_overlays(ip=ip, layout_id=layout_id, progress=log)
    except Exception as exc:  # noqa: BLE001
        log(f"dismiss warn: {exc}")
    try:
        cancel_all_bets(ip)
    except Exception as exc:  # noqa: BLE001
        log(f"cancel warn: {exc}")
    try:
        set_chip(ip, 0)
    except Exception as exc:  # noqa: BLE001
        log(f"chip warn: {exc}")


def ensure_credits(
    ip: str,
    *,
    aft_cents: int,
    no_aft: bool,
    aft_log: Path,
    log,
    min_credits: int = MIN_CREDITS_TO_RUN,
) -> bool:
    cred = read_credits(ip)
    if cred.credits is not None and cred.credits >= min_credits:
        return True
    if no_aft:
        log(f"low credits ({cred.credits} via {cred.source}); sleep 120s (no-inject)")
        time.sleep(120)
        return False
    ok, msg, after = egm_ensure_credits(
        ip,
        min_credits=min_credits,
        aft_cents=aft_cents,
        log=aft_log,
        try_dallas=False,
    )
    log(f"credit top-up kind={after.kind} ok={ok} {msg}")
    if not ok:
        log("credit inject failed; sleep 180s")
        time.sleep(180)
    return ok


def run_slot_phase(
    *,
    ip: str,
    out_dir: Path,
    rounds: int,
    log,
    fast: bool = True,
    aggressive: bool = True,
) -> dict[str, Any]:
    """Finite Slot Roulette click phase (OneHand + mapped slot hitboxes)."""
    import automation.roulette_slot_hotcold_run as hc
    from automation.remote_input_agent import build_input_agent_local, stage_input_agent

    out_dir.mkdir(parents=True, exist_ok=True)
    prev_ip, prev_out, prev_fast = hc._IP, hc.OUT, hc.FAST_MODE
    prev_agg = getattr(hc, "AGGRESSIVE_MODE", False)
    hc._IP = ip
    hc.OUT = out_dir
    hc.FAST_MODE = bool(fast) or bool(aggressive)
    hc.AGGRESSIVE_MODE = bool(aggressive)
    try:
        # Stable build dir (not per-cycle) so remote bin-{sha} is reused and
        # Windows\\Temp does not fill with dozens of InputAgent copies.
        agent_build = out_dir.parent / "input_agent_build"
        agent = stage_input_agent(
            ip=ip,
            local_exe=build_input_agent_local(out_dir=agent_build),
        )
        hc.ensure_history_closed(agent, out_dir / "boot_close")
        hc._grab(
            agent,
            out_dir / "boot_clear.png",
            [hc._focus(), hc._click("CANCEL_ALL", 250 if fast else 700)],
            250 if fast else 400,
        )
        history = hc.seed_history(agent)
        strategy = "random" if aggressive else "hotcold"
        poll_s = (
            hc.AGGRESSIVE_METER_POLL_S if aggressive
            else (hc.FAST_METER_POLL_S if fast else hc.METER_POLL_S)
        )
        timeout_s = (
            hc.AGGRESSIVE_METER_TIMEOUT_S if aggressive
            else (hc.FAST_METER_TIMEOUT_S if fast else hc.METER_TIMEOUT_S)
        )
        log(
            f"slot phase seed history_len={len(history)} head={history[:5]} "
            f"fast={hc.FAST_MODE} aggressive={hc.AGGRESSIVE_MODE} strategy={strategy}"
        )
        summary = hc.run_alternate(
            agent=agent,
            history=history,
            rounds=int(rounds),
            poll_s=poll_s,
            timeout_s=timeout_s,
            strategy=strategy,
        )
        return {
            "ok": True,
            "message": (
                f"slot_{strategy} rounds={summary.get('rounds_done')} "
                f"matches={summary.get('matches')} fast={fast} aggressive={aggressive}"
            ),
            "clicked": int(summary.get("rounds_done") or 0) * 6,
            "summary": summary,
        }
    finally:
        hc._IP = prev_ip
        hc.OUT = prev_out
        hc.FAST_MODE = prev_fast
        hc.AGGRESSIVE_MODE = prev_agg


def resume_cycle_number(out_root: Path) -> int:
    """Highest cycle already recorded in session.jsonl (0 if none)."""
    session = out_root / "session.jsonl"
    if not session.is_file():
        return 0
    max_c = 0
    try:
        for line in session.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                max_c = max(max_c, int(json.loads(line).get("cycle") or 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    except OSError:
        return 0
    return max_c


def prune_cycle_screenshots(cycle_dir: Path, log) -> None:
    """Drop PNGs after a cycle so multi-day soak does not fill the disk."""
    if not cycle_dir.is_dir():
        return
    n = 0
    freed = 0
    for png in cycle_dir.rglob("*.png"):
        try:
            freed += png.stat().st_size
            png.unlink()
            n += 1
        except OSError:
            pass
    if n:
        log(f"pruned {n} cycle screenshots ({freed // (1024 * 1024)} MB)")


def run_accounting_check_for_cycle(
    ip: str,
    cycle_dir: Path,
    log,
) -> dict[str, Any]:
    """Share-only Accounting tab (30 meters): gm2au vs SASControler1."""
    from automation.sas_accounting_page_check import run_accounting_page_check

    summary = run_accounting_page_check(
        ip,
        out_dir=cycle_dir / "sas_accounting",
        treat_syncing_as_ok=True,
    )
    brief = {
        "ok": bool(summary.get("ok")),
        "match": int(summary.get("match") or 0),
        "syncing": int(summary.get("syncing") or 0),
        "mismatch": int(summary.get("mismatch") or 0),
        "not_reported": int(summary.get("not_reported") or 0),
        "missing": int(summary.get("missing") or 0),
        "path": str(summary.get("path") or ""),
    }
    log(
        "accounting page: "
        f"ok={brief['ok']} MATCH={brief['match']} SYNCING={brief['syncing']} "
        f"MISMATCH={brief['mismatch']} NOT_REPORTED={brief['not_reported']} "
        f"missing={brief['missing']}"
    )
    return brief


def run_soak(
    *,
    ip: str,
    until: datetime,
    layouts: tuple[str, ...],
    aft_cents: int,
    no_aft: bool,
    profile: str,
    out_root: Path,
    force_kind: str = "auto",
    slot_rounds: int = 80,
    fast: bool = True,
    aggressive: bool = True,
    accounting_check: bool = True,
) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    heartbeat = out_root / "heartbeat.json"
    session = out_root / "session.jsonl"
    aft_log = out_root / "aft.log"
    stop_flag = out_root / "STOP"

    def log(msg: str) -> None:
        line = f"[{datetime.now(SI).strftime('%Y-%m-%d %H:%M:%S %z')}] {msg}"
        print(line, flush=True)
        with (out_root / "soak.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    meta = {
        "started_si": datetime.now(SI).isoformat(timespec="seconds"),
        "until_si": until.isoformat(timespec="seconds"),
        "ip": ip,
        "layouts": list(layouts),
        "profile": profile,
        "aft_cents": aft_cents,
        "no_aft": no_aft,
        "force_kind": force_kind,
        "slot_rounds": slot_rounds,
        "fast": fast,
        "aggressive": aggressive,
        "accounting_check": accounting_check,
    }
    (out_root / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"two-week soak start -> {out_root} until={until.isoformat()}")

    # One local build for the whole soak; remote stage uses source-sha identity.
    try:
        from automation.remote_input_agent import (
            build_input_agent_local,
            input_agent_source_sha12,
            prune_stale_input_agent_bins,
            stage_input_agent,
        )

        build_dir = out_root / "input_agent_build"
        local_exe = build_input_agent_local(out_dir=build_dir)
        staged = stage_input_agent(ip=ip, local_exe=local_exe, prune_stale=True)
        log(
            f"inputagent staged sha={input_agent_source_sha12()} "
            f"remote={staged.remote_dir}"
        )
    except Exception as exc:  # noqa: BLE001
        log(f"inputagent prestage warn: {exc}")
        try:
            prune_stale_input_agent_bins(ip=ip, keep_sha=None)
        except Exception as prune_exc:  # noqa: BLE001
            log(f"inputagent prune warn: {prune_exc}")

    cycle = resume_cycle_number(out_root)
    if cycle:
        log(f"resuming after cycle {cycle}")
    layout_prefer = layouts[-1] if layouts else "layout2"
    while True:
        if stop_flag.is_file():
            log("STOP flag present; exiting")
            break
        remaining = seconds_until(until)
        if remaining <= 0:
            log("deadline reached; exiting")
            break

        cycle += 1
        kind = force_kind if force_kind in ("slot", "roulette") else detect_live_egm_kind(ip)
        if kind == "unknown":
            # Prefer slot when middleware is down (current .90 lab posture).
            kind = "slot"
            log("egm kind unknown — assuming slot (OneHand)")

        if kind == "slot":
            phase = "slot_hotcold"
        elif cycle % 3 == 0:
            phase = "random_bets"
        elif cycle % 3 == 1:
            phase = "systematic"
        else:
            phase = "random_ui"

        cred_now = _credits(ip)
        write_heartbeat(
            heartbeat,
            {
                "cycle": cycle,
                "phase": phase,
                "kind": kind,
                "ip": ip,
                "remaining_sec": int(remaining),
                "until_si": until.isoformat(timespec="seconds"),
                "credits": cred_now,
                "alive": True,
            },
        )

        floor = SLOT_MIN_CREDITS_TO_RUN if kind == "slot" else MIN_CREDITS_TO_RUN
        if not ensure_credits(
            ip,
            aft_cents=aft_cents,
            no_aft=no_aft,
            aft_log=aft_log,
            log=log,
            min_credits=floor,
        ):
            continue

        recover_main(ip, layout_prefer, kind, log)
        t0 = time.time()
        row: dict[str, Any] = {
            "ts": datetime.now(SI).isoformat(timespec="seconds"),
            "cycle": cycle,
            "phase": phase,
            "kind": kind,
            "remaining_sec": int(remaining),
        }
        try:
            cycle_dir = out_root / f"cycle_{cycle:05d}_{phase}"
            if kind == "slot":
                result = run_slot_phase(
                    ip=ip,
                    out_dir=cycle_dir,
                    rounds=slot_rounds,
                    log=log,
                    fast=fast,
                    aggressive=aggressive,
                )
                row.update(
                    ok=bool(result.get("ok")),
                    message=str(result.get("message")),
                    clicked=int(result.get("clicked") or 0),
                    out=str(cycle_dir),
                )
            elif phase == "systematic":
                result = run_random_bot(
                    ip=ip,
                    out_dir=cycle_dir,
                    mode="systematic",
                    layouts=layouts,
                    start_layout=layouts[0],
                    client_ids=("player0",),
                    max_clicks=None,
                    include_unverified=True,
                    wait_betting=True,
                    resume=False,
                    progress=log,
                    bot_profile=profile,
                )
                row.update(
                    ok=bool(result.ok),
                    message=result.message,
                    clicked=int(result.clicked),
                    out=str(cycle_dir),
                )
            elif phase == "random_ui":
                result = run_random_bot(
                    ip=ip,
                    out_dir=cycle_dir,
                    mode="random",
                    layouts=layouts,
                    start_layout=layouts[-1],
                    client_ids=("player0",),
                    max_clicks=180,
                    include_unverified=True,
                    wait_betting=True,
                    resume=False,
                    progress=log,
                    bot_profile=profile,
                )
                row.update(
                    ok=bool(result.ok),
                    message=result.message,
                    clicked=int(result.clicked),
                    out=str(cycle_dir),
                )
            else:
                results = run_roulette_random(
                    ip=ip,
                    rounds=20,
                    strategy_id="random",
                    min_bets=8,
                    max_bets=16,
                    include_outside=True,
                    min_credits=MIN_CREDITS_TO_RUN,
                    progress=log,
                )
                cycle_dir.mkdir(parents=True, exist_ok=True)
                write_roulette_results_jsonl(results, cycle_dir / "rounds.jsonl")
                row.update(
                    ok=any(r.ok for r in results) if results else False,
                    message=(
                        f"random_bets n={len(results)} "
                        f"ok={sum(1 for r in results if r.ok)}"
                    ),
                    clicked=sum(len(r.bets or []) for r in results),
                    out=str(cycle_dir),
                )
        except Exception as exc:  # noqa: BLE001
            row.update(ok=False, message=f"exception: {exc}", clicked=0)
            log(f"phase exception: {exc}")
            traceback.print_exc()
            recover_main(ip, layout_prefer, kind, log)
            time.sleep(10)

        try:
            prune_cycle_screenshots(Path(str(row.get("out") or "")), log)
        except Exception as exc:  # noqa: BLE001
            log(f"prune warn: {exc}")

        if accounting_check:
            try:
                acct_dir = Path(str(row.get("out") or (out_root / f"cycle_{cycle:05d}")))
                row["accounting"] = run_accounting_check_for_cycle(ip, acct_dir, log)
            except Exception as exc:  # noqa: BLE001
                row["accounting"] = {"ok": False, "error": str(exc)}
                log(f"accounting page check failed: {exc}")

        row["elapsed_sec"] = round(time.time() - t0, 1)
        row["credits_after"] = _credits(ip)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        log(f"cycle done {json.dumps(row, ensure_ascii=False)}")
        write_heartbeat(
            heartbeat,
            {
                "cycle": cycle,
                "phase": phase,
                "kind": kind,
                "ip": ip,
                "remaining_sec": int(seconds_until(until)),
                "until_si": until.isoformat(timespec="seconds"),
                "last_row": row,
                "alive": True,
            },
        )
        time.sleep(0.5 if fast else 3.0)

    write_heartbeat(
        heartbeat,
        {
            "cycle": cycle,
            "alive": False,
            "stopped_si": datetime.now(SI).isoformat(timespec="seconds"),
            "until_si": until.isoformat(timespec="seconds"),
            "reason": "deadline_or_stop",
        },
    )
    (out_root / "stopped.json").write_text(
        json.dumps(
            {
                "stopped_si": datetime.now(SI).isoformat(timespec="seconds"),
                "cycles": cycle,
                "until_si": until.isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log("soak finished")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--until", default=DEFAULT_UNTIL, help="SI CEST wall time ISO")
    p.add_argument("--layouts", default="layout1,layout2")
    p.add_argument("--profile", default="fast")
    p.add_argument("--aft-cents", type=int, default=2_000_000)
    p.add_argument("--no-aft", action="store_true")
    p.add_argument("--out-root", default="")
    p.add_argument(
        "--kind",
        default="auto",
        choices=("auto", "slot", "roulette"),
        help="Force product mode (default: detect live OneHand vs godot)",
    )
    p.add_argument(
        "--slot-rounds",
        type=int,
        default=80,
        help="Spins per slot cycle (higher = fewer phase restarts)",
    )
    p.add_argument(
        "--fast",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Max games: bet+SPIN one trip, short settles (default on)",
    )
    p.add_argument(
        "--aggressive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Random numbers every spin, skip hot/cold UI, shortest click delays (default on)",
    )
    p.add_argument(
        "--accounting-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After each cycle, verify all Accounting-tab meters (gm2au vs SASControler1)",
    )
    args = p.parse_args(argv)
    until = parse_until(args.until)
    layouts = tuple(x.strip() for x in args.layouts.split(",") if x.strip())
    stamp = datetime.now(SI).strftime("%Y%m%d_%H%M%S")
    out = Path(args.out_root) if args.out_root else (
        ROOT / "automation_runs" / f"{stamp}_{args.ip.replace(':', '_')}_two_week_soak"
    )
    return run_soak(
        ip=args.ip,
        until=until,
        layouts=layouts,
        aft_cents=args.aft_cents,
        no_aft=bool(args.no_aft),
        profile=args.profile,
        out_root=out,
        force_kind=str(args.kind),
        slot_rounds=int(args.slot_rounds),
        fast=bool(args.fast),
        aggressive=bool(args.aggressive),
        accounting_check=bool(args.accounting_check),
    )


if __name__ == "__main__":
    raise SystemExit(main())
