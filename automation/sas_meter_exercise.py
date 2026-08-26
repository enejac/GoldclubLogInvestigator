"""Exercise every driveable Accounting-tab meter on a lab cabinet.

Drives bill-in, AFT cashable/restricted/non-restricted, a few spins, then Collect.
Compares Accounting meters before/after each step and writes a gap report for
meters we cannot forge on this OneHand slot-roulette setup.

  python -m automation.sas_meter_exercise --ip 10.0.0.90
  python -m automation.sas_meter_exercise --ip 10.0.0.90 --skip-collect --skip-play
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from automation.egm_credit_inject import (
    detect_live_egm_kind,
    inject_aft,
    inject_bill,
)
from automation.sas_accounting_page_check import check_accounting_page, write_report
from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

ROOT = Path(__file__).resolve().parents[1]

# What we expect each step to bump (primary codes). Aggregates may also move.
STEP_EXPECT: dict[str, tuple[str, ...]] = {
    "bill_200k": ("000B",),
    "bill_500k": ("000B",),
    "aft_cashable": ("00A0", "0017"),
    "aft_restricted": ("00A2", "0017"),
    "aft_non_restricted": ("00A4", "0017"),
    "play_spins": ("0005", "0000", "0001", "0006", "0007", "001C"),
    "collect": ("0016", "0003", "0018", "0004", "0023", "0086", "0088", "00B8", "00BA", "00BC"),
}

# Honest gaps ? no proven inject on this OneHand Slot Roulette lab path.
CANNOT_FORGE: dict[str, str] = {
    "0015": "Ticket/voucher IN ? FutureLogic :30400 barcode; no TicketInject tool yet",
    "0080": "Reg cashable ticket in ? same TITO in path",
    "0082": "Restricted ticket in ? same TITO in path",
    "0084": "Non-restricted ticket in ? same TITO in path",
    "0016": "Ticket OUT ? needs Collect with CashoutButtonMode=Ticket + printer; attempted via Collect",
    "0086": "Reg cashable ticket out ? Collect/TITO print",
    "0088": "Restricted ticket out ? Collect/TITO print",
    "006E": "Bills dispensed ? no note dispenser/hopper on this EGM path",
    "0002": "Jackpot ? needs real jackpot hit; no forge",
    "001D": "Machine-paid progressive ? needs progressive win; no forge",
    "001F": "Attendant-paid paytable ? needs attendant payout path; no forge",
    "0020": "Attendant-paid progressive ? needs attendant + progressive; no forge",
    "0003": "Handpay cancelled ? Collect only if payout mode is Handpay",
    "0023": "Total hand paid ? same as handpay payout path",
    "0018": "Transfer to host (WAT out) ? AFT inject is host->EGM only; Collect->Cashless if enabled",
    "00B8": "Reg cashable C-Less out ? WAT cashout to host",
    "00BA": "Restricted C-Less out ? WAT cashout to host",
    "00BC": "Non-restricted C-Less out ? WAT cashout to host",
    "0004": "Cancelled credits (derived = 0003+0016+0018) ? moves only if those move",
}


@dataclass
class StepResult:
    name: str
    ok: bool
    message: str
    elapsed_sec: float
    moved: list[dict[str, Any]] = field(default_factory=list)
    expected_hit: list[str] = field(default_factory=list)
    expected_miss: list[str] = field(default_factory=list)


def _snapshot(ip: str) -> dict[str, str | None]:
    report = check_accounting_page(ip, treat_syncing_as_ok=True)
    return {row.code: row.machine for row in report.rows}


def _deltas(before: dict[str, str | None], after: dict[str, str | None]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for code in DEFAULT_6F_VERIFY_POLL_CODES:
        b, a = before.get(code), after.get(code)
        if b is None or a is None:
            continue
        try:
            d = int(a) - int(b)
        except ValueError:
            continue
        if d != 0:
            out.append({"code": code, "before": b, "after": a, "delta": d})
    return out


def _run_ps(script: Path, args: list[str], log: Path, timeout: float) -> tuple[bool, str]:
    if not script.is_file():
        return False, f"missing {script}"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script), *args,
    ]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== {script.name} {datetime.now(timezone.utc).isoformat()} ===\n")
        fh.write(" ".join(cmd) + "\n")
        fh.flush()
        try:
            proc = subprocess.run(
                cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    return proc.returncode == 0, f"{script.name} exit={proc.returncode}"


def _step(
    name: str,
    ip: str,
    before: dict[str, str | None],
    action: Callable[[], tuple[bool, str]],
    settle_sec: float,
    log_print: Callable[[str], None],
) -> tuple[StepResult, dict[str, str | None]]:
    log_print(f"STEP {name} ...")
    t0 = time.time()
    ok, msg = action()
    if settle_sec > 0:
        time.sleep(settle_sec)
    after = _snapshot(ip)
    moved = _deltas(before, after)
    expect = STEP_EXPECT.get(name, ())
    hit = [c for c in expect if any(m["code"] == c and m["delta"] > 0 for m in moved)]
    miss = [c for c in expect if c not in hit]
    res = StepResult(
        name=name, ok=ok, message=msg, elapsed_sec=round(time.time() - t0, 1),
        moved=moved, expected_hit=hit, expected_miss=miss,
    )
    log_print(
        f"  ok={ok} msg={msg} moved={len(moved)} "
        f"expect_hit={hit or '-'} expect_miss={miss or '-'}"
    )
    for m in moved:
        log_print(f"    {m['code']} {m['before']} -> {m['after']} ({m['delta']:+d})")
    return res, after


def run_exercise(
    ip: str,
    *,
    out_dir: Path,
    aft_cents: int = 10_000,
    bill_denoms: tuple[int, ...] = (200_000, 500_000),
    play_spins: int = 3,
    skip_bill: bool = False,
    skip_aft: bool = False,
    skip_play: bool = False,
    skip_collect: bool = False,
    settle_sec: float = 25.0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "exercise.log"

    def log_print(msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    kind = detect_live_egm_kind(ip)
    if kind == "unknown":
        kind = "slot"
    log_print(f"meter exercise start ip={ip} kind={kind} out={out_dir}")

    baseline_report = check_accounting_page(ip, treat_syncing_as_ok=True)
    write_report(baseline_report, out_dir / "baseline")
    before = {row.code: row.machine for row in baseline_report.rows}
    (out_dir / "baseline_values.json").write_text(
        json.dumps(before, indent=2), encoding="utf-8"
    )

    steps: list[StepResult] = []
    cur = before
    inject_log = out_dir / "inject.log"

    if not skip_bill:
        for credits in bill_denoms:
            name = f"bill_{credits // 1000}k" if credits % 1000 == 0 else f"bill_{credits}"
            # normalize to known keys
            step_name = "bill_200k" if credits == 200_000 else (
                "bill_500k" if credits == 500_000 else name
            )

            def _bill(c: int = credits) -> tuple[bool, str]:
                return inject_bill(ip, kind=kind, credits=c, log=inject_log)

            res, cur = _step(step_name, ip, cur, _bill, settle_sec, log_print)
            steps.append(res)

    if not skip_aft:
        for ttype, step_name in (
            ("cashable", "aft_cashable"),
            ("restricted", "aft_restricted"),
            ("non-restricted", "aft_non_restricted"),
        ):
            def _aft(tt: str = ttype) -> tuple[bool, str]:
                return inject_aft(
                    ip, kind=kind, amount_cents=aft_cents,
                    transfer_type=tt,  # type: ignore[arg-type]
                    log=inject_log,
                )

            res, cur = _step(step_name, ip, cur, _aft, settle_sec, log_print)
            steps.append(res)

    if not skip_play and play_spins > 0:
        autoplay = ROOT / "lab" / "Invoke-AutoPlayRemote.ps1"

        def _play() -> tuple[bool, str]:
            return _run_ps(
                autoplay,
                ["-ComputerName", ip, "-Action", "spin", "-Count", str(play_spins)],
                inject_log, 300.0,
            )

        res, cur = _step("play_spins", ip, cur, _play, max(settle_sec, 40.0), log_print)
        steps.append(res)

    if not skip_collect:
        autoplay = ROOT / "lab" / "Invoke-AutoPlayRemote.ps1"
        buttons = ROOT / "lab" / "Invoke-ButtonSequenceRemote.ps1"

        def _collect() -> tuple[bool, str]:
            ok1, m1 = _run_ps(
                autoplay,
                ["-ComputerName", ip, "-Action", "collect", "-Count", "1"],
                inject_log, 180.0,
            )
            ok2, m2 = (False, "skip buttons")
            if buttons.is_file():
                ok2, m2 = _run_ps(
                    buttons,
                    ["-ComputerName", ip, "-Steps", "Collect@2500"],
                    inject_log, 180.0,
                )
            return ok1 or ok2, f"autoplay={m1}; buttons={m2}"

        res, cur = _step("collect", ip, cur, _collect, settle_sec, log_print)
        steps.append(res)

    final_report = check_accounting_page(ip, treat_syncing_as_ok=True)
    write_report(final_report, out_dir / "final")
    final_vals = {row.code: row.machine for row in final_report.rows}
    total_moved = _deltas(before, final_vals)

    still_zero: list[dict[str, str]] = []
    for code in DEFAULT_6F_VERIFY_POLL_CODES:
        try:
            if int(final_vals.get(code) or 0) == 0:
                still_zero.append({
                    "code": code,
                    "reason": CANNOT_FORGE.get(
                        code,
                        "still zero after exercise (check step logs / cabinet config)",
                    ),
                })
        except ValueError:
            pass

    summary = {
        "ip": ip,
        "kind": kind,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aft_cents": aft_cents,
        "bill_denoms": list(bill_denoms),
        "play_spins": play_spins,
        "steps": [asdict(s) for s in steps],
        "total_moved": total_moved,
        "still_zero": still_zero,
        "cannot_forge_catalog": CANNOT_FORGE,
        "final_match": final_report.match,
        "final_ok": final_report.ok,
        "out_dir": str(out_dir),
    }
    (out_dir / "exercise_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    md = [
        f"# Accounting meter exercise ({ip})",
        "",
        f"- kind: `{kind}`",
        f"- final MATCH: {final_report.match}/30 ok={final_report.ok}",
        f"- meters that moved (baseline?final): **{len(total_moved)}**",
        f"- still zero: **{len(still_zero)}**",
        "",
        "## Steps",
    ]
    for s in steps:
        md.append(
            f"- `{s.name}` ok={s.ok} hit={s.expected_hit or '-'} "
            f"miss={s.expected_miss or '-'} ({s.message})"
        )
    md += ["", "## Moved (total)", "| Code | Before | After | Delta |",
           "|------|--------|-------|-------|"]
    for m in total_moved:
        md.append(f"| {m['code']} | {m['before']} | {m['after']} | {m['delta']:+d} |")
    md += ["", "## Still zero / cannot forge", "| Code | Reason |", "|------|--------|"]
    for z in still_zero:
        md.append(f"| {z['code']} | {z['reason']} |")
    (out_dir / "EXERCISE-REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    log_print(f"done moved={len(total_moved)} still_zero={len(still_zero)} -> {out_dir}")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--out-dir", default="")
    p.add_argument("--aft-cents", type=int, default=10_000)
    p.add_argument("--play-spins", type=int, default=3)
    p.add_argument("--settle-sec", type=float, default=25.0)
    p.add_argument("--skip-bill", action="store_true")
    p.add_argument("--skip-aft", action="store_true")
    p.add_argument("--skip-play", action="store_true")
    p.add_argument("--skip-collect", action="store_true")
    args = p.parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out_dir) if args.out_dir else (
        ROOT / "automation_runs" / f"{stamp}_{args.ip}_meter_exercise"
    )
    summary = run_exercise(
        args.ip,
        out_dir=out,
        aft_cents=int(args.aft_cents),
        play_spins=int(args.play_spins),
        skip_bill=bool(args.skip_bill),
        skip_aft=bool(args.skip_aft),
        skip_play=bool(args.skip_play),
        skip_collect=bool(args.skip_collect),
        settle_sec=float(args.settle_sec),
    )
    print(json.dumps({
        "out_dir": summary["out_dir"],
        "moved": len(summary["total_moved"]),
        "still_zero": len(summary["still_zero"]),
        "final_ok": summary["final_ok"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
