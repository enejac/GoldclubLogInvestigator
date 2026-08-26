"""Qt signals for DB worker threads (no SQLAlchemy import)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class DbPersistEmitter(QObject):
    finished = Signal(bool, str)
    """``success``, human-readable summary or error."""


class DbQueryEmitter(QObject):
    finished = Signal(object)
    """``list[dict]`` of flat incident rows (or empty list on failure)."""


class DbCountEmitter(QObject):
    finished = Signal(int)