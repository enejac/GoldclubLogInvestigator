"""Read a meter timing trace and report how tightly each round painted.

    python -m automation.meter_trace_report                  # newest trace
    python -m automation.meter_trace_report path\\to\\trace.jsonl

The number that matters is **spread**: milliseconds between the first and the
last meter of one round changing on screen. A game moves several meters at
once, so they should all appear in a single paint — a spread of a few ms is one
paint, a spread of seconds means the Machine column ran ahead of SAS and the
operator watched the meters tick over one column at a time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def default_trace_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "meter_trace"


def newest_trace() -> Path | None:
    folder = default_trace_dir()
    if not folder.is_dir():
        return None
    traces = sorted(folder.glob("meter_trace_*.jsonl"), key=lambda p: p.stat().st_mtime)
    return traces[-1] if traces else None


def load(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def split_rounds(events: list[dict]) -> list[list[dict]]:
    """Group events into rounds; anything before the first open is its own group."""
    rounds: list[list[dict]] = []
    current: list[dict] = []
    for ev in events:
        if ev.get("kind") == "round_open":
            if current:
                rounds.append(current)
            current = [ev]
        else:
            current.append(ev)
    if current:
        rounds.append(current)
    return rounds


def report_round(idx: int, evs: list[dict]) -> None:
    t0 = evs[0].get("t_ms", 0.0)

    def rel(ev: dict) -> float:
        return float(ev.get("t_ms", 0.0)) - t0

    changes = [e for e in evs if e.get("kind") == "value_change"]
    flashes = [e for e in evs if e.get("kind") == "flash_start"]
    machine = [e for e in evs if e.get("kind") == "machine_landed"]
    sas = [e for e in evs if e.get("kind") == "sas_landed"]
    paints = [e for e in evs if e.get("kind") == "paint"]
    closes = [e for e in evs if e.get("kind") == "round_close"]

    opened = evs[0].get("kind") == "round_open"
    header = f"--- round {idx} " + ("" if opened else "(partial, no open) ")
    print(header + "-" * max(0, 60 - len(header)))

    for label, group in (
        ("machine landed", machine),
        ("sas landed", sas),
        ("paint", paints),
        ("round close", closes),
    ):
        for ev in group:
            extra = ""
            if label == "machine landed":
                extra = f"  deferred={ev.get('deferred')} ({ev.get('reason')})"
            if label == "sas landed":
                extra = f"  machine_paint_pending={ev.get('machine_paint_pending')}"
            print(f"  {rel(ev):9.1f} ms  {label}{extra}")

    # Only a real increase is a meter the player moved; a reload blanks a
    # column and refills it with the same number, which is not movement.
    moved = [e for e in changes if e.get("increased")]
    reseeds = len(changes) - len(moved)

    if not moved:
        print(f"   (no meter movement; {reseeds} cell reseed/blank)")
    else:
        print(f"  meter movement: {len(moved)} cells ({reseeds} reseeds ignored)")
        by_col: dict[str, list[float]] = {}
        for ev in moved:
            by_col.setdefault(str(ev.get("column")), []).append(rel(ev))
        for col, times in sorted(by_col.items()):
            print(
                f"    {col:<8} n={len(times):<3} "
                f"first={min(times):8.1f} ms  last={max(times):8.1f} ms"
            )
        for ev in moved:
            print(
                f"    {rel(ev):9.1f} ms  {ev.get('code'):<6} {str(ev.get('column')):<8} "
                f"{ev.get('old')!r} -> {ev.get('new')!r}"
            )
        times = [rel(e) for e in moved]
        spread = max(times) - min(times)
        verdict = "SINGLE PAINT" if spread <= 50.0 else "STAGGERED"
        print(f"  >> spread {spread:.1f} ms across {len(moved)} cells — {verdict}")

    for ev in flashes:
        print(f"  {rel(ev):9.1f} ms  flash_start {ev.get('codes')}")
    if moved and flashes:
        gap = min(rel(f) for f in flashes) - max(rel(c) for c in moved)
        print(f"  >> orange fires {gap:+.1f} ms after the last value change")
    print()


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1])
    else:
        found = newest_trace()
        if found is None:
            print(f"No trace found in {default_trace_dir()}")
            print("Run with SASVERIFY_METER_TRACE=1 to record one.")
            return 1
        path = found
    if not path.is_file():
        print(f"No such trace: {path}")
        return 1

    events = load(path)
    print(f"trace: {path}  ({len(events)} events)\n")
    rounds = split_rounds(events)
    real = [
        r
        for r in rounds
        if any(e.get("kind") == "value_change" and e.get("increased") for e in r)
    ]
    for i, evs in enumerate(rounds, 1):
        report_round(i, evs)
    print(f"{len(rounds)} round(s), {len(real)} with meter movement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
