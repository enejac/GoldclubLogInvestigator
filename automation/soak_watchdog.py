"""Self-heal the two-week soak without relying on Cursor chat ticks.

Checks heartbeat / python process / free disk / live credits, then:

* prunes soak PNGs when disk is low
* tops up credits (slot BillInject/AFT or roulette AFT)
* restarts ``Start-RouletteTwoWeekSoak.ps1`` when the soak is dead or stuck

  python -m automation.soak_watchdog --once
  python -m automation.soak_watchdog --loop-hours 3 --until 2026-08-17T07:30:00
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PTR = ROOT / "_tmp_logs" / "two_week_soak_active.txt"
SI = timezone(timedelta(hours=2))
DEFAULT_UNTIL = "2026-08-17T07:30:00"
DEFAULT_IP = "10.0.0.90"

# Heartbeat older than this → stuck (cycle meter-wait / hung InputAgent).
STALE_HEARTBEAT_SEC = 2 * 3600
# Slot burn rate is high; keep a real float.
SLOT_CREDIT_FLOOR = 50_000
DISK_FREE_MIN_BYTES = 2 * 1024**3
DISK_PRUNE_TARGET_BYTES = 3 * 1024**3


@dataclass
class HealthReport:
    ok: bool
    active: str
    actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    heartbeat_age_sec: float | None = None
    credits: int | None = None
    free_bytes: int | None = None
    python_alive: bool = False
    cycle: int | None = None
    kind: str | None = None


def parse_until(text: str) -> datetime:
    raw = (text or "").strip()
    if raw.endswith("Z"):
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SI)
    return dt.astimezone(SI)


def read_active_path() -> Path | None:
    if not ACTIVE_PTR.is_file():
        return None
    line = ACTIVE_PTR.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not line:
        return None
    path = Path(line[0].strip())
    return path if path.is_dir() else None


def write_active_path(path: Path) -> None:
    ACTIVE_PTR.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PTR.write_text(str(path.resolve()) + "\n", encoding="utf-8")


def free_bytes_on_drive(path: Path) -> int | None:
    try:
        import shutil

        return int(shutil.disk_usage(path.drive or path.anchor or str(ROOT)).free)
    except Exception:
        return None


def python_soak_alive(out_root: Path) -> bool:
    """True if a python process looks attached to this soak out-root."""
    marker = str(out_root.resolve())
    try:
        # Windows: lean on Get-CimInstance so we don't need psutil.
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Select-Object -ExpandProperty CommandLine"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return "roulette_two_week_soak" in blob and (
            marker in blob or out_root.name in blob
        )
    except Exception:
        return False


def any_python_soak() -> bool:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |"
                " Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return "roulette_two_week_soak" in (proc.stdout or "")
    except Exception:
        return False


def load_heartbeat(out_root: Path) -> dict[str, Any] | None:
    path = out_root / "heartbeat.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def heartbeat_age_sec(hb: dict[str, Any]) -> float | None:
    raw = hb.get("heartbeat_si") or hb.get("heartbeat_utc")
    if not raw:
        return None
    try:
        text = str(raw)
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SI)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def prune_pngs(root: Path, *, stop_when_free: int) -> tuple[int, int]:
    """Delete PNGs under automation_runs / _tmp_logs until free space recovers."""
    n = 0
    freed = 0
    roots = [root, ROOT / "automation_runs", ROOT / "_tmp_logs"]
    seen: set[str] = set()
    for base in roots:
        if not base.is_dir():
            continue
        for png in sorted(base.rglob("*.png"), key=lambda p: p.stat().st_mtime if p.exists() else 0):
            key = str(png.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                sz = png.stat().st_size
                png.unlink()
                n += 1
                freed += sz
            except OSError:
                continue
            free = free_bytes_on_drive(ROOT)
            if free is not None and free >= stop_when_free:
                return n, freed
    return n, freed


def topup_credits(ip: str, *, min_credits: int, aft_log: Path) -> tuple[bool, str, int | None]:
    from automation.egm_credit_inject import ensure_credits, read_credits

    ok, msg, after = ensure_credits(
        ip,
        min_credits=min_credits,
        aft_cents=2_000_000,
        bill_credits=200_000,
        log=aft_log,
        try_dallas=False,
        settle_sec=5.0,
    )
    # Re-read after settle — DeviceManager replicas can lag right after AFT.
    time.sleep(3.0)
    again = read_credits(ip)
    credits = again.credits if again.credits is not None else after.credits
    if credits is not None and credits >= min_credits:
        return True, msg, credits
    return ok, msg, credits


def stop_soak(out_root: Path) -> None:
    try:
        (out_root / "STOP").write_text("stop\n", encoding="utf-8")
    except OSError:
        pass
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'roulette_two_week_soak' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        pass
    time.sleep(2.0)


def start_supervisor(ip: str, until: str, out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    # Clear STOP so a recycled folder can run (we usually use a fresh stamp).
    stop = out_root / "STOP"
    if stop.is_file():
        try:
            stop.unlink()
        except OSError:
            pass
    script = ROOT / "Start-RouletteTwoWeekSoak.ps1"
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Ip",
            ip,
            "-Until",
            until,
            "-OutRoot",
            str(out_root),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_active_path(out_root)
    time.sleep(5.0)


def run_once(
    *,
    ip: str = DEFAULT_IP,
    until: str = DEFAULT_UNTIL,
    stale_sec: float = STALE_HEARTBEAT_SEC,
    credit_floor: int = SLOT_CREDIT_FLOOR,
    heal: bool = True,
) -> HealthReport:
    actions: list[str] = []
    notes: list[str] = []
    active = read_active_path()
    if active is None:
        # Prefer newest soak folder.
        runs = sorted(
            (ROOT / "automation_runs").glob(f"*_{ip}_two_week_soak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        active = runs[0] if runs else None
        if active is not None:
            write_active_path(active)
            notes.append(f"repointed active -> {active.name}")

    free = free_bytes_on_drive(ROOT)
    if free is not None and free < DISK_FREE_MIN_BYTES:
        notes.append(f"disk low free={free // (1024**2)}MB")
        if heal:
            n, freed = prune_pngs(active or ROOT / "automation_runs", stop_when_free=DISK_PRUNE_TARGET_BYTES)
            actions.append(f"pruned_pngs n={n} freed_mb={freed // (1024**2)}")
            free = free_bytes_on_drive(ROOT)

    credits: int | None = None
    kind_live = "unknown"
    try:
        from automation.egm_credit_inject import detect_live_egm_kind, read_credits

        kind_live = detect_live_egm_kind(ip)
        credits = read_credits(ip, kind=kind_live if kind_live != "unknown" else None).credits
    except Exception as exc:  # noqa: BLE001
        notes.append(f"credit_read_fail: {exc}")

    if credits is not None and credits < credit_floor:
        notes.append(f"credits low {credits} < {credit_floor}")
        if heal:
            aft_log = (active or ROOT / "_tmp_logs") / "watchdog_aft.log"
            ok, msg, after = topup_credits(ip, min_credits=credit_floor, aft_log=aft_log)
            actions.append(f"credit_topup ok={ok} credits={after} {msg}")
            credits = after

    if active is None:
        report = HealthReport(
            ok=False,
            active="",
            actions=actions,
            notes=notes + ["no active soak folder"],
            credits=credits,
            free_bytes=free,
            python_alive=any_python_soak(),
            kind=kind_live,
        )
        if heal:
            stamp = datetime.now(SI).strftime("%Y%m%d_%H%M%S")
            out = ROOT / "automation_runs" / f"{stamp}_{ip}_two_week_soak"
            start_supervisor(ip, until, out)
            actions.append(f"started_supervisor {out.name}")
            report.actions = actions
            report.active = str(out)
            report.ok = True
            report.python_alive = True
            report.notes.append("fresh supervisor launched")
        return report

    hb = load_heartbeat(active)
    age = heartbeat_age_sec(hb) if hb else None
    py_alive = python_soak_alive(active) or any_python_soak()
    stop_present = (active / "STOP").is_file()
    deadline = parse_until(until)
    past_deadline = datetime.now(SI) >= deadline

    stuck = (not py_alive) or (age is not None and age > stale_sec) or hb is None
    if stop_present and not past_deadline:
        notes.append("STOP flag present")
        stuck = True

    if past_deadline:
        notes.append("deadline reached")
        return HealthReport(
            ok=True,
            active=str(active),
            actions=actions,
            notes=notes,
            heartbeat_age_sec=age,
            credits=credits,
            free_bytes=free,
            python_alive=py_alive,
            cycle=(hb or {}).get("cycle"),
            kind=(hb or {}).get("kind") or kind_live,
        )

    # Always scrub remote InputAgent bins so Windows\\Temp cannot refill.
    if heal:
        try:
            from automation.remote_input_agent import (
                input_agent_source_sha12,
                prune_stale_input_agent_bins,
            )

            keep = input_agent_source_sha12()
            pruned = prune_stale_input_agent_bins(ip=ip, keep_sha=keep)
            if pruned.get("removed"):
                actions.append(
                    f"pruned_remote_inputagent removed={pruned['removed']} "
                    f"kept={pruned['kept']}"
                )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"remote_inputagent_prune_warn: {exc}")

    if stuck and heal:
        notes.append(
            f"stuck py_alive={py_alive} age_sec={None if age is None else int(age)}"
        )
        stop_soak(active)
        # Resume the same pack — do not stamp a new folder mid two-week run.
        start_supervisor(ip, until, active)
        actions.append(f"restarted_supervisor {active.name}")
        py_alive = True
        age = 0.0

    ok = py_alive and (age is None or age <= stale_sec) and (
        free is None or free >= DISK_FREE_MIN_BYTES // 2
    )
    return HealthReport(
        ok=ok,
        active=str(active),
        actions=actions,
        notes=notes,
        heartbeat_age_sec=age,
        credits=credits,
        free_bytes=free,
        python_alive=py_alive,
        cycle=(load_heartbeat(active) or {}).get("cycle"),
        kind=(load_heartbeat(active) or {}).get("kind") or kind_live,
    )


def _print_report(report: HealthReport) -> None:
    payload = asdict(report)
    if report.free_bytes is not None:
        payload["free_gb"] = round(report.free_bytes / (1024**3), 2)
    print(json.dumps(payload, indent=2, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ip", default=DEFAULT_IP)
    p.add_argument("--until", default=DEFAULT_UNTIL)
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop-hours", type=float, default=3.0)
    p.add_argument("--stale-hours", type=float, default=2.0)
    p.add_argument("--credit-floor", type=int, default=SLOT_CREDIT_FLOOR)
    p.add_argument("--no-heal", action="store_true")
    args = p.parse_args(argv)

    stale_sec = float(args.stale_hours) * 3600.0
    deadline = parse_until(args.until)

    def tick() -> int:
        report = run_once(
            ip=args.ip,
            until=args.until,
            stale_sec=stale_sec,
            credit_floor=int(args.credit_floor),
            heal=not args.no_heal,
        )
        _print_report(report)
        log_path = ROOT / "_tmp_logs" / "soak_watchdog.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {"ts": datetime.now(SI).isoformat(timespec="seconds"), **asdict(report)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return 0 if report.ok else 1

    if args.once:
        return tick()

    while datetime.now(SI) < deadline:
        code = tick()
        if datetime.now(SI) >= deadline:
            break
        # Sleep in chunks so STOP/deadline is noticed sooner.
        sleep_s = max(60.0, float(args.loop_hours) * 3600.0)
        end = time.time() + sleep_s
        while time.time() < end:
            if datetime.now(SI) >= deadline:
                break
            if ACTIVE_PTR.is_file():
                active = read_active_path()
                if active and (active / "WATCHDOG_STOP").is_file():
                    print("WATCHDOG_STOP present; exiting", flush=True)
                    return code
            time.sleep(60.0)
    print("watchdog deadline reached", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
