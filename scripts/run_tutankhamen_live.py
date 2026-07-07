#!/usr/bin/env python3
"""CLI: live Tutankhamen paytable sweep on lab cabinet."""

from __future__ import annotations

import argparse
import sys

from automation.tutankhamen_runner import run_tutankhamen_live_check


def main() -> int:
    p = argparse.ArgumentParser(description="Tutankhamen live paytable automation")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument(
        "--only",
        type=int,
        nargs="*",
        help="Sweep indices only (default: all 0..3). Example: --only 0",
    )
    p.add_argument("--wingraph-5x5", action="store_true", help="Pad WinGraph grid to 5 rows")
    args = p.parse_args()

    print("Ensure: TutankhamenGSBHW open, bet=20 (mult 2), OneHand focused.")
    print("Starting now...")

    results, out_dir = run_tutankhamen_live_check(
        ip=args.ip,
        sweep_indices=args.only,
        wingraph_5x5=args.wingraph_5x5,
    )
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} failure(s). See {out_dir / 'results.jsonl'}")
        return 1
    print(f"\nAll passed. Results: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
