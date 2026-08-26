"""
Verify every mapped bet hitbox against middleware ground truth.

For each mapped bet area we clear the cloth, arm a chip, click the hitbox centre
with InputAgent, then read ``PlayerDataBets.Bets`` from the middleware
(``/api/data/{player}``) and compare the reported bet with what that spot is
supposed to cover. A click that lands one cell off shows up immediately because
the reported number set no longer matches.

Placement only works while betting is open, so each spot waits for the
``Bets are open`` marker in the cabinet ``ruleta Roulette`` log and the bet is
cancelled again right after it is read, which keeps the stake refunded and the
board clean for the next spot.

    python -m automation.roulette_verify_bets --ip 10.0.0.90 --only 17,RED --raw
    python -m automation.roulette_verify_bets --ip 10.0.0.90           # all 50 spots
    python -m automation.roulette_verify_bets --layout layout2         # the Crycle skin
    python -m automation.roulette_verify_bets --layout layout2 --family race

``--layout`` picks which skin's registry is proven; the cabinet must already be
showing that skin, because the script clicks real pixels.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from app_paths import app_tmp_logs_dir
from automation.roulette_bet_catalog import place_bet_for_button
from automation.roulette_layout import ClickTarget
from automation.roulette_layout_store import (
    hitboxes_path as store_hitboxes_path,
    load_hitboxes,
    resolve_hitbox_center,
    resolve_target,
)
from automation.roulette_layout import CHIP_SPOTS
from automation.roulette_middleware import (
    cancel_all_bets,
    fetch_player_state,
    read_state_and_cancel,
    set_chip,
)
from automation.roulette_runner import _OPEN_RE, _latest_file, wait_for_log_marker
from automation.roulette_script import _click
from automation.roulette_surface import DEFAULT_LAYOUT
from automation.roulette_verify_ui import ensure_view

DEFAULT_IP = "10.0.0.90"
OUT_DIR = app_tmp_logs_dir() / "verify_bets"


def out_dir_for(layout_id: str | None = None) -> Path:
    """Results live beside layout1's for continuity; other skins get a subfolder."""
    lid = (layout_id or DEFAULT_LAYOUT).strip().lower()
    return OUT_DIR if lid == DEFAULT_LAYOUT else OUT_DIR / lid


def hitboxes_path_for(layout_id: str | None = None) -> Path:
    return store_hitboxes_path(layout_id or DEFAULT_LAYOUT)


def results_path_for(
    layout_id: str | None = None, *, anchors: bool = False, race: bool = False
) -> Path:
    """Each sweep keeps its own file so one verdict map never overwrites another."""
    name = "results_race.json" if race else "results_anchors.json" if anchors else "results.json"
    return out_dir_for(layout_id) / name

# Measured on .90: "Bets are open" -> "Bets are closing" is 22 s, cycle is 40 s.
# A spot costs ~7 s (one InputAgent run + one combined read/cancel), and a bet
# left standing when the window shuts is played for real, so keep the batch short.
OPEN_WINDOW_SEC = 22.0
SPOT_BUDGET_SEC = 8.0

# Outside bets need a bigger stake than the 1-credit table minimum for straights;
# clicks are silently dropped below it, so skip them instead of burning credits.
OUTSIDE_MIN_CREDITS = 10

RED_NUMBERS = (1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36)
BLACK_NUMBERS = tuple(n for n in range(1, 37) if n not in RED_NUMBERS)

# 00 is number 37 in the PlaceBet grammar / middleware ids.
DOUBLE_ZERO_ID = 37


def _column(base: int) -> tuple[int, ...]:
    return tuple(range(base, 37, 3))


# Mapped id -> exact set of numbers the spot must cover.
OUTSIDE_COVERAGE: dict[str, tuple[int, ...]] = {
    "1-12": tuple(range(1, 13)),
    "13-24": tuple(range(13, 25)),
    "25-36": tuple(range(25, 37)),
    "1-18": tuple(range(1, 19)),
    "19-36": tuple(range(19, 37)),
    "RED": RED_NUMBERS,
    "BLACK": BLACK_NUMBERS,
    "ODD": tuple(n for n in range(1, 37) if n % 2 == 1),
    "EVEN": tuple(n for n in range(1, 37) if n % 2 == 0),
    # Cloth rows top->bottom are 3,6,9.. / 2,5,8.. / 1,4,7.., so the "2 a 1"
    # boxes select columns 3, 2 and 1 respectively.
    "2to1_top": _column(3),
    "2to1_mid": _column(2),
    "2to1_bot": _column(1),
}

