"""
Lightweight schema versioning for SQLite (no Alembic dependency).

Bump ``CURRENT_SCHEMA_VERSION`` and append steps to ``MIGRATIONS`` when the ORM
schema changes; existing databases are upgraded in order.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import inspect as sql_inspect
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from database.models import Base

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 5

_META_TABLE = "_gli_schema_meta"


def _get_version(conn: Connection) -> int:
    try:
        row = conn.execute(
            text(f"SELECT v FROM {_META_TABLE} WHERE k = 'schema_version'")
        ).fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except Exception:  # noqa: BLE001 — table missing
        pass
    return 0


def _set_version(conn: Connection, version: int) -> None:
    conn.execute(
        text(
            f"INSERT INTO {_META_TABLE} (k, v) VALUES ('schema_version', :v) "
            f"ON CONFLICT(k) DO UPDATE SET v = excluded.v"
        ),
        {"v": str(version)},
    )


def _ensure_meta_table(conn: Connection) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_META_TABLE} ("
            f" k TEXT PRIMARY KEY NOT NULL, v TEXT NOT NULL)"
        )
    )


def _migration_001_create_core(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def _migration_002_fleet_columns(engine: Engine) -> None:
    """Add fleet / discovery columns to ``machines`` (existing DBs only)."""
    insp = sql_inspect(engine)
    cols = {c["name"] for c in insp.get_columns("machines")}
    alters = [
        ("asset_id", "ALTER TABLE machines ADD COLUMN asset_id VARCHAR(256)"),
        ("last_reachable", "ALTER TABLE machines ADD COLUMN last_reachable INTEGER"),
        ("last_smb_open", "ALTER TABLE machines ADD COLUMN last_smb_open INTEGER"),
        ("last_net_scan_at", "ALTER TABLE machines ADD COLUMN last_net_scan_at DATETIME"),
    ]
    with engine.begin() as conn:
        for name, ddl in alters:
            if name not in cols:
                conn.execute(text(ddl))


def _migration_003_environment_baseline(engine: Engine) -> None:
    """Environment fingerprint + baseline flag on ``machines``."""
    insp = sql_inspect(engine)
    cols = {c["name"] for c in insp.get_columns("machines")}
    alters = [
        ("app_version", "ALTER TABLE machines ADD COLUMN app_version VARCHAR(128)"),
        ("clr_version", "ALTER TABLE machines ADD COLUMN clr_version VARCHAR(256)"),
        ("os_version", "ALTER TABLE machines ADD COLUMN os_version VARCHAR(512)"),
        ("last_known_good_config", "ALTER TABLE machines ADD COLUMN last_known_good_config TEXT"),
    ]
    with engine.begin() as conn:
        for name, ddl in alters:
            if name not in cols:
                conn.execute(text(ddl))


def _migration_004_fleet_enrollment(engine: Engine) -> None:
    """Enrollment label + last admin log share probe on ``machines``."""
    insp = sql_inspect(engine)
    cols = {c["name"] for c in insp.get_columns("machines")}
    alters = [
        ("enrollment_status", "ALTER TABLE machines ADD COLUMN enrollment_status VARCHAR(128)"),
        ("last_admin_log_ok", "ALTER TABLE machines ADD COLUMN last_admin_log_ok INTEGER"),
    ]
    with engine.begin() as conn:
        for name, ddl in alters:
            if name not in cols:
                conn.execute(text(ddl))


def _migration_005_clock_drift(engine: Engine) -> None:
    """Last measured log clock drift (seconds) for Fleet Overview."""
    insp = sql_inspect(engine)
    cols = {c["name"] for c in insp.get_columns("machines")}
    with engine.begin() as conn:
        if "last_clock_drift_seconds" not in cols:
            conn.execute(
                text("ALTER TABLE machines ADD COLUMN last_clock_drift_seconds FLOAT")
            )


MIGRATIONS: list[Callable[[Engine], None]] = [
    _migration_001_create_core,
    _migration_002_fleet_columns,
    _migration_003_environment_baseline,
    _migration_004_fleet_enrollment,
    _migration_005_clock_drift,
]


def apply_migrations(engine: Engine) -> None:
    """Create / upgrade schema; safe to call on every startup."""
    with engine.begin() as conn:
        _ensure_meta_table(conn)
        current = _get_version(conn)

    if current >= CURRENT_SCHEMA_VERSION:
        return

    for target_ver in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
        idx = target_ver - 1
        if 0 <= idx < len(MIGRATIONS):
            logger.info("Applying database migration to version %s", target_ver)
            MIGRATIONS[idx](engine)
        with engine.begin() as conn:
            _ensure_meta_table(conn)
            _set_version(conn, target_ver)

    Base.metadata.create_all(engine)
