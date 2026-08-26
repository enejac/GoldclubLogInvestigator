"""
Run a local Jira/Xray roulette recipe (clicks + :8090 oracle + optional bill inject).

CLI::

    python -m automation.run_jira_repro --key RSW-6534 --ip 10.0.0.90
    python -m automation.run_jira_repro --key RSW-6534 --target local
    python -m automation.run_jira_repro --key RSW-6534 --no-bill-inject

A recipe runs the same either way: ``--target local`` clicks the roulette client on
this machine and reads its middleware over ``127.0.0.1``, while a cabinet IP goes
through WinRM and the admin share. The connection is checked before the first step,
so an unreachable cabinet fails with the command that fixes it instead of a pack
full of failed clicks.

Bill inject calls the repo lab script (``Invoke-BillInjectRoulette.ps1``). It is
**not** bundled in LogInvestigator.exe; GUI runs default to ``allow_bill_inject=False``.
It only ever runs against a cabinet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_paths import app_runs_dir
from automation.click_logger import ClickLogger
from automation.roulette_layout import ROULETTE_FOCUS_PROCESS
from automation.roulette_layout_store import resolve_hitbox_center, set_active_layout
from automation.roulette_runner import _latest_file, wait_for_betting_open
from automation.roulette_target import (
    Session,
    Target,
    check_target,
    open_session,
    parse_target,
)
from automation.xray_recipe import load_recipe, normalize_issue_key, recipe_path_for

ProgressFn = Callable[[str], None]

ROOT = Path(__file__).resolve().parents[1]
BILL_SCRIPT = ROOT / "Invoke-BillInjectRoulette.ps1"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _progress(cb: ProgressFn | None, msg: str) -> None:
    if cb is not None:
        cb(msg)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _center(button_id: str, layout_id: str) -> tuple[float, float]:
    c = resolve_hitbox_center(button_id, layout_id)
    if c is None:
        raise RuntimeError(f"no hitbox for {button_id!r} on {layout_id}")
    return float(c.x_pct), float(c.y_pct)


def _click(x: float, y: float, *, ms: int = 110) -> dict[str, Any]:
    return {
        "type": "click_window",
        "value": f"{ROULETTE_FOCUS_PROCESS}@{x:.3f},{y:.3f}",
        "ms": ms,
    }


def _credits(session: Session) -> int | None:
    st = session.player_state()
    if not st.get("ok"):
        return None
    try:
        return int(st.get("credits"))
    except (TypeError, ValueError):
        return None


def _bets(session: Session) -> list[Any]:
    st = session.player_state()
    if not st.get("ok"):
        return []
    bets = st.get("bets")
    return list(bets) if isinstance(bets, list) else []


@dataclass
class StepResult:
    index: int
    op: str
    ok: bool
    detail: str
    label: str = ""
    verdict: str = ""  # pass | fail | skip | bug_reproduced | info


@dataclass
class JiraReproResult:
    ok: bool
    message: str
    key: str = ""
    out_dir: str = ""
    steps: list[StepResult] = field(default_factory=list)
    jira_meta: dict[str, Any] = field(default_factory=dict)


def run_bill_inject(
    ip: str,
    *,
    credits: int,
    session: Session | None = None,
    profile: str = "captured",
    clear_lock_first: bool = True,
    log: Path | None = None,
    progress: ProgressFn | None = None,
) -> tuple[bool, str]:
    """Push credits into a cabinet's bill acceptor with the lab script.

    Cabinet-only: the script talks to KeyCtrl on the cabinet's own COM3 bridge, so
    there is nothing for it to do against a local target.
    """
    if not ip:
        return False, "bill inject needs a cabinet IP (not available for a local run)"
    if _is_frozen():
        return False, "bill inject unavailable inside LogInvestigator.exe (lab script only)"
    if not BILL_SCRIPT.is_file():
        return False, f"missing bill inject script: {BILL_SCRIPT}"
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BILL_SCRIPT),
        "-ComputerName",
        ip,
        "-PayloadProfile",
        profile,
        "-Credits",
        str(int(credits)),
        "-Send",
    ]
    if clear_lock_first:
        cmd.append("-ClearLockFirst")
    _progress(progress, f"Bill inject {credits} credits ({profile}) on {ip} ...")
    try:
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== bill_inject {_utc()} credits={credits} ===\n")
                fh.flush()
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                )
        else:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
    except subprocess.TimeoutExpired:
        return False, "bill inject timed out"
    except OSError as e:
        return False, str(e)
    time.sleep(2.0)
    c = _credits(session) if session is not None else None
    ok = proc.returncode == 0 and c is not None and c >= 1
    return ok, f"exit={proc.returncode} credits_now={c}"


def _run_clicks(
    *,
    session: Session,
    layout_id: str,
    button_ids: list[str],
    allow_forbidden: bool = False,
) -> tuple[bool, str]:
    steps: list[dict[str, Any]] = [
        {"type": "focus_process", "value": ROULETTE_FOCUS_PROCESS, "ms": 200},
    ]
    for bid in button_ids:
        if bid.upper() in ("COBRAR", "LLAMAR", "LOCK", "MENU_PIN_LOCK") and not allow_forbidden:
            return False, f"refusing forbidden control {bid!r} (set allow_forbidden)"
        x, y = _center(bid, layout_id)
        steps.append(_click(x, y, ms=120))
        steps.append({"type": "sleep", "ms": 180})
    return session.run(steps, timeout=90)


def _write_pack(
    *,
    session: Session,
    recipe: dict[str, Any],
    out_dir: Path,
    step_results: list[StepResult],
    trail: list[dict[str, Any]],
    overall_ok: bool,
    jira_meta: dict[str, Any],
) -> Path:
    key = str(recipe.get("key") or "UNKNOWN")
    bug = str(recipe.get("jira_bug") or "")
    summary = str(recipe.get("summary") or key)
    stamp = _utc_stamp()
    where = session.target.label
    pack = out_dir
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "logs").mkdir(exist_ok=True)

    with (pack / "click_log.jsonl").open("w", encoding="utf-8") as fh:
        for row in trail:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    st = session.player_state()
    with (pack / "player_state.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"ts": _utc(), "state": st}, ensure_ascii=True, default=str) + "\n"
        )

    steps_dump = [
        {
            "index": s.index,
            "op": s.op,
            "ok": s.ok,
            "detail": s.detail,
            "label": s.label,
            "verdict": s.verdict,
        }
        for s in step_results
    ]
    manifest = {
        "ts": _utc(),
        "stamp": stamp,
        "target": where,
        "target_kind": session.target.kind,
        "ip": session.target.ip,
        "surface": session.surface,
        "jira_test": key,
        "jira_bug": bug,
        "summary": summary,
        "layout": recipe.get("layout"),
        "overall_ok": overall_ok,
        "regression": bool(recipe.get("regression")),
        "steps": steps_dump,
        "jira_meta": jira_meta,
        "recipe_path": str(recipe_path_for(key) or ""),
        "browse": recipe.get("jira_browse")
        or f"https://winsytemsintl.atlassian.net/browse/{key}",
    }
    (pack / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )

    verdict_lines = "\n".join(
        f"| {s.index} | `{s.op}` | {s.verdict or ('ok' if s.ok else 'fail')} | {s.detail} |"
        for s in step_results
    )
    test_md = (
        f"# BUG-TEST-{key}\n\n"
        f"- **Ran on:** `{where}`\n"
        f"- **When:** {_utc()}\n"
        f"- **Xray Test:** [{key}]({manifest['browse']})\n"
        f"- **Linked bug:** {bug or 'n/a'}\n"
        f"- **Summary:** {summary}\n"
        f"- **Result:** {'PASS' if overall_ok else 'FAIL'}\n\n"
        f"## Preconditions\n\n"
        + "\n".join(f"- {p}" for p in (recipe.get("preconditions") or []))
        + "\n\n## Steps / verdicts\n\n"
        f"| # | Op | Verdict | Detail |\n|---|----|---------|--------|\n{verdict_lines}\n\n"
        f"## Notes\n\n{recipe.get('notes') or ''}\n\n"
        f"## Artifacts\n\n- `{pack}`\n- `manifest.json`, `click_log.jsonl`, `player_state.jsonl`\n"
    )
    (pack / f"BUG-TEST-{key}.md").write_text(test_md, encoding="utf-8")

    dev_md = (
        f"# BUG-DEV-{key}\n\n"
        f"- **jira_test:** {key}\n"
        f"- **jira_bug:** {bug}\n"
        f"- **layout:** {recipe.get('layout')}\n"
        f"- **:8090:** oracle for credits / PlayerDataBets\n"
        f"- **Actors:** `bot` (InputAgent), `ruleta`, `billinject` (lab script, optional)\n\n"
        f"## Flow\n\n"
        f"1. Optional bill inject (KeyCtrl :30300) for credits.\n"
        f"2. Wait betting open (ruleta log marker).\n"
        f"3. Click mapped spots / chips; assert via GET /api/data.\n"
        f"4. Cashout click (COBRAR) when recipe requests it.\n\n"
        f"## Step dump\n\n```json\n"
        f"{json.dumps(steps_dump, indent=2)}\n```\n\n"
        f"## Jira meta\n\n```json\n{json.dumps(jira_meta, indent=2, default=str)}\n```\n"
    )
    (pack / f"BUG-DEV-{key}.md").write_text(dev_md, encoding="utf-8")
    return pack


def run_jira_repro(
    *,
    key: str,
    ip: str = "10.0.0.90",
    target: Target | str | None = None,
    out_dir: Path | None = None,
    allow_bill_inject: bool = True,
    fetch_jira: bool = True,
    progress: ProgressFn | None = None,
) -> JiraReproResult:
    """
    Run one recipe against a cabinet or this machine and write the evidence pack.

    *target* wins over *ip* and may be a :class:`Target`, a cabinet IP, or
    ``"local"``; *ip* alone keeps every existing caller working.
    """
    issue_key = normalize_issue_key(key)
    recipe = load_recipe(issue_key)
    layout_id = str(recipe.get("layout") or "layout1")
    set_active_layout(layout_id)

    where = target if target is not None else ip
    run_target = where if isinstance(where, Target) else parse_target(str(where))

    stamp = _utc_stamp()
    if out_dir is None:
        out_dir = app_runs_dir() / f"{stamp}_{run_target.key}_repro_{issue_key}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jira_meta: dict[str, Any] = {}
    if fetch_jira:
        try:
            from network.jira_client import fetch_issue_metadata

            jira_meta = fetch_issue_metadata(issue_key)
            if jira_meta.get("ok"):
                _progress(
                    progress,
                    f"Jira: {jira_meta.get('summary') or issue_key} "
                    f"[{jira_meta.get('issuetype')}/{jira_meta.get('status')}]",
                )
            else:
                _progress(
                    progress,
                    f"Jira metadata skipped: {jira_meta.get('error') or 'unavailable'}",
                )
        except Exception as e:  # noqa: BLE001
            jira_meta = {"ok": False, "error": str(e)}
            _progress(progress, f"Jira metadata skipped: {e}")

    # Optional Xray steps overlay (does not replace local recipe).
    try:
        from network.xray_client import fetch_test_steps

        xray = fetch_test_steps(issue_key)
        if xray.get("ok") and xray.get("steps"):
            jira_meta["xray_steps"] = xray["steps"]
            _progress(progress, f"Xray Cloud returned {len(xray['steps'])} step(s) (info only)")
        elif xray.get("error"):
            jira_meta["xray"] = {"ok": False, "error": xray.get("error")}
    except Exception as e:  # noqa: BLE001
        jira_meta["xray"] = {"ok": False, "error": str(e)}

    reach = check_target(run_target)
    (out_dir / "connection.json").write_text(
        json.dumps(reach.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    _progress(progress, reach.summary())
    if not reach.ok:
        blocker = reach.blockers[0]
        return JiraReproResult(
            ok=False,
            message=f"{run_target.label} unreachable — {blocker.name}: {blocker.detail}. {blocker.hint}",
            key=issue_key,
            out_dir=str(out_dir),
            jira_meta=jira_meta,
        )

    _progress(progress, f"Opening a session on {run_target.label} ...")
    try:
        session = open_session(run_target)
    except Exception as e:  # noqa: BLE001
        return JiraReproResult(
            ok=False,
            message=f"InputAgent setup failed: {e}",
            key=issue_key,
            out_dir=str(out_dir),
            jira_meta=jira_meta,
        )

    click_log = ClickLogger(
        out_dir,
        cabinet_ip=run_target.ip or run_target.label,
        client_ids=("jira_repro",),
        session_meta={"jira_test": issue_key},
    )
    trail: list[dict[str, Any]] = []
    step_results: list[StepResult] = []
    overall_ok = True
    bill_log = out_dir / "logs" / "bill_inject.log"

    for idx, raw in enumerate(recipe.get("steps") or []):
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "").strip().lower()
        label = str(raw.get("label") or "")
        detail = ""
        ok = True
        verdict = "pass"

        try:
            if op == "bill_inject":
                if not allow_bill_inject:
                    ok, detail = True, "skipped (allow_bill_inject=False)"
                    verdict = "skip"
                else:
                    ok, detail = run_bill_inject(
                        run_target.ip,
                        credits=int(raw.get("credits") or 200000),
                        session=session,
                        profile=str(raw.get("profile") or "captured"),
                        clear_lock_first=bool(raw.get("clear_lock_first", True)),
                        log=bill_log,
                        progress=progress,
                    )
                    if not ok:
                        overall_ok = False
                        verdict = "fail"

            elif op == "assert_credits":
                c = _credits(session)
                mn = raw.get("min")
                mx = raw.get("max")
                ok = c is not None
                if ok and mn is not None:
                    ok = c >= int(mn)
                if ok and mx is not None:
                    ok = c <= int(mx)
                detail = f"credits={c}"
                if not ok:
                    overall_ok = False
                    verdict = "fail"

            elif op == "cancel_bets":
                r = session.put("CancelAllBets")
                ok = bool(r.get("ok", True))
                detail = str(r.get("error") or r.get("raw") or "CancelAllBets")
                if not ok:
                    # Soft: empty table is fine
                    ok = True
                    detail = f"cancel soft-ok: {detail}"
                verdict = "pass"

            elif op == "wait_open":
                timeout = float(raw.get("timeout_sec") or 90)
                log_file = _latest_file(session.log_dir("ruleta Roulette")) or _latest_file(
                    session.log_dir("ruleta")
                )
                if log_file is None:
                    ok = False
                    detail = f"no ruleta log under {session.log_dir('ruleta Roulette')}"
                    overall_ok = False
                    verdict = "fail"
                else:
                    _progress(progress, f"Waiting for betting open (≤{timeout:.0f}s) ...")
                    opened, open_detail = wait_for_betting_open(
                        log_file,
                        timeout_sec=timeout,
                        progress=progress,
                    )
                    ok = bool(opened)
                    detail = open_detail if ok else f"timeout: {open_detail}"
                    if not ok:
                        overall_ok = False
                        verdict = "fail"

            elif op == "click_ui":
                bid = str(raw.get("id") or "")
                allow_f = bool(raw.get("allow_forbidden", False))
                ok, detail = _run_clicks(
                    session=session,
                    layout_id=layout_id,
                    button_ids=[bid],
                    allow_forbidden=allow_f,
                )
                trail.append(
                    {
                        "ts": _utc(),
                        "op": op,
                        "id": bid,
                        "ok": ok,
                        "detail": detail,
                    }
                )
                click_log.log(
                    client_id="jira_repro",
                    layout_id=layout_id,
                    button_id=bid,
                    kind="ui",
                    x_pct=0.0,
                    y_pct=0.0,
                    phase="ok" if ok else "fail",
                    agent_ok=ok,
                    note=detail,
                )
                if not ok:
                    overall_ok = False
                    verdict = "fail"

            elif op == "click_bet":
                bid = str(raw.get("id") or "17")
                ok, detail = _run_clicks(
                    session=session,
                    layout_id=layout_id,
                    button_ids=[bid],
                    allow_forbidden=False,
                )
                trail.append(
                    {
                        "ts": _utc(),
                        "op": op,
                        "id": bid,
                        "ok": ok,
                        "detail": detail,
                    }
                )
                click_log.log(
                    client_id="jira_repro",
                    layout_id=layout_id,
                    button_id=bid,
                    kind="bet",
                    x_pct=0.0,
                    y_pct=0.0,
                    phase="ok" if ok else "fail",
                    agent_ok=ok,
                    note=detail,
                )
                time.sleep(0.6)
                if not ok:
                    overall_ok = False
                    verdict = "fail"

            elif op == "assert_bets":
                expect = str(raw.get("expect") or "nonempty").lower()
                bets = _bets(session)
                nonempty = len(bets) > 0
                if expect in ("nonempty", "any", "present"):
                    ok = nonempty
                elif expect in ("empty", "none"):
                    ok = not nonempty
                else:
                    ok = nonempty
                detail = f"bets={len(bets)} expect={expect}"
                on_fail = str(raw.get("on_fail") or "fail")
                if not ok:
                    if on_fail == "bug_reproduced":
                        verdict = "bug_reproduced"
                        overall_ok = False
                        detail += " (bug still present / regression fail)"
                    else:
                        verdict = "fail"
                        overall_ok = False
                else:
                    verdict = "pass"

            elif op == "snapshot_state":
                st = session.player_state()
                snap = out_dir / "logs" / f"state_{label or idx}.json"
                snap.write_text(
                    json.dumps(st, indent=2, ensure_ascii=True, default=str) + "\n",
                    encoding="utf-8",
                )
                ok = bool(st.get("ok"))
                detail = f"wrote {snap.name} credits={st.get('credits')}"
                verdict = "pass" if ok else "fail"
                if not ok:
                    overall_ok = False

            else:
                ok = False
                detail = f"unknown op {op!r}"
                verdict = "fail"
                overall_ok = False

        except Exception as e:  # noqa: BLE001
            ok = False
            detail = f"{type(e).__name__}: {e}"
            verdict = "fail"
            overall_ok = False

        sr = StepResult(
            index=idx,
            op=op,
            ok=ok,
            detail=detail,
            label=label,
            verdict=verdict,
        )
        step_results.append(sr)
        _progress(progress, f"[{idx}] {op} → {verdict}: {detail}")

    pack = _write_pack(
        session=session,
        recipe=recipe,
        out_dir=out_dir,
        step_results=step_results,
        trail=trail,
        overall_ok=overall_ok,
        jira_meta=jira_meta,
    )
    try:
        click_log.close()
    except OSError:
        pass
    msg = (
        f"{'PASS' if overall_ok else 'FAIL'} {issue_key} — pack {pack}"
    )
    return JiraReproResult(
        ok=overall_ok,
        message=msg,
        key=issue_key,
        out_dir=str(pack),
        steps=step_results,
        jira_meta=jira_meta,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run local Jira/Xray roulette recipe")
    p.add_argument("--key", required=True, help="Xray Test key or browse URL (e.g. RSW-6534)")
    p.add_argument("--ip", default="10.0.0.90", help="Cabinet IP (ignored when --target is set)")
    p.add_argument("--target", default="", help="'local' for this machine, or a cabinet IP")
    p.add_argument("--out-dir", default="", help="Override automation_runs pack folder")
    p.add_argument(
        "--no-bill-inject",
        action="store_true",
        help="Skip bill_inject steps (GUI default)",
    )
    p.add_argument(
        "--no-jira",
        action="store_true",
        help="Do not call Jira/Xray APIs for metadata",
    )
    args = p.parse_args(argv)
    out = Path(args.out_dir) if args.out_dir else None
    result = run_jira_repro(
        key=args.key,
        ip=args.ip,
        target=args.target or None,
        out_dir=out,
        allow_bill_inject=not args.no_bill_inject,
        fetch_jira=not args.no_jira,
        progress=lambda m: print(m, flush=True),
    )
    print(result.message, flush=True)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
