"""SQLite persistence for incidents, state timeline segments, and machines."""

from __future__ import annotations

__all__ = ["DatabaseManager", "IncidentQueryFilters", "default_db_path"]


def __getattr__(name: str):
    if name == "DatabaseManager":
        from database.manager import DatabaseManager

        return DatabaseManager
    if name == "IncidentQueryFilters":
        from database.manager import IncidentQueryFilters

        return IncidentQueryFilters
    if name == "default_db_path":
        from database.manager import default_db_path

        return default_db_path
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
