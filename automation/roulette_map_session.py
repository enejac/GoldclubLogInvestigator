"""
Three-role Layout1 map session (proper calibration flow).

Roles (run as separate agents / processes sharing one session dir):
  1. sniff  - WinDivert HttpGuiSniff on :8090 (+ godot1 PUT tail)
  2. place  - arm on bets-closed, click chip+spot, log coords, screenshot
  3. verify - correlate RCM + HTTP/godot actions with clicks; persist calibration

Session layout (under ``_tmp_logs/board_map_sessions/<id>/``)::

  plan.json           - chip, targets, layout_id, ip
  role_sniff.done.json
  sniff_dump.txt      - raw HttpGuiSniff
  http_events.jsonl   - parsed HTTP
  godot_puts.jsonl    - Sending put action lines during window
  role_place.done.json
  click_log.jsonl     - every click {name,x_pct,y_pct,ts,role}
  click_traffic.jsonl - joined click <-> middleware/godot codes (verify writes)
  screenshot.jpg
  rcm.json            - credits before/after
  verdict.json        - ok/fail + adjustments applied
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field, fields as dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_gui_traffic import (
    parse_godot_put_actions,
    parse_http_gui_dump_file,
    run_gui_sniff,
    summarize_events,
)
from automation.roulette_layout import (
    CANCEL_ALL_BUTTON,
    CHIP_SPOTS,
    DEFAULT_CHIP,
    NUMBER_SPOTS,
    OUTSIDE_SPOTS,
    ROULETTE_FOCUS_PROCESS,
    UI_BUTTONS,
    ClickTarget,
    all_bet_targets,
)
from automation.roulette_layout_store import (
    CalibrationHit,
    record_target,
    resolve_target,
    set_active_layout,
    spiral_offsets,
)
from automation.roulette_middleware import fetch_player_state
from automation.roulette_runner import (
    _CLOSED_RE,
    _OPEN_RE,
    _RCM_RE,
    _latest_file,
    _read_new_lines,
    wait_for_log_marker,
)
from automation.roulette_script import _click
from live_tail_io import read_new_bytes

SESSIONS_ROOT = Path("_tmp_logs") / "board_map_sessions"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _wait_file(path: Path, timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if path.is_file() and path.stat().st_size > 2:
            return True
        time.sleep(0.4)
    return False


def catalog_by_name() -> dict[str, ClickTarget]:
    cat: dict[str, ClickTarget] = {}
    for t in [*OUTSIDE_SPOTS, *CHIP_SPOTS, *UI_BUTTONS, *all_bet_targets()]:
        cat[t.name] = t
    for t in NUMBER_SPOTS.values():
        cat[t.name] = t
    return cat


@dataclass
class SessionPlan:
    session_id: str
    ip: str = "10.0.0.90"
    layout_id: str = "layout1"
    chip: str = "chip_1"
    targets: list[str] = field(default_factory=lambda: ["1"])
    # Middleware PlayerDataBets must match (Corner/Split false positives rejected).
    expect_bet_type: str = "Fields"  # straight-up on Alegro WebAPI
    expect_bet_id: str = "1"
    sniff_seconds: int = 55
    place_delay_after_sniff_start_sec: float = 8.0
    notes: str = "chip_1 on straight-up 1 (red pocket)"


def _bets_match_expected(
    bets: list[Any],
    *,
    expect_type: str,
    expect_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Return (ok, matching_bet)."""
    et = (expect_type or "").strip().lower()
    eid = (expect_id or "").strip()
    for b in bets or []:
        if not isinstance(b, dict):
            continue
        btype = str(b.get("BetType") or "")
        bid = str(b.get("Id") or "")
        type_ok = (not et) or et in btype.lower()
        id_ok = (not eid) or bid == eid or bid.split("+")[0] == eid
        # Straight-up on Alegro is BetType Fields with Id "1".."36" (not "Straight").
        if et in ("straight", "fields"):
            id_ok = bid == eid
            type_ok = ("fields" in btype.lower()) or ("straight" in btype.lower())
        if type_ok and id_ok:
            return True, b
    return False, None


