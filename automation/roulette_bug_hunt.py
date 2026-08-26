"""
Roulette bug-hunt loop: random clicks + godot1 critical watch + auto repro packs.

Discovery half of the user-like repro rule:
  click forever -> on NullReference/Unhandled/etc. freeze click trail ->
  emit BUG-TEST + BUG-DEV pack under automation_runs/ -> keep hunting.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automation.click_logger import ClickEvent
from automation.roulette_middleware import cancel_all_bets, fetch_player_state, set_chip
from automation.roulette_random_bot import (
    MIN_CREDITS_TO_RUN,
    ClickTargetSpec,
    dismiss_stuck_overlays,
    run_random_bot,
)
from automation.roulette_runner import _latest_file
from network.bug_session_classify import classify_line

ROOT = Path(__file__).resolve().parents[1]
# Kept for callers/docs; live inject routes via egm_credit_inject (slot vs roulette).
AFT_SCRIPT = ROOT / "Invoke-WinDivertAftRoulette.ps1"

_CRITICAL_RE = re.compile(
    r"NullReferenceException|Unhandled exception|exited unexpectedly|"
    r"Object reference not set|AccessViolation",
    re.I,
)
_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _credits(ip: str) -> int | None:
    """Credits from middleware (:8090) or DeviceManager/SlotLog when slot is up."""
    from automation.egm_credit_inject import read_credits

    return read_credits(ip).credits


def _aft_topup(ip: str, amount_cents: int, log: Path) -> bool:
    """Top up via product-aware AFT/BillInject (slot :30800 vs roulette :30300/:8090)."""
    from automation.egm_credit_inject import ensure_credits

    print(f"[{_utc()}] credit top-up {amount_cents} cents on {ip} ...", flush=True)
    ok, msg, after = ensure_credits(
        ip,
        min_credits=MIN_CREDITS_TO_RUN,
        aft_cents=int(amount_cents),
        log=log,
        try_dallas=False,
    )
    print(
        f"[{_utc()}] credit top-up ok={ok} kind={after.kind} "
        f"credits={after.credits} {msg}",
        flush=True,
    )
    return ok


def _godot_log(ip: str) -> Path | None:
    return _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\godot1"))


def _ruleta_log(ip: str) -> Path | None:
    return _latest_file(Path(rf"\\{ip}\c$\Goldclub\var\log\ruleta"))


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


@dataclass
class FaultHit:
    source: str
    summary: str
    excerpt: str
    fault_ts: str
    raw_line: str
    offset_after: int


@dataclass
class HuntState:
    ip: str
    hunt_root: Path
    trail: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    godot_offset: int = 0
    last_pack_fault_key: str = ""
    packs: list[str] = field(default_factory=list)
    cooldown_until: float = 0.0
    stop_on_first: bool = False
    pack_count: int = 0


def _append_trail(state: HuntState, events: list[ClickEvent]) -> None:
    for ev in events:
        state.trail.append(ev.to_dict())
    # Mirror rolling trail into hunt-level click_log for continuity.
    roll = state.hunt_root / "click_log.jsonl"
    with roll.open("a", encoding="utf-8") as fh:
        for ev in events:
            if ev.phase in ("ok", "fail", "skipped"):
                fh.write(json.dumps(ev.to_dict(), ensure_ascii=True) + "\n")


def scan_godot_critical(ip: str, *, start_offset: int) -> FaultHit | None:
    """Return the first new critical fault after *start_offset*, if any."""
    log = _godot_log(ip)
    if log is None:
        return None
    new_off, text = _read_from(log, start_offset)
    if not text.strip():
        return None
    lines = text.replace("\r\n", "\n").splitlines()
    for i, line in enumerate(lines):
        ev = classify_line(line, source="godot1", path=str(log))
        critical = bool(ev and ev.critical)
        if not critical and _CRITICAL_RE.search(line):
            critical = True
        if not critical:
            continue
        # Prefer a window around the fault line.
        lo = max(0, i - 8)
        hi = min(len(lines), i + 20)
        excerpt = "\n".join(lines[lo:hi])
        ts_m = _TS_RE.match(line.strip())
        fault_ts = ts_m.group("ts") if ts_m else _utc()
        summary = (ev.summary if ev else line.strip())[:220]
        return FaultHit(
            source="godot1",
            summary=summary,
            excerpt=excerpt,
            fault_ts=fault_ts,
            raw_line=line.strip(),
            offset_after=new_off,
        )
    return None


def _slug(summary: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", summary)[:48].strip("-")
    return s or "fault"


def write_repro_pack(
    *,
    ip: str,
    hunt_root: Path,
    fault: FaultHit,
    trail: list[dict[str, Any]],
    cycle: int,
    cycle_dir: Path | None,
) -> Path:
    """
    Emit SuperDragica-grade pack:
      automation_runs/<stamp>_<ip>_repro_<caseId>/
        manifest.json, click_log.jsonl, player_state.jsonl,
        BUG-TEST-*.md, BUG-DEV-*.md, logs/
    """
    case_id = f"{_utc_stamp()}_{_slug(fault.summary)}"
    stamp = _utc_stamp()
    safe_ip = ip.replace(":", "_")
    pack = Path("automation_runs") / f"{stamp}_{safe_ip}_repro_{case_id}"
    pack.mkdir(parents=True, exist_ok=True)
    logs_dir = pack / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Click trail: prefer last N ok/planned from rolling buffer.
    trail_slice = trail[-120:]
    click_path = pack / "click_log.jsonl"
    with click_path.open("w", encoding="utf-8") as fh:
        for row in trail_slice:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    if cycle_dir is not None:
        src = cycle_dir / "click_log.jsonl"
        if src.is_file():
            shutil.copy2(src, pack / "cycle_click_log.jsonl")

    # Player state
    st = fetch_player_state(ip)
    with (pack / "player_state.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": _utc(), "state": st}, ensure_ascii=True, default=str) + "\n"
        )

    (logs_dir / "godot1_excerpt.txt").write_text(fault.excerpt + "\n", encoding="utf-8")
    # Wider window from current godot log
    glog = _godot_log(ip)
    if glog is not None:
        try:
            tail = glog.read_bytes()[-120_000:].decode("utf-8", errors="replace")
            (logs_dir / "godot1_window.txt").write_text(tail, encoding="utf-8")
        except OSError:
            pass
        (logs_dir / "godot1_path.txt").write_text(str(glog), encoding="utf-8")
    rlog = _ruleta_log(ip)
    if rlog is not None:
        try:
            rtail = rlog.read_bytes()[-80_000:].decode("utf-8", errors="replace")
            (logs_dir / "ruleta_window.txt").write_text(rtail, encoding="utf-8")
        except OSError:
            pass

    ok_clicks = [r for r in trail_slice if r.get("phase") == "ok"]
    steps_md = "\n".join(
        f"{n}. Click `{r.get('button_id')}` "
        f"(layout `{r.get('layout_id')}`"
        f"{', overlay ' + str(r.get('overlay')) if r.get('overlay') else ''}) "
        f"at ({r.get('x_pct')}%, {r.get('y_pct')}%)"
        for n, r in enumerate(ok_clicks[-25:], start=1)
    ) or "1. (no ok clicks in trail window)"

    layouts = sorted({str(r.get("layout_id") or "") for r in ok_clicks if r.get("layout_id")})
    layout_note = ", ".join(layouts) or "unknown"

    test_path = pack / f"BUG-TEST-bot-crash-{stamp}.md"
    test_path.write_text(
        f"# Bot-discovered crash - tester guide\n\n"
        f"Cabinet: {ip}  \n"
        f"When: {fault.fault_ts}  \n"
        f"Detected: {_utc()}  \n"
        f"Actor: **bot** (random catalog hunt)\n\n"
        f"## What happens\n\n"
        f"1. Automation clicks mapped roulette controls at random / systematic order.\n"
        f"2. Godot1 logs a **critical** fault.\n"
        f"3. Summary: `{fault.summary}`\n\n"
        f"## How to reproduce (from click trail)\n\n"
        f"{steps_md}\n\n"
        f"## Pass / fail\n\n"
        f"| Result | Meaning |\n|---|---|\n"
        f"| UI stays up; no NullReference / Unhandled in godot1 | PASS |\n"
        f"| Same fault reappears in godot1 after the steps | FAIL |\n\n"
        f"## Logs\n\n"
        f"- Pack: `{pack}`\n"
        f"- Godot excerpt: `logs/godot1_excerpt.txt`\n"
        f"- Click trail: `click_log.jsonl`\n\n"
        f"## Fault excerpt\n\n```\n{fault.excerpt}\n```\n",
        encoding="utf-8",
    )

    trail_table = "\n".join(
        f"| {r.get('ts_utc','')} | bot | {r.get('phase')} "
        f"`{r.get('button_id')}` @{r.get('layout_id')} | - |"
        for r in ok_clicks[-20:]
    )
    dev_path = pack / f"BUG-DEV-bot-fault-{stamp}.md"
    dev_path.write_text(
        f"# Bot-discovered godot1 fault - developer notes\n\n"
        f"| | |\n|---|---|\n"
        f"| Cabinet | {ip} |\n"
        f"| Crash time | {fault.fault_ts} |\n"
        f"| Detected | {_utc()} |\n"
        f"| Kind | `bot_hunt_critical` |\n"
        f"| Fault | `{fault.summary}` |\n"
        f"| Layout(s) | {layout_note} |\n"
        f"| Hunt cycle | {cycle} |\n"
        f"| Pack | `{pack}` |\n\n"
        f"Actor **bot** — clicks from InputAgent against Godot; "
        f"middleware `:8090` is oracle only.\n\n"
        f"## Flow (recent ok clicks)\n\n"
        f"| Time | Actor | Event | Middleware |\n|---|---|---|---|\n"
        f"{trail_table}\n"
        f"| {fault.fault_ts} | bug | {fault.summary} | (none — process may die first) |\n\n"
        f"## Stack / excerpt\n\n```\n{fault.excerpt}\n```\n\n"
        f"## Raw fault line\n\n```\n{fault.raw_line}\n```\n",
        encoding="utf-8",
    )

    seqs = [int(r.get("seq") or 0) for r in trail_slice]
    manifest = {
        "case_id": case_id,
        "cabinet_ip": ip,
        "fault_ts": fault.fault_ts,
        "summary": fault.summary,
        "source": fault.source,
        "layouts": layouts,
        "hunt_root": str(hunt_root),
        "cycle": cycle,
        "click_seq_min": min(seqs) if seqs else None,
        "click_seq_max": max(seqs) if seqs else None,
        "click_events": len(trail_slice),
        "ok_clicks_in_pack": len(ok_clicks),
        "bug_test": str(test_path),
        "bug_dev": str(dev_path),
        "created_utc": _utc(),
    }
    (pack / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Index under hunt root
    index = hunt_root / "packs.jsonl"
    with index.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _utc(), "pack": str(pack), **manifest}, ensure_ascii=True) + "\n")
    return pack


def run_bug_hunt(
    *,
    ip: str = "10.0.0.90",
    hours: float = 0.0,
    mode: str = "random",
    layouts: tuple[str, ...] = ("layout1", "layout2"),
    max_clicks_per_cycle: int = 200,
    batch_size: int | None = None,
    aft_cents: int = 2_000_000,
    no_aft: bool = False,
    stop_on_first: bool = False,
    cooldown_sec: float = 45.0,
    out_root: Path | str | None = None,
    progress: Any = print,
    bot_profile: str | None = "fast",
    bot_config_path: Path | str | None = None,
) -> Path:
    stamp = _utc_stamp()
    root = Path(out_root) if out_root else Path("automation_runs") / f"{stamp}_{ip.replace(':', '_')}_bug_hunt"
    root.mkdir(parents=True, exist_ok=True)
    session = root / "session.jsonl"
    aft_log = root / "aft.log"
    deadline = time.time() + hours * 3600.0 if hours > 0 else None

    glog = _godot_log(ip)
    godot_off = glog.stat().st_size if glog is not None else 0
    state = HuntState(
        ip=ip,
        hunt_root=root,
        godot_offset=godot_off,
        stop_on_first=stop_on_first,
    )
    meta = {
        "started_utc": _utc(),
        "ip": ip,
        "hours": hours,
        "mode": mode,
        "max_clicks_per_cycle": max_clicks_per_cycle,
        "bot_profile": bot_profile,
        "aft_cents": aft_cents,
        "no_aft": no_aft,
        "stop_on_first": stop_on_first,
        "out": str(root),
    }
    (root / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    progress(f"[{_utc()}] bug-hunt start -> {root}")

    packed_this_cycle: list[str] = []

    def on_batch(
        batch: list[ClickTargetSpec],
        events: list[ClickEvent],
        agent_ok: bool,
    ) -> bool:
        _ = agent_ok
        _append_trail(state, events)
        # Always advance offset even when no fault, but keep a peek window.
        hit = scan_godot_critical(ip, start_offset=state.godot_offset)
        if hit is None:
            g = _godot_log(ip)
            if g is not None:
                try:
                    state.godot_offset = g.stat().st_size
                except OSError:
                    pass
            return False
        fault_key = f"{hit.fault_ts}|{hit.summary[:80]}"
        now = time.time()
        if fault_key == state.last_pack_fault_key or now < state.cooldown_until:
            state.godot_offset = max(state.godot_offset, hit.offset_after)
            return False
        pack = write_repro_pack(
            ip=ip,
            hunt_root=root,
            fault=hit,
            trail=list(state.trail),
            cycle=cycle,
            cycle_dir=cycle_dir,
        )
        state.packs.append(str(pack))
        state.pack_count += 1
        state.last_pack_fault_key = fault_key
        state.cooldown_until = now + cooldown_sec
        state.godot_offset = max(state.godot_offset, hit.offset_after)
        packed_this_cycle.append(str(pack))
        progress(f"[{_utc()}] FAULT PACKED -> {pack}")
        progress(f"[{_utc()}] {hit.summary}")
        try:
            dismiss_stuck_overlays(ip=ip, layout_id=layouts[-1], progress=progress)
        except Exception as e:  # noqa: BLE001
            progress(f"[{_utc()}] dismiss warn: {e}")
        return bool(stop_on_first)

    cycle = 0
    cycle_dir: Path | None = None
    while True:
        if deadline is not None and time.time() >= deadline:
            progress(f"[{_utc()}] hours limit reached; stopping")
            break
        cycle += 1
        packed_this_cycle = []
        creds = _credits(ip)
        progress(f"[{_utc()}] cycle={cycle} credits={creds} packs={state.pack_count}")

        if creds is None or creds < MIN_CREDITS_TO_RUN:
            if no_aft:
                progress(f"[{_utc()}] low credits; sleep 120s (no-aft)")
                time.sleep(120)
                continue
            if not _aft_topup(ip, aft_cents, aft_log):
                progress(f"[{_utc()}] AFT failed; sleep 180s")
                time.sleep(180)
                continue

        try:
            cancel_all_bets(ip)
            set_chip(ip, 0)
        except Exception as e:  # noqa: BLE001
            progress(f"[{_utc()}] preflight warn: {e}")

        # Reset watch offset to "now" at cycle start so we only pack new faults.
        g = _godot_log(ip)
        if g is not None:
            try:
                state.godot_offset = g.stat().st_size
            except OSError:
                pass

        cycle_dir = root / f"cycle_{cycle:05d}"
        t0 = time.time()
        try:
            result = run_random_bot(
                ip=ip,
                out_dir=cycle_dir,
                mode=mode,  # type: ignore[arg-type]
                layouts=layouts,
                start_layout=layouts[0],
                client_ids=("player0",),
                max_clicks=max_clicks_per_cycle or None,
                batch_size=batch_size,
                include_unverified=True,
                wait_betting=True,
                resume=False,
                progress=lambda m: progress(f"[{_utc()}] {m}"),
                on_batch_done=on_batch,
                bot_profile=bot_profile,
                bot_config_path=bot_config_path,
            )
            # End-of-cycle sweep in case fault landed after last batch settle.
            late = scan_godot_critical(ip, start_offset=state.godot_offset)
            if late is not None:
                fault_key = f"{late.fault_ts}|{late.summary[:80]}"
                if fault_key != state.last_pack_fault_key and time.time() >= state.cooldown_until:
                    pack = write_repro_pack(
                        ip=ip,
                        hunt_root=root,
                        fault=late,
                        trail=list(state.trail),
                        cycle=cycle,
                        cycle_dir=cycle_dir,
                    )
                    state.packs.append(str(pack))
                    state.pack_count += 1
                    state.last_pack_fault_key = fault_key
                    state.cooldown_until = time.time() + cooldown_sec
                    state.godot_offset = max(state.godot_offset, late.offset_after)
                    packed_this_cycle.append(str(pack))
                    progress(f"[{_utc()}] FAULT PACKED (cycle-end) -> {pack}")
            ok = bool(result.ok)
            msg = result.message
            clicked = int(result.clicked)
        except Exception as e:  # noqa: BLE001
            ok = False
            msg = f"exception: {e}"
            clicked = 0
            progress(f"[{_utc()}] cycle exception: {e}")
            try:
                dismiss_stuck_overlays(ip=ip, layout_id=layouts[-1], progress=progress)
            except Exception:  # noqa: BLE001
                pass

        row = {
            "ts": _utc(),
            "cycle": cycle,
            "ok": ok,
            "clicked": clicked,
            "message": msg,
            "elapsed_sec": round(time.time() - t0, 1),
            "credits_after": _credits(ip),
            "packs": list(packed_this_cycle),
            "out": str(cycle_dir),
        }
        with session.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        progress(f"[{_utc()}] cycle done {json.dumps(row)}")

        if stop_on_first and state.pack_count > 0:
            progress(f"[{_utc()}] stop-on-first: packed {state.pack_count}")
            break

        time.sleep(3.0)

    (root / "stopped.json").write_text(
        json.dumps(
            {
                "stopped_utc": _utc(),
                "cycles": cycle,
                "packs": state.packs,
                "pack_count": state.pack_count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Roulette bug-hunt: clicks + auto repro packs")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--hours", type=float, default=0.0, help="0 = forever")
    p.add_argument("--mode", choices=("random", "systematic"), default="random")
    p.add_argument("--layouts", default="layout1,layout2")
    p.add_argument("--max-clicks-per-cycle", type=int, default=200)
    p.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="0 = use bot_config profile batch_size",
    )
    p.add_argument("--profile", default="fast", help="Bot timing profile name")
    p.add_argument("--config", default="", help="Path to bot_config.json")
    p.add_argument("--aft-cents", type=int, default=2_000_000)
    p.add_argument("--no-aft", action="store_true")
    p.add_argument("--stop-on-first", action="store_true")
    p.add_argument("--cooldown-sec", type=float, default=45.0)
    p.add_argument("--out-root", default="")
    args = p.parse_args(argv)
    layouts = tuple(x.strip() for x in args.layouts.split(",") if x.strip())
    out = Path(args.out_root) if args.out_root else None
    root = run_bug_hunt(
        ip=args.ip,
        hours=args.hours,
        mode=args.mode,
        layouts=layouts,
        max_clicks_per_cycle=args.max_clicks_per_cycle,
        batch_size=args.batch_size or None,
        aft_cents=args.aft_cents,
        no_aft=bool(args.no_aft),
        stop_on_first=bool(args.stop_on_first),
        cooldown_sec=args.cooldown_sec,
        out_root=out,
        progress=print,
        bot_profile=args.profile or None,
        bot_config_path=args.config or None,
    )
    print(json.dumps({"ok": True, "out": str(root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
