"""
10 rounds per strategy — report correct cloth clicks and live-verify PlaceBet.

Plans (including progression stakes) are computed first with simulated W/L so
clicks are known up front; cabinet verifies each plan via middleware in batches
that fit an open window.

  python -m automation.run_strategy_rounds_report --rounds 10
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from automation.remote_exec import winrm_run_inline
from automation.roulette_layout import CHIP_SPOTS, NUMBER_SPOTS
from automation.roulette_middleware import fetch_player_state
from automation.roulette_strategies import (
    CHIP_VALUES,
    STRATEGIES,
    StrategyState,
    apply_result,
    decompose_units,
    plan_for_strategy,
)
from automation.run_strategy_cycles_fast import _wait_open, spot_to_place_bet

REPO = Path(__file__).resolve().parents[1]
CHIP_INDEX = {c.name: i for i, c in enumerate(CHIP_SPOTS)}


def clicks_for_plan(plan: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_chip: str | None = None
    for placement in plan.placements:
        if placement.units <= 0:
            continue
        spot = placement.spot
        bet = spot_to_place_bet(spot.name)
        for chip, count in decompose_units(placement.units):
            if chip.name != last_chip:
                out.append(
                    {
                        "action": "select_chip",
                        "name": chip.name,
                        "x_pct": round(chip.x_pct, 2),
                        "y_pct": round(chip.y_pct, 2),
                        "chip_index": CHIP_INDEX.get(chip.name),
                        "chip_units": CHIP_VALUES.get(chip.name),
                    }
                )
                last_chip = chip.name
            for _ in range(count):
                out.append(
                    {
                        "action": "place",
                        "name": spot.name,
                        "x_pct": round(spot.x_pct, 2),
                        "y_pct": round(spot.y_pct, 2),
                        "units": CHIP_VALUES.get(chip.name, 1),
                        "place_bet": bet,
                    }
                )
    return out


def actions_for_plan(plan: Any) -> tuple[list[dict[str, Any]], str]:
    actions: list[dict[str, Any]] = [{"Type": "CancelAllBets"}]
    last_chip_idx: int | None = None
    for placement in plan.placements:
        bet = spot_to_place_bet(placement.spot.name)
        if not bet:
            return [], f"no PlaceBet mapping for {placement.spot.name!r}"
        if placement.units <= 0:
            continue
        for chip, count in decompose_units(placement.units):
            idx = CHIP_INDEX.get(chip.name)
            if idx is None:
                return [], f"unknown chip {chip.name}"
            if last_chip_idx != idx:
                actions.append({"Type": "SetChip", "Data": str(idx)})
                last_chip_idx = idx
            actions.append({"Type": "PlaceBet", "Data": [bet] * int(count)})
    return actions, ""


def build_plans(
    strategy_ids: list[str],
    *,
    rounds: int,
    market: str,
    base_unit: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Precompute all rounds with simulated W/L for progression."""
    from automation.roulette_strategies import Placement, StrategyBetPlan

    jobs: list[dict[str, Any]] = []
    for sid in strategy_ids:
        state = StrategyState()
        for r in range(1, rounds + 1):
            if sid == "random":
                n = rng.randint(1, 36)
                plan = StrategyBetPlan(
                    [Placement(NUMBER_SPOTS[n], max(1, base_unit))],
                    max(1, base_unit),
                    "random",
                    f"random straight {n}",
                )
            else:
                plan = plan_for_strategy(
                    sid, state, base_unit=base_unit, market=market
                )
            clicks = clicks_for_plan(plan)
            actions, err = actions_for_plan(plan)
            won = None
            if sid not in ("random", "full_board"):
                won = rng.random() < 0.5
                apply_result(
                    sid,
                    state,
                    won=bool(won),
                    base_unit=base_unit,
                    stake_units=plan.stake_units,
                )
            jobs.append(
                {
                    "strategy": sid,
                    "round": r,
                    "stake_units": plan.stake_units,
                    "notes": plan.notes,
                    "clicks": clicks,
                    "actions": actions,
                    "error": err,
                    "sim_won": won,
                }
            )
    return jobs