def create_session(
    *,
    ip: str = "10.0.0.90",
    layout_id: str = "layout1",
    chip: str = "chip_1",
    targets: list[str] | None = None,
    sniff_seconds: int = 55,
    notes: str = "",
) -> Path:
    sid = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = SESSIONS_ROOT / sid
    root.mkdir(parents=True, exist_ok=True)
    tgs = targets or ["1"]
    plan = SessionPlan(
        session_id=sid,
        ip=ip,
        layout_id=layout_id,
        chip=chip,
        targets=tgs,
        # Alegro middleware uses BetType "Fields" for straight-up numbers.
        expect_bet_type="Fields" if len(tgs) == 1 and tgs[0].isdigit() else "",
        expect_bet_id=tgs[0] if len(tgs) == 1 else "",
        sniff_seconds=sniff_seconds,
        notes=notes or f"{chip} -> {','.join(tgs)}",
    )
    _write_json(root / "plan.json", asdict(plan))
    (root / "click_log.jsonl").write_text("", encoding="utf-8")
    (root / "click_traffic.jsonl").write_text("", encoding="utf-8")
    return root


def session_dir(session_id: str | Path) -> Path:
    p = Path(session_id)
    if p.is_dir():
        return p
    return SESSIONS_ROOT / str(session_id)


def load_plan(session: Path) -> SessionPlan:
    raw = _read_json(session / "plan.json")
    allowed = {f.name for f in dc_fields(SessionPlan)}
    return SessionPlan(**{k: v for k, v in raw.items() if k in allowed})


# --------------------------------------------------------------------------- sniff role
def role_sniff(session: Path) -> dict[str, Any]:
    plan = load_plan(session)
    set_active_layout(plan.layout_id)
    godot_log = _latest_file(Path(rf"\\{plan.ip}\c$\Goldclub\var\log\godot1"))
    godot_off = godot_log.stat().st_size if godot_log else 0
    started = _utc_now()
    marker = session / "sniff_started.json"
    _write_json(marker, {"started_utc": started, "seconds": plan.sniff_seconds})

    dump = run_gui_sniff(
        ip=plan.ip,
        seconds=plan.sniff_seconds,
        ports=[8090],
        out_dir=session / "sniff_raw",
    )
    local_dump = session / "sniff_dump.txt"
    local_dump.write_bytes(dump.read_bytes())

    events = parse_http_gui_dump_file(local_dump)
    http_path = session / "http_events.jsonl"
    if http_path.exists():
        http_path.unlink()
    for ev in events:
        _append_jsonl(
            http_path,
            {
                "t_ms": ev.t_ms,
                "wall": ev.wall,
                "direction": ev.direction,
                "method": ev.method,
                "status": ev.status,
                "path": ev.path,
                "action_hint": ev.action_hint,
                "body": ev.body[:2000],
            },
        )

    puts: list[dict[str, str]] = []
    if godot_log is not None:
        _, lines = _read_new_lines(godot_log, start_offset=godot_off)
        for action, data in parse_godot_put_actions(lines):
            rec = {"action": action, "data": data, "source": "godot1"}
            puts.append(rec)
            _append_jsonl(session / "godot_puts.jsonl", rec)

    done = {
        "role": "sniff",
        "ok": True,
        "started_utc": started,
        "ended_utc": _utc_now(),
        "dump": str(local_dump),
        "http_events": len(events),
        "http_summary": summarize_events(events),
        "godot_puts": len(puts),
    }
    _write_json(session / "role_sniff.done.json", done)
    return done


# --------------------------------------------------------------------------- place role
def _credits(ruleta: Path) -> int | None:
    try:
        size = ruleta.stat().st_size
        blob = read_new_bytes(ruleta, max(0, size - 100_000), size).decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(blob.splitlines()):
        m = _RCM_RE.search(line)
        if m:
            return int(m.group("c")) + int(m.group("p"))
    return None


