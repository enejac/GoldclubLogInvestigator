"""Background persistence and DB queries (SQLite via SQLAlchemy)."""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import QRunnable, QThreadPool

from database.manager import (
    DatabaseManager,
    IncidentQueryFilters,
    format_machine_label,
    run_sqlite_write_with_retry,
)
from gui.db_emitters import DbCountEmitter, DbPersistEmitter, DbQueryEmitter
from timeline_engine import EnvFingerprint


class _DbPersistRunnable(QRunnable):
    def __init__(
        self,
        manager: DatabaseManager,
        machine_ip: str,
        machine_name: str | None,
        incidents: Sequence[Any],
        state_nodes: Sequence[Any],
        emitter: DbPersistEmitter,
        logged_machine_id: str | None = None,
        env_fingerprint: EnvFingerprint | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._machine_ip = machine_ip
        self._machine_name = machine_name
        self._incidents = list(incidents)
        self._state_nodes = list(state_nodes)
        self._logged_machine_id = logged_machine_id
        self._env_fingerprint = env_fingerprint
        self._emitter = emitter

    def run(self) -> None:
        engine = self._manager.create_engine()
        try:
            sf = self._manager.session_factory(engine)

            def _persist() -> tuple[int, int]:
                with sf() as session:
                    return self._manager.persist_scan_results(
                        session,
                        machine_ip=self._machine_ip,
                        machine_name=self._machine_name,
                        incidents=self._incidents,
                        state_nodes=self._state_nodes,
                        logged_machine_id=self._logged_machine_id,
                        env_fingerprint=self._env_fingerprint,
                    )

            ni, ns = run_sqlite_write_with_retry(_persist)
            msg = (
                f"Database updated: submitted {ni} incident row(s) and {ns} state-node row(s) "
                f"(duplicates skipped by timestamp + IP + message hash)."
            )
            self._emitter.finished.emit(True, msg)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))
        finally:
            engine.dispose()


def schedule_db_persist(
    pool: QThreadPool,
    manager: DatabaseManager,
    machine_ip: str,
    machine_name: str | None,
    incidents: Sequence[Any],
    state_nodes: Sequence[Any],
    emitter: DbPersistEmitter,
    logged_machine_id: str | None = None,
    env_fingerprint: EnvFingerprint | None = None,
) -> None:
    pool.start(
        _DbPersistRunnable(
            manager,
            machine_ip,
            machine_name,
            incidents,
            state_nodes,
            emitter,
            logged_machine_id=logged_machine_id,
            env_fingerprint=env_fingerprint,
        )
    )


class _DbQueryRunnable(QRunnable):
    def __init__(
        self,
        manager: DatabaseManager,
        filters: IncidentQueryFilters,
        emitter: DbQueryEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._filters = filters
        self._emitter = emitter

    def run(self) -> None:
        engine = self._manager.create_engine()
        try:
            sf = self._manager.session_factory(engine)
            with sf() as session:
                rows = self._manager.query_incidents(session, self._filters)
                out: list[dict[str, Any]] = []
                for r in rows:
                    mach = r.machine
                    mip = mach.ip_address if mach else None
                    mname = mach.name if mach else None
                    out.append(
                        {
                            "timestamp_raw": r.timestamp_raw,
                            "machine_label": format_machine_label(mname, mip),
                            "game": r.game,
                            "severity": r.severity,
                            "error_type": r.error_type,
                            "probable_cause": r.probable_cause,
                            "log_file_path": r.log_file_path,
                            "line_number": r.line_number,
                            "line_snippet": r.line_snippet,
                            "validation_status": r.validation_status,
                            "validation_detail": r.validation_detail,
                        }
                    )
            self._emitter.finished.emit(out)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit({"__error__": str(e)})
        finally:
            engine.dispose()


def schedule_db_query(
    pool: QThreadPool,
    manager: DatabaseManager,
    filters: IncidentQueryFilters,
    emitter: DbQueryEmitter,
) -> None:
    pool.start(_DbQueryRunnable(manager, filters, emitter))


class _DbCountRunnable(QRunnable):
    def __init__(self, manager: DatabaseManager, emitter: DbCountEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._emitter = emitter

    def run(self) -> None:
        engine = self._manager.create_engine()
        try:
            sf = self._manager.session_factory(engine)
            with sf() as session:
                n = self._manager.count_incidents(session)
            self._emitter.finished.emit(n)
        except Exception:  # noqa: BLE001
            self._emitter.finished.emit(-1)
        finally:
            engine.dispose()


def schedule_db_count(
    pool: QThreadPool,
    manager: DatabaseManager,
    emitter: DbCountEmitter,
) -> None:
    pool.start(_DbCountRunnable(manager, emitter))