_NUM_RE = re.compile(r"\d+")


def expected_numbers(button_id: str) -> tuple[int, ...] | None:
    """Numbers a mapped bet spot must cover, or None when the id is unknown."""
    # Spots on the racetrack view are expected to cover exactly what the same id
    # covers on the big cloth. For the oval that is the claim under test: if a
    # pocket bets neighbours instead, the reported set is wider and the spot comes
    # back as a mismatch rather than a pass.
    button_id = button_id.removeprefix("race_").removeprefix("mini_")
    if button_id in OUTSIDE_COVERAGE:
        return OUTSIDE_COVERAGE[button_id]
    if button_id == "00":
        return (DOUBLE_ZERO_ID,)
    if button_id == "0":
        return (0,)
    if button_id.isdigit():
        return (int(button_id),)
    # Inside bets (splits / streets / corners / six-lines / basket): take the
    # coverage from the documented PlaceBet grammar rather than re-parsing the id.
    spec = place_bet_for_button(button_id)
    if spec:
        nums = [int(part) for part in str(spec[0]).split("+") if part.strip().isdigit()]
        if nums:
            return tuple(sorted(set(nums)))
    return None


def is_outside(button_id: str) -> bool:
    """Whether a spot is an outside bet, on either cloth."""
    return button_id.removeprefix("mini_") in OUTSIDE_COVERAGE


def reported_numbers(bet: dict[str, Any]) -> tuple[int, ...]:
    """Numbers a middleware bet entry covers, read from whichever field carries them."""
    for key in ("Numbers", "Fields", "Ids", "Positions"):
        value = bet.get(key)
        if isinstance(value, list) and value:
            out: list[int] = []
            for item in value:
                if isinstance(item, (int, float)):
                    out.append(int(item))
                elif isinstance(item, str) and item.strip().isdigit():
                    out.append(int(item))
                elif isinstance(item, dict):
                    for sub in ("Id", "Number", "Value"):
                        raw = item.get(sub)
                        if isinstance(raw, (int, float)):
                            out.append(int(raw))
                            break
                        if isinstance(raw, str) and raw.strip().isdigit():
                            out.append(int(raw))
                            break
            if out:
                return tuple(sorted(set(out)))
    raw_id = str(bet.get("Id") or "")
    if raw_id:
        nums = [int(m.group()) for m in _NUM_RE.finditer(raw_id)]
        if nums and ("+" in raw_id or len(nums) == 1):
            return tuple(sorted(set(nums)))
    return ()


# Registry kinds the sweep can prove. Inside-bet anchors are placed on a shared
# cell edge, so a click that drifts registers a straight instead and shows up as a
# mismatch — exactly the signal that says the anchor needs moving.
BET_KINDS = ("bet",)
ANCHOR_KINDS = ("anchor_unverified", "anchor")
# Racetrack pockets live on the other view, so they are swept on their own.
RACE_KINDS = ("race_unverified", "race")

# Inside-bet id prefixes, in the order the sweep walks them.
_ANCHOR_ORDER = ("split", "street", "corner", "sixline", "basket", "trio")

ANCHOR_FAMILIES = _ANCHOR_ORDER
BET_FAMILIES = ("straight", "outside")
# Both live on the racetrack view: the oval's pockets and the small cloth in it.
RACE_FAMILIES = ("race", "mini")
FAMILIES = BET_FAMILIES + ANCHOR_FAMILIES + RACE_FAMILIES


def family_of(button_id: str) -> str:
    """Which bet family a mapped id belongs to (``straight``, ``corner``, ...)."""
    if button_id.startswith("race_"):
        return "race"
    if button_id.startswith("mini_"):
        return "mini"
    if button_id in OUTSIDE_COVERAGE:
        return "outside"
    if button_id in ("0", "00") or button_id.isdigit():
        return "straight"
    return button_id.split("_")[0]


