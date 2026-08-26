"""
Layout1 board mapper: click candidates, watch RCM + optional :8090 sniff, auto-adjust misses.

Ground truth for a bet click is a credit drop (c+p) while bets are open / just after.
HTTP sniff enriches with SetChip / PUT bodies when present (board spots are often not named).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_gui_traffic import parse_godot_put_actions
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
    active_layout_id,
    record_target,
    resolve_target,
    set_active_layout,
    spiral_offsets,
)
from automation.roulette_runner import (
    _CLOSED_RE,
    _RCM_RE,
    _latest_file,
    _read_new_lines,
    wait_for_log_marker,
)
from automation.roulette_script import _click
from live_tail_io import read_new_bytes

ProgressFn = Callable[[str], None]

# Display credits use (c+p)/10000; one chip_1 unit = 10000 meter units.
_UNIT_METER = 10_000


@dataclass(slots=True)
class ProbeResult:
    name: str
    ok: bool
    x_pct: float
    y_pct: float
    credit_before: int | None
    credit_after: int | None
    credit_delta: int | None
    attempts: int = 1
    http_actions: list[str] = field(default_factory=list)
    note: str = ""


def _progress(cb: ProgressFn | None, msg: str) -> None:
    if cb is not None:
        cb(msg)


def _last_rcm(log: Path) -> tuple[int | None, int | None]:
    """Return (staked, credits_c_plus_p) from the newest RCM line in the file tail."""
    try:
        size = log.stat().st_size
        start = max(0, size - 120_000)
        blob = read_new_bytes(log, start, size)
        text = blob.decode("utf-8", errors="replace")
    except OSError:
        return None, None
    staked = credits = None
    for line in reversed(text.splitlines()):
        m = _RCM_RE.search(line)
        if not m:
            continue
        staked = int(m.group("staked"))
        credits = int(m.group("c")) + int(m.group("p"))
        return staked, credits
    return None, None


def _wait_credit_change(
    log: Path,
    *,
    before: int,
    timeout_sec: float = 8.0,
    poll: float = 0.35,
) -> tuple[int | None, int | None]:
    """Poll until credits differ from *before*. Returns (staked, credits)."""
    deadline = time.time() + timeout_sec
    last_s, last_c = _last_rcm(log)
    while time.time() < deadline:
        s, c = _last_rcm(log)
        if c is not None and c != before:
            return s, c
        last_s, last_c = s, c
        time.sleep(poll)
    return last_s, last_c


def _probe_script(target: ClickTarget, *, chip: ClickTarget = DEFAULT_CHIP) -> dict:
    # calibrate=False: caller supplies absolute % (including spiral nudges).
    steps: list[dict] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 250},
    ]
    cancel = resolve_target(CANCEL_ALL_BUTTON)
    chip_t = resolve_target(chip)
    for _ in range(2):
        steps.append(_click(cancel, ms=80, mode="window", calibrate=False))
        steps.append({"type": "sleep", "ms": 90})
    for _ in range(2):
        steps.append(_click(chip_t, ms=110, mode="window", calibrate=False))
        steps.append({"type": "sleep", "ms": 90})
    steps.append(_click(target, ms=90, mode="window", calibrate=False))
    steps.append({"type": "sleep", "ms": 450})
    return {"defaultKeyDelayMs": 35, "steps": steps}


def _arm_for_open(ip: str, progress: ProgressFn | None) -> Path:
    roulette_log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
    if roulette_log is None:
        raise FileNotFoundError("No ruleta Roulette log on cabinet")
    off = roulette_log.stat().st_size
    _progress(progress, "Waiting for bets closed (arm for next open) ...")
    off, ok, detail = wait_for_log_marker(
        roulette_log, start_offset=off, pattern=_CLOSED_RE, timeout_sec=90
    )
    if not ok:
        raise TimeoutError(detail)
    _progress(progress, f"Closed - sleeping 6s before click window ({detail[:80]})")
    time.sleep(6.0)
    return roulette_log


def probe_target(
    *,
    ip: str,
    target: ClickTarget,
    staged,
    ruleta_log: Path,
    godot_log: Path | None,
    min_credit_drop: int = _UNIT_METER,
    layout_id: str = "layout1",
    auto_adjust: bool = True,
    max_adjust_step: float = 2.0,
    progress: ProgressFn | None = None,
) -> ProbeResult:
    """
    Clear + chip_1 + click *target*. On miss, spiral-search nearby % and persist hit.
    """
    base = resolve_target(target, layout_id)
    offsets = spiral_offsets(max_step=max_adjust_step, step=0.5) if auto_adjust else [(0.0, 0.0)]
    godot_off = godot_log.stat().st_size if godot_log is not None else 0

    for attempt, (dx, dy) in enumerate(offsets, start=1):
        cand = ClickTarget(base.name, base.x_pct + dx, base.y_pct + dy)
        _progress(
            progress,
            f"Probe {cand.name} try {attempt}/{len(offsets)} "
            f"@ {cand.x_pct:.2f},{cand.y_pct:.2f}",
        )
        _, credits_before = _last_rcm(ruleta_log)
        if credits_before is None:
            return ProbeResult(
                name=cand.name,
                ok=False,
                x_pct=cand.x_pct,
                y_pct=cand.y_pct,
                credit_before=None,
                credit_after=None,
                credit_delta=None,
                attempts=attempt,
                note="no RCM before click",
            )

        script = _probe_script(cand)
        ok_a, msg = run_input_script_on_cabinet(
            ip=ip,
            agent=staged,
            script=script,
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=60,
        )
        if not ok_a:
            _progress(progress, f"  agent fail: {msg[:120]}")
            continue

        actions: list[str] = []
        if godot_log is not None:
            godot_off, glines = _read_new_lines(godot_log, start_offset=godot_off)
            actions = [f"{a}:{d}" for a, d in parse_godot_put_actions(glines)]

        _, credits_after = _wait_credit_change(
            ruleta_log, before=credits_before, timeout_sec=6.0
        )
        delta = None
        if credits_after is not None:
            delta = credits_after - credits_before

        hit = delta is not None and delta <= -min_credit_drop
        soft_ui = cand.name.startswith("chip_") or cand.name in {b.name for b in UI_BUTTONS}
        if soft_ui:
            if any(a.lower().startswith("setchip") for a in actions):
                hit = True
                note_soft = "setchip"
            elif cand.name.startswith("chip_"):
                # Re-selecting the already-active chip often emits no PUT and no credit
                # change. Cap spiral search: accept first agent-OK click as calibrated.
                hit = attempt == 1
                note_soft = "chip_best_effort" if hit else "chip_no_signal"
            elif actions:
                hit = True
                note_soft = "ui_put"
            else:
                hit = True
                note_soft = "ui_best_effort"
        else:
            note_soft = ""

        note = "credit_drop" if (delta is not None and delta < 0) else "no_credit_drop"
        if soft_ui and hit and (delta is None or delta >= 0):
            note = note_soft or "ui_or_chip_signal"
        if actions:
            note += "; http=" + ",".join(actions[:4])

        record_target(
            CalibrationHit(
                name=cand.name,
                x_pct=cand.x_pct,
                y_pct=cand.y_pct,
                verified=hit,
                credit_delta=delta,
                http_actions=tuple(actions[:8]),
                note=note,
            ),
            layout_id=layout_id,
        )

        if hit:
            _progress(
                progress,
                f"  HIT {cand.name} delta={delta} actions={actions[:3]}",
            )
            return ProbeResult(
                name=cand.name,
                ok=True,
                x_pct=cand.x_pct,
                y_pct=cand.y_pct,
                credit_before=credits_before,
                credit_after=credits_after,
                credit_delta=delta,
                attempts=attempt,
                http_actions=actions,
                note=note,
            )
        _progress(progress, f"  miss delta={delta}; nudging ...")

    return ProbeResult(
        name=base.name,
        ok=False,
        x_pct=base.x_pct,
        y_pct=base.y_pct,
        credit_before=None,
        credit_after=None,
        credit_delta=None,
        attempts=len(offsets),
        note="exhausted spiral search",
    )


def default_map_targets(*, include_inside: bool = False) -> list[ClickTarget]:
    """Prioritized Layout1 sample set (anchors first, then full cloth optionally)."""
    by_name = {t.name: t for t in OUTSIDE_SPOTS}
    anchors = [
        by_name["RED"],
        by_name["BLACK"],
        by_name["1-12"],
        by_name["ODD"],
        NUMBER_SPOTS[0],
        NUMBER_SPOTS[37],  # 00
        NUMBER_SPOTS[1],
        NUMBER_SPOTS[17],
        NUMBER_SPOTS[36],
        DEFAULT_CHIP,
        CANCEL_ALL_BUTTON,
    ]
    if include_inside:
        anchors.extend(all_bet_targets())
    # de-dupe preserving order
    seen: set[str] = set()
    out: list[ClickTarget] = []
    for t in anchors:
        if t.name in seen:
            continue
        seen.add(t.name)
        out.append(t)
    return out


def map_board(
    *,
    ip: str = "10.0.0.90",
    layout_id: str = "layout1",
    targets: Iterable[ClickTarget] | None = None,
    include_inside: bool = False,
    auto_adjust: bool = True,
    progress: ProgressFn | None = None,
) -> list[ProbeResult]:
    set_active_layout(layout_id)
    if layout_id == "layout2":
        _progress(
            progress,
            "WARNING: layout2 (Crycle) has no base geometry yet - probes use layout1 anchors.",
        )

    ruleta_log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta"))
    godot_log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\godot1"))
    if ruleta_log is None:
        raise FileNotFoundError("No ruleta log")

    _progress(progress, "Building InputAgent ...")
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=ip, local_exe=local)

    wanted = list(targets) if targets is not None else default_map_targets(
        include_inside=include_inside
    )
    results: list[ProbeResult] = []

    for idx, target in enumerate(wanted, start=1):
        _progress(progress, f"=== [{idx}/{len(wanted)}] {target.name} ({layout_id}) ===")
        _arm_for_open(ip, progress)
        res = probe_target(
            ip=ip,
            target=target,
            staged=staged,
            ruleta_log=ruleta_log,
            godot_log=godot_log,
            layout_id=layout_id,
            auto_adjust=auto_adjust,
            progress=progress,
        )
        results.append(res)
        # Clear leftovers so the next probe starts clean.
        clear = {
            "defaultKeyDelayMs": 35,
            "steps": [
                {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 150},
                _click(CANCEL_ALL_BUTTON, ms=80, mode="window"),
                {"type": "sleep", "ms": 80},
                _click(CANCEL_ALL_BUTTON, ms=80, mode="window"),
            ],
        }
        run_input_script_on_cabinet(
            ip=ip,
            agent=staged,
            script=clear,
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=45,
        )

    ok_n = sum(1 for r in results if r.ok)
    _progress(progress, f"Done: {ok_n}/{len(results)} verified on {layout_id}")
    return results


def auto_adjust_misclick(
    *,
    ip: str,
    target: ClickTarget,
    layout_id: str | None = None,
    progress: ProgressFn | None = None,
) -> ProbeResult:
    """Public helper used by the live bot when a planned bet does not land."""
    lid = layout_id or active_layout_id()
    ruleta_log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta"))
    godot_log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\godot1"))
    if ruleta_log is None:
        raise FileNotFoundError("No ruleta log")
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    staged = stage_input_agent(ip=ip, local_exe=local)
    _arm_for_open(ip, progress)
    return probe_target(
        ip=ip,
        target=target,
        staged=staged,
        ruleta_log=ruleta_log,
        godot_log=godot_log,
        layout_id=lid,
        auto_adjust=True,
        progress=progress,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Map Layout1 clickable board with auto-adjust")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--layout", default="layout1", choices=("layout1", "layout2"))
    p.add_argument("--targets", default="", help="Comma names (default: anchors)")
    p.add_argument("--all-bets", action="store_true", help="Include every bet surface")
    p.add_argument("--no-adjust", action="store_true")
    p.add_argument("--out", default="_tmp_logs/board_map_layout1.json")
    args = p.parse_args(argv)

    selected: list[ClickTarget] | None = None
    if args.targets.strip():
        catalog = {t.name: t for t in [*OUTSIDE_SPOTS, *CHIP_SPOTS, *UI_BUTTONS, *all_bet_targets()]}
        for n, t in NUMBER_SPOTS.items():
            catalog[t.name] = t
        selected = []
        for name in args.targets.split(","):
            name = name.strip()
            if not name:
                continue
            if name not in catalog:
                print(f"unknown target {name!r}", file=sys.stderr)
                return 2
            selected.append(catalog[name])

    def prog(m: str) -> None:
        print(m, flush=True)

    results = map_board(
        ip=args.ip,
        layout_id=args.layout,
        targets=selected,
        include_inside=bool(args.all_bets) and selected is None,
        auto_adjust=not args.no_adjust,
        progress=prog,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "layout_id": args.layout,
        "ip": args.ip,
        "results": [asdict(r) for r in results],
        "ok": sum(1 for r in results if r.ok),
        "total": len(results),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out} ok={payload['ok']}/{payload['total']}", flush=True)
    return 0 if payload["ok"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
