"""SQLAlchemy ORM models for Log Investigator."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MachineORM(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip_address: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reachable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_smb_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_net_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    app_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    clr_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # True = fingerprint matches accepted baseline; False = drift detected; None = not yet baselined.
    last_known_good_config: Mapped[bool | None] = mapped_column(JSON, nullable=True)

    enrollment_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_admin_log_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Investigator UTC minus cabinet log-line UTC (seconds); from last fleet probe.
    last_clock_drift_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    incidents: Mapped[list["IncidentORM"]] = relationship(back_populates="machine")
    state_nodes: Mapped[list["StateNodeORM"]] = relationship(back_populates="machine")


class IncidentORM(Base):
    """
    Mirrors ``parser.Incident`` plus persistence keys.

    ``message`` maps to ``line_snippet``; ``dedup_hash`` = SHA-256 of
    timestamp + machine IP + message for upsert deduplication.
    """

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("machine_id", "dedup_hash", name="uq_incident_machine_dedup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)

    timestamp_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timestamp_sort_key: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    game: Mapped[str] = mapped_column(String(512), default="", index=True)
    severity: Mapped[str] = mapped_column(String(64), default="", index=True)
    error_type: Mapped[str] = mapped_column(String(512), default="")
    probable_cause: Mapped[str] = mapped_column(Text, default="")
    log_file_path: Mapped[str] = mapped_column(Text, default="")
    line_number: Mapped[int] = mapped_column(Integer, default=0)
    line_snippet: Mapped[str] = mapped_column(Text, default="")
    first_cause_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_cause_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    machine: Mapped["MachineORM"] = relationship(back_populates="incidents")


class StateNodeORM(Base):
    """
    One segment in a bonus/base state (from ``timeline_engine.StateNode``).

    ``from_state`` = previous machine state; ``to_state`` = segment state name.
    """

    __tablename__ = "state_nodes"
    __table_args__ = (
        UniqueConstraint(
            "machine_id",
            "log_file_path",
            "segment_start_line",
            name="uq_state_node_machine_file_line",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)

    from_state: Mapped[str | None] = mapped_column(String(512), nullable=True)
    to_state: Mapped[str] = mapped_column(String(512), default="")
    start_time_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    end_time_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_sort_key: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    end_sort_key: Mapped[float] = mapped_column(Float, default=0.0)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_file_path: Mapped[str] = mapped_column(Text, default="")
    segment_start_line: Mapped[int] = mapped_column(Integer, default=0)
    segment_end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health: Mapped[str] = mapped_column(String(32), default="ok")
    bonus_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    incident_count: Mapped[int] = mapped_column(Integer, default=0)
    trigger_event: Mapped[str | None] = mapped_column(Text, nullable=True)

    machine: Mapped["MachineORM"] = relationship(back_populates="state_nodes")
