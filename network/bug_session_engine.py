"""Live Bug Detector session: multi-folder UNC tail + optional WdSniff capture."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app_paths import bug_detector_output_base

from live_tail_io import read_new_bytes
from network.bug_session_classify import classify_line, classify_sniff_line, source_from_path
from network.bug_session_events import EventCategory, SessionEvent
from network.bug_session_reports import write_session_reports

logger = logging.getLogger(__name__)

# Roulette-first log folders under Goldclub\var\log
ROULETTE_LOG_DIRS = (
    "godot1",
    "godot",
    "ruleta",
    "ruleta Roulette",
    "GoldClub.Aurum.Services",
    "GoldClub.Aurum.Services sasmsgr of SASControler1",
    "GoldClub.Aurum.Services SASControler1",
    "CommCtrl",
    "HWSubsys",
)

SNIFF_PORTS = "8090,30300,30550,30500"
SNIFF_DURATION_MS = 3_600_000  # 1h; stopped early via process kill


@dataclass
class BugSessionResult:
    ok: bool
    cabinet: str
    events: list[SessionEvent] = field(default_factory=list)
    tester_path: str = ""
    developer_path: str = ""
    output_dir: str = ""
    stopped_reason: str = ""
    log: str = ""
    sniff_used: bool = False


class BugSessionEngine:
    """Poll selected UNC logs; optionally start read-only WdSniff on the cabinet."""

    def __init__(
        self,
        cabinet: str,
        *,
        output_dir: Path,
        poll_ms: int = 500,
        enable_sniff: bool = True,
        auto_stop_on_critical: bool = True,
        progress: Callable[[str], None] | None = None,
        on_event: Callable[[SessionEvent], None] | None = None,
    ) -> None:
        self.cabinet = (cabinet or "").strip() or "local"
        self.output_dir = Path(output_dir)
        self.poll_ms = max(200, poll_ms)
        self.enable_sniff = enable_sniff
        self.auto_stop_on_critical = auto_stop_on_critical
        self._progress = progress
        self._on_event = on_event
        self._stop = False
        self._offsets: dict[str, int] = {}
        self.events: list[SessionEvent] = []
        self.stopped_reason = ""
        self._sniff_proc: subprocess.Popen[str] | None = None
        self._sniff_out: Path | None = None
        self._sniff_capture_dir: Path | None = None
        self._sniff_offset = 0
        self.sniff_used = False
        self.started_utc = ""

    def request_stop(self, reason: str = "user_stop") -> None:
        self._stop = True
        if not self.stopped_reason:
            self.stopped_reason = reason

    def _prog(self, msg: str) -> None:
        if self._progress:
            self._progress(msg)

    def _emit(self, ev: SessionEvent) -> None:
        self.events.append(ev)
        if self._on_event:
            self._on_event(ev)
        if ev.critical and self.auto_stop_on_critical:
            self.request_stop("auto_critical")

    def _log_root(self) -> Path | None:
        from network.bug_detector import resolve_log_roots

        roots = resolve_log_roots(self.cabinet)
        return roots[0] if roots else None

    def _discover_files(self, root: Path) -> list[Path]:
        found: list[Path] = []
        for name in ROULETTE_LOG_DIRS:
            d = root / name
            if not d.is_dir():
                continue
            logs = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if logs:
                found.append(logs[0])
        return found

    def _align_eof(self, paths: list[Path]) -> None:
        for p in paths:
            try:
                self._offsets[str(p)] = p.stat().st_size
            except OSError:
                self._offsets[str(p)] = 0

    def _tail_once(self, paths: list[Path]) -> None:
        for p in paths:
            key = str(p)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            start = self._offsets.get(key, size)
            if size < start:
                start = 0
            if size <= start:
                continue
            chunk = read_new_bytes(p, start, size)
            self._offsets[key] = size
            if not chunk:
                continue
            text = chunk.decode("utf-8", errors="replace")
            src = source_from_path(key)
            for line in text.splitlines():
                ev = classify_line(line, source=src, path=key)
                if ev:
                    self._emit(ev)

    def _start_sniff(self) -> None:
        if not self.enable_sniff or self.cabinet in ("", "local"):
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Reuse Stage0 read-only WdSniff (SNIFF|RECV_ONLY) with roulette ports.
        helper = Path(__file__).resolve().parents[1] / "lab" / "Invoke-Stage0SasSniff.ps1"
        if not helper.is_file():
            self._prog("Stage0 sniff helper missing; continuing with log-tail only")
            return
        self._sniff_capture_dir = self.output_dir / "sniff"
        self._sniff_capture_dir.mkdir(parents=True, exist_ok=True)
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-ComputerName",
                self.cabinet,
                "-Mode",
                "steady",
                "-Seconds",
                str(max(60, SNIFF_DURATION_MS // 1000)),
                "-Ports",
                "8090",
                "30300",
                "30550",
                "30500",
                "-OutDir",
                str(self._sniff_capture_dir),
            ]
            self._sniff_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            self.sniff_used = True
            self._prog(f"Started WdSniff ports {SNIFF_PORTS} on {self.cabinet}")
        except OSError as e:
            self._prog(f"Sniff start failed: {e}")

    def _stop_sniff(self) -> None:
        if self._sniff_proc and self._sniff_proc.poll() is None:
            try:
                self._sniff_proc.terminate()
            except OSError:
                pass
            try:
                self._sniff_proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                try:
                    self._sniff_proc.kill()
                except OSError:
                    pass
        # Best-effort: kill remote WdSniff left behind after local terminate
        if self.cabinet not in ("", "local"):
            try:
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            f"$p='C:\\Tools\\PSTools\\PsExec.exe'; "
                            f"if(Test-Path $p){{ & $p \\{self.cabinet} -accepteula "
                            f"-u GOLD-CLUB\\test -p test -s -n 30 "
                            f"cmd /c 'taskkill /IM WdSniff.exe /F' }}"
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                logger.debug("stop remote sniff: %s", e)

    def _pick_sniff_dump(self) -> Path | None:
        cap = getattr(self, "_sniff_capture_dir", None)
        if not cap or not Path(cap).is_dir():
            return None
        dumps = sorted(Path(cap).glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True)
        return dumps[0] if dumps else None

    def _tail_sniff_once(self) -> None:
        dump = self._pick_sniff_dump()
        if dump is None:
            return
        key = str(dump)
        if self._sniff_out is None or str(self._sniff_out) != key:
            self._sniff_out = dump
            self._sniff_offset = 0
        try:
            size = dump.stat().st_size
        except OSError:
            return
        start = self._sniff_offset
        if size <= start:
            return
        chunk = read_new_bytes(dump, start, size)
        self._sniff_offset = size
        if not chunk:
            return
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            ev = classify_sniff_line(line)
            if ev:
                self._emit(ev)

    def run_until_stopped(self) -> BugSessionResult:
        self.started_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        lines_log: list[str] = []
        root = self._log_root()
        if root is None:
            return BugSessionResult(
                ok=False,
                cabinet=self.cabinet,
                log="No log root reachable",
                stopped_reason="error",
            )
        self._prog(f"Session start cabinet={self.cabinet} root={root}")
        paths = self._discover_files(root)
        self._prog(f"Tailing {len(paths)} log file(s)")
        for p in paths:
            lines_log.append(f"tail {p}")
        self._align_eof(paths)
        self._start_sniff()

        while not self._stop:
            self._tail_once(paths)
            self._tail_sniff_once()
            # rediscover occasionally (rotation)
            time.sleep(self.poll_ms / 1000.0)

        self._stop_sniff()
        # final drain
        self._tail_once(paths)
        self._tail_sniff_once()

        reason = self.stopped_reason or "user_stop"
        self._prog(f"Session stop ({reason}), events={len(self.events)}")
        tester, developer = write_session_reports(
            cabinet=self.cabinet,
            events=self.events,
            output_dir=self.output_dir,
            started_utc=self.started_utc,
            stopped_reason=reason,
            sniff_used=self.sniff_used,
        )
        return BugSessionResult(
            ok=True,
            cabinet=self.cabinet,
            events=list(self.events),
            tester_path=str(tester),
            developer_path=str(developer),
            output_dir=str(self.output_dir),
            stopped_reason=reason,
            log="\n".join(lines_log),
            sniff_used=self.sniff_used,
        )


def default_session_dir(cabinet: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = (cabinet or "local").replace(":", "_")
    return bug_detector_output_base() / f"session_{safe}_{stamp}"
