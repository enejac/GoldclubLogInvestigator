"""Background QThread: periodic ICMP + SMB + admin log share probes for all DB machines."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal
from sqlalchemy import select

from config import FLEET_HEARTBEAT_INTERVAL_SEC, FLEET_SNAPSHOT_EXCLUDE_IPV4
from database.manager import DatabaseManager, run_sqlite_write_with_retry
from database.models import MachineORM
from network.fleet_scanner import NetworkScanner, is_ipv4_address

logger = logging.getLogger(__name__)


class FleetHeartbeatWorker(QThread):
    """
    Every ``FLEET_HEARTBEAT_INTERVAL_SEC``, re-probe all IPv4 rows in ``machines`` and
    commit updates on a short-lived engine (WAL + long busy_timeout + lock retries).
    """

    fleet_updated = Signal(object)
    """``list[dict]`` fleet snapshot rows for the UI."""

    heartbeat_failed = Signal(str)

    def __init__(self, manager: DatabaseManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._interval_ms = max(5_000, int(FLEET_HEARTBEAT_INTERVAL_SEC * 1_000))

    def set_interval_ms(self, ms: int) -> None:
        self._interval_ms = max(5_000, ms)

    def run(self) -> None:
        # Stagger slightly behind the UI startup fleet refresh to avoid duplicate full probes.
        self.msleep(5_000)
        while not self.isInterruptionRequested():
            engine = None
            try:
                engine = self._manager.create_engine()
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
                    self.fleet_updated.emit(rows)
                else:
                    scanner = NetworkScanner(max_concurrent_pings=12, max_concurrent_smb=8)
                    probes = scanner.scan(
                        ipv4_ips,
                        progress=None,
                        smb_after_ping=True,
                        probe_c_dollar_log=True,
                    )
                    def _write_and_snapshot() -> list[dict]:
                        with sf() as session:
                            self._manager.apply_fleet_probe_results(
                                session,
                                probes,
                                new_host_requires_log_root=False,
                            )
                            return self._manager.fleet_snapshots_as_dicts(session)

                    rows = run_sqlite_write_with_retry(_write_and_snapshot)
                    self.fleet_updated.emit(rows)
            except Exception as e:  # noqa: BLE001
                logger.exception("Fleet heartbeat cycle failed")
                self.heartbeat_failed.emit(str(e))
            finally:
                if engine is not None:
                    engine.dispose()

            self.msleep(self._interval_ms)
