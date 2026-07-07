"""SQLite database manager: bulk upsert, machine registry, filtered queries."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, TypeVar

from sqlalchemy import create_engine, event, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from config import FLEET_SNAPSHOT_EXCLUDE_IPV4
from database.migrate import apply_migrations
from database.models import Base, IncidentORM, MachineORM, StateNodeORM
from network.fleet_scanner import HostProbeResult
from parser import Incident
from timeline_engine import EnvFingerprint, parse_iso_timestamp_sort_key

logger = logging.getLogger(__name__)

_LOGGED_MACHINE_ID_RE = re.compile(r"^gst\d+$", re.IGNORECASE)


def _is_logged_machine_id_style(name: str | None) -> bool:
    return bool(name and _LOGGED_MACHINE_ID_RE.fullmatch(name.strip()))


def format_machine_label(name: str | None, ip_address: str | None) -> str:
    """Display ``id (ip)`` when a name/id is known, else the IP alone."""
    ip = (ip_address or "").strip() or "—"
    n = (name or "").strip()
    if n:
        return f"{n} ({ip})"
    return ip


_APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_FILE = _APP_DIR / "goldclub_investigator.db"


def default_db_path() -> Path:
    return DEFAULT_DB_FILE


def _dedup_hash(timestamp: datetime | str | None, machine_ip: str, message: str) -> str:
    if timestamp is None:
        raw = ""
    elif isinstance(timestamp, datetime):
        raw = timestamp.isoformat()
    else:
        raw = str(timestamp).strip()
    key = f"{raw}|{machine_ip.strip()}|{message.strip()[:4000]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sqlite_connect_wal(dbapi_conn: object, _record: object) -> None:
    """WAL + long busy_timeout so concurrent writers usually wait instead of failing."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=60000")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


