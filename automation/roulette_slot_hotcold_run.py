"""
Continuous Slot Roulette hot/cold stake test.

Default: N hot spins, then N cold spins.

Alternate forever (one hot round, one cold round, repeat)::

    python -m automation.roulette_slot_hotcold_run --ip 10.0.0.90 --alternate --rounds 0

Before each spin it:

1. Ensures HISTORY is closed
2. Reads UI hot/cold from the cloth flash (HOT_COLD pill)
3. Compares to ``hot_cold_from_history`` on a rolling last-100 list
4. CANCEL_ALL → chip_20 → click the 5 targets → SPIN
5. Waits until credits or LAST NUMBER meter changes (round settled)
6. Prepends the new LAST NUMBER into the rolling history

History is seeded from ``_tmp_logs/slot_roulette/history/last100_live.json``
(vision transcript) or a fresh HISTORY panel OCR — never from strip OCR.

    python -m automation.roulette_slot_hotcold_run --ip 10.0.0.90 --spins 10
"""

from __future__ import annotations

import argparse
import random
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from automation.remote_input_agent import (
    build_input_agent_local,
    capture_client_screenshot,
    stage_input_agent,
)
from automation.roulette_layout_store import resolve_hitbox_center
from automation.roulette_slot_areas import PANEL_Y, STATS_PANEL_AREAS_PX
from automation.roulette_slot_history import (
    HISTORY_COUNT,
    close_history_steps,
    hot_cold_from_history,
    open_history_steps,
    read_history_numbers,
)
from automation.roulette_slot_hotcold import (
    hot_cold_click_spec,
    read_hot_cold_from_cloth,
)

IP_DEFAULT = "10.0.0.90"
FOCUS = "OneHand"
OUT = Path("_tmp_logs/slot_roulette/hotcold_run")
LIVE_SEED = Path("_tmp_logs/slot_roulette/history/last100_live.json")
SPIN_WAIT_S = 20.0
BET_SETTLE_MS = 450
FLASH_SETTLE_MS = 500
METER_POLL_S = 1.5
METER_TIMEOUT_S = 90.0
# Cap PNG growth: each round writes several shots; keep only the newest N.
KEEP_SCREENSHOTS = 10
CHIP_STAKE = 20

# Throughput mode (soak --fast): spin in the same InputAgent trip as bets —
# no screenshot between last chip and SPIN.
FAST_MODE = False
FAST_FOCUS_MS = 80
FAST_CANCEL_MS = 220
FAST_CHIP_MS = 160
FAST_BET_MS = 120
FAST_SPIN_MS = 60
FAST_FLASH_SETTLE_MS = 280
FAST_METER_POLL_S = 0.65
FAST_METER_TIMEOUT_S = 55.0
FAST_SPIN_WAIT_S = 10.0
FAST_KEEP_SCREENSHOTS = 3
AGGRESSIVE_MODE = False
AGGRESSIVE_BET_COUNT = 5
AGGRESSIVE_FOCUS_MS = 50
AGGRESSIVE_CANCEL_MS = 140
AGGRESSIVE_CHIP_MS = 100
AGGRESSIVE_BET_MS = 70
AGGRESSIVE_SPIN_MS = 40
AGGRESSIVE_METER_POLL_S = 0.45
AGGRESSIVE_METER_TIMEOUT_S = 40.0
AGGRESSIVE_SPIN_WAIT_S = 8.0


def slot_straight_ids() -> list[str]:
    try:
        from automation.roulette_layout_store import load_hitboxes

        hb = load_hitboxes("slot") or {}
        buttons = hb.get("buttons") if isinstance(hb, dict) else None
        src = buttons if isinstance(buttons, dict) else hb
        ids = [str(k) for k in src if str(k).isdigit() or str(k) == "00"]
        if ids:
            return sorted(ids, key=lambda x: (len(x), x))
    except Exception:
        pass
    return [str(n) for n in range(37)]


