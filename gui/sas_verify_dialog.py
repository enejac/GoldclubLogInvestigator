"""Paste-based SAS 6F verification against machine accounting state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from types import SimpleNamespace
import sys
import threading
import traceback

from PySide6.QtCore import QObject, QSettings, QThread, QThreadPool, Signal, Qt, QPoint
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QMenu,
    QMenuBar,
    QPushButton,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

# --- EGM currency / dollar display (100 credits = $1 on USD cabinets) ---
SAS_VERIFY_MONETARY_CODES = frozenset({
    "0000", "0001", "0002", "0003", "0004", "000B",
    "0015", "0016", "0017", "0018", "001C", "001D", "001F", "0020", "0023",
    "006E", "0080", "0082", "0084", "0086", "0088",
    "00A0", "00A2", "00A4", "00B8", "00BA", "00BC",
})
CREDITS_PER_DOLLAR = 100
RAW_VALUE_ROLE = int(Qt.ItemDataRole.UserRole)
_CURRENCY_LINE_PATTERNS = (
    (re.compile(r"\$\s*[\d,]+\.\d{2}"), "$", "USD"),
    (re.compile(r"\$\s*[\d,]+"), "$", "USD"),
)


@dataclass(frozen=True, slots=True)
class EgmCurrency:
    symbol: str = "$"
    code: str = "USD"
    credits_per_dollar: int = CREDITS_PER_DOLLAR


def _is_monetary_sas_code(code: str) -> bool:
    return (code or "").strip().upper() in SAS_VERIFY_MONETARY_CODES


def _credits_to_dollar_amount(raw: str):
    s = (raw or "").strip()
    if not s:
        return 0.0
    if "." in s:
        try:
            # Machine gm2u values are already credits/100 (e.g. "126.00" = $126).
            return float(s)
        except ValueError:
            return None
    if not re.fullmatch(r"-?\d+", s):
        return None
    return int(s) / float(CREDITS_PER_DOLLAR)


def _format_dollar_amount(amount: float, *, symbol: str) -> str:
    if amount == int(amount):
        return f"{symbol}{int(amount):,}"
    return f"{symbol}{amount:,.2f}"


def _format_meter_value_display(
    raw: str,
    *,
    meter_code: str,
    currency: EgmCurrency,
    show_dollars: bool,
) -> str:
    text = (raw or "").strip()
    if not text:
        return "0"
    if not show_dollars and _is_monetary_sas_code(meter_code) and "." in text:
        # gm2u machine values arrive as credits/100 (e.g. "101.00" = 10100 credits).
        try:
            text = str(int(float(text) * float(CREDITS_PER_DOLLAR)))
        except ValueError:
            text = text.replace(".", "")
    if show_dollars and _is_monetary_sas_code(meter_code):
        dollars = _credits_to_dollar_amount(text)
        if dollars is not None:
            return _format_dollar_amount(dollars, symbol=currency.symbol)
    return text


def _detect_egm_currency(scan_root: str, vm=None) -> EgmCurrency:
    root_raw = (scan_root or "").strip()
    if not root_raw:
        return EgmCurrency()
    paths: list[str] = []
    if vm is not None and hasattr(vm, "_slotlog_candidate_paths"):
        try:
            paths = list(vm._slotlog_candidate_paths(Path(root_raw)))
        except Exception:
            paths = []
    if not paths:
        try:
            root = Path(root_raw)
            for pat in ("**/*SlotLog*.log", "**/OneHand*.log", "**/SlotLog/**/*.log"):
                paths.extend(str(p) for p in root.glob(pat))
        except OSError:
            paths = []
    def _mtime(p: str) -> float:
        try:
            return Path(p).stat().st_mtime
        except OSError:
            return 0.0
    for path in sorted(set(paths), key=_mtime, reverse=True)[:8]:
        lines: list[str] = []
        if vm is not None and hasattr(vm, "_tail_lines_from_file"):
            try:
                lines = list(vm._tail_lines_from_file(Path(path)))
            except Exception:
                lines = []
        if not lines:
            try:
                lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
            except OSError:
                continue
        for line in reversed(lines):
            if "$" not in line and "Cashless" not in line and "Handpay" not in line:
                continue
            for pat, sym, code in _CURRENCY_LINE_PATTERNS:
                if pat.search(line):
                    return EgmCurrency(symbol=sym, code=code)
    return EgmCurrency()

# IMPORTANT: do not import cabinet/network loaders at module-import time.
# Worker threads import them inside `CompareWorker.run()` to avoid pulling in any UI-linked globals.


# SAS codes whose Machine Value is COMPUTED in code from more than one cabinet meter
# (i.e. not a single "true" raw meter). Maps code -> human-readable formula for tooltips.
# Keep in sync with get_gm2u_value_for_sas_code() special cases in gui/view_model.py.
DERIVED_SAS_CODES: dict[str, str] = {
    "0004": "0003 HandPaidCancelled + 0016 TicketOut + 0018 CashlessOut",
    "0007": "0005 GamesPlayed - 0006 GamesWon",
    "0017": "WAT in: cashable + restricted (nonCash) + promo",
    "0018": "WAT out: cashable + restricted (nonCash) + promo",
}

# Colors for the Meter Name column.
_TRUE_METER_COLOR = "#0a7d6b"     # teal: direct single-source cabinet meter (highlighted)
_DERIVED_METER_COLOR = "#b8860b"  # dark goldenrod: value computed/aggregated in code

# Verify table column indices (keep copy/refresh logic in sync).
COL_6F_CODE = 0
COL_WIRE_ID = 1
COL_IGT_POLL = 2
COL_IGT_METER = 3
COL_METER_NAME = 4
COL_SAS_6F_VALUE = 5
COL_SAS_2F_VALUE = 6
COL_MACHINE_VALUE = 7
COL_STATUS = 8
COL_COUNT = 9

# Back-compat alias used in a few call sites.
COL_SAS_VALUE = COL_SAS_6F_VALUE

# Hex columns wrapped as Excel text literals on copy (preserve leading zeros).
_EXCEL_TEXT_COLS = frozenset({COL_6F_CODE, COL_WIRE_ID, COL_IGT_POLL, COL_IGT_METER})

# Optional column visibility (View -> Columns submenu).
_VERIFY_TABLE_COLUMNS: tuple[tuple[int, str], ...] = (
    (COL_6F_CODE, "6F Code"),
    (COL_WIRE_ID, "Wire ID"),
    (COL_IGT_POLL, "IGT $2F"),
    (COL_IGT_METER, "IGT Meter"),
    (COL_METER_NAME, "Meter Name"),
    (COL_SAS_6F_VALUE, "SAS (6F)"),
    (COL_SAS_2F_VALUE, "SAS ($2F)"),
    (COL_MACHINE_VALUE, "Machine"),
    (COL_STATUS, "Status"),
)
_SAS_VERIFY_SETTINGS_GROUP = "SasVerifyDialog"
_KEY_COM_PORT = "com_port"
_KEY_COM_BAUD = "com_baud"
# Bump when default column layout changes so saved prefs reset once.
_SAS_VERIFY_COLUMN_PREFS_VERSION = 2
_KEY_COLUMN_PREFS_VERSION = "column_prefs_version"
_DEFAULT_HIDDEN_VERIFY_COLUMNS = frozenset({COL_WIRE_ID, COL_SAS_2F_VALUE})


def default_verify_column_visible(col: int) -> bool:
    """Default column visibility when no saved preference exists."""
    return col not in _DEFAULT_HIDDEN_VERIFY_COLUMNS


def visible_verify_table_headers(
    *,
    show_dollars: bool,
    currency_symbol: str,
    column_visible: dict[int, bool],
) -> list[str]:
    """Header labels for copy/export, honoring optional column visibility."""
    sym = currency_symbol or "$"
    if show_dollars:
        sas_6f_h, sas_2f_h, mac_h = (
            f"SAS 6F ({sym})",
            f"SAS $2F ({sym})",
            f"Machine ({sym})",
        )
    else:
        sas_6f_h, sas_2f_h, mac_h = ("SAS (6F)", "SAS ($2F)", "Machine")
    all_headers = [
        "6F Code",
        "Wire ID",
        "IGT $2F",
        "IGT Meter",
        "Meter Name",
        sas_6f_h,
        sas_2f_h,
        mac_h,
        "Status",
    ]
    return [all_headers[col] for col, _ in _VERIFY_TABLE_COLUMNS if column_visible.get(col, False)]


def decode_sas_bcd(bcd_bytes: bytes) -> str:
    """
    Decode SAS meter BCD payload bytes to an integer credit string.

    BCD bytes are rendered as hex digits then read as a base-10 integer
    (same rules as :func:`network.meter_comparator._meter_value_to_int`).
    """
    from network.meter_comparator import _meter_value_to_int

    return str(_meter_value_to_int((bcd_bytes or b"").hex().upper()))


def wire_meter_code_to_6f_verify_code(wire_code: str) -> str:
    """Wire-order SAS code (``1800``) -> verify-table id (``0018``)."""
    w = (wire_code or "").strip().upper()
    if len(w) != 4 or not re.fullmatch(r"[0-9A-F]{4}", w):
        return w
    return f"{w[2:4]}{w[0:2]}"


@dataclass(frozen=True, slots=True)
class Sas6FRow:
    meter_id: str  # 4 hex chars, e.g. "0005"
    sas_value_text: str  # e.g. "90673"


def parse_sas_6f_paste(text: str) -> list[Sas6FRow]:
    """
    Parse pasted SAS RX<= 6F lines (multi-meter format).

    Uses :mod:`network.sas_parser` for byte-accurate framing; maps wire-order meter
    codes to the verify-table ids (``1800`` -> ``0018``). Later RX lines override
    earlier values for the same meter.
    """
    from network.sas_parser import parse_rx_response_ordered

    merged: dict[str, str] = {}
    for chunk in re.findall(r"RX<=\s*([0-9a-fA-F ]+)", text or ""):
        line = f"RX<= {chunk.strip()}"
        if not re.search(r"\b6F\b", line, re.I):
            continue
        for wire_code, value in parse_rx_response_ordered(line):
            verify_code = wire_meter_code_to_6f_verify_code(wire_code)
            merged[verify_code] = str(value)
    return [Sas6FRow(meter_id=code, sas_value_text=val) for code, val in merged.items()]


def build_verify_6f_rows_from_paste(text: str) -> list[Sas6FRow]:
    """Merge pasted 6F RX with the full verify-table meter list (stable row order)."""
    from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

    parsed = {r.meter_id.upper(): r.sas_value_text for r in parse_sas_6f_paste(text)}
    rows: list[Sas6FRow] = []
    seen: set[str] = set()
    for code in DEFAULT_6F_VERIFY_POLL_CODES:
        rid = code.upper()
        if rid in seen:
            continue
        seen.add(rid)
        rows.append(Sas6FRow(meter_id=rid, sas_value_text=parsed.get(rid, "")))
    for rid, val in parsed.items():
        if rid not in seen:
            rows.append(Sas6FRow(meter_id=rid, sas_value_text=val))
    return rows


def _hex_bytes_from_sas_line(line: str, *, direction: str) -> bytes | None:
    """Extract hex payload after ``TX>=`` or ``RX<=`` (case/space tolerant)."""
    if direction == "TX":
        m = re.search(r"(?i)TX\s*>=\s*([0-9A-F ]+)", line or "")
    else:
        m = re.search(r"(?i)RX\s*<=\s*([0-9A-F ]+)", line or "")
    if not m:
        return None
    compact = re.sub(r"\s+", "", m.group(1))
    if len(compact) < 4 or len(compact) % 2 != 0:
        return None
    try:
        return bytes.fromhex(compact)
    except ValueError:
        return None


def _igt_index_from_2f_tx(frame: bytes) -> str:
    """Meter index byte from ``TX>= Addr 2F Len Game(2) Index …``."""
    if len(frame) < 6 or frame[1] != 0x2F:
        return ""
    data_len = int(frame[2])
    if data_len < 3 or len(frame) < 3 + data_len:
        return ""
    payload = frame[3 : 3 + data_len]
    if len(payload) < 3:
        return ""
    return f"{payload[2]:02X}"


def _decode_2f_rx_frame(frame: bytes) -> tuple[str, str] | None:
    """Parse ``RX<= Addr 2F …`` → ``(igt_index, raw_value_text)``."""
    if len(frame) < 8 or frame[1] != 0x2F:
        return None
    data_len = int(frame[2])
    if data_len < 4 or len(frame) < 3 + data_len + 2:
        return None
    payload = frame[3 : 3 + data_len]
    if len(payload) < 4:
        return None
    meter_ix = f"{payload[2]:02X}"
    value_bytes = payload[3:]
    value_text = decode_sas_bcd(value_bytes) if value_bytes else "0"
    return meter_ix, value_text


def _decode_igt_first_meter_display(hex8: str) -> str:
    """IGT tester yellow-box ``First Meter = 00000100`` → credit string ``100``."""
    h = (hex8 or "").strip().upper()
    if len(h) != 8 or not re.fullmatch(r"[0-9A-F]{8}", h):
        return ""
    return decode_sas_bcd(bytes.fromhex(h))


def parse_sas_2f_paste(text: str) -> dict[str, str]:
    """
    Parse ``$2F`` meter polls from pasted SAS traffic or IGT tester output.

    Accepts:
    - ``RX<= 01 2F …`` response frames (preferred)
    - ``$2F = First Meter = 00000100`` after a matching ``TX>= 01 2F … Index``
    """
    try:
        from gui.view_model import sas_6f_code_for_igt_2f_index
    except Exception:
        return {}

    out: dict[str, str] = {}
    pending_tx_indexes: list[str] = []

    def _store(igt_index: str, value_text: str) -> None:
        code_6f = sas_6f_code_for_igt_2f_index(igt_index)
        if code_6f and value_text != "":
            out[code_6f] = value_text

    for line in (text or "").splitlines():
        first_meter = re.search(
            r"(?i)\$2F\s*=\s*First Meter\s*=\s*([0-9A-F]{8})",
            line,
        )
        if first_meter:
            value_text = _decode_igt_first_meter_display(first_meter.group(1))
            igt_ix = pending_tx_indexes.pop(0) if pending_tx_indexes else ""
            if igt_ix and value_text != "":
                _store(igt_ix, value_text)
            continue

        tx_frame = _hex_bytes_from_sas_line(line, direction="TX")
        if tx_frame is not None and len(tx_frame) >= 2 and tx_frame[1] == 0x2F:
            igt_ix = _igt_index_from_2f_tx(tx_frame)
            if igt_ix:
                pending_tx_indexes.append(igt_ix)
            continue

        if not re.search(r"(?i)RX\s*<=\s*.*\b01\s+2F\b", line):
            continue
        rx_frame = _hex_bytes_from_sas_line(line, direction="RX")
        if rx_frame is None:
            continue
        decoded = _decode_2f_rx_frame(rx_frame)
        if decoded is None:
            continue
        igt_ix, value_text = decoded
        if pending_tx_indexes and pending_tx_indexes[-1] == igt_ix:
            pending_tx_indexes.pop()
        _store(igt_ix, value_text)

    return out


class SasVerifyEmitter(QObject):
    finished = Signal(object)  # dict[str, str] or error string


def _extract_unc_host(scan_root: str) -> str:
    """
    Best-effort extraction of the host component from a UNC path:
    ``\\\\10.0.0.90\\c$\\...`` -> ``10.0.0.90``
    """
    s = (scan_root or "").strip().replace("/", "\\")
    # UNC: \\host\share\...
    # Import inside function to avoid any module-level side effects.
    from network.accounting_state_loader import extract_ip_from_path

    return extract_ip_from_path(s)


class CompareWorker(QObject):
    finished = Signal(object)  # dict[str, str] or error string
    error = Signal(str)

    def __init__(self, target_ip: str, scan_root: str) -> None:
        # CRITICAL: no parent; this object will be moved to a worker thread.
        super().__init__(None)
        self._target_ip = (target_ip or "").strip()
        self._scan_root = (scan_root or "").strip()

    def run(self) -> None:
        try:
            # Import ONLY inside the run method to avoid pulling UI-linked objects
            # during initialization or module import.
            from network.scanner_utils import is_smb_alive
            from network.accounting_state_loader import load_machine_accounting_state_pure

            # Robust IP extraction: prefer scan_root, fall back to explicit target_ip.
            host = _extract_unc_host(self._scan_root) or self._target_ip
            try:
                sys.__stdout__.write(
                    f"\n[WORKER-THREAD] Thread started (py_tid={threading.get_ident()}) "
                    f"for host='{host}' scan_root='{self._scan_root}'\n"
                )
                sys.__stdout__.flush()
            except Exception:
                pass
            if host and not is_smb_alive(host, timeout=1.0):
                try:
                    sys.__stdout__.write(f"[WORKER-THREAD] SMB Check FAILED for {host}\n")
                    sys.__stdout__.flush()
                except Exception:
                    pass
                self.error.emit(f"Connection Failed: {host}")
                self.finished.emit({})
                return
            try:
                sys.__stdout__.write(f"[WORKER-THREAD] SMB Alive. Handing off to pure loader.\n")
                sys.__stdout__.flush()
            except Exception:
                pass
            state = load_machine_accounting_state_pure(self._scan_root)
            try:
                sys.__stdout__.write(f"[WORKER-THREAD] Loader finished. Found {len(state or {})} keys.\n")
                sys.__stdout__.flush()
            except Exception:
                pass
            self.finished.emit(state if isinstance(state, dict) else {})
        except Exception as e:  # noqa: BLE001
            try:
                sys.__stderr__.write(f"[WORKER-THREAD] CRITICAL CRASH: {e}\n")
                traceback.print_exc(file=sys.__stderr__)
                sys.__stderr__.flush()
            except Exception:
                pass
            self.error.emit(str(e))
            self.finished.emit({})


class MeterFetchWorker(QObject):
    finished = Signal(object)  # SasMeterFetchResult
    error = Signal(str)

    def __init__(self, *, com_port: str, com_baud: int) -> None:
        super().__init__(None)
        self._com_port = (com_port or "").strip()
        self._com_baud = int(com_baud)

    def run(self) -> None:
        try:
            from network.sas_serial_meters import fetch_meters_over_serial

            result = fetch_meters_over_serial(
                port=self._com_port,
                baud=self._com_baud,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class SasVerifyDialog(QDialog):
    def __init__(self, vm: object, pool: QThreadPool, *, scan_root: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm
        self._pool = pool
        self._scan_root = scan_root
        self._machine_state: dict[str, str] = {}
        self._compare_thread: QThread | None = None
        self._compare_worker: CompareWorker | None = None
        self._meter_fetch_thread: QThread | None = None
        self._meter_fetch_worker: MeterFetchWorker | None = None
        self._last_parsed_rows: list[Sas6FRow] = []
        self._sas_2f_values: dict[str, str] = {}
        self._currency = EgmCurrency()
        self._show_dollars = False
        self._column_actions: dict[int, QAction] = {}

        self.setWindowTitle("SAS accounting verification")
        self.resize(1180, 780)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.addWidget(self._build_view_menu_bar())
        root.addWidget(
            QLabel(
                "Get Meters reads SAS over COM, then loads cabinet values from Scan root. "
                "Or paste TX/RX and click Compare."
            )
        )
        legend = QLabel(
            f"Meter Name legend: "
            f"<b style='color:{_TRUE_METER_COLOR};'>True meter</b> (direct cabinet value) &nbsp;·&nbsp; "
            f"<i style='color:{_DERIVED_METER_COLOR};'>Derived meter</i> "
            f"(computed in code from multiple meters — hover for formula)"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(legend)

        # Allow operators to paste/override UNC paths (do not lock this field).
        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Scan root:"))
        self._scan_root_edit = QLineEdit()
        self._scan_root_edit.setText(self._scan_root or "")
        self._scan_root_edit.setReadOnly(False)
        self._scan_root_edit.setEnabled(True)
        self._scan_root_edit.setPlaceholderText("Paste UNC path here...")
        scan_row.addWidget(self._scan_root_edit, stretch=1)
        scan_row.addWidget(QLabel("COM:"))
        self._com_port_combo = QComboBox()
        self._com_port_combo.setEditable(True)
        self._com_port_combo.setMinimumWidth(120)
        self._com_port_combo.setMaximumWidth(220)
        self._com_port_combo.setToolTip(
            "Serial port for the SAS host cable (default COM4). "
            "Get Meters tries raw SAS @ 19200 first (IGT standard), then 921600. "
            "Close IGT SAS tester before fetching."
        )
        self._load_com_port_prefs()
        self._refresh_com_port_list(preserve_text=True)
        scan_row.addWidget(self._com_port_combo)
        self._btn_get_meters = QPushButton("Get Meters")
        self._btn_get_meters.setToolTip(
            "Poll extended meters over COM (IGT 6F batches), paste TX/RX, then load Machine "
            "values from Scan root (DeviceManagerData.xml on the cabinet)."
        )
        self._btn_get_meters.clicked.connect(self._on_get_meters_clicked)
        scan_row.addWidget(self._btn_get_meters)
        root.addLayout(scan_row)

        self._paste = QTextEdit()
        self._paste.setPlaceholderText("Paste TX>= / RX<= lines here…")
        self._paste.setMinimumHeight(56)
        self._paste.setMaximumHeight(160)

        self._table = QTableWidget(0, COL_COUNT)
        self._table.setHorizontalHeaderLabels(
            [
                "6F Code",
                "Wire ID",
                "IGT $2F",
                "IGT Meter",
                "Meter Name",
                "SAS (6F)",
                "SAS ($2F)",
                "Machine",
                "Status",
            ]
        )
        self._table.setColumnWidth(COL_6F_CODE, 72)
        self._table.setColumnWidth(COL_WIRE_ID, 72)
        self._table.setColumnWidth(COL_IGT_POLL, 64)
        self._table.setColumnWidth(COL_IGT_METER, 88)
        self._table.setColumnWidth(COL_METER_NAME, 280)
        self._load_column_visibility_prefs()
        self._apply_column_visibility()
        self._table.horizontalHeader().setStretchLastSection(True)
        # Allow selecting whole rows so Ctrl+C never drops the Meter Name column.
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(320)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._content_split = QSplitter(Qt.Orientation.Vertical)
        self._content_split.addWidget(self._paste)
        self._content_split.addWidget(self._table)
        # Table should absorb almost all resize; paste stays a compact strip.
        self._content_split.setStretchFactor(0, 1)
        self._content_split.setStretchFactor(1, 9)
        self._content_split.setChildrenCollapsible(False)
        self._split_meter_dominant_applied = False
        root.addWidget(self._content_split, stretch=1)

        # Ctrl+C on the table copies FULL rows (all columns) as TSV. The built-in
        # QTableWidget copy only grabs the active cell/selection, which is why some
        # meter names appeared "lost" when copying directly from the grid.
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self._table)
        copy_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_sc.activated.connect(self._copy_selection_tsv)

        row = QHBoxLayout()
        row.addStretch(1)
        self._dollar_toggle = QCheckBox("Show $")
        self._dollar_toggle.setToolTip(
            "Monetary meters: show dollars instead of credits (100 credits = $1 on USD cabinets)."
        )
        self._dollar_toggle.toggled.connect(self._on_dollar_toggle)
        row.addWidget(self._dollar_toggle)
        self._btn_compare = QPushButton("Compare")
        row.addWidget(self._btn_compare)
        self._btn_copy = QPushButton("Copy report")
        self._btn_copy.clicked.connect(self._copy_report)
        row.addWidget(self._btn_copy)
        self._btn_clear = QPushButton("Clear")
        self._btn_clear.clicked.connect(self._clear_all)
        row.addWidget(self._btn_clear)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

        # Back-compat with requested attribute naming (ui.*)
        self.ui = SimpleNamespace(
            compare_btn=self._btn_compare,
            table=self._table,
            paste=self._paste,
            scan_root_edit=self._scan_root_edit,
        )
        self.ui.compare_btn.clicked.connect(self._on_compare_clicked)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: self._stop_compare_thread(wait_ms=2000))
            app.aboutToQuit.connect(lambda: self._stop_meter_fetch_thread(wait_ms=2000))

    def _load_com_port_prefs(self) -> None:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD, DEFAULT_SAS_COM_PORT

            port = s.value(_KEY_COM_PORT, DEFAULT_SAS_COM_PORT, type=str)
            self._preferred_com_port = (port or DEFAULT_SAS_COM_PORT).strip()
            self._com_baud = int(s.value(_KEY_COM_BAUD, DEFAULT_SAS_COM_BAUD, type=int))
        finally:
            s.endGroup()

    def _current_com_port(self) -> str:
        from network.sas_serial_meters import normalize_com_port

        data = self._com_port_combo.currentData()
        if data:
            return normalize_com_port(str(data))
        return normalize_com_port(self._com_port_combo.currentText())

    def _refresh_com_port_list(self, *, preserve_text: bool = True) -> None:
        from network.sas_serial_meters import (
            DEFAULT_SAS_COM_PORT,
            enumerate_serial_ports,
            normalize_com_port,
        )

        current = self._current_com_port() if preserve_text else ""
        if not current:
            current = normalize_com_port(getattr(self, "_preferred_com_port", DEFAULT_SAS_COM_PORT))
        self._com_port_combo.blockSignals(True)
        self._com_port_combo.clear()
        ports = enumerate_serial_ports()
        for info in ports:
            label = info.device
            if info.description:
                label = f"{info.device} — {info.description}"
            self._com_port_combo.addItem(label, info.device)
        if current:
            idx = self._com_port_combo.findData(normalize_com_port(current))
            if idx >= 0:
                self._com_port_combo.setCurrentIndex(idx)
            else:
                self._com_port_combo.setEditText(current)
        elif self._com_port_combo.count() == 0:
            self._com_port_combo.setEditText(DEFAULT_SAS_COM_PORT)
        self._com_port_combo.blockSignals(False)

    def _set_com_port_selection(self, port: str) -> None:
        from network.sas_serial_meters import normalize_com_port

        port_name = normalize_com_port(port)
        if not port_name:
            return
        idx = self._com_port_combo.findData(port_name)
        if idx >= 0:
            self._com_port_combo.setCurrentIndex(idx)
        else:
            self._refresh_com_port_list(preserve_text=False)
            idx = self._com_port_combo.findData(port_name)
            if idx >= 0:
                self._com_port_combo.setCurrentIndex(idx)
            else:
                self._com_port_combo.setEditText(port_name)
        self._preferred_com_port = port_name

    def _save_com_port_prefs(self) -> None:
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD

        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            port = self._current_com_port()
            self._preferred_com_port = port
            s.setValue(_KEY_COM_PORT, port)
            s.setValue(_KEY_COM_BAUD, int(getattr(self, "_com_baud", DEFAULT_SAS_COM_BAUD)))
        finally:
            s.endGroup()

    def _build_view_menu_bar(self) -> QMenuBar:
        bar = QMenuBar(self)
        view_menu = bar.addMenu("&View")
        columns_menu = view_menu.addMenu("&Columns")
        columns_menu.setToolTip("Show or hide table columns (copy/export uses visible columns only).")
        for col, label in _VERIFY_TABLE_COLUMNS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(default_verify_column_visible(col))
            act.toggled.connect(lambda checked, c=col: self._on_column_visibility_toggled(c, checked))
            columns_menu.addAction(act)
            self._column_actions[col] = act
        return bar

    def _column_settings(self) -> QSettings:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        return s

    def _load_column_visibility_prefs(self) -> None:
        s = self._column_settings()
        try:
            version = s.value(_KEY_COLUMN_PREFS_VERSION, 0, type=int)
            if version < _SAS_VERIFY_COLUMN_PREFS_VERSION:
                for col, act in self._column_actions.items():
                    act.setChecked(default_verify_column_visible(col))
                s.setValue(_KEY_COLUMN_PREFS_VERSION, _SAS_VERIFY_COLUMN_PREFS_VERSION)
                for col, act in self._column_actions.items():
                    s.setValue(f"col_{col}", act.isChecked())
                return
            for col, act in self._column_actions.items():
                default = default_verify_column_visible(col)
                act.setChecked(s.value(f"col_{col}", default, type=bool))
        finally:
            s.endGroup()

    def _save_column_visibility_prefs(self) -> None:
        s = self._column_settings()
        try:
            s.setValue(_KEY_COLUMN_PREFS_VERSION, _SAS_VERIFY_COLUMN_PREFS_VERSION)
            for col, act in self._column_actions.items():
                s.setValue(f"col_{col}", act.isChecked())
        finally:
            s.endGroup()

    def _on_column_visibility_toggled(self, col: int, checked: bool) -> None:
        if checked:
            visible_count = sum(1 for a in self._column_actions.values() if a.isChecked())
            if visible_count < 1:
                act = self._column_actions[col]
                act.blockSignals(True)
                act.setChecked(True)
                act.blockSignals(False)
                return
        self._apply_column_visibility()
        self._save_column_visibility_prefs()

    def _apply_column_visibility(self) -> None:
        for col, act in self._column_actions.items():
            self._table.setColumnHidden(col, not act.isChecked())

    def _column_visibility_map(self) -> dict[int, bool]:
        return {col: act.isChecked() for col, act in self._column_actions.items()}

    def _stop_compare_thread(self, wait_ms: int = 300) -> None:
        th = self._compare_thread
        if th is None:
            return
        try:
            if th.isRunning():
                th.quit()
                if not th.wait(wait_ms):
                    # Worker does blocking SMB/network I/O; quit() may not return promptly.
                    # Force-stop as a last resort to avoid "QThread destroyed while running".
                    th.terminate()
                    th.wait(1000)
        except Exception:
            pass

    def _stop_meter_fetch_thread(self, wait_ms: int = 300) -> None:
        th = self._meter_fetch_thread
        if th is None:
            return
        try:
            if th.isRunning():
                th.quit()
                if not th.wait(wait_ms):
                    th.terminate()
                    th.wait(1000)
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_compare_thread(wait_ms=500)
        self._stop_meter_fetch_thread(wait_ms=500)
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_com_port_list(preserve_text=True)
        if self._split_meter_dominant_applied:
            return
        self._split_meter_dominant_applied = True
        split_h = max(self._content_split.height(), 480)
        paste_h = min(140, max(72, int(split_h * 0.13)))
        self._content_split.setSizes([paste_h, split_h - paste_h])

    def _on_meter_fetch_thread_finished(self) -> None:
        self._meter_fetch_thread = None
        self._meter_fetch_worker = None

    def _on_get_meters_clicked(self) -> None:
        self._refresh_com_port_list(preserve_text=True)
        port = self._current_com_port()
        if not port:
            QMessageBox.warning(
                self,
                "Get Meters",
                "Enter a COM port (for example COM4).",
            )
            return
        self._save_com_port_prefs()
        self._btn_get_meters.setEnabled(False)
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD

        self._btn_get_meters.setText("Syncing SAS link…")
        self._stop_meter_fetch_thread(wait_ms=300)
        self._meter_fetch_thread = QThread(self)
        self._meter_fetch_worker = MeterFetchWorker(
            com_port=port,
            com_baud=int(getattr(self, "_com_baud", DEFAULT_SAS_COM_BAUD)),
        )
        self._meter_fetch_worker.moveToThread(self._meter_fetch_thread)
        self._meter_fetch_thread.started.connect(self._meter_fetch_worker.run)
        self._meter_fetch_worker.finished.connect(
            self._on_meter_fetch_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._meter_fetch_worker.error.connect(
            self._on_meter_fetch_error,
            Qt.ConnectionType.QueuedConnection,
        )
        self._meter_fetch_worker.finished.connect(self._meter_fetch_thread.quit)
        self._meter_fetch_worker.error.connect(self._meter_fetch_thread.quit)
        self._meter_fetch_worker.finished.connect(self._meter_fetch_worker.deleteLater)
        self._meter_fetch_worker.error.connect(self._meter_fetch_worker.deleteLater)
        self._meter_fetch_thread.finished.connect(self._on_meter_fetch_thread_finished)
        self._meter_fetch_thread.finished.connect(self._meter_fetch_thread.deleteLater)
        self._meter_fetch_thread.start()

    def _on_meter_fetch_finished(self, result: object) -> None:
        self._btn_get_meters.setEnabled(True)
        self._btn_get_meters.setText("Get Meters")
        paste_text = getattr(result, "paste_text", None)
        port_used = getattr(result, "port_used", "") or ""
        wire_mode = getattr(result, "wire_mode", "") or ""
        baud = getattr(result, "baud", 0) or 0
        if port_used:
            self._set_com_port_selection(str(port_used))
            self._save_com_port_prefs()
        if not paste_text:
            return
        self._paste.setPlainText(str(paste_text))
        self._paste.setFocus()
        parsed = build_verify_6f_rows_from_paste(str(paste_text))
        if any((r.sas_value_text or "").strip() for r in parsed):
            self._last_parsed_rows = parsed
            self._sas_2f_values = parse_sas_2f_paste(str(paste_text))
            self._currency = _detect_egm_currency(
                self._scan_root_edit.text() or self._scan_root, self._vm
            )
            self._update_dollar_toggle_label()
            self._render(parsed_rows=parsed, allow_machine_lookup=False)
            self._begin_cabinet_compare()
        try:
            p = self.parent()
            if p is not None and hasattr(p, "statusBar"):
                sb = p.statusBar()
                if sb is not None:
                    link = f"{port_used or self._current_com_port()}"
                    if wire_mode and baud:
                        link += f" ({wire_mode} @ {baud})"
                    sb.showMessage(
                        f"SAS meters fetched on {link} — loading cabinet values from Scan root…",
                        6000,
                    )
        except Exception:
            pass

    def _on_meter_fetch_error(self, message: str) -> None:
        self._btn_get_meters.setEnabled(True)
        self._btn_get_meters.setText("Get Meters")
        self._refresh_com_port_list(preserve_text=True)
        QMessageBox.warning(
            self,
            "Get Meters",
            message or "Serial meter fetch failed.",
        )

    def _on_compare_thread_finished(self) -> None:
        self._compare_thread = None
        self._compare_worker = None

    def _on_compare_clicked(self) -> None:
        parsed = build_verify_6f_rows_from_paste(self._paste.toPlainText())
        if not any((r.sas_value_text or "").strip() for r in parsed):
            QMessageBox.information(
                self,
                "SAS accounting verification",
                "No valid SAS 6F RX lines found in the pasted text.",
            )
            return

        self._last_parsed_rows = parsed
        self._sas_2f_values = parse_sas_2f_paste(self._paste.toPlainText())

        self._currency = _detect_egm_currency(self._scan_root_edit.text() or self._scan_root, self._vm)
        self._update_dollar_toggle_label()

        # Render immediately without any machine/log lookups (prevents UI-thread UNC IO).
        self._render(parsed_rows=parsed, allow_machine_lookup=False)
        self._begin_cabinet_compare()

    def _begin_cabinet_compare(self) -> None:
        """Load Machine column from cabinet state XML (Scan root UNC). Runs off the UI thread."""
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not self._scan_root:
            QMessageBox.information(
                self,
                "SAS accounting verification",
                "Set Scan root to the cabinet log UNC (e.g. \\\\10.0.0.90\\c$\\Goldclub\\var\\log) "
                "so Machine values can be loaded from DeviceManagerData.xml.",
            )
            return

        self.ui.compare_btn.setEnabled(False)
        self.ui.compare_btn.setText("Scanning Cabinet...")
        self._btn_get_meters.setEnabled(False)

        self._stop_compare_thread(wait_ms=300)

        self._compare_thread = QThread(self)
        self._compare_worker = CompareWorker(_extract_unc_host(self._scan_root), self._scan_root)
        self._compare_worker.moveToThread(self._compare_thread)

        self._compare_thread.started.connect(self._compare_worker.run)
        self._compare_worker.finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._compare_worker.error.connect(
            self._on_worker_error,
            Qt.ConnectionType.QueuedConnection,
        )
        self._compare_worker.finished.connect(self._compare_thread.quit)
        self._compare_worker.error.connect(self._compare_thread.quit)
        self._compare_worker.finished.connect(self._compare_worker.deleteLater)
        self._compare_worker.error.connect(self._compare_worker.deleteLater)
        self._compare_thread.finished.connect(self._compare_thread.deleteLater)
        self._compare_thread.finished.connect(self._on_compare_thread_finished)
        self._compare_thread.start()

    def _on_worker_finished(self, state_obj: object) -> None:
        try:
            try:
                sys.__stdout__.write(
                    f"\n[UI-THREAD] Signal received! Results count: {len(state_obj) if isinstance(state_obj, dict) else 0}\n"
                )
                sys.__stdout__.flush()
            except Exception:
                pass
            if isinstance(state_obj, dict):
                # IMPORTANT: keep keys normalized/lowercase for alias lookup
                # (loader returns normalized keys like "coinin"; uppercasing breaks lookups).
                self._machine_state = {str(k).strip(): str(v) for k, v in state_obj.items()}
            else:
                self._machine_state = {}
            self._currency = _detect_egm_currency(self._scan_root, self._vm)
            self._update_dollar_toggle_label()
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=True)
        finally:
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)

    def _on_worker_error(self, msg: str) -> None:
        try:
            try:
                sys.__stdout__.write(f"\n[UI-THREAD] Worker error received: {msg}\n")
                sys.__stdout__.flush()
            except Exception:
                pass
            print(f"[ERROR] {msg}")
            QMessageBox.information(self, "SAS accounting verification", msg)
        finally:
            self._machine_state = {}
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=False)
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)

    def _normalize_int_for_compare(self, s: str) -> str:
        """
        Normalize numeric strings for direct compare:
        - strip leading zeros
        - if a decimal slips through, drop the dot (gm2u should already be integer-cents)
        """
        raw = (s or "").strip()
        if not raw:
            return "0"
        sign = "-" if raw.startswith("-") else ""
        body = raw[1:] if sign else raw
        if "." in body:
            body = body.replace(".", "")
        if not re.fullmatch(r"\d+", body):
            return raw
        body = body.lstrip("0") or "0"
        return sign + body

    def _update_dollar_toggle_label(self) -> None:
        sym = self._currency.symbol or "$"
        blocked = self._dollar_toggle.blockSignals(True)
        self._dollar_toggle.setText(f"Show {sym}")
        self._dollar_toggle.blockSignals(blocked)

    def _on_dollar_toggle(self, checked: bool) -> None:
        self._show_dollars = checked
        self._refresh_value_columns()

    def _value_header_labels(self) -> tuple[str, str, str]:
        if self._show_dollars:
            sym = self._currency.symbol or "$"
            return (f"SAS 6F ({sym})", f"SAS $2F ({sym})", f"Machine ({sym})")
        return ("SAS (6F)", "SAS ($2F)", "Machine")

    def _apply_value_headers(self) -> None:
        sas_6f_h, sas_2f_h, mac_h = self._value_header_labels()
        self._table.setHorizontalHeaderItem(COL_SAS_6F_VALUE, QTableWidgetItem(sas_6f_h))
        self._table.setHorizontalHeaderItem(COL_SAS_2F_VALUE, QTableWidgetItem(sas_2f_h))
        self._table.setHorizontalHeaderItem(COL_MACHINE_VALUE, QTableWidgetItem(mac_h))

    def _table_headers_for_copy(self) -> list[str]:
        return visible_verify_table_headers(
            show_dollars=self._show_dollars,
            currency_symbol=self._currency.symbol or "$",
            column_visible=self._column_visibility_map(),
        )

    def _update_2f_column_visibility(self) -> None:
        has_2f = bool(self._sas_2f_values)
        act = self._column_actions.get(COL_SAS_2F_VALUE)
        if has_2f and act is not None and not act.isChecked():
            act.blockSignals(True)
            act.setChecked(True)
            act.blockSignals(False)
            self._save_column_visibility_prefs()
        self._apply_column_visibility()

    def _maybe_show_2f_paste_hint(self) -> None:
        if self._sas_2f_values:
            return
        try:
            p = self.parent()
            if p is not None and hasattr(p, "statusBar"):
                sb = p.statusBar()
                if sb is not None:
                    sb.showMessage(
                        "No $2F polls in paste — add TX/RX 2F lines or "
                        "“$2F = First Meter = …” lines from the IGT tester to fill SAS ($2F).",
                        8000,
                    )
        except Exception:
            pass

    def _refresh_2f_from_paste(self) -> None:
        self._sas_2f_values = parse_sas_2f_paste(self._paste.toPlainText())
        self._update_2f_column_visibility()

    def _meter_code_for_row(self, row: int) -> str:
        code_item = self._table.item(row, COL_6F_CODE)
        return (code_item.text() if code_item else "").strip().upper()

    def _format_cell_value(self, meter_code: str, raw: str) -> str:
        return _format_meter_value_display(
            raw,
            meter_code=meter_code,
            currency=self._currency,
            show_dollars=self._show_dollars,
        )

    def _set_value_item(
        self,
        row: int,
        col: int,
        *,
        meter_code: str,
        raw: str,
        missing_display: str | None = None,
        tooltip: str = "",
    ) -> None:
        if missing_display is not None and not (raw or "").strip():
            item = QTableWidgetItem(missing_display)
            item.setData(RAW_VALUE_ROLE, "")
        else:
            item = QTableWidgetItem(self._format_cell_value(meter_code, raw))
            item.setData(RAW_VALUE_ROLE, raw)
        if tooltip:
            item.setToolTip(tooltip)
        self._table.setItem(row, col, item)

    def _refresh_value_columns(self) -> None:
        self._apply_value_headers()
        for row in range(self._table.rowCount()):
            rid = self._meter_code_for_row(row)
            for col in (COL_SAS_6F_VALUE, COL_SAS_2F_VALUE, COL_MACHINE_VALUE):
                if col == COL_SAS_2F_VALUE:
                    raw_opt = self._sas_2f_values.get(rid)
                    if raw_opt is None:
                        item = self._table.item(row, col)
                        if item is not None:
                            item.setText("—")
                            item.setData(RAW_VALUE_ROLE, "")
                        continue
                    raw = self._normalize_int_for_compare(raw_opt) or raw_opt
                else:
                    item = self._table.item(row, col)
                    if item is None:
                        continue
                    raw = str(item.data(RAW_VALUE_ROLE) or item.text() or "")
                item = self._table.item(row, col)
                if item is None:
                    continue
                item.setText(self._format_cell_value(rid, raw))
                if col == COL_SAS_2F_VALUE:
                    sas_6f_item = self._table.item(row, COL_SAS_6F_VALUE)
                    sas_6f_raw = str(sas_6f_item.data(RAW_VALUE_ROLE) or "") if sas_6f_item else ""
                    if self._normalize_int_for_compare(raw) != self._normalize_int_for_compare(sas_6f_raw):
                        item.setForeground(QColor("#b45309"))
                    else:
                        item.setForeground(QColor())

    def _render(self, *, parsed_rows: list[Sas6FRow], allow_machine_lookup: bool = True) -> None:
        self._refresh_2f_from_paste()
        self.ui.table.setRowCount(len(parsed_rows))
        self._apply_value_headers()
        self._maybe_show_2f_paste_hint()
        # Best-effort status-bar hint for operators.
        try:
            if self._machine_state:
                p = self.parent()
                if p is not None and hasattr(p, "statusBar"):
                    sb = p.statusBar()
                    if sb is not None:
                        sb.showMessage("Path Loaded", 4000)
        except Exception:
            pass
        for row, r in enumerate(parsed_rows):
            rid = r.meter_id.upper()
            sas_v = r.sas_value_text.strip()
            has_sas = bool(sas_v)
            try:
                from gui.view_model import SAS_6F_METER_ALIASES as aliases
                from gui.view_model import sas_6f_meter_column_values

                meter_name = (aliases.get(rid) or [""])[0]
                code_6f, wire_id, igt_poll, igt_meter = sas_6f_meter_column_values(rid)
            except Exception:
                meter_name = ""
                code_6f, wire_id, igt_poll, igt_meter = rid, "", "", ""
            machine_v = ""
            if allow_machine_lookup and self._machine_state:
                try:
                    machine_v = (
                        getattr(self._vm, "get_gm2u_value_for_sas_code")(rid, self._machine_state) or ""
                    ).strip()
                except Exception:
                    machine_v = ""
                if not machine_v:
                    try:
                        machine_v = (
                            getattr(self._vm, "emergency_lookup_value_for_sas_code_from_logs")(
                                self._scan_root, rid
                            )
                            or ""
                        ).strip()
                    except Exception:
                        machine_v = ""
            machine_missing = not machine_v
            sas_norm = self._normalize_int_for_compare(sas_v) if has_sas else ""
            mac_norm = self._normalize_int_for_compare(machine_v)
            if machine_missing:
                if allow_machine_lookup and has_sas and sas_norm == "0":
                    machine_v = "0"
                    mac_norm = "0"
                    match = True
                    status = "MATCH"
                else:
                    match = False
                    status = "PENDING"
            elif not has_sas:
                match = False
                status = "PENDING"
            else:
                try:
                    match = int(mac_norm) == int(sas_norm)
                except ValueError:
                    match = mac_norm == sas_norm
                status = "MATCH" if match else "MISMATCH"
            code_item = QTableWidgetItem(code_6f)
            code_item.setToolTip("6F paste table id (from RX<= 6F meter-code bytes, little-endian).")
            self.ui.table.setItem(row, COL_6F_CODE, code_item)
            wire_item = QTableWidgetItem(wire_id)
            wire_item.setToolTip("On-wire SAS meter id (byte-swapped from 6F Code).")
            self.ui.table.setItem(row, COL_WIRE_ID, wire_item)
            poll_item = QTableWidgetItem(igt_poll)
            poll_item.setToolTip("Hex index to enter in the IGT tester $2F “Send selected meters” dialog.")
            self.ui.table.setItem(row, COL_IGT_POLL, poll_item)
            igt_item = QTableWidgetItem(igt_meter)
            igt_item.setToolTip("Label the IGT tester shows (e.g. First Meter = 00000200).")
            self.ui.table.setItem(row, COL_IGT_METER, igt_item)
            name_item = QTableWidgetItem(meter_name)
            derived_formula = DERIVED_SAS_CODES.get(rid)
            name_font = name_item.font()
            if derived_formula:
                name_font.setItalic(True)
                name_item.setForeground(QColor(_DERIVED_METER_COLOR))
                name_item.setToolTip(
                    "Derived meter — Machine Value is computed in code:\n"
                    f"{derived_formula}"
                )
            else:
                name_font.setBold(True)
                name_item.setForeground(QColor(_TRUE_METER_COLOR))
                name_item.setToolTip("True meter — direct single-source cabinet value.")
            name_item.setFont(name_font)
            self.ui.table.setItem(row, COL_METER_NAME, name_item)
            self._set_value_item(
                row,
                COL_SAS_6F_VALUE,
                meter_code=rid,
                raw=sas_norm or sas_v,
                missing_display="—" if not has_sas else None,
                tooltip="From RX<= 6F bulk poll in paste.",
            )
            sas_2f_raw = self._sas_2f_values.get(rid)
            if sas_2f_raw is None:
                self._set_value_item(
                    row,
                    COL_SAS_2F_VALUE,
                    meter_code=rid,
                    raw="",
                    missing_display="—",
                    tooltip="No matching RX<= 2F poll in paste for this meter's IGT $2F index.",
                )
            else:
                sas_2f_norm = self._normalize_int_for_compare(sas_2f_raw)
                tip = "From RX<= 2F poll (IGT Send selected meters)."
                if sas_2f_norm != sas_norm:
                    tip += f"\nDiffers from SAS (6F): $2F={sas_2f_norm or '0'}, 6F={sas_norm or '0'}."
                self._set_value_item(
                    row,
                    COL_SAS_2F_VALUE,
                    meter_code=rid,
                    raw=sas_2f_norm or sas_2f_raw,
                    tooltip=tip,
                )
                if sas_2f_norm != sas_norm:
                    item = self.ui.table.item(row, COL_SAS_2F_VALUE)
                    if item is not None:
                        item.setForeground(QColor("#b45309"))
            mac_display_raw = mac_norm if machine_v else ""
            self._set_value_item(
                row,
                COL_MACHINE_VALUE,
                meter_code=rid,
                raw=mac_display_raw,
                tooltip="From cabinet gm2u / accounting XML.",
            )
            st = QTableWidgetItem(status)
            if match:
                st.setForeground(Qt.GlobalColor.darkGreen)
            elif status == "PENDING":
                st.setForeground(Qt.GlobalColor.darkGray)
            else:
                f = st.font()
                f.setBold(True)
                st.setFont(f)
                st.setForeground(Qt.GlobalColor.red)
            self.ui.table.setItem(row, COL_STATUS, st)

    def _clear_all(self) -> None:
        """Reset the dialog: empty the paste box (upper) and the results table (bottom)."""
        self._paste.clear()
        self._table.clearContents()
        self._table.setRowCount(0)
        self._sas_2f_values = {}
        self._update_2f_column_visibility()
        self._last_parsed_rows = []
        self._machine_state = {}
        self._paste.setFocus()

    def _name_for_code(self, rid: str) -> str:
        """Resolve a friendly meter name from the alias table (fallback for copy)."""
        try:
            aliases = getattr(self._vm, "SAS_6F_METER_ALIASES", None)
            if not aliases:
                from gui.view_model import SAS_6F_METER_ALIASES as aliases  # type: ignore
            return (aliases.get(rid) or [""])[0]
        except Exception:
            return ""

    def _cell_text(self, row: int, col: int) -> str:
        """Cell text flattened for TSV; rebuilds Meter Name if empty.

        Hex id columns use Excel text literals (``="0005"``) so leading zeros survive paste.
        """
        item = self._table.item(row, col)
        text = item.text() if item else ""
        if col == COL_METER_NAME and not text:
            text = self._name_for_code(self._meter_code_for_row(row))
        text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
        if col in _EXCEL_TEXT_COLS and text:
            return f'="{text}"'
        return text

    def _row_tsv(self, row: int) -> str:
        cols = [c for c in range(COL_COUNT) if not self._table.isColumnHidden(c)]
        return "\t".join(self._cell_text(row, c) for c in cols)

    def _copy_all_meters_tsv(self) -> None:
        if self._table.rowCount() == 0:
            return
        header = "\t".join(self._table_headers_for_copy())
        lines = [header] + [self._row_tsv(r) for r in range(self._table.rowCount())]
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_selected_row_tsv(self, row: int | None = None) -> None:
        if row is None or row < 0:
            rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
            if not rows:
                return
            row = rows[0]
        if row < 0 or row >= self._table.rowCount():
            return
        header = "\t".join(self._table_headers_for_copy())
        QApplication.clipboard().setText(f"{header}\n{self._row_tsv(row)}")

    def _copy_selected_column_tsv(self, col: int) -> None:
        if col < 0 or col >= COL_COUNT or self._table.isColumnHidden(col):
            return
        visible_cols = [c for c in range(COL_COUNT) if not self._table.isColumnHidden(c)]
        headers = self._table_headers_for_copy()
        try:
            header = headers[visible_cols.index(col)]
        except ValueError:
            return
        lines = [header] + [
            self._cell_text(r, col) for r in range(self._table.rowCount())
        ]
        QApplication.clipboard().setText("\n".join(lines))

    def _on_table_context_menu(self, pos: QPoint) -> None:
        if self._table.rowCount() == 0:
            return
        idx = self._table.indexAt(pos)
        row = idx.row() if idx.isValid() else -1
        col = idx.column() if idx.isValid() else -1
        has_row = row >= 0 or bool(self._table.selectedIndexes())
        has_col = col >= 0 and not self._table.isColumnHidden(col)

        menu = QMenu(self)
        act_all = menu.addAction("Copy all meters")
        act_all.triggered.connect(self._copy_all_meters_tsv)
        act_row = menu.addAction("Copy selected row")
        act_row.setEnabled(has_row)
        if row >= 0:
            act_row.triggered.connect(lambda _checked=False, r=row: self._copy_selected_row_tsv(r))
        else:
            act_row.triggered.connect(lambda _checked=False: self._copy_selected_row_tsv(None))
        act_col = menu.addAction("Copy selected column")
        act_col.setEnabled(has_col)
        if has_col:
            act_col.triggered.connect(lambda _checked=False, c=col: self._copy_selected_column_tsv(c))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _copy_selection_tsv(self) -> None:
        """Ctrl+C: copy selected rows as full TSV rows (all columns). Falls back to all."""
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            self._copy_all_meters_tsv()
            return
        header = "\t".join(self._table_headers_for_copy())
        lines = [header] + [self._row_tsv(r) for r in rows]
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_report(self) -> None:
        """Copy the report as tab-separated rows so it pastes cleanly into Excel cells.

        Each line is one spreadsheet row; columns are separated by a single TAB.
        Any tab/newline inside a value is flattened to a space so the grid never
        gets misaligned.
        """
        prod = getattr(self._vm, "current_product_name", None) or "UnknownProduct"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        lines: list[str] = []
        lines.append(f"VERIFICATION REPORT\t{prod}\t{ts}")
        lines.append("")  # blank spacer row before the table
        lines.append("\t".join(self._table_headers_for_copy()))
        for i in range(self._table.rowCount()):
            lines.append(self._row_tsv(i))
        QApplication.clipboard().setText("\n".join(lines))

    def mismatch_detected(self) -> bool:
        for i in range(self._table.rowCount()):
            it = self._table.item(i, COL_STATUS)
            if it and it.text().strip().upper() == "MISMATCH":
                return True
        return False

