"""Indefinite gameplay-only classic Martingale (cloth clicks + START).

No random UI / admin / closed-phase menu spam.

  python -m automation.run_martingale_loop --ip 10.0.0.90
  python -m automation.run_martingale_loop --rounds 100 --market RED --base-unit 1
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from automation.roulette_middleware import fetch_player_state
from automation.roulette_random_bot import dismiss_stuck_overlays
from automation.roulette_runner import run_roulette_random, write_roulette_results_jsonl

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gameplay-only Martingale loop")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--market", default="RED", help="Even-money market spot")
    p.add_argument("--base-unit", type=int, default=1)
    p.add_argument(
        "--chunk-rounds",
        type=int,
        default=25,
        help="Rounds per run_roulette_random call before looping",
    )
    p.add_argument(
        "--rounds",
        type=int,
        default=0,
        help="Total rounds (0 = forever)",
    )
    p.add_argument("--layout", default="layout2", help="Skin for overlay dismiss")
    args = p.parse_args(argv)

    out_dir = REPO / "_tmp_logs" / "martingale_loop"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = out_dir / f"martingale_{stamp}.jsonl"
    print(
        f"[{time.strftime('%H:%M:%S')}] Martingale gameplay start "
        f"ip={args.ip} market={args.market} base={args.base_unit} "
        f"-> {log_path}",
        flush=True,
    )

    try:
        dismiss_stuck_overlays(
            ip=args.ip, layout_id=args.layout, progress=print
        )
    except Exception as e:  # noqa: BLE001
        print(f"dismiss warn: {e}", flush=True)

    done = 0
    while True:
        if args.rounds > 0 and done >= args.rounds:
            break
        chunk = args.chunk_rounds
        if args.rounds > 0:
            chunk = min(chunk, args.rounds - done)

        creds = fetch_player_state(args.ip).get("credits")
        print(
            f"[{time.strftime('%H:%M:%S')}] chunk rounds={chunk} "
            f"done={done} credits={creds}",
            flush=True,
        )
        results = run_roulette_random(
            ip=args.ip,
            rounds=chunk,
            strategy_id="martingale",
            market=args.market,
            base_unit=args.base_unit,
            include_outside=True,
            progress=print,
        )
        write_roulette_results_jsonl(results, log_path)
        # Append mode: write_roulette truncates; re-append all done so far via separate index
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
        print(
            f"[{time.strftime('%H:%M:%S')}] chunk done ok={ok_n}/{len(results)} "
            f"total_rounds={done}",
            flush=True,
        )
        if args.rounds > 0 and done >= args.rounds:
            break

    print(f"finished rounds={done} log={log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
