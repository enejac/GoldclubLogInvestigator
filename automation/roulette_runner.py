"""
Log-driven roulette automation: wait for betting open -> random bets + spin.

Uses cabinet InputAgent ``click_post`` (PostMessage / invisible clicks) against
the live ``godot`` window. Phase detection comes from ``ruleta Roulette`` logs.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_script import (
    script_for_board_scan,
    script_for_random_bets_and_spin,
    script_for_strategy_plan,
)
from automation.roulette_strategies import (
    StrategyState,
    apply_result,
    plan_for_strategy,
)
from live_tail_io import read_new_bytes

ProgressFn = Callable[[str], None]

_OPEN_RE = re.compile(r"Bets are open", re.IGNORECASE)
_CLOSING_RE = re.compile(r"Bets are clos", re.IGNORECASE)
_CLOSED_RE = re.compile(r"Bets are closed", re.IGNORECASE)
_RCM_RE = re.compile(
    r"RCM\s+s=\s*(?P<staked>\d+)\s+c=\s*(?P<c>\d+)\s+p=\s*(?P<p>\d+)",
    re.IGNORECASE,
)
_METERS_RE = re.compile(
    r"Meters\s*[—\-]\s*credits\s+(?P<credits>\d+),\s*staked\s+(?P<staked>\d+)",
    re.IGNORECASE,
)
_SETCHIP_RE = re.compile(r"Sending put action SetChip data\s+(\d+)", re.IGNORECASE)
_MENU_RE = re.compile(r"Sending put action MenuCommands data\s+(\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RouletteRoundResult:
    round_index: int
    ok: bool
    message: str
    chip: str = ""
    bets: list[str] = field(default_factory=list)
    staked_seen: int | None = None
    credits_before: int | None = None
    credits_after: int | None = None
    setchip_hits: int = 0
    strategy: str = "random"
    stake_units: int = 0
    won: bool | None = None


def _progress(cb: ProgressFn | None, msg: str) -> None:
    if cb is not None:
        cb(msg)


def _latest_file(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    files = [p for p in path.iterdir() if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _read_new_lines(path: Path, *, start_offset: int, max_bytes: int = 2_000_000) -> tuple[int, list[str]]:
    try:
        end = path.stat().st_size
    except OSError:
        return start_offset, []
    if end < start_offset:
        # log rotated
        start_offset = 0
    if end <= start_offset:
        return end, []
    to_read = min(end - start_offset, max_bytes)
    blob = read_new_bytes(path, start_offset, start_offset + to_read)
    txt = blob.decode("utf-8", errors="replace")
    lines = txt.replace("\r\n", "\n").splitlines()
    return start_offset + to_read, lines


def wait_for_log_marker(
    log_file: Path,
    *,
    start_offset: int,
    pattern: re.Pattern[str],
    timeout_sec: float,
    also_stop: re.Pattern[str] | None = None,
) -> tuple[int, bool, str]:
    """Poll a log until *pattern* matches a new line. Returns (offset, matched, detail)."""
    deadline = time.time() + timeout_sec
    offset = start_offset
    while time.time() < deadline:
        offset, lines = _read_new_lines(log_file, start_offset=offset)
        for line in lines:
            if also_stop is not None and also_stop.search(line):
                return offset, False, f"stopped on: {line.strip()[:120]}"
            if pattern.search(line):
                return offset, True, line.strip()[:160]
        time.sleep(0.25)
    return offset, False, f"timeout after {timeout_sec:.0f}s waiting for {pattern.pattern}"


def peek_betting_phase(log_file: Path, *, peek_bytes: int = 8000) -> str:
    """Return ``open`` / ``closing`` / ``closed`` / ``unknown`` from the log tail."""
    try:
        size = log_file.stat().st_size
    except OSError:
        return "unknown"
    peek_start = max(0, size - peek_bytes)
    _, peek_lines = _read_new_lines(log_file, start_offset=peek_start)
    for line in reversed(peek_lines):
        if _CLOSED_RE.search(line):
            return "closed"
        if _CLOSING_RE.search(line):
            return "closing"
        if _OPEN_RE.search(line):
            return "open"
    return "unknown"


# After the log says "Bets are open", Godot keeps chips / bet tools / START grey
# for a short beat while the open chrome enables. Measured settle on .90.
OPEN_UI_SETTLE_SEC = 0.85
# START (countdown disc / bottom-right button) arms a touch later than the tray.
OPEN_UI_SETTLE_START_SEC = 1.25


def wait_for_betting_open(
    log_file: Path,
    *,
    timeout_sec: float = 120.0,
    settle_sec: float = OPEN_UI_SETTLE_SEC,
    already_open_settle_sec: float = 0.15,
    progress: ProgressFn | None = None,
) -> tuple[bool, str]:
    """
    Block until betting is open and UI chrome should accept clicks.

    If the tail already shows open, only a short settle runs (do not burn the
    remaining window). Otherwise wait for the next ``Bets are open`` marker,
    then settle so START / chips / bet tools are clickable.
    """
    phase = peek_betting_phase(log_file)
    if phase == "open":
        if already_open_settle_sec > 0:
            time.sleep(already_open_settle_sec)
        return True, "already open"

    if progress is not None:
        progress(f"waiting for bets open (phase={phase}) ...")
    offset = log_file.stat().st_size
    _, matched, detail = wait_for_log_marker(
        log_file,
        start_offset=offset,
        pattern=_OPEN_RE,
        timeout_sec=timeout_sec,
    )
    if not matched:
        return False, detail
    if settle_sec > 0:
        if progress is not None:
            progress(f"open - settling {settle_sec:.2f}s for UI enable")
        time.sleep(settle_sec)
    return True, detail


def _scan_activity(lines: list[str]) -> tuple[int | None, int | None, int, list[int]]:
    """Return (max_staked, last_credits_total, setchip_hits, menu_codes).

    RCM total credits = cashable ``c`` + promo ``p`` (matches on-screen CRÉDITO).
    """
    staked: int | None = None
    credits_total: int | None = None
    setchip = 0
    menus: list[int] = []
    for line in lines:
        m = _METERS_RE.search(line)
        if m:
            try:
                val = int(m.group("staked"))
                staked = val if staked is None else max(staked, val)
            except (IndexError, ValueError):
                pass
            try:
                credits_total = int(m.group("credits"))
            except (IndexError, ValueError):
                pass
        m2 = _RCM_RE.search(line)
        if m2:
            try:
                val = int(m2.group("staked"))
                staked = val if staked is None else max(staked, val)
            except (IndexError, ValueError):
                pass
            try:
                credits_total = int(m2.group("c")) + int(m2.group("p"))
            except (IndexError, ValueError):
                pass
        if _SETCHIP_RE.search(line):
            setchip += 1
        mm = _MENU_RE.search(line)
        if mm:
            try:
                menus.append(int(mm.group(1)))
            except ValueError:
                pass
    return staked, credits_total, setchip, menus


def run_roulette_random(
    *,
    ip: str,
    rounds: int = 3,
    min_bets: int = 3,
    max_bets: int = 7,
    include_outside: bool = True,
    strategy_id: str = "random",
    market: str = "RED",
    base_unit: int = 1,
    open_timeout_sec: float = 90.0,
    session: int = 1,
    progress: ProgressFn | None = None,
) -> list[RouletteRoundResult]:
    """
    Run *rounds* of roulette bets + START on the live cabinet.

    ``strategy_id`` selects a progression system (see ``roulette_strategies``).
    ``random`` keeps the original random straight-up behaviour.
    """
    _ = session
    sid = (strategy_id or "random").strip().lower()
    roulette_log_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette")
    ruleta_log_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta")
    godot_log_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\godot1")

    roulette_log = _latest_file(roulette_log_dir)
    if roulette_log is None:
        raise FileNotFoundError(f"No ruleta Roulette logs under {roulette_log_dir}")

    _progress(progress, f"Building InputAgent ...")
    local_exe = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=ip, local_exe=local_exe)
    _progress(progress, f"Staged agent at {staged.remote_dir}")
    _progress(
        progress,
        f"Strategy={sid} market={market} base_unit={base_unit} rounds={rounds}",
    )

    results: list[RouletteRoundResult] = []
    offset = roulette_log.stat().st_size
    strat_state = StrategyState()

    for i in range(1, rounds + 1):
        # WinRM InputAgent is fast enough to launch after the real open marker.
        # Wait for "Bets are open" + UI settle so chips / START are clickable
        # (closed+6s often landed while chrome was still grey).
        _progress(progress, f"Round {i}/{rounds}: waiting for bets open ...")
        opened, detail = wait_for_betting_open(
            roulette_log,
            timeout_sec=open_timeout_sec,
            settle_sec=OPEN_UI_SETTLE_START_SEC,
            progress=progress,
        )
        if not opened:
            results.append(
                RouletteRoundResult(
                    round_index=i,
                    ok=False,
                    message=f"no bets-open arm point: {detail}",
                )
            )
            _progress(progress, f"Round {i}: FAIL - {detail}")
            continue
        offset = roulette_log.stat().st_size
        _progress(progress, f"Round {i}: open ready - {detail}")

        stake_units = 0
        plan = None
        if sid == "random":
            script = script_for_random_bets_and_spin(
                min_bets=min_bets,
                max_bets=max_bets,
                include_outside=include_outside,
                press_spin=True,
            )
        elif sid == "full_board":
            plan = plan_for_strategy(
                sid,
                strat_state,
                base_unit=base_unit,
                market=market,
            )
            script = script_for_board_scan(
                [p.spot for p in plan.placements],
                press_spin=False,
                clear_every=8,
            )
            stake_units = plan.stake_units
            _progress(progress, f"Round {i}: plan - {plan.notes}")
        else:
            plan = plan_for_strategy(
                sid,
                strat_state,
                base_unit=base_unit,
                market=market,
            )
            script = script_for_strategy_plan(plan, press_spin=True)
            stake_units = plan.stake_units
            _progress(progress, f"Round {i}: plan - {plan.notes}")
        meta = script.pop("meta", {})
        chip = str(meta.get("chip") or "")
        bets = list(meta.get("bets") or [])
        stake_units = int(meta.get("stake_units") or stake_units or 0)
        _progress(
            progress,
            f"Round {i}: [{sid}] chip={chip} stake={stake_units}u "
            f"bets={','.join(bets[:12])}{'...' if len(bets) > 12 else ''} (launching)",
        )

        godot_log = _latest_file(godot_log_dir)
        godot_off = godot_log.stat().st_size if godot_log is not None else 0
        ruleta_log = _latest_file(ruleta_log_dir)
        ruleta_off = ruleta_log.stat().st_size if ruleta_log is not None else 0
        pre_size = roulette_log.stat().st_size

        # Snapshot credit meter before clicks (RCM p=).
        credits_before: int | None = None
        if ruleta_log is not None:
            try:
                tail = ruleta_log.read_bytes()[-4000:].decode("utf-8", errors="replace")
                for line in reversed(tail.replace("\r\n", "\n").splitlines()):
                    _, p_cred, _, _ = _scan_activity([line])
                    if p_cred is not None:
                        credits_before = p_cred
                        break
            except OSError:
                pass

        ok_agent, agent_msg = run_input_script_on_cabinet(
            ip=ip,
            agent=staged,
            script=script,
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=90,
        )
        if not ok_agent:
            results.append(
                RouletteRoundResult(
                    round_index=i,
                    ok=False,
                    message=f"input agent failed: {agent_msg}",
                    chip=chip,
                    bets=bets,
                    credits_before=credits_before,
                )
            )
            _progress(progress, f"Round {i}: FAIL - agent: {agent_msg}")
            continue

        # Collect activity until bets close (or short grace).
        deadline = time.time() + 35.0
        staked_seen: int | None = None
        credits_after: int | None = None
        setchip_hits = 0
        menu_codes: list[int] = []
        closed = False
        while time.time() < deadline:
            if godot_log is not None:
                godot_off, glines = _read_new_lines(godot_log, start_offset=godot_off)
                s, p_cred, sc, menus = _scan_activity(glines)
                setchip_hits += sc
                menu_codes.extend(menus)
                if s is not None:
                    staked_seen = s if staked_seen is None else max(staked_seen, s)
                if p_cred is not None:
                    credits_after = p_cred
            if ruleta_log is not None:
                ruleta_off, rlines = _read_new_lines(ruleta_log, start_offset=ruleta_off)
                s, p_cred, _, _ = _scan_activity(rlines)
                if s is not None:
                    staked_seen = s if staked_seen is None else max(staked_seen, s)
                if p_cred is not None:
                    credits_after = p_cred
            offset, rlines = _read_new_lines(roulette_log, start_offset=max(offset, pre_size))
            for line in rlines:
                s, p_cred, _, _ = _scan_activity([line])
                if s is not None:
                    staked_seen = s if staked_seen is None else max(staked_seen, s)
                if p_cred is not None:
                    credits_after = p_cred
                if _CLOSING_RE.search(line) or _CLOSED_RE.search(line):
                    closed = True
            if closed:
                break
            time.sleep(0.3)

        # RCM credit lines often arrive a few seconds after "Bets are closed".
        if credits_after is None or credits_after == credits_before:
            grace_deadline = time.time() + 12.0
            while time.time() < grace_deadline:
                if ruleta_log is not None:
                    ruleta_off, rlines = _read_new_lines(ruleta_log, start_offset=ruleta_off)
                    _, p_cred, _, _ = _scan_activity(rlines)
                    if p_cred is not None:
                        credits_after = p_cred
                        if credits_before is not None and credits_after != credits_before:
                            break
                time.sleep(0.4)

        won: bool | None = None
        if credits_before is not None and credits_after is not None:
            if credits_after > credits_before:
                won = True
            elif credits_after < credits_before:
                won = False

        credit_moved = won is not None
        played = bool(
            ok_agent
            if sid == "full_board"
            else (
                credit_moved
                or (staked_seen is not None and staked_seen > 0)
                or setchip_hits > 0
                or any(code not in (193,) for code in menu_codes)
            )
        )
        if played and sid == "full_board":
            apply_result(
                sid,
                strat_state,
                won=bool(won),
                base_unit=base_unit,
                stake_units=stake_units,
            )
        elif played and won is not None and sid != "random":
            apply_result(
                sid,
                strat_state,
                won=won,
                base_unit=base_unit,
                stake_units=stake_units,
            )

        outcome = "win" if won is True else "loss" if won is False else "unknown"
        if played:
            scan_note = ""
            if sid == "full_board":
                scan_note = f" scan_idx={strat_state.scan_index}"
            msg = (
                f"{outcome} credits {credits_before}->{credits_after} "
                f"stake={stake_units}u staked={staked_seen} closed={closed}{scan_note}"
            )
            results.append(
                RouletteRoundResult(
                    round_index=i,
                    ok=True,
                    message=msg,
                    chip=chip,
                    bets=bets,
                    staked_seen=staked_seen,
                    credits_before=credits_before,
                    credits_after=credits_after,
                    setchip_hits=setchip_hits,
                    strategy=sid,
                    stake_units=stake_units,
                    won=won,
                )
            )
            _progress(progress, f"Round {i}: OK - {msg}")
        else:
            msg = (
                f"no game reaction (credits {credits_before}->{credits_after} "
                f"stake={stake_units}u staked={staked_seen} closed={closed})"
            )
            results.append(
                RouletteRoundResult(
                    round_index=i,
                    ok=False,
                    message=msg,
                    chip=chip,
                    bets=bets,
                    staked_seen=staked_seen,
                    credits_before=credits_before,
                    credits_after=credits_after,
                    setchip_hits=setchip_hits,
                    strategy=sid,
                    stake_units=stake_units,
                    won=won,
                )
            )
            _progress(progress, f"Round {i}: FAIL - {msg}")
            # Misclick recovery: spiral-search the primary spot and persist layout1 cal.
            if plan is not None and sid not in ("random", "full_board") and plan.placements:
                try:
                    from automation.roulette_board_mapper import auto_adjust_misclick

                    spot = plan.placements[0].spot
                    _progress(
                        progress,
                        f"Round {i}: auto-adjust misclick on {spot.name} ...",
                    )
                    adj = auto_adjust_misclick(
                        ip=ip,
                        target=spot,
                        layout_id="layout1",
                        progress=progress,
                    )
                    _progress(
                        progress,
                        f"Round {i}: adjust {'OK' if adj.ok else 'FAIL'} "
                        f"{adj.name} @ {adj.x_pct:.2f},{adj.y_pct:.2f} "
                        f"delta={adj.credit_delta}",
                    )
                except Exception as exc:  # noqa: BLE001
                    _progress(progress, f"Round {i}: auto-adjust error: {exc}")

        # Wait until closed so the next round starts on a fresh open.
        if not closed:
            offset, _, detail = wait_for_log_marker(
                roulette_log,
                start_offset=offset,
                pattern=_CLOSED_RE,
                timeout_sec=40.0,
            )
            _progress(progress, f"Round {i}: wait closed - {detail}")

    return results


def write_roulette_results_jsonl(results: list[RouletteRoundResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            row = {
                "ok": r.ok,
                "message": r.message,
                "round_index": r.round_index,
                "chip": r.chip,
                "bets": r.bets,
                "staked_seen": r.staked_seen,
                "credits_before": r.credits_before,
                "credits_after": r.credits_after,
                "setchip_hits": r.setchip_hits,
                "strategy": r.strategy,
                "stake_units": r.stake_units,
                "won": r.won,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