def create_engine_for_path(db_path: Path | str, *, echo: bool = False) -> Engine:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path.resolve().as_posix()}"
    eng = create_engine(
        url,
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    event.listen(eng, "connect", _sqlite_connect_wal)
    return eng


_T = TypeVar("_T")


def is_sqlite_database_locked(exc: BaseException) -> bool:
    """True when SQLite reports writer contention (retry or wait may succeed)."""
    if isinstance(exc, OperationalError):
        orig = getattr(exc, "orig", None)
        if isinstance(orig, sqlite3.OperationalError):
            return "locked" in str(orig).lower()
        return "database is locked" in str(exc).lower()
    return False


def run_sqlite_write_with_retry(
    fn: Callable[[], _T],
    *,
    attempts: int = 12,
    base_sleep_sec: float = 0.05,
    max_sleep_sec: float = 1.5,
) -> _T:
    """
    Re-run ``fn`` after SQLite ``database is locked`` errors.

    Each attempt uses a fresh session/transaction (call ``fn`` so it opens its own
    ``with session()`` and commits inside). Writer contention across threads
    exhausts ``busy_timeout`` on a single try when another commit is slow.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except OperationalError as e:
            if not is_sqlite_database_locked(e) or attempt >= attempts - 1:
                raise
            delay = min(base_sleep_sec * (2**attempt), max_sleep_sec)
            time.sleep(delay)
    raise RuntimeError("sqlite retry loop exhausted")


@dataclass(frozen=True, slots=True)
class FleetMachineSnapshot:
    """JSON-friendly row for the Fleet Overview UI."""

    id: int
    ip_address: str
    name: str | None
    asset_id: str | None
    last_reachable: bool | None
    last_smb_open: bool | None
    last_net_scan_at: str | None
    last_scanned_at: str | None
    health: str
    critical_24h: int
    math_fail_24h: int
    warn_24h: int
    app_version: str | None
    clr_version: str | None
    os_version: str | None
    config_warning: bool
    enrollment_status: str | None
    last_admin_log_ok: bool | None
    led_rank: int
    led_mode: str
    led_pulse: bool
    clock_drift_seconds: float | None


@dataclass(frozen=True, slots=True)
class IncidentQueryFilters:
    date_from_sort_key: float | None = None
    date_to_sort_key: float | None = None
    game_substring: str | None = None
    severity: str | None = None
    limit: int = 10_000


class DatabaseManager:
    """Thread-safe usage: prefer a **new** ``Engine`` per background worker, or one engine with ``check_same_thread=False`` and serialized sessions."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_FILE

    @property
    def db_path(self) -> Path:
        return self._db_path

    def create_engine(self, *, echo: bool = False) -> Engine:
        eng = create_engine_for_path(self._db_path, echo=echo)
        apply_migrations(eng)
        return eng

    def session_factory(self, engine: Engine) -> sessionmaker[Session]:
        return sessionmaker(engine, expire_on_commit=False, class_=Session)

    def get_or_create_machine(
        self,
        session: Session,
        ip_address: str,
        name: str | None,
        *,
        logged_machine_id: str | None = None,
    ) -> MachineORM:
        ip = ip_address.strip() or "local"
        lid = logged_machine_id.strip() if logged_machine_id and logged_machine_id.strip() else None
        disp = name.strip() if name and name.strip() else None
        row = session.scalars(select(MachineORM).where(MachineORM.ip_address == ip)).first()
        if row:
            if lid:
                row.name = lid
                row.enrollment_status = None
            elif disp and not _is_logged_machine_id_style(row.name):
                row.name = disp
            row.last_scanned_at = _utc_now()
            session.flush()
            return row
        init_name = lid or disp
        m = MachineORM(
            ip_address=ip,
            name=init_name,
            asset_id=None,
            last_scanned_at=_utc_now(),
            last_reachable=None,
            last_smb_open=None,
            last_net_scan_at=None,
        )
        session.add(m)
        session.flush()
        return m

    def _apply_environment_baseline(
        self,
        session: Session,
        machine: MachineORM,
        fp: EnvFingerprint | None,
    ) -> list[Incident]:
        """
        Update ``machine`` fingerprint columns, set ``last_known_good_config``, and return
        synthetic drift ``Incident`` rows when a stored value differs from the new scan.
        """
        old_a, old_c, old_o = machine.app_version, machine.clr_version, machine.os_version
        new_a = fp.app_version if fp and fp.app_version else None
        new_c = fp.clr_version if fp and fp.clr_version else None
        new_o = fp.os_version if fp and fp.os_version else None
        had_incoming = bool(new_a or new_c or new_o)

        def _changed(before: str | None, after: str | None) -> bool:
            if not before or not after:
                return False
            return before.strip() != after.strip()

        parts: list[str] = []
        severity = "WARN"
        if _changed(old_o, new_o):
            parts.append(f"OS {old_o} -> {new_o}")
            severity = "CRITICAL"
        if _changed(old_c, new_c):
            parts.append(f"CLR {old_c} -> {new_c}")
            severity = "CRITICAL"
        if _changed(old_a, new_a):
            parts.append(f"App {old_a} -> {new_a}")
            if severity != "CRITICAL":
                severity = "WARN"

        drift = bool(parts)
        out: list[Incident] = []
        if drift:
            msg = "Environment changed: " + ", ".join(parts) + "."
            out.append(
                Incident(
                    timestamp=_utc_now(),
                    game="environment",
                    severity=severity,
                    error_type="SYSTEM DRIFT DETECTED",
                    probable_cause=msg,
                    log_file_path="",
                    line_number=0,
                    line_snippet=msg,
                    first_cause_line=None,
                    first_cause_snippet=None,
                    validation_status=None,
                    validation_detail=None,
                )
            )

        if new_a is not None:
            machine.app_version = new_a
        if new_c is not None:
            machine.clr_version = new_c
        if new_o is not None:
            machine.os_version = new_o

        if drift:
            machine.last_known_good_config = False
        elif had_incoming:
            machine.last_known_good_config = True

        session.flush()
        return out

    def persist_scan_results(
        self,
        session: Session,
        *,
        machine_ip: str,
        machine_name: str | None,
        incidents: Sequence[Any],
        state_nodes: Sequence[Any],
        logged_machine_id: str | None = None,
        env_fingerprint: EnvFingerprint | None = None,
    ) -> tuple[int, int]:
        """
        Bulk-insert incidents (dedup on machine + hash) and state nodes (dedup on machine + file + line).

        Returns ``(incident_rows_submitted, state_node_rows_submitted)``.
        """
        machine = self.get_or_create_machine(
            session,
            machine_ip,
            machine_name,
            logged_machine_id=logged_machine_id,
        )
        mid = machine.id
        ip_key = machine.ip_address

        drift_incs = self._apply_environment_baseline(session, machine, env_fingerprint)
        combined_incidents: list[Any] = list(incidents) + drift_incs

        inc_maps: list[dict[str, Any]] = []
        for inc in combined_incidents:
            snippet = getattr(inc, "line_snippet", "") or ""
            ts = getattr(inc, "timestamp", None)
            ts_raw = (
                None
                if ts is None
                else ts.isoformat()
                if isinstance(ts, datetime)
                else str(ts).strip() or None
            )
            inc_maps.append(
                {
                    "machine_id": mid,
                    "timestamp_raw": ts_raw,
                    "timestamp_sort_key": parse_iso_timestamp_sort_key(ts),
                    "game": getattr(inc, "game", "") or "",
                    "severity": getattr(inc, "severity", "") or "",
                    "error_type": getattr(inc, "error_type", "") or "",
                    "probable_cause": getattr(inc, "probable_cause", "") or "",
                    "log_file_path": getattr(inc, "log_file_path", "") or "",
                    "line_number": int(getattr(inc, "line_number", 0) or 0),
                    "line_snippet": snippet,
                    "first_cause_line": getattr(inc, "first_cause_line", None),
                    "first_cause_snippet": getattr(inc, "first_cause_snippet", None),
                    "validation_status": getattr(inc, "validation_status", None),
                    "validation_detail": getattr(inc, "validation_detail", None),
                    "dedup_hash": _dedup_hash(ts, ip_key, snippet),
                }
            )

        node_maps: list[dict[str, Any]] = []
        for n in state_nodes:
            node_maps.append(
                {
                    "machine_id": mid,
                    "from_state": getattr(n, "previous_state", None),
                    "to_state": getattr(n, "state_name", "") or "",
                    "start_time_raw": getattr(n, "timestamp", None),
                    "end_time_raw": getattr(n, "end_timestamp", None),
                    "start_sort_key": float(getattr(n, "timestamp_sort_key", 0.0) or 0.0),
                    "end_sort_key": float(getattr(n, "end_timestamp_sort_key", 0.0) or 0.0),
                    "duration_sec": getattr(n, "duration_sec", None),
                    "log_file_path": getattr(n, "log_file_path", "") or "",
                    "segment_start_line": int(getattr(n, "line_number", 0) or 0),
                    "segment_end_line": getattr(n, "end_line_number", None),
                    "health": getattr(n, "health", "ok") or "ok",
                    "bonus_label": getattr(n, "bonus_label", None),
                    "incident_count": int(getattr(n, "incident_count", 0) or 0),
                    "trigger_event": getattr(n, "trigger_event", None),
                }
            )

        if inc_maps:
            stmt = sqlite_insert(IncidentORM).values(inc_maps)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["machine_id", "dedup_hash"],
            )
            session.execute(stmt)

        if node_maps:
            stmt_n = sqlite_insert(StateNodeORM).values(node_maps)
            stmt_n = stmt_n.on_conflict_do_nothing(
                index_elements=["machine_id", "log_file_path", "segment_start_line"],
            )
            session.execute(stmt_n)

        session.commit()
        return len(inc_maps), len(node_maps)

    def query_incidents(
        self,
        session: Session,
        filters: IncidentQueryFilters,
    ) -> list[IncidentORM]:
        q = select(IncidentORM).options(selectinload(IncidentORM.machine))
        if filters.date_from_sort_key is not None:
            q = q.where(IncidentORM.timestamp_sort_key >= filters.date_from_sort_key)
        if filters.date_to_sort_key is not None:
            q = q.where(IncidentORM.timestamp_sort_key <= filters.date_to_sort_key)
        if filters.game_substring and filters.game_substring.strip():
            pat = f"%{filters.game_substring.strip()}%"
            q = q.join(IncidentORM.machine).where(
                or_(
                    IncidentORM.game.ilike(pat),
                    MachineORM.name.ilike(pat),
                    MachineORM.ip_address.ilike(pat),
                    MachineORM.asset_id.ilike(pat),
                )
            )
        if filters.severity and filters.severity.strip():
            q = q.where(IncidentORM.severity == filters.severity.strip())
        q = q.order_by(IncidentORM.timestamp_sort_key.desc().nulls_last(), IncidentORM.id.desc())
        q = q.limit(max(1, min(filters.limit, 50_000)))
        return list(session.scalars(q).all())

    def count_incidents(self, session: Session) -> int:
        return int(session.scalar(select(func.count()).select_from(IncidentORM)) or 0)

    def get_machine_by_ip(self, session: Session, ip_address: str) -> MachineORM | None:
        ip = (ip_address or "").strip()
        if not ip:
            return None
        return session.scalars(select(MachineORM).where(MachineORM.ip_address == ip)).first()

    def delete_machine_by_ip(self, ip_address: str) -> bool:
        """Remove a fleet row by IP (cascades incidents/state nodes). Returns True if deleted."""
        ip = (ip_address or "").strip()
        if not ip:
            return False
        engine = self.create_engine()
        try:
            sf = self.session_factory(engine)
            with sf() as session:
                row = self.get_machine_by_ip(session, ip)
                if row is None:
                    return False
                session.delete(row)
                session.commit()
            return True
        finally:
            engine.dispose()

    def apply_fleet_probe_results(
        self,
        session: Session,
        results: Sequence[HostProbeResult],
        *,
        new_host_requires_log_root: bool = False,
    ) -> None:
        """
        Insert or update machines from a discovery / refresh / heartbeat sweep.

        When ``new_host_requires_log_root`` is True (subnet discovery), a **new** row is
        created only if the host responds to ping **and** ``c$\\Goldclub\\var\\log`` is reachable.
        """
        now = _utc_now()
        new_label = "New / Unscanned"
        for r in results:
            row = session.scalars(select(MachineORM).where(MachineORM.ip_address == r.ip)).first()
            if row:
                row.last_reachable = r.ping_ok
                row.last_smb_open = r.smb_open if r.ping_ok else False
                row.last_admin_log_ok = r.c_dollar_log_ok if r.ping_ok else False
                row.last_net_scan_at = now
                if r.ping_ok and r.c_dollar_log_ok:
                    row.last_clock_drift_seconds = r.clock_drift_seconds
                else:
                    row.last_clock_drift_seconds = None
                if r.ping_ok:
                    row.last_scanned_at = now
            else:
                if not r.ping_ok:
                    continue
                if new_host_requires_log_root and not r.c_dollar_log_ok:
                    continue
                session.add(
                    MachineORM(
                        ip_address=r.ip,
                        name=None,
                        asset_id=None,
                        enrollment_status=new_label if new_host_requires_log_root else None,
                        last_scanned_at=now,
                        last_reachable=r.ping_ok,
                        last_smb_open=r.smb_open if r.ping_ok else False,
                        last_admin_log_ok=r.c_dollar_log_ok,
                        last_net_scan_at=now,
                        last_clock_drift_seconds=(
                            r.clock_drift_seconds
                            if r.ping_ok and r.c_dollar_log_ok
                            else None
                        ),
                    )
                )
        session.commit()

    def _iso(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.isoformat()

    def _incident_buckets_24h(self, session: Session, machine_id: int) -> tuple[int, int, int]:
        """
        Returns ``(critical_non_math, math_fail, warn_count)`` for the last 24 hours.

        *critical_non_math*: ``CRITICAL`` severity excluding math discrepancy rows.
        *math_fail*: ``CRITICAL MATH DISCREPANCY`` or ``validation_status == FAIL``.
        *warn_count*: ``MEDIUM`` + ``LOW``.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - 86400.0
        q = select(IncidentORM).where(
            IncidentORM.machine_id == machine_id,
            IncidentORM.timestamp_sort_key.is_not(None),
            IncidentORM.timestamp_sort_key >= cutoff,
        )
        rows = session.scalars(q).all()
        crit_nm = math_f = warn = 0
        for inc in rows:
            et = inc.error_type or ""
            sev = inc.severity or ""
            vs = inc.validation_status or ""
            is_math = et == "CRITICAL MATH DISCREPANCY" or vs == "FAIL"
            if is_math:
                math_f += 1
            elif sev == "CRITICAL":
                crit_nm += 1
            elif sev in ("MEDIUM", "LOW"):
                warn += 1
        return crit_nm, math_f, warn

    @staticmethod
    def compute_fleet_health(
        reachable: bool | None,
        critical_non_math: int,
        math_fail: int,
        warn: int,
    ) -> str:
        if reachable is not True:
            return "grey"
        if critical_non_math > 0:
            return "red"
        if math_fail > 0 or warn > 0:
            return "yellow"
        return "green"

    def build_fleet_snapshots(self, session: Session) -> list[FleetMachineSnapshot]:
        machines = session.scalars(
            select(MachineORM).order_by(MachineORM.ip_address.asc())
        ).all()
        out: list[FleetMachineSnapshot] = []
        for m in machines:
            if (m.ip_address or "").strip() in FLEET_SNAPSHOT_EXCLUDE_IPV4:
                continue
            c, mf, w = self._incident_buckets_24h(session, m.id)
            health = self.compute_fleet_health(m.last_reachable, c, mf, w)
            cfg_warn = m.last_known_good_config is False
            reachable = m.last_reachable is True
            if not reachable:
                led_mode = "grey"
                led_rank = 3
                led_pulse = False
            elif c > 0:
                led_mode = "red"
                led_rank = 0
                led_pulse = False
            elif cfg_warn or mf > 0 or w > 0:
                led_mode = "orange"
                led_rank = 1
                led_pulse = False
            else:
                led_mode = "green"
                led_rank = 2
                led_pulse = True
            out.append(
                FleetMachineSnapshot(
                    id=m.id,
                    ip_address=m.ip_address,
                    name=m.name,
                    asset_id=m.asset_id,
                    last_reachable=m.last_reachable,
                    last_smb_open=m.last_smb_open,
                    last_net_scan_at=self._iso(m.last_net_scan_at),
                    last_scanned_at=self._iso(m.last_scanned_at),
                    health=health,
                    critical_24h=c,
                    math_fail_24h=mf,
                    warn_24h=w,
                    app_version=m.app_version,
                    clr_version=m.clr_version,
                    os_version=m.os_version,
                    config_warning=cfg_warn,
                    enrollment_status=m.enrollment_status,
                    last_admin_log_ok=m.last_admin_log_ok,
                    led_rank=led_rank,
                    led_mode=led_mode,
                    led_pulse=led_pulse,
                    clock_drift_seconds=m.last_clock_drift_seconds,
                )
            )
        return out

    def fleet_snapshots_as_dicts(self, session: Session) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "ip_address": s.ip_address,
                "name": s.name,
                "asset_id": s.asset_id,
                "last_reachable": s.last_reachable,
                "last_smb_open": s.last_smb_open,
                "last_net_scan_at": s.last_net_scan_at,
                "last_scanned_at": s.last_scanned_at,
                "health": s.health,
                "critical_24h": s.critical_24h,
                "math_fail_24h": s.math_fail_24h,
                "warn_24h": s.warn_24h,
                "app_version": s.app_version,
                "clr_version": s.clr_version,
                "os_version": s.os_version,
                "config_warning": s.config_warning,
                "enrollment_status": s.enrollment_status,
                "last_admin_log_ok": s.last_admin_log_ok,
                "led_rank": s.led_rank,
                "led_mode": s.led_mode,
                "led_pulse": s.led_pulse,
                "clock_drift_seconds": s.clock_drift_seconds,
            }
            for s in self.build_fleet_snapshots(session)
        ]

    def machine_registry_dict(self, session: Session, ip_address: str) -> dict[str, Any] | None:
        m = self.get_machine_by_ip(session, ip_address)
        if not m:
            return None
        return {
            "ip_address": m.ip_address,
            "machine_name": m.name,
            "asset_id": m.asset_id,
            "app_version": m.app_version,
            "clr_version": m.clr_version,
            "os_version": m.os_version,
            "last_known_good_config": m.last_known_good_config,
        }