def random_straight_targets(count: int | None = None) -> list[str]:
    pool = slot_straight_ids()
    n = int(count or AGGRESSIVE_BET_COUNT)
    n = max(1, min(n, len(pool)))
    return sorted(random.sample(pool, n), key=lambda x: (len(x), x))




def read_credit_meter(ip: str) -> int | None:
    """Live credit meter from DeviceManager state (preferred) or SlotLog."""
    try:
        from network.accounting_state_loader import load_machine_accounting_state_pure

        state = load_machine_accounting_state_pure(rf"\\{ip}\c$\Goldclub\var\log")
        raw = state.get("currentcredits") or state.get("CurrentCredits")
        if raw is not None and str(raw).strip() != "":
            return int(float(str(raw).strip()))
    except Exception:
        pass
    try:
        from automation.cabinet_preflight import read_cabinet_balance_credits

        return read_cabinet_balance_credits(ip)
    except Exception:
        return None


def wait_for_meter_change(
    *,
    ip: str,
    agent: Any,
    work: Path,
    tag: str,
    before_credits: int | None,
    before_last: int | None,
    poll_s: float = METER_POLL_S,
    timeout_s: float = METER_TIMEOUT_S,
    prefer_last_number: bool = False,
) -> dict[str, Any]:
    """Poll until credits or LAST NUMBER changes after SPIN (round settled).

    Credits are polled every cycle (fast UNC state read). LAST NUMBER OCR runs
    every poll when *prefer_last_number* is set (Slot Roulette often only moves
    DeviceManager credits after the wheel stops); otherwise every 3rd poll.
    """
    t0 = time.time()
    last_seen = before_last
    credits_seen = before_credits
    polls = 0
    while True:
        elapsed = time.time() - t0
        if elapsed >= timeout_s:
            return {
                "ok": False,
                "reason": "timeout",
                "elapsed_s": round(elapsed, 2),
                "credits": credits_seen,
                "last": last_seen,
                "polls": polls,
            }
        time.sleep(max(0.2, float(poll_s)))
        polls += 1
        credits_seen = read_credit_meter(ip)
        credit_changed = (
            before_credits is not None
            and credits_seen is not None
            and int(credits_seen) != int(before_credits)
        )
        do_ocr = prefer_last_number or (polls % 3 == 0) or credit_changed
        if do_ocr:
            try:
                shot = _grab(
                    agent,
                    work
                    / (
                        f"{tag}_meter_done.png"
                        if credit_changed
                        else f"{tag}_meter_{polls:02d}.png"
                    ),
                    [_focus()],
                    250,
                )
                last_seen = read_last_number_mid(Image.open(shot))
            except Exception:
                if not credit_changed:
                    last_seen = before_last
        last_changed = (
            before_last is not None
            and last_seen is not None
            and int(last_seen) != int(before_last)
        )
        if last_changed:
            return {
                "ok": True,
                "reason": "last_number",
                "elapsed_s": round(time.time() - t0, 2),
                "credits_before": before_credits,
                "credits": credits_seen,
                "last_before": before_last,
                "last": last_seen,
                "polls": polls,
            }
        if credit_changed:
            return {
                "ok": True,
                "reason": "credits",
                "elapsed_s": round(time.time() - t0, 2),
                "credits_before": before_credits,
                "credits": credits_seen,
                "last_before": before_last,
                "last": last_seen,
                "polls": polls,
            }

_IP = IP_DEFAULT
_OCR = None


def _spec(name: str) -> str:
    t = resolve_hitbox_center(name, layout_id="slot")
    if t is None:
        raise KeyError(name)
    return t.as_spec(FOCUS)


def _focus(ms: int | None = None) -> dict:
    if ms is None:
        ms = FAST_FOCUS_MS if FAST_MODE else 300
    return {"type": "focus_process", "value": FOCUS, "ms": int(ms)}


def _click(name: str, ms: int = BET_SETTLE_MS) -> dict:
    return {"type": "click", "value": _spec(name), "ms": ms}


