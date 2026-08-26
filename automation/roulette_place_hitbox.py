"""
Place a cloth bet using pixel-perfect hitbox center + middleware verify.

Clear/chip via action API (CancelAllBets / SetChip). Placement is a real
Godot click at the mapped hitbox center — not PlaceBet API — so the chip
appears on the correct visual spot.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_bet_catalog import place_bet_for_button
from automation.roulette_layout import DEFAULT_CHIP, ClickTarget
from automation.roulette_layout_store import load_hitboxes, resolve_hitbox_center, resolve_target
from automation.roulette_middleware import (
    bet_matches_expect,
    cancel_all_bets,
    fetch_player_state,
    set_chip,
)
from automation.roulette_runner import _CLOSED_RE, _latest_file, wait_for_log_marker
from automation.roulette_script import _click


def place_hitbox_bet(
    *,
    ip: str = "10.0.0.90",
    button_id: str = "straightup_red_1",
    chip_index: int = 0,
    wait_open: bool = True,
) -> dict[str, Any]:
    hb = (load_hitboxes().get("buttons") or {}).get(button_id) or {}
    center = resolve_hitbox_center(button_id)
    if center is None:
        raise RuntimeError(f"no hitbox center for {button_id}")
    expect = hb.get("expect") or {"BetType": "Fields", "Id": "1"}
    place_bet = hb.get("place_bet") or place_bet_for_button(button_id) or ["1"]

    if wait_open:
        roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
        if roulette is None:
            raise FileNotFoundError("ruleta Roulette log missing")
        off = roulette.stat().st_size
        off, ok, detail = wait_for_log_marker(
            roulette, start_offset=off, pattern=_CLOSED_RE, timeout_sec=90
        )
        if not ok:
            raise TimeoutError(detail)
        time.sleep(5.5)

    cancel_all_bets(ip)
    time.sleep(0.1)
    set_chip(ip, chip_index)
    time.sleep(0.1)
    chip = resolve_target(DEFAULT_CHIP)
    spot = ClickTarget(button_id, center.x_pct, center.y_pct)

    # Arm chip UI + click proven hitbox center (SendInput window mode).
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    agent = stage_input_agent(ip=ip, local_exe=local)
    script = {
        "defaultKeyDelayMs": 35,
        "steps": [
            _click(chip, ms=100, mode="window", calibrate=False),
            {"type": "sleep", "ms": 90},
            _click(spot, ms=90, mode="window", calibrate=False),
            {"type": "sleep", "ms": 200},
        ],
    }
    ok_run, detail = run_input_script_on_cabinet(
        ip=ip, agent=agent, script=script, focus_process="godot", timeout=60
    )
    if not ok_run:
        return {
            "ok": False,
            "error": detail,
            "button_id": button_id,
            "click": {"x_pct": center.x_pct, "y_pct": center.y_pct},
        }
    time.sleep(0.25)
    st = fetch_player_state(ip)
    ok = bet_matches_expect(
        st.get("bets") or [],
        expect_type=str(expect.get("BetType") or "Fields"),
        expect_id=str(expect.get("Id") or "1"),
        require_single=True,
    )
    return {
        "ok": ok,
        "button_id": button_id,
        "click": {"x_pct": center.x_pct, "y_pct": center.y_pct},
        "place_bet_grammar": place_bet,
        "expect": expect,
        "bets": st.get("bets"),
        "credits": st.get("credits"),
        "hitbox": {
            "x": hb.get("x"),
            "y": hb.get("y"),
            "width": hb.get("width"),
            "height": hb.get("height"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Place bet via mapped hitbox center")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--id", default="straightup_red_1")
    p.add_argument("--chip", type=int, default=0)
    p.add_argument("--no-wait", action="store_true")
    args = p.parse_args(argv)
    result = place_hitbox_bet(
        ip=args.ip,
        button_id=args.id,
        chip_index=args.chip,
        wait_open=not args.no_wait,
    )
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
