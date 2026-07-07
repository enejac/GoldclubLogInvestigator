"""
Background tail-follow: poll log sizes and feed deltas through incremental parser.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from config import LIVE_WATCH_ACTIVE_FILES, LIVE_WATCH_DISCOVER_SEC, LIVE_WATCH_POLL_MS, LOG_EXTENSIONS
from live_tail_io import read_new_bytes
from parser import Incident, LiveFileParseState, create_live_state_at_eof, feed_live_byte_chunk

logger = logging.getLogger(__name__)


class LiveWatchThread(QThread):
    """
    Polls the N most recently modified ``.log`` / ``.txt`` files under a root.

    Emits batches of new ``Incident`` rows on the GUI thread via Qt signals.
    """

    incidents_batch = Signal(object)
    """``(list[Incident], str | None)`` — incidents and optional active theme hint from tail."""

    status_message = Signal(str)

    def __init__(
        self,
        scan_root: str,
        *,
        active_limit: int = LIVE_WATCH_ACTIVE_FILES,
        poll_ms: int = LIVE_WATCH_POLL_MS,
        discover_sec: float = LIVE_WATCH_DISCOVER_SEC,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(scan_root)
        self._active_limit = max(1, active_limit)
        self._poll_ms = max(200, poll_ms)
        self._discover_sec = max(1.0, discover_sec)
        self._states: dict[str, LiveFileParseState] = {}
        self._eof_align_requested = False

    def set_scan_root(self, scan_root: str) -> None:
        self._root = Path(scan_root)

    def request_align_tracked_files_to_eof(self) -> None:
        """
        After a recording session starts, skip re-parsing bytes already in the file.

        Safe to call from the GUI thread; the worker picks this up on the next loop.
        """
        self._eof_align_requested = True

    def run(self) -> None:
        last_discover = 0.0
        active_paths: list[Path] = []

        while not self.isInterruptionRequested():
            if self._eof_align_requested:
                self._eof_align_requested = False
                for key in list(self._states.keys()):
                    p = Path(key)
                    fresh = create_live_state_at_eof(p)
                    if fresh is not None:
                        self._states[key] = fresh

            now = time.monotonic()
            if not active_paths or (now - last_discover) >= self._discover_sec:
                active_paths = self._discover_active_logs()
                last_discover = now
                self._ensure_states(active_paths)

            batch: list[Incident] = []
            latest_game: str | None = None
            for path in active_paths:
                if self.isInterruptionRequested():
                    break
                key = str(path)
                state = self._states.get(key)
                if state is None:
                    continue
                try:
                    size = path.stat().st_size
                except OSError as e:
                    logger.debug("stat failed %s: %s", path, e)
                    continue

                if size < state.byte_offset:
                    # Truncated / rotated log
                    fresh = create_live_state_at_eof(path)
                    if fresh is not None:
                        self._states[key] = fresh
                    continue

                if size == state.byte_offset:
                    continue

                try:
                    raw = read_new_bytes(path, state.byte_offset, size)
                except OSError as e:
                    logger.debug("tail read failed %s: %s", path, e)
                    self.status_message.emit(f"Live watch read issue: {path.name}: {e}")
                    continue

                new_incs, game_hint = feed_live_byte_chunk(state, raw)
                batch.extend(new_incs)
                if game_hint:
                    latest_game = game_hint

            if batch or latest_game is not None:
                self.incidents_batch.emit((batch, latest_game))

            self.msleep(self._poll_ms)

    def _discover_active_logs(self) -> list[Path]:
        root = self._root
        ext_set = {e.lower() for e in LOG_EXTENSIONS}
        candidates: list[Path] = []
        try:
            if not root.is_dir():
                return []
        except OSError:
            return []

        try:
            for p in root.rglob("*"):
                if self.isInterruptionRequested():
                    break
                try:
                    if p.is_file() and p.suffix.lower() in ext_set:
                        candidates.append(p)
                except OSError:
                    continue
        except OSError as e:
            logger.warning("Live watch discovery failed under %s: %s", root, e)
            return []

        def _mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        candidates.sort(key=_mtime, reverse=True)
        return candidates[: self._active_limit]

    def _ensure_states(self, paths: list[Path]) -> None:
        keys = {str(p) for p in paths}
        # Drop states for files no longer tracked
        for stale in list(self._states.keys()):
            if stale not in keys:
                del self._states[stale]

        for p in paths:
            key = str(p)
            if key in self._states:
                continue
            st = create_live_state_at_eof(p)
            if st is not None:
                self._states[key] = st
