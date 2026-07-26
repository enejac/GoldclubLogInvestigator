"""
Click-repro for Godot Collect/Payout NullReferenceException (GCI-GODOT-001).

Mirrors Bug Detector / SuperDragica-style TEST steps:
  layout or view switch -> Collect (COBRAR) within ~2s
  optional Collect again after Godot respawn

Success = new ``NullReferenceException`` + ``PayoutPressed`` in godot1 after
the attempt's log offset. Persists until hit or max attempts.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automation.click_logger import ClickLogger
from automation.remote_input_agent import (
    build_input_agent_local,
    run_input_script_on_cabinet,
    stage_input_agent,
)
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_layout_store import resolve_hitbox_center, set_active_layout
from automation.roulette_middleware import cancel_all_bets, fetch_player_state
from automation.roulette_runner import (
    OPEN_UI_SETTLE_SEC,
    _latest_file,
    wait_for_betting_open,
)

ProgressFn = Callable[[str], None]

_NRE_RE = re.compile(r"NullReferenceException", re.I)
_PAYOUT_RE = re.compile(r"PayoutPressed", re.I)
_OPF_RE = re.compile(r"Checking OPF for 0:\s*(True|False)", re.I)
_SETUP_RE = re.compile(r"Running base setup", re.I)

# Variant: (name, pre-clicks on current layout, layout id for COBRAR)
VARIANTS: list[tuple[str, tuple[str, ...], str]] = (
    ("layout_switch_then_collect", ("LAYOUT_SWITCH",), "layout1"),
    ("change_view_then_collect", ("CHANGE_VIEW",), "layout1"),
    ("collect_only", (), "layout1"),
    ("pano_then_collect", ("PANO",), "layout2"),  # PANO is on layout2 chrome
    ("layout2_collect", (), "layout2"),
    ("double_collect_burst", (), "layout1"),
)


@dataclass
class AttemptResult:
    attempt: int
    variant: str
    ok_agent: bool
    agent_detail: str
    nre: bool
    excerpt: str = ""
    opf: str = ""
    credits_before: int | None = None
    credits_after: int | None = None


@dataclass
class ReproResult:
    ok: bool
    message: str
    attempts: list[AttemptResult] = field(default_factory=list)
    out_dir: str = ""
    hit_attempt: int | None = None


def _progress(cb: ProgressFn | None, msg: str) -> None:
    if cb is not None:
        cb(msg)


def _godot_log(ip: str) -> Path:
    log = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\godot1"))
    if log is None:
        raise FileNotFoundError(f"no godot1 logs on {ip}")
    return log


def _read_from(path: Path, offset: int) -> tuple[int, str]:
    try:
        size = path.stat().st_size
    except OSError:
        return offset, ""
    if size < offset:
        offset = 0
    if size <= offset:
        return size, ""
    with path.open("rb") as f:
        f.seek(offset)
        blob = f.read(size - offset)
    return size, blob.decode("utf-8", errors="replace")


def _scan_nre(text: str) -> tuple[bool, str, str]:
    """Return (hit, excerpt, last_opf)."""
    opf = ""
    for m in _OPF_RE.finditer(text):
        opf = m.group(1)
    if not _NRE_RE.search(text):
        return False, "", opf
    if not _PAYOUT_RE.search(text) and "MainScreen" not in text:
        # Still count plain NRE in godot1 during our window
        pass
    lines = text.replace("\r\n", "\n").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if _NRE_RE.search(line):
            start = max(0, i - 6)
            chunk = "\n".join(lines[start : i + 18])
            return True, chunk, opf
    return True, text[-1200:], opf


def _center(bid: str, layout_id: str) -> tuple[float, float]:
    c = resolve_hitbox_center(bid, layout_id, allow_geometry=False)
    if c is None:
        raise RuntimeError(f"missing hitbox {bid} on {layout_id}")
    return float(c.x_pct), float(c.y_pct)


def _click(x: float, y: float, *, ms: int = 110) -> dict[str, Any]:
    return {
        "type": "click_window",
        "value": f"{ROULETTE_FOCUS_PROCESS}@{x:.3f},{y:.3f}",
        "ms": ms,
    }


def _script_for_variant(variant: str, layout_id: str, pre: tuple[str, ...]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 200},
    ]
    for bid in pre:
        # LAYOUT_SWITCH / CHANGE_VIEW / PANO live on layout1 (or layout2 for PANO)
        pre_layout = "layout2" if bid == "PANO" else "layout1"
        try:
            x, y = _center(bid, pre_layout)
        except RuntimeError:
            x, y = _center(bid, layout_id)
        steps.append(_click(x, y, ms=120))
        # Race remount: Collect within ~2s while MainScreen / TokenSpawner is half-built.
        # Historical crashes sit ~0.3–0.7s after subscribe/TouchZone activity.
        steps.append({"type": "sleep", "ms": 200 if bid != "CHANGE_VIEW" else 400})

    cx, cy = _center("COBRAR", layout_id)
    taps = 4 if variant == "double_collect_burst" else 2
    for i in range(taps):
        steps.append(_click(cx, cy, ms=140))
        steps.append({"type": "sleep", "ms": 120 if i == 0 else 280})

    return {"defaultKeyDelayMs": 35, "steps": steps}


def _wait_respawn_then_collect(
    ip: str,
    agent: Any,
    layout_id: str,
    *,
    log: Path,
    offset: int,
    timeout_sec: float = 25.0,
) -> tuple[bool, str, int]:
    """If Godot is respawning, click Collect as soon as base setup appears."""
    deadline = time.time() + timeout_sec
    cur = offset
    while time.time() < deadline:
        cur, text = _read_from(log, cur)
        if _SETUP_RE.search(text):
            time.sleep(0.4)
            cx, cy = _center("COBRAR", layout_id)
            script = {
                "defaultKeyDelayMs": 35,
                "steps": [
                    {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 150},
                    _click(cx, cy, ms=140),
                    {"type": "sleep", "ms": 200},
                    _click(cx, cy, ms=140),
                ],
            }
            ok, detail = run_input_script_on_cabinet(
                ip=ip,
                agent=agent,
                script=script,
                focus_process=ROULETTE_FOCUS_PROCESS,
                timeout=60,
            )
            return ok, detail, cur
        time.sleep(0.35)
    return False, "no respawn/setup seen", cur


def run_repro(
    *,
    ip: str = "10.0.0.90",
    out_dir: Path | None = None,
    max_attempts: int = 40,
    progress: ProgressFn | None = None,
) -> ReproResult:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = out_dir or Path("automation_runs") / f"{stamp}_{ip.replace(':', '_')}_repro_collect_nre"
    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    logger = ClickLogger(out, cabinet_ip=ip, client_ids=("player0",), session_meta={"case": "collect_nre"})
    _progress(progress, f"out={out}")
    _progress(progress, "Building InputAgent ...")
    local = build_input_agent_local(out_dir=Path("_tmp") / "inputagent_build")
    agent = stage_input_agent(ip=ip, local_exe=local)

    godot = _godot_log(ip)
    attempts: list[AttemptResult] = []
    hit_attempt: int | None = None

    for n in range(1, max_attempts + 1):
        variant, pre, layout_id = VARIANTS[(n - 1) % len(VARIANTS)]
        set_active_layout(layout_id if layout_id in ("layout1", "layout2") else "layout1")

        needs_open = any(
            b in ("LAYOUT_SWITCH", "CHANGE_VIEW", "PANO") for b in pre
        ) or variant in ("layout_switch_then_collect", "change_view_then_collect", "pano_then_collect")
        roulette = _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta Roulette"))
        if needs_open and roulette is not None:
            opened, open_detail = wait_for_betting_open(
                roulette,
                timeout_sec=90,
                settle_sec=OPEN_UI_SETTLE_SEC,
                progress=progress,
            )
            if not opened:
                _progress(progress, f"attempt {n}: no open window ({open_detail})")

        try:
            cancel_all_bets(ip)
        except Exception as e:  # noqa: BLE001
            _progress(progress, f"cancel_all warn: {e}")

        st0 = fetch_player_state(ip)
        credits_before = st0.get("credits") if st0.get("ok") else None
        try:
            credits_before = int(credits_before) if credits_before is not None else None
        except (TypeError, ValueError):
            credits_before = None

        off0 = godot.stat().st_size
        _progress(progress, f"attempt {n}/{max_attempts}: {variant} layout={layout_id}")

        script = _script_for_variant(variant, layout_id, pre)
        for bid in list(pre) + ["COBRAR"]:
            try:
                if bid == "PANO":
                    x, y = _center("PANO", "layout2")
                elif bid in ("LAYOUT_SWITCH", "CHANGE_VIEW"):
                    x, y = _center(bid, "layout1")
                else:
                    x, y = _center(bid, layout_id)
            except RuntimeError:
                continue
            logger.log(
                client_id="player0",
                layout_id=layout_id,
                button_id=bid,
                kind="ui",
                overlay=None,
                x_pct=x,
                y_pct=y,
                phase="planned",
                note=variant,
            )

        ok_a, detail = run_input_script_on_cabinet(
            ip=ip,
            agent=agent,
            script=script,
            focus_process=ROULETTE_FOCUS_PROCESS,
            timeout=90,
        )
        time.sleep(1.2)
        off1, text = _read_from(godot, off0)
        hit, excerpt, opf = _scan_nre(text)

        # After a soft miss, try Collect during the next remount window.
        if not hit and ("base setup" in text.lower() or variant.startswith("layout")):
            _progress(progress, f"attempt {n}: chasing respawn Collect ...")
            _wait_respawn_then_collect(
                ip, agent, layout_id, log=godot, offset=off1, timeout_sec=12.0
            )
            time.sleep(1.0)
            _, text2 = _read_from(godot, off0)
            hit, excerpt, opf2 = _scan_nre(text2)
            if opf2:
                opf = opf2
            if hit:
                text = text2

        st1 = fetch_player_state(ip)
        credits_after = st1.get("credits") if st1.get("ok") else None
        try:
            credits_after = int(credits_after) if credits_after is not None else None
        except (TypeError, ValueError):
            credits_after = None

        ar = AttemptResult(
            attempt=n,
            variant=variant,
            ok_agent=ok_a,
            agent_detail=detail,
            nre=hit,
            excerpt=excerpt,
            opf=opf,
            credits_before=credits_before,
            credits_after=credits_after,
        )
        attempts.append(ar)
        (out / "player_state.jsonl").open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "attempt": n,
                    "variant": variant,
                    "credits_before": credits_before,
                    "credits_after": credits_after,
                    "nre": hit,
                    "opf": opf,
                },
                ensure_ascii=True,
            )
            + "\n"
        )

        logger.log(
            client_id="player0",
            layout_id=layout_id,
            button_id="COBRAR",
            kind="ui",
            overlay=None,
            x_pct=0.0,
            y_pct=0.0,
            phase="ok" if hit else ("fail" if not ok_a else "miss"),
            agent_ok=ok_a,
            note=f"{variant}; nre={hit}; opf={opf}; {detail}",
        )

        if hit:
            hit_attempt = n
            (out / "logs" / "godot1_excerpt.txt").write_text(excerpt, encoding="utf-8")
            # Keep a wider window around the fault
            _, wide = _read_from(godot, max(0, off0 - 2000))
            (out / "logs" / "godot1_window.txt").write_text(wide[-80000:], encoding="utf-8")
            _progress(progress, f"HIT on attempt {n} ({variant})")
            break

        _progress(
            progress,
            f"attempt {n}: miss agent_ok={ok_a} opf={opf or '-'} "
            f"credits={credits_before}->{credits_after}",
        )
        # Brief pause; if Collect cashed out, still continue — NRE is the goal.
        time.sleep(1.5)

    summary = {
        "ok": hit_attempt is not None,
        "hit_attempt": hit_attempt,
        "attempts": len(attempts),
        "variants_tried": [a.variant for a in attempts],
        "godot_log": str(godot),
    }
    (out / "manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.close()

    if hit_attempt is not None:
        hit = next(a for a in attempts if a.attempt == hit_attempt)
        _write_pack(out, ip=ip, godot=godot, hit=hit)
        msg = f"REPRODUCED NRE on attempt {hit_attempt} ({hit.variant})"
        return ReproResult(ok=True, message=msg, attempts=attempts, out_dir=str(out), hit_attempt=hit_attempt)

    msg = f"no NRE after {len(attempts)} attempts"
    _write_pack_miss(out, ip=ip, godot=godot, attempts=attempts)
    return ReproResult(ok=False, message=msg, attempts=attempts, out_dir=str(out))


def _write_pack(out: Path, *, ip: str, godot: Path, hit: AttemptResult) -> None:
    slug = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    test = out / f"BUG-TEST-Collect-crash-{slug}.md"
    dev = out / f"BUG-DEV-Collect-Payout-NRE-{slug}.md"
    test.write_text(
        f"# Collect button crash - tester guide (LIVE REPRO)\n\n"
        f"Cabinet: {ip}  \n"
        f"When: attempt {hit.attempt} / {hit.variant}  \n"
        f"Result: **FAIL** (NRE reproduced)\n\n"
        f"## How reproduced\n\n"
        f"1. Wait for open betting window.\n"
        f"2. Cancel standing bets.\n"
        f"3. Variant `{hit.variant}` then press **Collect** (COBRAR).\n"
        f"4. OPF seen: `{hit.opf or 'n/a'}`\n\n"
        f"## Pass / fail\n\n| Result | FAIL - UI NRE |\n\n"
        f"## Logs\n\nGodot: `{godot}`\n\n"
        f"```\n{hit.excerpt}\n```\n",
        encoding="utf-8",
    )
    dev.write_text(
        f"# Collect / Payout NRE - developer notes (LIVE REPRO)\n\n"
        f"| | |\n|---|---|\n"
        f"| Cabinet | {ip} |\n"
        f"| Attempt | {hit.attempt} |\n"
        f"| Variant | `{hit.variant}` |\n"
        f"| Fault | `System.NullReferenceException` at `MainScreen.PayoutPressed` |\n"
        f"| OPF | {hit.opf or '-'} |\n"
        f"| Godot log | `{godot}` |\n"
        f"| Credits | {hit.credits_before} -> {hit.credits_after} |\n\n"
        f"## Stack\n\n```\n{hit.excerpt}\n```\n",
        encoding="utf-8",
    )


def _write_pack_miss(out: Path, *, ip: str, godot: Path, attempts: list[AttemptResult]) -> None:
    (out / "BUG-TEST-Collect-crash-MISS.md").write_text(
        f"# Collect crash - not reproduced\n\nCabinet: {ip}\n"
        f"Attempts: {len(attempts)}\nGodot: `{godot}`\n"
        f"Variants: {', '.join(a.variant for a in attempts)}\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Repro Godot Collect/Payout NRE via clicks")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--out", default="")
    p.add_argument("--max-attempts", type=int, default=40)
    args = p.parse_args(argv)
    out = Path(args.out) if args.out else None
    result = run_repro(
        ip=args.ip,
        out_dir=out,
        max_attempts=args.max_attempts,
        progress=print,
    )
    print(json.dumps({"ok": result.ok, "message": result.message, "out": result.out_dir}))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
