"""Run each roulette strategy for exactly one full cycle (1 open window + spin).

Prefer the fast middleware path for smoke tests:

  python -m automation.run_strategy_cycles_fast --mode place   # ~30s all strategies
  python -m automation.run_strategy_cycles_fast --mode spin    # API place+StartGame, no UI

This module keeps the slower InputAgent / pixel-click path for UI fidelity.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from automation.roulette_middleware import fetch_player_state
from automation.roulette_runner import run_roulette_random, write_roulette_results_jsonl
from automation.roulette_strategies import STRATEGIES

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument(
        "--strategies",
        default="",
        help="Comma list; default = all except full_board (too long for 1 cycle test)",
    )
    p.add_argument("--include-full-board", action="store_true")
    p.add_argument(
        "--fast",
        action="store_true",
        help="Delegate to run_strategy_cycles_fast --mode place (middleware, no UI)",
    )
    args = p.parse_args(argv)

    if args.fast:
        from automation.run_strategy_cycles_fast import main as fast_main

        extra: list[str] = ["--ip", args.ip, "--mode", "place"]
        if args.strategies:
            extra += ["--strategies", args.strategies]
        if args.include_full_board:
            extra.append("--include-full-board")
        return fast_main(extra)

    st = fetch_player_state(args.ip)
    if int(st.get("credits") or 0) < 50000:
        raise SystemExit(f"need credits; have {st.get('credits')}")

    ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if not ids:
        ids = [
            s.id
            for s in STRATEGIES
            if args.include_full_board or s.id != "full_board"
        ]

    out_dir = REPO / "_tmp_logs" / "strategy_cycles"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    for sid in ids:
        print(f"=== strategy {sid} (1 cycle) ===", flush=True)
        before = fetch_player_state(args.ip)
        results = run_roulette_random(
            ip=args.ip,
            rounds=1,
            strategy_id=sid,
            market="RED",
        )
        after = fetch_player_state(args.ip)
        path = out_dir / f"{sid}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        write_roulette_results_jsonl(results, path)
        row = {
            "strategy": sid,
            "ok": bool(results and results[0].ok),
            "message": results[0].message if results else "no result",
            "stake_units": results[0].stake_units if results else None,
            "won": results[0].won if results else None,
            "credits_before": before.get("credits"),
            "credits_after": after.get("credits"),
            "path": str(path),
        }
        summary.append(row)
        print(json.dumps(row, indent=2), flush=True)

    summary_path = out_dir / f"summary_{time.strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {summary_path}", flush=True)
    return 0 if all(r.get("ok") for r in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
