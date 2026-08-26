"""
Fast strategy-cycle tests via middleware PlaceBet / SetChip / StartGame.

Skips InputAgent, schtasks, and pixel clicks. Two modes:

  place  — place plan, verify bets via GET /api/data, CancelAllBets (no spin).
           Many strategies fit in one open window (~2–4 s each).
  spin   — place + StartGame + wait closed/credits (still ~1 spin/strategy, but
           no UI agent overhead).

Usage:
  python -m automation.run_strategy_cycles_fast --ip 10.0.0.90 --mode place
  python -m automation.run_strategy_cycles_fast --ip 10.0.0.90 --mode spin
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from automation.roulette_bet_catalog import catalog as bet_catalog
from automation.roulette_layout import CHIP_SPOTS
from automation.remote_exec import winrm_run_inline
from automation.roulette_middleware import (
    cancel_all_bets,
    fetch_player_state,
    place_bet,
    set_chip,
    start_game,
)
from automation.roulette_runner import (
    _CLOSED_RE,
    _OPEN_RE,
    _latest_file,
    wait_for_log_marker,
)
from automation.roulette_strategies import (
    CHIP_VALUES,
    STRATEGIES,
    StrategyState,
    decompose_units,
    plan_for_strategy,
)

REPO = Path(__file__).resolve().parents[1]

CHIP_INDEX: dict[str, int] = {c.name: i for i, c in enumerate(CHIP_SPOTS)}

# Cloth outside / column labels → PlaceBet grammar (Appendix A number-sets).
_OUTSIDE_PLACE: dict[str, str] = {
    "RED": "1+3+5+7+9+12+14+16+18+19+21+23+25+27+30+32+34+36",
    "BLACK": "2+4+6+8+10+11+13+15+17+20+22+24+26+28+29+31+33+35",
    "ODD": "1+3+5+7+9+11+13+15+17+19+21+23+25+27+29+31+33+35",
    "EVEN": "2+4+6+8+10+12+14+16+18+20+22+24+26+28+30+32+34+36",
    "1-18": "1+2+3+4+5+6+7+8+9+10+11+12+13+14+15+16+17+18",
    "19-36": "19+20+21+22+23+24+25+26+27+28+29+30+31+32+33+34+35+36",
    "1-12": "1+2+3+4+5+6+7+8+9+10+11+12",
    "13-24": "13+14+15+16+17+18+19+20+21+22+23+24",
    "25-36": "25+26+27+28+29+30+31+32+33+34+35+36",
    "2to1_bot": "1+4+7+10+13+16+19+22+25+28+31+34",
    "2to1_mid": "2+5+8+11+14+17+20+23+26+29+32+35",
    "2to1_top": "3+6+9+12+15+18+21+24+27+30+33+36",
}

_UI_NAMED: dict[str, str] = {
    "VECINOS": "Neighbours",
    "FINALES": "Finales",
    "COMPLETO": "MaxBet",
    "HUERFANOS": "Orphans",
    "VECINOS_0": "CloseToZero",
    "VECINOS_00": "CloseToDoubleZero",
}

_SPLIT_RE = re.compile(r"^split_(\d+)_(\d+)$")
_STREET_RE = re.compile(r"^street_(\d+)_(\d+)_(\d+)$")
_CORNER_RE = re.compile(r"^corner_(\d+)_(\d+)_(\d+)_(\d+)$")
_SIX_RE = re.compile(r"^sixline_(\d+)_(\d+)$")


def spot_to_place_bet(name: str) -> str | None:
    """Map a layout ClickTarget.name to one PlaceBet string."""
    n = str(name)
    if n in _OUTSIDE_PLACE:
        return _OUTSIDE_PLACE[n]
    if n in _UI_NAMED:
        return _UI_NAMED[n]
    if n == "00":
        return "37"
    if n.isdigit():
        return n
    m = _SPLIT_RE.match(n)
    if m:
        return f"{m.group(1)}+{m.group(2)}"
    m = _STREET_RE.match(n)
    if m:
        return f"{m.group(1)}+{m.group(2)}+{m.group(3)}"
    m = _CORNER_RE.match(n)
    if m:
        return f"{m.group(1)}+{m.group(2)}+{m.group(3)}+{m.group(4)}"
    m = _SIX_RE.match(n)
    if m:
        a = int(m.group(1))
        b = int(m.group(2))
        return "+".join(str(x) for x in range(a, b + 1))
    cat = bet_catalog()
    if n in cat:
        pb = cat[n].get("place_bet")
        if isinstance(pb, list) and pb:
            return str(pb[0])
    return None


def _wait_open(ip: str, timeout: float = 90.0) -> tuple[bool, str]:
    log_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette")
    roulette_log = _latest_file(log_dir)
    if roulette_log is None:
        return False, f"no roulette logs under {log_dir}"
    # Already open?
    peek_start = max(0, roulette_log.stat().st_size - 8000)
    text = roulette_log.read_bytes()[peek_start:].decode("utf-8", errors="replace")
    for line in reversed(text.replace("\r\n", "\n").splitlines()):
        if _CLOSED_RE.search(line):
            break
        if _OPEN_RE.search(line):
            return True, "already open"
    off = roulette_log.stat().st_size
    _, ok, detail = wait_for_log_marker(
        roulette_log, start_offset=off, pattern=_OPEN_RE, timeout_sec=timeout
    )
    return bool(ok), detail


def _wait_closed(ip: str, timeout: float = 90.0) -> tuple[bool, str]:
    log_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette")
    roulette_log = _latest_file(log_dir)
    if roulette_log is None:
        return False, f"no roulette logs under {log_dir}"
    off = roulette_log.stat().st_size
    _, ok, detail = wait_for_log_marker(
        roulette_log, start_offset=off, pattern=_CLOSED_RE, timeout_sec=timeout
    )
    return bool(ok), detail


def place_plan_via_api(ip: str, plan_placements: list[Any], *, player_id: int = 0) -> dict[str, Any]:
    """
    Cancel + SetChip + PlaceBet + read bets in **one WinRM round-trip**.

    (Per-action WinRM was ~10–15 s each and dominated place-mode runtime.)
    """
    actions: list[dict[str, Any]] = [{"Type": "CancelAllBets"}]
    placed: list[str] = []
    last_chip_idx: int | None = None
    for placement in plan_placements:
        spot_name = placement.spot.name
        bet = spot_to_place_bet(spot_name)
        if not bet:
            return {"ok": False, "error": f"no PlaceBet mapping for {spot_name!r}", "placed": placed}
        if placement.units <= 0:
            continue
        for chip, count in decompose_units(placement.units):
            idx = CHIP_INDEX.get(chip.name)
            if idx is None:
                return {"ok": False, "error": f"unknown chip {chip.name}", "placed": placed}
            if last_chip_idx != idx:
                actions.append({"Type": "SetChip", "Data": str(idx)})
                last_chip_idx = idx
            batch = [bet] * int(count)
            actions.append({"Type": "PlaceBet", "Data": batch})
            placed.extend(
                f"{spot_name}x{CHIP_VALUES.get(chip.name, chip.name)}" for _ in range(count)
            )

    actions_json = json.dumps(actions, separators=(",", ":"), ensure_ascii=True).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8090/api'
$playerId = {int(player_id)}
$actions = '{actions_json}' | ConvertFrom-Json
$results = @()
foreach ($a in $actions) {{
  $body = ($a | ConvertTo-Json -Compress -Depth 8)
  try {{
    $r = Invoke-WebRequest -Uri ($base + '/action/' + $playerId) -Method PUT -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 8
    $results += $r.Content
  }} catch {{
    $results += ('ERROR:' + $_.Exception.Message)
  }}
  Start-Sleep -Milliseconds 40
}}
Start-Sleep -Milliseconds 200
try {{
  $data = (Invoke-WebRequest -Uri ($base + '/data/' + $playerId) -UseBasicParsing -TimeoutSec 8).Content
}} catch {{
  $data = ('ERROR:' + $_.Exception.Message)
}}
# Clear board so next strategy can reuse the open window without leftover chips.
try {{
  $clr = '{{"Type":"CancelAllBets"}}'
  $null = Invoke-WebRequest -Uri ($base + '/action/' + $playerId) -Method PUT -Body $clr -ContentType 'application/json' -UseBasicParsing -TimeoutSec 8
}} catch {{}}
Write-Output ('ACTIONS=' + ($results -join '||'))
Write-Output ('DATA=' + $data)
"""
    res = winrm_run_inline(ip=ip, script=script, timeout=60)
    text = (res.stdout or "").strip()
    if res.returncode != 0 or "DATA=ERROR" in text:
        return {"ok": False, "error": text[:1500] or res.stderr, "placed": placed}

    data_raw = ""
    for line in text.splitlines():
        if line.startswith("DATA="):
            data_raw = line[5:]
    bets: list[Any] = []
    credits = None
    try:
        payload = json.loads(data_raw)
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("Type") or item.get("$type") or "")
                if "PlayerDataBets" in t or (
                    isinstance(item.get("Bets"), list) and "Bets" in item
                ):
                    bets = list(item.get("Bets") or [])
                if "PlayerData," in t or t.endswith("PlayerData"):
                    cred = item.get("Credits") or {}
                    if isinstance(cred, dict) and cred.get("Credit") is not None:
                        try:
                            credits = int(str(cred.get("Credit")))
                        except ValueError:
                            pass
    except json.JSONDecodeError:
        m = re.search(r'"Bets"\s*:\s*(\[[^\]]*\])', data_raw)
        if m:
            try:
                bets = json.loads(m.group(1))
            except json.JSONDecodeError:
                bets = []
        cm = re.search(r'"Credit"\s*:\s*"(\d+)"', data_raw)
        if cm:
            credits = int(cm.group(1))

    action_errors = [p for p in text.split("ACTIONS=", 1)[-1].split("DATA=")[0].split("||") if p.startswith("ERROR:")]
    return {
        "ok": len(bets) > 0 and not action_errors,
        "error": "; ".join(action_errors),
        "placed": placed,
        "bets_count": len(bets),
        "bets": bets,
        "credits": credits,
        "raw_actions": text[:500],
    }


