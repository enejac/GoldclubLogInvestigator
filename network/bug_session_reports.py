"""Render tester + developer markdown from a live Bug Detector session."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from network.bug_session_events import (
    CATEGORY_LABELS,
    EventCategory,
    SessionEvent,
)

_STACK = re.compile(r"^\s*at\s+\S", re.I)


def _md_line(ev: SessionEvent) -> str:
    """Plain markdown (no HTML) — safe for QTextBrowser.setMarkdown and .md files."""
    ts = ev.ts or "—"
    return f"**[{ev.label()}]** `{ts}` · `{ev.source}` — {ev.summary}"


def _is_stack_frame(ev: SessionEvent) -> bool:
    return bool(_STACK.match(ev.summary) or _STACK.match(ev.raw))


def _is_report_noise(ev: SessionEvent) -> bool:
    if ev.critical or _is_stack_frame(ev):
        return False
    if ev.category in (
        EventCategory.HARDWARE,
        EventCategory.SAS,
        EventCategory.MIDDLEWARE,
        EventCategory.CRITICAL,
    ):
        return False
    s = ev.summary
    if re.search(
        r"^(trying to subscribe|Subscribing WSRTL|Missing node\.|"
        r"TableLayout => background color)",
        s,
        re.I,
    ):
        return True
    return False


def _events_for_report(events: list[SessionEvent]) -> list[SessionEvent]:
    return [
        e
        for e in events
        if not _is_report_noise(e) and not _is_stack_frame(e)
    ]


def _first_critical_index(events: list[SessionEvent]) -> int | None:
    for i, e in enumerate(events):
        if e.critical:
            return i
    return None


def _context_before_critical(
    events: list[SessionEvent], *, max_items: int = 12
) -> list[SessionEvent]:
    idx = _first_critical_index(events)
    if idx is None:
        return []
    before = events[:idx]
    signal = [
        e
        for e in before
        if not _is_report_noise(e)
        and e.category
        in (
            EventCategory.HUMAN,
            EventCategory.HARDWARE,
            EventCategory.SAS,
            EventCategory.MIDDLEWARE,
        )
    ]
    return signal[-max_items:]


def _stack_after_critical(events: list[SessionEvent]) -> list[SessionEvent]:
    idx = _first_critical_index(events)
    if idx is None:
        return []
    out: list[SessionEvent] = []
    for e in events[idx + 1 :]:
        if _is_stack_frame(e):
            out.append(e)
        elif e.critical:
            break
        elif not _is_report_noise(e) and e.category != EventCategory.HUMAN:
            break
        elif not _is_stack_frame(e) and e.category == EventCategory.HUMAN:
            break
    return out

def render_tester_session(
    *,
    cabinet: str,
    events: list[SessionEvent],
    started_utc: str,
    stopped_reason: str,
) -> str:
    shown = _events_for_report(events)
    crit = [e for e in events if e.critical]
    lines = [
        "# Bug session — tester guide",
        "",
        "| | |",
        "|---|---|",
        f"| Cabinet | {cabinet} |",
        f"| Started (UTC) | {started_utc} |",
        f"| Stopped | {stopped_reason} |",
        f"| Signal events | {len(shown)} (omitted startup noise: {len(events) - len(shown)}) |",
        "",
        "## Legend",
        "",
        "- **user** — touch, payout/collect, layout",
        "- **hardware** — ticket, bill, Dallas key",
        "- **SAS/cashless** — WAT / AFT",
        "- **middleware** — :8090 HTTP / PUT actions",
        "- **critical** — crash or process exit",
        "",
        "## Timeline",
        "",
    ]
    for ev in shown:
        if ev.category == EventCategory.FREE_FLOW and not ev.critical:
            continue
        lines.append(f"- {_md_line(ev)}")
    if crit:
        lines.extend(
            [
                "",
                "## Pass / fail",
                "",
                "**FAIL** — critical error during session.",
                "",
                "Focus on the last **user** / **hardware** lines before the **critical** entry.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Pass / fail",
                "",
                "No critical auto-stop. Mark fail manually if the bug reproduced without a logged crash.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def render_developer_session(
    *,
    cabinet: str,
    events: list[SessionEvent],
    started_utc: str,
    stopped_reason: str,
    sniff_used: bool,
) -> str:
    shown = _events_for_report(events)
    crit = [e for e in events if e.critical]
    omitted_noise = sum(1 for e in events if _is_report_noise(e))

    lines = [
        "# Bug session — developer notes",
        "",
        "| | |",
        "|---|---|",
        f"| Cabinet | {cabinet} |",
        f"| Started (UTC) | {started_utc} |",
        f"| Stopped | {stopped_reason} |",
        f"| Sniff (8090/30300/30550) | {'yes' if sniff_used else 'no (log-only)'} |",
        f"| Raw events | {len(events)} |",
        f"| Report events | {len(shown)} |",
    ]
    if omitted_noise:
        lines.append(f"| Omitted noise | {omitted_noise} (WSRTL subscribe / missing node / idle layout) |")
    lines.extend(["", ""])

    if crit:
        primary = crit[0]
        lines.extend(
            [
                "## Incident",
                "",
                f"**Primary:** {_md_line(primary)}",
                "",
            ]
        )
        stack = _stack_after_critical(events)
        if stack:
            lines.append("**Stack (godot / ruleta):**")
            lines.append("")
            lines.append("```")
            for fr in stack:
                lines.append(fr.summary.strip())
            lines.append("```")
            lines.append("")
        for extra in crit[1:]:
            lines.append(f"- {_md_line(extra)}")
        lines.append("")

        ctx = _context_before_critical(events)
        if ctx:
            lines.extend(
                [
                    "## User / hardware context (before crash)",
                    "",
                    "| Time (UTC) | Cat | Source | Summary |",
                    "|---|---|---|---|",
                ]
            )
            for ev in ctx:
                ts = ev.ts or "—"
                summ = ev.summary.replace("|", "\\|")[:120]
                lines.append(
                    f"| `{ts}` | {ev.label()} | `{ev.source}` | {summ} |"
                )
                if ev.commands:
                    for c in ev.commands:
                        lines.append(f"| | | | ↳ `{c}` |")
            lines.append("")

    lines.extend(
        [
            "## Signal timeline",
            "",
            "| # | Time (UTC) | Cat | Source | Summary |",
            "|---:|---|---|---|---|",
        ]
    )
    for i, ev in enumerate(shown, 1):
        ts = ev.ts or "—"
        summ = ev.summary.replace("|", "\\|")[:160]
        lines.append(f"| {i} | `{ts}` | {ev.label()} | `{ev.source}` | {summ} |")
    lines.append("")

    if shown:
        with_cmds = [e for e in shown if e.commands]
        if with_cmds:
            lines.extend(["## Inferred commands", ""])
            for ev in with_cmds:
                lines.append(f"- {_md_line(ev)}")
                for c in ev.commands:
                    lines.append(f"  - `{c}`")
            lines.append("")

    lines.extend(
        [
            "## Middleware reference",
            "",
            "| Port / verb | Role |",
            "|---|---|",
            "| GET `/api/data/{playerId}` | free-flow age poll |",
            "| PUT `/api/action/{playerId}` | UI actions (Collect, layout, chips) |",
            "| TCP `:8090` | ruleta EmbedIO WebAPI |",
            "| TCP `:30300` | Dallas / KeyCtrl |",
            "| TCP `:30550` | SAS WakeUp (roulette MUX) |",
            "",
        ]
    )
    return "\n".join(lines)


def write_session_reports(
    *,
    cabinet: str,
    events: list[SessionEvent],
    output_dir: Path,
    started_utc: str,
    stopped_reason: str,
    sniff_used: bool = False,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tester = output_dir / f"BUG-TEST-session-{stamp}.md"
    developer = output_dir / f"BUG-DEV-session-{stamp}.md"
    tester.write_text(
        render_tester_session(
            cabinet=cabinet,
            events=events,
            started_utc=started_utc,
            stopped_reason=stopped_reason,
        ),
        encoding="utf-8",
    )
    developer.write_text(
        render_developer_session(
            cabinet=cabinet,
            events=events,
            started_utc=started_utc,
            stopped_reason=stopped_reason,
            sniff_used=sniff_used,
        ),
        encoding="utf-8",
    )
    return tester, developer


def event_to_html(ev: SessionEvent) -> str:
    """One HTML line for the live QTextEdit stream."""
    from network.bug_session_events import CATEGORY_COLORS

    color = ev.color()
    label = CATEGORY_LABELS.get(ev.category, ev.category.value)
    ts = ev.ts or ""
    safe = (
        ev.summary.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    cmd = ""
    if ev.commands:
        c0 = ev.commands[0].replace("&", "&amp;").replace("<", "&lt;")
        cmd = f" <i style='opacity:0.85'>{c0}</i>"
    return (
        f"<span style='color:{color}'>"
        f"<b>[{label}]</b> {ts} <code>{ev.source}</code> {safe}{cmd}"
        f"</span>"
    )
