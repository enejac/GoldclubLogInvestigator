"""Background fleet discovery and reachability refresh (ping + SMB 445)."""

from __future__ import annotations

import logging
import threading
from PySide6.QtCore import QRunnable, QThreadPool

from config import FLEET_SNAPSHOT_EXCLUDE_IPV4, format_unc_log_root
from diag_logging import fleet_timesync_logger
from database.manager import DatabaseManager, run_sqlite_write_with_retry
from gui.fleet_emitters import FleetEmitter, TimeSyncEmitter
from network.fleet_scanner import (
    NetworkScanner,
    check_clock_drift,
    is_ipv4_address,
    parse_ip_targets,
)

logger = logging.getLogger(__name__)


class _FleetSubnetScanRunnable(QRunnable):
    def __init__(
        self,
        manager: DatabaseManager,
        cidr_or_range: str,
        max_workers: int,
        emitter: FleetEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._spec = cidr_or_range.strip()
        self._max_workers = max_workers
        self._emitter = emitter

    def run(self) -> None:
        engine = self._manager.create_engine()
        try:
            ips = parse_ip_targets(self._spec)
        except ValueError as e:
            self._emitter.finished.emit({"__error__": str(e)})
            engine.dispose()
            return

        def prog(done: int, total: int) -> None:
            self._emitter.scan_progress.emit(done, total)

        scanner = NetworkScanner(max_concurrent_pings=self._max_workers)
        try:
            probes = scanner.scan(
                ips,
                progress=prog,
                smb_after_ping=True,
                probe_c_dollar_log=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Fleet subnet scan failed")
            self._emitter.finished.emit({"__error__": str(e)})
            engine.dispose()
            return

        try:
            sf = self._manager.session_factory(engine)

            def _write_subnet() -> list[dict]:
                with sf() as session:
                    self._manager.apply_fleet_probe_results(
                        session,
                        probes,
                        new_host_requires_log_root=True,
                    )
                    return self._manager.fleet_snapshots_as_dicts(session)

            rows = run_sqlite_write_with_retry(_write_subnet)
            self._emitter.finished.emit(rows)
        except Exception as e:  # noqa: BLE001
            logger.exception("Fleet DB update failed")
            self._emitter.finished.emit({"__error__": str(e)})
        finally:
            engine.dispose()


class _FleetRefreshRunnable(QRunnable):
    """Re-ping every IPv4 machine row in the database (startup / manual refresh)."""

    def __init__(
        self,
        manager: DatabaseManager,
        max_workers: int,
        emitter: FleetEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._max_workers = max_workers
        self._emitter = emitter

    def run(self) -> None:
        engine = self._manager.create_engine()
        try:
            from sqlalchemy import select

            from database.models import MachineORM

            sf = self._manager.session_factory(engine)
            with sf() as session:
                machines = session.scalars(select(MachineORM)).all()
                ipv4_ips = [
                    m.ip_address
                    for m in machines
                    if is_ipv4_address(m.ip_address)
                    and (m.ip_address or "").strip() not in FLEET_SNAPSHOT_EXCLUDE_IPV4
                ]

            if not ipv4_ips:
                with sf() as session:
                    rows = self._manager.fleet_snapshots_as_dicts(session)
                self._emitter.finished.emit(rows)
                return

            def prog(done: int, total: int) -> None:
                self._emitter.scan_progress.emit(done, total)

            scanner = NetworkScanner(max_concurrent_pings=self._max_workers)
            probes = scanner.scan(
                ipv4_ips,
                progress=prog,
                smb_after_ping=True,
                probe_c_dollar_log=True,
            )

            sf = self._manager.session_factory(engine)

            def _write_refresh() -> list[dict]:
                with sf() as session:
                    self._manager.apply_fleet_probe_results(
                        session,
                        probes,
                        new_host_requires_log_root=False,
                    )
                    return self._manager.fleet_snapshots_as_dicts(session)

            rows = run_sqlite_write_with_retry(_write_refresh)
            self._emitter.finished.emit(rows)
        except Exception as e:  # noqa: BLE001
            logger.exception("Fleet refresh failed")
            self._emitter.finished.emit({"__error__": str(e)})
        finally:
            engine.dispose()


class _SingleHostDriftRefreshRunnable(QRunnable):
    """Update one machine's clock drift in the DB and refresh fleet dicts.

    When ``drift_override`` is set (post Sync Time), write that wall-clock drift
    instead of re-measuring from log line timestamps (those stay skewed until
    new logs are written).
    """

    def __init__(
        self,
        manager: DatabaseManager,
        ip_address: str,
        emitter: FleetEmitter,
        *,
        drift_override: float | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._manager = manager
        self._ip = (ip_address or "").strip()
        self._emitter = emitter
        self._drift_override = drift_override

    def run(self) -> None:
        if not self._ip:
            fleet_timesync_logger().warning("drift worker: empty IP, not running")
            self._emitter.finished.emit({"__error__": "Empty IP for drift refresh."})
            return
        fleet_timesync_logger().info(
            "drift worker: begin ip=%s thread=%s override=%r",
            self._ip,
            threading.current_thread().name,
            self._drift_override,
        )
        engine = self._manager.create_engine()
        try:
            if self._drift_override is not None:
                drift = float(self._drift_override)
                fleet_timesync_logger().info(
                    "drift worker: using post-sync wall-clock drift ip=%s drift_seconds=%r",
                    self._ip,
                    drift,
                )
            else:
                try:
                    unc = format_unc_log_root(self._ip)
                except ValueError as e:
                    fleet_timesync_logger().warning(
                        "drift worker: bad UNC for ip=%s: %s", self._ip, e
                    )
                    self._emitter.finished.emit({"__error__": str(e)})
                    return
                fleet_timesync_logger().debug(
                    "drift worker: calling check_clock_drift unc=%s", unc
                )
                drift = check_clock_drift(unc)
                fleet_timesync_logger().info(
                    "drift worker: check_clock_drift done ip=%s drift_seconds=%r",
                    self._ip,
                    drift,
                )

            def _write() -> list[dict]:
                sf = self._manager.session_factory(engine)
                with sf() as session:
                    row = self._manager.get_machine_by_ip(session, self._ip)
                    if row is not None:
                        row.last_clock_drift_seconds = drift
                    session.commit()
                    return self._manager.fleet_snapshots_as_dicts(session)

            rows = run_sqlite_write_with_retry(_write)
            fleet_timesync_logger().info(
                "drift worker: DB updated, emitting %s fleet row(s) ip=%s",
                len(rows),
                self._ip,
            )
            self._emitter.finished.emit(rows)
            fleet_timesync_logger().info("drift worker: finished.emit returned ip=%s", self._ip)
        except Exception as e:  # noqa: BLE001
            logger.exception("Single-host drift refresh failed for %s", self._ip)
            fleet_timesync_logger().exception(
                "drift worker: exception ip=%s err=%s", self._ip, e
            )
            self._emitter.finished.emit({"__error__": str(e)})
        finally:
            engine.dispose()
            fleet_timesync_logger().debug("drift worker: engine disposed ip=%s", self._ip)


class _RemoteTimeSyncRunnable(QRunnable):
    """Runs ``force_remote_time_sync`` off the GUI thread (WinRM/PsExec can block)."""

    def __init__(self, ip: str, emitter: TimeSyncEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = (ip or "").strip()
        self._emitter = emitter

    def run(self) -> None:
        from network.time_sync import force_remote_time_sync

        fleet_timesync_logger().info(
            "time_sync worker: begin ip=%s thread=%s",
            self._ip,
            threading.current_thread().name,
        )
        try:
            ok, msg, drift = force_remote_time_sync(self._ip)
        except Exception as e:  # noqa: BLE001
            fleet_timesync_logger().exception(
                "time_sync worker: unexpected error ip=%s", self._ip
            )
            ok, msg, drift = False, str(e), None
        fleet_timesync_logger().info(
            "time_sync worker: end ip=%s ok=%s drift=%r", self._ip, ok, drift
        )
        self._emitter.finished.emit(self._ip, ok, msg, drift)


def schedule_remote_time_sync(
    pool: QThreadPool,
    ip_address: str,
    *,
    emitter: TimeSyncEmitter,
) -> None:
    ip = (ip_address or "").strip()
    fleet_timesync_logger().debug("schedule_remote_time_sync: queue ip=%s", ip)
    pool.start(_RemoteTimeSyncRunnable(ip, emitter))


def schedule_single_host_drift_refresh(
    pool: QThreadPool,
    manager: DatabaseManager,
    ip_address: str,
    *,
    emitter: FleetEmitter,
    drift_override: float | None = None,
) -> None:
    fleet_timesync_logger().debug(
        "schedule_single_host_drift_refresh: pool active=%s max=%s ip=%s override=%r",
        pool.activeThreadCount(),
        pool.maxThreadCount(),
        (ip_address or "").strip(),
        drift_override,
    )
    pool.start(
        _SingleHostDriftRefreshRunnable(
            manager,
            ip_address,
            emitter,
            drift_override=drift_override,
        )
    )

def schedule_fleet_subnet_scan(
    pool: QThreadPool,
    manager: DatabaseManager,
    cidr_or_range: str,
    *,
    max_workers: int = 12,
    emitter: FleetEmitter,
) -> None:
    pool.start(_FleetSubnetScanRunnable(manager, cidr_or_range, max_workers, emitter))


def schedule_fleet_refresh(
    pool: QThreadPool,
    manager: DatabaseManager,
    *,
    max_workers: int = 12,
    emitter: FleetEmitter,
) -> None:
    pool.start(_FleetRefreshRunnable(manager, max_workers, emitter))