def _run_clicks(ip: str, staged, clicks_spec: list[ClickTarget]) -> tuple[bool, str]:
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 200},
    ]
    for t in clicks_spec:
        steps.append(_click(t, ms=100, mode="window", calibrate=False))
        steps.append({"type": "sleep", "ms": 90})
    return run_input_script_on_cabinet(
        ip=ip,
        agent=staged,
        script={"defaultKeyDelayMs": 35, "steps": steps},
        focus_process=ROULETTE_FOCUS_PROCESS,
        timeout=60,
    )


def role_place(session: Path) -> dict[str, Any]:
    """
    Place chip on target(s) during an open window.

    Proof is middleware ``PlayerDataBets.Bets`` non-empty (not RCM credit churn).
    Screenshot is taken only after Bets[] confirms a chip is on the cloth.
    """
    plan = load_plan(session)
    set_active_layout(plan.layout_id)
    cat = catalog_by_name()
    chip_base = cat.get(plan.chip, DEFAULT_CHIP)
    cancel = resolve_target(CANCEL_ALL_BUTTON)
    spot_bases = [cat[n] for n in plan.targets if n in cat]
    if not spot_bases:
        raise KeyError(f"no valid targets in {plan.targets}")

    sniff_mark = session / "sniff_started.json"
    if _wait_file(sniff_mark, timeout_sec=90):
        time.sleep(max(0.0, float(plan.place_delay_after_sniff_start_sec)))

    roulette = _latest_file(Path(rf"\\{plan.ip}\c$\Goldclub\var\log\ruleta Roulette"))
    ruleta = _latest_file(Path(rf"\\{plan.ip}\c$\Goldclub\var\log\ruleta"))
    if roulette is None or ruleta is None:
        raise FileNotFoundError("missing ruleta logs")

    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=plan.ip, local_exe=local)

    before_state = fetch_player_state(plan.ip)
    before_credits = before_state.get("credits")
    if before_credits is None:
        before_credits = _credits(ruleta)

    clicks: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    bets_ok = False
    winning: dict[str, Any] | None = None
    agent_msg = ""
    shot_ok, shot_msg = False, ""
    shot = session / "screenshot.jpg"
    mw_after: dict[str, Any] = {}
    detail_c = ""
    detail_o = ""
    off = roulette.stat().st_size

    # Single-target map first (exact flow for full-board later).
    # InputAgent launch burns most of a ~22s open window, so each spiral
    # offset uses its own closed->open cycle.
    spot_base = resolve_target(spot_bases[0])
    chip = resolve_target(chip_base)
    # Session 20260724_085832: (17.29,57)->Corner 1+2+4+5; left/down->Split 1+2.
    # Corner hitbox sits near our geometric center, so Straight is further toward
    # the bottom of the cell (higher y%) and slightly left of the 1/4 boundary.
    interior = [
        (0.0, 2.5),
        (-0.4, 2.8),
        (0.4, 2.8),
        (-0.6, 3.2),
        (0.0, 3.5),
        (0.6, 3.0),
        (-0.8, 2.2),
        (0.3, 2.2),
        (-0.3, 4.0),
        (0.0, 1.8),
    ]
    offsets = interior


    for attempt, (dx, dy) in enumerate(offsets, start=1):
        off, ok_c, detail_c = wait_for_log_marker(
            roulette, start_offset=off, pattern=_CLOSED_RE, timeout_sec=90
        )
        if not ok_c:
            raise TimeoutError(detail_c)
        # Launch early on closed so clicks land at open (same pattern as runner).
        time.sleep(5.5)
        cand = ClickTarget(spot_base.name, spot_base.x_pct + dx, spot_base.y_pct + dy)
        # Clear -> arm chip_1 twice -> click candidate once.
        seq = [cancel, cancel, chip, chip, cand]
        ok_a, agent_msg = _run_clicks(plan.ip, staged, seq)
        # Track open marker for logs (may already have fired).
        off, ok_o, detail_o = wait_for_log_marker(
            roulette, start_offset=off, pattern=_OPEN_RE, timeout_sec=25
        )
        for t, kind in (
            (cancel, "ui"),
            (cancel, "ui"),
            (chip, "chip"),
            (chip, "chip"),
            (cand, "bet"),
        ):
            rec = {
                "ts_utc": _utc_now(),
                "name": t.name,
                "x_pct": t.x_pct,
                "y_pct": t.y_pct,
                "kind": kind,
                "attempt": attempt,
            }
            clicks.append(rec)
            _append_jsonl(session / "click_log.jsonl", rec)

        # Poll middleware IMMEDIATELY (PsExec screenshot is too slow and misses the open).
        match_ok = False
        matched: dict[str, Any] | None = None
        mw: dict[str, Any] = {}
        poll_deadline = time.time() + 3.5
        while time.time() < poll_deadline:
            mw = fetch_player_state(plan.ip)
            mw_after = mw
            match_ok, matched = _bets_match_expected(
                list(mw.get("bets") or []),
                expect_type=plan.expect_bet_type,
                expect_id=plan.expect_bet_id or cand.name,
            )
            if match_ok:
                break
            # Any bet (wrong type) still counts as a click landing — stop polling.
            if int(mw.get("bets_count") or 0) > 0:
                break
            time.sleep(0.25)

        hit = bool(mw.get("ok") and match_ok)
        wrong_bet = (not match_ok) and int(mw.get("bets_count") or 0) > 0

        # Screenshot only after we know cloth state (and only keep on true Straight hit).
        if hit or wrong_bet:
            try:
                from network.screen_capture import capture_remote_screen

                shot_ok, shot_msg = capture_remote_screen(plan.ip, str(shot))
            except Exception as exc:  # noqa: BLE001
                shot_msg = str(exc)

        attempts.append(
            {
                "attempt": attempt,
                "x_pct": cand.x_pct,
                "y_pct": cand.y_pct,
                "agent_ok": ok_a,
                "bets_count": mw.get("bets_count"),
                "bets": mw.get("bets"),
                "chip_id": mw.get("chip_id"),
                "credits": mw.get("credits"),
                "expect_bet_type": plan.expect_bet_type,
                "expect_bet_id": plan.expect_bet_id or cand.name,
                "match_ok": match_ok,
                "matched_bet": matched,
                "wrong_bet": wrong_bet,
                "hit": hit,
                "screenshot_ok": shot_ok,
            }
        )
        _append_jsonl(session / "place_attempts.jsonl", attempts[-1])
        if not hit:
            # Clear wrong/empty before next spiral offset.
            _run_clicks(plan.ip, staged, [cancel, cancel])
            continue

        bets_ok = True
        winning = {
            "name": cand.name,
            "x_pct": cand.x_pct,
            "y_pct": cand.y_pct,
            "attempt": attempt,
            "bets": mw.get("bets"),
            "matched_bet": matched,
            "bets_count": mw.get("bets_count"),
            "chip_id": mw.get("chip_id"),
            "credits": mw.get("credits"),
        }
        break

    after_credits = mw_after.get("credits")
    if after_credits is None:
        after_credits = _credits(ruleta)
    rcm = {
        "credits_before": before_credits,
        "credits_after": after_credits,
        "delta": (
            after_credits - before_credits
            if after_credits is not None and before_credits is not None
            else None
        ),
        "middleware_bets_count": mw_after.get("bets_count"),
        "middleware_chip_id": mw_after.get("chip_id"),
        "note": "credit delta alone is NOT proof; require bets_ok",
    }
    _write_json(session / "rcm.json", rcm)
    if winning is not None:
        _write_json(session / "winning_click.json", winning)

    done = {
        "role": "place",
        "ok": bool(bets_ok),
        "bets_ok": bool(bets_ok),
        "agent_msg": (agent_msg or "")[:300],
        "ended_utc": _utc_now(),
        "open_detail": (detail_o or "")[:160],
        "closed_detail": (detail_c or "")[:160],
        "clicks": clicks,
        "attempts": attempts,
        "winning_click": winning,
        "rcm": rcm,
        "middleware_after": {
            "ok": mw_after.get("ok"),
            "bets_count": mw_after.get("bets_count"),
            "chip_id": mw_after.get("chip_id"),
            "credits": mw_after.get("credits"),
            "bets": mw_after.get("bets"),
        },
        "screenshot": str(shot) if shot_ok else "",
        "screenshot_ok": shot_ok,
        "screenshot_msg": (shot_msg or "")[:200],
        "targets": plan.targets,
        "chip": plan.chip,
    }
    _write_json(session / "role_place.done.json", done)
    return done


