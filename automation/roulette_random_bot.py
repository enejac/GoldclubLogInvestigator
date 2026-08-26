"""
Random / systematic UI+bet click bot for mapped roulette hitboxes.

Walks every safe mapped control across layout1/layout2, opens overlays and the
racetrack view when needed, optionally switches skins, and logs every click with
``client_id`` (multi-seat ready; default ``player0``).
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from automation.bot_config import (
    BotConfig,
    active_bot_config,
    load_bot_config,
    push_bot_config,
    reset_bot_config,
)
from automation.click_logger import ClickEvent, ClickLogger
from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_layout_store import (
    load_hitboxes,
    resolve_hitbox_center,
    set_active_layout,
)
from automation.roulette_middleware import cancel_all_bets, fetch_player_state
from automation.roulette_runner import (
    _latest_file,
    peek_betting_phase,
    wait_for_betting_open,
)

ProgressFn = Callable[[str], None]
Mode = Literal["systematic", "random"]
# batch targets, events logged for this batch, agent_ok -> return True to abort early
OnBatchDoneFn = Callable[[list["ClickTargetSpec"], list[ClickEvent], bool], bool]

# Abort the sweep when the playable meter is at/under this (display credit units).
# Overridden by BotConfig.min_credits when a profile is active.
MIN_CREDITS_TO_RUN = 100

# Same hard excludes as roulette_verify_ui — never lock / call attendant / cashout.
NEVER_CLICK = frozenset(
    {"COBRAR", "EDGE_TAB", "MENU_PIN_LOCK", "MENU_ASSISTANT", "LLAMAR"}
)
# Centre-click moves opacity to ~50%; skip in catalog sweeps.
SKIP_IDS = frozenset({"OPACITY_SLIDER"})
# Spin burns the open window; include once per layout pass, not every bet.
DEFER_IDS = frozenset({"START"})

# Controls that stay grey until betting is open (aligned with roulette_verify_ui).
WINDOW_DEPENDENT_IDS = frozenset(
    {
        "START",
        "CREDIT_DISPLAY",
        "DENOM",
        "PAYTABLE",
        "CHANGE_VIEW",
        "SERIES_EYE_TOP",
        "SERIES_EYE_BOTTOM",
        "SERIES_EXTRA_EYE_TOP",
        "SERIES_EXTRA_EYE_BOTTOM",
        "chip_1",
        "chip_5",
        "chip_10",
        "chip_50",
        "chip_100",
        "VECINOS",
        "FINALES",
        "COMPLETO",
        "VECINOS_0",
        "HUERFANOS",
        "VECINOS_00",
        "CANCELAR_TODO",
        "BORRADOR",
        "REPETIR",
        "MUESTRA_GANANCIAS",
    }
)
WINDOW_DEPENDENT_KINDS = frozenset(
    {
        "bet",
        "bet_pill",
        "bet_tool",
        "chip",
        "flow",
        "race_unverified",
        "anchor",
        "anchor_unverified",
    }
)

LAYOUT_IDS = ("layout1", "layout2")

# overlay name -> (opener button ids tried in order, closer button ids)
# Timings aligned with roulette_verify_ui (panel settle ~1.2–1.6s, skin ~2.2s).
OVERLAY_IO: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "statistics": (("PLAYER", "STATISTICS"), ("STATS_EXIT",)),
    "language": (("IDIOMA",), ("LANG_ES",)),
    # PAYTABLE is NOT an opener on Crycle — it is the STANDARD 36X tile (inert here).
    "help": (("AYUDA",), ("HELP_EXIT",)),
    "menu": (("MENU",), ("MENU",)),
    "racetrack": (("CHANGE_VIEW",), ("CHANGE_VIEW",)),
}

# Legacy module defaults (safe profile). Prefer active_bot_config().
OVERLAY_OPEN_SLEEP_MS = 1600
OVERLAY_CLOSE_SLEEP_MS = 1200
LAYOUT_SWITCH_SLEEP_MS = 2200


def _cfg() -> BotConfig:
    return active_bot_config()


def _gap_ms(base: int) -> int:
    cfg = _cfg()
    j = int(cfg.gap_jitter_ms)
    if j <= 0:
        return max(0, int(base))
    return max(0, int(base) + random.randint(-j, j))


# Base chrome that opens a sub-window even when hitbox.overlay is null.
# After clicking these, always dismiss so the sweep cannot get stuck.
PANEL_OPENERS: dict[str, str] = {
    "AYUDA": "help",
    "IDIOMA": "language",
    "MENU": "menu",
    "PLAYER": "statistics",
    "STATISTICS": "statistics",
    # Menu row that jumps into the same help book as AYUDA.
    "MENU_AYUDA": "help",
}


@dataclass(frozen=True, slots=True)
class ClickTargetSpec:
    layout_id: str
    button_id: str
    kind: str
    overlay: str | None
    x_pct: float
    y_pct: float
    group: str  # base | overlay:<name> | deferred


@dataclass
class RandomBotResult:
    ok: bool
    message: str
    clicked: int = 0
    skipped: int = 0
    failed_batches: int = 0
    out_dir: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    aborted_early: bool = False
    abort_reason: str = ""


def _progress(cb: ProgressFn | None, msg: str) -> None:
    if cb is not None:
        cb(msg)


def _read_credits(ip: str) -> tuple[int | None, str]:
    """Return ``(credits, detail)``. credits is None when middleware is unusable."""
    try:
        st = fetch_player_state(ip)
    except Exception as e:  # noqa: BLE001
        return None, f"fetch failed: {e}"
    if not st.get("ok"):
        return None, str(st.get("error") or "middleware not ok")
    raw = st.get("credits")
    if raw is None:
        return None, "credits field missing"
    try:
        return int(raw), "ok"
    except (TypeError, ValueError):
        return None, f"bad credits value {raw!r}"


def _credits_block_reason(
    credits: int | None,
    detail: str,
    *,
    min_credits: int | None = None,
) -> str | None:
    """Non-empty reason means the bot must stop."""
    floor = MIN_CREDITS_TO_RUN if min_credits is None else int(min_credits)
    if credits is None:
        return f"cannot read credits ({detail}); refusing to run without a credit meter"
    if credits < floor:
        return f"no/low credits ({credits} < {floor}); top up before continuing"
    return None


def _click_step(x_pct: float, y_pct: float, *, ms: int = 100) -> dict[str, Any]:
    return {
        "type": "click_window",
        "value": f"{ROULETTE_FOCUS_PROCESS}@{x_pct:.3f},{y_pct:.3f}",
        "ms": ms,
    }


def _group_for(overlay: str | None, button_id: str) -> str:
    if button_id in DEFER_IDS:
        return "deferred"
    if overlay:
        return f"overlay:{overlay}"
    return "base"


def build_catalog(
    *,
    layouts: tuple[str, ...] = LAYOUT_IDS,
    include_unverified: bool = True,
) -> list[ClickTargetSpec]:
    """All clickable mapped controls with resolvable centres (safe set)."""
    out: list[ClickTargetSpec] = []
    seen: set[tuple[str, str]] = set()
    for lid in layouts:
        buttons = load_hitboxes(lid).get("buttons") or {}
        for bid, entry in buttons.items():
            if not isinstance(entry, dict):
                continue
            if bid in NEVER_CLICK or bid in SKIP_IDS:
                continue
            kind = str(entry.get("kind") or "")
            if not include_unverified and kind.endswith("_unverified"):
                continue
            center = resolve_hitbox_center(bid, lid, allow_geometry=False)
            if center is None:
                continue
            key = (lid, bid)
            if key in seen:
                continue
            seen.add(key)
            overlay = entry.get("overlay")
            overlay_s = str(overlay) if overlay else None
            out.append(
                ClickTargetSpec(
                    layout_id=lid,
                    button_id=bid,
                    kind=kind,
                    overlay=overlay_s,
                    x_pct=float(center.x_pct),
                    y_pct=float(center.y_pct),
                    group=_group_for(overlay_s, bid),
                )
            )
    return out


def _order_systematic(targets: list[ClickTargetSpec]) -> list[ClickTargetSpec]:
    """layout1 base → layout1 overlays → layout2 base → layout2 overlays → deferred."""
    overlay_rank = {
        "base": 0,
        "overlay:statistics": 1,
        "overlay:language": 2,
        "overlay:help": 3,
        "overlay:menu": 4,
        "overlay:racetrack": 5,
        "deferred": 9,
    }

    def key(t: ClickTargetSpec) -> tuple:
        layout_i = 0 if t.layout_id == "layout1" else 1
        return (layout_i, overlay_rank.get(t.group, 8), t.kind, t.button_id)

    return sorted(targets, key=key)


def _needs_open_window(target: ClickTargetSpec) -> bool:
    """True when the control is greyed out outside an open betting window."""
    if target.button_id in WINDOW_DEPENDENT_IDS or target.button_id in DEFER_IDS:
        return True
    if target.kind in WINDOW_DEPENDENT_KINDS:
        return True
    if target.button_id.startswith(("chip_", "race_", "mini_", "VECINOS", "FINALES")):
        return True
    return False


def _closed_safe_targets(
    catalog: list[ClickTargetSpec],
    *,
    layout_id: str,
) -> list[ClickTargetSpec]:
    """UI chrome that can be poked while bets are closed (no cloth / START)."""
    out: list[ClickTargetSpec] = []
    for t in catalog:
        if t.layout_id != layout_id:
            continue
        if t.button_id in NEVER_CLICK or t.button_id in SKIP_IDS:
            continue
        if _needs_open_window(t):
            continue
        # Prefer base-surface hitboxes; overlay chrome is reached via openers below.
        if t.overlay:
            continue
        out.append(t)
    return out


def _run_closed_phase_burst(
    *,
    ip: str,
    agent: Any,
    catalog: list[ClickTargetSpec],
    layout_id: str,
    logger: ClickLogger,
    client_id: str,
    progress: ProgressFn | None,
    on_batch_done: OnBatchDoneFn | None,
) -> tuple[int, bool]:
    """
    Randomize closed-safe UI clicks until betting opens or the budget is spent.

    Returns ``(clicks_done, abort)``.
    """
    cfg = _cfg()
    if not cfg.closed_phase_enabled or cfg.closed_clicks_max_per_wait <= 0:
        return 0, False

    roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if roulette is None:
        return 0, False
    phase = peek_betting_phase(roulette)
    if phase == "open":
        return 0, False

    pool = _closed_safe_targets(catalog, layout_id=layout_id)
    if not pool:
        _progress(progress, "closed-phase: no safe targets")
        return 0, False

    budget = int(cfg.closed_clicks_max_per_wait)
    batch_n = int(cfg.closed_batch_size)
    done = 0
    _progress(
        progress,
        f"closed-phase: randomize up to {budget} clicks "
        f"(batch={batch_n}, pool={len(pool)}, phase={phase})",
    )

    while done < budget:
        phase = peek_betting_phase(roulette)
        if phase == "open":
            _progress(progress, f"closed-phase: betting opened after {done} clicks")
            break

        n = min(batch_n, budget - done)
        batch = [random.choice(pool) for _ in range(n)]
        steps: list[dict[str, Any]] = [
            {
                "type": "focus_process",
                "value": ROULETTE_FOCUS_PROCESS,
                "ms": cfg.focus_short_ms,
            }
        ]
        batch_events: list[ClickEvent] = []
        for b in batch:
            batch_events.append(
                logger.log(
                    client_id=client_id,
                    layout_id=b.layout_id,
                    button_id=b.button_id,
                    kind=b.kind,
                    overlay=b.overlay,
                    x_pct=b.x_pct,
                    y_pct=b.y_pct,
                    phase="planned",
                    note="closed-phase",
                )
            )
            steps.append(_click_step(b.x_pct, b.y_pct, ms=cfg.click_ms))
            steps.append({"type": "sleep", "ms": _gap_ms(cfg.gap_ms)})

        _progress(
            progress,
            f"closed-phase batch {done + 1}-{done + len(batch)} "
            f"ids={[b.button_id for b in batch]}",
        )
        ok, detail = run_input_script_on_cabinet(
            ip=ip,
            agent=agent,
            script={
                "defaultKeyDelayMs": cfg.default_key_delay_ms,
                "steps": steps,
            },
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=60,
        )
        for b in batch:
            batch_events.append(
                logger.log(
                    client_id=client_id,
                    layout_id=b.layout_id,
                    button_id=b.button_id,
                    kind=b.kind,
                    overlay=b.overlay,
                    x_pct=b.x_pct,
                    y_pct=b.y_pct,
                    phase="ok" if ok else "fail",
                    agent_ok=ok,
                    note=("closed-phase; " + detail) if not ok else "closed-phase",
                )
            )
        if ok:
            done += len(batch)
        else:
            _progress(progress, f"closed-phase batch FAIL: {detail}")
            # Don't spin forever on agent failures.
            break

        # Dismiss panels that closed-phase openers may have raised.
        touched_openers = {b.button_id for b in batch}
        for ov, (openers, _) in OVERLAY_IO.items():
            if touched_openers.intersection(openers) or any(
                PANEL_OPENERS.get(oid) == ov for oid in touched_openers
            ):
                _close_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=layout_id,
                    overlay=ov,
                    logger=logger,
                    client_id=client_id,
                )

        if on_batch_done is not None:
            try:
                if bool(on_batch_done(batch, batch_events, ok)):
                    return done, True
            except Exception as e:  # noqa: BLE001
                _progress(progress, f"on_batch_done warn: {e}")

    return done, False


def _ensure_betting_open(
    ip: str,
    *,
    progress: ProgressFn | None,
    for_start: bool = False,
) -> bool:
    """Wait until open + UI settle so tray / bet tools / START accept clicks."""
    roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if roulette is None:
        _progress(progress, "no ruleta Roulette log — skipping open-window wait")
        return True
    cfg = _cfg()
    settle = (
        cfg.open_ui_settle_start_sec if for_start else cfg.open_ui_settle_sec
    )
    ok, detail = wait_for_betting_open(
        roulette,
        timeout_sec=float(cfg.wait_betting_timeout_sec),
        settle_sec=settle,
        progress=progress,
    )
    if not ok:
        _progress(progress, f"open wait failed: {detail}")
        return False
    _progress(progress, f"betting open ready - {detail}")
    return True


def _ensure_layout(
    *,
    ip: str,
    agent: Any,
    current: str,
    want: str,
    logger: ClickLogger,
    client_id: str,
    progress: ProgressFn | None,
) -> str:
    """Best-effort skin switch. Returns assumed current layout after attempt."""
    if current == want:
        set_active_layout(want)
        return current
    if want == "layout1" and current == "layout2":
        bid, lid = "PANO", "layout2"
    elif want == "layout2" and current == "layout1":
        bid, lid = "LAYOUT_SWITCH", "layout1"
    else:
        set_active_layout(want)
        return want

    center = resolve_hitbox_center(bid, lid, allow_geometry=False)
    if center is None:
        _progress(progress, f"cannot switch to {want}: missing {bid}")
        return current
    logger.log(
        client_id=client_id,
        layout_id=lid,
        button_id=bid,
        kind="ui",
        overlay=None,
        x_pct=center.x_pct,
        y_pct=center.y_pct,
        phase="planned",
        note=f"layout switch -> {want}",
    )
    cfg = _cfg()
    script = {
        "defaultKeyDelayMs": cfg.default_key_delay_ms,
        "steps": [
            {
                "type": "focus_process",
                "value": ROULETTE_FOCUS_PROCESS,
                "ms": cfg.focus_ms,
            },
            _click_step(center.x_pct, center.y_pct, ms=cfg.click_ms),
            {"type": "sleep", "ms": cfg.layout_switch_ms},
        ],
    }
    ok, detail = run_input_script_on_cabinet(
        ip=ip, agent=agent, script=script, focus_process=ROULETTE_FOCUS_PROCESS, timeout=60
    )
    logger.log(
        client_id=client_id,
        layout_id=lid,
        button_id=bid,
        kind="ui",
        overlay=None,
        x_pct=center.x_pct,
        y_pct=center.y_pct,
        phase="ok" if ok else "fail",
        agent_ok=ok,
        note=f"layout switch -> {want}; {detail}",
    )
    if ok:
        set_active_layout(want)
        _progress(progress, f"layout -> {want} via {bid}")
        return want
    _progress(progress, f"layout switch failed: {detail}")
    return current


def _open_overlay(
    *,
    ip: str,
    agent: Any,
    layout_id: str,
    overlay: str,
    logger: ClickLogger,
    client_id: str,
) -> bool:
    openers, _ = OVERLAY_IO.get(overlay, ((), ()))
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    for opener in openers:
        if opener not in buttons:
            continue
        center = resolve_hitbox_center(opener, layout_id, allow_geometry=False)
        if center is None:
            continue
        logger.log(
            client_id=client_id,
            layout_id=layout_id,
            button_id=opener,
            kind="ui",
            overlay=overlay,
            x_pct=center.x_pct,
            y_pct=center.y_pct,
            phase="planned",
            note=f"open overlay {overlay}",
        )
        cfg = _cfg()
        script = {
            "defaultKeyDelayMs": cfg.default_key_delay_ms,
            "steps": [
                {
                    "type": "focus_process",
                    "value": ROULETTE_FOCUS_PROCESS,
                    "ms": cfg.focus_ms,
                },
                _click_step(center.x_pct, center.y_pct, ms=cfg.click_ms),
                {"type": "sleep", "ms": cfg.overlay_open_ms},
            ],
        }
        ok, detail = run_input_script_on_cabinet(
            ip=ip,
            agent=agent,
            script=script,
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=60,
        )
        logger.log(
            client_id=client_id,
            layout_id=layout_id,
            button_id=opener,
            kind="ui",
            overlay=overlay,
            x_pct=center.x_pct,
            y_pct=center.y_pct,
            phase="ok" if ok else "fail",
            agent_ok=ok,
            note=f"open overlay {overlay}; {detail}",
        )
        return ok
    return False


def _close_overlay(
    *,
    ip: str,
    agent: Any,
    layout_id: str,
    overlay: str,
    logger: ClickLogger,
    client_id: str,
) -> None:
    _, closers = OVERLAY_IO.get(overlay, ((), ()))
    buttons = load_hitboxes(layout_id).get("buttons") or {}
    cfg = _cfg()
    steps: list[dict[str, Any]] = [
        {
            "type": "focus_process",
            "value": ROULETTE_FOCUS_PROCESS,
            "ms": cfg.focus_short_ms,
        }
    ]
    for closer in closers:
        if closer not in buttons:
            continue
        center = resolve_hitbox_center(closer, layout_id, allow_geometry=False)
        if center is None:
            continue
        logger.log(
            client_id=client_id,
            layout_id=layout_id,
            button_id=closer,
            kind="ui",
            overlay=overlay,
            x_pct=center.x_pct,
            y_pct=center.y_pct,
            phase="planned",
            note=f"close overlay {overlay}",
        )
        steps.append(_click_step(center.x_pct, center.y_pct, ms=cfg.click_ms))
        steps.append({"type": "sleep", "ms": cfg.overlay_close_ms})
        break
    else:
        # ESC does NOT close the help book on Alegro — still try as last resort
        # for other panels, then prefer HELP_EXIT coords if present.
        steps.append({"type": "key", "value": "ESCAPE"})
        steps.append({"type": "sleep", "ms": cfg.overlay_close_ms})
        logger.log(
            client_id=client_id,
            layout_id=layout_id,
            button_id="ESCAPE",
            kind="ui",
            overlay=overlay,
            x_pct=0.0,
            y_pct=0.0,
            phase="planned",
            note=f"close overlay {overlay} via ESC",
        )
    ok, detail = run_input_script_on_cabinet(
        ip=ip,
        agent=agent,
        script={"defaultKeyDelayMs": cfg.default_key_delay_ms, "steps": steps},
        focus_process=ROULETTE_FOCUS_PROCESS,
        timeout=60,
    )
    logger.log(
        client_id=client_id,
        layout_id=layout_id,
        button_id=closers[0] if closers else "ESCAPE",
        kind="ui",
        overlay=overlay,
        x_pct=0.0,
        y_pct=0.0,
        phase="ok" if ok else "fail",
        agent_ok=ok,
        note=f"close overlay {overlay}; {detail}",
    )


def load_completed_clicks(log_path: Path) -> set[tuple[str, str]]:
    """``(layout_id, button_id)`` pairs already marked phase=ok."""
    done: set[tuple[str, str]] = set()
    if not log_path.is_file():
        return done
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("phase") != "ok":
            continue
        lid = str(row.get("layout_id") or "")
        bid = str(row.get("button_id") or "")
        if lid and bid:
            done.add((lid, bid))
    return done


def dismiss_stuck_overlays(
    *,
    ip: str = "10.0.0.90",
    layout_id: str = "layout2",
    progress: ProgressFn | None = None,
) -> tuple[bool, str]:
    """
    Best-effort escape from help / menu / stats / language panels on the live EGM.

    Used when a sweep leaves ``AYUDA`` / ``PAYTABLE`` (dynamic paytables) open.
    """
    from automation.click_logger import ClickLogger

    out = Path("_tmp_logs") / "bot_smoke" / "dismiss_overlays"
    out.mkdir(parents=True, exist_ok=True)
    logger = ClickLogger(out, cabinet_ip=ip, client_ids=("player0",), session_meta={"action": "dismiss"})
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    agent = stage_input_agent(ip=ip, local_exe=local)
    for ov in ("help", "menu", "statistics", "language"):
        _progress(progress, f"dismiss {ov} on {layout_id}")
        _close_overlay(
            ip=ip,
            agent=agent,
            layout_id=layout_id,
            overlay=ov,
            logger=logger,
            client_id="player0",
        )
    # Extra HELP_EXIT + ESC for the dynamic paytable page.
    center = resolve_hitbox_center("HELP_EXIT", layout_id, allow_geometry=False)
    cfg = _cfg()
    steps: list[dict[str, Any]] = [
        {
            "type": "focus_process",
            "value": ROULETTE_FOCUS_PROCESS,
            "ms": cfg.focus_ms,
        }
    ]
    if center is not None:
        steps += [
            _click_step(center.x_pct, center.y_pct, ms=cfg.click_ms),
            {"type": "sleep", "ms": cfg.overlay_close_ms},
            _click_step(center.x_pct, center.y_pct, ms=cfg.click_ms),
            {"type": "sleep", "ms": cfg.overlay_close_ms},
        ]
    steps += [
        {"type": "key", "value": "ESCAPE"},
        {"type": "sleep", "ms": cfg.overlay_close_ms},
    ]
    ok, detail = run_input_script_on_cabinet(
        ip=ip,
        agent=agent,
        script={"defaultKeyDelayMs": cfg.default_key_delay_ms, "steps": steps},
        focus_process=ROULETTE_FOCUS_PROCESS,
        timeout=90,
    )
    logger.close()
    return ok, detail


def run_random_bot(
    *,
    ip: str = "10.0.0.90",
    out_dir: Path | str,
    mode: Mode = "systematic",
    layouts: tuple[str, ...] = LAYOUT_IDS,
    start_layout: str = "layout1",
    client_ids: tuple[str, ...] = ("player0",),
    max_clicks: int | None = None,
    batch_size: int | None = None,
    shuffle_layouts: bool | None = None,
    include_unverified: bool | None = None,
    wait_betting: bool | None = None,
    resume: bool = False,
    progress: ProgressFn | None = None,
    on_batch_done: OnBatchDoneFn | None = None,
    bot_config: BotConfig | None = None,
    bot_profile: str | None = None,
    bot_config_path: Path | str | None = None,
) -> RandomBotResult:
    """
    Click through the mapped catalog.

    *systematic* — deterministic group order covering every target once.
    *random* — shuffle remaining queue; may interleave layout switches.
    *resume* — append to existing ``click_log.jsonl`` and skip already-ok targets.
    *on_batch_done* — optional hook after each batch; return True to abort early
      (used by bug-hunt to freeze packs mid-cycle when godot1 faults).
    *bot_config* / *bot_profile* — timing profile (default: emulation from bot_config.json).
    """
    cfg = (bot_config or load_bot_config(bot_profile, bot_config_path)).clamp()
    if batch_size is not None:
        cfg.batch_size = int(batch_size)
    if shuffle_layouts is not None:
        cfg.shuffle_layouts = bool(shuffle_layouts)
    if include_unverified is not None:
        cfg.include_unverified = bool(include_unverified)
    if wait_betting is not None:
        cfg.wait_betting = bool(wait_betting)
    cfg.clamp()
    token = push_bot_config(cfg)
    try:
        return _run_random_bot_impl(
            ip=ip,
            out_dir=out_dir,
            mode=mode,
            layouts=layouts,
            start_layout=start_layout,
            client_ids=client_ids,
            max_clicks=max_clicks,
            resume=resume,
            progress=progress,
            on_batch_done=on_batch_done,
            cfg=cfg,
        )
    finally:
        reset_bot_config(token)


def _run_random_bot_impl(
    *,
    ip: str,
    out_dir: Path | str,
    mode: Mode,
    layouts: tuple[str, ...],
    start_layout: str,
    client_ids: tuple[str, ...],
    max_clicks: int | None,
    resume: bool,
    progress: ProgressFn | None,
    on_batch_done: OnBatchDoneFn | None,
    cfg: BotConfig,
) -> RandomBotResult:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    client_id = client_ids[0] if client_ids else "player0"
    log_path = out / "click_log.jsonl"
    done = load_completed_clicks(log_path) if resume else set()
    batch_size = cfg.batch_size
    shuffle_layouts = cfg.shuffle_layouts
    include_unverified = cfg.include_unverified
    wait_betting = cfg.wait_betting
    logger = ClickLogger(
        out,
        cabinet_ip=ip,
        client_ids=client_ids,
        resume=resume,
        session_meta={
            "mode": mode,
            "layouts": list(layouts),
            "start_layout": start_layout,
            "batch_size": batch_size,
            "shuffle_layouts": shuffle_layouts,
            "include_unverified": include_unverified,
            "wait_betting": wait_betting,
            "bot_profile": cfg.profile,
            "bot_config": cfg.to_dict(),
            "resume": resume,
            "already_ok": len(done),
        },
    )
    (out / "bot_config_used.json").write_text(
        json.dumps(cfg.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    _progress(
        progress,
        f"bot profile={cfg.profile} batch={batch_size} "
        f"click={cfg.click_ms}ms gap={cfg.gap_ms}ms "
        f"overlay_open={cfg.overlay_open_ms}ms",
    )

    catalog = build_catalog(layouts=layouts, include_unverified=include_unverified)
    if mode == "systematic":
        queue = _order_systematic(catalog)
    else:
        queue = list(catalog)
        random.shuffle(queue)
        # Pack open-window targets first so each betting session gets denser cloth/UI clicks;
        # closed-safe chrome is covered by the closed-phase burst while waiting.
        open_first = [t for t in queue if _needs_open_window(t)]
        rest = [t for t in queue if not _needs_open_window(t)]
        random.shuffle(open_first)
        random.shuffle(rest)
        queue = open_first + rest
        if shuffle_layouts:
            # Bias: occasionally force a layout-switch target to the front.
            switchers = [t for t in queue if t.button_id in ("PANO", "LAYOUT_SWITCH")]
            if switchers and random.random() < 0.35:
                s = random.choice(switchers)
                queue.remove(s)
                queue.insert(0, s)

    if done:
        before = len(queue)
        queue = [t for t in queue if (t.layout_id, t.button_id) not in done]
        _progress(progress, f"resume: skipped {before - len(queue)} already-ok targets")

    if max_clicks is not None:
        queue = queue[: max(0, int(max_clicks))]

    (out / "catalog.json").write_text(
        json.dumps(
            {
                "total": len(catalog),
                "queued": len(queue),
                "already_ok": len(done),
                "mode": mode,
                "resume": resume,
                "targets": [
                    {
                        "layout_id": t.layout_id,
                        "button_id": t.button_id,
                        "kind": t.kind,
                        "overlay": t.overlay,
                        "group": t.group,
                        "x_pct": t.x_pct,
                        "y_pct": t.y_pct,
                    }
                    for t in queue
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _progress(
        progress,
        f"catalog {len(catalog)} mapped; queue {len(queue)} remaining ({mode})",
    )

    credits, cred_detail = _read_credits(ip)
    _progress(
        progress,
        f"credits={credits if credits is not None else 'unknown'} "
        f"({cred_detail}) client={client_id}",
    )
    block = _credits_block_reason(credits, cred_detail, min_credits=cfg.min_credits)
    if block:
        logger.close()
        return RandomBotResult(
            ok=False,
            message=block,
            out_dir=str(out),
            summary=logger.summary(),
        )

    _progress(progress, "Building InputAgent ...")
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    agent = stage_input_agent(ip=ip, local_exe=local)

    current_layout = start_layout if start_layout in layouts else layouts[0]
    set_active_layout(current_layout)
    current_overlay: str | None = None
    clicked = 0
    skipped = 0
    failed_batches = 0
    cloth_cleared_this_window = False
    stop_reason: str | None = None
    aborted_early = False
    abort_reason = ""

    i = 0
    while i < len(queue):
        t = queue[i]
        # Layout switch
        if t.layout_id != current_layout:
            if current_overlay:
                _close_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=current_layout,
                    overlay=current_overlay,
                    logger=logger,
                    client_id=client_id,
                )
                current_overlay = None
            current_layout = _ensure_layout(
                ip=ip,
                agent=agent,
                current=current_layout,
                want=t.layout_id,
                logger=logger,
                client_id=client_id,
                progress=progress,
            )
            if current_layout != t.layout_id:
                logger.log(
                    client_id=client_id,
                    layout_id=t.layout_id,
                    button_id=t.button_id,
                    kind=t.kind,
                    overlay=t.overlay,
                    x_pct=t.x_pct,
                    y_pct=t.y_pct,
                    phase="skipped",
                    note="wrong layout after switch attempt",
                )
                skipped += 1
                i += 1
                continue

        # Overlay open/close
        want_ov = t.overlay
        if want_ov != current_overlay:
            if current_overlay:
                _close_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=current_layout,
                    overlay=current_overlay,
                    logger=logger,
                    client_id=client_id,
                )
                current_overlay = None
            if want_ov:
                if not _open_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=current_layout,
                    overlay=want_ov,
                    logger=logger,
                    client_id=client_id,
                ):
                    logger.log(
                        client_id=client_id,
                        layout_id=t.layout_id,
                        button_id=t.button_id,
                        kind=t.kind,
                        overlay=t.overlay,
                        x_pct=t.x_pct,
                        y_pct=t.y_pct,
                        phase="skipped",
                        note=f"could not open overlay {want_ov}",
                    )
                    skipped += 1
                    i += 1
                    continue
                current_overlay = want_ov

        # Collect a batch of same layout+overlay targets
        batch: list[ClickTargetSpec] = []
        while i < len(queue) and len(batch) < batch_size:
            n = queue[i]
            if n.layout_id != current_layout or n.overlay != current_overlay:
                break
            # Skip openers/closers when already inside that overlay (avoid toggle-off)
            if current_overlay:
                openers, closers = OVERLAY_IO.get(current_overlay, ((), ()))
                if n.button_id in openers or (
                    n.button_id in closers and n.button_id != "STATS_EXIT"
                ):
                    # still click unique overlay chrome once; skip pure openers
                    if n.button_id in openers:
                        logger.log(
                            client_id=client_id,
                            layout_id=n.layout_id,
                            button_id=n.button_id,
                            kind=n.kind,
                            overlay=n.overlay,
                            x_pct=n.x_pct,
                            y_pct=n.y_pct,
                            phase="skipped",
                            note="opener already used for this overlay",
                        )
                        skipped += 1
                        i += 1
                        continue
            batch.append(n)
            i += 1

        if not batch:
            continue

        # Credit gate once per batch (not per target — WinRM is expensive).
        credits, cred_detail = _read_credits(ip)
        block = _credits_block_reason(credits, cred_detail, min_credits=cfg.min_credits)
        if block:
            stop_reason = block
            _progress(progress, f"STOP: {block}")
            break

        needs_window = any(_needs_open_window(b) for b in batch)
        has_start = any(b.button_id == "START" for b in batch)
        if needs_window and wait_betting:
            # Burn the closed gap with random UI chrome clicks, then arm open.
            closed_n, abort = _run_closed_phase_burst(
                ip=ip,
                agent=agent,
                catalog=catalog,
                layout_id=current_layout,
                logger=logger,
                client_id=client_id,
                progress=progress,
                on_batch_done=on_batch_done,
            )
            if closed_n:
                clicked += closed_n
            if abort:
                aborted_early = True
                abort_reason = "on_batch_done requested abort (fault pack)"
                stop_reason = abort_reason
                _progress(progress, f"STOP: {abort_reason}")
                break
            # Re-check every window-dependent batch: a long overlay sweep can
            # outlast the 22s open, and START must land after chrome enables.
            if not _ensure_betting_open(ip, progress=progress, for_start=has_start):
                for b in batch:
                    logger.log(
                        client_id=client_id,
                        layout_id=b.layout_id,
                        button_id=b.button_id,
                        kind=b.kind,
                        overlay=b.overlay,
                        x_pct=b.x_pct,
                        y_pct=b.y_pct,
                        phase="skipped",
                        note="no open betting window",
                    )
                skipped += len(batch)
                continue
            if (
                cfg.cancel_cloth_before_bets
                and not cloth_cleared_this_window
                and not has_start
            ):
                try:
                    cancel_all_bets(ip)
                    cloth_cleared_this_window = True
                except Exception as e:  # noqa: BLE001
                    _progress(progress, f"cancel_all warn: {e}")

        steps: list[dict[str, Any]] = [
            {
                "type": "focus_process",
                "value": ROULETTE_FOCUS_PROCESS,
                "ms": cfg.focus_ms,
            }
        ]
        batch_events: list[ClickEvent] = []
        for b in batch:
            batch_events.append(
                logger.log(
                    client_id=client_id,
                    layout_id=b.layout_id,
                    button_id=b.button_id,
                    kind=b.kind,
                    overlay=b.overlay,
                    x_pct=b.x_pct,
                    y_pct=b.y_pct,
                    phase="planned",
                )
            )
            # START needs a slightly firmer press once the disc/button is live.
            if b.button_id == "START":
                click_ms = cfg.start_click_ms
                gap_ms = _gap_ms(cfg.start_gap_ms)
            else:
                click_ms = cfg.click_ms
                gap_ms = _gap_ms(cfg.gap_ms)
            steps.append(_click_step(b.x_pct, b.y_pct, ms=click_ms))
            steps.append({"type": "sleep", "ms": gap_ms})
            if b.button_id == "START" and cfg.start_double_tap:
                # Double-tap like strategy scripts — first may only focus.
                steps.append(_click_step(b.x_pct, b.y_pct, ms=click_ms))
                steps.append(
                    {"type": "sleep", "ms": _gap_ms(cfg.start_double_gap_ms)}
                )

        _progress(
            progress,
            f"batch {clicked + 1}-{clicked + len(batch)} "
            f"layout={current_layout} overlay={current_overlay or '-'} "
            f"ids={[b.button_id for b in batch]}",
        )
        ok, detail = run_input_script_on_cabinet(
            ip=ip,
            agent=agent,
            script={
                "defaultKeyDelayMs": cfg.default_key_delay_ms,
                "steps": steps,
            },
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=90,
        )
        for b in batch:
            batch_events.append(
                logger.log(
                    client_id=client_id,
                    layout_id=b.layout_id,
                    button_id=b.button_id,
                    kind=b.kind,
                    overlay=b.overlay,
                    x_pct=b.x_pct,
                    y_pct=b.y_pct,
                    phase="ok" if ok else "fail",
                    agent_ok=ok,
                    note=detail if not ok else "",
                )
            )
        if ok:
            clicked += len(batch)
            if has_start:
                # START burns the open window; next window-dependent batch re-arms.
                cloth_cleared_this_window = False
            # Base chrome openers (AYUDA/PAYTABLE/…) leave a modal up — dismiss it.
            opened = {
                PANEL_OPENERS[b.button_id]
                for b in batch
                if b.button_id in PANEL_OPENERS
                and (b.overlay is None or b.button_id == "MENU_AYUDA")
            }
            for ov in sorted(opened):
                _progress(progress, f"dismiss overlay after {ov} opener")
                _close_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=current_layout,
                    overlay=ov,
                    logger=logger,
                    client_id=client_id,
                )
                if current_overlay == ov:
                    current_overlay = None
        else:
            failed_batches += 1
            _progress(progress, f"batch FAIL: {detail}")
            # Recover from a stuck help/menu/stats panel after a failed batch.
            for ov in ("help", "menu", "statistics", "language"):
                _close_overlay(
                    ip=ip,
                    agent=agent,
                    layout_id=current_layout,
                    overlay=ov,
                    logger=logger,
                    client_id=client_id,
                )
            current_overlay = None

        if on_batch_done is not None:
            try:
                should_abort = bool(on_batch_done(batch, batch_events, ok))
            except Exception as e:  # noqa: BLE001
                _progress(progress, f"on_batch_done warn: {e}")
                should_abort = False
            if should_abort:
                aborted_early = True
                abort_reason = "on_batch_done requested abort (fault pack)"
                stop_reason = abort_reason
                _progress(progress, f"STOP: {abort_reason}")
                break

        # Clear cloth periodically so credit burn stays bounded
        if clicked and clicked % 24 == 0:
            try:
                cancel_all_bets(ip)
            except Exception:  # noqa: BLE001
                pass
            cloth_cleared_this_window = False  # re-arm next betting chunk
            credits, cred_detail = _read_credits(ip)
            block = _credits_block_reason(credits, cred_detail, min_credits=cfg.min_credits)
            if block:
                stop_reason = block
                _progress(progress, f"STOP: {block}")
                break

        # Random mode: occasional mid-run layout hop
        if mode == "random" and shuffle_layouts and clicked and clicked % 18 == 0:
            other = "layout2" if current_layout == "layout1" else "layout1"
            if other in layouts:
                if current_overlay:
                    _close_overlay(
                        ip=ip,
                        agent=agent,
                        layout_id=current_layout,
                        overlay=current_overlay,
                        logger=logger,
                        client_id=client_id,
                    )
                    current_overlay = None
                current_layout = _ensure_layout(
                    ip=ip,
                    agent=agent,
                    current=current_layout,
                    want=other,
                    logger=logger,
                    client_id=client_id,
                    progress=progress,
                )

    if current_overlay:
        _close_overlay(
            ip=ip,
            agent=agent,
            layout_id=current_layout,
            overlay=current_overlay,
            logger=logger,
            client_id=client_id,
        )

    summary = logger.summary()
    summary_path = logger.close()
    credits_after, _ = _read_credits(ip)
    msg = (
        f"clicked={clicked} skipped={skipped} failed_batches={failed_batches} "
        f"credits_after={credits_after} log={summary_path}"
    )
    if stop_reason:
        msg = f"{stop_reason} | {msg}"
    _progress(progress, msg)
    # Early abort for a fault pack is success for the hunt loop; credit stop is not.
    ok_run = (
        aborted_early
        or (stop_reason is None and failed_batches == 0 and clicked > 0)
    )
    (out / "bot_result.json").write_text(
        json.dumps(
            {
                "ok": ok_run,
                "stopped_no_credits": bool(stop_reason) and not aborted_early,
                "stop_reason": stop_reason,
                "aborted_early": aborted_early,
                "abort_reason": abort_reason,
                "message": msg,
                "clicked": clicked,
                "skipped": skipped,
                "failed_batches": failed_batches,
                "credits_after": credits_after,
                "summary": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RandomBotResult(
        ok=ok_run,
        message=msg,
        clicked=clicked,
        skipped=skipped,
        failed_batches=failed_batches,
        out_dir=str(out),
        summary=summary,
        aborted_early=aborted_early,
        abort_reason=abort_reason,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Roulette random / systematic click bot")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--out", default="")
    p.add_argument("--mode", choices=("systematic", "random"), default="systematic")
    p.add_argument("--layouts", default="layout1,layout2")
    p.add_argument("--start-layout", default="layout1")
    p.add_argument("--client-id", default="player0")
    p.add_argument("--max-clicks", type=int, default=0, help="0 = all")
    p.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="0 = use profile batch_size from bot_config.json",
    )
    p.add_argument(
        "--profile",
        default="",
        help="Bot timing profile (emulation|safe|custom). Default: active in JSON.",
    )
    p.add_argument(
        "--config",
        default="",
        help="Path to bot_config.json (default: automation/bot_config.json).",
    )
    p.add_argument("--no-wait-betting", action="store_true")
    p.add_argument("--verified-only", action="store_true")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Append to existing out click_log and skip already-ok targets",
    )
    p.add_argument(
        "--dismiss-overlays",
        action="store_true",
        help="Only dismiss stuck help/menu/stats/language panels, then exit",
    )
    args = p.parse_args(argv)
    if args.dismiss_overlays:
        ok, detail = dismiss_stuck_overlays(
            ip=args.ip, layout_id=args.start_layout, progress=print
        )
        print(json.dumps({"ok": ok, "detail": detail}))
        return 0 if ok else 1
    layouts = tuple(x.strip() for x in args.layouts.split(",") if x.strip())
    out = Path(args.out) if args.out else Path("automation_runs") / (
        time.strftime("%Y%m%d_%H%M%S") + f"_{args.ip.replace(':', '_')}_random_bot"
    )
    result = run_random_bot(
        ip=args.ip,
        out_dir=out,
        mode=args.mode,  # type: ignore[arg-type]
        layouts=layouts,
        start_layout=args.start_layout,
        client_ids=(args.client_id,),
        max_clicks=args.max_clicks or None,
        batch_size=args.batch_size or None,
        include_unverified=not args.verified_only,
        wait_betting=not args.no_wait_betting,
        resume=bool(args.resume),
        progress=print,
        bot_profile=args.profile or None,
        bot_config_path=args.config or None,
    )
    print(json.dumps({"ok": result.ok, "message": result.message, "out": result.out_dir}))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