def kinds_for_families(families: Iterable[str]) -> tuple[str, ...]:
    """
    Registry kinds that hold the requested families.

    Bet spots, inside-bet anchors and racetrack pockets are kept in separate
    results files, so one run cannot mix them; asking for two groups at once is a
    usage error rather than a silent half-sweep.
    """
    wanted = [f.strip().lower() for f in families if f.strip()]
    unknown = [f for f in wanted if f not in FAMILIES]
    if unknown:
        raise ValueError(f"unknown bet families {unknown}; use {'|'.join(FAMILIES)}")
    groups = {
        ANCHOR_KINDS: any(f in ANCHOR_FAMILIES for f in wanted),
        BET_KINDS: any(f in BET_FAMILIES for f in wanted),
        RACE_KINDS: any(f in RACE_FAMILIES for f in wanted),
    }
    chosen = [kinds for kinds, used in groups.items() if used]
    if len(chosen) > 1:
        raise ValueError(
            "sweep bet spots, inside-bet anchors or racetrack pockets, not a mix: "
            "their verdicts live in different results files"
        )
    return chosen[0] if chosen else BET_KINDS


def bet_targets(
    ids: Iterable[str] | None = None,
    layout_id: str | None = None,
    *,
    kinds: tuple[str, ...] = BET_KINDS,
    families: Iterable[str] | None = None,
) -> list[str]:
    """Mapped spots of the requested kinds (and families), in cloth reading order."""
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    mapped = [
        bid
        for bid, entry in buttons.items()
        if entry.get("kind") in kinds and entry.get("click_area_pct")
    ]
    if families is not None:
        wanted_families = {f.strip().lower() for f in families if f.strip()}
        mapped = [b for b in mapped if family_of(b) in wanted_families]
    if ids:
        wanted: list[str] = []
        for raw in ids:
            name = raw.strip()
            if name and name not in wanted:
                wanted.append(name)
        missing = [i for i in wanted if i not in mapped]
        if missing:
            raise KeyError(f"not mapped bet spots: {missing}")
        return wanted
    zeros = [b for b in ("0", "00") if b in mapped]
    straights = sorted((b for b in mapped if b.isdigit() and b not in zeros), key=int)
    outside = [b for b in mapped if b in OUTSIDE_COVERAGE]
    anchors = [
        b
        for prefix in _ANCHOR_ORDER
        for b in sorted(m for m in mapped if m.startswith(f"{prefix}_"))
    ]
    # Pockets keep their registry order, which is wheel order, so the sweep walks
    # the oval instead of hopping across it; the small cloth follows in the same
    # reading order as the big one.
    race = [b for b in mapped if b.startswith("race_")]
    mini_all = [b for b in mapped if b.startswith("mini_")]
    mini_zeros = [b for b in ("mini_0", "mini_00") if b in mini_all]
    mini_nums = sorted(
        (b for b in mini_all if b.removeprefix("mini_").isdigit() and b not in mini_zeros),
        key=lambda b: int(b.removeprefix("mini_")),
    )
    mini_outside = [b for b in mini_all if b.removeprefix("mini_") in OUTSIDE_COVERAGE]
    mini = mini_zeros + mini_nums + mini_outside
    return zeros + straights + outside + anchors + race + mini


def _roulette_log(ip: str) -> Path:
    log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if log is None:
        raise FileNotFoundError(f"ruleta Roulette log missing on {ip}")
    return log


def wait_for_open_window(log: Path, *, offset: int, timeout_sec: float = 120.0) -> tuple[int, bool, str]:
    """Block until the next betting window opens; returns the new log offset."""
    offset, matched, detail = wait_for_log_marker(
        log, start_offset=offset, pattern=_OPEN_RE, timeout_sec=timeout_sec
    )
    return offset, matched, detail


def arm_chip(ip: str, *, agent: Any, chip_index: int = 0, layout_id: str | None = None) -> None:
    """Select a chip once per betting window (API + a real click on the tray)."""
    cancel_all_bets(ip)
    set_chip(ip, chip_index)
    chip = resolve_target(CHIP_SPOTS[chip_index], layout_id)
    run_input_script_on_cabinet(
        ip=ip,
        agent=agent,
        script={
            "defaultKeyDelayMs": 30,
            "steps": [
                _click(chip, ms=110, mode="window", calibrate=False),
                {"type": "sleep", "ms": 90},
            ],
        },
        focus_process="godot",
        timeout=60,
    )


