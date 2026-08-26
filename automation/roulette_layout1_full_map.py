"""
Full layout1 (futura_doublezero) surface map.

Probes every catalog target (chips, UI, straights, outside, inside lines) plus a
coarse dead-zone grid. Each click is proven via middleware GET /api/data/0.

Clear: CancelAllBets API + UI CANCELAR TODO (85%,90.5) — never BORRADOR alone.
Chip: SetChip API + UI chip_1 arm.

Resumable state: ``_tmp_logs/layout1_full_map/state.json``
Registry out: ``automation/layouts/layout1_hitboxes.json`` (+ dead_zones).
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
from automation.roulette_layout import (
    CANCEL_ALL_BUTTON,
    DEFAULT_CHIP,
    SPIN_BUTTON,
    ClickTarget,
    all_scan_targets,
    board_catalog,
)
from automation.roulette_layout_store import resolve_target
from automation.roulette_ui_areas import build_ui_hitbox_fields
from automation.roulette_middleware import (
    cancel_all_bets,
    fetch_player_state,
    set_chip,
)
from automation.roulette_runner import _CLOSED_RE, _latest_file, wait_for_log_marker
from automation.roulette_script import _click

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = REPO / "_tmp_logs" / "layout1_full_map"
STATE_PATH = STATE_DIR / "state.json"
REGISTRY = REPO / "automation" / "layouts" / "layout1_hitboxes.json"
IP_DEFAULT = "10.0.0.90"
CHUNK = 10  # probes per open window


def _dead_zone_grid() -> list[ClickTarget]:
    """Coarse samples in areas that should often be non-betting chrome."""
    pts: list[ClickTarget] = []
    # Top chrome / wheel area (avoid START center)
    for x in (10, 25, 40, 60, 75, 90):
        for y in (10, 18, 25):
            if abs(x - 49.5) < 8 and abs(y - 6.5) < 5:
                continue
            pts.append(ClickTarget(f"dead_{x}_{y}", float(x), float(y)))
    # Between cloth and chip bar
    for x in (20, 40, 60, 80):
        pts.append(ClickTarget(f"dead_{x}_78", float(x), 78.0))
    # Far left / right gutters
    for y in (35, 45, 55, 65):
        pts.append(ClickTarget(f"dead_5_{y}", 5.0, float(y)))
        pts.append(ClickTarget(f"dead_95_{y}", 95.0, float(y)))
    return pts


def _load_state() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
    return {
        "layout_id": "layout1",
        "probes": {},
        "dead_zones": {},
        "buttons": {},
        "client": {"width": 1920, "height": 1080},
    }


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _pct_to_px(x_pct: float, y_pct: float, client: dict[str, Any]) -> tuple[int, int]:
    cw = int(client.get("width") or 1920)
    ch = int(client.get("height") or 1080)
    return int(round(cw * x_pct / 100.0)), int(round(ch * y_pct / 100.0))


def _classify(
    name: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    b_bets = before.get("bets") or []
    a_bets = after.get("bets") or []
    b_chip = before.get("chip_id")
    a_chip = after.get("chip_id")
    kind = "dead"
    expect = None
    note = ""
    if len(a_bets) > len(b_bets) or (a_bets and a_bets != b_bets):
        kind = "bet"
        b0 = a_bets[0] if isinstance(a_bets[0], dict) else {}
        expect = {
            "BetType": str(b0.get("BetType") or ""),
            "Id": str(b0.get("Id") or ""),
        }
        note = f"middleware {expect['BetType']}/{expect['Id']}"
    elif a_chip is not None and a_chip != b_chip:
        kind = "chip_select"
        note = f"chip_id {b_chip}->{a_chip}"
    elif name.upper().startswith("CANCEL") and not a_bets and b_bets:
        kind = "ui_clear"
        note = "cleared bets"
    elif name in (
        "VECINOS",
        "FINALES",
        "COMPLETO",
        "VECINOS_0",
        "HUERFANOS",
        "VECINOS_00",
        "PAYTABLE",
        "CALIENTE_FRIO",
        "MUESTRA_GANANCIAS",
        "DENOM",
        "REPETIR",
        "BORRADOR",
        "START",
    ):
        # UI may open layer2 / change denom without bet delta — mark ui_probe.
        if a_bets == b_bets and a_chip == b_chip:
            kind = "ui_or_dead"
            note = "no bet/chip delta (may open overlay — layer2 later)"
        else:
            kind = "ui_effect"
            note = "state changed"
    return {
        "kind": kind,
        "expect": expect,
        "bets_after": a_bets[:3],
        "chip_before": b_chip,
        "chip_after": a_chip,
        "note": note,
    }


def _probe_chunk(
    *,
    ip: str,
    targets: list[ClickTarget],
    agent: Any,
    chip: ClickTarget,
    cancel: ClickTarget,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if roulette is None:
        raise FileNotFoundError("ruleta Roulette log missing")
    print(f"  waiting closed for chunk of {len(targets)}...", flush=True)
    off = roulette.stat().st_size
    off, ok, detail = wait_for_log_marker(
        roulette, start_offset=off, pattern=_CLOSED_RE, timeout_sec=90
    )
    if not ok:
        raise TimeoutError(detail)
    print("  closed - open in 5.5s", flush=True)
    time.sleep(5.5)

    for t in targets:
        cancel_all_bets(ip)
        time.sleep(0.05)
        set_chip(ip, 0)
        time.sleep(0.05)
        before = fetch_player_state(ip)
        script = {
            "defaultKeyDelayMs": 30,
            "steps": [
                _click(cancel, ms=80, mode="window", calibrate=False),
                {"type": "sleep", "ms": 80},
                _click(chip, ms=90, mode="window", calibrate=False),
                {"type": "sleep", "ms": 70},
                _click(t, ms=90, mode="window", calibrate=False),
                {"type": "sleep", "ms": 220},
            ],
        }
        ok_run, detail = run_input_script_on_cabinet(
            ip=ip, agent=agent, script=script, focus_process="godot", timeout=70
        )
        time.sleep(0.15)
        after = fetch_player_state(ip)
        cls = _classify(t.name, before, after)
        client = {"width": 1920, "height": 1080}
        ui_area = build_ui_hitbox_fields(t.name, client_w=1920, client_h=1080)
        if ui_area is not None:
            rec = {
                "id": t.name,
                **ui_area,
                "place_bet": place_bet_for_button(t.name),
                "chip": "chip_1",
                "chip_index": 0,
                "clear": "CancelAllBets",
                "probe": cls,
                "agent_ok": ok_run,
            }
        else:
            px = _pct_to_px(t.x_pct, t.y_pct, client)
            rec = {
                "id": t.name,
                "x_pct": round(t.x_pct, 3),
                "y_pct": round(t.y_pct, 3),
                "x": px[0],
                "y": px[1],
                "width": 8,
                "height": 8,
                "click_center_px": {"x": px[0], "y": px[1]},
                "click_center_pct": {"x_pct": round(t.x_pct, 3), "y_pct": round(t.y_pct, 3)},
                "input_ok": ok_run,
                "input_detail": detail if not ok_run else "",
                **cls,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        # Prefer catalog PlaceBet grammar when we know it
        pb = place_bet_for_button(t.name) or place_bet_for_button(f"straightup_{t.name}")
        if pb:
            rec["place_bet"] = pb
        results.append(rec)
        print(
            f"    {t.name}: {cls['kind']} {cls.get('note') or ''}",
            flush=True,
        )
        cancel_all_bets(ip)
        time.sleep(0.05)
    return results


def _write_registry(state: dict[str, Any]) -> None:
    buttons: dict[str, Any] = {}
    dead: dict[str, Any] = {}
    for name, rec in (state.get("probes") or {}).items():
        kind = rec.get("kind")
        entry = {
            "id": name,
            "x": rec.get("x"),
            "y": rec.get("y"),
            "width": rec.get("width", 8),
            "height": rec.get("height", 8),
            "click_center_px": rec.get("click_center_px"),
            "click_center_pct": rec.get("click_center_pct"),
            "kind": kind,
            "expect": rec.get("expect"),
            "place_bet": rec.get("place_bet"),
            "note": rec.get("note"),
            "client": state.get("client"),
            "chip": "chip_1",
            "chip_index": 0,
            "clear": "CancelAllBets",
        }
        if kind == "bet":
            buttons[name] = entry
        elif kind in ("dead", "ui_or_dead") or name.startswith("dead_"):
            dead[name] = entry
        else:
            buttons[name] = entry  # chips / ui still useful

    # Keep prior straightup_red_1 hitbox if richer
    if REGISTRY.is_file():
        prev = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
        old = (prev.get("buttons") or {}).get("straightup_red_1")
        if old and int(old.get("hit_count") or 0) > 0:
            buttons["straightup_red_1"] = old
            # also alias number "1"
            if "1" not in buttons:
                buttons["1"] = {
                    **{k: v for k, v in old.items() if k != "id"},
                    "id": "1",
                }

    out = {
        "layout_id": "layout1",
        "layer": 1,
        "buttons": buttons,
        "dead_zones": dead,
        "catalog": board_catalog(),
        "mapped_count": len(state.get("probes") or {}),
        "source": str(STATE_PATH).replace("\\", "/"),
    }
    REGISTRY.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    (STATE_DIR / "layout1_full_map.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )


def map_layout1(*, ip: str = IP_DEFAULT, include_dead: bool = True, resume: bool = True) -> dict[str, Any]:
    state = _load_state() if resume else {
        "layout_id": "layout1",
        "probes": {},
        "dead_zones": {},
        "buttons": {},
        "client": {"width": 1920, "height": 1080},
    }
    targets = list(all_scan_targets())
    targets.append(SPIN_BUTTON)
    if include_dead:
        targets.extend(_dead_zone_grid())

    done = set(state.get("probes") or {})
    pending = [t for t in targets if t.name not in done]
    print(
        f"layout1 map: {len(done)} done, {len(pending)} pending, catalog={board_catalog()}",
        flush=True,
    )
    if not pending:
        _write_registry(state)
        return state

    st = fetch_player_state(ip)
    if not st.get("ok"):
        raise RuntimeError(f"middleware unreachable: {st.get('error')}")
    if int(st.get("credits") or 0) < 10000:
        raise RuntimeError(
            f"credits too low ({st.get('credits')}); AFT top-up required before mapping"
        )

    local = build_input_agent_local(out_dir=REPO / "_tmp" / "inputagent_build")
    agent = stage_input_agent(ip=ip, local_exe=local)
    chip = resolve_target(DEFAULT_CHIP)
    cancel = resolve_target(CANCEL_ALL_BUTTON)

    for i in range(0, len(pending), CHUNK):
        chunk = pending[i : i + CHUNK]
        print(f"=== chunk {i // CHUNK + 1} ({len(chunk)} targets) ===", flush=True)
        try:
            rows = _probe_chunk(
                ip=ip, targets=chunk, agent=agent, chip=chip, cancel=cancel
            )
        except Exception as exc:  # noqa: BLE001
            print(f"chunk error: {exc}", flush=True)
            _save_state(state)
            _write_registry(state)
            raise
        for rec in rows:
            state["probes"][rec["id"]] = rec
            if rec.get("kind") in ("dead", "ui_or_dead") or rec["id"].startswith("dead_"):
                state["dead_zones"][rec["id"]] = rec
            else:
                state["buttons"][rec["id"]] = rec
        _save_state(state)
        _write_registry(state)
        print(f"  saved {len(state['probes'])} probes", flush=True)

    return state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Full layout1 surface mapper")
    p.add_argument("--ip", default=IP_DEFAULT)
    p.add_argument("--no-dead", action="store_true")
    p.add_argument("--fresh", action="store_true")
    args = p.parse_args(argv)
    state = map_layout1(
        ip=args.ip, include_dead=not args.no_dead, resume=not args.fresh
    )
    print(
        json.dumps(
            {
                "mapped": len(state.get("probes") or {}),
                "bets": sum(
                    1 for r in (state.get("probes") or {}).values() if r.get("kind") == "bet"
                ),
                "dead": len(state.get("dead_zones") or {}),
                "registry": str(REGISTRY),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
