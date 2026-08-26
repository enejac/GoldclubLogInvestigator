"""CLI: invoke any middleware /action command on a lab cabinet.

Examples:
  python -m automation.roulette_action_cli --ip 10.0.0.90 list
  python -m automation.roulette_action_cli --ip 10.0.0.90 CancelAllBets
  python -m automation.roulette_action_cli --ip 10.0.0.90 SetChip --data 0
  python -m automation.roulette_action_cli --ip 10.0.0.90 PlaceBet --data '["1"]'
  python -m automation.roulette_action_cli --ip 10.0.0.90 Paytable --data 0
  python -m automation.roulette_action_cli --ip 10.0.0.90 Keyboard --data Control,C
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from automation.roulette_middleware import (
    ALL_ACTIONS,
    RELEASE_ACTIONS,
    fetch_player_state,
    put_action,
)


def _parse_data(raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    # Prefer JSON (arrays / objects / quoted strings / numbers as JSON)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PUT /api/action/{player} on lab cabinet")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--player", type=int, default=0)
    p.add_argument("action", nargs="?", default="list", help="Type or 'list' / 'state'")
    p.add_argument(
        "--data",
        default=None,
        help='Data payload: JSON (e.g. \'["1"]\') or raw string (e.g. Control,C)',
    )
    p.add_argument(
        "--allow-unknown",
        action="store_true",
        help="Do not reject Types outside the known 19+Dallas set",
    )
    args = p.parse_args(argv)

    if args.action == "list":
        print(json.dumps({"release": list(RELEASE_ACTIONS), "all": list(ALL_ACTIONS)}, indent=2))
        return 0
    if args.action == "state":
        print(json.dumps(fetch_player_state(args.ip, player_id=args.player), indent=2, default=str)[:4000])
        return 0

    data = _parse_data(args.data)
    result = put_action(
        args.ip,
        args.action,
        data,
        player_id=args.player,
        allow_unknown=args.allow_unknown,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") and result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