def verify_jobs_on_cabinet(
    ip: str,
    jobs: list[dict[str, Any]],
    *,
    chunk_size: int = 8,
    player_id: int = 0,
) -> list[dict[str, Any]]:
    """Live PlaceBet verify in open-window chunks; attach bets_landed/ok."""
    results: list[dict[str, Any]] = []
    for i in range(0, len(jobs), chunk_size):
        chunk = jobs[i : i + chunk_size]
        open_ok, detail = _wait_open(ip, timeout=90.0)
        if not open_ok:
            for job in chunk:
                row = dict(job)
                row["ok"] = False
                row["bets_landed"] = 0
                row["verify_error"] = f"no open window: {detail}"
                results.append(row)
            continue

        remote = [
            {
                "strategy": j["strategy"],
                "round": j["round"],
                "error": j["error"],
                "actions": j["actions"] if not j["error"] else [],
            }
            for j in chunk
        ]
        jobs_json = json.dumps(remote, separators=(",", ":"), ensure_ascii=True).replace(
            "'", "''"
        )
        script = f"""
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8090/api'
$playerId = {int(player_id)}
$jobs = '{jobs_json}' | ConvertFrom-Json
$out = @()
function Invoke-Action([object]$a) {{
  $body = ($a | ConvertTo-Json -Compress -Depth 8)
  try {{
    $r = Invoke-WebRequest -Uri ($base + '/action/' + $playerId) -Method PUT -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 5
    return $r.Content
  }} catch {{ return ('ERROR:' + $_.Exception.Message) }}
}}
function Get-BetsCount {{
  try {{
    $raw = (Invoke-WebRequest -Uri ($base + '/data/' + $playerId) -UseBasicParsing -TimeoutSec 5).Content
    if ($raw -match '"Bets"\\s*:\\s*\\[([^\\]]*)\\]') {{
      $inner = $Matches[1].Trim()
      if (-not $inner) {{ return 0 }}
      return (($inner -split '\\}},\\s*\\{{').Count)
    }}
    return -1
  }} catch {{ return -1 }}
}}
foreach ($job in $jobs) {{
  $row = [ordered]@{{
    strategy = [string]$job.strategy
    round = [int]$job.round
    ok = $false
    bets_landed = 0
    verify_error = ''
  }}
  if ($job.error) {{
    $row.verify_error = [string]$job.error
    $out += [pscustomobject]$row
    continue
  }}
  $fail = ''
  foreach ($a in @($job.actions)) {{
    $resp = Invoke-Action $a
    if ($resp -like 'ERROR:*') {{ $fail = $resp; break }}
    Start-Sleep -Milliseconds 20
  }}
  if ($fail) {{
    $row.verify_error = $fail
  }} else {{
    Start-Sleep -Milliseconds 100
    $bc = Get-BetsCount
    $row.bets_landed = $bc
    $row.ok = ($bc -gt 0)
    if (-not $row.ok) {{ $row.verify_error = 'bets_count=0 after PlaceBet' }}
  }}
  $null = Invoke-Action ([pscustomobject]@{{ Type = 'CancelAllBets' }})
  Start-Sleep -Milliseconds 50
  $out += [pscustomobject]$row
}}
$out | ConvertTo-Json -Depth 5 -Compress
"""
        print(
            f"verify chunk {i // chunk_size + 1}: "
            f"{chunk[0]['strategy']} r{chunk[0]['round']} .. "
            f"{chunk[-1]['strategy']} r{chunk[-1]['round']}",
            flush=True,
        )
        res = winrm_run_inline(ip=ip, script=script, timeout=90)
        text = (res.stdout or "").strip()
        parsed: list[Any] = []
        if res.returncode == 0 and text:
            js = text[text.find("[") :] if "[" in text else text[text.find("{") :]
            try:
                parsed = json.loads(js)
                if isinstance(parsed, dict):
                    parsed = [parsed]
            except json.JSONDecodeError:
                parsed = []
        by_key = {
            (str(p.get("strategy")), int(p.get("round") or 0)): p
            for p in parsed
            if isinstance(p, dict)
        }
        for job in chunk:
            row = dict(job)
            # Drop bulky actions from saved report.
            row.pop("actions", None)
            hit = by_key.get((job["strategy"], job["round"]))
            if hit:
                row["ok"] = bool(hit.get("ok"))
                row["bets_landed"] = hit.get("bets_landed")
                row["verify_error"] = hit.get("verify_error") or ""
            else:
                row["ok"] = False
                row["bets_landed"] = 0
                row["verify_error"] = f"no cabinet result: {(text or res.stderr)[:200]}"
            results.append(row)
            status = "OK" if row["ok"] else "FAIL"
            print(
                f"  {row['strategy']} r{row['round']}: {status} "
                f"stake={row['stake_units']}u bets={row.get('bets_landed')} "
                f"sim_won={row.get('sim_won')} clicks={len(row.get('clicks') or [])}",
                flush=True,
            )
    return results


