"""Background parse of the EGM's logged RAM usage near a CRITICAL incident.

Unlike ``ram_fetch_worker`` (live WMIC snapshot of the remote host), this reads only
log files — the ``OneHand`` memory sample closest to the incident's timestamp — so the
value reflects RAM consumption *at the time of the event*, even for historical scans.
The GUI thread receives the result via a signal and updates the inspector.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class EventRamEmitter(QObject):
    finished = Signal(str, object)
    """``incident_id``, payload dict (or None when no sample was found).

    Payload keys: ``process_mb``, ``free_mb``, ``total_mb``, ``used_mb`` (floats),
    ``sample_ts`` (str | None), ``delta_sec`` (float | None).
    """


class _EventRamRunnable(QRunnable):
    def __init__(
        self,
        incident_id: str,
        log_path: str,
        target_line: str,
        emitter: EventRamEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._iid = incident_id
        self._log_path = log_path
        self._target_line = target_line
        self._emitter = emitter

    def run(self) -> None:
        payload: Any = None
        try:
            from parser import parse_event_ram_sample

            payload = parse_event_ram_sample(self._log_path, self._target_line)
        except Exception:  # noqa: BLE001 — best-effort; report "unavailable" to UI
            payload = None
        self._emitter.finished.emit(self._iid, payload)


def schedule_event_ram_parse(
    pool: QThreadPool,
    incident_id: str,
    log_path: str,
    target_line: str,
    emitter: EventRamEmitter,
) -> None:
    if not incident_id or not log_path:
        return
    pool.start(_EventRamRunnable(incident_id, log_path, target_line, emitter))
