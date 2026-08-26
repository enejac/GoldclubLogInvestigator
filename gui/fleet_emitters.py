"""Qt signals for fleet worker threads (no SQLAlchemy import)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class FleetEmitter(QObject):
    """Emits serialized fleet rows for the UI thread."""

    scan_progress = Signal(int, int)
    finished = Signal(object)
    """``list[dict]`` fleet snapshots, or ``{"__error__": str}``."""


class TimeSyncEmitter(QObject):
    """Remote PsExec/WinRM time sync finished on a worker thread."""

    finished = Signal(str, bool, str, object)
    """``ip``, ``success``, ``message``, ``post_sync_drift_seconds`` (float|None)."""
