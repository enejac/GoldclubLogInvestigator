"""Indefinite aggressive random betting (cloth clicks + single START).

Waits when the cabinet has no/low credits instead of clicking empty rounds.

  python -m automation.run_random_bet_loop --ip 10.0.0.90
  python -m automation.run_random_bet_loop --min-bets 10 --max-bets 18
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from automation.roulette_middleware import fetch_player_state
from automation.roulette_random_bot import dismiss_stuck_overlays
from automation.roulette_runner import run_roulette_random, write_roulette_results_jsonl

REPO = Path(__file__).resolve().parents[1]


def _playable_credits(st: dict[str, Any] | None) -> int | None:
    if not st:
        return None
    for key in ("credits", "credit_value_denom"):
        raw = (st or {}).get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def wait_for_credits(
    ip: str,
    *,
    min_credits: int,
    poll_sec: float = 15.0,
    progress=print,
) -> int:
    """Block until the cabinet reports at least *min_credits*."""
    floor = max(0, int(min_credits))
    while True:
        st = fetch_player_state(ip)
        credits = _playable_credits(st)
        if credits is not None and credits >= floor:
            progress(
                f"[{time.strftime('%H:%M:%S')}] credits ok ({credits} >= {floor})"
            )
            return credits
        progress(
            f"[{time.strftime('%H:%M:%S')}] waiting for credits "
            f"({credits} < {floor}); poll {poll_sec:.0f}s ..."
        )
        time.sleep(poll_sec)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aggressive random bet loop")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--min-bets", type=int, default=10)
    p.add_argument("--max-bets", type=int, default=18)
    p.add_argument("--chunk-rounds", type=int, default=25)
    p.add_argument("--rounds", type=int, default=0, help="0 = forever")
    p.add_argument("--layout", default="layout2")
    p.add_argument("--no-outside", action="store_true")
    p.add_argument(
        "--min-credits",
        type=int,
        default=100,
        help="Pause betting while cabinet credits are below this floor",
    )
    p.add_argument("--credit-poll-sec", type=float, default=15.0)
    args = p.parse_args(argv)

    out_dir = REPO / "_tmp_logs" / "random_bet_loop"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"random_{stamp}.jsonl"
    print(
        f"[{time.strftime('%H:%M:%S')}] Aggressive random betting start "
        f"ip={args.ip} bets={args.min_bets}-{args.max_bets} "
        f"min_credits={args.min_credits} -> {log_path}",
        flush=True,
    )

    try:
        dismiss_stuck_overlays(ip=args.ip, layout_id=args.layout, progress=print)
    except Exception as e:  # noqa: BLE001
        print(f"dismiss warn: {e}", flush=True)

    done = 0
    while True:
        if args.rounds > 0 and done >= args.rounds:
            break

        wait_for_credits(
            args.ip,
            min_credits=args.min_credits,
            poll_sec=args.credit_poll_sec,
            progress=print,
        )

        chunk = args.chunk_rounds
        if args.rounds > 0:
            chunk = min(chunk, args.rounds - done)

        creds = _playable_credits(fetch_player_state(args.ip))
        print(
            f"[{time.strftime('%H:%M:%S')}] chunk rounds={chunk} "
            f"done={done} credits={creds}",
            flush=True,
        )
        results = run_roulette_random(
            ip=args.ip,
            rounds=chunk,
            strategy_id="random",
            min_bets=args.min_bets,
            max_bets=args.max_bets,
            include_outside=not args.no_outside,
            min_credits=args.min_credits,
            progress=print,
        )
        write_roulette_results_jsonl(results, log_path)
        with (out_dir / f"session_{stamp}.jsonl").open("a", encoding="utf-8") as fh:
            for r in results:
                fh.write(
                    json.dumps(
                        {
                            "ok": r.ok,
                            "message": r.message,
                            "round_index": done + r.round_index,
                            "won": r.won,
                            "stake_units": r.stake_units,
                            "credits_before": r.credits_before,
                            "credits_after": r.credits_after,
                            "bets": r.bets,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        done += len(results)
        ok_n = sum(1 for r in results if r.ok)
        stopped_low = any("no/low credits" in (r.message or "") for r in results)
        print(
            f"[{time.strftime('%H:%M:%S')}] chunk done ok={ok_n}/{len(results)} "
            f"total_rounds={done}"
            + (" (paused: low credits)" if stopped_low else ""),
            flush=True,
        )
        if stopped_low:
            time.sleep(max(2.0, float(args.credit_poll_sec) / 3.0))
            continue
        if args.rounds > 0 and done >= args.rounds:
            break

    print(f"finished rounds={done} log={log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