# --------------------------------------------------------------------------- verify role
def role_verify(session: Path, *, wait_sec: float = 180.0) -> dict[str, Any]:
    plan = load_plan(session)
    set_active_layout(plan.layout_id)

    if not _wait_file(session / "role_place.done.json", wait_sec):
        raise TimeoutError("place role did not finish")
    _wait_file(session / "role_sniff.done.json", min(90.0, wait_sec))

    place = _read_json(session / "role_place.done.json")
    sniff = (
        _read_json(session / "role_sniff.done.json")
        if (session / "role_sniff.done.json").is_file()
        else {}
    )
    rcm = place.get("rcm") or {}
    delta = rcm.get("delta")
    clicks = place.get("clicks") or []
    winning = place.get("winning_click") or {}

    http_actions: list[dict[str, Any]] = []
    http_path = session / "http_events.jsonl"
    if http_path.is_file():
        for line in http_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            http_actions.append(json.loads(line))

    godot_puts: list[dict[str, Any]] = []
    gp = session / "godot_puts.jsonl"
    if gp.is_file():
        for line in gp.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                godot_puts.append(json.loads(line))

    put_codes = [f"{p.get('action')}:{p.get('data')}" for p in godot_puts]
    # Prefer HTTP bodies that actually contain a non-empty Bets array.
    bets_http = [
        h
        for h in http_actions
        if '"Bets":[' in (h.get("body") or "").replace(" ", "")
        and '"Bets":[]' not in (h.get("body") or "").replace(" ", "")
    ]
    interesting_http = [
        h
        for h in http_actions
        if (h.get("method") == "PUT")
        or (h.get("action_hint") in ("SetChip", "MenuCommands", "Paytable", "api_action"))
        or ("PlayerDataBets" in (h.get("body") or ""))
    ]

    for c in clicks:
        row = {
            "ts_utc": c.get("ts_utc"),
            "click_name": c.get("name"),
            "x_pct": c.get("x_pct"),
            "y_pct": c.get("y_pct"),
            "kind": c.get("kind"),
            "attempt": c.get("attempt"),
            "godot_put_actions": put_codes,
            "http_action_hints": sorted(
                {h.get("action_hint") or h.get("method") or "" for h in interesting_http}
            ),
            "http_paths": sorted({h.get("path") or "" for h in interesting_http if h.get("path")}),
            "middleware_bets_nonempty_http": len(bets_http),
            "credit_delta": delta,
            "bets_ok": bool(place.get("bets_ok")),
        }
        _append_jsonl(session / "click_traffic.jsonl", row)

    match_ok, matched = _bets_match_expected(
        list(winning.get("bets") or place.get("middleware_after", {}).get("bets") or []),
        expect_type=plan.expect_bet_type,
        expect_id=plan.expect_bet_id or (plan.targets[0] if plan.targets else ""),
    )
    bets_ok = bool(place.get("bets_ok")) and match_ok
    place_ok = bool(place.get("ok")) and bets_ok
    shot_ok = bool(place.get("screenshot_ok"))
    # Hard rule: middleware BetType/Id must match; credit-only is never enough.
    ok = place_ok and bets_ok and shot_ok and bool(winning) and match_ok

    adjustments: list[dict[str, Any]] = []
    if ok:
        hit = CalibrationHit(
            name=str(winning["name"]),
            x_pct=float(winning["x_pct"]),
            y_pct=float(winning["y_pct"]),
            verified=True,
            credit_delta=int(delta) if isinstance(delta, int) else None,
            http_actions=tuple(
                [
                    f"BetType:{(matched or {}).get('BetType')}",
                    f"Id:{(matched or {}).get('Id')}",
                    f"Bets:{winning.get('bets_count')}",
                ]
                + put_codes[:4]
            ),
            note="map_session_straight_bet_proof",
        )
        adjustments.append(
            {"name": winning["name"], "saved": record_target(hit, layout_id=plan.layout_id)}
        )
        chip_clicks = [c for c in clicks if c.get("kind") == "chip"]
        if chip_clicks:
            c0 = chip_clicks[-1]
            adjustments.append(
                {
                    "name": c0["name"],
                    "saved": record_target(
                        CalibrationHit(
                            name=str(c0["name"]),
                            x_pct=float(c0["x_pct"]),
                            y_pct=float(c0["y_pct"]),
                            verified=True,
                            credit_delta=0,
                            http_actions=tuple(put_codes[:6]),
                            note="map_session_chip",
                        ),
                        layout_id=plan.layout_id,
                    ),
                }
            )
    else:
        # Explicitly mark failed target as unverified so bots do not trust bad coords.
        cat = catalog_by_name()
        for name in plan.targets:
            base = cat.get(name)
            if base is None:
                continue
            cur = resolve_target(base, plan.layout_id)
            adjustments.append(
                {
                    "name": name,
                    "saved": record_target(
                        CalibrationHit(
                            name=name,
                            x_pct=float(winning.get("x_pct") or cur.x_pct),
                            y_pct=float(winning.get("y_pct") or cur.y_pct),
                            verified=False,
                            credit_delta=int(delta) if isinstance(delta, int) else None,
                            http_actions=(),
                            note="REJECTED_no_middleware_bets",
                        ),
                        layout_id=plan.layout_id,
                    ),
                }
            )

    verdict = {
        "role": "verify",
        "ok": ok,
        "ended_utc": _utc_now(),
        "place_ok": place_ok,
        "bets_ok": bets_ok,
        "screenshot_ok": shot_ok,
        "credit_ok_ignored": isinstance(delta, int) and delta < 0,
        "rcm": rcm,
        "winning_click": winning,
        "middleware_bets": winning.get("bets") if winning else place.get("middleware_after", {}).get("bets"),
        "matched_bet": matched,
        "expect_bet_type": plan.expect_bet_type,
        "expect_bet_id": plan.expect_bet_id,
        "sniff_http_events": sniff.get("http_events"),
        "sniff_summary": sniff.get("http_summary"),
        "http_nonempty_bets_msgs": len(bets_http),
        "godot_puts": put_codes,
        "adjustments": adjustments,
        "click_traffic_path": str(session / "click_traffic.jsonl"),
        "message": (
            f"VERIFIED: middleware {plan.expect_bet_type} id={plan.expect_bet_id} + screenshot"
            if ok
            else "FAILED: need exact Straight bet on target (Corner/Split/credit-only rejected)"
        ),
    }
    _write_json(session / "verdict.json", verdict)
    _write_json(session / "role_verify.done.json", verdict)
    return verdict


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Layout1 map session (sniff|place|verify|create)")
    p.add_argument("role", choices=("create", "sniff", "place", "verify", "run_all"))
    p.add_argument("--session", default="", help="session id or path (required except create)")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--layout", default="layout1")
    p.add_argument("--chip", default="chip_1")
    p.add_argument("--targets", default="1", help="comma names, e.g. 1 or RED")
    p.add_argument("--sniff-seconds", type=int, default=55)
    args = p.parse_args(argv)

    if args.role == "create":
        root = create_session(
            ip=args.ip,
            layout_id=args.layout,
            chip=args.chip,
            targets=[t.strip() for t in args.targets.split(",") if t.strip()],
            sniff_seconds=args.sniff_seconds,
        )
        print(root)
        return 0

    if not args.session:
        print("--session required", file=sys.stderr)
        return 2
    session = session_dir(args.session)

    if args.role == "sniff":
        print(json.dumps(role_sniff(session), indent=2))
        return 0
    if args.role == "place":
        print(json.dumps(role_place(session), indent=2))
        return 0
    if args.role == "verify":
        print(json.dumps(role_verify(session), indent=2))
        return 0

    # run_all sequential (agents do parallel; this is fallback)
    print(json.dumps(role_sniff(session), indent=2))
    print(json.dumps(role_place(session), indent=2))
    print(json.dumps(role_verify(session, wait_sec=30), indent=2))
    verdict = _read_json(session / "verdict.json")
    return 0 if verdict.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
