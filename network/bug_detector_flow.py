"""Flow-step enrichment for Bug Detector developer reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
)
_MSG = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\S+\s+(?:INFO|WARN|ERROR|ERRO)\s+(?:\[[^\]]*\]\s+)?(?P<body>.*)$",
    re.I,
)
_PUT = re.compile(r"Sending put action (?P<name>\S+) data (?P<data>.*)$", re.I)
_PID = re.compile(r"This process['\u2019] PID:(?P<pid>\d+)", re.I)
_TOUCH = re.compile(r"_on_TouchZoneButTouchScreen_pressed|Checking OPF for", re.I)


@dataclass
class FlowStep:
    number: int
    time: str
    title: str
    actor: str
    godot_commands: list[str] = field(default_factory=list)
    middleware_commands: list[str] = field(default_factory=list)
    note: str = ""


def line_ts(line: str) -> str:
    m = _TS.match(line.strip())
    return m.group("ts") if m else ""


def message_body(line: str) -> str:
    m = _MSG.match(line.strip())
    if m:
        return (m.group("body") or "").strip()
    s = line.strip()
    if s.lstrip().startswith("at ") or s.startswith("sender:"):
        return s
    return s


def is_interesting_godot(line: str) -> bool:
    body = message_body(line)
    if not body or "Average FPS" in body:
        return False
    keys = (
        "PID:",
        "Sending put action",
        "Initiating get with age",
        "Connection response",
        "Sending settings",
        "Skin:",
        "Starting new gui",
        "Running base setup",
        "trying to subscribe",
        "Subscribing WSRTL",
        "Missing node",
        "TableLayout",
        "TouchZone",
        "Checking OPF",
        "NullReferenceException",
        "PayoutPressed",
        "BarsButtonController",
        "WinSysButton",
        "Wrong game status",
        "localizationAPIURL",
        "Language option",
        "ChangeView",
        "COLLECT CREDIT",
        "Frontend watching",
        "There is no --url",
        "Adding player station",
        "Setting up RouletteData",
        "Id is ",
        "Unable to find cached messages",
        "Running an exported version",
    )
    return any(k in body for k in keys) or body.lstrip().startswith("at ")


def collapse_godot_commands(raw_lines: list[str], *, limit: int = 48) -> list[str]:
    out: list[str] = []
    empty_sub = 0

    def flush() -> None:
        nonlocal empty_sub
        if empty_sub:
            out.append(f"trying to subscribe  (x{empty_sub}, empty target)")
            empty_sub = 0

    for line in raw_lines:
        if not is_interesting_godot(line):
            continue
        body = message_body(line)
        ts = line_ts(line)
        if body.strip() == "trying to subscribe":
            empty_sub += 1
            continue
        flush()
        if ts and not body.lstrip().startswith("at "):
            out.append(f"{ts}  {body}")
        else:
            out.append(body)
        if len(out) >= limit:
            break
    flush()
    return out[:limit]


def puts_in_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for wl in lines:
        pm = _PUT.search(wl)
        if pm:
            out.append(
                f"PUT /api/action/{{playerId}}  action={pm.group('name')} "
                f"data={pm.group('data').strip()}"
            )
    return out


def gets_in_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for wl in lines:
        body = message_body(wl)
        if "Initiating get with age" in body:
            out.append("GET /api/data/{playerId}  (age poll)")
        if "localizationAPIURL" in body:
            out.append(body)
        if "Connection response" in body:
            out.append(f"middleware: {body}")
        if "Sending settings" in body:
            out.append("settings handshake / connect to middleware")
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def build_flow_steps(
    g_lines: list[str],
    crash_idx: int,
    *,
    crash_ts: str,
    layout_time: str,
    touch_time: str,
    layout_before: bool,
    exits: list[str],
    stack: str,
) -> list[FlowStep]:
    steps: list[FlowStep] = []
    n = 0

    def add(
        time: str,
        title: str,
        actor: str,
        godot: list[str],
        mid: list[str],
        note: str = "",
    ) -> None:
        nonlocal n
        n += 1
        steps.append(
            FlowStep(
                number=n,
                time=time,
                title=title,
                actor=actor,
                godot_commands=godot,
                middleware_commands=mid,
                note=note,
            )
        )

    # 1) Godot up
    pid_ts = ""
    pid_idx = None
    for k in range(crash_idx - 1, max(-1, crash_idx - 2500), -1):
        if k < 0:
            break
        if _PID.search(g_lines[k]):
            pid_idx = k
            pid_ts = line_ts(g_lines[k])
            break
    if pid_idx is not None:
        chunk = g_lines[pid_idx : pid_idx + 50]
        mid = gets_in_lines(chunk) or [
            "connect / settings handshake",
            "GET /api/data/{playerId} (age polls start)",
        ]
        add(
            pid_ts,
            "Godot up + WebAPI connect",
            "free-flow",
            collapse_godot_commands(chunk, limit=22),
            mid,
        )

    # 2) Idle sync puts before layout/crash
    put_idxs = [k for k, wl in enumerate(g_lines[:crash_idx]) if _PUT.search(wl)]
    if put_idxs:
        last_i = put_idxs[-1]
        last_ts = line_ts(g_lines[last_i])
        layout_cmp = (layout_time or crash_ts or "")[:19]
        if not layout_cmp or last_ts[:19] < layout_cmp:
            cluster = [g_lines[k] for k in put_idxs[-8:]]
            mid = puts_in_lines(cluster) or [
                "PUT /api/action/{playerId}  (Paytable / MenuCommands / SetChip / SetNeighboursPower)"
            ]
            add(
                last_ts,
                "Idle UI sync PUT quartet",
                "free-flow",
                collapse_godot_commands(cluster, limit=12),
                mid,
                note="Typical ~40s cadence when idle; not Collect.",
            )

    # 3) Idle gap
    if pid_ts and layout_time and pid_ts[:16] < layout_time[:16]:
        add(
            f"{pid_ts[:16]} … {layout_time[:19]}",
            "Idle gap before layout",
            "free-flow",
            [
                "Average FPS heartbeats (omitted from report)",
                "Initiating get with age (periodic)",
            ],
            ["GET /api/data/{playerId} only"],
            note="No human PUT/touch required in this window.",
        )

    # 4) Layout switch - remount cluster immediately before Collect/crash
    if layout_before and layout_time:
        lay_i = None
        for j in range(crash_idx - 1, max(-1, crash_idx - 400), -1):
            if j < 0:
                break
            if re.search(r"Running base setup L\b", g_lines[j], re.I):
                lay_i = j
                # Include preceding subscribe storm in the same minute
                while lay_i > 0 and is_interesting_godot(g_lines[lay_i - 1]):
                    prev_ts = line_ts(g_lines[lay_i - 1])
                    cur_ts = line_ts(g_lines[lay_i])
                    if prev_ts and cur_ts and prev_ts[:16] != cur_ts[:16]:
                        break
                    lay_i -= 1
                break
        if lay_i is None:
            for j in range(crash_idx - 1, max(-1, crash_idx - 400), -1):
                if j < 0:
                    break
                if re.search(r"trying to subscribe|TableLayout", g_lines[j], re.I):
                    lay_i = j
                    while lay_i > 0 and is_interesting_godot(g_lines[lay_i - 1]):
                        prev_ts = line_ts(g_lines[lay_i - 1])
                        cur_ts = line_ts(g_lines[lay_i])
                        if prev_ts and cur_ts and prev_ts[:16] != cur_ts[:16]:
                            break
                        lay_i -= 1
                    break
        if lay_i is None:
            lay_i = max(0, crash_idx - 80)
        touch_i = crash_idx
        for j in range(lay_i, crash_idx):
            if "_on_TouchZoneButTouchScreen_pressed" in g_lines[j]:
                touch_i = j
                break
        lay_lines = g_lines[lay_i:touch_i]
        # Keep only lines near crash minute to avoid earlier boot noise
        crash_min = (crash_ts or "")[:16]
        if crash_min:
            filtered = []
            for wl in lay_lines:
                ts = line_ts(wl)
                if not ts or ts[:16] >= crash_min:
                    filtered.append(wl)
            if filtered:
                lay_lines = filtered
        mid = puts_in_lines(lay_lines) + gets_in_lines(lay_lines)
        note = (
            "Local MainScreen/TableLayout remount. No dedicated Layout/ChangeView PUT name."
            if not puts_in_lines(lay_lines)
            else "Layout remount also pushed the standard sync PUT quartet."
        )
        if not mid:
            mid = ["(none logged - local remount only)"]
        remount_ts = line_ts(g_lines[lay_i]) or layout_time
        add(
            remount_ts,
            "Layout switch / Change-View remount (human)",
            "human (layout)",
            collapse_godot_commands(lay_lines, limit=48),
            mid,
            note=note,
        )

    # 5) Collect press
    if touch_time:
        t_lines: list[str] = []
        for j in range(max(0, crash_idx - 40), crash_idx):
            ts = line_ts(g_lines[j])
            if _TOUCH.search(g_lines[j]) or (
                ts and touch_time and ts[:19] >= touch_time[:19]
            ):
                t_lines.append(g_lines[j])
        godot = collapse_godot_commands(t_lines, limit=20)
        if not any("TouchZone" in x or "OPF" in x for x in godot):
            godot = [
                f"{touch_time}  PLAYER0: _on_TouchZoneButTouchScreen_pressed",
                f"{touch_time}  Checking OPF for 0: …",
            ] + godot
        godot.append(
            "-> WinSysButtonImages.ButtonUp/ButtonDown -> "
            "BarsButtonController.PayoutPressed -> MainScreen.PayoutPressed"
        )
        add(
            touch_time,
            "Collect press (human)",
            "human (Collect)",
            godot,
            ["(none yet - Collect PUT not sent before handler runs)"],
            note="UI label Collect maps to PayoutPressed.",
        )

    # 6) NRE
    stack_lines = [x for x in stack.splitlines() if x.strip()]
    godot = []
    if stack_lines:
        first = stack_lines[0]
        if line_ts(first):
            godot.append(f"{line_ts(first)}  {message_body(first)}")
        else:
            godot.append(f"{crash_ts}  {first}")
        godot.extend(stack_lines[1:])
    else:
        godot = [f"{crash_ts}  NullReferenceException in MainScreen.PayoutPressed"]
    add(
        crash_ts,
        "NRE - MainScreen.PayoutPressed",
        "bug",
        godot,
        ["(none - process dies before Collect/Payout PUT)"],
        note="Fault site IL [0x00122] in MainScreen.PayoutPressed.",
    )

    # 7) exits (collapse crash-loop PIDs into one readable step)
    if exits:
        exit_bodies = [message_body(ex) for ex in exits[:5]]
        add(
            line_ts(exits[0]) or crash_ts,
            "ruleta: Godot process exited (crash loop)",
            "ruleta",
            exit_bodies,
            ["Middleware :8090 stays up; Godot client socket drops"],
            note="ruleta keeps respawning Godot; each Collect re-press can NRE again.",
        )

    # 8) first respawn after this crash (skip the NRE block itself)
    after = g_lines[crash_idx : crash_idx + 120]
    respawn_idx = next((i for i, wl in enumerate(after) if _PID.search(wl)), None)
    if respawn_idx is not None:
        chunk = after[respawn_idx : respawn_idx + 55]
        rts = line_ts(after[respawn_idx])
        mid = gets_in_lines(chunk) or [
            "reconnect / Sending settings",
            "GET /api/data/{playerId}",
        ]
        add(
            rts,
            "Godot respawn (auto)",
            "free-flow",
            collapse_godot_commands(chunk, limit=32),
            mid,
            note="Re-pressing Collect during/after init often repeats the NRE.",
        )

    return steps


def render_flow_section(steps: list[FlowStep]) -> str:
    if not steps:
        return "_No flow steps captured._"

    def bullets(items: list[str], empty: str) -> str:
        if not items:
            return f"  - {empty}"
        lines_out: list[str] = []
        for x in items:
            if x.startswith("->") or x.startswith("(") or x.lstrip().startswith("at "):
                lines_out.append(f"  - {x}")
            else:
                lines_out.append(f"  - `{x}`")
        return "\n".join(lines_out)

    blocks: list[str] = []
    for step in steps:
        block = [
            f"### {step.number}. {step.title}",
            f"- When: `{step.time or '-'}`",
            f"- Actor: **{step.actor}**",
        ]
        if step.note:
            block.append(f"- Note: {step.note}")
        block.append("- Godot commands / log events:")
        block.append(bullets(step.godot_commands, "(none captured)"))
        block.append("- Middleware after this step:")
        block.append(bullets(step.middleware_commands, "(none)"))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)
