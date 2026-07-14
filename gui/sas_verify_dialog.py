"""Paste-based SAS 6F verification against machine accounting state."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from types import SimpleNamespace
import sys
import threading
import traceback

from PySide6.QtCore import QEvent, QObject, QSettings, QThread, QThreadPool, Signal, Qt, QPoint, QSize, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QMenu,
    QMenuBar,
    QPushButton,
    QLineEdit,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from config_manager import SettingsManager
from gui.machine_yield_chart import MachineYieldChartWidget
from gui.palette_adapt import surface_is_light

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
# WindowSystemMenuHint is required on Windows so taskbar right-click → Close works.
_SAS_VERIFY_WINDOW_FLAGS = (
    Qt.WindowType.Window
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowSystemMenuHint
    | Qt.WindowType.WindowMinimizeButtonHint
    | Qt.WindowType.WindowMaximizeButtonHint
    | Qt.WindowType.WindowCloseButtonHint
)
_KEY_COM_PORT = "com_port"
_KEY_COM_BAUD = "com_baud"
_KEY_COM_WIRE = "com_wire_mode"
_KEY_COM_RTS = "com_rts"
# Bump when default column layout changes so saved prefs reset once.
_SAS_VERIFY_COLUMN_PREFS_VERSION = 2
_KEY_COLUMN_PREFS_VERSION = "column_prefs_version"
_DEFAULT_HIDDEN_VERIFY_COLUMNS = frozenset({COL_WIRE_ID, COL_SAS_2F_VALUE})
_VERIFY_COL_MIN_WIDTHS: dict[int, int] = {
    COL_6F_CODE: 72,
    COL_WIRE_ID: 72,
    COL_IGT_POLL: 64,
    COL_IGT_METER: 88,
    COL_METER_NAME: 180,
    COL_SAS_6F_VALUE: 72,
    COL_SAS_2F_VALUE: 72,
    COL_MACHINE_VALUE: 72,
    COL_STATUS: 72,
}
_VERIFY_METER_NAME_MAX_WIDTH = 240

# Meter tabs (EGM accounting UI order — View -> Meter tabs).
TAB_ACCOUNTING = 0
TAB_GAME = 1
TAB_MASTER = 2
TAB_BILLS = 3
TAB_COINS = 4
TAB_TRANSFER = 5
TAB_SECURITY = 6
_METER_TAB_NAMES: tuple[str, ...] = (
    "Accounting",
    "Game",
    "Master",
    "Bills",
    "Coins",
    "Transfer",
    "Security",
)
# Tabs that show the shared SAS verify grid (View -> Columns applies).
_METER_COLUMN_TABS = frozenset({
    TAB_ACCOUNTING,
})
_METER_CODES_GAME = frozenset({"0005", "0006", "0007", "0000", "0001", "001C", "001D"})
_METER_CODES_COINS = frozenset({"0000", "0001"})
_METER_CODES_TRANSFER = frozenset({
    "0080", "0082", "0084", "0086", "0088",
    "00A0", "00A2", "00A4", "00B8", "00BA", "00BC",
})
_METER_CODES_SECURITY = frozenset({"0005", "0006", "0007"})

# Bills tab (View -> meter tabs -> Bills) — matches cabinet meter UI: BILL | AMOUNT | COUNT.
_BILLS_TABLE_COLUMN_COUNT = 3
_BILLS_COL_BILL = 0
_BILLS_COL_AMOUNT = 1
_BILLS_COL_COUNT_IDX = 2
_BILLS_TABLE_HEADERS: tuple[str, ...] = (
    "BILL",
    "AMOUNT",
    "COUNT",
)
_BILLS_CATALOG_ROW_COUNT = 7

# Coins tab — 2x2 COIN IN / OUT / TO DROP BOX / TO HOPPER (EGM global meter UI).
_COINS_TABLE_COLUMN_COUNT = 3
_COINS_COL_COIN = 0
_COINS_COL_AMOUNT = 1
_COINS_COL_COUNT_IDX = 2
_COINS_TABLE_HEADERS: tuple[str, ...] = (
    "COIN",
    "AMOUNT",
    "COUNT",
)
_COINS_CATALOG_ROW_COUNT = 6
_COINS_PANELS: tuple[tuple[str, str], ...] = (
    ("in", "COIN IN"),
    ("out", "COIN OUT"),
    ("drop", "COIN TO DROP BOX"),
    ("hopper", "COIN TO HOPPER"),
)

# Master tab — EGM-style TOTAL CREDIT / HANDPAY OUT / WAGERED layout.
MASTER_CREDIT_IN_CODES: tuple[str, ...] = ("000B", "0000", "0017", "0015", "0023")
MASTER_CREDIT_OUT_CODES: tuple[str, ...] = ("006E", "0001", "0003", "0016", "0018")
MASTER_HANDPAY_CODES: tuple[str, ...] = ("0003", "0002", "001F", "0020", "001D")
MASTER_CANCELLED_CODE = "0004"
MASTER_WAGERED_CODES: tuple[str, ...] = ("001C", "00A4", "00A2")
MASTER_TRACKED_CODES: tuple[str, ...] = (
    *MASTER_CREDIT_IN_CODES,
    *MASTER_CREDIT_OUT_CODES,
    *MASTER_HANDPAY_CODES,
    MASTER_CANCELLED_CODE,
    *MASTER_WAGERED_CODES,
)
MASTER_CREDIT_IN_ROWS: tuple[tuple[str, str], ...] = (
    ("Bill In", "000B"),
    ("Coin In", "0000"),
    ("Remote In", "0017"),
    ("Ticket In", "0015"),
    ("Handpay In", "0023"),
)
MASTER_CREDIT_OUT_ROWS: tuple[tuple[str, str], ...] = (
    ("Bill out", "006E"),
    ("Coin Out", "0001"),
    ("Handpay Out", "0003"),
    ("Ticket Out", "0016"),
    ("Remote Out", "0018"),
)
MASTER_JACKPOT_SUB_ROWS: tuple[tuple[str, str], ...] = (
    ("Win Limit Jackpot", "001F"),
    ("External Bonus", "0020"),
    ("Progressive", "001D"),
)
MASTER_WAGERED_ROWS: tuple[tuple[str, str], ...] = (
    ("Cashable wagered", "001C"),
    ("Promotional wagered", "00A4"),
    ("Non Cashable wagered", "00A2"),
)

# Transfer tab — TICKET / CASHLESS panels (matches EGM accounting UI).
TRANSFER_TICKET_IN_CODE = "0015"
TRANSFER_TICKET_OUT_CODE = "0016"
TRANSFER_CASHLESS_IN_CODE = "0017"
TRANSFER_CASHLESS_OUT_CODE = "0018"
TRANSFER_TICKET_IN_BUCKETS: tuple[tuple[str, str], ...] = (
    ("Cashable", "0080"),
    ("Non-Cashable", "0082"),
    ("Promotional", "0084"),
)
TRANSFER_TICKET_OUT_BUCKETS: tuple[tuple[str, str], ...] = (
    ("Cashable", "0086"),
    ("Non-Cashable", "0088"),
    ("Promotional", ""),
)
TRANSFER_CASHLESS_IN_BUCKETS: tuple[tuple[str, str], ...] = (
    ("Cashable", "00A0"),
    ("Non-Cashable", "00A2"),
    ("Promotional", "00A4"),
)
TRANSFER_CASHLESS_OUT_BUCKETS: tuple[tuple[str, str], ...] = (
    ("Cashable", "00B8"),
    ("Non-Cashable", "00BA"),
    ("Promotional", "00BC"),
)
TRANSFER_PROMO_TICKET_OUT_STATE_KEYS: tuple[str, ...] = (
    "voucherpromooutamt",
    "voucher_promoOutAmt",
)
TRANSFER_BUCKET_COUNT_KEYS: dict[str, tuple[str, ...]] = {
    "0080": ("vouchercashableincnt", "regularcashableticketincnt"),
    "0082": ("vouchernoncashincnt", "restrictedticketincnt"),
    "0084": ("voucherpromoincnt", "nonrestrictedticketincnt"),
    "0086": ("vouchercashableoutcnt", "regularcashableticketoutcnt"),
    "0088": ("vouchernoncashoutcnt", "restrictedticketoutcnt"),
    "00A0": ("watcashableincnt",),
    "00A2": ("watnoncashincnt",),
    "00A4": ("watpromoincnt",),
    "00B8": ("watcashableoutcnt",),
    "00BA": ("watnoncashoutcnt",),
    "00BC": ("watpromooutcnt",),
}
TRANSFER_COUNT_STATE_KEYS: dict[str, tuple[str, ...]] = {
    "ticket:in": ("ticketincnt", "voucherticketincnt", "totalvoucherincnt"),
    "ticket:out": ("ticketoutcnt", "voucherticketoutcnt", "totalvoucheroutcnt"),
    "ticket:accepted": (
        "ticketacceptedcnt",
        "voucheracceptedcnt",
        "ticketsacceptedcnt",
        "voucherticketacceptedcnt",
    ),
    "ticket:printed": (
        "ticketprintedcnt",
        "voucherprintedcnt",
        "ticketsprintedcnt",
        "voucherticketprintedcnt",
    ),
    "cashless:in": ("wattransferincnt", "transferincnt", "afttransferincnt"),
    "cashless:out": ("wattransferoutcnt", "transferoutcnt", "afttransferoutcnt"),
    "cashless:transfers_in": ("wattransferincnt", "transferincnt", "transfersincnt"),
    "cashless:transfers_out": ("wattransferoutcnt", "transferoutcnt", "transfersoutcnt"),
}
TRANSFER_TRACKED_AMOUNT_CODES: tuple[str, ...] = tuple(
    dict.fromkeys(
        c
        for c in (
            TRANSFER_TICKET_IN_CODE,
            TRANSFER_TICKET_OUT_CODE,
            TRANSFER_CASHLESS_IN_CODE,
            TRANSFER_CASHLESS_OUT_CODE,
            *(code for _, code in TRANSFER_TICKET_IN_BUCKETS if code),
            *(code for _, code in TRANSFER_TICKET_OUT_BUCKETS if code),
            *(code for _, code in TRANSFER_CASHLESS_IN_BUCKETS),
            *(code for _, code in TRANSFER_CASHLESS_OUT_BUCKETS),
        )
        if c
    )
)

# Security tab — Door Open Count / Games Since (gm2au cabinet meters on lab .90).
SECURITY_DOOR_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Note Door", ("notedooropens", "noteDoorOpens")),
    ("Drop Door", ("dropdooropens", "dropDoorOpens")),
    ("Hopper Door", ("hopperdooropens", "hopperDoorOpens")),
    ("Logic Door", ("logicdooropens", "logicDoorOpens")),
    ("Auxiliary Door", ("auxdooropens", "auxDoorOpens")),
    ("Cabinet Door", ("cabinetdooropens", "cabinetDoorOpens")),
)
SECURITY_GAMES_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Initialized", ("gamessinceinit", "gamesSinceInit")),
    ("Power Reset", ("gamessincepowerreset", "gamesSincePowerReset")),
    ("Door Closed", ("gamessincedoorclosed", "gamesSinceDoorClosed")),
)

# Game tab — Performance Meters / Residual Credit (EGM accounting UI).
GAME_PLAYED_STATE_KEYS: tuple[str, ...] = ("gamesplayed", "gamebaseplays")
GAME_WON_STATE_KEYS: tuple[str, ...] = ("gameswon",)
GAME_LOST_STATE_KEYS: tuple[str, ...] = ("gameslost", "totalgameslost")
GAME_BET_CODE = "0000"
GAME_WIN_CODE = "0001"
GAME_GAME_WIN_CODE = "001C"
GAME_PROG_WIN_CODE = "001D"
GAME_BONUS_WIN_STATE_KEYS: tuple[str, ...] = (
    "scattercoinout",
    "addscattercoinout",
    "bonuswin",
)
GAME_SAS_BONUS_STATE_KEYS: tuple[str, ...] = ("sasbonuswin",)
GAME_PROG_WIN_STATE_KEYS: tuple[str, ...] = ("progwin",)
GAME_RESIDUAL_PLAYED_KEYS: tuple[str, ...] = (
    "residualcreditplays",
    "residualcreditgamesplayed",
)
GAME_RESIDUAL_WON_KEYS: tuple[str, ...] = (
    "residualcreditwon",
    "residualcreditgameswon",
)
GAME_RESIDUAL_LOST_KEYS: tuple[str, ...] = (
    "residualcreditlost",
    "residualcreditgameslost",
)
GAME_RESIDUAL_COIN_IN_KEYS: tuple[str, ...] = ("residualcreditcoinin",)
GAME_RESIDUAL_COIN_OUT_KEYS: tuple[str, ...] = ("residualcreditcoinout",)
GAME_RESIDUAL_ROWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Played", GAME_RESIDUAL_PLAYED_KEYS),
    ("Won", GAME_RESIDUAL_WON_KEYS),
    ("Lost", GAME_RESIDUAL_LOST_KEYS),
    ("Coin In", GAME_RESIDUAL_COIN_IN_KEYS),
    ("Coin Out", GAME_RESIDUAL_COIN_OUT_KEYS),
)
GAME_THEME_TOTAL = "Total"

_GAME_SAS_AMOUNT_STATE_KEYS: dict[str, tuple[str, ...]] = {
    "0000": ("coinin", "gamecoinin"),
    "0001": ("coinout", "totalcoinout"),
    "001C": ("basegamecoinout", "bggamecoinout", "coinout"),
    "001D": ("progwin", "progscattercoinout"),
}


def game_amount_state_keys_for_sas_code(code: str) -> tuple[str, ...]:
    return _GAME_SAS_AMOUNT_STATE_KEYS.get((code or "").strip().upper(), ())


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

    return build_verify_6f_rows_for_codes(text, DEFAULT_6F_VERIFY_POLL_CODES)


def filter_verify_6f_rows(
    parsed_rows: list[Sas6FRow],
    meter_codes: tuple[str, ...] | list[str],
) -> list[Sas6FRow]:
    """Keep stable row order for a meter-code subset."""
    by_id = {r.meter_id.upper(): r.sas_value_text for r in parsed_rows}
    rows: list[Sas6FRow] = []
    seen: set[str] = set()
    for code in meter_codes:
        rid = code.upper()
        if rid in seen:
            continue
        seen.add(rid)
        rows.append(Sas6FRow(meter_id=rid, sas_value_text=by_id.get(rid, "")))
    return rows


def build_verify_6f_rows_for_codes(
    text: str,
    meter_codes: tuple[str, ...] | list[str],
) -> list[Sas6FRow]:
    """Merge pasted 6F RX with a fixed meter-code list (stable row order)."""
    return filter_verify_6f_rows(parse_sas_6f_paste(text), meter_codes)


def verify_accounting_tab_spec(
    *,
    tab_names: tuple[str, ...],
    has_accounting_panel: bool,
    verify_row_count: int,
    expected_row_count: int,
) -> list[str]:
    """Self-check Accounting tab uses the shared meter-panel shell."""
    issues: list[str] = []
    if "Submeter" in tab_names:
        issues.append("Submeter tab must not be present")
    if "Accounting" not in tab_names:
        issues.append("missing Accounting tab")
    if not has_accounting_panel:
        issues.append("Accounting tab must use meter panel layout")
    if verify_row_count != expected_row_count:
        issues.append(
            f"verify row count {verify_row_count} != expected {expected_row_count}"
        )
    return issues


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


def should_skip_cabinet_reload(
    *,
    scan_root: str,
    loaded_scan_root: str,
    machine_state_loaded: bool,
) -> bool:
    """True when cabinet XML for *scan_root* is already in memory."""
    sr = (scan_root or "").strip()
    if not machine_state_loaded or not sr:
        return False
    return sr == (loaded_scan_root or "").strip()


def meter_fetch_display_action(
    *,
    cached_result: object | None,
    fetch_running: bool,
    user_already_applied: bool = False,
) -> str:
    """Get Meters action: ``wait`` while capturing, else ``capture`` (refresh from COM)."""
    del cached_result, user_already_applied
    if fetch_running:
        return "wait"
    return "capture"


def format_bill_amount_display(amount_cents: int) -> str:
    """Amount column: dollars without ``$`` (e.g. ``208.00``)."""
    cents = max(0, int(amount_cents))
    return f"{cents / 100.0:.2f}"


def format_bill_reject_count_label(count: str | None) -> str:
    """Footer under BILL IN: ``BILL REJECT COUNT 0``."""
    raw = (count or "").strip()
    return f"BILL REJECT COUNT {raw}" if raw else "BILL REJECT COUNT"


def prepare_bill_table_body_rows(
    bill_rows: list | tuple,
    *,
    direction: str = "in",
) -> tuple[list, dict[str, int] | None]:
    """
    Expand aggregate-only fallback to the full denomination catalog (zeros per row).

    Returns ``(body_rows, aggregate_totals)`` where *aggregate_totals* is set only
    for the single-row 000B fallback path.
    """
    from network.sas_serial_meters import (
        SAS_BILL_OUT_DENOMINATIONS,
        SasBillDenomRow,
        expand_aggregate_bill_to_catalog,
        infer_aggregate_bill_face_cents,
        merge_bill_rows_with_catalog,
    )

    rows = list(bill_rows or ())
    if (
        len(rows) == 1
        and isinstance(rows[0], SasBillDenomRow)
        and rows[0].source == "aggregate"
    ):
        agg = rows[0]
        catalog = SAS_BILL_OUT_DENOMINATIONS if direction == "out" else None
        if catalog is not None:
            display = expand_aggregate_bill_to_catalog(agg, direction="out", catalog=catalog)
        else:
            display = expand_aggregate_bill_to_catalog(agg, direction="in")
        if infer_aggregate_bill_face_cents(int(agg.amount_cents), int(agg.count)) is not None:
            return list(display), None
        return list(display), {
            "count": int(agg.count),
            "amount_cents": int(agg.amount_cents),
        }
    return rows, None


def verify_bills_table_spec(
    *,
    column_count: int,
    headers: tuple[str, ...],
    vertical_header_hidden: bool,
    row_count: int | None = None,
) -> list[str]:
    """Self-check Bills table layout; empty list means the spec is satisfied."""
    issues: list[str] = []
    if column_count != _BILLS_TABLE_COLUMN_COUNT:
        issues.append(f"expected {_BILLS_TABLE_COLUMN_COUNT} columns, got {column_count}")
    if headers != _BILLS_TABLE_HEADERS:
        issues.append(f"headers must be {_BILLS_TABLE_HEADERS!r}, got {headers!r}")
    if not vertical_header_hidden:
        issues.append("vertical row header must be hidden")
    if row_count is not None and row_count != _BILLS_CATALOG_ROW_COUNT + 1:
        issues.append(
            f"expected {_BILLS_CATALOG_ROW_COUNT + 1} rows (denoms + TOTAL), got {row_count}"
        )
    return issues


def verify_coins_table_spec(
    *,
    column_count: int,
    headers: tuple[str, ...],
    vertical_header_hidden: bool,
    row_count: int | None = None,
) -> list[str]:
    """Self-check Coins table layout; empty list means the spec is satisfied."""
    issues: list[str] = []
    if column_count != _COINS_TABLE_COLUMN_COUNT:
        issues.append(f"expected {_COINS_TABLE_COLUMN_COUNT} columns, got {column_count}")
    if headers != _COINS_TABLE_HEADERS:
        issues.append(f"headers must be {_COINS_TABLE_HEADERS!r}, got {headers!r}")
    if not vertical_header_hidden:
        issues.append("vertical row header must be hidden")
    if row_count is not None and row_count != _COINS_CATALOG_ROW_COUNT + 1:
        issues.append(
            f"expected {_COINS_CATALOG_ROW_COUNT + 1} rows (denoms + TOTAL), got {row_count}"
        )
    return issues


def bills_group_box_stylesheet(*, light: bool) -> str:
    if light:
        return (
            "QGroupBox { font-weight: bold; border: 1px solid #b0b0b0; margin-top: 14px; "
            "padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; "
            "padding: 2px 12px; background-color: #c8d4e0; color: #1a1a1a; }"
        )
    return (
        "QGroupBox { font-weight: bold; border: 1px solid #555555; margin-top: 14px; "
        "padding-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; "
        "padding: 2px 12px; background-color: #3a4550; }"
    )


def bills_table_stylesheet(*, light: bool) -> str:
    """Compact BILL IN/OUT tables — matches Master tab density."""
    if light:
        return (
            "QTableWidget { gridline-color: #c0c0c0; background: #ffffff; font-size: 9pt; }"
            "QTableWidget::item { padding: 0px 4px; }"
            "QHeaderView::section { background-color: #c8d4e0; color: #1a1a1a; "
            "font-weight: bold; font-size: 9pt; border: 1px solid #c0c0c0; "
            "padding: 2px 4px; }"
        )
    return (
        "QTableWidget { gridline-color: #555555; font-size: 9pt; }"
        "QTableWidget::item { padding: 0px 4px; }"
        "QHeaderView::section { background-color: #3a4550; font-weight: bold; "
        "font-size: 9pt; border: 1px solid #555555; padding: 2px 4px; }"
    )


def meter_panel_group_box_stylesheet(*, light: bool) -> str:
    return bills_group_box_stylesheet(light=light)


def master_tab_group_box_stylesheet(*, light: bool) -> str:
    """Compact EGM-style group boxes for the Master tab."""
    if light:
        return (
            "QGroupBox { font-weight: bold; font-size: 9pt; border: 1px solid #b0b0b0; "
            "margin-top: 6px; padding: 2px 6px 4px 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; "
            "padding: 1px 8px; background-color: #c8d4e0; color: #1a1a1a; }"
        )
    return (
        "QGroupBox { font-weight: bold; font-size: 9pt; border: 1px solid #555555; "
        "margin-top: 6px; padding: 2px 6px 4px 6px; }"
        "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; "
        "padding: 1px 8px; background-color: #3a4550; }"
    )


def master_cancelled_row_stylesheet(*, light: bool) -> str:
    border = "#b0b0b0" if light else "#555555"
    bg = "#f5f5f5" if light else "#2a2a2a"
    return (
        f"QGroupBox {{ border: 1px solid {border}; margin-top: 4px; padding: 2px 6px; "
        f"background: {bg}; font-size: 9pt; }}"
        "QGroupBox::title { subcontrol-origin: margin; left: 0px; padding: 0px; "
        "width: 0px; height: 0px; }"
    )


def lookup_normalized_machine_value(state: dict[str, str], *keys: str) -> str:
    """Find the first matching value in *state* using normalized key names."""
    if not state or not keys:
        return ""
    norm: dict[str, str] = {}
    for k, v in state.items():
        nk = re.sub(r"[^a-z0-9]+", "", str(k).lower())
        if nk:
            norm[nk] = str(v).strip()
    for key in keys:
        nk = re.sub(r"[^a-z0-9]+", "", key.lower())
        if nk in norm and norm[nk]:
            return norm[nk]
    return ""


def format_transfer_count_display(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "0"
    cleaned = text.replace(",", "")
    if re.fullmatch(r"\d+", cleaned):
        return str(int(cleaned))
    return text


def meter_tabs_stylesheet() -> str:
    """Center meter category tabs (Accounting / Game / …) like the EGM UI."""
    return "QTabWidget::tab-bar { alignment: center; }"


_METER_PANEL_FONT_PT = 9
_METER_ROW_MIN_HEIGHT = 20
_METER_VALUE_MIN_WIDTH = 96
_METER_COUNT_MIN_WIDTH = 56
_METER_TRANSFER_AMOUNT_WIDTH = 96
_METER_FORM_MIN_WIDTH = 300
_METER_PANEL_OUTER_MARGIN = 8
_METER_PANEL_CLUSTER_SPACING = 8


def meter_panel_font(*, bold: bool = False) -> QFont:
    font = QFont()
    font.setPointSize(_METER_PANEL_FONT_PT)
    font.setBold(bold)
    return font


def meter_row_label(text: str, *, indent: bool = False, bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    if indent:
        lbl.setContentsMargins(10, 0, 0, 0)
    lbl.setFont(meter_panel_font(bold=bold))
    lbl.setMinimumHeight(_METER_ROW_MIN_HEIGHT)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def meter_value_label(*, bold: bool = False, min_width: int = _METER_VALUE_MIN_WIDTH) -> QLabel:
    """Value column for form-style meter panels (Game / Master / Security)."""
    lbl = QLabel("—")
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setFont(meter_panel_font(bold=bold))
    lbl.setMinimumHeight(_METER_ROW_MIN_HEIGHT)
    lbl.setMinimumWidth(min_width)
    lbl.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return lbl


def meter_grid_value_label(*, bold: bool = False, min_width: int = _METER_VALUE_MIN_WIDTH) -> QLabel:
    """Centered value cell for Transfer-style grid panels."""
    lbl = QLabel("—")
    lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    lbl.setFont(meter_panel_font(bold=bold))
    lbl.setMinimumHeight(_METER_ROW_MIN_HEIGHT)
    lbl.setMinimumWidth(min_width)
    lbl.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return lbl


def meter_tab_outer_layout(body: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(body)
    layout.setContentsMargins(
        _METER_PANEL_OUTER_MARGIN,
        _METER_PANEL_OUTER_MARGIN,
        _METER_PANEL_OUTER_MARGIN,
        _METER_PANEL_OUTER_MARGIN,
    )
    layout.setSpacing(6)
    return layout


def meter_centered_filter_row(*widgets: QWidget) -> QHBoxLayout:
    """Center a compact row of filter controls above meter panels."""
    outer = QHBoxLayout()
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addStretch(1)
    inner = QHBoxLayout()
    inner.setContentsMargins(0, 0, 0, 0)
    inner.setSpacing(_METER_PANEL_CLUSTER_SPACING)
    for widget in widgets:
        inner.addWidget(widget)
    outer.addLayout(inner)
    outer.addStretch(1)
    return outer


def meter_panel_cluster(*widgets: QWidget) -> QWidget:
    """Horizontal cluster of meter group boxes with consistent spacing."""
    cluster = QWidget()
    layout = QHBoxLayout(cluster)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(_METER_PANEL_CLUSTER_SPACING)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    for widget in widgets:
        layout.addWidget(widget)
    cluster.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
    return cluster


def meter_subpanel_form_layout() -> QFormLayout:
    """Compact label/value form; caller wraps with ``center_layout_in_group_box``."""
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setVerticalSpacing(0)
    form.setHorizontalSpacing(10)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    form.setFormAlignment(
        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
    )
    return form


def center_layout_in_group_box(box: QGroupBox, inner: QLayout) -> None:
    """Fit the group box border around *inner* (no empty interior stretch)."""
    holder = QWidget()
    holder.setLayout(inner)
    holder.setMinimumWidth(_METER_FORM_MIN_WIDTH)
    holder.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(10, 12, 10, 8)
    outer.setSpacing(0)
    outer.addWidget(holder)
    box.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)


def center_widget_in_panel(layout: QVBoxLayout, widget: QWidget) -> None:
    """Horizontally center *widget* in a tab panel; stick to the top vertically."""
    widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch(1)
    row.addWidget(widget)
    row.addStretch(1)
    layout.addLayout(row)
    layout.addStretch(1)


def configure_verify_table_scroll(table: QTableWidget, *, min_height: int = 120) -> None:
    """Keep verify tables scrollable so they do not lock dialog vertical resize."""
    table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setMinimumHeight(min_height)


def stretch_widget_in_panel(layout: QVBoxLayout, widget: QWidget) -> None:
    """Horizontally center *widget* and grow it vertically with the tab panel."""
    widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addStretch(1)
    row.addWidget(widget)
    row.addStretch(1)
    layout.addLayout(row, 1)


def center_table_in_group_box(box: QGroupBox, table: QTableWidget) -> None:
    """Wrap a compact meter table in a tight group box."""
    table.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(10, 12, 10, 8)
    outer.setSpacing(0)
    outer.addWidget(table)
    box.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)


def wrap_expand_verify_table_in_group_box(box: QGroupBox, table: QTableWidget) -> None:
    """Accounting verify table: column-fit width, grows vertically when the window is tall."""
    configure_verify_table_scroll(table)
    table.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(10, 12, 10, 8)
    outer.setSpacing(0)
    outer.addWidget(table, 1)
    box.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)


def wrap_compact_verify_table_in_group_box(box: QGroupBox, table: QTableWidget) -> None:
    """Compact verify table in a group box (Accounting tab — centered like Bills/Coins)."""
    table.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    center_table_in_group_box(box, table)


def fit_verify_table_columns(table: QTableWidget) -> None:
    """Resize visible verify columns to content; cap Meter Name width; shrink table to fit."""
    header = table.horizontalHeader()
    total = 0
    for col in range(table.columnCount()):
        if table.isColumnHidden(col):
            continue
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        table.resizeColumnToContents(col)
        width = table.columnWidth(col)
        min_w = _VERIFY_COL_MIN_WIDTHS.get(col, 48)
        width = max(width, min_w)
        if col == COL_METER_NAME:
            width = min(width, _VERIFY_METER_NAME_MAX_WIDTH)
        table.setColumnWidth(col, width)
        total += width
    header.setStretchLastSection(False)
    frame = table.frameWidth() * 2
    row_h = table.verticalHeader().defaultSectionSize() or 20
    header_h = table.horizontalHeader().height() or 22
    needs_vscroll = table.rowCount() * row_h + header_h > table.height() - 4
    vscroll_gutter = 18 if needs_vscroll or table.rowCount() > 12 else 0
    content_w = total + frame + vscroll_gutter
    table.setMinimumWidth(content_w)
    table.setMaximumWidth(content_w)


def verify_accounting_tab_layout(
    *,
    accounting_tab: QWidget,
    accounting_box: QGroupBox | None,
    table: QTableWidget,
    tab_names: tuple[str, ...],
) -> list[str]:
    """Self-check Accounting tab is centered and verify columns fit without excess stretch."""
    issues: list[str] = []
    if "Submeter" in tab_names:
        issues.append("Submeter tab must not be present")
    if accounting_box is None:
        issues.append("missing accounting group box")
    else:
        pol = accounting_box.sizePolicy()
        if pol.verticalPolicy() != QSizePolicy.Policy.Expanding:
            issues.append("accounting box should expand vertically with the tab")
        if pol.horizontalPolicy() not in (
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.MinimumExpanding,
        ):
            issues.append("accounting box should size to table content (not full-width expand)")

    layout = accounting_tab.layout()
    if not isinstance(layout, QVBoxLayout):
        issues.append("accounting tab must use QVBoxLayout")
    elif verify_stretch_widget_in_panel_layout(layout) != []:
        issues.append("accounting tab must use stretch_widget_in_panel for vertical fill")

    if table.isColumnHidden(COL_STATUS):
        issues.append("Status column must be visible")
    if table.rowCount() < 1:
        issues.append("verify table must have rows after render")

    header = table.horizontalHeader()
    if header.sectionResizeMode(COL_METER_NAME) == QHeaderView.ResizeMode.Stretch:
        issues.append("Meter Name column must not stretch (causes excess empty space)")

    meter_name_w = table.columnWidth(COL_METER_NAME)
    if meter_name_w > _VERIFY_METER_NAME_MAX_WIDTH:
        issues.append(
            f"Meter Name column too wide ({meter_name_w}px > {_VERIFY_METER_NAME_MAX_WIDTH}px)"
        )

    if table.horizontalScrollBar().maximum() > 0:
        issues.append(
            f"horizontal scrollbar still required (max={table.horizontalScrollBar().maximum()})"
        )

    visible_width = sum(
        table.columnWidth(c)
        for c in range(table.columnCount())
        if not table.isColumnHidden(c)
    )
    viewport_w = table.viewport().width()
    if viewport_w > 0 and visible_width > viewport_w + 4:
        issues.append(
            f"columns ({visible_width}px) clipped in {viewport_w}px viewport"
        )

    return issues


def format_master_amount_display(raw: str, *, symbol: str = "$") -> str:
    """Master tab currency: always ``$208.00`` (two decimals, zero -> ``$0.00``)."""
    text = (raw or "").strip() or "0"
    dollars = _credits_to_dollar_amount(text)
    if dollars is None:
        return "—"
    return f"{symbol}{dollars:.2f}"


def master_dollar_amount(raw: str) -> float:
    dollars = _credits_to_dollar_amount((raw or "").strip() or "0")
    return float(dollars) if dollars is not None else 0.0


def compute_master_summary(values: dict[str, str]) -> dict[str, float | None]:
    """Derived Master-tab totals from raw meter strings."""
    credit_in = sum(master_dollar_amount(values.get(c, "0")) for c in MASTER_CREDIT_IN_CODES)
    credit_out = sum(master_dollar_amount(values.get(c, "0")) for c in MASTER_CREDIT_OUT_CODES)
    total = credit_in - credit_out
    inout_pct = (credit_out / credit_in * 100.0) if credit_in > 0 else None
    return {
        "credit_in": credit_in,
        "credit_out": credit_out,
        "total_credit": total,
        "inout_pct": inout_pct,
    }


def format_signed_dollar_amount(amount: float, *, symbol: str = "$") -> str:
    if amount < 0:
        return f"-{symbol}{abs(amount):.2f}"
    return f"{symbol}{amount:.2f}"


def format_game_pct_display(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def compute_game_summary(
    *,
    played_raw: str,
    won_raw: str,
    lost_raw: str,
    bet_raw: str,
    win_raw: str,
) -> dict[str, float | int | None]:
    """Derived Game-tab counters and yield from raw meter strings."""
    played = int(format_transfer_count_display(played_raw))
    won = int(format_transfer_count_display(won_raw))
    lost_text = format_transfer_count_display(lost_raw)
    if lost_text and lost_text != "0":
        lost = int(lost_text)
    elif played or won:
        lost = max(played - won, 0)
    else:
        lost = 0
    bet = master_dollar_amount(bet_raw)
    win = master_dollar_amount(win_raw)
    bet_minus_win = bet - win
    yield_pct = (win / bet * 100.0) if bet > 0 else None
    hold_pct = ((bet - win) / bet * 100.0) if bet > 0 else None
    return {
        "played": played,
        "won": won,
        "lost": lost,
        "bet": bet,
        "win": win,
        "bet_minus_win": bet_minus_win,
        "yield_pct": yield_pct,
        "hold_pct": hold_pct,
    }


def verify_stretch_widget_in_panel_layout(layout: QVBoxLayout) -> list[str]:
    """Self-check: one expanding content row, no trailing stretch eating height."""
    issues: list[str] = []
    if layout.count() < 1:
        issues.append("expected at least one content row")
        return issues
    first = layout.itemAt(0)
    if first is not None and first.spacerItem() is not None:
        issues.append("leading vertical stretch must not be present")
    if layout.count() > 1:
        last = layout.itemAt(layout.count() - 1)
        if last is not None and last.spacerItem() is not None:
            issues.append("trailing vertical stretch must not be present on expandable tabs")
    row = layout.itemAt(0)
    if row is None or row.layout() is None:
        issues.append("expected first item to be a horizontal center row")
    return issues


def verify_center_widget_in_panel_layout(layout: QVBoxLayout) -> list[str]:
    """Self-check: panel content is top-stuck and horizontally centered (bottom stretch only)."""
    issues: list[str] = []
    if layout.count() < 2:
        issues.append("expected at least a content row and trailing stretch")
        return issues
    first = layout.itemAt(0)
    if first is not None and first.spacerItem() is not None:
        issues.append("leading vertical stretch must not be present (top-stuck layout)")
    last = layout.itemAt(layout.count() - 1)
    if last is None or last.spacerItem() is None:
        issues.append("trailing vertical stretch required for top-stuck layout")
    return issues


def verify_game_tab_spec(
    *,
    group_titles: tuple[str, ...],
    perf_label_keys: frozenset[str],
    residual_label_keys: frozenset[str],
    has_yield_chart: bool,
) -> list[str]:
    """Self-check Game tab structure against the cabinet reference layout."""
    issues: list[str] = []
    expected_titles = ("Performance Meters", "Residual Credit Removal Feature", "Machine Yield Chart")
    for title in expected_titles:
        if title not in group_titles:
            issues.append(f"missing group box {title!r}")
    expected_perf = frozenset(
        {
            "played",
            "won",
            "lost",
            "bet",
            "win",
            "game_win",
            "bonus_win",
            "sas_bonus",
            "prog_win",
            "bet_minus_win",
            "yield",
            "hold",
        }
    )
    missing_perf = expected_perf - perf_label_keys
    if missing_perf:
        issues.append(f"missing performance labels: {sorted(missing_perf)}")
    expected_residual = frozenset(k.lower().replace(" ", "_") for k, _ in GAME_RESIDUAL_ROWS)
    missing_residual = expected_residual - residual_label_keys
    if missing_residual:
        issues.append(f"missing residual labels: {sorted(missing_residual)}")
    if not has_yield_chart:
        issues.append("Machine Yield Chart panel required")
    return issues


def verify_master_tab_spec(
    *,
    group_titles: tuple[str, ...],
    value_label_codes: frozenset[str],
    has_credit_section_totals: bool,
) -> list[str]:
    """Self-check Master tab structure against the cabinet reference layout."""
    issues: list[str] = []
    expected_titles = ("TOTAL CREDIT", "HANDPAY OUT", "WAGERED CREDITS")
    for title in expected_titles:
        if title not in group_titles:
            issues.append(f"missing group box {title!r}")
    missing = frozenset(MASTER_TRACKED_CODES) - value_label_codes
    if missing:
        issues.append(f"missing value labels for codes: {sorted(missing)}")
    if not has_credit_section_totals:
        issues.append("Credit In / Credit Out section totals required")
    return issues


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
            from network.goldclub_paths import layout_requires_smb, resolve_goldclub_layout

            layout = resolve_goldclub_layout(self._scan_root)
            host = _extract_unc_host(self._scan_root) or self._target_ip
            try:
                sys.__stdout__.write(
                    f"\n[WORKER-THREAD] Thread started (py_tid={threading.get_ident()}) "
                    f"for host='{host}' scan_root='{self._scan_root}' "
                    f"layout={layout.kind.value if layout else 'none'}\n"
                )
                sys.__stdout__.flush()
            except Exception:
                pass
            if layout_requires_smb(layout) and host and not is_smb_alive(host, timeout=1.0):
                try:
                    sys.__stdout__.write(f"[WORKER-THREAD] SMB Check FAILED for {host}\n")
                    sys.__stdout__.flush()
                except Exception:
                    pass
                self.error.emit(f"Connection Failed: {host}")
                self.finished.emit({})
                return
            try:
                sys.__stdout__.write(f"[WORKER-THREAD] Loading cabinet state (local/USB/UNC).\n")
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


class OneHandCheckWorker(QObject):
    finished = Signal(str, object, object)  # ip, running: bool | None, smb_reachable: bool | None

    def __init__(self, ip: str) -> None:
        super().__init__(None)
        self._ip = (ip or "").strip()

    def run(self) -> None:
        from network.health_monitor import check_onehand_status

        try:
            status = check_onehand_status(self._ip) if self._ip else None
            if status is None:
                self.finished.emit(self._ip, None, None)
                return
            self.finished.emit(self._ip, status.running, status.smb_reachable)
        except Exception:  # noqa: BLE001
            self.finished.emit(self._ip, None, None)


class MeterFetchWorker(QObject):
    finished = Signal(object)  # SasMeterFetchResult
    error = Signal(str)

    def __init__(
        self,
        *,
        com_port: str,
        com_baud: int,
        skip_bill_polls: bool = True,
        cached_profile: tuple[str, int, bool] | None = None,
    ) -> None:
        super().__init__(None)
        self._com_port = (com_port or "").strip()
        self._com_baud = int(com_baud)
        self._skip_bill_polls = skip_bill_polls
        self._cached_profile = cached_profile

    def run(self) -> None:
        try:
            import sys

            from network.sas_serial_meters import fetch_meters_over_serial

            sys.__stdout__.write(
                f"[COM-FETCH] Starting on {self._com_port} "
                f"(bill_lps={'skip' if self._skip_bill_polls else 'full'})\n"
            )
            result = fetch_meters_over_serial(
                port=self._com_port,
                baud=self._com_baud,
                force_capture=True,
                skip_bill_polls=self._skip_bill_polls,
                cached_profile=self._cached_profile,
            )
            lines = (getattr(result, "paste_text", "") or "").count("\n") + 1
            sys.__stdout__.write(
                f"[COM-FETCH] OK {getattr(result, 'port_used', self._com_port)} "
                f"{getattr(result, 'wire_mode', '')}@{getattr(result, 'baud', '')} "
                f"({lines} paste lines)\n"
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            import sys

            sys.__stderr__.write(f"[COM-FETCH] FAILED: {exc}\n")
            self.error.emit(str(exc))


class SasVerifyDialog(QDialog):
    def __init__(
        self,
        vm: object,
        pool: QThreadPool,
        *,
        scan_root: str,
        remote_ip: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vm = vm
        self._pool = pool
        from network.goldclub_paths import extract_ip_from_path, resolve_log_scan_root

        hint = (scan_root or "").strip()
        discovery = resolve_log_scan_root(
            hint,
            remote_ip=remote_ip or extract_ip_from_path(hint) or None,
        )
        self._scan_root = discovery.scan_root
        self._scan_game_kind = discovery.game_kind
        self._machine_state: dict[str, str] = {}
        self._machine_state_loaded = False
        self._compare_thread: QThread | None = None
        self._compare_worker: CompareWorker | None = None
        self._meter_fetch_thread: QThread | None = None
        self._meter_fetch_worker: MeterFetchWorker | None = None
        self._cabinet_ui_refresh_pending = False
        self._onehand_check_thread: QThread | None = None
        self._onehand_check_worker: OneHandCheckWorker | None = None
        self._onehand_running: bool | None = None
        self._onehand_smb_reachable: bool | None = None
        self._onehand_check_ip = ""
        self._onehand_check_pending = False
        self._last_parsed_rows: list[Sas6FRow] = []
        self._last_bill_in_rows: list = []
        self._last_bill_out_rows: list = []
        self._sas_2f_values: dict[str, str] = {}
        self._currency = EgmCurrency()
        self._show_dollars = False
        self._column_actions: dict[int, QAction] = {}
        self._columns_menu: QMenu | None = None
        self._prefetch_started = False
        self._cached_meter_result: object | None = None
        self._meter_fetch_error: str | None = None
        self._meter_fetch_user_clicked_apply = False
        self._loaded_cabinet_scan_root = ""
        self._cabinet_compare_prefetch = False
        self._meter_fetch_prefetch = False
        self._meter_prefetch_retried = False
        self._accept_worker_signals = True
        self._last_displayed_paste_fingerprint = ""

        self.setWindowTitle("SAS accounting verification")
        from gui.app_branding import apply_window_branding

        apply_window_branding(self)
        self.setWindowFlags(_SAS_VERIFY_WINDOW_FLAGS)
        self.setMinimumSize(800, 560)
        self.resize(1180, 780)

        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.addWidget(self._build_view_menu_bar())
        root.addWidget(
            QLabel(
                "Cabinet and COM meters prefetch when this dialog opens "
                "(close IGT SAS tester first if it holds the COM port). "
                "Tables fill automatically; click Refresh Meters to capture again from COM, "
                "or paste TX/RX and Compare."
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
            "Capture SAS 6F meters over COM (IGT five polls). "
            "Prefetch runs when this dialog opens and fills the tables automatically. "
            "Click again to refresh from COM. Bill LPs are skipped during prefetch."
        )
        self._btn_get_meters.clicked.connect(self._on_get_meters_clicked)
        scan_row.addWidget(self._btn_get_meters)
        root.addLayout(scan_row)

        self._prefetch_status_label = QLabel("")
        self._prefetch_status_label.setWordWrap(True)
        self._prefetch_status_label.setStyleSheet(
            "QLabel { color: #475569; padding: 2px 0; }"
        )
        root.addWidget(self._prefetch_status_label)

        self._onehand_warning = QLabel("")
        self._onehand_warning.setWordWrap(True)
        self._onehand_warning.setTextFormat(Qt.TextFormat.RichText)
        self._onehand_warning.setStyleSheet(
            "QLabel { background-color: #fef3c7; color: #78350f; padding: 6px 8px; "
            "border: 1px solid #f59e0b; border-radius: 4px; }"
        )
        self._onehand_warning.hide()
        root.addWidget(self._onehand_warning)

        self._onehand_check_timer = QTimer(self)
        self._onehand_check_timer.setSingleShot(True)
        self._onehand_check_timer.setInterval(450)
        self._onehand_check_timer.timeout.connect(self._run_onehand_check)
        self._scan_root_edit.textChanged.connect(self._on_scan_root_edit_changed)

        self._paste = QTextEdit()
        self._paste.setPlaceholderText("Paste TX>= / RX<= lines here…")
        self._paste.setMinimumHeight(56)
        self._paste.setMaximumHeight(160)

        self._table = self._make_verify_table_widget()
        self._verify_tables: tuple[QTableWidget, ...] = (self._table,)
        self._accounting_box: QGroupBox | None = None
        self._master_value_labels: dict[str, QLabel] = {}
        self._master_value_label_codes: dict[str, str] = {}
        self._master_credit_in_total: QLabel | None = None
        self._master_credit_out_total: QLabel | None = None
        self._game_perf_labels: dict[str, QLabel] = {}
        self._game_residual_labels: dict[str, QLabel] = {}
        self._game_residual_state_keys: dict[str, tuple[str, ...]] = {}
        self._game_yield_chart: MachineYieldChartWidget | None = None
        self._game_theme_combo: QComboBox | None = None
        self._game_paytable_combo: QComboBox | None = None
        self._theme_perf_by_paytable: dict[str, dict[str, dict[str, str]]] = {}
        self._game_catalog_folders: dict[str, str] = {}
        self._game_theme_ids: list[str] = []
        self._transfer_amount_labels: dict[str, QLabel] = {}
        self._transfer_count_labels: dict[str, QLabel] = {}
        self._transfer_amount_codes: dict[str, str] = {}
        self._transfer_count_key_ids: dict[str, str] = {}
        self._transfer_promo_out_keys: tuple[str, ...] = TRANSFER_PROMO_TICKET_OUT_STATE_KEYS
        self._security_value_labels: dict[str, QLabel] = {}
        self._security_state_keys: dict[str, tuple[str, ...]] = {}
        self._game_tab = self._build_game_tab()
        self._master_tab = self._build_master_tab()
        self._transfer_tab = self._build_transfer_tab()
        self._security_tab = self._build_security_tab()
        self._accounting_tab = self._build_accounting_tab()
        self._load_column_visibility_prefs()
        self._apply_column_visibility()
        for tbl in self._verify_tables:
            tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tbl.customContextMenuRequested.connect(
                lambda pos, t=tbl: self._on_verify_table_context_menu(t, pos)
            )

        bills_in_box = QGroupBox("BILL IN")
        self._bills_table = self._make_bills_table_widget()
        self._bills_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bills_table.customContextMenuRequested.connect(self._on_bills_in_table_context_menu)
        center_table_in_group_box(bills_in_box, self._bills_table)
        bills_out_box = QGroupBox("BILL OUT")
        self._bills_out_table = self._make_bills_table_widget()
        self._bills_out_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bills_out_table.customContextMenuRequested.connect(self._on_bills_out_table_context_menu)
        center_table_in_group_box(bills_out_box, self._bills_out_table)
        self._bill_reject_label = QLabel(format_bill_reject_count_label(None))
        self._bill_reject_label.setContentsMargins(4, 2, 4, 0)
        bills_cluster = meter_panel_cluster(bills_in_box, bills_out_box)
        bills_tab = QWidget()
        bills_tab_layout = meter_tab_outer_layout(bills_tab)
        center_widget_in_panel(bills_tab_layout, bills_cluster)
        self._bill_reject_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        bills_tab_layout.addWidget(self._bill_reject_label)
        self._bills_in_box = bills_in_box
        self._bills_out_box = bills_out_box

        self._coin_tables: dict[str, QTableWidget] = {}
        self._coin_boxes: dict[str, QGroupBox] = {}
        coins_tab = self._build_coins_tab()
        self._apply_meter_panel_styles()

        self._meter_tabs = QTabWidget()
        self._meter_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._meter_tabs.setStyleSheet(meter_tabs_stylesheet())
        self._meter_tabs.addTab(self._accounting_tab, _METER_TAB_NAMES[TAB_ACCOUNTING])
        self._meter_tabs.addTab(self._game_tab, _METER_TAB_NAMES[TAB_GAME])
        self._meter_tabs.addTab(self._master_tab, _METER_TAB_NAMES[TAB_MASTER])
        self._meter_tabs.addTab(bills_tab, _METER_TAB_NAMES[TAB_BILLS])
        self._meter_tabs.addTab(coins_tab, _METER_TAB_NAMES[TAB_COINS])
        self._meter_tabs.addTab(self._transfer_tab, _METER_TAB_NAMES[TAB_TRANSFER])
        self._meter_tabs.addTab(self._security_tab, _METER_TAB_NAMES[TAB_SECURITY])
        self._meter_tabs.currentChanged.connect(self._on_meter_tab_changed)
        self._on_meter_tab_changed(self._meter_tabs.currentIndex())

        self._content_split = QSplitter(Qt.Orientation.Vertical)
        self._content_split.addWidget(self._paste)
        self._content_split.addWidget(self._meter_tabs)
        # Table should absorb almost all resize; paste stays a compact strip.
        self._content_split.setStretchFactor(0, 1)
        self._content_split.setStretchFactor(1, 9)
        self._content_split.setChildrenCollapsible(False)
        self._split_meter_dominant_applied = False
        self._window_geometry_restored = False
        root.addWidget(self._content_split, stretch=1)

        # Ctrl+C copies FULL rows (all columns) as TSV from whichever meter tab has focus.
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self._meter_tabs)
        copy_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_sc.activated.connect(self._copy_focused_table_row_tsv)

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
            app.aboutToQuit.connect(lambda: self._stop_onehand_check_thread(wait_ms=500))

    def _cabinet_ip_from_scan_root(self) -> str:
        from network.health_monitor import is_valid_remote_cabinet_ip

        raw = _extract_unc_host(self._scan_root_edit.text() or self._scan_root)
        return raw if is_valid_remote_cabinet_ip(raw) else ""

    def _onehand_check_uses_local(self) -> bool:
        from network.goldclub_paths import GoldclubLayoutKind, resolve_goldclub_layout

        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            return True
        layout = resolve_goldclub_layout(sr)
        if layout and layout.kind in (
            GoldclubLayoutKind.USB_EXPORT,
            GoldclubLayoutKind.LOCAL_CABINET,
        ):
            return True
        return not self._cabinet_ip_from_scan_root()

    @staticmethod
    def _make_verify_table_widget() -> QTableWidget:
        table = QTableWidget(0, COL_COUNT)
        table.setHorizontalHeaderLabels(
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
        table.setColumnWidth(COL_6F_CODE, _VERIFY_COL_MIN_WIDTHS[COL_6F_CODE])
        table.setColumnWidth(COL_WIRE_ID, _VERIFY_COL_MIN_WIDTHS[COL_WIRE_ID])
        table.setColumnWidth(COL_IGT_POLL, _VERIFY_COL_MIN_WIDTHS[COL_IGT_POLL])
        table.setColumnWidth(COL_IGT_METER, _VERIFY_COL_MIN_WIDTHS[COL_IGT_METER])
        table.setColumnWidth(COL_METER_NAME, _VERIFY_COL_MIN_WIDTHS[COL_METER_NAME])
        table.horizontalHeader().setStretchLastSection(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.horizontalHeader().setFixedHeight(22)
        table.setShowGrid(True)
        configure_verify_table_scroll(table)
        table.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        return table

    def _build_accounting_tab(self) -> QWidget:
        """EGM-style centered Meters panel (same shell as Bills/Coins/Master)."""
        body = QWidget()
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tab_layout = meter_tab_outer_layout(body)
        box = QGroupBox("Meters")
        wrap_expand_verify_table_in_group_box(box, self._table)
        self._accounting_box = box
        stretch_widget_in_panel(tab_layout, box)
        return body

    def _build_game_tab(self) -> QWidget:
        """EGM-style Performance Meters / Residual Credit / Yield chart layout."""
        body = QWidget()
        root = meter_tab_outer_layout(body)

        self._game_theme_combo = QComboBox()
        self._game_theme_combo.addItem(GAME_THEME_TOTAL)
        self._game_theme_combo.setFont(meter_panel_font())
        self._game_theme_combo.setMinimumWidth(160)
        self._game_theme_combo.setMaximumWidth(240)
        self._game_theme_combo.currentIndexChanged.connect(self._on_game_theme_filter_changed)
        self._game_paytable_combo = QComboBox()
        self._game_paytable_combo.addItem(GAME_THEME_TOTAL)
        self._game_paytable_combo.setEnabled(False)
        self._game_paytable_combo.setFont(meter_panel_font())
        self._game_paytable_combo.setMinimumWidth(120)
        self._game_paytable_combo.setMaximumWidth(200)
        self._game_paytable_combo.currentIndexChanged.connect(self._on_game_paytable_filter_changed)
        denom_combo = QComboBox()
        denom_combo.addItem(GAME_THEME_TOTAL)
        denom_combo.setEnabled(False)
        denom_combo.setFont(meter_panel_font())
        denom_combo.setMinimumWidth(100)
        denom_combo.setMaximumWidth(160)
        root.addLayout(
            meter_centered_filter_row(
                self._game_theme_combo,
                self._game_paytable_combo,
                denom_combo,
            )
        )

        def _perf_row(
            form: QFormLayout,
            title: str,
            key: str,
            *,
            indent: bool = False,
            bold_label: bool = False,
            bold_value: bool = False,
        ) -> None:
            lbl = meter_value_label(bold=bold_value)
            self._game_perf_labels[key] = lbl
            form.addRow(meter_row_label(title, indent=indent, bold=bold_label), lbl)

        perf = QGroupBox("Performance Meters")
        perf_form = meter_subpanel_form_layout()
        _perf_row(perf_form, "Games Played", "played")
        _perf_row(perf_form, "Games Won", "won")
        _perf_row(perf_form, "Games Lost", "lost")
        _perf_row(perf_form, "Bet", "bet")
        _perf_row(perf_form, "Win", "win", bold_label=True, bold_value=True)
        _perf_row(perf_form, "Game Win", "game_win", indent=True)
        _perf_row(perf_form, "Bonus Win", "bonus_win", indent=True)
        _perf_row(perf_form, "SAS Bonus Win", "sas_bonus", indent=True)
        _perf_row(perf_form, "Progr. Win", "prog_win", indent=True)
        _perf_row(perf_form, "Bet - Win", "bet_minus_win", bold_label=True, bold_value=True)
        _perf_row(perf_form, "Machine Yield", "yield", bold_label=True, bold_value=True)
        _perf_row(perf_form, "Machine Hold", "hold", bold_label=True, bold_value=True)
        center_layout_in_group_box(perf, perf_form)

        residual = QGroupBox("Residual Credit Removal Feature")
        residual_form = meter_subpanel_form_layout()
        for label, state_keys in GAME_RESIDUAL_ROWS:
            key = label.lower().replace(" ", "_")
            val = meter_value_label()
            self._game_residual_labels[key] = val
            self._game_residual_state_keys[key] = state_keys
            residual_form.addRow(meter_row_label(label), val)
        center_layout_in_group_box(residual, residual_form)

        left_inner = QVBoxLayout()
        left_inner.setContentsMargins(0, 0, 0, 0)
        left_inner.setSpacing(_METER_PANEL_CLUSTER_SPACING)
        left_inner.addWidget(perf)
        left_inner.addWidget(residual)
        left_panel = QWidget()
        left_panel.setLayout(left_inner)
        left_panel.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)

        chart_box = QGroupBox("Machine Yield Chart")
        chart_layout = QVBoxLayout(chart_box)
        chart_layout.setContentsMargins(12, 10, 12, 10)
        self._game_yield_chart = MachineYieldChartWidget()
        chart_layout.addWidget(self._game_yield_chart)
        chart_box.setMinimumWidth(300)
        chart_box.setMinimumHeight(260)
        chart_box.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)

        self._game_perf_box = perf
        self._game_residual_box = residual
        self._game_chart_box = chart_box

        center_widget_in_panel(root, meter_panel_cluster(left_panel, chart_box))
        return body

    def _build_master_tab(self) -> QWidget:
        """EGM-style Master summary matching cabinet meter UI layout."""
        body = QWidget()
        body.setObjectName("masterTabBody")

        def _section_label(text: str) -> QLabel:
            return meter_row_label(text, bold=True)

        def _sub_label(text: str) -> QLabel:
            return meter_row_label(text, indent=True)

        def _row(
            form: QFormLayout,
            title: str | QLabel,
            key: str,
            code: str,
            *,
            bold_value: bool = False,
        ) -> None:
            lbl = meter_value_label(bold=bold_value)
            self._master_value_labels[key] = lbl
            self._master_value_label_codes[key] = code
            form.addRow(title, lbl)

        credit = QGroupBox("TOTAL CREDIT")
        credit_form = meter_subpanel_form_layout()
        self._master_credit_in_total = meter_value_label(bold=True)
        credit_form.addRow(_section_label("Credit In"), self._master_credit_in_total)
        for title, code in MASTER_CREDIT_IN_ROWS:
            _row(credit_form, _sub_label(title), f"in:{code}", code)
        self._master_credit_out_total = meter_value_label(bold=True)
        credit_form.addRow(_section_label("Credit Out"), self._master_credit_out_total)
        for title, code in MASTER_CREDIT_OUT_ROWS:
            _row(credit_form, _sub_label(title), f"out:{code}", code)
        self._master_total_credit = meter_value_label(bold=True)
        credit_form.addRow(_section_label("Total Credit"), self._master_total_credit)
        self._master_inout_pct = meter_value_label(bold=True)
        credit_form.addRow(_section_label("TOTAL IN-OUT %"), self._master_inout_pct)
        center_layout_in_group_box(credit, credit_form)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(_METER_PANEL_CLUSTER_SPACING)

        handpay = QGroupBox("HANDPAY OUT")
        hp_form = meter_subpanel_form_layout()
        _row(hp_form, _section_label("Cancelled Credits"), "hp:0003", "0003", bold_value=True)
        _row(hp_form, _section_label("Total Jackpot"), "hp:0002", "0002", bold_value=True)
        for title, code in MASTER_JACKPOT_SUB_ROWS:
            _row(hp_form, _sub_label(title), f"hp:{code}", code)
        center_layout_in_group_box(handpay, hp_form)
        right.addWidget(handpay)

        cancelled = QGroupBox("")
        cancelled.setFlat(True)
        c_form = meter_subpanel_form_layout()
        _row(c_form, _section_label("Total Cancelled Credits"), "cancelled:0004", MASTER_CANCELLED_CODE)
        center_layout_in_group_box(cancelled, c_form)
        right.addWidget(cancelled)

        wagered = QGroupBox("WAGERED CREDITS")
        w_form = meter_subpanel_form_layout()
        for title, code in MASTER_WAGERED_ROWS:
            _row(w_form, meter_row_label(title), f"wager:{code}", code)
        center_layout_in_group_box(wagered, w_form)
        right.addWidget(wagered)

        self._master_credit_box = credit
        self._master_handpay_box = handpay
        self._master_cancelled_box = cancelled
        self._master_wagered_box = wagered

        right_panel = QWidget()
        right_panel.setLayout(right)
        right_panel.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)

        tab_layout = meter_tab_outer_layout(body)
        center_widget_in_panel(tab_layout, meter_panel_cluster(credit, right_panel))
        return body

    def _build_coins_tab(self) -> QWidget:
        """EGM-style 2x2 COIN IN / OUT / TO DROP BOX / TO HOPPER panels."""
        body = QWidget()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        panel_positions = (
            ("in", 0, 0),
            ("out", 0, 1),
            ("drop", 1, 0),
            ("hopper", 1, 1),
        )
        titles = dict(_COINS_PANELS)
        for panel_id, row, col in panel_positions:
            box = QGroupBox(titles[panel_id])
            table = self._make_coins_table_widget()
            center_table_in_group_box(box, table)
            self._coin_tables[panel_id] = table
            self._coin_boxes[panel_id] = box
            grid.addWidget(box, row, col)
        cluster = QWidget()
        cluster.setLayout(grid)
        cluster.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
        tab_layout = meter_tab_outer_layout(body)
        center_widget_in_panel(tab_layout, cluster)
        return body

    def _build_transfer_tab(self) -> QWidget:
        """EGM-style TICKET / CASHLESS transfer panels."""
        body = QWidget()

        def _cell_label(
            text: str,
            *,
            bold: bool = False,
            indent: bool = False,
            align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        ) -> QLabel:
            lbl = QLabel(text)
            lbl.setAlignment(align)
            lbl.setFont(meter_panel_font(bold=bold))
            lbl.setMinimumHeight(_METER_ROW_MIN_HEIGHT)
            if indent:
                lbl.setContentsMargins(10, 0, 0, 0)
            return lbl

        def _amount_label(*, bold: bool = False) -> QLabel:
            return meter_grid_value_label(bold=bold, min_width=_METER_TRANSFER_AMOUNT_WIDTH)

        def _count_label(*, bold: bool = False) -> QLabel:
            return meter_grid_value_label(bold=bold, min_width=_METER_COUNT_MIN_WIDTH)

        def _register_amount(key: str, code: str, lbl: QLabel) -> None:
            self._transfer_amount_labels[key] = lbl
            self._transfer_amount_codes[key] = code

        def _register_count(key: str, count_id: str, lbl: QLabel) -> None:
            self._transfer_count_labels[key] = lbl
            self._transfer_count_key_ids[key] = count_id

        def _add_column_headers(grid: QGridLayout, row: int) -> int:
            grid.addWidget(_cell_label(""), row, 0)
            amt_hdr = _cell_label(
                "Amount",
                bold=True,
                align=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            )
            amt_hdr.setMinimumWidth(_METER_TRANSFER_AMOUNT_WIDTH)
            cnt_hdr = _cell_label(
                "Count",
                bold=True,
                align=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            )
            cnt_hdr.setMinimumWidth(_METER_COUNT_MIN_WIDTH)
            grid.addWidget(amt_hdr, row, 1)
            grid.addWidget(cnt_hdr, row, 2)
            return row + 1

        def _add_section(
            grid: QGridLayout,
            row: int,
            *,
            prefix: str,
            title: str,
            amount_code: str,
            count_id: str,
            buckets: tuple[tuple[str, str], ...],
        ) -> int:
            amt_lbl = _amount_label(bold=True)
            cnt_lbl = _count_label(bold=True)
            _register_amount(f"{prefix}:total", amount_code, amt_lbl)
            _register_count(f"{prefix}:total", count_id, cnt_lbl)
            grid.addWidget(_cell_label(title, bold=True), row, 0)
            grid.addWidget(amt_lbl, row, 1)
            grid.addWidget(cnt_lbl, row, 2)
            row += 1
            for label, code in buckets:
                sub_amt = _amount_label()
                sub_cnt = _count_label()
                slug = code or label.lower().replace("-", "").replace(" ", "")
                key = f"{prefix}:{slug}"
                _register_amount(key, code, sub_amt)
                _register_count(key, f"bucket:{code}" if code else f"promo_out:{prefix}", sub_cnt)
                grid.addWidget(_cell_label(label, indent=True), row, 0)
                grid.addWidget(sub_amt, row, 1)
                grid.addWidget(sub_cnt, row, 2)
                row += 1
            return row

        def _add_count_only_row(
            grid: QGridLayout,
            row: int,
            *,
            prefix: str,
            title: str,
            count_id: str,
        ) -> int:
            cnt_lbl = _count_label(bold=True)
            _register_count(f"{prefix}:{count_id}", count_id, cnt_lbl)
            grid.addWidget(_cell_label(title, bold=True), row, 0, 1, 2)
            grid.addWidget(cnt_lbl, row, 2)
            return row + 1

        def _build_panel(title: str, *, prefix: str, sections: list) -> QGroupBox:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setContentsMargins(4, 6, 4, 4)
            grid.setVerticalSpacing(0)
            grid.setHorizontalSpacing(8)
            row = _add_column_headers(grid, 0)
            for section in sections:
                kind = section[0]
                if kind == "section":
                    _, sect_title, amount_code, count_id, buckets = section
                    row = _add_section(
                        grid,
                        row,
                        prefix=prefix,
                        title=sect_title,
                        amount_code=amount_code,
                        count_id=count_id,
                        buckets=buckets,
                    )
                elif kind == "count_only":
                    _, sect_title, count_id = section
                    row = _add_count_only_row(
                        grid,
                        row,
                        prefix=prefix,
                        title=sect_title,
                        count_id=count_id,
                    )
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)
            grid.setColumnStretch(2, 0)
            center_layout_in_group_box(box, grid)
            return box

        ticket = _build_panel(
            "TICKET",
            prefix="ticket",
            sections=[
                (
                    "section",
                    "TICKET IN",
                    TRANSFER_TICKET_IN_CODE,
                    "ticket:in",
                    TRANSFER_TICKET_IN_BUCKETS,
                ),
                (
                    "section",
                    "TICKET OUT",
                    TRANSFER_TICKET_OUT_CODE,
                    "ticket:out",
                    TRANSFER_TICKET_OUT_BUCKETS,
                ),
                ("count_only", "ACCEPTED TICKETS", "ticket:accepted"),
                ("count_only", "PRINTED TICKETS", "ticket:printed"),
            ],
        )
        cashless = _build_panel(
            "CASHLESS",
            prefix="cashless",
            sections=[
                (
                    "section",
                    "CASHLESS IN",
                    TRANSFER_CASHLESS_IN_CODE,
                    "cashless:in",
                    TRANSFER_CASHLESS_IN_BUCKETS,
                ),
                (
                    "section",
                    "CASHLESS OUT",
                    TRANSFER_CASHLESS_OUT_CODE,
                    "cashless:out",
                    TRANSFER_CASHLESS_OUT_BUCKETS,
                ),
                ("count_only", "TRANSFERS IN", "cashless:transfers_in"),
                ("count_only", "TRANSFERS OUT", "cashless:transfers_out"),
            ],
        )
        self._transfer_ticket_box = ticket
        self._transfer_cashless_box = cashless
        tab_layout = meter_tab_outer_layout(body)
        center_widget_in_panel(tab_layout, meter_panel_cluster(ticket, cashless))
        return body

    def _build_security_tab(self) -> QWidget:
        """EGM-style Door Open Count / Games Since panels."""
        body = QWidget()

        def _build_panel(title: str, rows: tuple[tuple[str, tuple[str, ...]], ...]) -> QGroupBox:
            box = QGroupBox(title)
            form = meter_subpanel_form_layout()
            for label, state_keys in rows:
                key = label.lower().replace(" ", "_")
                val = meter_value_label()
                self._security_value_labels[key] = val
                self._security_state_keys[key] = state_keys
                form.addRow(meter_row_label(label), val)
            center_layout_in_group_box(box, form)
            return box

        doors = _build_panel("Door Open Count", SECURITY_DOOR_ROWS)
        games = _build_panel("Games Since", SECURITY_GAMES_ROWS)
        self._security_doors_box = doors
        self._security_games_box = games
        tab_layout = meter_tab_outer_layout(body)
        center_widget_in_panel(tab_layout, meter_panel_cluster(doors, games))
        return body

    @staticmethod
    def _make_bills_table_widget() -> QTableWidget:
        table = QTableWidget(0, _BILLS_TABLE_COLUMN_COUNT)
        table.setHorizontalHeaderLabels(list(_BILLS_TABLE_HEADERS))
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        table.setColumnWidth(_BILLS_COL_BILL, 72)
        table.setColumnWidth(_BILLS_COL_AMOUNT, 72)
        table.setColumnWidth(_BILLS_COL_COUNT_IDX, 52)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setFixedHeight(22)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.setShowGrid(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QTableWidget.SizeAdjustPolicy.AdjustToContents)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        return table

    @staticmethod
    def _make_coins_table_widget() -> QTableWidget:
        table = QTableWidget(0, _COINS_TABLE_COLUMN_COUNT)
        table.setHorizontalHeaderLabels(list(_COINS_TABLE_HEADERS))
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        table.setColumnWidth(_COINS_COL_COIN, 72)
        table.setColumnWidth(_COINS_COL_AMOUNT, 72)
        table.setColumnWidth(_COINS_COL_COUNT_IDX, 52)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setFixedHeight(22)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.setShowGrid(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QTableWidget.SizeAdjustPolicy.AdjustToContents)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        return table

    def _apply_meter_panel_styles(self) -> None:
        light = surface_is_light(self.palette())
        table_style = bills_table_stylesheet(light=light)
        cancelled_style = master_cancelled_row_stylesheet(light=light)
        panel_style = master_tab_group_box_stylesheet(light=light)
        self._bills_in_box.setStyleSheet(panel_style)
        self._bills_out_box.setStyleSheet(panel_style)
        self._bills_table.setStyleSheet(table_style)
        self._bills_out_table.setStyleSheet(table_style)
        for panel_id, table in self._coin_tables.items():
            box = self._coin_boxes.get(panel_id)
            if box is not None:
                box.setStyleSheet(panel_style)
            table.setStyleSheet(table_style)
        self._master_credit_box.setStyleSheet(panel_style)
        self._master_handpay_box.setStyleSheet(panel_style)
        self._master_wagered_box.setStyleSheet(panel_style)
        self._master_cancelled_box.setStyleSheet(cancelled_style)
        self._transfer_ticket_box.setStyleSheet(panel_style)
        self._transfer_cashless_box.setStyleSheet(panel_style)
        self._security_doors_box.setStyleSheet(panel_style)
        self._security_games_box.setStyleSheet(panel_style)
        self._game_perf_box.setStyleSheet(panel_style)
        self._game_residual_box.setStyleSheet(panel_style)
        self._game_chart_box.setStyleSheet(panel_style)
        if self._accounting_box is not None:
            self._accounting_box.setStyleSheet(panel_style)
        self._table.setStyleSheet(table_style)
        reject_font = self._bill_reject_label.font()
        reject_font.setPointSize(9)
        self._bill_reject_label.setFont(reject_font)

    @staticmethod
    def _bill_table_item(
        text: str,
        *,
        align: Qt.AlignmentFlag,
        bold: bool = False,
        foreground: QColor | None = None,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(int(align))
        font = item.font()
        font.setPointSize(9)
        font.setBold(bold)
        item.setFont(font)
        if foreground is not None:
            item.setForeground(foreground)
        return item

    def _schedule_onehand_check(self) -> None:
        self._onehand_check_timer.start()

    def _stop_onehand_check_thread(self, wait_ms: int = 300) -> None:
        self._quit_or_orphan_thread(self._onehand_check_thread, wait_ms)

    def _on_onehand_check_thread_finished(self) -> None:
        self._onehand_check_thread = None
        self._onehand_check_worker = None

    def _run_onehand_check(self) -> None:
        if self._onehand_check_uses_local():
            from network.health_monitor import check_onehand_status_local

            status = check_onehand_status_local()
            self._onehand_check_pending = False
            self._onehand_check_ip = "local"
            self._onehand_running = status.running
            self._onehand_smb_reachable = status.smb_reachable
            self._update_onehand_warning_label("local")
            return
        ip = self._cabinet_ip_from_scan_root()
        if not ip:
            self._onehand_running = None
            self._onehand_smb_reachable = None
            self._onehand_check_ip = ""
            self._onehand_check_pending = False
            self._onehand_warning.hide()
            return
        if ip == self._onehand_check_ip and self._onehand_running is not None:
            self._update_onehand_warning_label(ip)
            return
        self._onehand_check_pending = True
        self._onehand_warning.hide()
        self._stop_onehand_check_thread(wait_ms=200)
        self._onehand_check_thread = QThread(self)
        self._onehand_check_worker = OneHandCheckWorker(ip)
        self._onehand_check_worker.moveToThread(self._onehand_check_thread)
        self._onehand_check_thread.started.connect(self._onehand_check_worker.run)
        self._onehand_check_worker.finished.connect(
            self._on_onehand_check_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._onehand_check_worker.finished.connect(self._onehand_check_thread.quit)
        self._onehand_check_worker.finished.connect(self._onehand_check_worker.deleteLater)
        self._onehand_check_thread.finished.connect(self._on_onehand_check_thread_finished)
        self._onehand_check_thread.finished.connect(self._onehand_check_thread.deleteLater)
        self._onehand_check_thread.start()

    def _on_onehand_check_finished(self, ip: str, running: object, smb_reachable: object) -> None:
        if not self._worker_signals_enabled():
            return
        if (ip or "").strip() != self._cabinet_ip_from_scan_root():
            return
        self._onehand_check_pending = False
        self._onehand_check_ip = (ip or "").strip()
        self._onehand_running = running if isinstance(running, bool) else None
        self._onehand_smb_reachable = smb_reachable if isinstance(smb_reachable, bool) else None
        self._update_onehand_warning_label(self._onehand_check_ip)

    def _update_onehand_warning_label(self, ip: str) -> None:
        from network.health_monitor import onehand_warning_text

        if self._onehand_check_pending:
            self._onehand_warning.hide()
            return
        text = onehand_warning_text(
            ip,
            running=self._onehand_running,
            smb_reachable=self._onehand_smb_reachable,
            com_meters_ok=self._cached_meter_result is not None,
        )
        if text:
            self._onehand_warning.setText(text)
            self._onehand_warning.show()
        else:
            self._onehand_warning.hide()

    def _load_com_port_prefs(self) -> None:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD, DEFAULT_SAS_COM_PORT

            port = s.value(_KEY_COM_PORT, DEFAULT_SAS_COM_PORT, type=str)
            self._preferred_com_port = (port or DEFAULT_SAS_COM_PORT).strip()
            self._com_baud = int(s.value(_KEY_COM_BAUD, DEFAULT_SAS_COM_BAUD, type=int))
            wire = (s.value(_KEY_COM_WIRE, "", type=str) or "").strip().lower()
            rts = s.value(_KEY_COM_RTS, False, type=bool)
            self._cached_serial_profile: tuple[str, int, bool] | None = None
            if wire:
                self._cached_serial_profile = (wire, self._com_baud, bool(rts))
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
            profile = getattr(self, "_cached_serial_profile", None)
            if profile:
                s.setValue(_KEY_COM_WIRE, profile[0])
                s.setValue(_KEY_COM_RTS, bool(profile[2]))
        finally:
            s.endGroup()

    def _build_view_menu_bar(self) -> QMenuBar:
        bar = QMenuBar(self)
        view_menu = bar.addMenu("&View")
        self._columns_menu = view_menu.addMenu("&Columns")
        self._columns_menu.setToolTip(
            "Show or hide meter table columns (copy/export uses visible columns only)."
        )
        for col, label in _VERIFY_TABLE_COLUMNS:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(default_verify_column_visible(col))
            act.toggled.connect(lambda checked, c=col: self._on_column_visibility_toggled(c, checked))
            self._columns_menu.addAction(act)
            self._column_actions[col] = act
        return bar

    def _on_meter_tab_changed(self, index: int) -> None:
        self._sync_columns_menus_for_tab(index)

    def _sync_columns_menus_for_tab(self, tab_index: int) -> None:
        columns_active = tab_index in _METER_COLUMN_TABS
        if self._columns_menu is not None:
            self._columns_menu.setEnabled(columns_active)
        for act in self._column_actions.values():
            act.setEnabled(columns_active)

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
        for tbl in self._verify_tables:
            for col, act in self._column_actions.items():
                tbl.setColumnHidden(col, not act.isChecked())
            fit_verify_table_columns(tbl)

    def _column_visibility_map(self) -> dict[int, bool]:
        return {col: act.isChecked() for col, act in self._column_actions.items()}

    @staticmethod
    def _quit_or_orphan_thread(th: QThread | None, wait_ms: int) -> None:
        """Ask a worker thread to quit; if it is stuck in blocking network I/O,
        detach it from the dialog so its destructor never fires while running
        ("QThread: Destroyed while thread is still running"). The thread's
        finished→deleteLater connection cleans it up once the call returns.
        """
        if th is None:
            return
        try:
            if th.isRunning():
                th.quit()
                if not th.wait(wait_ms):
                    th.setParent(None)
        except Exception:
            pass

    def _stop_compare_thread(self, wait_ms: int = 300) -> None:
        self._quit_or_orphan_thread(self._compare_thread, wait_ms)

    def _stop_meter_fetch_thread(self, wait_ms: int = 300) -> None:
        self._quit_or_orphan_thread(self._meter_fetch_thread, wait_ms)
        self._meter_fetch_thread = None
        self._meter_fetch_worker = None

    def _worker_signals_enabled(self) -> bool:
        try:
            return bool(self._accept_worker_signals)
        except RuntimeError:
            return False

    def _meter_fetch_running(self) -> bool:
        th = self._meter_fetch_thread
        return th is not None and th.isRunning()

    def _compare_running(self) -> bool:
        th = self._compare_thread
        return th is not None and th.isRunning()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        SettingsManager.save_sas_verify_dialog_geometry(self)
        self._accept_worker_signals = False
        self._stop_compare_thread(wait_ms=500)
        self._stop_meter_fetch_thread(wait_ms=500)
        self._stop_onehand_check_thread(wait_ms=500)
        self._cached_meter_result = None
        self._meter_fetch_error = None
        if hasattr(self._vm, "invalidate_accounting_registers_cache"):
            try:
                self._vm.invalidate_accounting_registers_cache()
            except Exception:
                pass
        event.accept()
        # Exit dlg.exec() for title-bar X and taskbar/system-menu Close (SC_CLOSE).
        self.done(QDialog.DialogCode.Rejected)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._window_geometry_restored:
            self._window_geometry_restored = True
            SettingsManager.restore_sas_verify_dialog_geometry(self)
        # Saved geometry must not pin max height (blocks vertical resize on Windows).
        self.setMaximumSize(QSize(16777215, 16777215))
        self.setMinimumSize(800, 560)
        self._reload_game_theme_catalog()
        self._refresh_com_port_list(preserve_text=True)
        if not self._split_meter_dominant_applied:
            self._split_meter_dominant_applied = True
            split_h = max(self._content_split.height(), 480)
            paste_h = min(140, max(72, int(split_h * 0.13)))
            self._content_split.setSizes([paste_h, split_h - paste_h])
        QTimer.singleShot(0, self._start_prefetch)

    def _on_scan_root_edit_changed(self) -> None:
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if hasattr(self._vm, "invalidate_accounting_registers_cache"):
            try:
                self._vm.invalidate_accounting_registers_cache(sr)
            except Exception:
                pass
        self._schedule_onehand_check()
        if not self._prefetch_started:
            return
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if should_skip_cabinet_reload(
            scan_root=sr,
            loaded_scan_root=self._loaded_cabinet_scan_root,
            machine_state_loaded=self._machine_state_loaded,
        ):
            return
        if sr and not self._compare_running():
            self._begin_cabinet_compare(prefetch=True)

    def _cabinet_cache_valid(self) -> bool:
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        return should_skip_cabinet_reload(
            scan_root=sr,
            loaded_scan_root=self._loaded_cabinet_scan_root,
            machine_state_loaded=self._machine_state_loaded,
        )

    def _start_prefetch(self) -> None:
        if self._prefetch_started:
            return
        self._prefetch_started = True
        self._meter_prefetch_retried = False
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        self._update_prefetch_status("Prefetching: starting…")
        if self._scan_root:
            self._begin_cabinet_compare(prefetch=True)
        ip = self._cabinet_ip_from_scan_root()
        if ip:
            QTimer.singleShot(1500, self._run_onehand_check)
        # USB log exports are offline snapshots — do not grab the live COM port.
        from network.goldclub_paths import GoldclubLayoutKind, resolve_goldclub_layout

        layout = resolve_goldclub_layout(self._scan_root) if self._scan_root else None
        live_com = layout is None or layout.kind != GoldclubLayoutKind.USB_EXPORT
        if live_com and self._current_com_port():
            self._begin_meter_fetch(prefetch=True)
        self._update_prefetch_status()

    def _sync_get_meters_button_label(self) -> None:
        if self._meter_fetch_running():
            self._btn_get_meters.setText("Capturing COM…")
            return
        if self._meter_fetch_user_clicked_apply or self._last_parsed_rows:
            self._btn_get_meters.setText("Refresh Meters")
        else:
            self._btn_get_meters.setText("Get Meters")

    def _auto_apply_cached_meters_if_needed(self) -> None:
        if self._meter_fetch_running() or self._meter_fetch_user_clicked_apply:
            return
        if self._cached_meter_result is None:
            return
        try:
            if self._apply_meter_fetch_result(self._cached_meter_result):
                self._meter_fetch_user_clicked_apply = True
        except Exception:
            traceback.print_exc()
        finally:
            self._update_prefetch_status()

    def _update_prefetch_status(self, override: str | None = None) -> None:
        self._sync_get_meters_button_label()
        if override:
            self._prefetch_status_label.setText(override)
            return
        if self._cached_meter_result is not None and self._cabinet_cache_valid():
            if self._meter_fetch_user_clicked_apply:
                self._prefetch_status_label.setText(
                    "Meters displayed — click Refresh Meters to capture again from COM."
                )
            else:
                self._prefetch_status_label.setText("Applying prefetched COM meters…")
                QTimer.singleShot(0, self._auto_apply_cached_meters_if_needed)
            return
        if self._meter_fetch_error and not self._cached_meter_result:
            self._prefetch_status_label.setText(
                f"COM capture failed: {self._meter_fetch_error} "
                "(click Refresh Meters to retry)"
            )
            return
        parts: list[str] = []
        if self._compare_running():
            parts.append("cabinet")
        if self._meter_fetch_running():
            parts.append("COM capture")
        if parts:
            self._prefetch_status_label.setText(
                f"Prefetching: {' and '.join(parts)}…"
            )
            return
        if self._cached_meter_result is not None:
            if self._meter_fetch_user_clicked_apply:
                self._prefetch_status_label.setText(
                    "Meters displayed — click Refresh Meters to capture again from COM."
                )
            else:
                self._prefetch_status_label.setText("Applying prefetched COM meters…")
                QTimer.singleShot(0, self._auto_apply_cached_meters_if_needed)
            return
        if self._cabinet_cache_valid():
            self._prefetch_status_label.setText(
                "Cabinet loaded — prefetching COM meters (tables fill automatically)…"
            )
            return
        if not self._current_com_port():
            self._prefetch_status_label.setText(
                "Set COM port — prefetch will capture SAS meters when the dialog opens."
            )
            return
        self._prefetch_status_label.setText(
            "Cabinet loading… COM meters will appear automatically when capture finishes."
        )

    def _paste_fingerprint(self, paste_text: str) -> str:
        import hashlib

        raw = (paste_text or "").encode("utf-8", errors="replace")
        return hashlib.sha256(raw).hexdigest()[:16]

    def _meter_result_already_displayed(self, result: object) -> bool:
        paste_text = getattr(result, "paste_text", None)
        if not paste_text:
            return False
        fp = self._paste_fingerprint(str(paste_text))
        return fp == self._last_displayed_paste_fingerprint and bool(self._last_parsed_rows)

    def _begin_meter_fetch(self, *, prefetch: bool, force: bool = False) -> bool:
        """Start background COM capture (IGT 200 ms GP cadence in ``sas_serial_meters``).

        Prefetch skips bill long polls for speed; use a manual refresh for full bill LPs.
        """
        port = self._current_com_port()
        if not port:
            return False
        if self._meter_fetch_running():
            if force:
                self._stop_meter_fetch_thread(wait_ms=0)
            else:
                return False
        if self._cached_meter_result is not None and not force:
            return False
        self._meter_fetch_prefetch = prefetch
        self._meter_fetch_error = None
        self._btn_get_meters.setText("Capturing COM…")
        if not prefetch:
            self._btn_get_meters.setEnabled(False)
        else:
            self._update_prefetch_status()
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD

        self._meter_fetch_thread = QThread(self)
        self._meter_fetch_worker = MeterFetchWorker(
            com_port=port,
            com_baud=int(getattr(self, "_com_baud", DEFAULT_SAS_COM_BAUD)),
            skip_bill_polls=prefetch,
            cached_profile=getattr(self, "_cached_serial_profile", None),
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
        return True

    def _flush_pending_cabinet_ui_refresh(self) -> None:
        """Apply deferred cabinet panel refresh once COM work is finished."""
        if not self._cabinet_ui_refresh_pending:
            return
        if self._meter_fetch_running():
            return
        self._run_cabinet_ui_refresh()

    def _apply_meter_fetch_result(self, result: object, *, switch_tab: bool = False) -> bool:
        paste_text = getattr(result, "paste_text", None)
        port_used = getattr(result, "port_used", "") or ""
        wire_mode = getattr(result, "wire_mode", "") or ""
        baud = getattr(result, "baud", 0) or 0
        if port_used:
            self._set_com_port_selection(str(port_used))
        if wire_mode and baud:
            self._cached_serial_profile = (
                str(wire_mode).strip().lower(),
                int(baud),
                bool(getattr(result, "rts", False)),
            )
            self._com_baud = int(baud)
            self._save_com_port_prefs()
        if self._cached_meter_result is not None:
            self._update_onehand_warning_label(self._onehand_check_ip or "local")
        if not paste_text:
            self._flush_pending_cabinet_ui_refresh()
            return False
        paste_str = str(paste_text)
        if self._meter_result_already_displayed(result):
            if switch_tab:
                parsed = self._last_parsed_rows
                has_6f = any((r.sas_value_text or "").strip() for r in parsed)
                if has_6f:
                    self._meter_tabs.setCurrentIndex(0)
                elif self._last_bill_in_rows or self._last_bill_out_rows:
                    self._meter_tabs.setCurrentIndex(TAB_BILLS)
                self._paste.setFocus()
            self._flush_pending_cabinet_ui_refresh()
            return bool(self._last_parsed_rows or self._last_bill_in_rows)
        has_6f = False
        display_rows: list = []
        self.setUpdatesEnabled(False)
        try:
            self._paste.setPlainText(paste_str)
            if switch_tab:
                self._paste.setFocus()
            parsed = build_verify_6f_rows_from_paste(paste_str)
            bill_rows = getattr(result, "bill_rows", None) or ()
            bill_out_rows = getattr(result, "bill_out_rows", None) or ()
            from network.sas_serial_meters import build_bill_display_rows, build_bill_out_display_rows

            display_rows = build_bill_display_rows(
                bill_rows=bill_rows,
                paste_text=paste_str,
                machine_state=self._machine_state or None,
            )
            out_display = build_bill_out_display_rows(bill_rows=bill_out_rows)
            if display_rows or out_display:
                self._render_bills(
                    display_rows,
                    out_rows=out_display,
                    machine_state=self._machine_state or None,
                )
            has_6f = any((r.sas_value_text or "").strip() for r in parsed)
            if has_6f:
                self._last_parsed_rows = parsed
                self._sas_2f_values = parse_sas_2f_paste(paste_str)
                self._currency = _detect_egm_currency(
                    self._scan_root_edit.text() or self._scan_root, self._vm
                )
                self._update_dollar_toggle_label()
                self._render(
                    parsed_rows=parsed,
                    allow_machine_lookup=self._machine_state_loaded,
                )
                if switch_tab:
                    self._meter_tabs.setCurrentIndex(0)
            elif display_rows and switch_tab:
                self._meter_tabs.setCurrentIndex(TAB_BILLS)
            self._last_displayed_paste_fingerprint = self._paste_fingerprint(paste_str)
        finally:
            self.setUpdatesEnabled(True)
        try:
            p = self.parent()
            if p is not None and hasattr(p, "statusBar"):
                sb = p.statusBar()
                if sb is not None:
                    link = f"{port_used or self._current_com_port()}"
                    if wire_mode and baud:
                        link += f" ({wire_mode} @ {baud})"
                    sb.showMessage(
                        f"SAS meters displayed from session cache ({link}).",
                        6000,
                    )
        except Exception:
            pass
        self._flush_pending_cabinet_ui_refresh()
        return has_6f or bool(display_rows)

    def _on_meter_fetch_thread_finished(self) -> None:
        self._meter_fetch_thread = None
        self._meter_fetch_worker = None

    def _begin_get_meters_capture(self) -> None:
        if not self._worker_signals_enabled():
            return
        if self._cached_meter_result is not None:
            self._cached_meter_result = None
        self._meter_fetch_error = None
        self._meter_fetch_user_clicked_apply = False
        self._last_displayed_paste_fingerprint = ""
        ip = self._cabinet_ip_from_scan_root()
        if ip and self._onehand_running is False:
            QMessageBox.warning(
                self,
                "Get Meters",
                f"OneHand.exe is not running on {ip}.\n\n"
                "The SAS host link often returns no RX until the game client is started "
                "on the EGM (Aurum / CommCtrl). Start OneHand on the cabinet, then retry.",
            )
        elif ip and self._onehand_running is None and not self._onehand_check_pending:
            self._run_onehand_check()
        self._save_com_port_prefs()
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if self._scan_root and not self._compare_running() and not self._cabinet_cache_valid():
            self._begin_cabinet_compare(prefetch=True)
        if not self._begin_meter_fetch(prefetch=False, force=True):
            self._btn_get_meters.setEnabled(True)
            self._sync_get_meters_button_label()
            QMessageBox.warning(
                self,
                "Get Meters",
                "Could not start COM capture. Check the COM port and try again.",
            )

    def _on_get_meters_clicked(self) -> None:
        port = self._current_com_port()
        if not port:
            QMessageBox.warning(
                self,
                "Get Meters",
                "Enter a COM port (for example COM4).",
            )
            return
        action = meter_fetch_display_action(
            cached_result=self._cached_meter_result,
            fetch_running=self._meter_fetch_running(),
            user_already_applied=self._meter_fetch_user_clicked_apply,
        )
        if action == "wait":
            QMessageBox.information(
                self,
                "Get Meters",
                "Background COM capture is still running.\n\n"
                "Tables will populate automatically when it finishes.",
            )
            return
        self._btn_get_meters.setEnabled(False)
        self._btn_get_meters.setText("Capturing COM…")
        QTimer.singleShot(0, self._begin_get_meters_capture)

    def _on_meter_fetch_finished(self, result: object) -> None:
        if not self._worker_signals_enabled():
            return
        QTimer.singleShot(0, lambda r=result: self._apply_meter_fetch_complete(r))

    def _apply_meter_fetch_complete(self, result: object) -> None:
        if not self._worker_signals_enabled():
            return
        self._cached_meter_result = result
        self._meter_fetch_error = None
        self._btn_get_meters.setEnabled(True)
        self._meter_fetch_prefetch = False
        displayed = False
        try:
            displayed = bool(self._apply_meter_fetch_result(result))
        except Exception:
            traceback.print_exc()
        if displayed:
            self._meter_fetch_user_clicked_apply = True
        self._flush_pending_cabinet_ui_refresh()
        self._update_prefetch_status()

    def _on_meter_fetch_error(self, message: str) -> None:
        if not self._worker_signals_enabled():
            return
        self._meter_fetch_error = message or "Serial meter fetch failed."
        self._btn_get_meters.setEnabled(True)
        was_prefetch = self._meter_fetch_prefetch
        self._meter_fetch_prefetch = False
        self._refresh_com_port_list(preserve_text=True)
        self._flush_pending_cabinet_ui_refresh()
        msg = self._meter_fetch_error or ""
        if was_prefetch and not self._meter_prefetch_retried:
            retry_markers = (
                "SAS link not responding",
                "No bytes were received",
                "Only idle 0x00",
                "No SAS 6F response",
            )
            if any(marker in msg for marker in retry_markers):
                self._meter_prefetch_retried = True
                self._meter_fetch_error = None
                self._update_prefetch_status(
                    "COM sync retrying with full SAS timing (like IGT tester)…"
                )
                QTimer.singleShot(1000, lambda: self._begin_meter_fetch(prefetch=True, force=True))
                return
        self._update_prefetch_status()
        if was_prefetch:
            return
        msg = self._meter_fetch_error
        if self._onehand_running is False and "SAS link not responding" in msg:
            ip = self._cabinet_ip_from_scan_root() or "the cabinet"
            msg += (
                f"\n\nOneHand.exe is not running on {ip}. "
                "Start the game client on the EGM, then click Refresh Meters."
            )
        QMessageBox.warning(
            self,
            "Get Meters",
            msg,
        )

    def _on_compare_thread_finished(self) -> None:
        self._compare_thread = None
        self._compare_worker = None

    def _on_compare_clicked(self) -> None:
        paste_text = self._paste.toPlainText()
        parsed = build_verify_6f_rows_from_paste(paste_text)
        has_6f = any((r.sas_value_text or "").strip() for r in parsed)
        self._load_bills_from_paste(paste_text)
        has_bills = bool(self._last_bill_in_rows or self._last_bill_out_rows)
        if not has_6f and not has_bills:
            QMessageBox.information(
                self,
                "SAS accounting verification",
                "No valid SAS 6F or bill-in RX lines found in the pasted text.",
            )
            return
        if not has_6f:
            self._meter_tabs.setCurrentIndex(TAB_BILLS)
            return

        self._last_parsed_rows = parsed
        self._sas_2f_values = parse_sas_2f_paste(paste_text)

        self._currency = _detect_egm_currency(self._scan_root_edit.text() or self._scan_root, self._vm)
        self._update_dollar_toggle_label()

        cache_ok = self._cabinet_cache_valid()
        self._render(parsed_rows=parsed, allow_machine_lookup=cache_ok)
        if not cache_ok:
            self._begin_cabinet_compare(prefetch=False)

    def _begin_cabinet_compare(self, *, prefetch: bool = False) -> None:
        """Load Machine column from cabinet state XML (Scan root UNC). Runs off the UI thread."""
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not self._scan_root:
            if not prefetch:
                QMessageBox.information(
                    self,
                    "SAS accounting verification",
                    "Set Scan root to the cabinet log UNC (e.g. \\\\10.0.0.90\\c$\\Goldclub\\var\\log) "
                    "so Machine values can be loaded from DeviceManagerData.xml.",
                )
            else:
                self._update_prefetch_status()
            return

        if self._cabinet_cache_valid() and not self._compare_running():
            self._update_prefetch_status()
            return

        if self._compare_running():
            return

        self._cabinet_compare_prefetch = prefetch
        if prefetch:
            self._update_prefetch_status()
        else:
            self.ui.compare_btn.setEnabled(False)
            self.ui.compare_btn.setText("Scanning Cabinet...")

        self._stop_compare_thread(wait_ms=0)
        if not self._cabinet_cache_valid():
            self._machine_state_loaded = False
            if self._scan_root != self._loaded_cabinet_scan_root:
                self._machine_state = {}

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
        if not self._worker_signals_enabled():
            return
        # Defer UI work to the next event-loop tick so we do not re-enter the
        # dialog while OneHand / COM prefetch handlers are still running.
        QTimer.singleShot(0, lambda s=state_obj: self._apply_cabinet_state(s))

    def _apply_cabinet_state(self, state_obj: object) -> None:
        if not self._worker_signals_enabled():
            return
        try:
            if isinstance(state_obj, dict):
                # IMPORTANT: keep keys normalized/lowercase for alias lookup
                # (loader returns normalized keys like "coinin"; uppercasing breaks lookups).
                self._machine_state = {str(k).strip(): str(v) for k, v in state_obj.items()}
                self._machine_state_loaded = bool(self._machine_state)
                if self._machine_state_loaded:
                    self._loaded_cabinet_scan_root = self._scan_root
            else:
                self._machine_state = {}
                self._machine_state_loaded = False
            if self._meter_fetch_running():
                # COM capture is still running — defer panel refresh until it finishes.
                self._cabinet_ui_refresh_pending = True
                return
            self._run_cabinet_ui_refresh()
        except Exception:
            traceback.print_exc()
        finally:
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)
            self._cabinet_compare_prefetch = False
            self._update_prefetch_status()

    def _run_cabinet_ui_refresh(self) -> None:
        self._cabinet_ui_refresh_pending = False
        self.setUpdatesEnabled(False)
        try:
            self._currency = _detect_egm_currency(self._scan_root, self._vm)
            self._update_dollar_toggle_label()
            self._render(
                parsed_rows=self._last_parsed_rows,
                allow_machine_lookup=self._machine_state_loaded,
            )
            paste_text = self._paste.toPlainText()
            from network.sas_serial_meters import (
                build_bill_display_rows,
                build_bill_out_display_rows,
                parse_sas_bill_paste,
            )

            bill_display = build_bill_display_rows(
                bill_rows=parse_sas_bill_paste(paste_text),
                paste_text=paste_text,
                machine_state=self._machine_state,
            )
            bill_out_display = build_bill_out_display_rows()
            if bill_display or bill_out_display:
                self._render_bills(
                    bill_display,
                    out_rows=bill_out_display,
                    machine_state=self._machine_state,
                )
        finally:
            self.setUpdatesEnabled(True)
        QTimer.singleShot(0, self._reload_game_theme_catalog)

    def _on_worker_error(self, msg: str) -> None:
        if not self._worker_signals_enabled():
            return
        try:
            try:
                sys.__stdout__.write(f"\n[UI-THREAD] Worker error received: {msg}\n")
                sys.__stdout__.flush()
            except Exception:
                pass
            print(f"[ERROR] {msg}")
            was_prefetch = self._cabinet_compare_prefetch
            if not was_prefetch:
                QMessageBox.information(self, "SAS accounting verification", msg)
        finally:
            self._machine_state = {}
            self._machine_state_loaded = False
            self._loaded_cabinet_scan_root = ""
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=False)
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)
            self._cabinet_compare_prefetch = False
            self._update_prefetch_status()

    def _normalize_int_for_compare(self, s: str) -> str:
        """
        Normalize numeric strings for direct compare:
        - strip leading zeros
        - if a decimal slips through, drop the dot (gm2u should already be integer-cents)
        """
        raw = (s or "").strip()
        if not raw:
            return ""
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
        for tbl in self._verify_tables:
            tbl.setHorizontalHeaderItem(COL_SAS_6F_VALUE, QTableWidgetItem(sas_6f_h))
            tbl.setHorizontalHeaderItem(COL_SAS_2F_VALUE, QTableWidgetItem(sas_2f_h))
            tbl.setHorizontalHeaderItem(COL_MACHINE_VALUE, QTableWidgetItem(mac_h))

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

    def _meter_code_for_row(self, row: int, table: QTableWidget | None = None) -> str:
        tbl = table or self._table
        code_item = tbl.item(row, COL_6F_CODE)
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
        table: QTableWidget | None = None,
    ) -> None:
        tbl = table or self._table
        if missing_display is not None and not (raw or "").strip():
            item = QTableWidgetItem(missing_display)
            item.setData(RAW_VALUE_ROLE, "")
        else:
            item = QTableWidgetItem(self._format_cell_value(meter_code, raw))
            item.setData(RAW_VALUE_ROLE, raw)
        if tooltip:
            item.setToolTip(tooltip)
        tbl.setItem(row, col, item)

    def _refresh_value_columns(self) -> None:
        self._apply_value_headers()
        for tbl in self._verify_tables:
            for row in range(tbl.rowCount()):
                rid = self._meter_code_for_row(row, tbl)
                for col in (COL_SAS_6F_VALUE, COL_SAS_2F_VALUE, COL_MACHINE_VALUE):
                    if col == COL_SAS_2F_VALUE:
                        raw_opt = self._sas_2f_values.get(rid)
                        if raw_opt is None:
                            item = tbl.item(row, col)
                            if item is not None:
                                item.setText("—")
                                item.setData(RAW_VALUE_ROLE, "")
                            continue
                        raw = self._normalize_int_for_compare(raw_opt) or raw_opt
                    else:
                        item = tbl.item(row, col)
                        if item is None:
                            continue
                        raw = str(item.data(RAW_VALUE_ROLE) or item.text() or "")
                    item = tbl.item(row, col)
                    if item is None:
                        continue
                    item.setText(self._format_cell_value(rid, raw))
                    if col == COL_SAS_2F_VALUE:
                        sas_6f_item = tbl.item(row, COL_SAS_6F_VALUE)
                        sas_6f_raw = str(sas_6f_item.data(RAW_VALUE_ROLE) or "") if sas_6f_item else ""
                        if self._normalize_int_for_compare(raw) != self._normalize_int_for_compare(sas_6f_raw):
                            item.setForeground(QColor("#b45309"))
                        else:
                            item.setForeground(QColor())
            fit_verify_table_columns(tbl)
        self._update_game_summary()
        self._update_master_summary()
        self._update_transfer_summary()
        self._update_security_summary()
        if self._last_bill_in_rows or self._last_bill_out_rows:
            self._render_bills(machine_state=self._machine_state or None)
        if self._coin_tables:
            self._render_coins(machine_state=self._machine_state or None)

    @staticmethod
    def _rows_for_meter_codes(parsed_rows: list[Sas6FRow], codes: frozenset[str]) -> list[Sas6FRow]:
        wanted = {c.upper() for c in codes}
        return [r for r in parsed_rows if r.meter_id.upper() in wanted]

    def _sas_value_for_code(self, code: str) -> str:
        rid = (code or "").strip().upper()
        for row in self._last_parsed_rows:
            if row.meter_id.upper() == rid:
                sas_v = row.sas_value_text.strip()
                if sas_v:
                    return self._normalize_int_for_compare(sas_v) or sas_v
        return ""

    def _machine_value_for_code(self, code: str, *, allow_machine_lookup: bool) -> str:
        rid = (code or "").strip().upper()
        if not allow_machine_lookup or not self._machine_state_loaded:
            return ""
        machine_v = ""
        if self._machine_state:
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
        sas_norm = self._sas_value_for_code(rid)
        if not machine_v and sas_norm == "0":
            return "0"
        if machine_v:
            return self._normalize_int_for_compare(machine_v) or machine_v
        return ""

    def _master_summary_raw(self, code: str, *, allow_machine_lookup: bool) -> str:
        machine_v = self._machine_value_for_code(code, allow_machine_lookup=allow_machine_lookup)
        if machine_v:
            return machine_v
        return self._sas_value_for_code(code) or "0"

    def _selected_game_theme_id(self) -> str:
        if self._game_theme_combo is None:
            return ""
        text = (self._game_theme_combo.currentText() or "").strip()
        if not text or text == GAME_THEME_TOTAL:
            return ""
        return text

    def _selected_game_paytable_id(self) -> str:
        if self._game_paytable_combo is None:
            return ""
        text = (self._game_paytable_combo.currentText() or "").strip()
        if not text or text == GAME_THEME_TOTAL:
            return ""
        return text

    def _game_meter_state(self, *, allow_machine_lookup: bool) -> tuple[dict[str, str], bool]:
        """Return meter state for the Game tab and whether a per-game filter is active."""
        from network.accounting_state_loader import aggregate_theme_paytable_meters

        theme_id = self._selected_game_theme_id()
        if theme_id:
            theme_data = self._theme_perf_by_paytable.get(theme_id, {})
            paytable_id = self._selected_game_paytable_id()
            if paytable_id:
                return dict(theme_data.get(paytable_id, {})), True
            return aggregate_theme_paytable_meters(theme_data), True
        if allow_machine_lookup and self._machine_state:
            return dict(self._machine_state), False
        return {}, False

    def _reload_game_theme_catalog(self) -> None:
        from network.accounting_state_loader import (
            load_cabinet_game_catalog,
            load_theme_perf_meters_by_paytable,
        )

        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            self._theme_perf_by_paytable = {}
            self._game_catalog_folders = {}
            self._game_theme_ids = []
            return
        try:
            catalog = load_cabinet_game_catalog(sr)
            self._game_catalog_folders = {theme_id: folder for theme_id, folder in catalog}
            self._theme_perf_by_paytable = load_theme_perf_meters_by_paytable(sr)
        except OSError:
            self._theme_perf_by_paytable = {}
            self._game_catalog_folders = {}
            catalog = []
        all_ids = sorted(
            set(self._game_catalog_folders.keys()) | set(self._theme_perf_by_paytable.keys()),
            key=str.lower,
        )
        self._game_theme_ids = all_ids
        if self._game_theme_combo is None:
            return
        current = self._game_theme_combo.currentText()
        blocked = self._game_theme_combo.blockSignals(True)
        self._game_theme_combo.clear()
        self._game_theme_combo.addItem(GAME_THEME_TOTAL)
        for tid in all_ids:
            self._game_theme_combo.addItem(tid)
        idx = self._game_theme_combo.findText(current)
        self._game_theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._game_theme_combo.blockSignals(blocked)
        self._reload_game_paytable_filter()

    def _reload_game_paytable_filter(self) -> None:
        from network.accounting_state_loader import load_theme_paytable_ids

        if self._game_paytable_combo is None:
            return
        theme_id = self._selected_game_theme_id()
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        current = self._game_paytable_combo.currentText()
        blocked = self._game_paytable_combo.blockSignals(True)
        self._game_paytable_combo.clear()
        self._game_paytable_combo.addItem(GAME_THEME_TOTAL)
        if theme_id and sr:
            self._game_paytable_combo.setEnabled(True)
            for paytable_id in load_theme_paytable_ids(
                sr,
                theme_id,
                perf_by_paytable=self._theme_perf_by_paytable,
                catalog_folders=self._game_catalog_folders,
            ):
                self._game_paytable_combo.addItem(paytable_id)
        else:
            self._game_paytable_combo.setEnabled(False)
        idx = self._game_paytable_combo.findText(current)
        self._game_paytable_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._game_paytable_combo.blockSignals(blocked)

    def _on_game_theme_filter_changed(self, _index: int = 0) -> None:
        self._reload_game_paytable_filter()
        self._update_game_summary()

    def _on_game_paytable_filter_changed(self, _index: int = 0) -> None:
        self._update_game_summary()

    def _game_count_raw(
        self,
        state_keys: tuple[str, ...],
        sas_code: str,
        *,
        state: dict[str, str],
        theme_filtered: bool,
    ) -> str:
        if state:
            v = lookup_normalized_machine_value(state, *state_keys)
            if v:
                return v
        if theme_filtered:
            return "0"
        if sas_code:
            return self._sas_value_for_code(sas_code) or "0"
        return "0"

    def _game_bonus_win_raw(self, *, state: dict[str, str], theme_filtered: bool) -> str:
        if not state:
            return "0"
        total = 0
        found = False
        for key in GAME_BONUS_WIN_STATE_KEYS:
            v = lookup_normalized_machine_value(state, key)
            if v and re.fullmatch(r"\d+", v.replace(",", "")):
                total += int(v.replace(",", ""))
                found = True
        if found:
            return str(total)
        return "0" if theme_filtered else "0"

    def _game_amount_raw(
        self,
        state_keys: tuple[str, ...],
        sas_code: str,
        *,
        state: dict[str, str],
        theme_filtered: bool,
        allow_machine_lookup: bool,
    ) -> str:
        if state and state_keys:
            v = lookup_normalized_machine_value(state, *state_keys)
            if v:
                return self._normalize_int_for_compare(v) or v
        if theme_filtered:
            if sas_code:
                v = lookup_normalized_machine_value(
                    state, *game_amount_state_keys_for_sas_code(sas_code)
                )
                if v:
                    return self._normalize_int_for_compare(v) or v
            return "0"
        if sas_code:
            return self._master_summary_raw(sas_code, allow_machine_lookup=allow_machine_lookup)
        return "0"

    def _reset_game_summary(self) -> None:
        for lbl in self._game_perf_labels.values():
            lbl.setText("—")
        for lbl in self._game_residual_labels.values():
            lbl.setText("—")
        if self._game_yield_chart is not None:
            self._game_yield_chart.set_values(None, None)

    def _update_game_summary(self, *, allow_machine_lookup: bool | None = None) -> None:
        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        state, theme_filtered = self._game_meter_state(allow_machine_lookup=allow_machine_lookup)
        sym = self._currency.symbol or "$"
        played_raw = self._game_count_raw(
            GAME_PLAYED_STATE_KEYS,
            "0005",
            state=state,
            theme_filtered=theme_filtered,
        )
        won_raw = self._game_count_raw(
            GAME_WON_STATE_KEYS,
            "0006",
            state=state,
            theme_filtered=theme_filtered,
        )
        lost_raw = self._game_count_raw(
            GAME_LOST_STATE_KEYS,
            "0007",
            state=state,
            theme_filtered=theme_filtered,
        )
        bet_raw = self._game_amount_raw(
            ("coinin", "gamecoinin"),
            GAME_BET_CODE,
            state=state,
            theme_filtered=theme_filtered,
            allow_machine_lookup=allow_machine_lookup,
        )
        win_raw = self._game_amount_raw(
            ("coinout", "totalcoinout"),
            GAME_WIN_CODE,
            state=state,
            theme_filtered=theme_filtered,
            allow_machine_lookup=allow_machine_lookup,
        )
        game_win_raw = self._game_amount_raw(
            ("basegamecoinout", "bggamecoinout"),
            GAME_GAME_WIN_CODE,
            state=state,
            theme_filtered=theme_filtered,
            allow_machine_lookup=allow_machine_lookup,
        )
        bonus_raw = self._game_bonus_win_raw(state=state, theme_filtered=theme_filtered)
        sas_bonus_raw = self._game_amount_raw(
            GAME_SAS_BONUS_STATE_KEYS,
            "",
            state=state,
            theme_filtered=theme_filtered,
            allow_machine_lookup=allow_machine_lookup,
        )
        prog_raw = self._game_amount_raw(
            GAME_PROG_WIN_STATE_KEYS,
            GAME_PROG_WIN_CODE,
            state=state,
            theme_filtered=theme_filtered,
            allow_machine_lookup=allow_machine_lookup,
        )
        totals = compute_game_summary(
            played_raw=played_raw,
            won_raw=won_raw,
            lost_raw=lost_raw,
            bet_raw=bet_raw,
            win_raw=win_raw,
        )
        count_map = {
            "played": format_transfer_count_display(played_raw),
            "won": format_transfer_count_display(won_raw),
            "lost": str(totals["lost"]),
        }
        amount_map = {
            "bet": format_master_amount_display(bet_raw, symbol=sym),
            "win": format_master_amount_display(win_raw, symbol=sym),
            "game_win": format_master_amount_display(game_win_raw, symbol=sym),
            "bonus_win": format_master_amount_display(bonus_raw, symbol=sym),
            "sas_bonus": format_master_amount_display(sas_bonus_raw, symbol=sym),
            "prog_win": format_master_amount_display(prog_raw, symbol=sym),
            "bet_minus_win": format_signed_dollar_amount(float(totals["bet_minus_win"]), symbol=sym),
            "yield": format_game_pct_display(
                float(totals["yield_pct"]) if totals["yield_pct"] is not None else None
            ),
            "hold": format_game_pct_display(
                float(totals["hold_pct"]) if totals["hold_pct"] is not None else None
            ),
        }
        for key, lbl in self._game_perf_labels.items():
            if key in count_map:
                lbl.setText(count_map[key])
            elif key in amount_map:
                lbl.setText(amount_map[key])
        for key, lbl in self._game_residual_labels.items():
            state_keys = self._game_residual_state_keys.get(key, ())
            raw = "0"
            if theme_filtered:
                raw = "0"
            elif allow_machine_lookup and self._machine_state:
                raw = lookup_normalized_machine_value(self._machine_state, *state_keys) or "0"
            if key in ("coin_in", "coin_out"):
                lbl.setText(format_master_amount_display(raw, symbol=sym))
            else:
                lbl.setText(format_transfer_count_display(raw))
        if self._game_yield_chart is not None:
            yield_pct = totals.get("yield_pct")
            hold_pct = totals.get("hold_pct")
            if isinstance(yield_pct, (int, float)):
                self._game_yield_chart.set_values(float(yield_pct), float(hold_pct) if hold_pct is not None else None)
            else:
                self._game_yield_chart.set_values(None, None)

    def _reset_master_summary(self) -> None:
        for lbl in self._master_value_labels.values():
            lbl.setText("—")
        if self._master_credit_in_total is not None:
            self._master_credit_in_total.setText("—")
        if self._master_credit_out_total is not None:
            self._master_credit_out_total.setText("—")
        self._master_total_credit.setText("—")
        self._master_inout_pct.setText("—")

    def _update_master_summary(self, *, allow_machine_lookup: bool | None = None) -> None:
        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        sym = self._currency.symbol or "$"
        values: dict[str, str] = {}
        for code in MASTER_TRACKED_CODES:
            values[code] = self._master_summary_raw(code, allow_machine_lookup=allow_machine_lookup)
        for key, lbl in self._master_value_labels.items():
            code = self._master_value_label_codes.get(key, "")
            raw = values.get(code, "0")
            lbl.setText(format_master_amount_display(raw, symbol=sym))
        totals = compute_master_summary(values)
        if self._master_credit_in_total is not None:
            self._master_credit_in_total.setText(f"{sym}{totals['credit_in']:.2f}")
        if self._master_credit_out_total is not None:
            self._master_credit_out_total.setText(f"{sym}{totals['credit_out']:.2f}")
        self._master_total_credit.setText(f"{sym}{totals['total_credit']:.2f}")
        pct = totals["inout_pct"]
        self._master_inout_pct.setText(f"{pct:.2f}%" if pct is not None else "—")

    def _transfer_amount_raw(self, code: str, *, allow_machine_lookup: bool) -> str:
        if code:
            return self._master_summary_raw(code, allow_machine_lookup=allow_machine_lookup)
        if allow_machine_lookup and self._machine_state:
            v = lookup_normalized_machine_value(
                self._machine_state,
                *self._transfer_promo_out_keys,
            )
            if v:
                return self._normalize_int_for_compare(v) or v
        return "0"

    def _transfer_count_raw(self, count_id: str, *, allow_machine_lookup: bool) -> str:
        if not allow_machine_lookup or not self._machine_state:
            return "0"
        if count_id.startswith("bucket:"):
            code = count_id.split(":", 1)[1]
            keys = TRANSFER_BUCKET_COUNT_KEYS.get(code, ())
            return lookup_normalized_machine_value(self._machine_state, *keys) or "0"
        if count_id.startswith("promo_out:"):
            return "0"
        keys = TRANSFER_COUNT_STATE_KEYS.get(count_id, ())
        return lookup_normalized_machine_value(self._machine_state, *keys) or "0"

    def _reset_transfer_summary(self) -> None:
        for lbl in self._transfer_amount_labels.values():
            lbl.setText("—")
        for lbl in self._transfer_count_labels.values():
            lbl.setText("—")

    def _update_transfer_summary(self, *, allow_machine_lookup: bool | None = None) -> None:
        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        sym = self._currency.symbol or "$"
        for key, lbl in self._transfer_amount_labels.items():
            code = self._transfer_amount_codes.get(key, "")
            raw = self._transfer_amount_raw(code, allow_machine_lookup=allow_machine_lookup)
            lbl.setText(format_master_amount_display(raw, symbol=sym))
        for key, lbl in self._transfer_count_labels.items():
            count_id = self._transfer_count_key_ids.get(key, "")
            raw = self._transfer_count_raw(count_id, allow_machine_lookup=allow_machine_lookup)
            lbl.setText(format_transfer_count_display(raw))

    def _security_count_raw(self, state_keys: tuple[str, ...], *, allow_machine_lookup: bool) -> str:
        if not allow_machine_lookup or not self._machine_state:
            return "0"
        return lookup_normalized_machine_value(self._machine_state, *state_keys) or "0"

    def _reset_security_summary(self) -> None:
        for lbl in self._security_value_labels.values():
            lbl.setText("—")

    def _update_security_summary(self, *, allow_machine_lookup: bool | None = None) -> None:
        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        for key, lbl in self._security_value_labels.items():
            state_keys = self._security_state_keys.get(key, ())
            raw = self._security_count_raw(state_keys, allow_machine_lookup=allow_machine_lookup)
            lbl.setText(format_transfer_count_display(raw))

    def _render_table(
        self,
        table: QTableWidget,
        parsed_rows: list[Sas6FRow],
        *,
        allow_machine_lookup: bool,
    ) -> None:
        table.setRowCount(len(parsed_rows))
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
            machine_missing = True
            if allow_machine_lookup and self._machine_state_loaded:
                machine_v = self._machine_value_for_code(rid, allow_machine_lookup=True)
                machine_missing = not machine_v
            sas_norm = self._normalize_int_for_compare(sas_v) if has_sas else ""
            mac_norm = self._normalize_int_for_compare(machine_v) if machine_v else ""
            if rid == "000B" and has_sas and machine_v:
                from network.meter_comparator import align_bills_in_sas_credits

                aligned = align_bills_in_sas_credits(sas_norm, mac_norm)
                if aligned != sas_norm:
                    sas_v = aligned
                    sas_norm = self._normalize_int_for_compare(aligned)
            if (
                machine_missing
                and self._machine_state_loaded
                and has_sas
                and sas_norm == "0"
            ):
                machine_v = "0"
                mac_norm = "0"
                machine_missing = False
            if not has_sas or not self._machine_state_loaded:
                match = False
                status = "PENDING"
            elif machine_missing:
                match = False
                status = "PENDING"
            elif rid == "000B" and machine_v:
                from network.meter_comparator import bills_in_meters_match

                match = bills_in_meters_match(sas_norm, mac_norm)
                status = "MATCH" if match else "MISMATCH"
            else:
                try:
                    match = int(mac_norm) == int(sas_norm)
                except ValueError:
                    match = mac_norm == sas_norm
                status = "MATCH" if match else "MISMATCH"
            code_item = QTableWidgetItem(code_6f)
            code_item.setToolTip("6F paste table id (from RX<= 6F meter-code bytes, little-endian).")
            table.setItem(row, COL_6F_CODE, code_item)
            wire_item = QTableWidgetItem(wire_id)
            wire_item.setToolTip("On-wire SAS meter id (byte-swapped from 6F Code).")
            table.setItem(row, COL_WIRE_ID, wire_item)
            poll_item = QTableWidgetItem(igt_poll)
            poll_item.setToolTip("Hex index to enter in the IGT tester $2F “Send selected meters” dialog.")
            table.setItem(row, COL_IGT_POLL, poll_item)
            igt_item = QTableWidgetItem(igt_meter)
            igt_item.setToolTip("Label the IGT tester shows (e.g. First Meter = 00000200).")
            table.setItem(row, COL_IGT_METER, igt_item)
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
            table.setItem(row, COL_METER_NAME, name_item)
            self._set_value_item(
                row,
                COL_SAS_6F_VALUE,
                meter_code=rid,
                raw=sas_norm or sas_v,
                missing_display="—" if not has_sas else None,
                tooltip="From RX<= 6F bulk poll in paste.",
                table=table,
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
                    table=table,
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
                    table=table,
                )
                if sas_2f_norm != sas_norm:
                    item = table.item(row, COL_SAS_2F_VALUE)
                    if item is not None:
                        item.setForeground(QColor("#b45309"))
            mac_display_raw = mac_norm if machine_v else ""
            self._set_value_item(
                row,
                COL_MACHINE_VALUE,
                meter_code=rid,
                raw=mac_display_raw,
                missing_display="—" if not machine_v else None,
                tooltip="From cabinet gm2u / accounting XML.",
                table=table,
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
            table.setItem(row, COL_STATUS, st)
        fit_verify_table_columns(table)

    def _render(self, *, parsed_rows: list[Sas6FRow], allow_machine_lookup: bool = True) -> None:
        self.setUpdatesEnabled(False)
        try:
            self._refresh_2f_from_paste()
            self._apply_value_headers()
            self._maybe_show_2f_paste_hint()
            try:
                if self._machine_state:
                    p = self.parent()
                    if p is not None and hasattr(p, "statusBar"):
                        sb = p.statusBar()
                        if sb is not None:
                            sb.showMessage("Path Loaded", 4000)
            except Exception:
                pass
            self._render_table(
                self._table,
                parsed_rows,
                allow_machine_lookup=allow_machine_lookup,
            )
            self._render_coins(
                machine_state=self._machine_state or None,
                allow_machine_lookup=allow_machine_lookup,
            )
            self._update_game_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_master_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_transfer_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_security_summary(allow_machine_lookup=allow_machine_lookup)
        finally:
            self.setUpdatesEnabled(True)

    def _format_bill_dollars(self, amount_cents: int) -> str:
        return format_bill_amount_display(amount_cents)

    def _bill_total_foreground(self) -> QColor:
        """TOTAL row labels: black in light mode, palette text in dark mode."""
        pal = self.palette()
        if surface_is_light(pal):
            return QColor("#000000")
        return pal.color(QPalette.ColorRole.Text)

    def _render_bills(
        self,
        rows: list | tuple | None = None,
        *,
        out_rows: list | tuple | None = None,
        machine_state: dict[str, str] | None = None,
    ) -> None:
        from network.accounting_state_loader import cabinet_bill_reject_count
        from network.sas_serial_meters import build_bill_out_display_rows

        if rows is not None:
            self._last_bill_in_rows = list(rows)
        if out_rows is not None:
            self._last_bill_out_rows = list(out_rows)
        state = machine_state if machine_state is not None else self._machine_state
        self._bill_reject_label.setText(
            format_bill_reject_count_label(cabinet_bill_reject_count(state or {}))
        )
        self._render_bill_table(
            self._bills_table,
            self._last_bill_in_rows,
            direction="in",
        )
        out = self._last_bill_out_rows or build_bill_out_display_rows()
        self._render_bill_table(
            self._bills_out_table,
            out,
            direction="out",
        )

    def _coin_sas_values(self, *, allow_machine_lookup: bool) -> dict[str, str]:
        from network.sas_serial_meters import COIN_PANEL_SPECS

        out: dict[str, str] = {}
        for _panel, (code, *_rest) in COIN_PANEL_SPECS.items():
            if code:
                out[code] = self._master_summary_raw(code, allow_machine_lookup=allow_machine_lookup)
        return out

    def _render_coins(
        self,
        *,
        machine_state: dict[str, str] | None = None,
        allow_machine_lookup: bool | None = None,
    ) -> None:
        from network.sas_serial_meters import build_all_coin_panel_rows

        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        state = machine_state if machine_state is not None else self._machine_state
        sas_values = self._coin_sas_values(allow_machine_lookup=allow_machine_lookup) if allow_machine_lookup else {}
        panels = build_all_coin_panel_rows(machine_state=state or {}, sas_values=sas_values)
        for panel_id, table in self._coin_tables.items():
            body_rows, aggregate_totals = panels.get(panel_id, ((), None))
            self._render_coin_table(table, body_rows, aggregate_totals=aggregate_totals)

    def _render_coin_table(
        self,
        table: QTableWidget,
        coin_rows: tuple | list,
        *,
        aggregate_totals: dict[str, int] | None = None,
    ) -> None:
        from network.sas_serial_meters import SasCoinDenomRow

        table.setRowCount(0)
        center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

        total_amount = 0
        total_count = 0
        for row in coin_rows:
            if isinstance(row, SasCoinDenomRow):
                label = row.label
                amount_cents = row.amount_cents
                count = row.count
            else:
                label = str(getattr(row, "label", ""))
                amount_cents = int(getattr(row, "amount_cents", 0))
                count = int(getattr(row, "count", 0))
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, _COINS_COL_COIN, self._bill_table_item(label, align=center))
            table.setItem(
                r,
                _COINS_COL_AMOUNT,
                self._bill_table_item(format_bill_amount_display(amount_cents), align=center),
            )
            table.setItem(
                r,
                _COINS_COL_COUNT_IDX,
                self._bill_table_item(str(count), align=center),
            )
            total_amount += amount_cents
            total_count += count

        if aggregate_totals is not None:
            total_amount = int(aggregate_totals.get("amount_cents", 0))
            total_count = int(aggregate_totals.get("count", 0))

        r = table.rowCount()
        table.insertRow(r)
        total_fg = self._bill_total_foreground()
        table.setItem(
            r,
            _COINS_COL_COIN,
            self._bill_table_item("TOTAL", align=center, bold=True, foreground=total_fg),
        )
        table.setItem(
            r,
            _COINS_COL_AMOUNT,
            self._bill_table_item(
                format_bill_amount_display(total_amount),
                align=center,
                bold=True,
                foreground=total_fg,
            ),
        )
        table.setItem(
            r,
            _COINS_COL_COUNT_IDX,
            self._bill_table_item(str(total_count), align=center, bold=True, foreground=total_fg),
        )
        self._fit_bill_table_height(table)

    def _render_bill_table(
        self,
        table: QTableWidget,
        bill_rows: list,
        *,
        direction: str = "in",
    ) -> None:
        from network.sas_serial_meters import SasBillDenomRow

        table.setRowCount(0)
        if not bill_rows:
            return

        body_rows, aggregate_totals = prepare_bill_table_body_rows(
            bill_rows,
            direction=direction,
        )
        center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

        total_amount = 0
        total_count = 0
        for row in body_rows:
            if isinstance(row, SasBillDenomRow):
                label = row.label
                amount_cents = row.amount_cents
                count = row.count
            else:
                label = str(getattr(row, "label", ""))
                amount_cents = int(getattr(row, "amount_cents", 0))
                count = int(getattr(row, "count", 0))
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(
                r,
                _BILLS_COL_BILL,
                self._bill_table_item(label, align=center),
            )
            table.setItem(
                r,
                _BILLS_COL_AMOUNT,
                self._bill_table_item(format_bill_amount_display(amount_cents), align=center),
            )
            table.setItem(
                r,
                _BILLS_COL_COUNT_IDX,
                self._bill_table_item(str(count), align=center),
            )
            total_amount += amount_cents
            total_count += count

        if aggregate_totals is not None:
            total_amount = int(aggregate_totals.get("amount_cents", 0))
            total_count = int(aggregate_totals.get("count", 0))

        r = table.rowCount()
        table.insertRow(r)
        total_fg = self._bill_total_foreground()
        table.setItem(
            r,
            _BILLS_COL_BILL,
            self._bill_table_item("TOTAL", align=center, bold=True, foreground=total_fg),
        )
        table.setItem(
            r,
            _BILLS_COL_AMOUNT,
            self._bill_table_item(
                format_bill_amount_display(total_amount),
                align=center,
                bold=True,
                foreground=total_fg,
            ),
        )
        table.setItem(
            r,
            _BILLS_COL_COUNT_IDX,
            self._bill_table_item(str(total_count), align=center, bold=True, foreground=total_fg),
        )
        self._fit_bill_table_height(table)

    @staticmethod
    def _fit_bill_table_height(table: QTableWidget) -> None:
        """Shrink table to row content (compact EGM-style, no empty scroll area)."""
        header_h = table.horizontalHeader().height()
        row_h = table.verticalHeader().defaultSectionSize()
        frame = table.frameWidth() * 2
        table.setFixedHeight(header_h + row_h * max(table.rowCount(), 1) + frame)

    def _load_bills_from_paste(self, paste_text: str) -> None:
        from network.sas_serial_meters import (
            build_bill_display_rows,
            build_bill_out_display_rows,
            parse_sas_bill_paste,
        )

        rows = build_bill_display_rows(
            bill_rows=parse_sas_bill_paste(paste_text),
            paste_text=paste_text,
            machine_state=self._machine_state or None,
        )
        out_rows = build_bill_out_display_rows()
        if rows or out_rows:
            self._render_bills(rows, out_rows=out_rows)

    def _clear_all(self) -> None:
        """Reset the dialog: empty the paste box (upper) and the results table (bottom)."""
        self._paste.clear()
        for tbl in self._verify_tables:
            tbl.clearContents()
            tbl.setRowCount(0)
        self._reset_game_summary()
        self._reset_master_summary()
        self._reset_transfer_summary()
        self._reset_security_summary()
        self._theme_perf_by_paytable = {}
        self._game_catalog_folders = {}
        self._game_theme_ids = []
        if self._game_theme_combo is not None:
            blocked = self._game_theme_combo.blockSignals(True)
            self._game_theme_combo.clear()
            self._game_theme_combo.addItem(GAME_THEME_TOTAL)
            self._game_theme_combo.setCurrentIndex(0)
            self._game_theme_combo.blockSignals(blocked)
        if self._game_paytable_combo is not None:
            blocked = self._game_paytable_combo.blockSignals(True)
            self._game_paytable_combo.clear()
            self._game_paytable_combo.addItem(GAME_THEME_TOTAL)
            self._game_paytable_combo.setEnabled(False)
            self._game_paytable_combo.setCurrentIndex(0)
            self._game_paytable_combo.blockSignals(blocked)
        self._bills_table.clearContents()
        self._bills_table.setRowCount(0)
        self._bills_out_table.clearContents()
        self._bills_out_table.setRowCount(0)
        self._bill_reject_label.setText(format_bill_reject_count_label(None))
        for table in self._coin_tables.values():
            table.clearContents()
            table.setRowCount(0)
        self._render_coins(machine_state=None, allow_machine_lookup=False)
        self._sas_2f_values = {}
        self._update_2f_column_visibility()
        self._last_parsed_rows = []
        self._last_bill_in_rows = []
        self._last_bill_out_rows = []
        self._cached_meter_result = None
        self._meter_fetch_error = None
        self._meter_fetch_user_clicked_apply = False
        self._last_displayed_paste_fingerprint = ""
        self._loaded_cabinet_scan_root = ""
        self._machine_state = {}
        self._machine_state_loaded = False
        self._update_prefetch_status()
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

    def _visible_table_columns(self, table: QTableWidget) -> list[int]:
        return [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]

    def _flat_table_cell_text(self, text: str) -> str:
        return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

    def _generic_table_cell_text(
        self,
        table: QTableWidget,
        row: int,
        col: int,
        *,
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> str:
        item = table.item(row, col)
        text = item.text() if item else ""
        if text_resolver is not None:
            text = text_resolver(row, col, text)
        text = self._flat_table_cell_text(text)
        if col in excel_text_cols and text:
            return f'="{text}"'
        return text

    def _generic_table_row_tsv(
        self,
        table: QTableWidget,
        row: int,
        *,
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> str:
        cols = self._visible_table_columns(table)
        return "\t".join(
            self._generic_table_cell_text(
                table,
                row,
                c,
                excel_text_cols=excel_text_cols,
                text_resolver=text_resolver,
            )
            for c in cols
        )

    def _copy_table_all_tsv(
        self,
        table: QTableWidget,
        headers: list[str],
        *,
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> None:
        if table.rowCount() == 0:
            return
        header = "\t".join(headers)
        lines = [
            header,
            *(
                self._generic_table_row_tsv(
                    table,
                    r,
                    excel_text_cols=excel_text_cols,
                    text_resolver=text_resolver,
                )
                for r in range(table.rowCount())
            ),
        ]
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_table_row_tsv(
        self,
        table: QTableWidget,
        headers: list[str],
        row: int | None = None,
        *,
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> None:
        if row is None or row < 0:
            rows = sorted({idx.row() for idx in table.selectedIndexes()})
            if not rows:
                return
            row = rows[0]
        if row < 0 or row >= table.rowCount():
            return
        header = "\t".join(headers)
        QApplication.clipboard().setText(
            f"{header}\n{self._generic_table_row_tsv(table, row, excel_text_cols=excel_text_cols, text_resolver=text_resolver)}"
        )

    def _copy_table_column_tsv(
        self,
        table: QTableWidget,
        headers: list[str],
        col: int,
        *,
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> None:
        if col < 0 or col >= table.columnCount() or table.isColumnHidden(col):
            return
        visible_cols = self._visible_table_columns(table)
        try:
            header = headers[visible_cols.index(col)]
        except ValueError:
            return
        lines = [
            header,
            *(
                self._generic_table_cell_text(
                    table,
                    r,
                    col,
                    excel_text_cols=excel_text_cols,
                    text_resolver=text_resolver,
                )
                for r in range(table.rowCount())
            ),
        ]
        QApplication.clipboard().setText("\n".join(lines))

    def _accounting_copy_headers(self) -> list[str]:
        return self._table_headers_for_copy()

    def _accounting_cell_resolver(self, row: int, col: int, text: str) -> str:
        if col == COL_METER_NAME and not text:
            text = self._name_for_code(self._meter_code_for_row(row))
        return text

    def _verify_cell_resolver(self, table: QTableWidget) -> Callable[[int, int, str], str]:
        def resolver(row: int, col: int, text: str) -> str:
            if col == COL_METER_NAME and not text:
                text = self._name_for_code(self._meter_code_for_row(row, table))
            return text

        return resolver

    def _bills_copy_headers(self) -> list[str]:
        return list(_BILLS_TABLE_HEADERS)

    def _show_table_copy_menu(
        self,
        table: QTableWidget,
        pos: QPoint,
        *,
        all_label: str,
        headers_fn: Callable[[], list[str]],
        excel_text_cols: frozenset[int] = frozenset(),
        text_resolver: Callable[[int, int, str], str] | None = None,
    ) -> None:
        if table.rowCount() == 0:
            return
        idx = table.indexAt(pos)
        row = idx.row() if idx.isValid() else -1
        col = idx.column() if idx.isValid() else -1
        has_row = row >= 0 or bool(table.selectedIndexes())
        visible_cols = self._visible_table_columns(table)
        headers = headers_fn()

        menu = QMenu(self)
        act_all = menu.addAction(all_label)
        act_all.triggered.connect(
            lambda _checked=False: self._copy_table_all_tsv(
                table,
                headers,
                excel_text_cols=excel_text_cols,
                text_resolver=text_resolver,
            )
        )
        act_row = menu.addAction("Copy selected row")
        act_row.setEnabled(has_row)
        if row >= 0:
            act_row.triggered.connect(
                lambda _checked=False, r=row: self._copy_table_row_tsv(
                    table,
                    headers,
                    r,
                    excel_text_cols=excel_text_cols,
                    text_resolver=text_resolver,
                )
            )
        else:
            act_row.triggered.connect(
                lambda _checked=False: self._copy_table_row_tsv(
                    table,
                    headers,
                    None,
                    excel_text_cols=excel_text_cols,
                    text_resolver=text_resolver,
                )
            )
        col_label = ""
        if col >= 0 and not table.isColumnHidden(col):
            try:
                col_label = headers[visible_cols.index(col)]
            except ValueError:
                header_item = table.horizontalHeaderItem(col)
                col_label = header_item.text() if header_item else f"Column {col + 1}"
        act_col = menu.addAction(
            f"Copy column: {col_label}" if col_label else "Copy column"
        )
        act_col.setEnabled(bool(col_label))
        if col_label:
            act_col.triggered.connect(
                lambda _checked=False, column=col: self._copy_table_column_tsv(
                    table,
                    headers,
                    column,
                    excel_text_cols=excel_text_cols,
                    text_resolver=text_resolver,
                )
            )
        menu.exec(table.viewport().mapToGlobal(pos))

    def _copy_bill_table_row_tsv(self, table: QTableWidget) -> None:
        headers = self._bills_copy_headers()
        self._copy_table_row_tsv(table, headers, None)

    def _copy_focused_table_row_tsv(self) -> None:
        focus = QApplication.focusWidget()
        if focus is self._bills_out_table or (
            focus is not None and self._bills_out_table.isAncestorOf(focus)
        ):
            self._copy_bill_table_row_tsv(self._bills_out_table)
            return
        if focus is self._bills_table or (
            focus is not None and self._bills_table.isAncestorOf(focus)
        ):
            self._copy_bill_table_row_tsv(self._bills_table)
            return
        for tbl in self._verify_tables:
            if focus is tbl or (focus is not None and tbl.isAncestorOf(focus)):
                self._copy_table_row_tsv(
                    tbl,
                    self._accounting_copy_headers(),
                    None,
                    excel_text_cols=_EXCEL_TEXT_COLS,
                    text_resolver=self._verify_cell_resolver(tbl),
                )
                return

    def _on_verify_table_context_menu(self, table: QTableWidget, pos: QPoint) -> None:
        self._show_table_copy_menu(
            table,
            pos,
            all_label="Copy all meters",
            headers_fn=self._accounting_copy_headers,
            excel_text_cols=_EXCEL_TEXT_COLS,
            text_resolver=self._verify_cell_resolver(table),
        )

    def _on_table_context_menu(self, pos: QPoint) -> None:
        self._on_verify_table_context_menu(self._table, pos)

    def _on_bills_in_table_context_menu(self, pos: QPoint) -> None:
        self._show_table_copy_menu(
            self._bills_table,
            pos,
            all_label="Copy all bills",
            headers_fn=self._bills_copy_headers,
        )

    def _on_bills_out_table_context_menu(self, pos: QPoint) -> None:
        self._show_table_copy_menu(
            self._bills_out_table,
            pos,
            all_label="Copy all bills",
            headers_fn=self._bills_copy_headers,
        )

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
            lines.append(
                self._generic_table_row_tsv(
                    self._table,
                    i,
                    excel_text_cols=_EXCEL_TEXT_COLS,
                    text_resolver=self._accounting_cell_resolver,
                )
            )
        QApplication.clipboard().setText("\n".join(lines))

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_meter_panel_styles()
            if self._last_bill_in_rows or self._last_bill_out_rows:
                self._render_bills(machine_state=self._machine_state or None)

    def mismatch_detected(self) -> bool:
        for tbl in self._verify_tables:
            for i in range(tbl.rowCount()):
                it = tbl.item(i, COL_STATUS)
                if it and it.text().strip().upper() == "MISMATCH":
                    return True
        return False