def verify_batch(
    ip: str,
    button_ids: list[str],
    *,
    agent: Any,
    settle_sec: float = 0.4,
    layout_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Click one or more spots in a single InputAgent run, then read all bets at once.

    Batching only stays exact for spots covering disjoint numbers (straights):
    every expected number set must show up exactly once in the reply, so a
    matching batch proves each spot individually. Anything else is reported as
    inconclusive and re-checked one spot at a time.
    """
    steps: list[dict[str, Any]] = []
    clicks: dict[str, dict[str, float]] = {}
    for button_id in button_ids:
        center = resolve_hitbox_center(button_id, layout_id)
        if center is None:
            return {button_id: {"id": button_id, "ok": False, "error": "no hitbox center"}}
        clicks[button_id] = {"x_pct": center.x_pct, "y_pct": center.y_pct}
        steps.append(
            _click(
                ClickTarget(button_id, center.x_pct, center.y_pct),
                ms=90,
                mode="window",
                calibrate=False,
            )
        )
        steps.append({"type": "sleep", "ms": 120})

    ok_run, detail = run_input_script_on_cabinet(
        ip=ip,
        agent=agent,
        script={"defaultKeyDelayMs": 30, "steps": steps},
        focus_process="godot",
        timeout=60,
    )
    if not ok_run:
        return {
            b: {"id": b, "ok": False, "error": f"input agent: {detail}", "bet_count": 0}
            for b in button_ids
        }

    time.sleep(settle_sec)
    state = read_state_and_cancel(ip)
    bets = state.get("bets") or []
    # The read happens while the chips still stand, so add the staked amount back
    # to describe the balance we actually have after the cancel refunds it.
    staked = sum(int(b.get("CreditValue") or 0) for b in bets)
    available = state.get("credits")
    if available is not None:
        available = int(available) + staked
    by_numbers: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    for bet in bets:
        by_numbers.setdefault(reported_numbers(bet), []).append(bet)

    out: dict[str, dict[str, Any]] = {}
    for button_id in button_ids:
        want = expected_numbers(button_id)
        matches = by_numbers.get(tuple(sorted(want or ())), [])
        mine = matches[0] if len(matches) == 1 else None
        result: dict[str, Any] = {
            "id": button_id,
            "click_pct": clicks[button_id],
            "expected_numbers": list(want or ()),
            "reported_numbers": list(reported_numbers(mine)) if mine else [],
            "bet_count": len(bets),
            "batch": list(button_ids),
            "bet_type": str(mine.get("BetType") or "") if mine else "",
            "bet_id": str(mine.get("Id") or "") if mine else "",
            "credits": available,
            "credits_while_staked": state.get("credits"),
        }
        if want is None:
            result["ok"] = False
            result["error"] = "no expectation defined"
        elif mine is not None and len(bets) == len(button_ids):
            result["ok"] = True
            result["error"] = ""
        elif len(bets) == 0:
            result["ok"] = False
            result["error"] = "middleware reported no bet (window closed or click ignored)"
        else:
            result["ok"] = False
            result["error"] = (
                f"expected {want}, middleware reported "
                f"{[ (str(b.get('BetType')), str(b.get('Id'))) for b in bets ]}"
            )
        result["bets_raw"] = bets
        out[button_id] = result
    return out


def verify_all(
    ip: str = DEFAULT_IP,
    *,
    ids: Iterable[str] | None = None,
    chip_index: int = 0,
    layout_id: str | None = None,
    kinds: tuple[str, ...] = BET_KINDS,
    families: Iterable[str] | None = None,
    out_dir: Path | None = None,
    passes: int = 2,
    batch_size: int = 3,
    min_credits: int = 2,
    progress: Any = print,
) -> dict[str, Any]:
    """
    Verify every mapped bet spot, as many as fit inside each 22 s betting window.

    A spot that reports no bet at all is retried on a later pass (the usual cause
    is the window closing mid-click, not a bad hitbox). Outside spots are skipped
    while the balance is under :data:`OUTSIDE_MIN_CREDITS`, because the core drops
    those bets silently and the click would look like a mapping failure.
    """
    layout_id = (layout_id or DEFAULT_LAYOUT).strip().lower()
    families = list(families) if families is not None else None
    if families is not None:
        kinds = kinds_for_families(families)
    targets = bet_targets(ids, layout_id, kinds=kinds, families=families)
    anchors_run = any(k in ANCHOR_KINDS for k in kinds)
    race_run = any(k in RACE_KINDS for k in kinds)
    results_name = results_path_for(layout_id, anchors=anchors_run, race=race_run).name
    out_dir = out_dir or out_dir_for(layout_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    agent = stage_input_agent(
        ip=ip, local_exe=build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    )
    # The pockets only exist on the other view. If we are already there, do not
    # spend another betting window toggling.
    from automation.roulette_verify_ui import capture as ui_capture, current_view, _load as ui_load

    work = out_dir / "view"
    work.mkdir(parents=True, exist_ok=True)
    probe = work / "view_probe.png"
    ui_capture(ip, agent, probe, settle_ms=800)
    seen = current_view(ui_load(probe), layout_id)
    want = "race" if race_run else "square"
    if seen != want:
        if not ensure_view(ip, agent, layout_id, work, view=want, progress=progress):
            raise RuntimeError(f"cabinet is not on the {want} view; nothing to click there")
    log = _roulette_log(ip)
    offset = log.stat().st_size

    state = fetch_player_state(ip)
    credits = _credits_display(state.get("credits"))
    progress(f"start: {layout_id}, {len(targets)} spots, credits={credits}")

    # Keep earlier verdicts for spots this run does not touch (e.g. re-running only
    # the outside bets after a credit top-up) so results.json stays a full picture.
    results = _load_previous(
        out_dir / results_name,
        exclude=targets,
        keep=bet_targets(None, layout_id, kinds=kinds),
    )
    pending = list(targets)
    chip_armed = False

    for attempt in range(1, max(1, passes) + 1):
        if not pending:
            break
        # Retries go one spot at a time so a bad batch cannot hide which spot failed.
        size = batch_size if attempt == 1 else 1
        if attempt > 1:
            progress(f"pass {attempt}: re-checking {len(pending)} spot(s) individually")
        retry: list[str] = []
        queue = list(pending)

        while queue:
            offset, matched, detail = wait_for_open_window(log, offset=offset)
            if not matched:
                for button_id in queue:
                    results[button_id] = {
                        "id": button_id,
                        "ok": False,
                        "error": f"no betting window: {detail}",
                    }
                break
            window_start = time.monotonic()
            progress(f"window open ({len(queue)} left, credits={credits}): {detail}")
            if not chip_armed:
                arm_chip(ip, agent=agent, chip_index=chip_index, layout_id=layout_id)
                chip_armed = True

            while queue and (time.monotonic() - window_start) < (OPEN_WINDOW_SEC - SPOT_BUDGET_SEC):
                batch = _next_batch(queue, size, credits, min_credits)
                if not batch:
                    break
                for button_id in batch:
                    if is_outside(button_id) and (credits or 0) < OUTSIDE_MIN_CREDITS:
                        results[button_id] = {
                            "id": button_id,
                            "ok": None,
                            "skipped": True,
                            "expected_numbers": list(expected_numbers(button_id) or ()),
                            "error": (
                                f"outside bet needs >= {OUTSIDE_MIN_CREDITS} credits, "
                                f"balance is {credits}"
                            ),
                        }
                        progress(f"  SKIP {button_id:<9} outside bet, credits={credits}")
                batch = [b for b in batch if b not in results]
                if not batch:
                    continue

                batch_results = verify_batch(ip, batch, agent=agent, layout_id=layout_id)
                window_closed = False
                for button_id in batch:
                    res = batch_results[button_id]
                    credits = _credits_display(res.get("credits"), fallback=credits)
                    res["credits_display"] = credits
                    mark = "OK " if res.get("ok") else "BAD"
                    progress(
                        f"  {mark} {button_id:<9} type={res.get('bet_type','')!r} "
                        f"id={res.get('bet_id','')!r} got={res.get('reported_numbers')} "
                        f"credits={credits} {res.get('error','')}"
                    )
                    if res.get("ok"):
                        results[button_id] = res
                        continue
                    if res.get("bet_count") == 0:
                        window_closed = True
                    # Inconclusive in a batch: prove or disprove it on its own.
                    if len(batch) > 1 or res.get("bet_count") == 0:
                        retry.append(button_id)
                    else:
                        results[button_id] = res
                _write_results(out_dir / results_name, ip, targets, results, layout_id, kinds)
                if window_closed:
                    break

            if (credits or 0) < min_credits:
                progress(f"stopping: balance {credits} below {min_credits} credits")
                for button_id in queue:
                    results[button_id] = {
                        "id": button_id,
                        "ok": None,
                        "skipped": True,
                        "error": f"stopped: balance {credits} < {min_credits} credits",
                    }
                break

        pending = [b for b in dict.fromkeys(retry) if b not in results]

    for button_id in pending:
        results.setdefault(
            button_id,
            {"id": button_id, "ok": False, "error": "no conclusive read after all passes"},
        )
    return _write_results(out_dir / results_name, ip, targets, results, layout_id, kinds)


def _next_batch(
    queue: list[str], size: int, credits: int | None, min_credits: int
) -> list[str]:
    """
    Pop up to *size* spots that can be verified together.

    Each straight costs one credit while it stands, so the batch is capped by the
    spare balance. Outside spots overlap each other's numbers, so they go alone.
    """
    spare = max(1, (credits or 1) - min_credits + 1)
    room = max(1, min(size, spare))
    batch: list[str] = []
    while queue and len(batch) < room:
        button_id = queue[0]
        if is_outside(button_id) and batch:
            break
        batch.append(queue.pop(0))
        if is_outside(button_id):
            break
    return batch


def _credits_display(raw: Any, *, fallback: int | None = None) -> int | None:
    """Middleware reports credit *units*; the cabinet shows units / 10000."""
    if raw is None:
        return fallback
    try:
        return int(raw) // 10_000
    except (TypeError, ValueError):
        return fallback


def _load_previous(
    path: Path, *, exclude: Iterable[str], keep: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    """
    Earlier verdicts for spots this run does not touch.

    *keep* is the set of ids still in the registry; a verdict for an id that was
    since renamed or removed is dropped instead of haunting the summary.
    """
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    skip = set(exclude)
    known = set(keep) if keep is not None else None
    return {
        str(r.get("id")): r
        for r in payload.get("results") or []
        if r.get("id")
        and str(r.get("id")) not in skip
        and (known is None or str(r.get("id")) in known)
    }


def _write_results(
    path: Path,
    ip: str,
    targets: list[str],
    results: dict[str, dict[str, Any]],
    layout_id: str | None = None,
    kinds: tuple[str, ...] = BET_KINDS,
) -> dict[str, Any]:
    order = list(dict.fromkeys(list(bet_targets(None, layout_id, kinds=kinds)) + list(targets)))
    ordered = [results[b] for b in order if b in results]
    passed = [r for r in ordered if r.get("ok") is True]
    failed = [r for r in ordered if r.get("ok") is False]
    skipped = [r for r in ordered if r.get("ok") is None]
    summary = {
        "ip": ip,
        "layout_id": (layout_id or DEFAULT_LAYOUT),
        "total": len(ordered),
        "tested": len(passed) + len(failed),
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "failed_ids": [r["id"] for r in failed],
        "skipped_ids": [r["id"] for r in skipped],
        "results": ordered,
    }
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def apply_results_to_hitboxes(
    *,
    layout_id: str | None = None,
    anchors: bool = False,
    race: bool = False,
    results_path: Path | None = None,
    hitboxes_path: Path | None = None,
) -> dict[str, Any]:
    """
    Record the live proof in the hitbox registry.

    A verified spot gets the ``expect`` block the middleware actually returned,
    so later automation can assert against a measured value instead of a guess.
    A proven inside-bet anchor also drops its ``anchor_unverified`` kind.
    """
    layout_id = (layout_id or DEFAULT_LAYOUT).strip().lower()
    results_path = results_path or results_path_for(layout_id, anchors=anchors, race=race)
    hitboxes_path = hitboxes_path or hitboxes_path_for(layout_id)
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    data = json.loads(hitboxes_path.read_text(encoding="utf-8"))
    buttons = data.get("buttons") or {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    updated: list[str] = []
    for res in payload.get("results") or []:
        button_id = str(res.get("id"))
        entry = buttons.get(button_id)
        if entry is None:
            continue
        if res.get("ok") is True:
            entry["expect"] = {"BetType": res.get("bet_type"), "Id": res.get("bet_id")}
            entry["verified"] = {
                "at": stamp,
                "method": "Godot click at click_center_px + GET /api/data/0 PlayerDataBets",
                "reported_numbers": res.get("reported_numbers"),
                "chip": "chip_1",
            }
            entry["note"] = (
                f"Live-verified on {payload.get('ip')}: a click at click_center_px makes the core "
                f"report {res.get('bet_type')} Id={res.get('bet_id')} "
                f"(covers {res.get('reported_numbers')})."
            )
            if entry.get("kind") in ("anchor_unverified", "race_unverified"):
                entry["kind"] = entry["kind"].removesuffix("_unverified")
                entry.pop("proof", None)
            updated.append(button_id)
        elif res.get("ok") is None:
            entry["verified"] = {"at": stamp, "blocked": res.get("error")}
            entry["note"] = (
                f"Area measured but not provable now: {res.get('error')}. "
                "Outside bets below the outside minimum are dropped silently by the core."
            )
            updated.append(button_id)

    prefix = "racetrack" if race else "inside_anchors" if anchors else "bet_spots"
    data["status"] = {
        **(data.get("status") if isinstance(data.get("status"), dict) else {}),
        f"{prefix}_verified": payload.get("passed"),
        f"{prefix}_blocked": payload.get("skipped"),
        f"{prefix}_failed": payload.get("failed"),
        "verified_at": stamp,
    }
    hitboxes_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"updated": len(updated), "ids": updated}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verify mapped bet hitboxes against middleware")
    p.add_argument("--ip", default=DEFAULT_IP)
    p.add_argument("--layout", default=DEFAULT_LAYOUT, choices=("layout1", "layout2"))
    p.add_argument("--only", default=None, help="Comma-separated hitbox ids (default: all)")
    p.add_argument("--chip", type=int, default=0, help="Chip index (0 = chip_1)")
    p.add_argument("--passes", type=int, default=3, help="Retry passes for inconclusive spots")
    p.add_argument("--batch", type=int, default=3, help="Straights clicked per betting window")
    p.add_argument("--raw", action="store_true", help="Print the raw middleware bet entries")
    p.add_argument(
        "--anchors",
        action="store_true",
        help="Sweep the inside-bet anchors (splits/streets/corners/six-lines) instead",
    )
    p.add_argument(
        "--family",
        default=None,
        help=(
            "Comma-separated bet families to sweep: "
            f"{'|'.join(FAMILIES)} (implies --anchors for inside bets)"
        ),
    )
    p.add_argument(
        "--write-hitboxes",
        action="store_true",
        help="Only fold an existing results.json into the layout's hitbox registry",
    )
    args = p.parse_args(argv)

    families = args.family.split(",") if args.family else None
    kinds = kinds_for_families(families) if families else (
        ANCHOR_KINDS if args.anchors else BET_KINDS
    )
    anchors_run = any(k in ANCHOR_KINDS for k in kinds)
    race_run = any(k in RACE_KINDS for k in kinds)

    if args.write_hitboxes:
        print(
            json.dumps(
                apply_results_to_hitboxes(
                    layout_id=args.layout, anchors=anchors_run, race=race_run
                ),
                indent=2,
            )
        )
        return 0

    ids = args.only.split(",") if args.only else None
    summary = verify_all(
        args.ip,
        ids=ids,
        chip_index=args.chip,
        layout_id=args.layout,
        kinds=kinds,
        families=families,
        passes=args.passes,
        batch_size=args.batch,
    )
    if args.raw:
        for res in summary["results"]:
            print(json.dumps({res["id"]: res.get("bets_raw")}, indent=2))
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "results"},
            indent=2,
        )
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
