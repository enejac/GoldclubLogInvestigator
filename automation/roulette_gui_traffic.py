"""
Parse HttpGuiSniff dumps and orchestrate remote Godot<->middleware (:8090) captures.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_HTTP_RE = re.compile(
    r"^HTTP\s+t=\s*(?P<t>\d+)\s+wall=(?P<wall>\S+)\s+dir=(?P<dir>\S+)\s+"
    r"sport=(?P<sport>\d+)\s+dport=(?P<dport>\d+)\s+"
    r"method=(?P<method>\S+)\s+status=(?P<status>\S+)\s+path=(?P<path>\S+)\s+"
    r"body=(?P<body>.*)$"
)

_PUT_ACTION_RE = re.compile(
    r"Sending put action\s+(?P<action>\S+)\s+data\s+(?P<data>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class HttpGuiEvent:
    t_ms: int
    wall: str
    direction: str
    sport: int
    dport: int
    method: str
    status: str
    path: str
    body: str

    @property
    def action_hint(self) -> str:
        """Best-effort action name from path/body (SetChip, Paytable, ...)."""
        blob = f"{self.path} {self.body}".replace("%20", " ")
        for key in (
            "SetChip",
            "SetNeighboursPower",
            "MenuCommands",
            "Paytable",
            "Clear",
            "Cancel",
            "Repeat",
            "Spin",
            "Bet",
        ):
            if key.lower() in blob.lower():
                return key
        if "/api/action" in self.path.lower():
            return "api_action"
        if "/api/data" in self.path.lower():
            return "api_data"
        return ""


def _load_dump_text(path: Path) -> str:
    raw = path.read_bytes()
    # PowerShell `>` redirect is UTF-16 LE; HttpGuiSniff / Start-Process redirect is UTF-8.
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_http_gui_dump_file(path: Path) -> list[HttpGuiEvent]:
    return parse_http_gui_dump(_load_dump_text(path))


def parse_http_gui_dump(text: str) -> list[HttpGuiEvent]:
    events: list[HttpGuiEvent] = []
    for line in text.splitlines():
        m = _HTTP_RE.match(line.strip())
        if not m:
            continue
        body = m.group("body")
        if body == "-":
            body = ""
        else:
            body = body.replace("%20", " ").replace("\\n", "\n").replace("\\r", "")
        method = m.group("method")
        status = m.group("status")
        path = m.group("path")
        if method == "-":
            method = ""
        if status == "-":
            status = ""
        if path == "-":
            path = ""
        events.append(
            HttpGuiEvent(
                t_ms=int(m.group("t")),
                wall=m.group("wall"),
                direction=m.group("dir"),
                sport=int(m.group("sport")),
                dport=int(m.group("dport")),
                method=method,
                status=status,
                path=path,
                body=body,
            )
        )
    return events


def parse_godot_put_actions(lines: list[str]) -> list[tuple[str, str]]:
    """Return (action, data) from godot1 'Sending put action' lines."""
    out: list[tuple[str, str]] = []
    for line in lines:
        m = _PUT_ACTION_RE.search(line)
        if m:
            out.append((m.group("action"), m.group("data").strip()))
    return out


def summarize_events(events: list[HttpGuiEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ev in events:
        key = ev.action_hint or (ev.method or "RESP")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_gui_sniff(
    *,
    ip: str = "10.0.0.90",
    seconds: int = 40,
    ports: list[int] | None = None,
    out_dir: Path | None = None,
) -> Path:
    """
    Run ``Invoke-RuletaGuiSniff.ps1`` and return the local dump path.
    Requires WinDivert at ``C:\\Tools\\WinDivert\\x64`` on the workstation.
    """
    root = _repo_root()
    script = root / "lab" / "roulette" / "Invoke-RuletaGuiSniff.ps1"
    if not script.is_file():
        raise FileNotFoundError(script)
    ports = ports or [8090]
    out_dir = out_dir or (root / "_tmp_logs" / "gui-sniff")
    out_dir.mkdir(parents=True, exist_ok=True)
    ports_csv = ",".join(str(p) for p in ports)
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ComputerName",
        ip,
        "-Seconds",
        str(int(seconds)),
        "-Ports",
        ports_csv,
        "-OutDir",
        str(out_dir),
    ]
    run_kw: dict = {"capture_output": True, "text": True, "timeout": seconds + 120}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    r = subprocess.run(cmd, **run_kw)
    # Script Write-Outputs the dump path on the last line.
    stdout = (r.stdout or "").strip()
    stderr = (r.stderr or "").strip()
    if r.returncode != 0:
        raise RuntimeError(
            f"Invoke-RuletaGuiSniff failed rc={r.returncode}\n{stdout[-2000:]}\n{stderr[-1000:]}"
        )
    # Prefer explicit path line ending with .txt under out_dir.
    dump: Path | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip().strip('"')
        if line.lower().endswith(".txt") and Path(line).is_file():
            dump = Path(line)
            break
    if dump is None:
        cands = sorted(out_dir.glob("gui8090-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            raise FileNotFoundError(f"No gui sniff dump in {out_dir}\n{stdout[-1500:]}")
        dump = cands[0]
    return dump


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Sniff Godot<->ruleta HTTP (:8090)")
    p.add_argument("--ip", default="10.0.0.90")
    p.add_argument("--seconds", type=int, default=40)
    p.add_argument("--ports", default="8090")
    args = p.parse_args(argv)
    ports = [int(x) for x in args.ports.split(",") if x.strip()]
    dump = run_gui_sniff(ip=args.ip, seconds=args.seconds, ports=ports)
    events = parse_http_gui_dump_file(dump)
    print(f"dump={dump}")
    print(f"http_events={len(events)} summary={summarize_events(events)}")
    for ev in events[:30]:
        print(
            f"  {ev.wall} {ev.direction} {ev.method or ev.status} "
            f"{ev.path} hint={ev.action_hint or '-'} body={ev.body[:80]!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