def write_markdown(results: list[dict[str, Any]], meta: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Strategy click report ({meta.get('rounds')} rounds × each method)",
        "",
        f"- IP: `{meta.get('ip')}`  market: `{meta.get('market')}`",
        f"- Progression W/L: simulated (for stake escalation); PlaceBet live-verified",
        f"- {meta.get('started')} → {meta.get('finished')}",
        "",
    ]
    by_sid: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_sid.setdefault(str(row["strategy"]), []).append(row)

    for sid, rows in by_sid.items():
        ok_n = sum(1 for r in rows if r.get("ok"))
        lines += [f"## {sid}", f"Verified {ok_n}/{len(rows)}", ""]
        for row in rows:
            lines.append(
                f"### Round {row['round']} — {row['stake_units']}u — "
                f"ok={row.get('ok')} sim_won={row.get('sim_won')}"
            )
            lines.append(f"- {row.get('notes')}")
            lines.append("| # | action | name | x% | y% | detail |")
            lines.append("|---|--------|------|----|----|--------|")
            for i, c in enumerate(row.get("clicks") or [], 1):
                if c["action"] == "select_chip":
                    lines.append(
                        f"| {i} | **chip** | `{c['name']}` | {c['x_pct']} | {c['y_pct']} | "
                        f"idx={c['chip_index']} |"
                    )
                else:
                    lines.append(
                        f"| {i} | place | `{c['name']}` | {c['x_pct']} | {c['y_pct']} | "
                        f"u={c['units']} `{c['place_bet']}` |"
                    )
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--market", default="RED")
    p.add_argument("--base-unit", type=int, default=1)
    p.add_argument("--strategies", default="")
    p.add_argument("--include-full-board", action="store_true")
    p.add_argument("--chunk-size", type=int, default=8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-credits", type=int, default=200000)
    args = p.parse_args(argv)

    st = fetch_player_state(args.ip)
    creds = int(st.get("credits") or 0)
    if creds < args.min_credits:
        raise SystemExit(f"need credits>={args.min_credits}; have {creds}")

    ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if not ids:
        ids = [
            s.id
            for s in STRATEGIES
            if args.include_full_board or s.id != "full_board"
        ]

    rng = random.Random(args.seed)
    print(f"building {len(ids)} strategies × {args.rounds} rounds (seed={args.seed})", flush=True)
    jobs = build_plans(
        ids, rounds=args.rounds, market=args.market, base_unit=args.base_unit, rng=rng
    )

    # Print all correct clicks first.
    print("\n======== CORRECT CLICKS (planned) ========", flush=True)
    for job in jobs:
        print(
            f"\n{job['strategy']} round {job['round']} — {job['stake_units']}u "
            f"(sim_won→{job['sim_won']}) — {job['notes']}",
            flush=True,
        )
        for c in job["clicks"]:
            if c["action"] == "select_chip":
                print(
                    f"  CHIP {c['name']:10} @ {c['x_pct']:5.1f}%, {c['y_pct']:5.1f}%",
                    flush=True,
                )
            else:
                print(
                    f"  BET  {c['name']:10} @ {c['x_pct']:5.1f}%, {c['y_pct']:5.1f}%  "
                    f"u={c['units']}  PlaceBet={c['place_bet']}",
                    flush=True,
                )

    print("\n======== LIVE VERIFY ========", flush=True)
    t0 = time.time()
    results = verify_jobs_on_cabinet(
        args.ip, jobs, chunk_size=max(1, args.chunk_size)
    )
    elapsed = round(time.time() - t0, 1)

    meta = {
        "ip": args.ip,
        "rounds": args.rounds,
        "market": args.market,
        "seed": args.seed,
        "credits": creds,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_sec": elapsed,
        "ok": sum(1 for r in results if r.get("ok")),
        "total": len(results),
    }
    out_dir = REPO / "_tmp_logs" / "strategy_rounds_report"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"report_{stamp}.json"
    md_path = out_dir / f"clicks_{stamp}.md"
    payload = {"meta": meta, "rounds": results}
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    write_markdown(results, meta, md_path)

    print(
        f"\nRESULT: {meta['ok']}/{meta['total']} verified in {elapsed}s",
        flush=True,
    )
    print(f"wrote {md_path}", flush=True)
    print(f"wrote {json_path}", flush=True)
    return 0 if meta["ok"] == meta["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
