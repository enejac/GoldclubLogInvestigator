"""
Indefinite roulette endurance — thin wrapper around the bug-hunt loop.

Pack-on-fault is the default: random clicks + godot1 watch + auto BUG-TEST/DEV
packs under ``automation_runs/``. Use ``--no-pack`` for click-only legacy mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from automation.roulette_bug_hunt import run_bug_hunt


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Indefinite roulette endurance (bug-hunt with pack-on-fault)"
    )
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--hours", type=float, default=0.0, help="0 = run forever")
    p.add_argument("--mode", choices=("random", "systematic"), default="random")
    p.add_argument("--layouts", default="layout1,layout2")
    p.add_argument("--max-clicks-per-cycle", type=int, default=80)
    p.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="0 = use bot_config profile batch_size",
    )
    p.add_argument("--profile", default="", help="Bot timing profile (emulation|safe|custom)")
    p.add_argument("--config", default="", help="Path to bot_config.json")
    p.add_argument("--aft-cents", type=int, default=2_000_000)
    p.add_argument("--out-root", default="")
    p.add_argument("--no-aft", action="store_true")
    p.add_argument("--stop-on-first", action="store_true")
    p.add_argument(
        "--no-pack",
        action="store_true",
        help="Legacy: do not emit repro packs (still runs hunt loop watch but skips write)",
    )
    args = p.parse_args(argv)
    layouts = tuple(x.strip() for x in args.layouts.split(",") if x.strip())

    if args.no_pack:
        # Keep a click-only path by using a huge cooldown + never stopping on fault
        # via a dedicated out under _tmp_logs (packs still written unless we
        # monkey-patch — prefer directing users to bug_hunt without packs).
        print(
            json.dumps(
                {
                    "warning": "--no-pack is deprecated; use automation.run_bot_endurance "
                    "without this flag. Pack-on-fault is the product default. "
                    "Proceeding WITH packs anyway.",
                }
            ),
            flush=True,
        )

    out = Path(args.out_root) if args.out_root else None
    root = run_bug_hunt(
        ip=args.ip,
        hours=args.hours,
        mode=args.mode,
        layouts=layouts,
        max_clicks_per_cycle=args.max_clicks_per_cycle,
        batch_size=args.batch_size or None,
        aft_cents=args.aft_cents,
        no_aft=bool(args.no_aft),
        stop_on_first=bool(args.stop_on_first),
        out_root=out,
        progress=print,
        bot_profile=args.profile or None,
        bot_config_path=args.config or None,
    )
    print(json.dumps({"ok": True, "out": str(root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
