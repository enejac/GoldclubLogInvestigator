"""
Live Tutankhamen-only paytable automation (cabinet F11 forced combos + GameData check).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from automation.cabinet_credit import ensure_cabinet_balance
from automation.cabinet_preflight import (
    cabinet_has_tutankhamen_loaded,
    cabinet_preflight_status,
    script_for_multigamer_select_tutankhamen,
    wait_for_tutankhamen_loaded,
)
from automation.gamedata_parser import GameDataSpin
from automation.input_script import normalize_combo_text, script_for_forced_combo
from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.runner import SpinCase, SpinCheckResult, wait_for_next_spin, write_results_jsonl
from automation.tutankhamen_paytable import (
    THEME_ID,
    TOTAL_BET_CREDITS,
    validate_spin as validate_paytable_spin,
)
from automation.tutankhamen_symbols import tutankhamen_sweep_group, tutankhamen_symbol_sweep_combos


ProgressFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class TutankhamenSweepCase:
    sweep_index: int
    top: int
    middle: int
    bottom: int
    combo_pipe: str


def _log(progress: ProgressFn | None, msg: str) -> None:
    if progress:
        progress(msg)
    else:
        print(msg, flush=True)


def build_sweep_cases(*, wingraph_5x5: bool = False) -> list[TutankhamenSweepCase]:
    cases: list[TutankhamenSweepCase] = []
    for pipe in tutankhamen_symbol_sweep_combos(pipe_separated=True, wingraph_5x5=wingraph_5x5):
        k = len(cases)
        top, middle, bottom = tutankhamen_sweep_group(k)
        cases.append(
            TutankhamenSweepCase(
                sweep_index=k,
                top=top,
                middle=middle,
                bottom=bottom,
                combo_pipe=pipe,
            )
        )
    return cases


def _validate_spin(
    spin: GameDataSpin,
    *,
    sweep_index: int,
    top: int,
    middle: int,
    bottom: int,
) -> tuple[bool, str]:
    try:
        total_bet = int(spin.raw_fields[4]) if len(spin.raw_fields) > 4 else None
    except ValueError:
        total_bet = None
    if total_bet is not None and total_bet != TOTAL_BET_CREDITS:
        return False, f"bet mismatch: expected total bet {TOTAL_BET_CREDITS}, got {total_bet}"

    return validate_paytable_spin(
        spin,
        sweep_index=sweep_index,
        top=top,
        middle=middle,
        bottom=bottom,
    )


def _cabinet_tutankhamen_ready(ip: str, *, min_credits: int = TOTAL_BET_CREDITS) -> tuple[bool, str, bool]:
    """
    Preflight checks before F11 tests.

    Returns (ok, message, needs_game_select).
    """
    bal_ok, bal_msg, balance = ensure_cabinet_balance(ip, min_credits=min_credits)
    state, detail = cabinet_preflight_status(ip)

    if state == "in_tutankhamen":
        if not bal_ok:
            return False, f"{bal_msg}; {detail}", False
        return True, f"{bal_msg}; {detail}", False

    if not bal_ok:
        return False, bal_msg, False

    if state == "selector":
        return True, f"{bal_msg}; on game selector — will select {THEME_ID}", True
    if state == "other_game":
        return False, f"{bal_msg}; wrong game loaded ({detail}) — return to game selector first", False
    return False, f"{bal_msg}; {detail} — open {THEME_ID} on cabinet", False


def run_tutankhamen_live_check(
    *,
    ip: str = "10.0.0.90",
    sweep_indices: list[int] | None = None,
    pause_between_sec: float = 4.0,
    wingraph_5x5: bool = False,
    out_dir: Path | None = None,
    progress: ProgressFn | None = None,
) -> tuple[list[SpinCheckResult], Path]:
    """
    Run Tutankhamen symbol sweep on the cabinet.

  Prerequisites: OneHand running on cabinet; bet mult 2 (total 20) once in-game.
  If on multigamer selector, Tutankhamen is selected automatically before F11.
    """
    gamedata_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\OneHand GameData")
    log_files = [p for p in gamedata_dir.iterdir() if p.is_file()]
    if not log_files:
        raise FileNotFoundError(f"No GameData logs under {gamedata_dir}")
    log_file = max(log_files, key=lambda p: p.stat().st_mtime)

    if out_dir is None:
        out_dir = Path("automation_runs") / f"tutankhamen_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_cases = build_sweep_cases(wingraph_5x5=wingraph_5x5)
    if sweep_indices is not None:
        pick = set(sweep_indices)
        all_cases = [c for c in all_cases if c.sweep_index in pick]
    if not all_cases:
        raise ValueError("no sweep cases selected")

    _log(progress, f"Cabinet {ip} theme {THEME_ID} log={log_file.name} cases={len(all_cases)}")

    ready, ready_msg, needs_game_select = _cabinet_tutankhamen_ready(
        ip, min_credits=TOTAL_BET_CREDITS * max(1, len(all_cases))
    )
    if not ready:
        raise RuntimeError(ready_msg)
    _log(progress, f"Preflight: {ready_msg}")

    local_exe = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=ip, local_exe=local_exe)

    if needs_game_select:
        _log(progress, "Selecting Tutankhamen from multigamer menu...")
        ok, msg = run_input_script_on_cabinet(
            ip=ip,
            agent=staged,
            script=script_for_multigamer_select_tutankhamen(),
            timeout=60,
        )
        if not ok:
            raise RuntimeError(f"game select input failed: {msg}")
        loaded, load_msg = wait_for_tutankhamen_loaded(ip, timeout_sec=35.0)
        if not loaded:
            raise RuntimeError(load_msg)
        _log(progress, f"Game select: {load_msg}")
        time.sleep(3.0)

    if not cabinet_has_tutankhamen_loaded(ip):
        state, detail = cabinet_preflight_status(ip)
        raise RuntimeError(f"cannot run F11: not in {THEME_ID} (state={state}: {detail})")

    results: list[SpinCheckResult] = []
    offset = log_file.stat().st_size

    for i, tc in enumerate(all_cases):
        combo_nl = normalize_combo_text(tc.combo_pipe)
        case = SpinCase(theme_id=THEME_ID, bet_multiplier=2, combo_text=combo_nl)
        label = f"sweep[{tc.sweep_index}] rows {tc.top}/{tc.middle}/{tc.bottom}"
        _log(progress, f"--- {label} ---")

        script = script_for_forced_combo(combo_text=tc.combo_pipe)
        earliest = datetime.now().astimezone() - timedelta(seconds=2)
        start_offset = log_file.stat().st_size

        ok, msg = run_input_script_on_cabinet(
            ip=ip,
            agent=staged,
            script=script,
            timeout=120,
        )
        if not ok:
            _log(progress, f"FAIL input: {msg}")
            results.append(SpinCheckResult(case=case, ok=False, message=f"input: {msg}", spin=None))
            time.sleep(pause_between_sec)
            continue

        start_offset, spin, why = wait_for_next_spin(
            log_file,
            start_offset=start_offset,
            earliest_ts=earliest,
            theme_id=THEME_ID,
            timeout_sec=60.0,
        )
        offset = start_offset
        if spin is None:
            _log(progress, f"FAIL gamedata: {why}")
            results.append(SpinCheckResult(case=case, ok=False, message=why, spin=None))
            time.sleep(pause_between_sec)
            continue

        ok, vmsg = _validate_spin(
            spin,
            sweep_index=tc.sweep_index,
            top=tc.top,
            middle=tc.middle,
            bottom=tc.bottom,
        )
        status = "PASS" if ok else "FAIL"
        _log(
            progress,
            f"{status} spin_id={spin.spin_id} win={spin.total_win_credits} [{vmsg}]",
        )
        results.append(SpinCheckResult(case=case, ok=ok, message=vmsg, spin=spin))
        time.sleep(pause_between_sec)

    out_path = out_dir / "results.jsonl"
    write_results_jsonl(results, out_path)

    summary = {
        "theme": THEME_ID,
        "ip": ip,
        "cases": len(results),
        "passed": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _log(progress, f"Done {summary['passed']}/{summary['cases']} passed -> {out_dir}")
    return results, out_dir
