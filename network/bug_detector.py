"""Detect known Godot/ruleta crash patterns and generate tester + developer reports."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import DEFAULT_LOCAL_LOG_ROOT, format_unc_log_root
from app_paths import bug_detector_output_base
from network.bug_detector_flow import FlowStep, build_flow_steps, render_flow_section

_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
)
_NRE_LINE = re.compile(r"NullReferenceException", re.I)
_PAYOUT_FRAME = re.compile(r"MainScreen\.PayoutPressed", re.I)
_LAYOUT_MARK = re.compile(
    r"Running base setup [LS]|TableLayout|trying to subscribe|Subscribing WSRTL",
    re.I,
)
_TOUCH = re.compile(r"_on_TouchZoneButTouchScreen_pressed|Checking OPF for", re.I)
_PUT = re.compile(r"Sending put action (?P<name>\S+) data (?P<data>.*)$", re.I)
_PID = re.compile(r"This process['\u2019] PID:(?P<pid>\d+)", re.I)
_EXIT = re.compile(r"Proces with ID (?P<pid>\d+) exited unexpectedly", re.I)


@dataclass
class TimelineRow:
    time: str
    actor: str
    event: str
    middleware: str


@dataclass
class BugIncident:
    kind: str
    title: str
    cabinet: str
    detected_at: str
    crash_time: str
    godot_log: str
    ruleta_log: str
    layout_before_crash: bool
    layout_time: str
    touch_time: str
    stack_excerpt: str
    timeline: list[TimelineRow] = field(default_factory=list)
    flow_steps: list[FlowStep] = field(default_factory=list)
    put_actions_nearby: list[str] = field(default_factory=list)
    process_exits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class BugDetectorResult:
    ok: bool
    incidents: list[BugIncident]
    tester_paths: list[str]
    developer_paths: list[str]
    log: str
    output_dir: str


def _cabinet_is_local_token(cabinet: str) -> bool:
    c = (cabinet or "").strip().lower()
    return c in {"", "local", "localhost", ".", "127.0.0.1"}


def resolve_log_roots(cabinet: str) -> list[Path]:
    """Local Goldclub log roots and/or UNC for ``cabinet`` IP / local."""
    roots: list[Path] = []
    cab = (cabinet or "").strip() or "local"

    def _add(p: Path) -> None:
        try:
            if p.is_dir() and p not in roots:
                roots.append(p)
        except OSError:
            return

    if _cabinet_is_local_token(cab):
        for p in (
            Path(DEFAULT_LOCAL_LOG_ROOT),
            Path(r"C:\Goldclub\var\log"),
            Path(r"C:\goldclub\var\log"),
            Path(r"G:\var\log"),
        ):
            _add(p)
        return roots

    _add(Path(format_unc_log_root(cab)))
    # On-cabinet run with fleet IP of self: also try local
    for p in (Path(DEFAULT_LOCAL_LOG_ROOT), Path(r"C:\Goldclub\var\log")):
        _add(p)
    return roots


def _find_component_logs(roots: list[Path], component: str) -> list[Path]:
    found: list[Path] = []
    comp = component.lower()
    for root in roots:
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name.lower()
                if name == comp or name.startswith(comp):
                    for log in child.glob("*.log"):
                        found.append(log)
        except OSError:
            continue
        try:
            for log in root.glob(f"**/{component}/*.log"):
                if log not in found:
                    found.append(log)
        except OSError:
            continue
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found


def _read_text(path: Path, max_bytes: int = 12_000_000) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
    except OSError:
        return ""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


def _line_ts(line: str) -> str:
    m = _TS.match(line.strip())
    return m.group("ts") if m else ""


def _extract_stack_block(lines: list[str], start: int, max_follow: int = 14) -> str:
    block = [lines[start].rstrip()]
    for j in range(start + 1, min(len(lines), start + max_follow)):
        s = lines[j].rstrip()
        if not s.strip():
            break
        if _TS.match(s.strip()) and "at " not in s and "NullReference" not in s:
            break
        if (
            s.lstrip().startswith("at ")
            or "NullReference" in s
            or "sender:" in s
            or "PayoutPressed" in s
            or "WinSysButton" in s
            or "BarsButton" in s
        ):
            block.append(s)
            continue
        break
    return "\n".join(block)


def detect_collect_payout_incidents(
    *,
    cabinet: str,
    godot_text: str,
    ruleta_text: str,
    godot_log: str,
    ruleta_log: str,
) -> list[BugIncident]:
    """Parse log text into Collect/Payout NRE incidents."""
    g_lines = godot_text.splitlines()
    incidents: list[BugIncident] = []

    for i, line in enumerate(g_lines):
        if not _NRE_LINE.search(line):
            continue
        stack = _extract_stack_block(g_lines, i)
        if not _PAYOUT_FRAME.search(stack):
            continue
        crash_ts = _line_ts(line)

        layout_time = ""
        touch_time = ""
        layout_before = False
        window = g_lines[max(0, i - 200) : i]
        remount_start = ""
        for wl in window:
            if re.search(r"Running base setup L\b", wl, re.I):
                remount_start = _line_ts(wl) or remount_start
            if _LAYOUT_MARK.search(wl):
                layout_before = True
                if not layout_time:
                    layout_time = _line_ts(wl)
            if _TOUCH.search(wl):
                touch_time = _line_ts(wl) or touch_time
        if remount_start:
            layout_time = remount_start

        puts: list[str] = []
        for wl in g_lines[max(0, i - 400) : i]:
            pm = _PUT.search(wl)
            if pm:
                puts.append(
                    f"{_line_ts(wl)} PUT {pm.group('name')} data={pm.group('data').strip()}"
                )

        exits: list[str] = []
        crash_prefix = (crash_ts or "")[:19]
        for rl in ruleta_text.splitlines():
            rts = _line_ts(rl)
            if crash_prefix and rts and rts[:19] >= crash_prefix:
                if _EXIT.search(rl):
                    exits.append(rl.strip())

        timeline = _build_timeline(
            g_lines,
            i,
            crash_ts=crash_ts,
            layout_time=layout_time,
            touch_time=touch_time,
            layout_before=layout_before,
            exits=exits,
        )
        flow_steps = build_flow_steps(
            g_lines,
            i,
            crash_ts=crash_ts,
            layout_time=layout_time,
            touch_time=touch_time,
            layout_before=layout_before,
            exits=exits,
            stack=stack,
        )

        notes: list[str] = []
        if layout_before:
            notes.append(
                "Layout/MainScreen remount markers appear shortly before Collect/Payout."
            )
            if layout_time and crash_ts:
                layout_puts = [
                    p
                    for p in puts
                    if p[:19] >= layout_time[:19] and p[:19] <= crash_ts[:19]
                ]
                if not layout_puts:
                    notes.append(
                        "No middleware PUT logged between layout remount and Collect crash "
                        "(local UI remount only)."
                    )
        notes.append(
            "Collect UI maps to MainScreen.PayoutPressed; crash happens before any "
            "Collect/Payout PUT is logged."
        )

        incidents.append(
            BugIncident(
                kind="collect_payout_nre",
                title="Godot Collect/Payout NullReferenceException",
                cabinet=cabinet,
                detected_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                crash_time=crash_ts,
                godot_log=godot_log,
                ruleta_log=ruleta_log,
                layout_before_crash=layout_before,
                layout_time=layout_time,
                touch_time=touch_time,
                stack_excerpt=stack,
                timeline=timeline,
                flow_steps=flow_steps,
                put_actions_nearby=puts[-12:],
                process_exits=exits[:8],
                notes=notes,
            )
        )
    return incidents


def _build_timeline(
    g_lines: list[str],
    crash_idx: int,
    *,
    crash_ts: str,
    layout_time: str,
    touch_time: str,
    layout_before: bool,
    exits: list[str],
) -> list[TimelineRow]:
    rows: list[TimelineRow] = []
    for wl in reversed(g_lines[max(0, crash_idx - 800) : crash_idx]):
        pm = _PID.search(wl)
        if pm:
            rows.append(
                TimelineRow(
                    _line_ts(wl),
                    "free-flow",
                    f"Godot PID {pm.group('pid')} running",
                    "GET /api/data/{id} age polls; optional sync PUTs",
                )
            )
            break
    if layout_before and layout_time:
        rows.append(
            TimelineRow(
                layout_time,
                "human (layout)",
                "Layout / MainScreen remount (base setup + subscribe / TableLayout)",
                "Often none; sometimes PUT Paytable/MenuCommands/SetChip/SetNeighboursPower",
            )
        )
    if touch_time:
        rows.append(
            TimelineRow(
                touch_time,
                "human (Collect)",
                "Touch / Collect press (TouchZone or WinSys button)",
                "Expected Collect PUT - not reached on failure",
            )
        )
    rows.append(
        TimelineRow(
            crash_ts,
            "bug",
            "NullReferenceException in MainScreen.PayoutPressed",
            "No Collect/Payout PUT - process dies first",
        )
    )
    for ex in exits[:3]:
        rows.append(
            TimelineRow(
                _line_ts(ex) or crash_ts,
                "ruleta",
                ex[-140:],
                "Middleware stays up; Godot client drops",
            )
        )
    return rows


def render_tester_report(inc: BugIncident) -> str:
    if inc.layout_before_crash:
        steps = (
            "1. Start roulette so the Godot game screen is up (station 1 / player 0).\n"
            "2. Wait until the screen looks idle (game running normally).\n"
            "3. Switch layout / change view on the table.\n"
            "4. Within about 2 seconds, press **Collect**.\n"
            "5. Watch for the UI to freeze or restart.\n"
            "6. Optional: press Collect again as soon as the screen comes back.\n"
        )
    else:
        steps = (
            "1. Start roulette so the Godot game screen is up (station 1 / player 0).\n"
            "2. Wait until the screen looks idle.\n"
            "3. Press **Collect** (cashout / collect credits).\n"
            "4. Watch for the UI to freeze or restart.\n"
            "5. Optional: press Collect again after restart.\n"
        )
    return (
        f"# Collect button crash - tester guide\n\n"
        f"Cabinet: {inc.cabinet}  \n"
        f"When: {inc.crash_time or inc.detected_at}  \n"
        f"Detected: {inc.detected_at}\n\n"
        f"## What happens\n\n"
        f"1. Player may switch **layout / view** on the table.\n"
        f"2. Player presses **Collect** (cashout / collect credits).\n"
        f"3. Godot UI **crashes** and restarts.\n"
        f"4. Pressing Collect again right after restart can crash again.\n\n"
        f"## How to reproduce\n\n"
        f"{steps}\n"
        f"## Pass / fail\n\n"
        f"| Result | Meaning |\n"
        f"|---|---|\n"
        f"| UI stays up; Collect works or shows a normal message | PASS |\n"
        f"| UI dies / restarts right after Collect | FAIL |\n\n"
        f"## What you should see in logs (optional check)\n\n"
        f"Godot: `{inc.godot_log}`\n\n"
        f"- Layout switch (if used): `Running base setup`, `TableLayout`\n"
        f"- Crash: `PayoutPressed` and `NullReferenceException`\n\n"
        f"Backend: `{inc.ruleta_log}` may say a process `exited unexpectedly`\n\n"
        f"## Notes for testers\n\n"
        f"- Collect is the on-screen collect/cashout control. In logs it may appear as **Payout**.\n"
        f"- You do **not** need to place bets for this bug.\n"
        f"- Layout before Collect was "
        f"{'observed' if inc.layout_before_crash else 'not clearly logged'} in this detection.\n"
        f"- Developer detail: see the matching `BUG-DEV-*.md` next to this file.\n"
    )


def render_developer_report(inc: BugIncident) -> str:
    puts = "\n".join(f"- `{p}`" for p in inc.put_actions_nearby) or "- (none in lookback window)"
    notes = "\n".join(f"- {n}" for n in inc.notes) or "- (none)"
    exits = "\n".join(f"- `{e}`" for e in inc.process_exits) or "- (none parsed)"
    flow_body = render_flow_section(inc.flow_steps)
    if not inc.flow_steps and inc.timeline:
        tl_lines = [
            f"| {r.time} | {r.actor} | {r.event} | {r.middleware} |"
            for r in inc.timeline
        ]
        flow_body = (
            "| Time | Actor | Event | Middleware |\n|---|---|---|---|\n" + "\n".join(tl_lines)
        )

    yes_layout = "yes" if inc.layout_before_crash else "no / unclear"
    return (
        "# Collect / Payout NRE - developer notes\n\n"
        "| | |\n|---|---|\n"
        f"| Cabinet | {inc.cabinet} |\n"
        f"| Crash time | {inc.crash_time or 'unknown'} |\n"
        f"| Detected | {inc.detected_at} |\n"
        f"| Kind | `{inc.kind}` |\n"
        "| Fault | `System.NullReferenceException` at `MainScreen.PayoutPressed` |\n"
        f"| Godot log | `{inc.godot_log}` |\n"
        f"| Ruleta log | `{inc.ruleta_log}` |\n"
        f"| Layout before crash | {yes_layout} ({inc.layout_time or '-'}) |\n\n"
        "UI label **Collect** -> "
        "`WinSysButton* -> BarsButtonController.PayoutPressed -> MainScreen.PayoutPressed`.\n\n"
        "## Middleware cheatsheet (8090)\n\n"
        "| Verb | Path | Role |\n|---|---|---|\n"
        "| GET | `/api/data/{playerId}` | free-flow age poll |\n"
        "| PUT | `/api/action/{playerId}` | UI actions (`Sending put action …`) |\n\n"
        "Logged PUT names in the wild: `Paytable`, `MenuCommands`, `SetChip`, `SetNeighboursPower`.\n"
        "There is **no** logged Collect / Payout / Layout action name on failure.\n\n"
        "## Flow (UTC) + Godot commands + middleware after each step\n\n"
        f"{flow_body}\n\n"
        f"## Stack\n\n```\n{inc.stack_excerpt}\n```\n\n"
        f"## Nearby PUTs (before crash)\n\n{puts}\n\n"
        f"## Ruleta process exits\n\n{exits}\n\n"
        f"## Notes\n\n{notes}\n\n"
        "## Likely fix\n\n"
        "Null-guard in `MainScreen.PayoutPressed`. Disable Collect until layout remount / "
        "WSRTL subscriptions finish. Handle OPF false without throwing.\n"
    )



def default_output_dir(cabinet: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", (cabinet or "local").strip()) or "local"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return bug_detector_output_base() / f"{safe}_{stamp}"


def scan_and_write_reports(
    cabinet: str,
    *,
    output_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> BugDetectorResult:
    """Scan cabinet logs, detect incidents, write tester + developer markdown."""

    def prog(msg: str) -> None:
        if progress:
            progress(msg)

    lines: list[str] = []
    cab = (cabinet or "").strip() or "local"
    roots = resolve_log_roots(cab)
    if not roots:
        return BugDetectorResult(
            ok=False,
            incidents=[],
            tester_paths=[],
            developer_paths=[],
            log=f"No log roots reachable for cabinet={cab!r}",
            output_dir="",
        )
    lines.append(f"cabinet={cab}")
    lines.append("roots=" + "; ".join(str(r) for r in roots))

    godot_logs = _find_component_logs(roots, "godot1")
    if not godot_logs:
        godot_logs = _find_component_logs(roots, "godot")
    ruleta_logs = _find_component_logs(roots, "ruleta")
    lines.append(f"godot_logs={len(godot_logs)} ruleta_logs={len(ruleta_logs)}")
    if not godot_logs:
        return BugDetectorResult(
            ok=False,
            incidents=[],
            tester_paths=[],
            developer_paths=[],
            log="\n".join(lines + ["No godot1/godot logs found"]),
            output_dir="",
        )

    all_incidents: list[BugIncident] = []
    for gpath in godot_logs[:5]:
        prog(f"Scanning {gpath}")
        gtext = _read_text(gpath)
        rpath = Path()
        rtext = ""
        day = gpath.stem
        for rp in ruleta_logs:
            if rp.stem == day or day in rp.name:
                rpath = rp
                rtext = _read_text(rp)
                break
        if not rtext and ruleta_logs:
            rpath = ruleta_logs[0]
            rtext = _read_text(rpath)
        found = detect_collect_payout_incidents(
            cabinet=cab,
            godot_text=gtext,
            ruleta_text=rtext,
            godot_log=str(gpath),
            ruleta_log=str(rpath) if rpath else "",
        )
        lines.append(f"{gpath.name}: {len(found)} incident(s)")
        all_incidents.extend(found)

    out = output_dir or default_output_dir(cab)
    out.mkdir(parents=True, exist_ok=True)
    tester_paths: list[str] = []
    dev_paths: list[str] = []

    if not all_incidents:
        empty_t = out / "BUG-TEST-no-incidents.md"
        empty_d = out / "BUG-DEV-no-incidents.md"
        empty_t.write_text(
            f"# Bug Detector - no Collect/Payout NRE found\n\n"
            f"Cabinet: {cab}\nScanned: "
            + ", ".join(str(r) for r in roots)
            + "\n",
            encoding="utf-8",
        )
        empty_d.write_text(
            "# Bug Detector - no incidents\n\nLog:\n```\n"
            + "\n".join(lines)
            + "\n```\n",
            encoding="utf-8",
        )
        return BugDetectorResult(
            ok=True,
            incidents=[],
            tester_paths=[str(empty_t)],
            developer_paths=[str(empty_d)],
            log="\n".join(lines + ["No Collect/Payout NRE incidents matched"]),
            output_dir=str(out),
        )

    seen: set[str] = set()
    unique: list[BugIncident] = []
    for inc in all_incidents:
        key = inc.crash_time or inc.stack_excerpt[:80]
        if key in seen:
            continue
        seen.add(key)
        unique.append(inc)

    for n, inc in enumerate(unique, start=1):
        safe_ts = re.sub(r"[^\d]", "", (inc.crash_time or "")[:19]) or f"{n:02d}"
        t_path = out / f"BUG-TEST-Collect-crash-{safe_ts}.md"
        d_path = out / f"BUG-DEV-Collect-Payout-NRE-{safe_ts}.md"
        t_path.write_text(render_tester_report(inc), encoding="utf-8")
        d_path.write_text(render_developer_report(inc), encoding="utf-8")
        tester_paths.append(str(t_path))
        dev_paths.append(str(d_path))
        prog(f"Wrote {t_path.name} / {d_path.name}")

    (out / "README.md").write_text(
        "# Bug Detector reports\n\n"
        "| Audience | Files |\n|---|---|\n"
        "| Testers | `BUG-TEST-*.md` |\n"
        "| Developers | `BUG-DEV-*.md` |\n",
        encoding="utf-8",
    )

    return BugDetectorResult(
        ok=True,
        incidents=unique,
        tester_paths=tester_paths,
        developer_paths=dev_paths,
        log="\n".join(lines + [f"Wrote {len(unique)} incident report pair(s) -> {out}"]),
        output_dir=str(out),
    )