def run_all_place_batched(
    ip: str,
    strategy_ids: list[str],
    *,
    market: str,
    base_unit: int,
    player_id: int = 0,
) -> list[dict[str, Any]]:
    """
    Place+verify+clear every strategy inside **one** open window and **one** WinRM call.

    Irreducible floor is still ~1 open-window wait; per-strategy overhead collapses to
    local HTTP on the cabinet (~tens of ms each).
    """
    from automation.roulette_layout import NUMBER_SPOTS
    from automation.roulette_strategies import Placement, StrategyBetPlan

    plans: list[tuple[str, Any]] = []
    for sid in strategy_ids:
        state = StrategyState()
        if sid == "random":
            n = random.randint(1, 36)
            plan = StrategyBetPlan(
                [Placement(NUMBER_SPOTS[n], max(1, base_unit))],
                max(1, base_unit),
                "random",
                f"api random straight {n}",
            )
        else:
            plan = plan_for_strategy(sid, state, base_unit=base_unit, market=market)
        plans.append((sid, plan))

    # Build per-strategy action lists for the remote loop.
    remote_jobs: list[dict[str, Any]] = []
    for sid, plan in plans:
        actions: list[dict[str, Any]] = [{"Type": "CancelAllBets"}]
        labels: list[str] = []
        last_chip_idx: int | None = None
        err = ""
        for placement in plan.placements:
            bet = spot_to_place_bet(placement.spot.name)
            if not bet:
                err = f"no PlaceBet mapping for {placement.spot.name!r}"
                break
            if placement.units <= 0:
                continue
            for chip, count in decompose_units(placement.units):
                idx = CHIP_INDEX.get(chip.name)
                if idx is None:
                    err = f"unknown chip {chip.name}"
                    break
                if last_chip_idx != idx:
                    actions.append({"Type": "SetChip", "Data": str(idx)})
                    last_chip_idx = idx
                actions.append({"Type": "PlaceBet", "Data": [bet] * int(count)})
                labels.extend(
                    f"{placement.spot.name}x{CHIP_VALUES.get(chip.name, chip.name)}"
                    for _ in range(count)
                )
            if err:
                break
        remote_jobs.append(
            {
                "strategy": sid,
                "stake_units": plan.stake_units,
                "notes": plan.notes,
                "labels": labels,
                "error": err,
                "actions": [] if err else actions,
            }
        )

    jobs_json = json.dumps(remote_jobs, separators=(",", ":"), ensure_ascii=True).replace(
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
  }} catch {{
    return ('ERROR:' + $_.Exception.Message)
  }}
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
function Get-Credits {{
  try {{
    $raw = (Invoke-WebRequest -Uri ($base + '/data/' + $playerId) -UseBasicParsing -TimeoutSec 5).Content
    if ($raw -match '"Credit"\\s*:\\s*"(\\d+)"') {{ return [int]$Matches[1] }}
    return -1
  }} catch {{ return -1 }}
}}
foreach ($job in $jobs) {{
  $row = [ordered]@{{
    strategy = [string]$job.strategy
    mode = 'place'
    stake_units = [int]$job.stake_units
    notes = [string]$job.notes
    placed_labels = @($job.labels)
    ok = $false
    message = ''
    bets_while_placed = 0
    credits_before = (Get-Credits)
    credits_after = $null
    won = $null
  }}
  if ($job.error) {{
    $row.message = [string]$job.error
    $out += [pscustomobject]$row
    continue
  }}
  foreach ($a in @($job.actions)) {{
    $resp = Invoke-Action $a
    if ($resp -like 'ERROR:*') {{
      $row.message = $resp
      break
    }}
    Start-Sleep -Milliseconds 25
  }}
  if (-not $row.message) {{
    Start-Sleep -Milliseconds 120
    $bc = Get-BetsCount
    $row.bets_while_placed = $bc
    $midCred = Get-Credits
    $null = Invoke-Action ([pscustomobject]@{{ Type = 'CancelAllBets' }})
    Start-Sleep -Milliseconds 80
    $row.credits_after = (Get-Credits)
    $row.ok = ($bc -gt 0)
    $row.message = ('placed=' + @($job.labels).Count + ' bets=' + $bc + ' credits ' + $row.credits_before + '->' + $midCred + '->' + $row.credits_after + ' notes=' + $job.notes)
  }} else {{
    $row.credits_after = (Get-Credits)
  }}
  $out += [pscustomobject]$row
}}
$out | ConvertTo-Json -Depth 6 -Compress
"""
    before = fetch_player_state(ip)
    res = winrm_run_inline(ip=ip, script=script, timeout=120)
    text = (res.stdout or "").strip()
    if res.returncode != 0 or not text or text.startswith("ERROR"):
        return [
            {
                "strategy": sid,
                "mode": "place",
                "ok": False,
                "message": f"batched WinRM failed: {(text or res.stderr)[:800]}",
                "credits_before": before.get("credits"),
            }
            for sid, _ in plans
        ]
    # stdout may include WinRM noise; take last JSON array/object.
    json_start = text.find("[")
    if json_start < 0:
        json_start = text.find("{")
    payload = text[json_start:] if json_start >= 0 else text
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return [
            {
                "strategy": "batch",
                "mode": "place",
                "ok": False,
                "message": f"bad JSON from cabinet: {text[:800]}",
            }
        ]
    if isinstance(parsed, dict):
        parsed = [parsed]
    rows: list[dict[str, Any]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "strategy": row.get("strategy"),
                "mode": "place",
                "ok": bool(row.get("ok")),
                "message": row.get("message"),
                "stake_units": row.get("stake_units"),
                "won": None,
                "credits_before": row.get("credits_before"),
                "credits_after": row.get("credits_after"),
                "bets_while_placed": row.get("bets_while_placed"),
                "placed_labels": row.get("placed_labels"),
            }
        )
    return rows


def run_one_place(ip: str, sid: str, *, market: str, base_unit: int) -> dict[str, Any]:
    rows = run_all_place_batched(ip, [sid], market=market, base_unit=base_unit)
    return rows[0] if rows else {"strategy": sid, "ok": False, "message": "empty"}


def run_one_spin(ip: str, sid: str, *, market: str, base_unit: int) -> dict[str, Any]:
    open_ok, open_detail = _wait_open(ip)
    if not open_ok:
        return {
            "strategy": sid,
            "mode": "spin",
            "ok": False,
            "message": f"no open window: {open_detail}",
            "stake_units": None,
            "won": None,
            "credits_before": None,
            "credits_after": None,
        }
    before = fetch_player_state(ip)
    state = StrategyState()
    if sid == "random":
        n = random.randint(1, 36)
        from automation.roulette_layout import NUMBER_SPOTS
        from automation.roulette_strategies import Placement, StrategyBetPlan

        plan = StrategyBetPlan(
            [Placement(NUMBER_SPOTS[n], max(1, base_unit))],
            max(1, base_unit),
            "random",
            f"api random straight {n}",
        )
    else:
        plan = plan_for_strategy(sid, state, base_unit=base_unit, market=market)

    placed = place_plan_via_api(ip, plan.placements)
    if not placed.get("ok") or int(placed.get("bets_count") or 0) <= 0:
        cancel_all_bets(ip)
        return {
            "strategy": sid,
            "mode": "spin",
            "ok": False,
            "message": f"place failed: {placed.get('error') or placed}",
            "stake_units": plan.stake_units,
            "won": None,
            "credits_before": before.get("credits"),
            "credits_after": fetch_player_state(ip).get("credits"),
        }

    sg = start_game(ip)
    if not sg.get("success"):
        cancel_all_bets(ip)
        return {
            "strategy": sid,
            "mode": "spin",
            "ok": False,
            "message": f"StartGame failed: {sg}",
            "stake_units": plan.stake_units,
            "won": None,
            "credits_before": before.get("credits"),
            "credits_after": fetch_player_state(ip).get("credits"),
        }

    closed_ok, closed_detail = _wait_closed(ip, timeout=120.0)
    # Brief settle for credit meter.
    time.sleep(3.0)
    after = fetch_player_state(ip)
    won: bool | None = None
    if before.get("credits") is not None and after.get("credits") is not None:
        if after["credits"] > before["credits"]:
            won = True
        elif after["credits"] < before["credits"]:
            won = False
    outcome = "win" if won is True else "loss" if won is False else "unknown"
    ok = closed_ok and won is not None
    return {
        "strategy": sid,
        "mode": "spin",
        "ok": ok,
        "message": (
            f"{outcome} credits {before.get('credits')}->{after.get('credits')} "
            f"stake={plan.stake_units}u closed={closed_ok} ({closed_detail}) "
            f"start={sg.get('success')} notes={plan.notes}"
        ),
        "stake_units": plan.stake_units,
        "won": won,
        "credits_before": before.get("credits"),
        "credits_after": after.get("credits"),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fast middleware strategy cycle tests")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--mode", choices=("place", "spin"), default="place")
    p.add_argument("--strategies", default="", help="Comma list; default all except full_board")
    p.add_argument("--include-full-board", action="store_true")
    p.add_argument("--market", default="RED")
    p.add_argument("--base-unit", type=int, default=1)
    p.add_argument("--min-credits", type=int, default=100000)
    args = p.parse_args(argv)

    st = fetch_player_state(args.ip)
    if int(st.get("credits") or 0) < args.min_credits:
        raise SystemExit(f"need credits>={args.min_credits}; have {st.get('credits')}")

    ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if not ids:
        ids = [
            s.id
            for s in STRATEGIES
            if args.include_full_board or s.id != "full_board"
        ]

    out_dir = REPO / "_tmp_logs" / "strategy_cycles_fast"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    if args.mode == "place":
        open_ok, open_detail = _wait_open(args.ip)
        if not open_ok:
            raise SystemExit(f"need bets open: {open_detail}")
        print(f"open window ready ({open_detail})", flush=True)
        print(f"=== batched place ({len(ids)} strategies, one WinRM) ===", flush=True)
        t0 = time.time()
        summary = run_all_place_batched(
            args.ip, ids, market=args.market, base_unit=args.base_unit
        )
        elapsed = round(time.time() - t0, 2)
        for row in summary:
            row["elapsed_sec"] = elapsed
            print(json.dumps(row, indent=2, default=str), flush=True)
    else:
        for sid in ids:
            print(f"=== strategy {sid} ({args.mode}) ===", flush=True)
            t0 = time.time()
            row = run_one_spin(args.ip, sid, market=args.market, base_unit=args.base_unit)
            row["elapsed_sec"] = round(time.time() - t0, 2)
            summary.append(row)
            print(json.dumps(row, indent=2, default=str), flush=True)

    path = out_dir / f"summary_{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {path}", flush=True)
    return 0 if summary and all(r.get("ok") for r in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
