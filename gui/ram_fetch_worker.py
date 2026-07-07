"""Background WMIC RAM fetch for live incidents (GUI thread receives results via signal)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class RamFetchEmitter(QObject):
    finished = Signal(str, object)
    """
    ``incident_id``, payload dict:

    - ``used_pct`` (float)
    - ``process_mb`` (float | None) — OneHand.exe working set in MB
    - ``total_mb``, ``used_mb`` (float) — system RAM for tooltips
    """

    batch_finished = Signal(object, object)
    """
    ``list[str]`` incident ids, same payload dict — one WMIC round-trip for many rows.
    """


class _RamFetchRunnable(QRunnable):
    def __init__(self, ip: str, incident_id: str, emitter: RamFetchEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._iid = incident_id
        self._emitter = emitter

    def run(self) -> None:
        from network.health_monitor import get_remote_memory_stats

        stats = get_remote_memory_stats(self._ip)
        captured = datetime.now(timezone.utc).isoformat()
        if stats is None:
            return
        try:
            payload: dict[str, Any] = {
                "used_pct": float(stats["used_pct"]),
                "process_mb": stats.get("process_mb"),
                "total_mb": float(stats["total_mb"]),
                "used_mb": float(stats["used_mb"]),
                "capture_ts": captured,
            }
        except (KeyError, TypeError, ValueError):
            return
        pm = payload["process_mb"]
        if pm is not None:
            try:
                payload["process_mb"] = float(pm)
            except (TypeError, ValueError):
                payload["process_mb"] = None
        self._emitter.finished.emit(self._iid, payload)


class _RamFetchBatchRunnable(QRunnable):
    """Single ``get_remote_memory_stats`` call; apply the same snapshot to many incidents."""

    def __init__(self, ip: str, incident_ids: list[str], emitter: RamFetchEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._ids = list(dict.fromkeys(incident_ids))
        self._emitter = emitter

    def run(self) -> None:
        if not self._ids:
            return
        from network.health_monitor import get_remote_memory_stats

        stats = get_remote_memory_stats(self._ip)
        captured = datetime.now(timezone.utc).isoformat()
        if stats is None:
            return
        try:
            payload: dict[str, Any] = {
                "used_pct": float(stats["used_pct"]),
                "process_mb": stats.get("process_mb"),
                "total_mb": float(stats["total_mb"]),
                "used_mb": float(stats["used_mb"]),
                "capture_ts": captured,
            }
        except (KeyError, TypeError, ValueError):
            return
        pm = payload["process_mb"]
        if pm is not None:
            try:
                payload["process_mb"] = float(pm)
            except (TypeError, ValueError):
                payload["process_mb"] = None
        self._emitter.batch_finished.emit(self._ids, payload)


def schedule_ram_fetch(
    pool: QThreadPool,
    ip: str,
    incident_id: str,
    emitter: RamFetchEmitter,
) -> None:
    pool.start(_RamFetchRunnable(ip, incident_id, emitter))


def schedule_ram_fetch_batch(
    pool: QThreadPool,
    ip: str,
    incident_ids: list[str],
    emitter: RamFetchEmitter,
) -> None:
    if not incident_ids:
        return
    pool.start(_RamFetchBatchRunnable(ip, incident_ids, emitter))
