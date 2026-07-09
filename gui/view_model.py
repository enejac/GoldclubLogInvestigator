"""
ViewModel: scan statistics, incident store, filter indices, selection.

The table model reads ``DisplayRow`` entries built from ``_filtered`` indices
into ``all_incidents`` for a virtual, memory-efficient row surface.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import glob
import json
import logging
import os
import uuid
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QThreadPool, QTimer, QStandardPaths, Signal

from product_version import (
    DEFAULT_PRODUCT_NAME,
    PRODUCT_VERSION_PLACEHOLDER,
    format_product_build_version,
    parse_product_core_from_build_string,
)
from gui.filter_runnable import (
    FilterIndexEmitter,
    QuickFilterSnapshot,
    _brush_time_slice_match,
    _incident_blob,
    _session_match,
    _timeline_match,
    quick_filter_match,
    schedule_filter_build,
)
from gui.event_ram_worker import (
    EventRamEmitter,
    schedule_event_ram_parse,
)
from gui.ram_fetch_worker import (
    RamFetchEmitter,
    schedule_ram_fetch_batch,
)
from parser import (
    Incident,
    _ONEHAND_SLOT_VER_RE,
    extract_env_fingerprint_from_logtree,
)
from timeline_engine import EnvFingerprint, ParseResult
from network.meter_comparator import SAS_TO_XML
from network.sas_parser import last_rx_code_values_from_text


_SAS_ID_BYTE_TO_CODE: dict[str, str] = {
    # Common shorthand mapping (byte id -> 16-bit SAS meter code used elsewhere)
    "00": "0000",  # Total Coin In
    "01": "0100",  # Total Coin Out
    "02": "0200",  # Total Jackpot
    "03": "0300",  # HandPaid Cancelled
    "04": "0400",  # Cancelled Credits
}

# IGT SAS tester ($2F — "Send selected meters for game N"): 2-digit hex entered in the
# meter-number dialog. Maps 6F verify-table code -> IGT index.
# Lab-verified: enter 22 -> returns meter 0200 (display 0002 / Total Jackpot).
# Cumulative meters 0000..000F (except 000B): (n << 4) | 0x02  →  02, 12, 22, …, F2.
# 000B bills-in: B2.  0010+ and 006E: enter the low byte (15, 17, 6E, …).
SAS_6F_TO_IGT_2F_INDEX: dict[str, str] = {
    "0000": "02",  # Total Coin In
    "0001": "12",  # Total Coin Out
    "0002": "22",  # Total Jackpot (verified on lab IGT tester)
    "0003": "32",  # Hand Paid Cancelled
    "0004": "42",  # Cancelled Credits
    "0005": "52",  # Games Played
    "0006": "62",  # Games Won
    "0007": "72",  # Games Lost
    "000B": "B2",  # Bills In
    "0015": "15",  # Ticket In
    "0016": "16",  # Ticket Out
    "0017": "17",  # Transfer To EGM
    "0018": "18",  # Transfer To Host
    "001C": "1C",  # Machine Paid Paytable Win
    "001D": "1D",  # Machine Paid Progressive Win
    "001F": "1F",  # Attendant Paid Paytable Win
    "0020": "20",  # Attendant Paid Progressive Win
    "0023": "23",  # Hand Paid
    "006E": "6E",  # Bills Dispensed
    "0080": "80",  # Reg Cashable Ticket In
    "0082": "82",  # Restricted Ticket In
    "0084": "84",  # NonRestricted Ticket In
    "0086": "86",  # Reg Cashable Ticket Out
    "0088": "88",  # Restricted Ticket Out
    "00A0": "A0",  # Reg Cashable C-Less In
    "00A2": "A2",  # Restricted C-Less In
    "00A4": "A4",  # NonRestricted C-Less In
    "00B8": "B8",  # Reg Cashable C-Less Out
    "00BA": "BA",  # Restricted C-Less Out
    "00BC": "BC",  # NonRestricted C-Less Out
}

SAS_IGT_2F_POLL_CMD = 0x2F

IGT_2F_INDEX_TO_6F_CODE: dict[str, str] = {
    idx.upper(): code for code, idx in SAS_6F_TO_IGT_2F_INDEX.items()
}


def sas_6f_code_for_igt_2f_index(index: str) -> str:
    """Map IGT tester $2F dialog hex index -> 6F verify-table code (``0005``, …)."""
    return IGT_2F_INDEX_TO_6F_CODE.get((index or "").strip().upper(), "")


def base_sas_6f_meter_code(text: str) -> str:
    """Strip IGT hint / wire suffix from a Meter Code cell."""
    t = (text or "").strip().upper()
    if not t:
        return ""
    if " ($2F" in t:
        t = t.split(" ($2F", 1)[0].strip()
    if " · " in t:
        t = t.split(" · ", 1)[0].strip()
    return t


def sas_6f_wire_code(display: str) -> str:
    """6F verify code (``0002``) -> SAS wire / IGT id (``0200``). Same meter, byte-swapped."""
    c = base_sas_6f_meter_code(display)
    if len(c) != 4 or not re.fullmatch(r"[0-9A-F]{4}", c):
        return ""
    return (c[2:4] + c[0:2]).upper()


def igt_2f_meter_index_for_6f_code(display: str) -> str:
    """IGT tester hex index for poll ``$2F`` (empty if unknown)."""
    c = base_sas_6f_meter_code(display)
    return SAS_6F_TO_IGT_2F_INDEX.get(c, "")


def format_sas_6f_meter_code_display(code: str) -> str:
    """
    Legacy single-cell label (TSV / tooltips). Prefer separate table columns in verify UI.

    Example: ``0002`` -> ``0002 · 0200 ($2F ; 22)``.
    IGT tester decodes the same meter as ``00000200`` (padded wire id ``0200``).
    """
    c = base_sas_6f_meter_code(code)
    if len(c) != 4 or not re.fullmatch(r"[0-9A-F]{4}", c):
        return c or (code or "")
    meter_ix = igt_2f_meter_index_for_6f_code(c)
    wire = sas_6f_wire_code(c)
    if not meter_ix:
        return c
    wire_part = f" · {wire}" if wire else ""
    return f"{c}{wire_part} (${SAS_IGT_2F_POLL_CMD:02X} ; {meter_ix})"


def igt_meter_display_id(code: str) -> str:
    """IGT tester label (``First Meter = 00000200``) — wire id zero-padded to 8 hex digits."""
    wire = sas_6f_wire_code(code)
    if not wire:
        return ""
    return wire.zfill(8)


def sas_6f_meter_column_values(code: str) -> tuple[str, str, str, str]:
    """
    Human-readable verify-table columns for one 6F meter code.

    Returns ``(6f_code, wire_id, igt_poll_index, igt_meter_label)``.
    """
    c = base_sas_6f_meter_code(code)
    if len(c) != 4 or not re.fullmatch(r"[0-9A-F]{4}", c):
        return c or (code or ""), "", "", ""
    return (
        c,
        sas_6f_wire_code(c),
        igt_2f_meter_index_for_6f_code(c),
        igt_meter_display_id(c),
    )


SAS_6F_METER_ALIASES: dict[str, list[str]] = {
    "0000": [
        "TotalCoinInCredits (Bet)",
        "TotalCoinIn",
        "TotalIn",
        "vCoinIn",
        "Meter_0",
        "coinIn",
        "coinin",
        "cabinet_cashablein",
        "cabinet_cashableinamt",
        "GameCoinIn",
        "gamecoinin",
        "totalcoinin",
        "Total Coin In",
    ],
    "0001": [
        "TotalCoinOutCredits (Win)",
        "TotalOut",
        "coinOut",
        "coinout",
        "cabinet_cashableout",
        "cabinet_cashableoutamt",
        "GameCoinOut",
        "gamecoinout",
        "totalcoinout",
        "BG_GameCoinOut",
        "bg_gamecoinout",
        "Total Coin Out",
    ],
    "0002": ["TotalJackpot", "Drop", "jackpot", "progressivecoinout", "totaljackpot"],
    "0003": ["TotalHandPaidCancelled", "totalhandpaidcancelled", "Handpay", "handpay", "TotalHandpay", "totalhandpay", "attendantpaid"],
    # NOTE: some Goldclub versions report handpaid/cancelled under the same XML meter name.
    "0004": ["TotalCancelledCredits", "cancelledcredits", "totalcancelledcredits", "handpay"],
    "0005": ["TotalGamesPlayed", "GamesPlayed", "vGamesPlayed", "Meter_5", "gamesPlayed", "GameBasePlays", "Games Played"],
    "0006": ["GamesWon", "Games Won"],
    # Not always stored directly; often computed as Played - Won.
    "0007": ["TotalGamesLost", "totalgameslost", "gameslost"],
    # 000B: bills-in. Prefer composite keys from gm2au DeviceClass="meters"/"cabinet" buckets.
    "000B": [
        # Real cabinet key (verified on lab cabinet): note acceptor stacker total (credits).
        "notesInStackerAmt",
        "TotalCreditsFromBills",
        "meters_billinamt",
        "meters_totalbillsin",
        "cabinet_billinamt",
        "cabinet_totalbillsin",
        # Legacy fallbacks (non-composite)
        "billinamt",
        "totalbillsin",
        "totalbills",
        "billin",
    ],
    # Ticket meters: use deviceClass+meterName composite keys to avoid collisions with balances.
    # Keep lists intentionally tight so we never bind these rows to player credit buckets.
    "0015": [
        "TotalTicketIn",
        "voucher_cashableinamt",
        "ticketinamt",
    ],
    "0016": [
        "TotalTicketOut",
        "voucher_cashableoutamt",
        "vouchercashout",
    ],
    # 0017: transfer-in. Prefer deviceClass+meterName composite keys (WAT) to avoid collisions.
    # Keep list tight; never use promo or player balance buckets here.
    "0017": [
        "TotalTransferToEGM",
        "wat_cashableinamt",
        "regularcashabletransferin",
        "aftcashablein",
        "cashabletransferin",
        "wat_in_cashable",
        "watin_cashable",
        "transferincashable",
        "watincashable",
    ],
    # 0018: transfer-out. Prefer deviceClass+meterName composite keys (WAT) to avoid collisions.
    "0018": [
        "TotalTransferToHost",
        "wat_cashableoutamt",
        "transferout",
        "wat_out",
    ],
    # NOTE: 001C is Total Machine Paid Paytable Win (not Current Credits).
    "001C": ["TotalMachinePaidPaytableWin", "totalmachinepaidpaytablewin", "machinepaidpaytablewin", "paytablewin"],
    "001D": ["TotalMachinePaidProg.Win", "Prog Win"],
    "001F": ["TotalAttendantPaidPayTableWin"],
    "0020": ["TotalAttendantPaidProg.Win"],
    "0023": [
        "TotalHandPaid",
        "handpay_keyedoffcashableoutamt",
        "cancelledcredits",
    ],
    "006E": ["Total Bills Dispensed", "totalbillsdispensed", "billsdispensed", "hopperout", "TotalBillsDispensed"],
    # Ticket / voucher transfer buckets (0x80..0x88). Real cabinet voucher bucket keys
    # (verified on lab cabinet 10.0.0.90):
    #   voucher_cashableInAmt / voucher_nonCashInAmt / voucher_promoInAmt (in)
    #   voucher_cashableOutAmt / voucher_nonCashOutAmt (out)
    "0080": [
        "Reg Cashable Ticket In",
        "voucher_cashableInAmt",
        "ticketinamt",
        "regularcashableticketin",
        "RegularCashableTicketIn",
    ],
    "0082": [
        "Restricted Ticket In",
        "voucher_nonCashInAmt",
        "RestrictedTicketIn",
    ],
    "0084": [
        "NonRestricted Ticket In",
        "voucher_promoInAmt",
        "NonrestrictedTicketIn",
    ],
    "0086": [
        "Reg Cashable Ticket Out",
        "voucher_cashableOutAmt",
        "vouchercashout",
        "regularcashableticketout",
        "RegularCashableTicketOut",
    ],
    "0088": [
        "Restricted Ticket Out",
        "voucher_nonCashOutAmt",
        "RestrictedTicketOut",
    ],
    # Cashless / WAT transfer buckets (0xA0..0xBC). These are TRANSFER meters, NOT ticket
    # meters — keep them distinct so a cashless row never binds to a ticket bucket.
    # Real cabinet WAT bucket keys (verified on lab cabinet 10.0.0.90):
    #   wat_cashableInAmt / wat_nonCashInAmt / wat_promoInAmt (in)
    #   wat_cashableOutAmt / wat_nonCashOutAmt / wat_promoOutAmt (out)
    # Mapping confirmed against expected SAS values:
    #   Reg Cashable -> wat_cashable*  | Restricted -> wat_nonCash* | NonRestricted -> wat_promo*
    "00A0": [
        "Reg Cashable C-Less In",
        "wat_cashableInAmt",
        "wat_in_cashable",
        "regularcashabletransferin",
        "aftcashablein",
    ],
    "00A2": [
        "Restricted C-Less In",
        "wat_nonCashInAmt",
        "wat_in_restricted",
        "restrictedtransferin",
    ],
    "00A4": [
        "NonRestricted C-Less In",
        "wat_promoInAmt",
        "wat_in_nonrestricted",
        "nonrestrictedtransferin",
    ],
    "00B8": [
        "Reg Cashable C-Less Out",
        "wat_cashableOutAmt",
        "wat_out_cashable",
        "regularcashabletransferout",
    ],
    "00BA": [
        "Restricted C-Less Out",
        "wat_nonCashOutAmt",
        "wat_out_restricted",
        "restrictedtransferout",
    ],
    "00BC": [
        "NonRestricted C-Less Out",
        "wat_promoOutAmt",
        "wat_out_nonrestricted",
        "nonrestrictedtransferout",
    ],
}


ASYNC_FILTER_THRESHOLD = 20_000

logger = logging.getLogger(__name__)

# Session snapshot next to project root (same directory as ``gui_app.py`` / repo root).
_LAST_SESSION_FORMAT_VERSION = 1
_MAX_SESSION_INCIDENTS = 12_000


def _last_session_json_path() -> Path:
    p = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fallback to repo root when directory creation is not permitted.
        p = Path(__file__).resolve().parent.parent
    return p / "last_session.json"


def _incident_to_dict(inc: Incident) -> dict[str, Any]:
    return {
        "timestamp": inc.timestamp.isoformat() if inc.timestamp else None,
        "game": inc.game,
        "severity": inc.severity,
        "error_type": inc.error_type,
        "probable_cause": inc.probable_cause,
        "log_file_path": inc.log_file_path,
        "line_number": inc.line_number,
        "line_snippet": inc.line_snippet,
        "first_cause_line": inc.first_cause_line,
        "first_cause_snippet": inc.first_cause_snippet,
        "validation_status": inc.validation_status,
        "validation_detail": inc.validation_detail,
        "remote_ram_used_pct": inc.remote_ram_used_pct,
        "remote_process_mb": inc.remote_process_mb,
        "remote_ram_total_mb": inc.remote_ram_total_mb,
        "remote_ram_used_mb": inc.remote_ram_used_mb,
        "live_ram_used_mb": inc.live_ram_used_mb,
        "live_ram_total_mb": inc.live_ram_total_mb,
        "live_ram_used_pct": inc.live_ram_used_pct,
        "live_ram_process_mb": inc.live_ram_process_mb,
        "live_ram_captured_ts": inc.live_ram_captured_ts,
        "live_ram_delta_sec": inc.live_ram_delta_sec,
        "id": inc.id,
    }


def _incident_from_dict(d: dict[str, Any]) -> Incident:
    raw_ts = d.get("timestamp")
    timestamp: datetime | None = None
    if raw_ts:
        try:
            s = str(raw_ts).replace("Z", "+00:00")
            timestamp = datetime.fromisoformat(s)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                timestamp = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            timestamp = None
    return Incident(
        timestamp=timestamp,
        game=str(d.get("game") or "unknown"),
        severity=str(d.get("severity") or ""),
        error_type=str(d.get("error_type") or ""),
        probable_cause=str(d.get("probable_cause") or ""),
        log_file_path=str(d.get("log_file_path") or ""),
        line_number=int(d.get("line_number") or 0),
        line_snippet=str(d.get("line_snippet") or ""),
        first_cause_line=d.get("first_cause_line"),
        first_cause_snippet=d.get("first_cause_snippet"),
        validation_status=d.get("validation_status"),
        validation_detail=d.get("validation_detail"),
        remote_ram_used_pct=d.get("remote_ram_used_pct"),
        remote_process_mb=d.get("remote_process_mb"),
        remote_ram_total_mb=d.get("remote_ram_total_mb"),
        remote_ram_used_mb=d.get("remote_ram_used_mb"),
        live_ram_used_mb=d.get("live_ram_used_mb"),
        live_ram_total_mb=d.get("live_ram_total_mb"),
        live_ram_used_pct=d.get("live_ram_used_pct"),
        live_ram_process_mb=d.get("live_ram_process_mb"),
        live_ram_captured_ts=d.get("live_ram_captured_ts"),
        live_ram_delta_sec=d.get("live_ram_delta_sec"),
        id=str(d.get("id") or "") or uuid.uuid4().hex,
    )


def _sort_state_nodes_chronologically(nodes: list[Any]) -> list[Any]:
    """Order state timeline segments by time, then path and line (stable tie-break)."""

    def _key(n: Any) -> tuple[float, str, int]:
        sk = getattr(n, "timestamp_sort_key", None)
        sk_f = float(sk) if sk is not None else 0.0
        p = str(getattr(n, "log_file_path", "") or "")
        ln = int(getattr(n, "line_number", 0) or 0)
        return (sk_f, p, ln)

    return sorted(nodes, key=_key)


_RAM_FETCH_SEVERITIES = frozenset(
    {"WARN", "WARNING", "ERROR", "FATAL", "CRITICAL", "MEDIUM"},
)


def _severity_triggers_remote_ram_fetch(severity: str) -> bool:
    return (severity or "").strip().upper() in _RAM_FETCH_SEVERITIES


def _naive_utc_if_needed(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Bookmark:
    """User pin on a specific incident with optional note text."""

    incident_id: str
    note: str


@dataclass(frozen=True, slots=True)
class DisplayRow:
    """One visible table row; ``count`` > 1 when consecutive duplicates are collapsed."""

    incident: Incident
    count: int = 1


def collapse_consecutive_incidents_to_rows(
    incidents: list[Incident], collapse: bool
) -> list[DisplayRow]:
    """Group consecutive incidents with same severity, error_type, and game."""
    if not incidents:
        return []
    if not collapse:
        return [DisplayRow(incident=inc, count=1) for inc in incidents]
    rows: list[DisplayRow] = []
    for inc in incidents:
        if rows:
            prev = rows[-1].incident
            if (
                prev.severity == inc.severity
                and prev.error_type == inc.error_type
                and prev.game == inc.game
            ):
                last = rows[-1]
                rows[-1] = DisplayRow(incident=last.incident, count=last.count + 1)
                continue
        rows.append(DisplayRow(incident=inc, count=1))
    return rows


def _logical_incident_key(inc: Incident) -> tuple[str, str, str, str]:
    """
    Collapse rows that are the same log message mirrored into several files.

    Live watch follows several recently modified logs; Slot / OneHand often writes
    the same line to more than one file. The table does not show path, so those
    look like duplicate issues.
    """
    ts_key = (
        inc.timestamp.isoformat()
        if inc.timestamp is not None
        else ""
    )
    return (
        ts_key,
        inc.severity,
        inc.error_type,
        (inc.line_snippet or "").strip(),
    )


class IncidentViewModel(QObject):
    """Central state for the incident table, dashboard metrics, and filtering."""

    filter_rebuilt = Signal()
    rows_inserted = Signal(int, int)
    stats_changed = Signal()
    selected_incident_changed = Signal(object)
    live_critical_filtered = Signal(int, object)
    """Filtered table row index and ``Incident`` for live CRITICAL alerts."""
    scan_state_changed = Signal(bool)
    filter_busy_changed = Signal(bool)
    state_nodes_changed = Signal()
    session_state_changed = Signal()
    quick_filters_changed = Signal()
    # Dashboard label updates: themes, or literal "Multigame Selector" after exit-sequence lines.
    active_game_changed = Signal(str)
    bookmarks_changed = Signal()
    incident_ram_updated = Signal(str)
    """Emitted with ``incident_id`` after ``remote_ram_used_pct`` is applied (table refresh)."""
    remote_ram_bulk_updated = Signal()
    """After a batch WMIC snapshot updates many incidents; refresh RAM column in one shot."""
    incident_event_ram_updated = Signal(str)
    """Emitted with ``incident_id`` after the log-sampled event RAM is applied (inspector refresh)."""
    version_identified = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._all: list[Incident] = []
        self._filtered: list[int] = []
        self._filter_text = ""
        self._filter_seq = 0
        self._filter_emitter = FilterIndexEmitter(self)
        self._filter_emitter.done.connect(self._on_async_filter_done)

        self._pool = QThreadPool.globalInstance()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._run_filter_pass)

        self._files_scanned = 0
        self._files_total = 0
        self._critical_count = 0
        self._active_game_name = "—"
        self._scanning = False
        self._filter_busy = False
        self._selected_incident: Incident | None = None
        self._state_nodes: list[Any] = []
        self._timeline_t0: float | None = None
        self._timeline_t1: float | None = None
        self._val_sessions_validated = 0
        self._val_sessions_passed = 0
        self._val_discrepancies = 0
        self._logged_machine_id: str | None = None
        self._env_app: str | None = None
        self._env_clr: str | None = None
        self._env_os: str | None = None
        self._env_enriched = False
        self._onehand_version: str | None = None
        self._accounting_registers_cache: dict[str, dict[str, int]] = {}
        self.current_product_name: str | None = None
        self.current_software_version: str | None = None
        self._software_version_product_raw: str | None = None
        self._logical_incident_keys: set[tuple[str, str, str, str]] = set()
        self._session_start_time: datetime | None = None
        self._timeline_dirty = False
        self._qf_critical: bool = False
        self._qf_warn: bool = False
        self._qf_math_fails: bool = False
        self._qf_drift: bool = False
        self._qf_known_issues: bool = False
        self._bookmarks: dict[str, str] = {}
        self._time_slice_start: datetime | None = None
        self._time_slice_end: datetime | None = None
        self._collapse_duplicates: bool = False
        self._display_rows: list[DisplayRow] = []
        self._remote_ram_target_ip: str | None = None
        self._ram_fetch_emitter = RamFetchEmitter(self)
        self._ram_fetch_emitter.finished.connect(self._on_remote_ram_fetch_finished)
        self._ram_fetch_emitter.batch_finished.connect(
            self._on_remote_ram_batch_finished
        )
        self._event_ram_emitter = EventRamEmitter(self)
        self._event_ram_emitter.finished.connect(self._on_event_ram_finished)
        self._event_ram_attempted: set[str] = set()
        self._live_ram_pending: set[str] = set()

    def set_remote_ram_target_ip(self, ip: str | None) -> None:
        """Host used for WMIC when live-tailing (remote cabinet IP)."""
        s = (ip or "").strip()
        self._remote_ram_target_ip = s or None

    def _on_remote_ram_fetch_finished(self, incident_id: str, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._apply_remote_ram_to_incident(incident_id, payload)

    def _parse_remote_ram_payload(
        self, payload: dict
    ) -> tuple[float, float, float, float | None] | None:
        try:
            used_pct = float(payload["used_pct"])
            total_mb = float(payload["total_mb"])
            used_mb = float(payload["used_mb"])
        except (KeyError, TypeError, ValueError):
            return None
        proc = payload.get("process_mb")
        if proc is None:
            process_mb = None
        else:
            try:
                process_mb = float(proc)
            except (TypeError, ValueError):
                process_mb = None
        return used_pct, total_mb, used_mb, process_mb

    def _apply_remote_ram_to_incident(self, incident_id: str, payload: dict) -> None:
        parsed = self._parse_remote_ram_payload(payload)
        if parsed is None:
            return
        used_pct, total_mb, used_mb, process_mb = parsed
        for i, inc in enumerate(self._all):
            if inc.id == incident_id:
                self._all[i] = replace(
                    inc,
                    remote_ram_used_pct=used_pct,
                    remote_process_mb=process_mb,
                    remote_ram_total_mb=total_mb,
                    remote_ram_used_mb=used_mb,
                )
                break
        else:
            return
        self._rebuild_display_rows()
        self.incident_ram_updated.emit(incident_id)

    def _on_remote_ram_batch_finished(self, incident_ids: object, payload: object) -> None:
        if not isinstance(incident_ids, list) or not isinstance(payload, dict):
            return
        parsed = self._parse_remote_ram_payload(payload)
        if parsed is None:
            return
        used_pct, total_mb, used_mb, process_mb = parsed
        want = {str(x) for x in incident_ids}
        if not want:
            return
        capture_ts = self._capture_ts_from_payload(payload)
        live_ids: list[str] = []
        changed = False
        for i, inc in enumerate(self._all):
            if inc.id not in want:
                continue
            fields: dict[str, Any] = {
                "remote_ram_used_pct": used_pct,
                "remote_process_mb": process_mb,
                "remote_ram_total_mb": total_mb,
                "remote_ram_used_mb": used_mb,
            }
            if inc.id in self._live_ram_pending and capture_ts is not None:
                fields.update(
                    {
                        "live_ram_used_pct": used_pct,
                        "live_ram_total_mb": total_mb,
                        "live_ram_used_mb": used_mb,
                        "live_ram_process_mb": process_mb,
                        "live_ram_captured_ts": capture_ts.isoformat(),
                        "live_ram_delta_sec": self._delta_sec(
                            capture_ts, inc.timestamp
                        ),
                    }
                )
                live_ids.append(inc.id)
            updated = replace(inc, **fields)
            self._all[i] = updated
            if (
                self._selected_incident is not None
                and self._selected_incident.id == inc.id
            ):
                self._selected_incident = updated
            changed = True
        for iid in live_ids:
            self._live_ram_pending.discard(iid)
        if not changed:
            return
        self._rebuild_display_rows()
        self.remote_ram_bulk_updated.emit()
        for iid in live_ids:
            self.incident_event_ram_updated.emit(iid)

    @staticmethod
    def _capture_ts_from_payload(payload: dict) -> datetime | None:
        raw = payload.get("capture_ts")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
        return _naive_utc_if_needed(dt)

    @staticmethod
    def _delta_sec(capture: datetime, event: datetime | None) -> float | None:
        if event is None:
            return None
        try:
            return (capture - _naive_utc_if_needed(event)).total_seconds()
        except (TypeError, ValueError):
            return None

    def _schedule_remote_ram_for_incidents(
        self, incidents: list[Incident], *, live: bool = False
    ) -> None:
        """WMIC snapshot for remote IP; used after scan file batches and live tail batches.

        When ``live`` is set (Live Watch), CRITICAL ids are also flagged so the returning
        snapshot is recorded as a near-real-time "RAM at error" sample (with capture time
        and delta), not just the table's current-RAM column.
        """
        ip = (self._remote_ram_target_ip or "").strip()
        if not ip or not incidents:
            return
        ids = [
            inc.id
            for inc in incidents
            if _severity_triggers_remote_ram_fetch(inc.severity)
        ]
        if not ids:
            return
        if live:
            for inc in incidents:
                if (inc.severity or "").strip().upper() == "CRITICAL":
                    self._live_ram_pending.add(inc.id)
        schedule_ram_fetch_batch(self._pool, ip, ids, self._ram_fetch_emitter)

    def request_event_ram(self, incident: Incident) -> None:
        """Async-parse the EGM's logged RAM nearest a CRITICAL incident (once per incident).

        Pure log read (never queries a host), so it works for historical scans. The result
        is cached on the incident, so re-selecting it costs nothing.
        """
        if not isinstance(incident, Incident):
            return
        if (incident.severity or "").strip().upper() != "CRITICAL":
            return
        if incident.event_ram_status is not None:
            return
        if incident.id in self._event_ram_attempted:
            return
        self._event_ram_attempted.add(incident.id)
        schedule_event_ram_parse(
            self._pool,
            incident.id,
            incident.log_file_path,
            incident.line_snippet or "",
            self._event_ram_emitter,
        )

    def _on_event_ram_finished(self, incident_id: str, payload: object) -> None:
        if not isinstance(incident_id, str):
            return
        if isinstance(payload, dict):
            def _f(key: str) -> float | None:
                v = payload.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            ts = payload.get("sample_ts")
            fields: dict[str, Any] = {
                "event_ram_used_mb": _f("used_mb"),
                "event_ram_total_mb": _f("total_mb"),
                "event_ram_free_mb": _f("free_mb"),
                "event_process_mb": _f("process_mb"),
                "event_ram_sample_ts": str(ts) if ts else None,
                "event_ram_delta_sec": _f("delta_sec"),
                "event_ram_status": "ok",
            }
        else:
            fields = {"event_ram_status": "unavailable"}

        updated: Incident | None = None
        for i, inc in enumerate(self._all):
            if inc.id == incident_id:
                updated = replace(inc, **fields)
                self._all[i] = updated
                break
        if updated is None:
            return
        # Keep the stored selection reference fresh so the inspector/AI see the new data.
        if self._selected_incident is not None and self._selected_incident.id == incident_id:
            self._selected_incident = updated
        self.incident_event_ram_updated.emit(incident_id)

    def incident_by_id(self, incident_id: str) -> Incident | None:
        for inc in self._all:
            if inc.id == incident_id:
                return inc
        return None

    # --- read API for views / models ---
    @property
    def all_incidents(self) -> list[Incident]:
        return self._all

    @property
    def filtered_indices(self) -> list[int]:
        return self._filtered

    def filtered_row_count(self) -> int:
        return len(self._display_rows)

    def incident_at_filtered_row(self, row: int) -> Incident | None:
        dr = self.display_row_at(row)
        return dr.incident if dr is not None else None

    def display_row_at(self, row: int) -> DisplayRow | None:
        if row < 0 or row >= len(self._display_rows):
            return None
        return self._display_rows[row]

    def filtered_incidents(self) -> list[DisplayRow]:
        """Visible rows in table order (filters + optional consecutive duplicate collapse)."""
        return list(self._display_rows)

    def filtered_incidents_flat(self) -> list[Incident]:
        """Every matched incident in filter order (ignores collapse grouping)."""
        return [self._all[i] for i in self._filtered]

    def unfiltered_incidents(self) -> list[Incident]:
        """All incidents from the current scan session (ignores table filters)."""
        return list(self._all)

    def set_collapse_duplicates(self, enabled: bool) -> None:
        if self._collapse_duplicates == enabled:
            return
        self._collapse_duplicates = enabled
        self._rebuild_filtered()

    def _rebuild_display_rows(self) -> None:
        if not self._filtered:
            self._display_rows = []
            return
        incs = [self._all[i] for i in self._filtered]
        self._display_rows = collapse_consecutive_incidents_to_rows(
            incs, self._collapse_duplicates
        )

    def display_row_index_for_filtered_index(self, fi: int) -> int:
        """Map index into ``_filtered`` to a collapsed table row (0-based)."""
        if fi < 0 or fi >= len(self._filtered):
            return 0
        incs = [self._all[i] for i in self._filtered[: fi + 1]]
        rows = collapse_consecutive_incidents_to_rows(incs, self._collapse_duplicates)
        return max(0, len(rows) - 1)

    def _passes_incident_filters(self, inc: Incident) -> bool:
        from gui.filter_runnable import _incident_blob

        needle = self._filter_text.strip().lower()
        sess_ts = self.session_start_epoch()
        qf = self._quick_filter_snapshot()
        b0, b1 = self._session_brush_epoch_bounds()
        if not _session_match(inc, sess_ts):
            return False
        if not quick_filter_match(inc, qf):
            return False
        if not _timeline_match(inc, self._timeline_t0, self._timeline_t1):
            return False
        if not _brush_time_slice_match(inc, b0, b1):
            return False
        if needle and needle not in _incident_blob(inc):
            return False
        return True

    def _finalize_filtered_growth(self, old_filtered_len: int) -> None:
        self._rebuild_display_rows()
        new_len = len(self._filtered)
        if new_len <= old_filtered_len:
            return
        if self._collapse_duplicates:
            self.filter_rebuilt.emit()
        else:
            self.rows_inserted.emit(old_filtered_len, new_len - 1)

    def files_scanned(self) -> int:
        return self._files_scanned

    def files_total(self) -> int:
        return self._files_total

    def critical_count(self) -> int:
        return self._critical_count

    def last_active_game(self) -> str:
        return self._active_game_name

    def _set_active_game_if_changed(self, name: str | None) -> None:
        """Update dashboard label; accepts theme names and ``Multigame Selector``."""
        if not name or name == "unknown":
            return
        if name == self._active_game_name:
            return
        self._active_game_name = name
        self.active_game_changed.emit(name)

    def logged_machine_id(self) -> str | None:
        """First non-empty cabinet id (gst…) seen during this scan session."""
        return self._logged_machine_id

    def onehand_version(self) -> str | None:
        """First ``OneHand.MainFrm - SlotMachine v…`` version seen this scan (same order as file parse)."""
        return self._onehand_version

    def software_version_product_raw(self) -> str | None:
        """Verbatim ``ProductVersion`` from ``OneHand.exe`` metadata (details tab), if read."""
        return self._software_version_product_raw

    def _extract_full_version_from_logs(
        self, log_dir: str | Path
    ) -> tuple[str | None, str | None]:
        """
        Search for LogDaemon logs under ``log_dir`` and extract version + optional Description.

        Returns ``(core_version, product_name_or_none)`` from lines like
        ``Spawning v2.0.0-rc6… (Description: SomeProduct)``.
        """
        root = Path(str(log_dir))
        try:
            if root.is_file():
                root = root.parent
        except OSError:
            return None, None
        try:
            if not root.exists():
                return None, None
        except OSError:
            return None, None

        # Find LogDaemon logs under the root (recursive), then sort by most-recently modified.
        # Lab layout: ``GoldClub.Logging.LogDaemon\YYYY-MM-DD.log`` (no ``LogDaemon`` in filename).
        patterns = [
            str(root / "**" / "*LogDaemon*.log"),
            str(root / "**" / "*LogDaemon*.txt"),
            str(root / "**" / "*logdaemon*.log"),
            str(root / "**" / "*logdaemon*.txt"),
            str(root / "GoldClub.Logging.LogDaemon" / "**" / "*.log"),
            str(root / "**" / "GoldClub.Logging.LogDaemon" / "**" / "*.log"),
            str(root / "GoldClub.Logging.LogDaemon" / "**" / "*.txt"),
            str(root / "**" / "GoldClub.Logging.LogDaemon" / "**" / "*.txt"),
        ]
        files: list[str] = []
        for pat in patterns:
            try:
                files.extend(glob.glob(pat, recursive=True))
            except Exception:
                continue
        # De-dup and keep only plausible files.
        uniq: list[str] = []
        seen: set[str] = set()
        for f in files:
            s = str(f)
            if not s or s in seen:
                continue
            seen.add(s)
            uniq.append(s)
        if not uniq:
            return None, None

        def _mtime(p: str) -> float:
            try:
                return float(os.path.getmtime(p))
            except OSError:
                return 0.0

        uniq.sort(key=_mtime, reverse=True)

        # Optional "(Description: …)" for product label (e.g. JinLong, GameStar+36).
        spawn_pat = re.compile(
            r"Spawning\s+v(?P<base>[\d.]+)(?P<rc>(?:[-+]\s*rc\s*\d+))?"
            r"(?:.*?\(Description:\s*(?P<desc>[^)]+?)\))?",
            flags=re.IGNORECASE,
        )
        # Spawning line can be hundreds of rows into a daily file; scan a deeper prefix.
        for fp_s in uniq[:8]:
            fp = Path(fp_s)
            try:
                with fp.open("r", encoding="utf-8", errors="replace") as f:
                    for _ in range(2000):
                        line = f.readline()
                        if not line:
                            break
                        m = spawn_pat.search(line)
                        if not m:
                            continue
                        base = m.group("base")
                        rc_raw = (m.group("rc") or "")
                        rc = (
                            rc_raw.replace(" ", "")
                            .replace("+", "-")
                            .lower()
                        )
                        desc = m.group("desc")
                        prod = desc.strip() if desc else None
                        if base:
                            return f"v{base}{rc}", prod
            except OSError:
                continue
        return None, None

    def _extract_version_from_slotlog(
        self, log_dir: str | Path
    ) -> tuple[str | None, str | None]:
        """
        Read SlotLog / OneHand logs under ``log_dir`` for ``OneHand.MainFrm - SlotMachine v…``.

        Filenames are often ``YYYY-MM-DD.log`` inside a ``SlotLog`` folder (no ``SlotLog`` in the name).

        Returns ``(core_version, product_name)``. Product name is always ``None`` here (use EXE
        or LogDaemon for platform label). Prefer this over LogDaemon when the daemon line is stale.
        """
        root = Path(str(log_dir))
        try:
            if root.is_file():
                root = root.parent
        except OSError:
            return None, None
        try:
            if not root.exists():
                return None, None
        except OSError:
            return None, None

        patterns = [
            str(root / "**" / "*SlotLog*.log"),
            str(root / "**" / "*slotlog*.log"),
            str(root / "SlotLog" / "**" / "*.log"),
            str(root / "**" / "SlotLog" / "**" / "*.log"),
            str(root / "**" / "OneHand*" / "**" / "*.log"),
        ]
        files: list[str] = []
        for pat in patterns:
            try:
                files.extend(glob.glob(pat, recursive=True))
            except Exception:
                continue
        uniq: list[str] = []
        seen: set[str] = set()
        for f in files:
            s = str(f)
            if not s or s in seen:
                continue
            seen.add(s)
            uniq.append(s)
        if not uniq:
            return None, None

        def _mtime(p: str) -> float:
            try:
                return float(os.path.getmtime(p))
            except OSError:
                return 0.0

        uniq.sort(key=_mtime, reverse=True)

        # Broader fallback if a line omits ``OneHand.MainFrm -`` (match parser where possible).
        slot_only_pat = re.compile(r"SlotMachine\s+v([\d.]+)", re.IGNORECASE)

        for fp_s in uniq[:12]:
            fp = Path(fp_s)
            try:
                with fp.open("r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i > 8000:
                            break
                        m = _ONEHAND_SLOT_VER_RE.search(line)
                        if not m:
                            m = slot_only_pat.search(line)
                        if not m:
                            continue
                        base = m.group(1)
                        if base:
                            return f"v{base}", None
            except OSError:
                continue
        return None, None

    def _extract_version_from_onehand_exe(
        self, log_dir: str | Path
    ) -> tuple[str | None, str | None, str | None]:
        """
        Windows-only: read ``OneHand.exe`` **Product version** and **Product name** (Properties → Details).

        Returns ``(core_version, raw_product_version, product_name_or_none)``.
        """
        if os.name != "nt":
            return None, None, None
        root = Path(str(log_dir))
        if root.is_file():
            root = root.parent

        # Try to infer Goldclub root from ...\\Goldclub\\var\\log using safe Path traversal.
        goldclub_root: Path | None = None
        cur: Path | None = root
        for _ in range(10):
            if cur is None:
                break
            try:
                if cur.name.lower() == "goldclub":
                    goldclub_root = cur
                    break
            except Exception:
                pass
            parent = cur.parent
            if parent == cur:
                break
            cur = parent

        # UNC/local common layout fallback: .../Goldclub/var/log -> parent.parent == Goldclub.
        fallback_goldclub = root.parent.parent if root.parent != root else root
        exe_candidates: list[Path] = []
        if goldclub_root is not None:
            exe_candidates.append(goldclub_root / "slot" / "OneHand.exe")
            exe_candidates.append(goldclub_root / "slot" / "bin" / "OneHand.exe")
        exe_candidates.append(fallback_goldclub / "slot" / "OneHand.exe")
        exe_candidates.append(fallback_goldclub / "slot" / "bin" / "OneHand.exe")
        exe_candidates.append(Path(r"C:\Goldclub\slot\OneHand.exe"))

        exe_path: Path | None = None
        for p in exe_candidates:
            try:
                if p.is_file():
                    exe_path = p
                    break
            except OSError:
                continue
        if exe_path is None:
            logger.debug(
                "OneHand.exe not found for log root %s; tried %s",
                log_dir,
                exe_candidates[:4],
            )
            return None, None, None

        exe_str = str(exe_path)
        product_version = ""
        product_name_meta = ""
        win32api = None
        try:
            import win32api as _win32api  # type: ignore

            win32api = _win32api
        except Exception:
            logger.debug(
                "[VERSION] pywin32 (win32api) not available; will sniff OneHand.exe bytes for ProductVersion."
            )

        try:
            lang_cp: tuple[int, int] | None = None
            if win32api is not None:
                try:
                    lang_cp = win32api.GetFileVersionInfo(
                        exe_str, r"\VarFileInfo\Translation"
                    )[0]
                    lang, codepage = lang_cp
                    str_info_path = (
                        f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductVersion"
                    )
                    product_version = (
                        win32api.GetFileVersionInfo(exe_str, str_info_path) or ""
                    )
                except Exception:
                    try:
                        product_version = (
                            win32api.GetFileVersionInfo(
                                exe_str, r"\StringFileInfo\040904b0\ProductVersion"
                            )
                            or ""
                        )
                    except Exception:
                        product_version = (
                            win32api.GetFileVersionInfo(
                                exe_str, r"\StringFileInfo\000004b0\ProductVersion"
                            )
                            or ""
                        )

                try:
                    if lang_cp is not None:
                        lang, codepage = lang_cp
                        pn_path = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\ProductName"
                        product_name_meta = (
                            win32api.GetFileVersionInfo(exe_str, pn_path) or ""
                        )
                    else:
                        raise OSError("no translation")
                except Exception:
                    try:
                        product_name_meta = (
                            win32api.GetFileVersionInfo(
                                exe_str, r"\StringFileInfo\040904b0\ProductName"
                            )
                            or ""
                        )
                    except Exception:
                        try:
                            product_name_meta = (
                                win32api.GetFileVersionInfo(
                                    exe_str, r"\StringFileInfo\000004b0\ProductName"
                                )
                                or ""
                            )
                        except Exception:
                            product_name_meta = ""

            pv = str(product_version).strip()
            pn_out = str(product_name_meta).strip() or None

            if not pv:
                logger.debug(
                    "[VERSION] No ProductVersion from win32api; sniffing binary: %s",
                    exe_str,
                )
                sniff_pv, sniff_pn = self._sniff_onehand_exe_version_strings(exe_path)
                if sniff_pv:
                    pv = sniff_pv.strip()
                if sniff_pn and not pn_out:
                    pn_out = sniff_pn

            base_m = re.search(r"(\d+\.\d+\.\d+)", pv)
            rc_m = re.search(r"(?:\+|-)\s*rc\s*([0-9]{1,3})\b", pv, flags=re.IGNORECASE)
            if not rc_m:
                rc_m = re.search(r"\brc\s*([0-9]{1,3})\b", pv, flags=re.IGNORECASE)

            raw_for_tooltip = pv if pv else None

            if base_m:
                base = base_m.group(1)
                rc = f"-rc{rc_m.group(1)}" if rc_m else ""
                final_version = f"v{base}{rc}"
                logger.debug(
                    "OneHand.exe product version from %s: %r → %s",
                    exe_str,
                    pv,
                    final_version,
                )
                return final_version, raw_for_tooltip, pn_out

            # Numeric file version only when win32api can read VS_FIXEDFILEINFO.
            if win32api is not None:
                info = win32api.GetFileVersionInfo(exe_str, "\\")
                ms = info["FileVersionMS"]
                ls = info["FileVersionLS"]
                a = (ms >> 16) & 0xFFFF
                b = ms & 0xFFFF
                c = (ls >> 16) & 0xFFFF
                final_version = f"v{a}.{b}.{c}"
                logger.debug(
                    "OneHand.exe FileVersion from %s (ProductVersion unparsed): %s",
                    exe_str,
                    final_version,
                )
                return final_version, raw_for_tooltip, pn_out

            return None, None, pn_out
        except Exception as e:
            logger.debug("Failed to extract exe version from %s: %s", exe_path, e)
            return None, None, None

    def _sniff_onehand_exe_version_strings(
        self, exe_path: Path, *, max_bytes: int = 32 * 1024 * 1024
    ) -> tuple[str, str | None]:
        """
        Pure-Python fallback: scan the first ``max_bytes`` of the PE for UTF-16LE strings.

        Looks for a ProductVersion-like token (e.g. ``2.0.0+RC6+df5851235``). Optional product
        hints are best-effort (marketing strings embedded in the image).
        """
        try:
            with exe_path.open("rb") as f:
                blob = f.read(max_bytes)
        except OSError as e:
            logger.debug("[VERSION] Binary sniff read failed: %s", e)
            return "", None
        if not blob:
            return "", None

        text = blob.decode("utf-16le", errors="ignore")
        raw_pv = ""
        m = re.search(
            r"(\d+\.\d+\.\d+(?:\.\d+)?[+-]RC\d+(?:[+-][a-zA-Z0-9]+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            raw_pv = m.group(1)
        else:
            m2 = re.search(
                r"(\d+\.\d+\.\d+[+-]RC\d+)\b",
                text,
                re.IGNORECASE,
            )
            if m2:
                raw_pv = m2.group(1)

        product: str | None = None
        if "GameStar+36" in text:
            product = "GameStar+36"
        elif "JinLong" in text:
            product = "JinLong"

        if raw_pv:
            logger.debug("[VERSION] Binary sniff found ProductVersion-like: %r", raw_pv)
        return raw_pv, product

    def refresh_current_software_version(self, log_dir: str | Path) -> None:
        # Priority: EXE (metadata) → SlotLog (OneHand SlotMachine line) → LogDaemon (often stale).
        core: str | None = None
        product: str | None = None
        raw_pv: str | None = None

        exe = self._extract_version_from_onehand_exe(log_dir)
        if exe[0]:
            core, raw_pv, product = exe[0], exe[1], (exe[2] or "").strip() or None
            logger.debug("Version source=EXE core=%s product=%s", core, product)
        else:
            slot = self._extract_version_from_slotlog(log_dir)
            if slot[0]:
                core = slot[0]
                product = (slot[1] or "").strip() or None
                logger.debug("Version source=SlotLog core=%s", core)
            else:
                daemon = self._extract_full_version_from_logs(log_dir)
                if daemon[0]:
                    core = daemon[0]
                    product = (daemon[1] or "").strip() or None
                    logger.debug(
                        "Version source=LogDaemon core=%s product=%s", core, product
                    )

        self._software_version_product_raw = raw_pv if exe[0] else None
        self.current_software_version = core
        self.current_product_name = product

        if self.current_software_version:
            self.version_identified.emit(self.software_version_for_ai())
        else:
            self.version_identified.emit(PRODUCT_VERSION_PLACEHOLDER)

    def software_version_for_ai(self) -> str:
        core = (self.current_software_version or "").strip()
        if not core:
            return PRODUCT_VERSION_PLACEHOLDER
        pn = (self.current_product_name or "").strip() or DEFAULT_PRODUCT_NAME
        return format_product_build_version(pn, core)

    def _slotlog_candidate_paths(self, root: Path) -> list[str]:
        """Glob patterns matching SlotLog / OneHand logs (same family as version sniff)."""
        patterns = [
            str(root / "**" / "*SlotLog*.log"),
            str(root / "**" / "*slotlog*.log"),
            str(root / "SlotLog" / "**" / "*.log"),
            str(root / "**" / "SlotLog" / "**" / "*.log"),
            str(root / "**" / "OneHand*" / "**" / "*.log"),
        ]
        files: list[str] = []
        for pat in patterns:
            try:
                files.extend(glob.glob(pat, recursive=True))
            except Exception:
                continue
        uniq: list[str] = []
        seen: set[str] = set()
        for f in files:
            s = str(f)
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)

        def _mtime(p: str) -> float:
            try:
                return float(os.path.getmtime(p))
            except OSError:
                return 0.0

        uniq.sort(key=_mtime, reverse=False)
        return uniq

    def _tail_lines_from_file(self, path: Path, *, max_bytes: int = 262_144) -> list[str]:
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                sz = f.tell()
                f.seek(max(0, sz - max_bytes))
                data = f.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        return data.splitlines()

    def _parse_accounting_register_tokens(self, line: str) -> dict[str, int]:
        """
        Extract ``name -> int`` from lines mentioning ``Accounting::UpdateRegisters``.

        Handles ``key=value``, ``key: value``, and quoted JSON-like ``"key": 123`` fragments.
        """
        if "Accounting" not in line or "UpdateRegisters" not in line:
            return {}
        out: dict[str, int] = {}
        for m in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(-?\d+)\b",
            line,
        ):
            out[m.group(1)] = int(m.group(2))
        for m in re.finditer(
            r'["\']([A-Za-z][A-Za-z0-9_]*)["\']\s*:\s*(-?\d+)\b',
            line,
        ):
            out[m.group(1)] = int(m.group(2))
        return out

    def invalidate_accounting_registers_cache(self, log_root: str | None = None) -> None:
        """Drop cached SlotLog register tails (scan root changed or dialog closed)."""
        if log_root is None:
            self._accounting_registers_cache.clear()
            return
        key = (log_root or "").strip().lower()
        if key:
            self._accounting_registers_cache.pop(key, None)

    def _latest_accounting_registers_from_logs(self, log_root: str) -> dict[str, int]:
        """
        Merge accounting fields from tail of SlotLog-ish files; **last** value wins per key.
        Keys are normalized to lowercase for comparison to ``meterName`` / SAS XML map.
        """
        root_raw = (log_root or "").strip()
        if not root_raw:
            return {}
        cache_key = root_raw.lower()
        cached = self._accounting_registers_cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        root = Path(root_raw)
        try:
            if root.is_file():
                root = root.parent
            if not root.exists():
                return {}
        except OSError:
            return {}

        merged: dict[str, int] = {}
        paths = self._slotlog_candidate_paths(root)
        for fp_s in paths[-24:]:
            fp = Path(fp_s)
            if not fp.is_file():
                continue
            for line in self._tail_lines_from_file(fp):
                if "UpdateRegisters" not in line:
                    continue
                if "Accounting::" not in line and "Accounting." not in line:
                    continue
                for k, v in self._parse_accounting_register_tokens(line).items():
                    merged[k.lower()] = v
        self._accounting_registers_cache[cache_key] = dict(merged)
        return dict(merged)

    def compare_sas_with_accounting(self, sas_hex_block: str, *, log_root: str) -> str:
        """
        Compare last SAS 6F RX meter values with ``Accounting::UpdateRegisters`` fields
        found in SlotLog / OneHand logs under ``log_root``.
        """
        body: list[str] = []
        sas_by_code = last_rx_code_values_from_text(sas_hex_block)
        if not sas_by_code:
            body.append(
                "No SAS 01 6F RX line found in the supplied data — cannot compare meters."
            )

        log_regs = self._latest_accounting_registers_from_logs(log_root)
        root_ok = bool((log_root or "").strip())
        if not root_ok:
            body.append("Scan root is empty — set Scan root to load SlotLog accounting lines.")
        elif not log_regs and sas_by_code:
            body.append(
                "No Accounting::UpdateRegisters key/value pairs found under Scan root "
                "(searched tails of recent SlotLog / OneHand logs). "
                "Confirm cabinet logs include that marker."
            )

        matches = 0
        mismatches = 0
        for code in sorted(sas_by_code.keys()):
            meta = SAS_TO_XML.get(code.upper())
            if not meta:
                display = f"Unknown ({code})"
                body.append(
                    f"MISMATCH: {display}: SAS={sas_by_code[code]} | Log: <unmapped SAS code>"
                )
                mismatches += 1
                continue
            display, xml_key = meta
            sas_val = sas_by_code[code]
            lk = xml_key.lower()
            log_val = log_regs.get(lk)
            if log_val is None:
                for k, v in log_regs.items():
                    if k.lower() == lk:
                        log_val = v
                        break

            if log_val is None:
                body.append(
                    f"MISMATCH: {display}: SAS={sas_val} | Log {xml_key}: <missing> => NO_LOG_FIELD"
                )
                mismatches += 1
            elif log_val == sas_val:
                body.append(
                    f"MATCH: {display} ({code}): SAS={sas_val} | Log {xml_key}={log_val}"
                )
                matches += 1
            else:
                body.append(
                    f"MISMATCH: {display} ({code}): SAS={sas_val} | Log {xml_key}={log_val}"
                )
                mismatches += 1

        summary = (
            f"=== SAS vs SlotLog accounting ===\n"
            f"Summary: {matches} match(es), {mismatches} mismatch(es) or missing.\n"
            f"---\n"
        )
        return summary + "\n".join(body)

    def summarize_sas_accounting_comparison(self, report_plain: str) -> str:
        """Short line for AI / status; flags cross-check mismatches."""
        for ln in report_plain.splitlines():
            s = ln.strip()
            if not s.startswith("Summary:"):
                continue
            m = re.search(r"(\d+)\s+mismatch", s)
            if m and int(m.group(1)) > 0:
                return (
                    s + " — SAS/Accounting mismatch detected in one or more meters."
                )
            return s
        return ""

    def load_machine_accounting_state(self, scan_root: str) -> dict[str, str]:
        """
        Source of truth for SAS verification dialog.

        Primary: attempt to find ``gm2u`` under / near scan_root and parse newest JSON-like file.
        Secondary: fall back to SlotLog ``Accounting::UpdateRegisters`` tokens.

        Returns a dict where keys are meter IDs (\"00\") and/or SAS codes (\"0000\") as strings.
        Values are strings (formatted numeric) for direct table display.
        """
        print("\n" + "=" * 50)
        print(f"[DEBUG-SAS] 1. Original scan_root: {scan_root}")

        root_raw = (scan_root or "").strip()
        if not root_raw:
            print("[DEBUG-SAS] -> Path is empty. Aborting.")
            return {}

        # Normalize Windows/UNC paths that may be entered with forward slashes.
        # IMPORTANT: do not call ``normpath`` here; in some environments it can collapse
        # UNC prefixes unexpectedly. Enforce exactly two leading backslashes for UNC.
        normalized_root = root_raw.replace("/", "\\")
        if root_raw.startswith("//") or root_raw.startswith("\\\\"):
            normalized_root = "\\\\" + normalized_root.lstrip("\\")
        print(f"[DEBUG-SAS] 2. Normalized path: {normalized_root}")
        base_path = Path(normalized_root)
        files_to_parse: list[Path] = []
        root = base_path  # used for logs fallback; may be adjusted to a directory below

        try:
            print(f"[DEBUG-SAS] 3. Does base_path exist?: {base_path.exists()}")

            # NEW: If user points directly at a file (often extensionless gm2u/gm2au), queue it immediately.
            if base_path.exists() and base_path.is_file():
                files_to_parse.append(base_path)
                print("[DEBUG-SAS] -> Base path is a FILE. Added directly to parse queue.")
                root = base_path.parent
            elif base_path.exists() and base_path.is_dir():
                root = base_path
        except OSError:
            print("[DEBUG-SAS] -> OSError probing path; aborting.")
            return {}

        def norm_key(k: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", (k or "").lower())

        def walk(obj: object) -> list[tuple[str, object]]:
            items: list[tuple[str, object]] = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    items.append((str(k), v))
                    items.extend(walk(v))
            elif isinstance(obj, list):
                for v in obj:
                    items.extend(walk(v))
            return items

        def flatten_json_to_norm_map(data: object) -> dict[str, object]:
            flat: dict[str, object] = {}
            for k, v in walk(data):
                nk = norm_key(str(k))
                if nk:
                    flat[nk] = v
            return flat

        def flatten_xml_file_to_norm_map(xml_path: Path) -> dict[str, object]:
            flat: dict[str, object] = {}
            try:
                # Some gm2* state files contain raw XML without an .xml extension,
                # and may include non-XML headers before the "<?xml" prolog.
                raw = xml_path.read_text(encoding="utf-8", errors="ignore")
                xml_start = raw.find("<?xml")
                if xml_start != -1:
                    raw = raw[xml_start:]
                root_el = ET.fromstring(raw)
            except Exception as e:  # noqa: BLE001
                logger.debug("[SAS VERIFY] XML parse failed for %s: %s", xml_path, e)
                return flat
            if not root_el:
                print(f"    [WARNING] XML root missing for {xml_path}")

            for elem in root_el.iter():
                tag_name = str(getattr(elem, "tag", "") or "")
                tag_name = tag_name.split("}")[-1] if tag_name else ""
                text = (getattr(elem, "text", None) or "").strip()
                if tag_name and text:
                    flat[norm_key(tag_name)] = text

                # Aggressive capture for nested-value schemas.
                if tag_name:
                    tn = tag_name.lower()
                    if tn in ("currentvalue", "cumulativevalue", "amount") and text:
                        flat[norm_key(tn)] = text

                attrs = getattr(elem, "attrib", None) or {}
                # DeviceManagerData.xml schema: perfMeter/curMeter nodes store the actual key/value
                # as attributes (often namespaced): meterName="coinIn", meterValue="12345".
                meter_name: str | None = None
                meter_value: str | None = None
                device_class: str = ""
                for ak, av in attrs.items():
                    k = str(ak).split("}")[-1]  # strip XML namespace
                    k = k.split(":")[-1]  # strip prefix (e.g. d4p1:meterName)
                    kl = k.lower()
                    if kl == "metername":
                        meter_name = str(av).strip()
                    elif kl == "metervalue":
                        meter_value = str(av).strip()
                    elif kl == "deviceclass":
                        device_class = str(av).strip().lower()
                if meter_name and meter_value is not None:
                    # Emit composite key first (deviceClass + meterName) to avoid collisions.
                    if device_class:
                        flat[norm_key(f"{device_class}_{meter_name}")] = meter_value
                    # Back-compat: also emit the meterName-only key.
                    flat[norm_key(meter_name)] = meter_value
                    continue

                attrs_l = {str(k).lower(): str(v).strip() for k, v in attrs.items()}
                key_attr = attrs_l.get("name") or attrs_l.get("key") or attrs_l.get("id")
                val_attr = attrs_l.get("value") or attrs_l.get("val")
                if key_attr and val_attr is not None:
                    flat[norm_key(key_attr)] = str(val_attr).strip()

                # Some files store values under CurrentValue/CumulativeValue attributes.
                for special in ("currentvalue", "cumulativevalue", "amount"):
                    if special in attrs_l and attrs_l.get(special):
                        flat[norm_key(special)] = str(attrs_l.get(special)).strip()

                # Dump all attributes too (best-effort)
                for k, v in attrs.items():
                    nk = norm_key(str(k))
                    if nk:
                        flat[nk] = str(v).strip()
            return flat

        # Fast-path: try known authoritative gm2au state files first (avoids expensive walks).
        try:
            m = re.match(r"^\\\\([^\\]+)\\", normalized_root)
            if m:
                ip = (m.group(1) or "").strip()
                if ip:
                    direct_files = [
                        Path(
                            rf"\\{ip}\c$\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger\gm2au\DeviceManagerData.xml_1"
                        ),
                        Path(
                            rf"\\{ip}\c$\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger\gm2au\DeviceManagerData.xml_2"
                        ),
                    ]
                    for df in direct_files:
                        try:
                            if df.exists() and df.is_file():
                                print(f"[DEBUG-SAS] -> FOUND DIRECT FILE: {df}. Parsing immediately.")
                                flat = flatten_xml_file_to_norm_map(df)
                                if flat:
                                    out_direct: dict[str, str] = {}
                                    for k, v in flat.items():
                                        if isinstance(v, (int, float, str)):
                                            out_direct[k] = self._normalize_gm2u_money_value(k, v)
                                    print(f"[DEBUG-SAS] -> Direct parse keys merged: {len(out_direct)}")
                                    print("[DEBUG-SAS] Fast-path successful. Skipping walk.")
                                    return out_direct
                        except OSError:
                            continue
        except Exception:
            pass
        print("[DEBUG-SAS] Fast-path empty. Falling back to recursive walk...")

        # Candidate directories: exact path + up to 3 parents.
        target_dirs: list[Path] = []
        try:
            if root.exists() and root.is_dir():
                target_dirs.append(root)
        except OSError:
            print("[DEBUG-SAS] -> OSError calling exists()/is_dir() on base_path.")
            pass
        cur = root
        for _ in range(3):
            if cur.parent and cur.parent != cur:
                cur = cur.parent
                try:
                    if cur.exists() and cur.is_dir():
                        target_dirs.append(cur)
                except OSError:
                    continue

        # Expand search radius for Aurum state roots: scan GoldClub.Aurum.Services siblings.
        try:
            parts = [p.lower() for p in root.parts]
            if "goldclub.aurum.services" in parts:
                idx = parts.index("goldclub.aurum.services")
                aurum_root = Path(*root.parts[: idx + 1])
                parent_state = aurum_root.parent  # usually ...\var\state
                for extra in (aurum_root, parent_state):
                    if extra not in target_dirs:
                        target_dirs.append(extra)
                try:
                    for sib in parent_state.iterdir():
                        if sib.is_dir():
                            name_l = sib.name.lower()
                            if any(k in name_l for k in ("gcmessenger", "accounting", "meters", "state")):
                                if sib not in target_dirs:
                                    target_dirs.append(sib)
                except OSError:
                    pass
        except Exception:
            pass

        # Hard-target critical gm2* paths when scanning a cabinet via UNC share.
        # This bypasses walk-depth / starting-directory issues when the operator supplies a high-level path.
        try:
            m = re.match(r"^\\\\([^\\]+)\\", normalized_root)
            if m:
                ip = (m.group(1) or "").strip()
                if ip:
                    base_unc = Path(rf"\\{ip}\c$\Goldclub\var\state")
                    gc_messenger_base = base_unc / "GoldClub.Aurum.Services" / "GCMessenger"
                    critical_dirs = [
                        gc_messenger_base / "gm2",
                        gc_messenger_base / "gm2u",
                        gc_messenger_base / "gm2au",
                    ]
                    for c_dir in [base_unc, *critical_dirs]:
                        if c_dir not in target_dirs:
                            target_dirs.append(c_dir)
                            print(f"[DEBUG-SAS] -> Injected critical target dir: {c_dir}")
        except Exception:
            pass

        # BRUTE-FORCE: If GCMessenger is anywhere in our paths, explicitly add the gm2 folders.
        try:
            expanded_dirs: set[Path] = set(target_dirs)
            for d in list(target_dirs):
                d_str = str(d).lower()
                if "gcmessenger" not in d_str:
                    continue
                parts = Path(d).parts
                try:
                    idx = next(i for i, p in enumerate(parts) if str(p).lower() == "gcmessenger")
                except StopIteration:
                    continue
                gc_msg_path = Path(*parts[: idx + 1])
                expanded_dirs.add(gc_msg_path / "gm2")
                expanded_dirs.add(gc_msg_path / "gm2u")
                expanded_dirs.add(gc_msg_path / "gm2au")
                print(f"[DEBUG-SAS] -> Injected gm2 folders into target_dirs based on {gc_msg_path}")
            target_dirs = list(expanded_dirs)
        except Exception:
            pass

        # Also hard-target the exact known gm2au DeviceManagerData files (common authoritative sources).
        try:
            for d in list(target_dirs):
                dl = str(d).lower()
                if "gcmessenger" not in dl:
                    continue
                gm2au = Path(d) / "gm2au"
                for fn in ("DeviceManagerData.xml_1", "DeviceManagerData.xml_2"):
                    fp = gm2au / fn
                    if fp not in files_to_parse:
                        files_to_parse.append(fp)
                        print(f"[DEBUG-SAS] -> Injected critical state file: {fp}")
        except Exception:
            pass


        print("[DEBUG-SAS] 4. Target directories to scan:")
        for d in target_dirs:
            print(f"    -> {d}")

        file_paths: list[Path] = []
        # Ensure explicitly-provided file (if any) is included in parse set.
        file_paths.extend(files_to_parse)
        for d in target_dirs:
            try:
                print(f"[DEBUG-SAS] 5. Walking dir: {d}")
                for root_s, _dirs, files in os.walk(d):
                    root_p = Path(root_s)
                    try:
                        rel_parts = root_p.relative_to(d).parts
                    except Exception:
                        rel_parts = ()
                    # Allow deeper walks (up to 4 levels), and allow deeper when path hints "accounting/meters/state".
                    if rel_parts:
                        depth = len(rel_parts)
                        hint = any(
                            k in str(root_p).lower()
                            for k in ("accounting", "meter", "meters", "state")
                        )
                        if depth > 4 and not hint:
                            continue
                    for fn in files:
                        low = fn.lower()
                        full_path = root_p / fn

                        # Bypass extension filtering when we're in a gm2u/gm2au directory.
                        root_l = str(root_p).lower()
                        is_gm2_dir = ("\\gm2u" in root_l) or ("\\gm2au" in root_l) or ("gm2u" in root_l) or ("gm2au" in root_l)
                        is_gm2_file = low in ("gm2u", "gm2au") or low.startswith("gm2")

                        if is_gm2_dir or is_gm2_file or low.endswith(".json") or low.endswith(".xml"):
                            file_paths.append(full_path)
                            if is_gm2_file:
                                print(f"    [FOUND DIRECT GM2 FILE] {full_path}")
                            elif is_gm2_dir and not (low.endswith(".json") or low.endswith(".xml")):
                                print(f"    [FOUND EXTENSIONLESS GM2 FILE] {full_path}")
                            else:
                                print(f"    [FOUND XML/JSON] {full_path}")
            except Exception:
                continue

        seen: set[str] = set()
        uniq_files: list[Path] = []
        for p in file_paths:
            s = str(p)
            if s and s not in seen:
                seen.add(s)
                uniq_files.append(p)

        print(f"[DEBUG-SAS] 6. Total files to parse: {len(uniq_files)}")

        # Return a flattened key/value dictionary for matching (normalized keys).
        # Blindly load all JSON data found; alias mapping filters what we need later.
        out: dict[str, str] = {}
        for fp in uniq_files:
            print(f"[DEBUG-SAS] 7. Parsing: {fp}")
            low = fp.name.lower()
            flat: dict[str, object] = {}
            if low.endswith(".json"):
                try:
                    txt = fp.read_text(encoding="utf-8", errors="ignore")
                    data = json.loads(txt)
                    flat = flatten_json_to_norm_map(data)
                except Exception as e:  # noqa: BLE001
                    print(f"    [ERROR] JSON read failed: {e}")
                    continue
            else:
                # Route .xml AND extensionless gm2* files here.
                flat = flatten_xml_file_to_norm_map(fp)
                if not flat:
                    print(f"    [WARNING] 0 keys extracted from {fp.name}")
                    name_l = fp.name.lower()
                    if ("meter" in name_l) or ("accounting" in name_l) or ("gm2" in str(fp).lower()):
                        print(f"    [WARNING] File name/path suggests meters/accounting/gm2*: {fp}")
            for k, v in flat.items():
                if isinstance(v, (int, float, str)):
                    out[k] = self._normalize_gm2u_money_value(k, v)
            print(f"    [EXTRACTED] {len(flat)} keys from file.")
            if flat:
                try:
                    sample_keys = list(flat.keys())[:20]
                except Exception:
                    sample_keys = []
                print(f"    [SAMPLE KEYS from {fp.name}]: {sample_keys[:10]}")

        print(f"[DEBUG-SAS] 8. Total unique keys merged: {len(out)}")
        print("=" * 50 + "\n")

        # Secondary: logs (always as fallback): merge normalized keys too.
        log_regs = self._latest_accounting_registers_from_logs(str(root))
        for k, v in log_regs.items():
            nk = norm_key(str(k))
            if nk:
                out[nk] = self._normalize_gm2u_money_value(nk, v)
        # Also include SAS_TO_XML meterName keys when present, for backward compatibility with earlier tooling.
        for _code, meta in SAS_TO_XML.items():
            _display, xml_key = meta
            if not xml_key:
                continue
            nk = norm_key(xml_key)
            if nk and nk in log_regs:
                out[nk] = self._normalize_gm2u_money_value(nk, log_regs[nk])
        return out

    def get_gm2u_value_for_sas_code(self, sas_code: str, machine_state: dict[str, str]) -> str | None:
        """
        Resolve a 6F 2-byte meter code (e.g. ``0005``) to a gm2u JSON value using aliases.

        ``machine_state`` is expected to be flattened with normalized keys (see load_machine_accounting_state).
        """
        code = (sas_code or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{4}", code):
            return None
        aliases = SAS_6F_METER_ALIASES.get(code, [])
        if not aliases:
            return None

        def norm_key(k: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", (k or "").lower())

        def _safe_int(raw: object) -> int:
            try:
                s = str(raw).strip()
                if not s:
                    return 0
                return int(s)
            except Exception:
                return 0

        # SPECIAL CASE: SAS 0004 (Total Cancelled Credits)
        # Observed on this cabinet/firmware the SAS aggregate equals:
        #   0003 HandPaidCancelled + 0016 TicketOut + 0018 CashlessOut.
        # Reuse the existing per-code resolution so each component uses the path
        # that already resolves correctly (0003 shared bucket, 0016 voucher
        # alias, 0018 WAT bucket-sum) rather than guessing raw keys.
        if code == "0004":
            def _resolved_int(sub_code: str) -> int:
                resolved = self.get_gm2u_value_for_sas_code(sub_code, machine_state)
                if resolved is None:
                    return 0
                s = str(resolved).strip()
                # Some codes (e.g. 0003) return a scaled "x.yy" value; the
                # dialog's integer comparison drops the decimal point, so undo
                # the scaling here to recover the raw meter integer before
                # summing the three aggregate components.
                if "." in s:
                    s = s.replace(".", "")
                return _safe_int(s)

            total = _resolved_int("0003") + _resolved_int("0016") + _resolved_int("0018")
            return str(total)

        # SPECIAL CASE: SAS 001C (Total Machine Paid Paytable Win)
        # On some firmware coinOut XML tracks this meter; do not reuse 0001 coin-out
        # aggregation (sasbonuswin/progwin) — that is total coin out, not paytable-only.
        if code == "001C":
            v = machine_state.get("coinout") or machine_state.get("totalcoinout")
            return str(v).strip() if v is not None else None

        # SPECIAL CASE: SAS 0001 (Total Coin Out Credits / Win)
        # Include bonus cashable-in transfers when present in transMeter:
        #   deviceClass="bonus", meterName="cashableInAmt" -> bonus_cashableinamt.
        bonus_cashable_in = _safe_int(machine_state.get("bonuscashableinamt"))
        if code == "0001":
            def _scale_0001_raw(raw: str) -> str:
                s = (raw or "").strip()
                if not s or not s.isdigit():
                    return s
                return f"{(int(s) / 100.0):.2f}"

            def _finish_coin_out(base_int: int) -> str:
                if bonus_cashable_in > 0:
                    combined = base_int + bonus_cashable_in
                    return _scale_0001_raw(str(combined))
                return _scale_0001_raw(str(base_int))

            # SAS 0001 is total coin-out (paytable + SAS/prog win). GoldClub XML splits
            # these: coinout/basegamecoinout hold paytable only; sasbonuswin/progwin are
            # separate. Do not return early from alias coinout — it misses win buckets.
            paytable_keys = (
                "coinout",
                "totalcoinout",
                "gamecoinout",
                "basegamecoinout",
                "bggamecoinout",
                "scattercoinout",
                "progscattercoinout",
                "addscattercoinout",
            )
            win_keys = ("sasbonuswin", "progwin")
            paytable_total = max(
                (_safe_int(machine_state.get(k)) for k in paytable_keys),
                default=0,
            )
            win_total = sum(_safe_int(machine_state.get(k)) for k in win_keys)
            if paytable_total > 0 or win_total > 0:
                return _finish_coin_out(paytable_total + win_total)

            # Legacy alias fallthrough for cabinets that store a single coin-out key.
            for a in aliases:
                nk = norm_key(a)
                if not nk or nk in paytable_keys or nk in win_keys:
                    continue
                v = machine_state.get(nk)
                if v is None:
                    continue
                base_raw = str(v).strip()
                if not base_raw or not base_raw.isdigit():
                    continue
                base_int = _safe_int(base_raw)
                if base_int == 0:
                    continue
                return _finish_coin_out(base_int)

            return _finish_coin_out(0)

        # SPECIAL CASE: SAS 0017 / 0018 (AFT/WAT transfers) are AGGREGATE meters.
        # The EGM reports the transfer total as cashable + non-cashable (restricted)
        # + promotional in a single value, while the WAT XML stores each bucket
        # separately. Comparing against the cashable-only bucket made these rows look
        # short by the non-cashable amount (false MISMATCH). Reconcile against the sum
        # so the value matches the SAS aggregate to the cent.
        if code in {"0017", "0018"}:
            if code == "0017":
                bucket_names = ("wat_cashableInAmt", "wat_nonCashInAmt", "wat_promoInAmt")
            else:
                bucket_names = ("wat_cashableOutAmt", "wat_nonCashOutAmt", "wat_promoOutAmt")
            bucket_keys = [norm_key(n) for n in bucket_names]
            if any(str(machine_state.get(k, "")).strip() != "" for k in bucket_keys):
                total = sum(_safe_int(machine_state.get(k)) for k in bucket_keys)
                return str(total)
            if "watcashableinamt" in machine_state or "wattransferincnt" in machine_state:
                return "0"
            # Older cabinets / different schema: fall through to alias lookup below.

        # DEBUG: For Transfer In (0017), help locate any hidden "1000" values in machine_state.
        # Do not short-circuit normal alias matching; only fall back to the first found 1000
        # if we otherwise cannot resolve a value for this SAS code.
        fallback_1000: str | None = None
        if code == "0017":
            for k, v in (machine_state or {}).items():
                nk = norm_key(str(k))
                if str(v).strip() == "1000":
                    if fallback_1000 is None:
                        if (
                            "cashable" in nk
                            or "transferin" in nk
                            or "watin" in nk
                            or "aft" in nk
                        ) and ("promo" not in nk):
                            fallback_1000 = "1000"

        # SPECIAL CASE: Games Won — missing gameswon on multigamer cabinets means 0 wins.
        if code == "0006":
            won_s = str(machine_state.get("gameswon") or "").strip()
            if won_s.isdigit():
                return won_s
            if str(
                machine_state.get("gamesplayed")
                or machine_state.get("gamebaseplays")
                or ""
            ).strip().isdigit():
                return "0"

        # SPECIAL CASE: Games Lost (Played - Won). Not always stored as its own meter.
        if code == "0007":
            played_s = str(machine_state.get("gamesplayed") or "").strip()
            won_s = str(machine_state.get("gameswon") or "").strip()
            played = int(played_s) if played_s.isdigit() else None
            if played is not None:
                won = int(won_s) if won_s.isdigit() else 0
                return str(max(0, played - won))
            # If we can't compute it, fall through to alias lookup; may exist directly on some cabinets.

        # WAT per-bucket meters: when WAT is active but a bucket key is absent, treat as 0.
        _wat_in_bucket_keys = {
            "00A0": "watcashableinamt",
            "00A2": "watnoncashinamt",
            "00A4": "watpromoinamt",
        }
        _wat_out_bucket_keys = {
            "00B8": "watcashableoutamt",
            "00BA": "watnoncashoutamt",
            "00BC": "watpromooutamt",
        }
        if code in _wat_in_bucket_keys:
            nk = _wat_in_bucket_keys[code]
            if nk in machine_state:
                return str(machine_state[nk]).strip()
            if "watcashableinamt" in machine_state or "wattransferincnt" in machine_state:
                return "0"
        if code in _wat_out_bucket_keys:
            nk = _wat_out_bucket_keys[code]
            if nk in machine_state:
                return str(machine_state[nk]).strip()
            if "watcashableinamt" in machine_state or "wattransferincnt" in machine_state:
                return "0"

        # Denomination scaling:
        # - Machine-side meters in gm2* are often stored in base units (e.g., cents, x100).
        # - SAS 6F values are typically displayed/compared in credits.
        # Scale only monetary meters; leave counters (e.g., gamesPlayed) untouched.
        MONETARY_SAS_CODES: set[str] = {"0000", "0001", "0002", "0003", "0004", "001A", "001C"}

        def scale_machine_value_for_code(code: str, raw: str) -> str:
            s = (raw or "").strip()
            if not s:
                return ""
            if not s.isdigit():
                return s
            if code not in MONETARY_SAS_CODES:
                return s
            v = int(s)
            adjusted = v / 100.0
            # Always keep 2 fractional digits so comparisons remain stable
            # (e.g., 12600 cents -> "126.00", not "126").
            return f"{adjusted:.2f}"

        # Shared bucket override: some Goldclub cabinets store Hand Paid / Cancelled variants
        # under the same XML meter key ("cancelledcredits").
        if code in {"0003", "0004", "0023"}:
            shared = machine_state.get("cancelledcredits")
            if shared is not None and str(shared).strip() != "":
                return scale_machine_value_for_code(code, str(shared))

        for a in aliases:
            nk = norm_key(a)
            if not nk:
                continue
            v = machine_state.get(nk)
            if v is not None:
                return scale_machine_value_for_code(code, str(v))
        # No match found in XML/state.
        if code == "0017" and fallback_1000 is not None:
            return fallback_1000
        return None

    def emergency_lookup_value_for_sas_code_from_logs(self, scan_root: str, sas_code: str) -> str:
        """
        If gm2u did not yield a value, scan SlotLog accounting tokens for any alias key.
        """
        code = (sas_code or "").strip().upper()
        aliases = SAS_6F_METER_ALIASES.get(code, [])
        if not aliases:
            return ""
        regs = self._latest_accounting_registers_from_logs(scan_root)

        def norm_key(k: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", (k or "").lower())

        regs_norm: dict[str, object] = {norm_key(k): v for k, v in regs.items() if norm_key(k)}
        for a in aliases:
            nk = norm_key(a)
            if nk in regs_norm:
                raw = self._normalize_gm2u_money_value(nk, regs_norm[nk])
                # Apply the same denomination scaling policy as gm2* values.
                MONETARY_SAS_CODES: set[str] = {"0000", "0001", "0002", "0003", "0004", "001A", "001C"}
                if code in MONETARY_SAS_CODES and raw.isdigit():
                    adjusted = int(raw) / 100.0
                    return f"{adjusted:.2f}"
                return raw
        return ""

    def emergency_lookup_meter_value_from_logs(self, scan_root: str, meter_id: str) -> str:
        """
        Secondary per-meter lookup for the paste workflow when gm2u did not yield a value.

        This reuses the SlotLog ``Accounting::UpdateRegisters`` token extraction and maps:
        - meter_id byte (e.g. ``00``) -> SAS 16-bit code (e.g. ``0000``) -> SAS_TO_XML meterName key.
        """
        mid = (meter_id or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}", mid):
            return ""
        code = _SAS_ID_BYTE_TO_CODE.get(mid)
        if not code:
            return ""
        meta = SAS_TO_XML.get(code.upper())
        if not meta:
            return ""
        _display, xml_key = meta
        regs = self._latest_accounting_registers_from_logs(scan_root)
        v = regs.get((xml_key or "").lower())
        if v is None:
            return ""
        raw = self._normalize_gm2u_money_value(xml_key, v)
        # For 1-byte meter IDs we only know a small mapping; scale monetary ids only.
        # (00/01/02/03/04 are money-like; others are typically counters.)
        MONETARY_IDS: set[str] = {"00", "01", "02", "03", "04"}
        if mid in MONETARY_IDS and raw.isdigit():
            adjusted = int(raw) / 100.0
            if adjusted.is_integer():
                return str(int(adjusted))
            return f"{adjusted:.2f}"
        return raw

    def _normalize_gm2u_money_value(self, norm_key: str, v: object) -> str:
        """
        Normalize gm2u / log meter values into a raw integer string.

        - If the value contains a decimal point (e.g. ``29687.94``), convert to integer cents: ``2968794``.
        - If the value is already an integer (occurrence counts or cents), keep it (strip leading zeros).
        """
        _ = norm_key  # retained for call-site compatibility
        s = str(v).strip()
        if not s:
            return "0"

        if "." in s:
            try:
                return str(int(round(float(s) * 100)))
            except ValueError:
                return s

        if s.startswith("-"):
            body = s[1:].lstrip("0") or "0"
            return "-" + body
        return s.lstrip("0") or "0"

    def save_session_state(
        self,
        *,
        log_directory: str,
        connection_mode: str = "local",
        remote_ip: str | None = None,
    ) -> None:
        """Persist last scan path, software version, and incidents for restart."""
        path = _last_session_json_path()
        incidents = list(self._all)
        if len(incidents) > _MAX_SESSION_INCIDENTS:
            incidents = incidents[:_MAX_SESSION_INCIDENTS]
        payload = {
            "format_version": _LAST_SESSION_FORMAT_VERSION,
            "log_directory": (log_directory or "").strip(),
            "connection_mode": (connection_mode or "local").strip().lower(),
            "remote_ip": (remote_ip or "").strip() or None,
            "product_name": self.current_product_name,
            "software_version": self.current_software_version,
            "incidents": [_incident_to_dict(i) for i in incidents],
        }
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Could not write %s: %s", path, e)

    def load_session_state(self) -> dict[str, Any] | None:
        """
        Load ``last_session.json`` and restore incidents into this view model.

        Returns UI hints (log path, connection) for the main window, or ``None``.
        """
        path = _last_session_json_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Session load skipped: %s", e)
            return None
        if int(data.get("format_version") or 0) != _LAST_SESSION_FORMAT_VERSION:
            return None
        raw_list = data.get("incidents")
        if not isinstance(raw_list, list):
            return None
        incidents: list[Incident] = []
        for item in raw_list:
            if isinstance(item, dict):
                try:
                    incidents.append(_incident_from_dict(item))
                except (TypeError, ValueError):
                    continue
        if not incidents:
            return None
        pn_raw = data.get("product_name")
        pn_stored = str(pn_raw).strip() if pn_raw else None
        sv_raw = data.get("software_version")
        stored_sv = str(sv_raw).strip() if sv_raw else None
        self._apply_restored_session_incidents(
            incidents,
            software_version=stored_sv,
            product_name=pn_stored,
        )
        return {
            "log_directory": str(data.get("log_directory") or "").strip(),
            "connection_mode": str(data.get("connection_mode") or "local").strip().lower(),
            "remote_ip": data.get("remote_ip"),
        }

    def _apply_restored_session_incidents(
        self,
        incidents: list[Incident],
        *,
        software_version: str | None,
        product_name: str | None = None,
    ) -> None:
        self._all = list(incidents)
        self._filtered.clear()
        self._display_rows.clear()
        self._filter_seq += 1
        self._logical_incident_keys = {_logical_incident_key(i) for i in incidents}
        pn_stored = (product_name or "").strip() or None
        sv_stored = (software_version or "").strip() or None
        if pn_stored and sv_stored and (
            sv_stored.startswith("v") or re.match(r"^\d+\.\d+", sv_stored)
        ):
            self.current_product_name = pn_stored
            self.current_software_version = (
                sv_stored if sv_stored.startswith("v") else f"v{sv_stored}"
            )
        elif sv_stored:
            p_parsed, c_parsed = parse_product_core_from_build_string(sv_stored)
            self.current_product_name = pn_stored or p_parsed
            self.current_software_version = c_parsed
        else:
            self.current_product_name = pn_stored
            self.current_software_version = None
        self._critical_count = sum(1 for i in incidents if i.severity == "CRITICAL")
        last_game = "—"
        best_ts: datetime | None = None
        for i in incidents:
            if i.timestamp is None:
                continue
            if best_ts is None or i.timestamp > best_ts:
                best_ts = i.timestamp
                last_game = i.game or last_game
        if last_game and last_game != "—":
            self._active_game_name = last_game
            self.active_game_changed.emit(last_game)
        self._rebuild_filtered()
        self.filter_rebuilt.emit()
        self.stats_changed.emit()
        self.state_nodes_changed.emit()

    def scan_environment_fingerprint(self) -> EnvFingerprint | None:
        """Aggregated env fingerprint from parsed logs (LogDaemon Spawning lines, etc.)."""
        if not (self._env_app or self._env_clr or self._env_os):
            return None
        return EnvFingerprint(self._env_app, self._env_clr, self._env_os)

    def enrich_environment_fingerprint(self, scan_root: str | None) -> bool:
        """Fill missing CLR/OS from sibling subsystem logs (e.g. ``BiOS2``).

        ``GoldClub.Logging.LogDaemon`` truncates the spawn banner, so CLR/OS are often
        absent from the primary scan root. This does ONE bounded log read per scan
        (guarded by ``_env_enriched``) — never querying the local machine's OS — so the
        values reflect the EGM and there is no per-incident performance cost.

        Returns True only if it actually populated a previously-missing value.
        """
        if self._env_enriched:
            return False
        self._env_enriched = True
        if self._env_clr and self._env_os:
            return False
        try:
            fp = extract_env_fingerprint_from_logtree(scan_root) if scan_root else None
        except Exception:  # noqa: BLE001 — enrichment is best-effort, never fatal
            fp = None
        if fp is None:
            return False
        # Only CLR/OS: the banner's version is the subsystem (e.g. BiOS2) component
        # version, not the game app — App stays sourced from the product build version.
        changed = False
        if fp.clr_version and not self._env_clr:
            self._env_clr = fp.clr_version
            changed = True
        if fp.os_version and not self._env_os:
            self._env_os = fp.os_version
            changed = True
        return changed

    def environment_fingerprint_lines(self) -> list[str]:
        """Human-readable lines for the incident inspector header."""
        sv = (self.current_software_version or "").strip()
        if not (self._env_app or self._env_clr or self._env_os or sv):
            return [
                "Environment fingerprint (this scan): not detected "
                "(scan includes ``GoldClub.Logging.LogDaemon`` logs for ``Spawning v…``).",
            ]
        # App prefers the product build version; CLR/OS come from the spawn banner
        # (enriched once from a sibling subsystem log when missing from the scan root).
        if sv:
            pn = (self.current_product_name or "").strip() or DEFAULT_PRODUCT_NAME
            app_disp = format_product_build_version(pn, sv)
        else:
            app_disp = self._env_app or "—"
        return [
            "Environment fingerprint (this scan):",
            f"  App:  {app_disp}",
            f"  CLR:  {self._env_clr or '—'}",
            f"  OS:   {self._env_os or '—'}",
        ]

    def is_scanning(self) -> bool:
        return self._scanning

    def add_bookmark(self, incident_id: str, note: str) -> None:
        self._bookmarks[incident_id] = (note or "").strip()
        self.bookmarks_changed.emit()

    def remove_bookmark(self, incident_id: str) -> None:
        if self._bookmarks.pop(incident_id, None) is not None:
            self.bookmarks_changed.emit()

    def get_bookmark_note(self, incident_id: str) -> str | None:
        return self._bookmarks.get(incident_id)

    def is_bookmarked(self, incident_id: str) -> bool:
        return incident_id in self._bookmarks

    def bookmark_notes_map(self) -> dict[str, str]:
        """Snapshot for CSV export (incident id → note)."""
        return dict(self._bookmarks)

    def get_current_machine_info(self) -> dict[str, Any]:
        """Cabinet / session context for case packs (UI may add IP, hostname, drift)."""
        return {
            "cabinet_id": self._logged_machine_id,
            "active_game": self._active_game_name,
            "session_active": self.is_session_active(),
        }

    def has_session_time_slice(self) -> bool:
        return self._time_slice_start is not None and self._time_slice_end is not None

    def set_time_slice(self, start: object, end: object) -> None:
        """Filter table to log timestamps in ``[start, end]`` (inclusive). Pass ``None, None`` to clear."""
        if start is None and end is None:
            self._time_slice_start = None
            self._time_slice_end = None
        elif isinstance(start, datetime) and isinstance(end, datetime):
            a, b = start, end
            if a > b:
                a, b = b, a
            self._time_slice_start = _naive_utc_if_needed(a)
            self._time_slice_end = _naive_utc_if_needed(b)
        else:
            return
        self._rebuild_filtered()

    def _session_brush_epoch_bounds(self) -> tuple[float | None, float | None]:
        if self._time_slice_start is None or self._time_slice_end is None:
            return None, None
        return (
            self._time_slice_start.timestamp(),
            self._time_slice_end.timestamp(),
        )

    def incidents_for_session_timeline(self) -> list[Incident]:
        """Incidents matching search, session, state-timeline window, and quick chips — not session brush."""
        needle = self._filter_text.strip().lower()
        sess_ts = self.session_start_epoch()
        qf = self._quick_filter_snapshot()
        return [
            inc
            for inc in self._all
            if _session_match(inc, sess_ts)
            and quick_filter_match(inc, qf)
            and _timeline_match(inc, self._timeline_t0, self._timeline_t1)
            and (not needle or needle in _incident_blob(inc))
        ]

    def filter_text(self) -> str:
        return self._filter_text

    def state_nodes(self) -> list[Any]:
        """All state timeline nodes from the scan (unfiltered). Use for DB persist, etc."""
        return _sort_state_nodes_chronologically(list(self._state_nodes))

    def state_nodes_for_timeline(self) -> list[Any]:
        """
        Nodes shown on the State Timeline tab.

        When a recording session is active, drops segments whose start time is
        strictly before ``session_start_time`` (same rule as incident filtering).
        """
        if self._session_start_time is None:
            return _sort_state_nodes_chronologically(list(self._state_nodes))
        t0 = self.session_start_epoch()
        if t0 is None:
            return _sort_state_nodes_chronologically(list(self._state_nodes))
        out: list[Any] = []
        for n in self._state_nodes:
            sk = getattr(n, "timestamp_sort_key", None)
            if sk is None:
                out.append(n)
                continue
            if sk >= t0:
                out.append(n)
        return _sort_state_nodes_chronologically(out)

    def timeline_filter_active(self) -> bool:
        return self._timeline_t0 is not None and self._timeline_t1 is not None

    def session_start_time(self) -> datetime | None:
        """UTC start of the live recording window, or ``None`` if not recording."""
        return self._session_start_time

    def is_session_active(self) -> bool:
        return self._session_start_time is not None

    def session_start_epoch(self) -> float | None:
        if self._session_start_time is None:
            return None
        return self._session_start_time.timestamp()

    def session_active_duration_hms(self) -> str | None:
        """
        Elapsed time ``HH:MM:SS`` since session start, or ``None`` if inactive.
        Used for case snapshot metadata while recording.
        """
        if self._session_start_time is None:
            return None
        secs = int(
            (datetime.now(timezone.utc) - self._session_start_time).total_seconds()
        )
        secs = max(0, secs)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def start_session(self) -> None:
        self._session_start_time = datetime.now(timezone.utc)
        self.session_state_changed.emit()
        self._rebuild_filtered()
        self.state_nodes_changed.emit()

    def stop_session(self) -> None:
        if self._session_start_time is None:
            return
        self._session_start_time = None
        self.session_state_changed.emit()
        self._rebuild_filtered()
        self.state_nodes_changed.emit()

    def _quick_filter_snapshot(self) -> QuickFilterSnapshot:
        return QuickFilterSnapshot(
            critical=self._qf_critical,
            warn=self._qf_warn,
            math_fails=self._qf_math_fails,
            drift=self._qf_drift,
            known_issues=self._qf_known_issues,
        )

    def quick_filter_critical_active(self) -> bool:
        return self._qf_critical

    def quick_filter_warn_active(self) -> bool:
        return self._qf_warn

    def quick_filter_math_fails_active(self) -> bool:
        return self._qf_math_fails

    def quick_filter_drift_active(self) -> bool:
        return self._qf_drift

    def quick_filter_known_issues_active(self) -> bool:
        return self._qf_known_issues

    def set_quick_filter_critical(self, active: bool) -> None:
        if self._qf_critical == active:
            return
        self._qf_critical = active
        self._rebuild_filtered()

    def set_quick_filter_warn(self, active: bool) -> None:
        if self._qf_warn == active:
            return
        self._qf_warn = active
        self._rebuild_filtered()

    def set_quick_filter_math_fails(self, active: bool) -> None:
        if self._qf_math_fails == active:
            return
        self._qf_math_fails = active
        self._rebuild_filtered()

    def set_quick_filter_drift(self, active: bool) -> None:
        if self._qf_drift == active:
            return
        self._qf_drift = active
        self._rebuild_filtered()

    def set_quick_filter_known_issues(self, active: bool) -> None:
        if self._qf_known_issues == active:
            return
        self._qf_known_issues = active
        self._rebuild_filtered()

    def clear_quick_filter_chips(self) -> None:
        """Turn off all quick-filter chips and rebuild indices (used by Esc in search box)."""
        if not (
            self._qf_critical
            or self._qf_warn
            or self._qf_math_fails
            or self._qf_drift
            or self._qf_known_issues
        ):
            return
        self._qf_critical = False
        self._qf_warn = False
        self._qf_math_fails = False
        self._qf_drift = False
        self._qf_known_issues = False
        self._rebuild_filtered()
        self.quick_filters_changed.emit()

    def validation_summary_for_snapshot(self, exported: list[Incident]) -> dict[str, Any]:
        """
        Case zip metadata: combine full-scan roulette math stats with whether the
        exported (filtered) rows include a discrepancy.
        """
        fail_export = any(
            getattr(i, "error_type", "") == "CRITICAL MATH DISCREPANCY" for i in exported
        )
        fail_scan = self._val_discrepancies > 0
        if fail_export or fail_scan:
            status = "FAIL"
        elif self._val_sessions_validated > 0:
            status = "PASS"
        else:
            status = "SKIPPED"
        return {
            "validation_status": status,
            "roulette_sessions_validated": self._val_sessions_validated,
            "roulette_sessions_passed_math": self._val_sessions_passed,
            "roulette_math_discrepancy_count": self._val_discrepancies,
            "exported_math_discrepancy_rows": sum(
                1
                for i in exported
                if getattr(i, "error_type", "") == "CRITICAL MATH DISCREPANCY"
            ),
        }

    def set_timeline_window(self, t0: float, t1: float) -> None:
        lo, hi = min(t0, t1), max(t0, t1)
        self._timeline_t0 = lo
        self._timeline_t1 = max(hi, lo + 1e-3)
        self._rebuild_filtered()

    def clear_timeline_filter(self) -> None:
        self._timeline_t0 = None
        self._timeline_t1 = None
        self._rebuild_filtered()

    def thread_pool(self) -> QThreadPool:
        return self._pool

    def selected_incident(self) -> Incident | None:
        return self._selected_incident

    def set_selected_incident(self, incident: Incident | None) -> None:
        if self._selected_incident is incident:
            return
        self._selected_incident = incident
        self.selected_incident_changed.emit(incident)

    # --- scan lifecycle ---
    def clear(self) -> None:
        self._all.clear()
        self._filtered.clear()
        self._display_rows.clear()
        self._filter_seq += 1
        self._files_scanned = 0
        self._files_total = 0
        self._critical_count = 0
        self._active_game_name = "—"
        self.active_game_changed.emit("—")
        had_selection = self._selected_incident is not None
        self._selected_incident = None
        self._state_nodes.clear()
        self._timeline_t0 = None
        self._timeline_t1 = None
        self._time_slice_start = None
        self._time_slice_end = None
        self._val_sessions_validated = 0
        self._val_sessions_passed = 0
        self._val_discrepancies = 0
        self._logged_machine_id = None
        self._env_app = None
        self._env_clr = None
        self._env_os = None
        self._env_enriched = False
        self._event_ram_attempted.clear()
        self._live_ram_pending.clear()
        self._onehand_version = None
        self.current_product_name = None
        self.current_software_version = None
        self._software_version_product_raw = None
        self._logical_incident_keys.clear()
        had_session = self._session_start_time is not None
        self._session_start_time = None
        self._timeline_dirty = False
        self._qf_critical = False
        self._qf_warn = False
        self._qf_math_fails = False
        self._qf_drift = False
        self._qf_known_issues = False
        if self._bookmarks:
            self._bookmarks.clear()
            self.bookmarks_changed.emit()
        self.filter_rebuilt.emit()
        self.quick_filters_changed.emit()
        if had_session:
            self.session_state_changed.emit()
        self.state_nodes_changed.emit()
        self.stats_changed.emit()
        if had_selection:
            self.selected_incident_changed.emit(None)

    def set_scan_totals(self, total_files: int) -> None:
        self._files_total = total_files
        self.stats_changed.emit()

    def set_scanning(self, active: bool) -> None:
        if self._scanning == active:
            return
        self._scanning = active
        if not active and self._timeline_dirty:
            self._timeline_dirty = False
            self.state_nodes_changed.emit()
        self.scan_state_changed.emit(active)

    def append_file_results(self, batch: list[Incident] | ParseResult) -> None:
        """Merge incidents (and optional state timeline) from one parsed log file."""
        file_last_theme: str | None = None
        if isinstance(batch, ParseResult):
            incs = batch.incidents
            nodes = batch.state_nodes
            vs = batch.validation_stats
            file_last_theme = batch.last_active_theme
            if batch.machine_id and not self._logged_machine_id:
                self._logged_machine_id = batch.machine_id
            if batch.env_fingerprint:
                ef = batch.env_fingerprint
                if ef.app_version:
                    self._env_app = ef.app_version
                if ef.clr_version:
                    self._env_clr = ef.clr_version
                if ef.os_version:
                    self._env_os = ef.os_version
            if batch.onehand_version and not self._onehand_version:
                self._onehand_version = batch.onehand_version
        else:
            incs = batch
            nodes = []
            vs = None

        self._files_scanned += 1
        if vs:
            self._val_sessions_validated += int(vs.get("sessions_validated", 0))
            self._val_sessions_passed += int(vs.get("sessions_passed", 0))
            self._val_discrepancies += int(vs.get("discrepancies", 0))
        if nodes:
            self._state_nodes.extend(nodes)
            if self._scanning:
                self._timeline_dirty = True
            else:
                self.state_nodes_changed.emit()

        deduped: list[Incident] = []
        for inc in incs:
            k = _logical_incident_key(inc)
            if k in self._logical_incident_keys:
                continue
            self._logical_incident_keys.add(k)
            deduped.append(inc)
        incs = deduped

        if not incs:
            if file_last_theme:
                self._set_active_game_if_changed(file_last_theme)
            self.stats_changed.emit()
            return

        old_filtered_len = len(self._filtered)
        start_all = len(self._all)
        self._all.extend(incs)

        for inc in incs:
            if inc.severity == "CRITICAL":
                self._critical_count += 1
            self._set_active_game_if_changed(inc.game)
        if file_last_theme:
            self._set_active_game_if_changed(file_last_theme)

        for i, inc in enumerate(incs, start=start_all):
            if self._passes_incident_filters(inc):
                self._filtered.append(i)

        self.stats_changed.emit()
        self._finalize_filtered_growth(old_filtered_len)
        self._schedule_remote_ram_for_incidents(incs)

    def append_live_incidents(
        self,
        batch: list[Incident],
        *,
        active_game_hint: str | None = None,
    ) -> None:
        """Append incidents from live tail without incrementing scan file counters."""
        if active_game_hint:
            self._set_active_game_if_changed(active_game_hint)
        if not batch:
            return

        deduped: list[Incident] = []
        for inc in batch:
            k = _logical_incident_key(inc)
            if k in self._logical_incident_keys:
                continue
            self._logical_incident_keys.add(k)
            deduped.append(inc)
        batch = deduped
        if not batch:
            return

        old_filtered_len = len(self._filtered)
        start_all = len(self._all)
        self._all.extend(batch)

        for inc in batch:
            if inc.severity == "CRITICAL":
                self._critical_count += 1
            self._set_active_game_if_changed(inc.game)

        for i, inc in enumerate(batch, start=start_all):
            if self._passes_incident_filters(inc):
                self._filtered.append(i)

        self.stats_changed.emit()
        self._finalize_filtered_growth(old_filtered_len)

        for i, inc in enumerate(batch, start=start_all):
            if inc.severity != "CRITICAL":
                continue
            if not self._passes_incident_filters(inc):
                continue
            try:
                fr = self._filtered.index(i)
            except ValueError:
                continue
            dr = self.display_row_index_for_filtered_index(fr)
            self.live_critical_filtered.emit(dr, inc)

        self._schedule_remote_ram_for_incidents(batch, live=True)

    # --- filter ---
    def set_filter_text(self, text: str) -> None:
        self._filter_text = text
        self._debounce.start()

    def _run_filter_pass(self) -> None:
        self._filter_seq += 1
        seq = self._filter_seq
        needle = self._filter_text.strip().lower()

        if len(self._all) >= ASYNC_FILTER_THRESHOLD:
            self._set_filter_busy(True)
            b0, b1 = self._session_brush_epoch_bounds()
            schedule_filter_build(
                self._pool,
                self._all,
                self._filter_text,
                self._filter_emitter,
                seq,
                self._timeline_t0,
                self._timeline_t1,
                b0,
                b1,
                self.session_start_epoch(),
                self._quick_filter_snapshot(),
            )
        else:
            self._apply_filter_sync(needle)
            self._set_filter_busy(False)
            self.filter_rebuilt.emit()

    def _rebuild_filtered(self) -> None:
        """Rebuild indices immediately (timeline / filter sync)."""
        self._filter_seq += 1
        seq = self._filter_seq
        needle = self._filter_text.strip().lower()
        if len(self._all) >= ASYNC_FILTER_THRESHOLD:
            self._set_filter_busy(True)
            b0, b1 = self._session_brush_epoch_bounds()
            schedule_filter_build(
                self._pool,
                self._all,
                self._filter_text,
                self._filter_emitter,
                seq,
                self._timeline_t0,
                self._timeline_t1,
                b0,
                b1,
                self.session_start_epoch(),
                self._quick_filter_snapshot(),
            )
        else:
            self._apply_filter_sync(needle)
            self._set_filter_busy(False)
            self.filter_rebuilt.emit()

    def _apply_filter_sync(self, needle: str) -> None:
        from gui.filter_runnable import _incident_blob

        sess_ts = self.session_start_epoch()
        qf = self._quick_filter_snapshot()
        b0, b1 = self._session_brush_epoch_bounds()
        self._filtered = [
            i
            for i, inc in enumerate(self._all)
            if _session_match(inc, sess_ts)
            and quick_filter_match(inc, qf)
            and _timeline_match(inc, self._timeline_t0, self._timeline_t1)
            and _brush_time_slice_match(inc, b0, b1)
            and (not needle or needle in _incident_blob(inc))
        ]
        self._rebuild_display_rows()

    def _on_async_filter_done(self, indices: list[int], seq: int) -> None:
        if seq != self._filter_seq:
            return
        self._filtered = indices
        self._rebuild_display_rows()
        self._set_filter_busy(False)
        self.filter_rebuilt.emit()

    def _set_filter_busy(self, busy: bool) -> None:
        if self._filter_busy == busy:
            return
        self._filter_busy = busy
        self.filter_busy_changed.emit(busy)
