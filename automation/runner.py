from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from automation.gamedata_parser import GameDataSpin, iter_spins
from automation.input_script import script_for_forced_combo
from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.theme_loader import ThemeBetConfig, list_installed_themes, load_theme_bet_config
from live_tail_io import read_new_bytes


@dataclass(frozen=True, slots=True)
class SpinCase:
    theme_id: str
    bet_multiplier: int
    combo_text: str


@dataclass(frozen=True, slots=True)
class SpinCheckResult:
    case: SpinCase
    ok: bool
    message: str
    spin: GameDataSpin | None = None


def _latest_file(path: Path) -> Path | None:
    files = [p for p in path.iterdir() if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _read_new_lines(path: Path, *, start_offset: int, max_bytes: int = 2_000_000) -> tuple[int, list[str]]:
    end = path.stat().st_size
    if end <= start_offset:
        return end, []
    to_read = min(end - start_offset, max_bytes)
    blob = read_new_bytes(path, start_offset, start_offset + to_read)
    try:
        txt = blob.decode("utf-8", errors="replace")
    except Exception:
        txt = blob.decode("latin-1", errors="replace")
    lines = txt.replace("\r\n", "\n").splitlines()
    return start_offset + to_read, lines


def wait_for_next_spin(
    gamedata_log: Path,
    *,
    start_offset: int,
    earliest_ts: datetime | None,
    theme_id: str,
    timeout_sec: float = 25.0,
) -> tuple[int, GameDataSpin | None, str]:
    """
    After we trigger a spin, poll the GameData log for the next matching spin record.
    """

    deadline = time.time() + timeout_sec
    offset = start_offset
    last_seen: GameDataSpin | None = None

    while time.time() < deadline:
        try:
            offset, lines = _read_new_lines(gamedata_log, start_offset=offset)
        except OSError as e:
            return offset, None, f"gamedata read failed: {e}"

        for ev in iter_spins(lines):
            last_seen = ev
            if ev.game != theme_id:
                continue
            if earliest_ts is not None and ev.timestamp < earliest_ts:
                continue
            return offset, ev, "ok"

        time.sleep(0.35)

    if last_seen is not None:
        return offset, None, f"timeout waiting for spin (last gamedata={last_seen.game} spin_id={last_seen.spin_id})"
    return offset, None, "timeout waiting for spin (no new gamedata parsed)"


def run_matrix(
    *,
    ip: str,
    themes_root_unc: Path,
    gamedata_dir_unc: Path,
    themes: list[str] | None,
    forced_combos: list[str],
    session: int = 1,
    focus_process: str = "OneHand",
) -> list[SpinCheckResult]:
    """
    Run the automation matrix.

    Current scope: validate that the forced combo produces a deterministic GameData spin record
    for each bet step. (Paytable mapping from .thm is not available in XML, so this stage focuses
    on exact match of GameData win field + total per bet step.)
    """

    installed = list_installed_themes(themes_root_unc)
    theme_dirs = [p for p in installed if themes is None or p.name in set(themes)]
    if not theme_dirs:
        raise ValueError("No themes selected / found.")

    log_file = _latest_file(gamedata_dir_unc)
    if log_file is None:
        raise FileNotFoundError(f"No GameData logs found under {gamedata_dir_unc}")

    # Build and stage the cabinet agent once.
    local_exe = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=ip, local_exe=local_exe)

    results: list[SpinCheckResult] = []

    for theme_dir in theme_dirs:
        cfg: ThemeBetConfig = load_theme_bet_config(theme_dir)
        for bet_mult in cfg.bet_multipliers:
            for combo in forced_combos:
                case = SpinCase(theme_id=theme_dir.name, bet_multiplier=bet_mult, combo_text=combo)

                # TODO: actually change bet step on cabinet via key script.
                # For now, we assume the operator has the bet step set, or forced scripts include bet change.
                script = script_for_forced_combo(combo_text=combo)

                start_offset = log_file.stat().st_size
                earliest = datetime.now().astimezone() - timedelta(seconds=2)

                ok, msg = run_input_script_on_cabinet(
                    ip=ip,
                    agent=staged,
                    script=script,
                    focus_process=focus_process,
                    session=session,
                    timeout=60,
                )
                if not ok:
                    results.append(SpinCheckResult(case=case, ok=False, message=f"input agent failed: {msg}", spin=None))
                    continue

                start_offset, spin, why = wait_for_next_spin(
                    log_file,
                    start_offset=start_offset,
                    earliest_ts=earliest,
                    theme_id=theme_dir.name,
                    timeout_sec=25.0,
                )
                if spin is None:
                    results.append(SpinCheckResult(case=case, ok=False, message=why, spin=None))
                    continue

                # Minimal validation: total win matches the sum of embedded win totals when available.
                embedded = [w.total for w in spin.wins if w.total is not None]
                if spin.total_win_credits is not None and embedded:
                    if sum(embedded) != spin.total_win_credits:
                        results.append(
                            SpinCheckResult(
                                case=case,
                                ok=False,
                                message=f"win mismatch: sum(tokens)={sum(embedded)} total={spin.total_win_credits}",
                                spin=spin,
                            )
                        )
                        continue

                results.append(SpinCheckResult(case=case, ok=True, message="ok", spin=spin))

    return results


def write_results_jsonl(results: list[SpinCheckResult], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            row = {
                "ok": r.ok,
                "message": r.message,
                "case": {
                    "theme_id": r.case.theme_id,
                    "bet_multiplier": r.case.bet_multiplier,
                    "combo_text": r.case.combo_text,
                },
                "spin": None
                if r.spin is None
                else {
                    "ts": r.spin.timestamp.isoformat(),
                    "game": r.spin.game,
                    "spin_id": r.spin.spin_id,
                    "bet_credits": r.spin.bet_credits,
                    "total_win_credits": r.spin.total_win_credits,
                    "wins": [w.raw for w in r.spin.wins],
                    "raw": r.spin.raw_line,
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