def _run_steps(agent: Any, steps: list[dict], *, timeout: int = 120) -> None:
    """Run InputAgent clicks with no screenshot (keeps SPIN tight after bets)."""
    from automation.remote_input_agent import run_input_script_on_cabinet

    ok, msg = run_input_script_on_cabinet(
        ip=_IP,
        agent=agent,
        script={"defaultKeyDelayMs": 15 if FAST_MODE else 30, "steps": list(steps)},
        focus_process=FOCUS,
        timeout=timeout,
    )
    if not ok:
        raise RuntimeError(f"input steps failed: {msg}")


def _grab(agent: Any, dest: Path, steps: list[dict], settle_ms: int = 400) -> Path:
    """Capture with retries for flaky WinRM/schtasks clock warnings."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_msg = ""
    for attempt in range(1, 4):
        ok, msg = capture_client_screenshot(
            ip=_IP,
            agent=agent,
            dest=dest,
            focus_process=FOCUS,
            settle_ms=settle_ms,
            steps=steps,
            timeout=120,
        )
        if ok and dest.exists() and dest.stat().st_size > 1000:
            return dest
        last_msg = msg or "unknown"
        transient = (
            "/ST is earlier" in last_msg
            or "Task may not run" in last_msg
            or "WinRM" in last_msg
            or "timed out" in last_msg.lower()
        )
        print(
            f"  WARN: capture attempt {attempt}/3 failed: {last_msg[:200]}",
            flush=True,
        )
        if not transient or attempt >= 3:
            break
        time.sleep(2.0 * attempt)
    raise RuntimeError(f"capture failed: {last_msg}")


def _ocr_reader():
    global _OCR
    if _OCR is None:
        import easyocr

        _OCR = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _OCR


def mid_panel(full: Image.Image) -> Image.Image:
    if full.height >= 3000:
        return full.crop((0, 1080, 1920, 2160))
    if full.height >= 2000:
        return full.crop((0, full.height - 2160, 1920, full.height - 1080))
    return full.crop((0, 0, min(1920, full.width), min(1080, full.height)))


def read_last_number_mid(full: Image.Image) -> int | None:
    """OCR the large LAST NUMBER circle on the mid panel."""
    mid = mid_panel(full)
    # STATS box is full-client; convert to mid-local.
    x0, y0, x1, y1 = STATS_PANEL_AREAS_PX["LAST_NUMBER"]
    y0, y1 = y0 - 1080, y1 - 1080
    # Tighten onto the circle (left side of the LAST NUMBER label).
    crop = mid.crop((x0, y0 + 40, x0 + 220, y1 - 40))
    up = np.asarray(crop.resize((crop.width * 2, crop.height * 2), Image.BICUBIC))
    hits = _ocr_reader().readtext(up, allowlist="0123456789", detail=1)
    best: tuple[float, int | None] = (0.0, None)
    for _box, text, conf in hits:
        text = str(text).strip()
        if not text.isdigit():
            continue
        n = int(text)
        if 0 <= n <= 36 and float(conf) > best[0]:
            best = (float(conf), n)
    return best[1] if best[0] >= 0.5 else None


def ensure_history_closed(agent: Any, work: Path) -> None:
    """Click CLOSE then verify cloth is visible (no HISTORY title band)."""
    _grab(agent, work / "close_try.png", close_history_steps(focus=FOCUS), settle_ms=600)
    # Second click is cheap if already closed (hits felt).
    shot = _grab(agent, work / "close_check.png", [_focus()], 300)
    bot = shot if False else Image.open(shot).convert("RGB")
    if bot.height >= 3000:
        panel = bot.crop((0, PANEL_Y, 1920, PANEL_Y + 1080))
    else:
        panel = bot
    # HISTORY title zone turns very green/flat when open; cloth has number grid.
    title = np.asarray(panel.crop((700, 100, 1220, 180)))
    # If modal open, title area is solid green panel — low edge density.
    gray = title.mean(axis=2)
    edges = np.abs(np.diff(gray.astype(np.float32), axis=1)).mean()
    if edges < 2.0:
        _grab(agent, work / "close_retry.png", close_history_steps(focus=FOCUS), settle_ms=700)


def history_modal_open(full: Image.Image) -> bool:
    """True when the LAST 100 modal is covering the cloth."""
    if full.height >= 3000:
        panel = full.crop((0, PANEL_Y, 1920, PANEL_Y + 1080))
    else:
        panel = full
    close = np.asarray(panel.crop((880, 790, 1040, 870)))
    r, g, b = close[:, :, 0], close[:, :, 1], close[:, :, 2]
    glow = ((g > 180) & (g > r + 40) & (g > b + 40)).mean()
    return float(glow) > 0.02


def snapshot_history(agent: Any, work: Path, tag: str) -> list[int]:
    shot = _grab(
        agent,
        work / f"{tag}_history.png",
        open_history_steps(focus=FOCUS),
        settle_ms=1200,
    )
    im = Image.open(shot).convert("RGB")
    if not history_modal_open(im):
        shot = _grab(
            agent,
            work / f"{tag}_history2.png",
            open_history_steps(focus=FOCUS),
            settle_ms=1200,
        )
        im = Image.open(shot).convert("RGB")
    if not history_modal_open(im):
        ensure_history_closed(agent, work / f"{tag}_close")
        raise RuntimeError("HISTORY modal did not open")
    read = read_history_numbers(im)
    ensure_history_closed(agent, work / f"{tag}_close")
    nums = [int(n) for n in read.numbers[:HISTORY_COUNT]]
    print(
        f"  history[{tag}] source={read.source} head={nums[:5]} len={len(nums)}",
        flush=True,
    )
    return nums


def seed_history(agent: Any) -> list[int]:
    boot = _grab(agent, OUT / "boot_screen.png", [_focus()], 400)
    last = read_last_number_mid(Image.open(boot))
    if LIVE_SEED.is_file():
        nums = [
            int(n)
            for n in json.loads(LIVE_SEED.read_text(encoding="utf-8"))[
                "numbers_newest_first"
            ]
        ][:HISTORY_COUNT]
        if last is not None and nums and int(nums[0]) == int(last):
            print(f"seed live-json head={nums[:5]} last={last}", flush=True)
            return nums
        if last is not None and nums:
            print(
                f"seed live-json stale (json={nums[0]} last={last}) -> prepend last",
                flush=True,
            )
            return [int(last)] + list(nums)[: HISTORY_COUNT - 1]
        print(f"seed live-json last={last} head={nums[:5]}", flush=True)
        return nums
    print(f"seed HISTORY OCR (no live-json), last={last}", flush=True)
    return snapshot_history(agent, OUT / "boot", "seed")


def reveal_ui_sets(agent: Any, work: Path, tag: str) -> tuple[set[str], set[str]]:
    ensure_history_closed(agent, work / f"{tag}_pre")
    flash_ms = FAST_FLASH_SETTLE_MS if FAST_MODE else FLASH_SETTLE_MS
    before = _grab(agent, work / f"{tag}_before.png", [_focus()], 200 if FAST_MODE else 300)
    during = _grab(
        agent,
        work / f"{tag}_during.png",
        [
            _focus(),
            {
                "type": "click",
                "value": hot_cold_click_spec(FOCUS),
                "ms": 220 if FAST_MODE else 400,
            },
        ],
        settle_ms=flash_ms,
    )
    sets = read_hot_cold_from_cloth(during, before=before)
    return set(sets.hot), set(sets.cold)


def prune_run_screenshots(work: Path, keep: int = KEEP_SCREENSHOTS) -> int:
    """Delete oldest PNGs in *work* so at most *keep* remain (newest kept)."""
    keep = max(0, int(keep))
    pngs = sorted(
        (p for p in work.glob("*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in pngs[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def place_bets(agent: Any, work: Path, targets: list[str], tag: str) -> Path:
    """CANCEL → chip → target cells. Does not spin (capture shows chips on cloth)."""
    steps = [_focus(), _click("CANCEL_ALL", 700), _click("chip_20", 550)]
    for n in targets:
        steps.append(_click(n, 420))
    return _grab(agent, work / f"{tag}_bets.png", steps, settle_ms=600)


def click_spin(agent: Any, work: Path, tag: str) -> Path:
    return _grab(
        agent,
        work / f"{tag}_spin.png",
        [_focus(), _click("SPIN", 900)],
        settle_ms=500,
    )


def bet_and_spin_steps(targets: list[str]) -> list[dict]:
    """CANCEL → chip → targets → SPIN in one script (SPIN delay is minimal when fast)."""
    if FAST_MODE or AGGRESSIVE_MODE:
        focus_ms = AGGRESSIVE_FOCUS_MS if AGGRESSIVE_MODE else FAST_FOCUS_MS
        cancel_ms = AGGRESSIVE_CANCEL_MS if AGGRESSIVE_MODE else FAST_CANCEL_MS
        chip_ms = AGGRESSIVE_CHIP_MS if AGGRESSIVE_MODE else FAST_CHIP_MS
        bet_ms = AGGRESSIVE_BET_MS if AGGRESSIVE_MODE else FAST_BET_MS
        spin_ms = AGGRESSIVE_SPIN_MS if AGGRESSIVE_MODE else FAST_SPIN_MS
        steps = [
            _focus(focus_ms),
            _click("CANCEL_ALL", cancel_ms),
            _click("chip_20", chip_ms),
        ]
        for n in targets:
            steps.append(_click(n, bet_ms))
        steps.append(_click("SPIN", spin_ms))
        return steps
    steps = [_focus(), _click("CANCEL_ALL", 700), _click("chip_20", 550)]
    for n in targets:
        steps.append(_click(n, 420))
    steps.append(_click("SPIN", 400))
    return steps


def place_bets_and_spin(agent: Any, work: Path, targets: list[str], tag: str) -> Path | None:
    """Place bets and press SPIN in one InputAgent round-trip.

    Fast mode skips the post-spin proof screenshot so the wheel starts sooner.
    """
    steps = bet_and_spin_steps(targets)
    if FAST_MODE:
        _run_steps(agent, steps)
        print(
            f"  fast bet+spin targets={targets} spin_ms={(AGGRESSIVE_SPIN_MS if AGGRESSIVE_MODE else FAST_SPIN_MS)}",
            flush=True,
        )
        return None
    return _grab(agent, work / f"{tag}_spin.png", steps, settle_ms=350)


def place_and_spin(agent: Any, work: Path, targets: list[str], tag: str) -> None:
    """Click CANCEL → chip → targets → SPIN. Caller waits for meter change."""
    place_bets_and_spin(agent, work, targets, tag)


def place_spin_and_wait_meters(
    *,
    agent: Any,
    work: Path,
    targets: list[str],
    tag: str,
    ip: str,
    poll_s: float = METER_POLL_S,
    timeout_s: float = METER_TIMEOUT_S,
    fallback_sleep_s: float = SPIN_WAIT_S,
    before_last: int | None = None,
) -> dict[str, Any]:
    """Place bets, SPIN, then wait until the round actually settles.

    On Slot Roulette the DeviceManager credit meter often does **not** drop when
    chips land (UI BET updates, accounting stays flat until the spin resolves).
    So we must not treat "credits unchanged after clicks" as a missed bet, and we
    wait on LAST NUMBER (primary) with credit change as a secondary signal from
    the pre-bet baseline.
    """
    if FAST_MODE:
        poll_s = FAST_METER_POLL_S if poll_s == METER_POLL_S else poll_s
        timeout_s = FAST_METER_TIMEOUT_S if timeout_s == METER_TIMEOUT_S else timeout_s
        fallback_sleep_s = (
            FAST_SPIN_WAIT_S if fallback_sleep_s == SPIN_WAIT_S else fallback_sleep_s
        )

    before_credits = read_credit_meter(ip)
    if before_last is None:
        before_shot = _grab(
            agent,
            work / f"{tag}_before_spin.png",
            [_focus()],
            200 if FAST_MODE else 300,
        )
        before_last = read_last_number_mid(Image.open(before_shot))
    print(
        f"  meters before spin credits={before_credits} last={before_last} "
        f"targets={targets} fast={FAST_MODE}",
        flush=True,
    )
    place_bets_and_spin(agent, work, targets, tag)
    post_bet_credits = before_credits if FAST_MODE else read_credit_meter(ip)
    if not FAST_MODE:
        print(
            f"  bets+spin done credits={before_credits}->{post_bet_credits}",
            flush=True,
        )
    settled = wait_for_meter_change(
        ip=ip,
        agent=agent,
        work=work,
        tag=tag,
        before_credits=before_credits,
        before_last=before_last,
        poll_s=poll_s,
        timeout_s=timeout_s,
        prefer_last_number=True,
    )
    settled["credits_pre_bet"] = before_credits
    settled["credits_post_bet"] = post_bet_credits
    if not settled.get("ok"):
        print(
            f"  WARN: meter wait {settled.get('reason')} after {settled.get('elapsed_s')}s "
            f"— falling back to sleep {fallback_sleep_s:.0f}s",
            flush=True,
        )
        time.sleep(max(0.0, float(fallback_sleep_s)))
        after = _grab(agent, work / f"{tag}_after_fallback.png", [_focus()], 400)
        settled = {
            **settled,
            "ok": False,
            "fallback_sleep_s": fallback_sleep_s,
            "credits": read_credit_meter(ip),
            "last": read_last_number_mid(Image.open(after)),
        }
    else:
        print(
            f"  meters settled via {settled.get('reason')} in {settled.get('elapsed_s')}s "
            f"credits={settled.get('credits_before')}->{settled.get('credits')} "
            f"last={settled.get('last_before')}->{settled.get('last')}",
            flush=True,
        )
    return settled


def run_block(
    *,
    agent: Any,
    mode: str,
    spins: int,
    history: list[int],
) -> tuple[dict[str, Any], list[int]]:
    block_dir = OUT / f"{mode}_{time.strftime('%H%M%S')}"
    block_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    matches = 0

    for i in range(1, spins + 1):
        tag = f"{i:02d}"
        print(f"[{mode}] spin {i}/{spins} ...", flush=True)
        ui_hot, ui_cold = reveal_ui_sets(agent, block_dir, f"{tag}_ui")
        freq = hot_cold_from_history(history)
        freq_hot, freq_cold = set(freq.hot), set(freq.cold)
        ok = ui_hot == freq_hot and ui_cold == freq_cold
        matches += int(ok)
        targets = sorted(ui_hot if mode == "hot" else ui_cold, key=int)
        print(
            f"  ui hot={sorted(ui_hot, key=int)} cold={sorted(ui_cold, key=int)} "
            f"freq hot={sorted(freq_hot, key=int)} cold={sorted(freq_cold, key=int)} "
            f"match={ok} stake={targets}",
            flush=True,
        )
        settled = place_spin_and_wait_meters(
            agent=agent,
            work=block_dir,
            targets=targets,
            tag=tag,
            ip=_IP,
            before_last=int(history[0]) if history else None,
        )
        last = settled.get("last")
        if last is None:
            after = _grab(agent, block_dir / f"{tag}_after.png", [_focus()], 500)
            last = read_last_number_mid(Image.open(after))
        if last is not None:
            if not history or int(history[0]) != int(last):
                history = [int(last)] + list(history)
                history = history[:HISTORY_COUNT]
        else:
            print("  WARN: last-number OCR missed", flush=True)
        row = {
            "i": i,
            "mode": mode,
            "match": ok,
            "ui_hot": sorted(ui_hot, key=int),
            "ui_cold": sorted(ui_cold, key=int),
            "freq_hot": sorted(freq_hot, key=int),
            "freq_cold": sorted(freq_cold, key=int),
            "targets": targets,
            "result": last,
            "credits": settled.get("credits"),
            "meter_wait": {
                k: settled.get(k)
                for k in ("ok", "reason", "elapsed_s", "credits_before", "last_before")
            },
            "history_head": history[:5],
        }
        rows.append(row)
        (block_dir / f"{tag}_result.json").write_text(
            json.dumps(row, indent=2) + "\n", encoding="utf-8"
        )
        prune_run_screenshots(block_dir, KEEP_SCREENSHOTS)

    summary = {
        "mode": mode,
        "spins": spins,
        "matches": matches,
        "mismatches": spins - matches,
        "pass": matches == spins,
        "rows": rows,
    }
    (block_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[{mode}] done matches={matches}/{spins} pass={summary['pass']}",
        flush=True,
    )
    return summary, history


def next_alternate_mode(round_index: int) -> str:
    """Round 1 -> hot, 2 -> cold, 3 -> hot, ... (1-based)."""
    return "hot" if (int(round_index) % 2) == 1 else "cold"


def run_alternate(
    *,
    agent: Any,
    history: list[int],
    rounds: int = 0,
    poll_s: float = METER_POLL_S,
    timeout_s: float = METER_TIMEOUT_S,
    strategy: str = "hotcold",
) -> dict[str, Any]:
    """One hot round, one cold round, forever (rounds=0) or until N rounds."""
    run_dir = OUT / f"alternate_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "rounds.jsonl"
    print(
        f"[alternate] start rounds={'infinity' if rounds <= 0 else rounds} "
        f"dir={run_dir}",
        flush=True,
    )
    i = 0
    matches = 0
    while True:
        i += 1
        if rounds > 0 and i > rounds:
            break
        mode = "random" if strategy == "random" else next_alternate_mode(i)
        tag = f"{i:04d}_{mode}"
        print(f"[alternate] round {i} mode={mode} strategy={strategy} ...", flush=True)
        ui_hot: set[int] = set()
        ui_cold: set[int] = set()
        freq_hot: set[int] = set()
        freq_cold: set[int] = set()
        ok = True
        if strategy == "random":
            targets = random_straight_targets()
            print(f"  random stake={targets}", flush=True)
        else:
            ui_hot, ui_cold = reveal_ui_sets(agent, run_dir, f"{tag}_ui")
            freq = hot_cold_from_history(history)
            freq_hot, freq_cold = set(freq.hot), set(freq.cold)
            ok = ui_hot == freq_hot and ui_cold == freq_cold
            matches += int(ok)
            targets = sorted(ui_hot if mode == "hot" else ui_cold, key=int)
            print(
                f"  ui hot={sorted(ui_hot, key=int)} cold={sorted(ui_cold, key=int)} "
                f"freq match={ok} stake={targets}",
                flush=True,
            )
        if not targets:
            print("  WARN: empty target set — skipping SPIN", flush=True)
            row = {
                "i": i,
                "mode": mode,
                "skipped": True,
                "reason": "empty_targets",
                "ui_hot": sorted(ui_hot, key=int),
                "ui_cold": sorted(ui_cold, key=int),
            }
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            continue
        settled = place_spin_and_wait_meters(
            agent=agent,
            work=run_dir,
            targets=targets,
            tag=tag,
            ip=_IP,
            poll_s=poll_s,
            timeout_s=timeout_s,
            before_last=int(history[0]) if history else None,
        )
        last = settled.get("last")
        if last is None:
            after = _grab(agent, run_dir / f"{tag}_after.png", [_focus()], 500)
            last = read_last_number_mid(Image.open(after))
        if last is not None:
            if not history or int(history[0]) != int(last):
                history = [int(last)] + list(history)
                history = history[:HISTORY_COUNT]
        row = {
            "i": i,
            "mode": mode,
            "match": ok,
            "ui_hot": sorted(ui_hot, key=int),
            "ui_cold": sorted(ui_cold, key=int),
            "freq_hot": sorted(freq_hot, key=int),
            "freq_cold": sorted(freq_cold, key=int),
            "targets": targets,
            "result": last,
            "credits": settled.get("credits"),
            "credits_pre_bet": settled.get("credits_pre_bet"),
            "credits_post_bet": settled.get("credits_post_bet"),
            "meter_wait": {
                k: settled.get(k)
                for k in ("ok", "reason", "elapsed_s", "credits_before", "last_before")
            },
            "history_head": history[:5],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "fast": FAST_MODE,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        (run_dir / f"{tag}_result.json").write_text(
            json.dumps(row, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"  done result={last} credits={settled.get('credits')} "
            f"meter_ok={settled.get('ok')}",
            flush=True,
        )
        keep_n = FAST_KEEP_SCREENSHOTS if FAST_MODE else KEEP_SCREENSHOTS
        pruned = prune_run_screenshots(run_dir, keep_n)
        if pruned:
            print(f"  pruned {pruned} old screenshots (keep={KEEP_SCREENSHOTS})", flush=True)
    summary = {
        "mode": "alternate",
        "rounds_done": i if rounds <= 0 else min(i, rounds),
        "matches": matches,
        "dir": str(run_dir),
        "log": str(log_path),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    global _IP, SPIN_WAIT_S, METER_POLL_S, METER_TIMEOUT_S, FAST_MODE
    p = argparse.ArgumentParser(description="Slot Roulette hot/cold continuous stake test")
    p.add_argument("--ip", default=IP_DEFAULT)
    p.add_argument("--spins", type=int, default=10, help="Spins per mode (hot then cold)")
    p.add_argument("--spin-wait", type=float, default=SPIN_WAIT_S,
                   help="Fallback sleep if meter wait times out")
    p.add_argument(
        "--alternate",
        action="store_true",
        help="Alternate one hot round, one cold round (see --rounds)",
    )
    p.add_argument(
        "--rounds",
        type=int,
        default=0,
        help="With --alternate: number of rounds (0 = infinity)",
    )
    p.add_argument("--meter-poll", type=float, default=METER_POLL_S)
    p.add_argument("--meter-timeout", type=float, default=METER_TIMEOUT_S)
    p.add_argument(
        "--fast",
        action="store_true",
        help="Max throughput: bet+SPIN in one trip, short settles, fewer screenshots",
    )
    args = p.parse_args(argv)
    _IP = args.ip
    FAST_MODE = bool(args.fast)
    SPIN_WAIT_S = float(args.spin_wait)
    METER_POLL_S = float(args.meter_poll)
    METER_TIMEOUT_S = float(args.meter_timeout)

    OUT.mkdir(parents=True, exist_ok=True)
    agent = stage_input_agent(
        ip=_IP, local_exe=build_input_agent_local(out_dir=Path("_tmp_logs/input_agent_build"))
    )
    ensure_history_closed(agent, OUT / "boot_close")
    _grab(agent, OUT / "boot_clear.png", [_focus(), _click("CANCEL_ALL", 700)], 400)
    history = seed_history(agent)
    print(f"seed history_len={len(history)} head={history[:5]}", flush=True)

    if args.alternate:
        summary = run_alternate(
            agent=agent,
            history=history,
            rounds=int(args.rounds),
            poll_s=METER_POLL_S,
            timeout_s=METER_TIMEOUT_S,
        )
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    hot_sum, history = run_block(
        agent=agent, mode="hot", spins=args.spins, history=history
    )
    cold_sum, history = run_block(
        agent=agent, mode="cold", spins=args.spins, history=history
    )
    report = {
        "ip": _IP,
        "spins_per_mode": args.spins,
        "hot": {k: hot_sum[k] for k in ("matches", "mismatches", "pass", "spins")},
        "cold": {k: cold_sum[k] for k in ("matches", "mismatches", "pass", "spins")},
        "pass": hot_sum["pass"] and cold_sum["pass"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    out = OUT / f"report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    print(f"report {out}", flush=True)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
