"""Paste-based SAS 6F verification against machine accounting state."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial

from gui.sas_money_format import (
    CREDITS_PER_DOLLAR,
    EgmCurrency,
    SAS_VERIFY_MONETARY_CODES,
    credits_to_dollar_amount as _credits_to_dollar_amount,
    currency_from_id as _currency_from_id,
    format_dollar_amount as _format_dollar_amount,
    format_meter_value_display as _format_meter_value_display,
    is_monetary_sas_code as _is_monetary_sas_code,
)
from datetime import datetime, timezone
import os
import time
from types import SimpleNamespace
import sys
import threading
import traceback

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QFileSystemWatcher,
    QObject,
    QRect,
    QRunnable,
    QSettings,
    QThread,
    QThreadPool,
    Signal,
    Qt,
    QPoint,
    QSize,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
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
from gui.meter_timing_trace import trace_event
from gui.palette_adapt import surface_is_light
from gui.win_global_hotkey import (
    KILL_HOTKEY_ID,
    KILL_HOTKEY_LABEL,
    KILL_HOTKEY_MODS,
    KILL_HOTKEY_VK,
    MOVE_HOTKEY_ID,
    MOVE_HOTKEY_LABEL,
    MOVE_HOTKEY_MODS,
    MOVE_HOTKEY_VK,
    TAB_HOTKEY_ID,
    TAB_HOTKEY_LABEL,
    TAB_HOTKEY_MODS,
    TAB_HOTKEY_VK,
    GlobalHotkeyManager,
)
from gui.win_title_bar import set_window_always_on_top

# --- EGM currency / dollar display (100 credits = $1 on USD cabinets) ---
RAW_VALUE_ROLE = int(Qt.ItemDataRole.UserRole)

# IMPORTANT: do not import cabinet/network loaders at module-import time.
# Worker threads import them inside `CompareWorker.run()` to avoid pulling in any UI-linked globals.


# SAS codes whose Machine Value is COMPUTED in code from more than one cabinet meter
# (i.e. not a single "true" raw meter). Maps code -> human-readable formula for tooltips.
# Keep in sync with get_gm2u_value_for_sas_code() special cases in gui/view_model.py.
DERIVED_SAS_CODES: dict[str, str] = {
    "0004": (
        "0003 HandPaidCancelled + 0016 TicketOut + 0018 CashlessOut "
        "(roulette: handpayCashableOutAmt + voucher out + WAT out)"
    ),
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
SAS_VERIFY_HELP_HTML = """
<h2>SAS Verify Meters</h2>

<h3>What this tool is for</h3>
<p>
Compare <b>live SAS</b> meters with the cabinet <b>Machine</b> snapshot — same screens
as the EGM (Master, Bills, Transfer, …). Use after credits, handpay, tickets/AFT,
RAM clear, or a new build.
</p>

<h3>First run</h3>
<ol>
<li><b>Scan root</b> — remote example
    <code>\\\\10.0.0.90\\c$\\Goldclub\\var</code>; on the EGM use
    <code>C:\\Goldclub\\var</code>.</li>
<li><b>Machine column</b> — reads <i>DeviceManagerData.xml</i> from that tree
    (slot: <code>…\\GCMessenger\\{gm2au,SASControler1}</code>; roulette:
    <code>…\\ruleta\\var</code>). Lab IPs are <b>registered automatically</b>;
    if the share fails, run:<br>
    <code>cmdkey /add:&lt;cabinet-ip&gt; /user:GOLD-CLUB\\test /pass:test</code></li>
<li><b>SAS columns</b> — need the host cable / MUX on this PC.
    <b>Close the IGT SAS tester</b> first (one app per COM).
    If COM is busy, the tool waits and recaptures when free — or, with a cabinet UNC,
    offers <b>Machine + SASControler</b> from the share.</li>
<li>Game client must be running on the EGM (OneHand / Ruleta). If it was down, meters
    re-fetch when it comes up. On the EGM itself, live SAS/MUX is not used — local
    gm2au vs SASControler folders are compared instead.</li>
</ol>
<p>While the share is down, SAS can still fill from COM; Machine <b>reloads automatically</b>
when the share returns. Click <b>Refresh Meters</b> anytime for a full refresh.</p>

<h3>RAM Clear</h3>
<p>
<strong>No EGM reboot required.</strong> <b>Tools → RAM Clear…</b> wipes official state
and stamps soft meters (<b>0x7A</b>), then restarts services on the live cabinet —
you do not need to power-cycle or reboot. Target follows the Scan root (or this PC
when it is the EGM). Meters clear immediately while it runs.
</p>

<h3>Everyday controls</h3>
<ul>
<li><b>Auto fetch</b> — when on, refreshes when the cabinet writes new state.
    <b>MATCH</b> rows get a short <b>soft pulse</b>; status may show <b>SYNCING</b>
    while SAS and Machine catch up. Fast play (product debug <b>option Q</b>, ~3
    <b>games/s</b>) shows a thin <b>Burst</b> strip; normal pulse returns when play slows.</li>
<li><b>Show $</b> — money instead of credits (saved across launches).</li>
<li><b>Always on top</b> — on by default (<i>View</i> or right-click the title bar).</li>
<li><b>Move to Monitor 2</b> — put meters on the second screen while you play
    (<i>View</i> menu; remembered).</li>
<li><b>Columns</b> — show/hide Accounting columns (<i>View → Columns</i>).</li>
</ul>

<h3>Master tab</h3>
<p>
Matches the EGM TOTAL CREDIT screen. <b>Handpay In</b> is DeviceManager
<code>handpay*InAmt</code> (manual pay-in) — <b>not</b> SAS code <b>0023</b>
(that code is hand-paid <b>out</b>).
</p>

<h3>Keyboard shortcuts</h3>
<ul>
<li><b>Ctrl+Alt+Shift+M</b> — other monitor (works even if the game has focus).</li>
<li><b>Ctrl+Alt+Shift+K</b> — close the app (global).</li>
<li><b>Ctrl+Alt+Shift+T</b> — next meter tab (global).</li>
<li><b>Ctrl+Tab</b> / <b>Ctrl+Shift+Tab</b> (or PgDown / PgUp) — next / previous tab.</li>
<li><b>Ctrl+1</b> … <b>Ctrl+8</b> — Accounting … MagicWheel.</li>
<li><b>Ctrl+C</b> — copy focused row (TSV).</li>
</ul>

<h3>Right-click a cell</h3>
<ul>
<li><b>Copy</b> — row / column / all (TSV for Excel).</li>
<li><b>Open Machine source file…</b> — newest gm2au <i>DeviceManagerData.xml_*</i>
    (slot or <code>ruleta\\var</code>).</li>
<li><b>Open SAS source file…</b> — local SASControler snapshot, or this tool's SAS log.</li>
</ul>

<p>Bottom-left status shows the last successful fetch time. Hover a meter name for
<b>True meter</b> vs <i>Derived meter</i>.</p>
"""

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
_KEY_ALWAYS_ON_TOP = "always_on_top"
_KEY_ON_SECONDARY_MONITOR = "on_secondary_monitor"
_KEY_AUTO_FETCH = "auto_fetch"
_KEY_SHOW_DOLLARS = "show_dollars"
_KEY_METER_TAB = "meter_tab"
_KEY_SCAN_ROOT = "scan_root"
_KEY_CABINET_IP = "cabinet_ip"
# Bump when default column layout changes so saved prefs reset once.
_SAS_VERIFY_COLUMN_PREFS_VERSION = 2
_KEY_COLUMN_PREFS_VERSION = "column_prefs_version"

def _sas_verify_settings():
    """Shared QSettings handle for SAS Verify session prefs (same machine)."""
    from config_manager import SettingsManager

    return SettingsManager._s()


def load_persisted_scan_root() -> str:
    """Last Scan root typed in SAS Verify (empty when never saved)."""
    try:
        raw = _sas_verify_settings().value(f"sasVerify/{_KEY_SCAN_ROOT}", "", type=str)
        return (raw or "").strip()
    except Exception:
        return ""


def save_persisted_scan_root(scan_root: str) -> None:
    try:
        s = _sas_verify_settings()
        text = (scan_root or "").strip()
        if text:
            s.setValue(f"sasVerify/{_KEY_SCAN_ROOT}", text)
        else:
            s.remove(f"sasVerify/{_KEY_SCAN_ROOT}")
        s.sync()
    except Exception:
        pass


def load_persisted_cabinet_ip() -> str:
    """Last Cabinet IP chosen in SAS Verify (empty when never saved)."""
    try:
        raw = _sas_verify_settings().value(f"sasVerify/{_KEY_CABINET_IP}", "", type=str)
        return (raw or "").strip()
    except Exception:
        return ""


def save_persisted_cabinet_ip(ip: str) -> None:
    try:
        s = _sas_verify_settings()
        text = (ip or "").strip()
        if text:
            s.setValue(f"sasVerify/{_KEY_CABINET_IP}", text)
        else:
            s.remove(f"sasVerify/{_KEY_CABINET_IP}")
        s.sync()
    except Exception:
        pass


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
TAB_MAGICWHEEL = 7
_METER_TAB_NAMES: tuple[str, ...] = (
    "Accounting",
    "Game",
    "Master",
    "Bills",
    "Coins",
    "Transfer",
    "Security",
    "MagicWheel",
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
# Fixed column widths (COUNT matches Master/Game/Transfer count fields).
# Never stretch the last section — that made COUNT eat the whole panel.
_BILLS_COINS_LABEL_COL_WIDTH = 72
_BILLS_COINS_AMOUNT_COL_WIDTH = 80
_COINS_PANELS: tuple[tuple[str, str], ...] = (
    ("in", "COIN IN"),
    ("out", "COIN OUT"),
    ("drop", "COIN TO DROP BOX"),
    ("hopper", "COIN TO HOPPER"),
)

# Master tab — EGM-style TOTAL CREDIT / HANDPAY OUT / WAGERED layout.
# Handpay In is not SAS LP 0023 (that is Total Hand Paid / out). EGM Maestro
# "Insertar el pago manual" comes from DeviceManager handpay*InAmt — synthetic HPIN.
MASTER_HANDPAY_IN_CODE = "HPIN"
# Cash / transfer into the machine only. SAS 0000 (Total Coin In) is amount
# *wagered* — including it here made Master Credit In / Total In grow on every
# bet ("cash inside also adds a bet").
MASTER_CREDIT_IN_CODES: tuple[str, ...] = ("000B", "0017", "0015", MASTER_HANDPAY_IN_CODE)
MASTER_CREDIT_OUT_CODES: tuple[str, ...] = ("006E", "0001", "0003", "0016", "0018")
MASTER_HANDPAY_CODES: tuple[str, ...] = ("0003", "0002", "001F", "0020", "001D")
MASTER_CANCELLED_CODE = "0004"
# 0000 = Total Coin In (credits played / bets), not physical cash drop.
MASTER_WAGERED_CODES: tuple[str, ...] = ("0000", "001C", "00A4", "00A2")
MASTER_TRACKED_CODES: tuple[str, ...] = (
    *MASTER_CREDIT_IN_CODES,
    *MASTER_CREDIT_OUT_CODES,
    *MASTER_HANDPAY_CODES,
    MASTER_CANCELLED_CODE,
    *MASTER_WAGERED_CODES,
)
MASTER_CREDIT_IN_ROWS: tuple[tuple[str, str], ...] = (
    ("Bill In", "000B"),
    ("Remote In", "0017"),
    ("Ticket In", "0015"),
    ("Handpay In", MASTER_HANDPAY_IN_CODE),
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
    ("Coin In (wagered)", "0000"),
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
    Parse pasted SAS RX<= 6F lines (multi-meter format) **or** IGT tester
    yellow-box ``$6F = Meter N Code / Meter N`` lines.

    Uses :mod:`network.sas_parser` for byte-accurate framing; maps wire-order meter
    codes to the verify-table ids (``1800`` -> ``0018``). Later RX lines override
    earlier values for the same meter.
    """
    from network.sas_parser import parse_rx_response_ordered

    merged: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        frame = _hex_bytes_from_sas_line(raw_line, direction="RX")
        if frame is None or len(frame) < 2 or frame[1] != 0x6F:
            continue
        spaced = " ".join(f"{b:02X}" for b in frame)
        line = f"RX<= {spaced}"
        for wire_code, value in parse_rx_response_ordered(line):
            verify_code = wire_meter_code_to_6f_verify_code(wire_code)
            merged[verify_code] = str(value)

    # IGT SAS tester Message Display (Quick Commands), e.g.:
    #   $6F = Meter 1 Code     = 0500
    #   $6F = Meter 1          = 000000000000000002
    pending_codes: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or not line.upper().startswith("$6F"):
            continue
        m_code = re.match(
            r"(?i)\$6F\s*=\s*Meter\s+(\d+)\s+Code\s*=\s*([0-9A-F]{4})\s*$",
            line,
        )
        if m_code:
            pending_codes[m_code.group(1)] = m_code.group(2).upper()
            continue
        m_val = re.match(
            r"(?i)\$6F\s*=\s*Meter\s+(\d+)\s*=\s*([0-9A-F]+)\s*$",
            line,
        )
        if not m_val:
            continue
        idx, raw_val = m_val.group(1), m_val.group(2).upper()
        wire = pending_codes.pop(idx, "")
        if not wire:
            continue
        verify_code = wire_meter_code_to_6f_verify_code(wire)
        merged[verify_code] = _igt_6f_meter_value_to_credits(raw_val)
    return [Sas6FRow(meter_id=code, sas_value_text=val) for code, val in merged.items()]


def _igt_6f_meter_value_to_credits(raw: str) -> str:
    """IGT Message Display meter field → decimal credit string for the verify table."""
    s = (raw or "").strip().upper()
    # BCD credits are decimal digits only; A-F means corrupt payload.
    if not s or not re.fullmatch(r"[0-9]+", s):
        return ""
    return str(int(s, 10))


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
    from network.meter_comparator import meter_value_to_int_or_none

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
    if not value_bytes:
        return meter_ix, "0"
    value = meter_value_to_int_or_none(value_bytes.hex().upper())
    if value is None:
        return None
    return meter_ix, str(value)


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

    _MAX_PENDING_TX = 8
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
                while len(pending_tx_indexes) > _MAX_PENDING_TX:
                    pending_tx_indexes.pop(0)
            continue

        rx_frame = _hex_bytes_from_sas_line(line, direction="RX")
        if rx_frame is None:
            continue
        decoded = _decode_2f_rx_frame(rx_frame)
        if decoded is None:
            continue
        igt_ix, value_text = decoded
        if pending_tx_indexes and pending_tx_indexes[0] == igt_ix:
            pending_tx_indexes.pop(0)
        _store(igt_ix, value_text)

    return out


class SasVerifyEmitter(QObject):
    finished = Signal(object)  # dict[str, str] or error string


def _scan_root_is_unc(scan_root: str) -> bool:
    """True for ``\\\\host\\…`` (and ``//host/…``) paths — first SMB can stall for seconds."""
    s = (scan_root or "").strip()
    return s.startswith("\\\\") or s.startswith("//")


def _unc_host_only(scan_root: str) -> str:
    """
    Host component of a UNC path, or ``""`` when the path is not UNC.

    Accepts hostnames as well as IPs (``\\\\GST20664\\c$\\…``) and understands the
    extended-length ``\\\\?\\UNC\\host\\share`` form. Unlike an IP regex over the
    whole string this never matches an address embedded in a *local* folder name.
    """
    s = (scan_root or "").strip().replace("/", "\\")
    if not s.startswith("\\\\"):
        return ""
    parts = [p for p in s[2:].split("\\")]
    if not parts:
        return ""
    head = parts[0].strip()
    if head in ("?", ".") :
        if len(parts) > 2 and parts[1].strip().upper() == "UNC":
            return parts[2].strip()
        return ""
    return head


def _extract_unc_host(scan_root: str) -> str:
    """
    Best-effort extraction of the host component from a UNC path:
    ``\\\\10.0.0.90\\c$\\...`` -> ``10.0.0.90``
    """
    host = _unc_host_only(scan_root)
    if host:
        return host
    s = (scan_root or "").strip().replace("/", "\\")
    # Import inside function to avoid any module-level side effects.
    from network.accounting_state_loader import extract_ip_from_path

    return extract_ip_from_path(s)

def _newest_existing_file(paths: Iterable[Path]) -> Path | None:
    """Most recently modified file among *paths* that actually exists, or None."""
    best: Path | None = None
    best_mtime = -1.0
    for p in paths:
        try:
            if not p.is_file():
                continue
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best = p
    return best


# Prefetch may reuse an in-memory Machine snapshot briefly; explicit Get Meters /
# Compare / Refresh always force-reload (see ``_begin_cabinet_compare(force=True)``).
CABINET_MACHINE_CACHE_TTL_S = 15.0

# Auto fetch: minimum spacing between forced refreshes (coalesces a burst of
# EGM state writes into one reload) and the retry delay when work is already
# in flight (never start a refresh on top of a live compare / COM capture).
# Measured on .90 over SMB, the whole Machine read (stat + parse) is ~40 ms, so
# the old 3 s spacing was pure lag: dropped to 1.5 s so a played game shows in
# ~1-2 s instead of ~10 s. The Machine column repaints as soon as the cabinet
# XML compare lands, independent of the (slower) SAS/COM capture.
AUTO_FETCH_MIN_REFRESH_S = 1.5
# Local EGM: short coalesce only. The old 2 s was pure lag — write storms are
# absorbed by the dual-source pair wait below (one LocalDiff, not Compare+Diff).
AUTO_FETCH_MIN_REFRESH_LOCAL_S = 0.35
# While product debug "option Q" (~3 games/s) drives a write storm, coalesce
# local refreshes a bit tighter so the activity strip stays alive without
# stacking full-table rebuilds.
AUTO_FETCH_MIN_REFRESH_LOCAL_BURST_S = 0.20
AUTO_FETCH_BUSY_RETRY_S = 1.0
# Remote SMB poll cadence. The stat is ~30-40 ms, so 4 s was needless lag; 1 s
# notices a fresh game write almost immediately without hammering the share.
AUTO_FETCH_POLL_INTERVAL_MS = 1000
AUTO_FETCH_POLL_INTERVAL_LOCAL_MS = 500
# Local EGM: after either gm2au or SASControler1 writes, wait this long for the
# other folder before reading. Measured on .90: the lagging side is often
# <50 ms, but can be several seconds — reading early flashes false MISMATCH.
AUTO_FETCH_LOCAL_PAIR_WAIT_S = 1.5
AUTO_FETCH_LOCAL_PAIR_RETRY_MS = 150
# How long a SYNCING row is given to settle before the table repaints itself
# to show the real MATCH/MISMATCH verdict (see compare_status()).
_SETTLE_REPAINT_MS = 1500

# Whole-row highlight after a confirmed MATCH meter change (text stays opaque).
# Long enough to catch on a cabinet you are standing at: a meter moves while you
# are looking at the wheel, not the screen.
METER_FLASH_DURATION_S = 3.0
# Full strength for this long before the fade starts, so glancing over late in
# the hold still shows a fully lit row.
METER_FLASH_HOLD_S = 0.9
METER_FLASH_TICK_MS = 40
# Rapid play (option Q / ~3 games/s): a 3 s pulse spans many games and sticks
# every row orange. Burst mode uses a short envelope that clears before the
# next cluster.
METER_FLASH_BURST_DURATION_S = 0.45
METER_FLASH_BURST_HOLD_S = 0.12
METER_FLASH_BURST_TICK_MS = 50
# Amber reads clearly against both themes and against the green MATCH text;
# translucent so the foreground is never dimmed (foreground is untouched).
METER_FLASH_RGB = (255, 176, 32)
METER_FLASH_PEAK_ALPHA = 150
# Burst-mode detector: landings that moved watched meters (see rate estimator).
BURST_LANDING_RING_SIZE = 8
BURST_ENTER_GAMES_PER_S = 1.0
BURST_ENTER_CLUSTER_COUNT = 3
BURST_ENTER_CLUSTER_WINDOW_S = 1.5
BURST_EXIT_IDLE_S = 2.0
# Meter codes used for the burst activity strip deltas.
_BURST_GAMES_CODE = "0005"
_BURST_COININ_CODE = "0000"
# Auto-fetch watchdogs: a wedged mtime poll or open round must not block forever.
AUTO_FETCH_POLL_STALE_S = 30.0
AUTO_FETCH_ROUND_STALE_S = 90.0
# A round whose workers have all finished but which no completion path closed.
# Safety net only — local LocalDiff and COM paths must close themselves. Kept
# short so a missed close cannot hold SYNCING for seconds.
AUTO_FETCH_ROUND_IDLE_S = 0.75
# Empty Machine reload during Auto fetch: keep last good snapshot and retry.
# Retries are Machine-only (never a COM capture), back off exponentially and
# give up after the cap — the mtime watcher / share recovery takes over then.
AUTO_FETCH_EMPTY_MACHINE_RETRY_MS = 2500
AUTO_FETCH_EMPTY_MACHINE_RETRY_MAX = 5
# Share recovery may orphan a wedged Machine compare (same threshold as force restart).
SHARE_RECOVERY_COMPARE_STUCK_S = 20.0
# Game recovery: disarm after this many inconclusive WinRM probes (~60 s at 4 s/timer).
GAME_RECOVERY_INCONCLUSIVE_MAX = 15
# Wedged COM capture (serial open/sync hung) — orphan like compare at 45 s.
METER_FETCH_STALE_S = 90.0
# Value columns watched for a raw-value change; the whole row pulses on MATCH.
_METER_FLASH_WATCH_COLS = frozenset({COL_SAS_6F_VALUE, COL_SAS_2F_VALUE, COL_MACHINE_VALUE})
# Readable column names for the millisecond trace (see gui.meter_timing_trace).
_METER_TRACE_COLUMN_NAMES = {
    COL_SAS_6F_VALUE: "sas_6f",
    COL_SAS_2F_VALUE: "sas_2f",
    COL_MACHINE_VALUE: "machine",
}


def meter_value_increased(prev_raw: str, new_raw: str) -> bool:
    """True only when both sides are numbers and the meter actually went up.

    Meters are monotonic counters, so anything else is churn rather than a
    change. A raw string compare flashed rows whose displayed number never
    moved: every Auto fetch reload blanks the Machine column and refills it with
    the same value (``"" -> "49580"``), which is unequal as text.
    """
    try:
        return int(str(new_raw).strip()) > int(str(prev_raw).strip())
    except (TypeError, ValueError):
        return False


def meter_flash_strength(
    elapsed_s: float,
    duration_s: float = METER_FLASH_DURATION_S,
    hold_s: float = METER_FLASH_HOLD_S,
) -> float:
    """0..1 highlight envelope: hold at full, then a smooth fade back to nothing.

    Deliberately monotonic after the hold. The previous two-beat pulse dropped
    to near zero halfway through its own run, so looking over at the wrong
    moment showed an unlit row and the change was missed. Never dims text.
    """
    import math

    if elapsed_s < 0.0 or duration_s <= 0.0 or elapsed_s >= duration_s:
        return 0.0
    hold = max(0.0, min(float(hold_s), float(duration_s)))
    if elapsed_s <= hold:
        return 1.0
    fade = float(duration_s) - hold
    if fade <= 0.0:
        return 1.0
    u = (elapsed_s - hold) / fade
    return max(0.0, min(1.0, 0.5 * (1.0 + math.cos(math.pi * u))))


def meter_flash_background(strength: float) -> QColor | None:
    """Translucent fill for a flashing cell, or ``None`` when the pulse is over."""
    s = max(0.0, min(1.0, float(strength)))
    if s <= 0.001:
        return None
    alpha = int(round(METER_FLASH_PEAK_ALPHA * s))
    if alpha <= 0:
        return None
    r, g, b = METER_FLASH_RGB
    return QColor(r, g, b, alpha)


def estimate_landing_rate(timestamps: list[float] | tuple[float, ...]) -> float:
    """Games/s from a ring of monotonic landing times (increases that moved meters)."""
    if len(timestamps) < 2:
        return 0.0
    span = float(timestamps[-1]) - float(timestamps[0])
    if span <= 0.0:
        return 0.0
    return (len(timestamps) - 1) / span


def burst_mode_should_be_on(
    timestamps: list[float] | tuple[float, ...],
    *,
    now: float,
    currently_on: bool,
    enter_rate: float = BURST_ENTER_GAMES_PER_S,
    cluster_count: int = BURST_ENTER_CLUSTER_COUNT,
    cluster_window_s: float = BURST_ENTER_CLUSTER_WINDOW_S,
    exit_idle_s: float = BURST_EXIT_IDLE_S,
) -> bool:
    """Hysteresis for rapid-play visuals.

    Enter when the landing rate is high *or* a tight cluster of landings arrives.
    Exit only after *exit_idle_s* with no new landing so the strip does not flicker.
    """
    if not timestamps:
        return False
    last = float(timestamps[-1])
    idle = max(0.0, float(now) - last)
    if currently_on:
        return idle < float(exit_idle_s)
    rate = estimate_landing_rate(timestamps)
    if rate >= float(enter_rate):
        return True
    recent = [t for t in timestamps if float(now) - float(t) <= float(cluster_window_s)]
    return len(recent) >= int(cluster_count)


def format_burst_activity_strip(
    *,
    games_per_s: float,
    delta_games: int,
    delta_coinin: int,
    last_ago_s: float,
) -> str:
    """One-line burst HUD under the prefetch status."""
    ago_ms = max(0, int(round(float(last_ago_s) * 1000.0)))
    return (
        f"Burst ~{float(games_per_s):.1f} games/s"
        f"  ·  Δgames +{max(0, int(delta_games))}"
        f"  ·  Δcoin-in +{max(0, int(delta_coinin))}"
        f"  ·  last {ago_ms} ms ago"
    )


def meter_value_delta(prev_raw: str, new_raw: str) -> int:
    """Positive integer increase, or 0 when not a numeric rise."""
    try:
        prev = int(str(prev_raw).strip())
        new = int(str(new_raw).strip())
    except (TypeError, ValueError):
        return 0
    return max(0, new - prev)


def should_skip_cabinet_reload(
    *,
    scan_root: str,
    loaded_scan_root: str,
    machine_state_loaded: bool,
    loaded_at: float | None = None,
    ttl_seconds: float | None = None,
    now: float | None = None,
) -> bool:
    """True when cabinet XML for *scan_root* is already in memory and still fresh.

    Matching ``scan_root`` alone is not enough when *ttl_seconds* is set: a prior
    snapshot must also be younger than the TTL (monotonic clock). When
    *ttl_seconds* is ``None``, same-root + loaded is treated as valid (tests /
    callers that opt out of ageing).
    """
    sr = (scan_root or "").strip()
    if not machine_state_loaded or not sr:
        return False
    if sr != (loaded_scan_root or "").strip():
        return False
    if ttl_seconds is None:
        return True
    if loaded_at is None:
        return False
    t = float(now) if now is not None else time.monotonic()
    return (t - float(loaded_at)) <= float(ttl_seconds)



def evaluate_auto_fetch_poll(baseline_mtime: float, current_mtime: float) -> tuple[float, bool]:
    """
    Auto-fetch watcher decision: returns ``(new_baseline, should_fetch)``.

    The first successful poll only records the baseline (state already shown was
    loaded when the dialog opened); a later, strictly newer mtime means the EGM
    wrote fresh meters and a re-fetch should run. Failed polls (<= 0) change nothing.
    """
    if current_mtime <= 0.0:
        return baseline_mtime, False
    if baseline_mtime <= 0.0:
        return current_mtime, False
    if current_mtime > baseline_mtime:
        return current_mtime, True
    return baseline_mtime, False


def meters_fetched_status_text(fetched_at: datetime) -> str:
    """Status-bar message for a completed meter fetch, second-accurate."""
    return f"Meters fetched {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}"


def pick_secondary_screen_index(screen_count: int, primary_index: int) -> int:
    """Index of the first non-primary monitor, or -1 when there is only one.

    Used by "Move to Monitor 2" so the meters window can sit on the second
    (typically non-touch) display while the game stays on the primary.
    """
    if screen_count <= 1:
        return -1
    for i in range(screen_count):
        if i != primary_index:
            return i
    return -1


def fitted_client_rect(
    available: QRect,
    margins: tuple[int, int, int, int],
    size: QSize | None = None,
    *,
    max_fraction: float = 0.8,
) -> QRect:
    """Client rect whose *window frame* lands inside *available*.

    ``setGeometry`` positions the client area, not the frame, so filling a
    screen with its available geometry pushes the title bar off the top by the
    caption height — on a second, non-touch monitor that leaves no way to grab
    or close the window. *margins* is (left, top, right, bottom) of the frame.

    With *size* omitted the window fills the monitor; with a size it is centred
    and capped to *max_fraction*, which is the landing spot for the trip back
    when there is no remembered geometry worth restoring.
    """
    left, top, right, bottom = margins
    max_w = max(1, available.width() - left - right)
    max_h = max(1, available.height() - top - bottom)
    if size is None:
        w, h = max_w, max_h
    else:
        w = max(1, min(int(size.width()), max(1, int(max_w * max_fraction))))
        h = max(1, min(int(size.height()), max(1, int(max_h * max_fraction))))
    x = available.x() + left + (max_w - w) // 2
    y = available.y() + top + (max_h - h) // 2
    return QRect(x, y, w, h)


# Row status wording for a SAS cell with no value yet.
SAS_STATUS_PENDING = "PENDING"
SAS_STATUS_NOT_REPORTED = "NOT REPORTED"
# Row status while a COM capture and a snapshot re-read are a few games apart
# (Auto fetch mid-round) — held instead of flashing MISMATCH.
SAS_STATUS_SYNCING = "SYNCING"


def missing_sas_status(*, sas_capture_expected: bool) -> str:
    """Wording for an empty SAS cell: still coming, or nothing will ever arrive.

    A local scan root has no host cable to poll — an unlisted meter in the
    cabinet's own snapshot is not "pending", it simply is not reported there.
    """
    return SAS_STATUS_PENDING if sas_capture_expected else SAS_STATUS_NOT_REPORTED


def compare_status(*, match: bool, sources_settled: bool) -> str:
    """MATCH/MISMATCH verdict, held to SYNCING while the sources have not landed together.

    A COM capture takes seconds and a snapshot read milliseconds, so during
    Auto fetch the two columns are briefly a few games apart on a machine in
    play. A difference in that window is not a real mismatch yet.
    """
    if match:
        return "MATCH"
    return "MISMATCH" if sources_settled else SAS_STATUS_SYNCING


def auto_fetch_refresh_delay_s(
    *,
    now_mono: float,
    last_refresh_mono: float,
    busy: bool,
    min_refresh_s: float | None = None,
) -> float:
    """Seconds to wait before running a queued Auto fetch refresh.

    Two independent brakes, the longer one wins: a minimum spacing between
    forced refreshes (coalesces a burst of EGM state writes into one reload),
    and a short retry when a compare / COM capture is already in flight (never
    start a refresh on top of live work).
    """
    spacing = (
        AUTO_FETCH_MIN_REFRESH_S if min_refresh_s is None else float(min_refresh_s)
    )
    elapsed = max(0.0, now_mono - last_refresh_mono)
    spacing_remaining = max(0.0, spacing - elapsed)
    if not busy:
        return spacing_remaining
    return max(spacing_remaining, AUTO_FETCH_BUSY_RETRY_S)


_COM_PORT_BUSY_ERROR_MARKERS = (
    "could not open port",
    "permissionerror",
    "access is denied",
    "looks occupied",
    "is in use",
)


def com_error_is_port_busy(message: str) -> bool:
    """True when a COM fetch error means another app is holding the port."""
    m = (message or "").lower()
    return any(marker in m for marker in _COM_PORT_BUSY_ERROR_MARKERS)


def igt_sas_tester_is_running(*, force_refresh: bool = False) -> bool:
    """True when the IGT SAS tester / SASHost family is running on this PC."""
    from network.sas_serial_meters import find_running_sas_com_blockers

    return bool(find_running_sas_com_blockers(force_refresh=force_refresh))


def com_access_denied_online_status(port: str = "", detail: str = "") -> str:
    """Status when COM open failed and no IGT SAS tester / SASHost is running."""
    p = (port or "").strip() or "SAS/MUX"
    base = (
        f"Access denied opening {p} — Windows refused the port. "
        "No IGT SAS tester / SASHost is running. Close any other meter tool "
        "using this COM port (including another SasVerifyMeters window), wait "
        "a few seconds, then click Refresh Meters."
    )
    d = (detail or "").strip()
    if d and "access denied" not in d.lower() and "occupied" not in d.lower():
        return f"{base} ({d})"
    return base


def should_offer_local_scan_prompt(
    *,
    mux_detail: str = "",
    igt_running: bool | None = None,
) -> bool:
    """Offer local D:\\ / USB scan only when COM/MUX is down and IGT is not holding it.

    Port busy without IGT → online/host system → show access denied, no prompt.
    IGT running → wait for close / auto-recovery, no prompt.
    """
    if igt_running is None:
        igt_running = igt_sas_tester_is_running()
    if igt_running:
        return False
    if com_error_is_port_busy(mux_detail):
        return False
    return True


def should_offer_g_drive_prompt(
    *,
    mux_detail: str = "",
    igt_running: bool | None = None,
    local_game_running: bool | None = None,
) -> bool:
    """Offer ``G:\\`` only when COM is down, IGT is not running, and a local EGM client is.

    ``G:\\`` is the cabinet game image on *this* device — only prompt when
    ``OneHand.exe`` or ``Ruleta.exe`` / ``godot.exe`` is running here.
    """
    if not should_offer_local_scan_prompt(
        mux_detail=mux_detail, igt_running=igt_running
    ):
        return False
    if local_game_running is None:
        from network.health_monitor import local_egm_game_client_running

        local_game_running = local_egm_game_client_running()
    return bool(local_game_running)


def local_g_drive_waiting_for_game_status(candidate: str = "") -> str:
    root = (candidate or "").strip() or "G:\\"
    return (
        f"Local game drive {root} is present, but OneHand.exe / Ruleta.exe / "
        "godot.exe is not running on this device — start the game client to "
        "use G:\\, or set a scan root manually."
    )


_SHARE_ACCESS_ERROR_MARKERS = (
    "access is denied",
    "system error 5",
    "connection failed",
    "logon failure",
    "network path",
    "winerror 5",
    "winerror 53",
    "winerror 1326",
    "smb",
    "unreachable",
)


def share_error_is_access(message: str) -> bool:
    """True when a Machine/cabinet load error means the UNC share is not accessible."""
    m = (message or "").lower()
    return any(marker in m for marker in _SHARE_ACCESS_ERROR_MARKERS)


def com_recovery_waiting_status(port: str) -> str:
    p = (port or "").strip() or "the SAS COM port"
    return (
        f"{p} is held by another app (IGT SAS tester / SASHost). Close it — "
        "COM capture retries automatically the moment the port is freed."
    )


def should_offer_cabinet_share_when_com_blocked(
    *,
    has_remote_unc: bool,
    port_busy: bool = False,
) -> bool:
    """Offer Machine+SAS from the remote cabinet share after COM Access Denied.

    Process-name checks are not used — any host (IGT, online system, unknown)
    can hold the port. The serial open error is the only source of truth.
    """
    return bool(has_remote_unc) and bool(port_busy)


# Label on the COM-blocked dialog — keep stable for tests / UI copy.
CABINET_SHARE_FETCH_BUTTON_LABEL = (
    "Fetch from cabinet share (gm2au + SASControler1)"
)
CABINET_SHARE_WAIT_COM_BUTTON_LABEL = "Wait for COM"


def cabinet_share_when_com_blocked_prompt_text(
    *,
    scan_root: str,
    cabinet_ip: str = "",
    com_detail: str = "",
) -> str:
    ip = (cabinet_ip or "").strip() or "the cabinet"
    root = (scan_root or "").strip() or f"\\\\{ip}\\c$\\Goldclub"
    detail = (com_detail or "").strip()
    detail_line = f"Windows error: {detail}\n\n" if detail else ""
    return (
        "COM port Access Denied — another app or online host holds SAS/MUX.\n"
        f"{detail_line}"
        f"Scan root: {root}\n\n"
        "Use the button below to fetch everything over the cabinet share:\n"
        f"• Machine — gm2au DeviceManager on {ip}\n"
        f"• SAS — SASControler1 snapshot on the same share\n"
        f"• No live SAS/MUX until the COM port opens again\n"
    )


def build_com_blocked_cabinet_share_dialog(
    parent: QWidget | None,
    *,
    body: str,
) -> QMessageBox:
    """Dialog with an explicit share-fetch button (not Yes/No)."""
    box = QMessageBox(parent)
    box.setWindowTitle("COM access denied — fetch via cabinet share?")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("COM port is Access Denied / held by another app.")
    box.setInformativeText(body)
    fetch_btn = box.addButton(
        CABINET_SHARE_FETCH_BUTTON_LABEL,
        QMessageBox.ButtonRole.AcceptRole,
    )
    box.addButton(
        CABINET_SHARE_WAIT_COM_BUTTON_LABEL,
        QMessageBox.ButtonRole.RejectRole,
    )
    box.setDefaultButton(fetch_btn)
    return box


def cabinet_share_only_status(scan_root: str, detail: str = "") -> str:
    root = (scan_root or "").strip() or "cabinet share"
    base = (
        f"COM blocked — meters from cabinet share ({root}); "
        "SAS column from SASControler snapshot (no live SAS/MUX)."
    )
    d = (detail or "").strip()
    return f"{base} {d}".rstrip() if d else base


_COM_LINK_DEAD_ERROR_MARKERS = (
    "sas link not responding",
    "no bytes were received",
    "only idle 0x00",
    "no sas 6f response",
    "returned no bytes",
)


def com_error_is_link_dead(message: str) -> bool:
    """True when the port opened but the EGM did not answer (client down / booting)."""
    m = (message or "").lower()
    return any(marker in m for marker in _COM_LINK_DEAD_ERROR_MARKERS)


def game_recovery_waiting_status(exe_label: str, ip: str) -> str:
    exe = (exe_label or "").strip() or "OneHand.exe / Ruleta.exe"
    where = (ip or "").strip() or "the EGM"
    return (
        f"{exe} is not running on {where} (EGM booting or game client stopped). "
        "All meters re-fetch automatically once the game client is up."
    )


def game_link_dead_status(exe_label: str, ip: str) -> str:
    """Game client is up but SAS RX stays silent — waiting cannot fix a cable."""
    exe = (exe_label or "").strip() or "the game client"
    where = (ip or "").strip() or "the EGM"
    return (
        f"SAS link is silent although {exe} is running on {where} — check the "
        "SAS/MUX cable and COM port selection, then click Refresh Meters to retry."
    )


def game_recovery_probe_failed_status(exe_label: str, ip: str) -> str:
    """WinRM could not confirm game client state — stop waiting automatically."""
    exe = (exe_label or "").strip() or "OneHand.exe / Ruleta.exe"
    where = (ip or "").strip() or "the EGM"
    return (
        f"Could not verify whether {exe} is running on {where} (WinRM unreachable). "
        "Check network access to the cabinet, then click Refresh Meters to retry."
    )


def local_files_only_status(scan_root: str, diff_summary: str = "") -> str:
    base = (
        f"Local EGM mode — Machine meters loaded from local files ({scan_root}). "
        "Live SAS/MUX compare is not possible on the machine itself; only local "
        "snapshot folders were compared."
    )
    extra = (diff_summary or "").strip()
    return f"{base} {extra}".rstrip()


def machine_meters_unavailable_status(scan_root: str) -> str:
    """Idle status when nothing is loading and Machine has no snapshot yet."""
    root = (scan_root or "").strip() or "scan root"
    return (
        f"Machine meters not found under {root} (no DeviceManagerData). "
        "Auto fetch retries when state files change — or click Refresh Meters."
    )


def local_diff_summary_text(
    source_names: list[str] | tuple[str, ...],
    diffs: dict[str, dict[str, str]],
) -> str:
    names = sorted(source_names)
    if len(names) < 2:
        return "Only one local state folder found — no cross-check possible."
    joined = " vs ".join(names)
    if not diffs:
        return f"Local state folders agree ({joined})."
    keys = ", ".join(list(diffs)[:6]) + ("…" if len(diffs) > 6 else "")
    return f"{len(diffs)} meter(s) differ between local folders ({joined}): {keys}"


def share_recovery_waiting_status(scan_root: str, host: str = "") -> str:
    h = (host or "").strip() or "<cabinet-ip>"
    return (
        "Machine column unavailable — no access to the cabinet share "
        f"({scan_root or 'scan root not set'}). SAS columns still fill from COM. "
        "Lab access is registered automatically for fleet IPs; if it still fails, run:  "
        f"cmdkey /add:{h} /user:GOLD-CLUB\\test /pass:test  "
        "— Machine reloads automatically once the share is reachable."
    )


def cabinet_unreachable_status(host: str) -> str:
    """Idle prefetch status when SMB/445 does not answer for the scan-root host."""
    h = (host or "").strip() or "cabinet"
    return (
        f"Cabinet {h} is not reachable (SMB/445). "
        "Pick another Cabinet IP above or edit Scan root — the window stays responsive."
    )


def busy_progress_should_run(
    *,
    meters_ui_pending: bool = False,
    compare_ui_pending: bool = False,
    compare_running: bool = False,
    meter_fetch_running: bool = False,
    local_diff_running: bool = False,
) -> bool:
    """True while compare / COM / LocalDiff is in flight (status-line gating).

    The thin busy progress bar was removed from the UI; this helper remains so
    finish handlers know when another worker is still running.
    """
    return bool(
        meters_ui_pending
        or compare_ui_pending
        or compare_running
        or meter_fetch_running
        or local_diff_running
    )



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
    try:
        cents = int(amount_cents)
    except (TypeError, ValueError):
        return "0.00"
    if cents < 0:
        cents = 0
    dollars, rem = divmod(cents, 100)
    return f"{dollars}.{rem:02d}"


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
    Always expand to the full denomination catalog (zeros per missing row).

    Returns ``(body_rows, aggregate_totals)`` where *aggregate_totals* is set only
    for the single-row 000B fallback path (TOTAL uses cabinet aggregate when the
    face cannot be inferred).
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
    # Empty or partial captures must still show every catalog denomination —
    # otherwise the Bills tab collapses to a header-only blank panel.
    if direction == "out":
        return list(
            merge_bill_rows_with_catalog(rows, SAS_BILL_OUT_DENOMINATIONS, direction="out")
        ), None
    return list(merge_bill_rows_with_catalog(rows, direction="in")), None


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



def bills_coins_column_widths() -> tuple[int, int, int]:
    """Label / Amount / Count widths used by Bills and Coins meter tables."""
    return (
        _BILLS_COINS_LABEL_COL_WIDTH,
        _BILLS_COINS_AMOUNT_COL_WIDTH,
        _METER_COUNT_MIN_WIDTH,
    )


def fit_bills_coins_table_columns(table: QTableWidget) -> None:
    """Pin Bills/Coins columns to compact fixed widths; lock table to content width.

    Idempotent: repeated calls with the same geometry do not change sizes, so
    Auto fetch / meter refresh does not produce horizontal jump glitches.
    """
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    widths = bills_coins_column_widths()
    total = 0
    for col, width in enumerate(widths):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        if table.columnWidth(col) != width:
            table.setColumnWidth(col, width)
        total += width
    frame = table.frameWidth() * 2
    content_w = total + frame
    if table.minimumWidth() != content_w or table.maximumWidth() != content_w:
        table.setMinimumWidth(content_w)
        table.setMaximumWidth(content_w)

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
    # Keep Fixed tables (Bills/Coins) from being stretched by the panel.
    if table.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Fixed:
        table.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum)
    outer = QVBoxLayout(box)
    outer.setContentsMargins(10, 12, 10, 8)
    outer.setSpacing(0)
    outer.addWidget(table)
    box.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)


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
    if value is None or isinstance(value, bool):
        return "—"
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(pct):
        return "—"
    return f"{pct:.2f}%"


def _count_or_zero(raw: str) -> int:
    """
    Meter count as an int, or ``0`` for anything non-numeric.

    ``format_transfer_count_display`` passes odd cabinet values through unchanged
    (``"—"``, ``"N/A"``, spaced digits), so a bare ``int()`` here would abort the
    whole Game tab render on one malformed meter.
    """
    text = format_transfer_count_display(raw).replace(",", "").replace(" ", "").strip()
    try:
        return int(text)
    except ValueError:
        return 0


def compute_game_summary(
    *,
    played_raw: str,
    won_raw: str,
    lost_raw: str,
    bet_raw: str,
    win_raw: str,
) -> dict[str, float | int | None]:
    """Derived Game-tab counters and yield from raw meter strings."""
    played = _count_or_zero(played_raw)
    won = _count_or_zero(won_raw)
    lost_text = format_transfer_count_display(lost_raw)
    if lost_text and lost_text != "0":
        lost = _count_or_zero(lost_raw)
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


class _RecoveryProbeSignals(QObject):
    # com_port_free, share_reachable, game_client_running (None = not probed/unknown)
    done = Signal(bool, bool, object)


class _ComPortRefreshSignals(QObject):
    done = Signal(str)  # normalized COM port, or ""


class _ComPortRefreshTask(QRunnable):
    """Enumerate SAS COM ports off the UI thread (list_ports can hang for 30+ s)."""

    def __init__(
        self,
        *,
        preferred: str,
        game_kind: str,
        on_cabinet: bool,
        signals: _ComPortRefreshSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._preferred = (preferred or "").strip()
        self._game_kind = (game_kind or "slot").strip().lower() or "slot"
        self._on_cabinet = bool(on_cabinet)
        self._signals = signals

    def run(self) -> None:
        from network.sas_serial_meters import (
            enumerate_serial_ports,
            normalize_com_port,
            pick_sas_com_port,
        )

        try:
            ports = enumerate_serial_ports(force_refresh=True)
            picked = pick_sas_com_port(
                self._preferred,
                ports,
                game_kind=self._game_kind,
                on_cabinet=self._on_cabinet,
            )
            port = normalize_com_port(picked or "")
        except Exception:  # noqa: BLE001
            port = ""
        try:
            self._signals.done.emit(port)
        except RuntimeError:
            pass


class _CabinetReachabilitySignals(QObject):
    done = Signal(str, bool)  # host, smb_alive


class _CabinetReachabilityTask(QRunnable):
    """SMB/445 probe off the UI thread (used for prefetch status only)."""

    def __init__(self, host: str, signals: _CabinetReachabilitySignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._host = (host or "").strip()
        self._signals = signals

    def run(self) -> None:
        from network.scanner_utils import is_smb_alive

        alive = is_smb_alive(self._host, timeout=1.5) if self._host else False
        try:
            self._signals.done.emit(self._host, alive)
        except RuntimeError:
            pass


class _RecoveryProbeTask(QRunnable):
    """Off-UI-thread probe: COM port free / UNC share back / game client up on the EGM?"""

    def __init__(
        self,
        *,
        port: str,
        scan_root: str,
        check_com: bool,
        check_share: bool,
        signals: _RecoveryProbeSignals,
        ip: str = "",
        game_kind: str = "slot",
        check_game: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._port = (port or "").strip()
        self._scan_root = (scan_root or "").strip()
        self._check_com = check_com
        self._check_share = check_share
        self._ip = (ip or "").strip()
        self._game_kind = (game_kind or "slot").strip().lower() or "slot"
        self._check_game = check_game
        self._signals = signals

    def run(self) -> None:
        com_free = False
        share_ok = False
        game_running: bool | None = None  # None = not probed / probe inconclusive
        try:
            if self._check_com and self._port:
                try:
                    from network.sas_serial_meters import probe_com_port_available

                    com_free, _detail = probe_com_port_available(self._port)
                except Exception:  # noqa: BLE001
                    com_free = False
            if self._check_share and self._scan_root:
                try:
                    host = _extract_unc_host(self._scan_root) or self._ip
                    if host:
                        from network.lab_access import ensure_lab_smb_credential

                        ensure_lab_smb_credential(host)
                    from network.goldclub_paths import unc_share_scan_root_reachable

                    share_ok = unc_share_scan_root_reachable(self._scan_root)
                except Exception:  # noqa: BLE001
                    share_ok = False
            if self._check_game and self._ip:
                try:
                    from network.health_monitor import check_game_client_status

                    status = check_game_client_status(
                        self._ip,
                        kind=self._game_kind,
                        allow_psexec=False,
                        scan_root=self._scan_root,
                    )
                    game_running = None if status is None else status.running
                except Exception:  # noqa: BLE001
                    game_running = None
        finally:
            try:
                self._signals.done.emit(bool(com_free), bool(share_ok), game_running)
            except RuntimeError:
                pass  # dialog already destroyed


class _LocalDiffSignals(QObject):
    # summary + payload dict {"sas": ..., "machine": ...} from one paired read
    done = Signal(str, object)


class _LocalDiffTask(QRunnable):
    """Local-EGM mode: cross-check the state folders (gm2au vs SASControler1)."""

    def __init__(
        self, scan_root: str, signals: _LocalDiffSignals, job_id: int = 0
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._scan_root = (scan_root or "").strip()
        self._signals = signals
        self._job_id = int(job_id)

    def run(self) -> None:
        payload: dict = {"sas": {}, "machine": {}}
        try:
            from network.accounting_state_loader import (
                diff_machine_state_sources,
                load_machine_state_sources,
                pick_gm2au_source,
                pick_sas_controller_source,
            )

            sources = load_machine_state_sources(self._scan_root)
            summary = local_diff_summary_text(
                list(sources.keys()), diff_machine_state_sources(sources)
            )
            # Both sides from the same load — never pair a later SAS snapshot
            # with an earlier Machine read (that flashed false MISMATCH).
            payload = {
                "sas": pick_sas_controller_source(sources),
                "machine": pick_gm2au_source(sources),
            }
        except Exception:  # noqa: BLE001
            summary = ""
        # Stamp the job so a task released by the stale-round watchdog cannot
        # close a later round with this (dead) round's data.
        payload["__job__"] = self._job_id
        try:
            self._signals.done.emit(summary, payload)
        except RuntimeError:
            pass  # dialog already destroyed


class _StateMtimePollSignals(QObject):
    done = Signal(float, str)  # latest mtime (0.0 on failure), scan root polled


class _StateMtimePollTask(QRunnable):
    """Stat DeviceManagerData + AFT XML mtimes off the UI thread (SMB can block)."""

    def __init__(self, scan_root: str, signals: _StateMtimePollSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._scan_root = (scan_root or "").strip()
        self._signals = signals

    def run(self) -> None:
        from network.accounting_state_loader import latest_device_state_mtime

        try:
            mtime = latest_device_state_mtime(self._scan_root)
        except Exception:  # noqa: BLE001
            mtime = 0.0
        try:
            self._signals.done.emit(mtime, self._scan_root)
        except RuntimeError:
            pass  # dialog already destroyed


class CompareWorker(QObject):
    finished = Signal(object)  # dict[str, str] or error string
    error = Signal(str)

    def __init__(self, target_ip: str, scan_root: str) -> None:
        # CRITICAL: no parent; this object will be moved to a worker thread.
        super().__init__(None)
        self._target_ip = (target_ip or "").strip()
        self._scan_root = (scan_root or "").strip()

    def run(self) -> None:
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify.compare")
        try:
            # Import ONLY inside the run method to avoid pulling UI-linked objects
            # during initialization or module import.
            from network.scanner_utils import is_smb_alive
            from network.accounting_state_loader import load_machine_accounting_state_pure
            from network.goldclub_paths import resolve_goldclub_layout
            from network.lab_access import ensure_lab_smb_credential

            host = _extract_unc_host(self._scan_root) or self._target_ip
            if host:
                ensure_lab_smb_credential(host)
            if host and not is_smb_alive(host, timeout=2.0):
                log.warning(
                    "SMB TCP/445 probe failed for %s — skipping UNC machine load",
                    host,
                )
                self.finished.emit({})
                return
            layout = resolve_goldclub_layout(self._scan_root)
            log.info(
                "compare worker start host=%s scan_root=%s layout=%s tid=%s",
                host,
                self._scan_root,
                layout.kind.value if layout else "none",
                threading.get_ident(),
            )
            state = load_machine_accounting_state_pure(self._scan_root)
            nkeys = len(state) if isinstance(state, dict) else 0
            log.info("compare worker done keys=%s scan_root=%s", nkeys, self._scan_root)
            if nkeys == 0:
                log.warning("compare worker returned empty machine state")
            self.finished.emit(state if isinstance(state, dict) else {})
        except Exception as e:  # noqa: BLE001
            log.exception("compare worker crashed: %s", e)
            self.error.emit(str(e))


class OneHandCheckWorker(QObject):
    finished = Signal(str, object, object)  # ip, running: bool | None, smb_reachable: bool | None

    def __init__(
        self,
        ip: str,
        *,
        game_kind: str = "slot",
        scan_root: str = "",
        allow_psexec: bool = False,
        psexec_only: bool = False,
    ) -> None:
        super().__init__(None)
        self._ip = (ip or "").strip()
        self._game_kind = (game_kind or "slot").strip().lower() or "slot"
        self._scan_root = (scan_root or "").strip()
        # Fast path (WinRM/WMIC) by default; the slow PsExec fallback is a separate
        # explicit tier so the initial probe stays snappy. ``psexec_only`` runs the
        # Sysinternals fallback on its own.
        self._allow_psexec = bool(allow_psexec)
        self._psexec_only = bool(psexec_only)

    def run(self) -> None:
        from network.health_monitor import (
            check_game_client_status,
            check_game_client_via_psexec,
            resolve_game_client_kind,
        )

        kind = resolve_game_client_kind(hint=self._game_kind, scan_root=self._scan_root)
        try:
            if not self._ip:
                self.finished.emit(self._ip, None, None)
                return
            if self._psexec_only:
                status = check_game_client_via_psexec(self._ip, kind=kind)
            else:
                status = check_game_client_status(
                    self._ip,
                    kind=kind,
                    allow_psexec=self._allow_psexec,
                    scan_root=self._scan_root,
                )
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
        prefetch: bool = False,
        game_kind: str = "slot",
        on_cabinet: bool = False,
        full_timing: bool = False,
        warm: bool = False,
    ) -> None:
        super().__init__(None)
        self._com_port = (com_port or "").strip()
        self._com_baud = int(com_baud)
        self._skip_bill_polls = skip_bill_polls
        self._cached_profile = cached_profile
        self._prefetch = prefetch
        self._game_kind = (game_kind or "slot").strip().lower() or "slot"
        self._on_cabinet = bool(on_cabinet)
        self._full_timing = bool(full_timing)
        # A previous capture on this port already answered $6F: skip the GP
        # wakeup flood and the port grab-wait, both of which are pure latency
        # on a link that is already up.
        self._warm = bool(warm)

    def run(self) -> None:
        try:
            import logging

            from network.sas_serial_meters import (
                DEFAULT_RESPONSE_TIMEOUT_S,
                fetch_meters_over_serial,
                find_running_sas_com_blockers,
                link_sync_polls_for_capture,
            )

            log = logging.getLogger("gui.sas_verify.com_fetch")
            # Prefetch: short GP wakeup then $6F (IGT Quick Commands). Do not
            # burn 20s×4 combos before the first meter poll — that left the UI
            # stuck on "prefetching" while IGT already had values.
            quick = self._prefetch and not self._full_timing
            # Cold captures must enumerate — a held port is the usual failure.
            # Warm Autofetch rounds may reuse the cache so tasklist does not
            # delay every $6F; never treat an empty cache as "no blockers".
            blockers = find_running_sas_com_blockers(cached_only=self._warm)
            if self._warm and not blockers:
                from network.sas_serial_meters import sas_com_blocker_cache_ready

                if not sas_com_blocker_cache_ready():
                    blockers = find_running_sas_com_blockers(force_refresh=True)
            sync_polls = link_sync_polls_for_capture(quick=quick, warm=self._warm)
            log.info(
                "Starting COM fetch port=%s kind=%s on_cabinet=%s timing=%s "
                "warm=%s sync_polls=%s bill_lps=%s blockers=%s",
                self._com_port,
                self._game_kind,
                self._on_cabinet,
                "quick" if quick else "full",
                self._warm,
                sync_polls,
                "skip" if self._skip_bill_polls else "full",
                blockers or "(none)",
            )
            # Do not fail on process names — open the port; Access Denied is truth.
            result = fetch_meters_over_serial(
                port=self._com_port,
                baud=self._com_baud,
                force_capture=True,
                skip_bill_polls=self._skip_bill_polls,
                cached_profile=self._cached_profile,
                port_wait_s=2.0 if self._warm else (6.0 if self._prefetch else 4.0),
                timeout_s=8.0 if quick else DEFAULT_RESPONSE_TIMEOUT_S,
                # Quick still probes both raw RTS on/off; full tries all wire/baud.
                # A warm profile is proven — re-probing it only costs time.
                max_combos=1
                if self._warm and self._cached_profile
                else (2 if quick and not self._cached_profile else None),
                game_kind=self._game_kind,
                on_cabinet=self._on_cabinet,
                link_sync_polls=sync_polls,
            )
            lines = (getattr(result, "paste_text", "") or "").count("\n") + 1
            log.info(
                "COM fetch OK port=%s wire=%s baud=%s paste_lines=%s",
                getattr(result, "port_used", self._com_port),
                getattr(result, "wire_mode", ""),
                getattr(result, "baud", ""),
                lines,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("gui.sas_verify.com_fetch").exception(
                "COM fetch FAILED: %s", exc
            )
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
        self._startup_remote_ip = (remote_ip or "").strip()
        self._ram_clear_busy = False
        self._ram_clear_target_label = ""
        self._ram_clear_saved_auto_fetch = None
        # After RAM Clear: never keep a pre-clear Machine snapshot on empty reload.
        self._discard_stale_machine_after_ram_clear = False
        from gui.ram_clear_worker import RamClearEmitter

        self._ram_clear_emitter = RamClearEmitter(self)
        self._ram_clear_emitter.progress.connect(self._on_ram_clear_progress)
        self._ram_clear_emitter.finished.connect(self._on_ram_clear_finished)
        from network.goldclub_paths import normalize_path_str
        from gui.app_logging import get_logger

        # Fast open: trust the provided scan_root string. Roulette/slot remapping
        # and DeviceManager probing run later in Compare (off the UI thread path
        # after first paint) via ``_resolve_active_scan_root``.
        hint = normalize_path_str(scan_root or "")
        self._scan_root = hint
        get_logger("gui.sas_verify").info("dialog init begin scan_root=%s", hint)
        # String-only heuristic here — the full resolve (which can SMB-probe a
        # generic ``…\var\log`` UNC for Ruleta.exe) would block the first paint.
        # ``_resolve_active_scan_root`` corrects the kind right after show.
        self._scan_game_kind = "roulette" if "ruleta" in hint.lower() else "slot"
        self._machine_state: dict[str, str] = {}
        self._machine_state_loaded = False
        self._compare_thread: QThread | None = None
        self._compare_worker: CompareWorker | None = None
        self._compare_job_id = 0
        self._active_compare_job_id = 0
        # job_id → scan root the CompareWorker was started with (stale-root guard).
        self._compare_job_roots: dict[int, str] = {}
        self._active_compare_scan_root = ""
        self._meter_fetch_thread: QThread | None = None
        self._meter_fetch_worker: MeterFetchWorker | None = None
        self._meter_fetch_job_id = 0
        self._active_meter_fetch_job_id = 0
        self._meter_fetch_started_mono = 0.0
        self._cabinet_ui_refresh_pending = False
        self._onehand_check_thread: QThread | None = None
        self._onehand_check_worker: OneHandCheckWorker | None = None
        self._onehand_running: bool | None = None
        self._onehand_smb_reachable: bool | None = None
        self._onehand_check_ip = ""
        self._onehand_check_pending = False
        # Tier 3 (PsExec) fallback state — only used when WinRM/WMIC were
        # inconclusive AND the SAS COM/MUX capture also failed.
        self._psexec_verify_thread: QThread | None = None
        self._psexec_verify_worker: OneHandCheckWorker | None = None
        self._psexec_verify_running = False
        self._psexec_verify_done = False
        self._last_parsed_rows: list[Sas6FRow] = []
        self._last_bill_in_rows: list = []
        self._last_bill_out_rows: list = []
        self._sas_2f_values: dict[str, str] = {}
        # Auto-show SAS ($2F) once when paste first has 2F data; do not persist.
        self._2f_autoshow_done = False
        self._currency = EgmCurrency()
        # Money view remembers the last Show $ tick (default on for first run).
        self._show_dollars = self._load_show_dollars_pref()
        self._column_actions: dict[int, QAction] = {}
        self._columns_menu: QMenu | None = None

        self._prefetch_started = False
        self._cabinet_share_unreachable = False
        self._cached_meter_result: object | None = None
        # True once a capture has returned $6F on this port, so Auto fetch may
        # re-capture without the cold GP wakeup.
        self._com_link_warm = False
        self._meter_fetch_error: str | None = None
        self._meter_fetch_user_clicked_apply = False
        self._loaded_cabinet_scan_root = ""
        self._machine_state_loaded_at = 0.0
        self._cabinet_compare_force_pending = False
        self._compare_started_mono = 0.0
        self._meters_ui_pending = False
        self._compare_ui_pending = False
        self._cabinet_compare_prefetch = False
        self._meter_fetch_prefetch = False
        self._meter_prefetch_retried = False
        self._pending_forced_fetch: dict[str, bool] | None = None
        self._accept_worker_signals = True
        self._compare_starting = False
        self._last_scan_root_unc_host = _extract_unc_host(self._scan_root or "")
        self._last_displayed_paste_fingerprint = ""
        self._about_to_quit_hooks_installed = False

        # Auto fetch: watch the EGM's state XML mtimes and re-fetch on change.
        self._auto_fetch_baseline_mtime = 0.0
        self._auto_fetch_baseline_root = ""
        self._auto_fetch_poll_running = False
        self._auto_fetch_signals = _StateMtimePollSignals()
        self._auto_fetch_signals.done.connect(
            self._on_state_mtime_polled, Qt.ConnectionType.QueuedConnection
        )
        self._auto_fetch_timer = QTimer(self)
        self._auto_fetch_timer.setInterval(AUTO_FETCH_POLL_INTERVAL_MS)
        self._auto_fetch_timer.timeout.connect(self._on_auto_fetch_timer)
        # Instant local-disk watcher (AFT XML + DeviceManagerData folders).
        # Remote UNC keeps the poll timer only — SMB watchers are unreliable.
        self._auto_fetch_fs_watcher = QFileSystemWatcher(self)
        self._auto_fetch_fs_watcher.directoryChanged.connect(self._on_auto_fetch_fs_changed)
        self._auto_fetch_fs_watcher.fileChanged.connect(self._on_auto_fetch_fs_changed)
        # Coalesced refresh queued by the mtime watcher / the toggle turning on;
        # runs after the spacing/busy brake in auto_fetch_refresh_delay_s() clears.
        self._auto_fetch_refresh_queued = False
        self._auto_fetch_last_force_mono = 0.0
        self._auto_fetch_queue_timer = QTimer(self)
        self._auto_fetch_queue_timer.setSingleShot(True)
        self._auto_fetch_queue_timer.timeout.connect(self._run_queued_auto_fetch_refresh)
        self._auto_fetch_poll_started_mono = 0.0
        self._auto_fetch_round_started_mono = 0.0
        # Per-folder DeviceManagerData mtimes after the last local paired read.
        self._auto_fetch_source_mtimes: dict[str, float] = {}
        self._auto_fetch_pair_wait_started_mono = 0.0
        # Soft whole-row pulse for meters that changed and settled MATCH.
        self._meter_flash_arm = False
        self._meter_flash_started_mono = 0.0
        self._meter_flash_keys: set[str] = set()
        self._meter_flash_pending: set[str] = set()
        self._meter_flash_timer = QTimer(self)
        self._meter_flash_timer.setInterval(METER_FLASH_TICK_MS)
        self._meter_flash_timer.timeout.connect(self._on_meter_flash_tick)
        # Rapid-play (option Q) visuals: short pulse + activity strip.
        self._burst_mode = False
        self._burst_landing_times: list[float] = []
        self._burst_delta_games = 0
        self._burst_delta_coinin = 0
        self._burst_paint_increased = False
        self._burst_paint_delta_games = 0
        self._burst_paint_delta_coinin = 0
        # A round holds MISMATCH -> SYNCING while a forced COM capture and the
        # paired Machine re-read have not both landed (see compare_status()).
        self._auto_fetch_round_active = False
        self._auto_fetch_round_resynced = False
        self._machine_empty_retry_armed = False
        self._machine_empty_retry_count = 0
        # Post COM-recovery recapture in flight: its completion must pair with
        # a fresh Machine read even though the failed round already closed.
        self._recovery_recapture_pending = False
        # Manual Refresh Meters in flight — Auto fetch must queue, not restart.
        self._manual_meters_refresh = False
        self._settle_repaint_timer = QTimer(self)
        self._settle_repaint_timer.setSingleShot(True)
        self._settle_repaint_timer.setInterval(_SETTLE_REPAINT_MS)
        self._settle_repaint_timer.timeout.connect(self._on_settle_repaint)

        # Auto-recovery: retry COM capture when the SAS tester releases the port,
        # and retry the Machine share when access comes back.
        self._com_recovery_pending = False
        self._share_recovery_pending = False
        self._share_recovery_reloading = False
        self._game_recovery_pending = False
        self._game_recovery_seen_down = False
        self._game_recovery_inconclusive_count = 0
        self._recovery_probe_running = False
        self._recovery_probe_started_mono = 0.0
        # Remote UNC + COM held by IGT: user chose Machine+SAS from the share
        # (no live SAS/MUX) until the port is free again.
        self._cabinet_share_only_mode = False
        self._cabinet_share_prompt_asked = False
        self._local_diff_summary = ""
        self._local_diff_running = False
        self._local_diff_job_id = 0
        # Local scan root SAS side (SASControler* snapshot under GCMessenger or
        # ruleta\\var). Live COM capture replaces this when a host cable is used.
        self._local_sas_state: dict[str, str] = {}
        # Sticky: a settled MISMATCH was painted this session (survives SYNCING churn).
        self._had_settled_mismatch = False
        self._local_diff_signals = _LocalDiffSignals()
        self._local_diff_signals.done.connect(
            self._on_local_diff_done, Qt.ConnectionType.QueuedConnection
        )
        self._recovery_signals = _RecoveryProbeSignals()
        self._recovery_signals.done.connect(
            self._on_recovery_probe_done, Qt.ConnectionType.QueuedConnection
        )
        self._com_port_refresh_signals = _ComPortRefreshSignals()
        self._com_port_refresh_signals.done.connect(
            self._on_com_port_refreshed, Qt.ConnectionType.QueuedConnection
        )
        self._com_port_refresh_running = False
        self._cabinet_reach_signals = _CabinetReachabilitySignals()
        self._cabinet_reach_signals.done.connect(
            self._on_cabinet_reachability_probed, Qt.ConnectionType.QueuedConnection
        )
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(4000)
        self._recovery_timer.timeout.connect(self._on_recovery_timer)

        self.setWindowTitle("SAS accounting verification")
        from gui.app_branding import apply_window_branding

        apply_window_branding(self)
        self.setWindowFlags(_SAS_VERIFY_WINDOW_FLAGS)
        self.setMinimumSize(800, 560)
        self.resize(1180, 780)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 5)
        root.setSpacing(4)
        self._always_on_top = False
        self._on_secondary_monitor = False
        self._pre_move_geometry: QByteArray | None = None
        self._global_hotkeys: GlobalHotkeyManager | None = None
        self._global_hotkeys_installed = False
        root.addWidget(self._build_view_menu_bar())
        # Always on at each launch (user can turn it off for this session).
        self._always_on_top_action.setChecked(True)
        # Restore the last "Move to Monitor 2" choice without firing the move
        # yet — showEvent places the window once the screen list is known.
        blocked = self._move_monitor2_action.blockSignals(True)
        self._move_monitor2_action.setChecked(self._read_on_secondary_pref())
        self._move_monitor2_action.blockSignals(blocked)
        info_label = QLabel(
            f"Compare: SAS = live COM · Machine = scan-root snapshot &nbsp;·&nbsp; "
            f"<b style='color:{_TRUE_METER_COLOR};'>True meter</b> / "
            f"<i style='color:{_DERIVED_METER_COLOR};'>Derived meter</i> "
            f"&nbsp;·&nbsp; hover for details"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setStyleSheet("QLabel { font-size: 11px; }")
        info_label.setToolTip(
            "<div style='max-width:480px;'>"
            "Compare: SAS columns = live COM (host cable / MUX); "
            "Machine column = scan-root snapshot (cabinet DeviceManager XML). "
            "On a workstation, Machine prefers the remote share even when COM is attached "
            "(local G:\\\\ is not the EGM on the cable). Local scan root only when running "
            "on the EGM, or when remote is down (then D:\\\\ USB / local folder). "
            "Close IGT SAS tester if it holds the COM port. "
            "Diagnostics: SasVerifyMeters.log next to this program "
            "(or LogInvestigator.log when opened from the main app)."
            "<br><br>Meter Name legend: "
            f"<b style='color:{_TRUE_METER_COLOR};'>True meter</b> = direct cabinet value "
            f"&nbsp;·&nbsp; <i style='color:{_DERIVED_METER_COLOR};'>Derived meter</i> "
            "= computed in code from multiple meters (hover the meter name for its formula)."
            "</div>"
        )
        root.addWidget(info_label)

        # Cabinet IP drives the default Scan root UNC (lab fleet dropdown).
        self._cabinet_ip_changing = False
        cabinet_row = QHBoxLayout()
        cabinet_row.addWidget(QLabel("Cabinet IP:"))
        self._cabinet_ip_combo = QComboBox()
        self._cabinet_ip_combo.setEditable(True)
        self._cabinet_ip_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._cabinet_ip_combo.setMaximumWidth(148)
        from network.lab_access import LAB_FLEET_IPS

        for fleet_ip in sorted(LAB_FLEET_IPS):
            self._cabinet_ip_combo.addItem(fleet_ip)
        self._cabinet_ip_combo.setCurrentText(self._resolve_initial_cabinet_ip())
        self._cabinet_ip_combo.setToolTip(
            "Lab cabinet address for the Machine column. Changing this rebuilds "
            "Scan root to \\\\IP\\c$\\Goldclub\\var (you can still edit Scan root "
            "for USB exports or custom paths)."
        )
        self._cabinet_ip_combo.currentTextChanged.connect(self._schedule_cabinet_ip_change)
        self._cabinet_ip_debounce_timer = QTimer(self)
        self._cabinet_ip_debounce_timer.setSingleShot(True)
        self._cabinet_ip_debounce_timer.setInterval(450)
        self._cabinet_ip_debounce_timer.timeout.connect(self._apply_debounced_cabinet_ip_change)
        cabinet_row.addWidget(self._cabinet_ip_combo)
        cabinet_row.addStretch(1)
        root.addLayout(cabinet_row)

        # Allow operators to paste/override UNC paths (do not lock this field).
        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Scan root:"))
        self._scan_root_edit = QLineEdit()
        self._scan_root_edit.setText(self._scan_root or "")
        self._scan_root_edit.setReadOnly(False)
        self._scan_root_edit.setEnabled(True)
        self._scan_root_edit.setPlaceholderText(
            "Log root (roulette: ruleta\\var\\gm2au; slot: GCMessenger)…"
        )
        scan_row.addWidget(self._scan_root_edit, stretch=1)
        # SAS COM port is auto-detected (no manual picker): live capture when the
        # host cable is attached, otherwise cabinet snapshot meters are used.
        # Serial enumeration is deferred to ``_deferred_startup_warm`` (400 ms
        # after show) — enumerating COM ports here delayed the first paint.
        self._load_com_port_prefs()
        self._btn_get_meters = QPushButton("Get Meters")
        self._btn_get_meters.setToolTip(
            "Capture SAS 6F meters live over the auto-detected COM port (IGT five polls). "
            "Prefetch runs when this dialog opens and fills the tables automatically. "
            "SAS columns use COM when available; Machine always comes from the Scan root "
            "(remote UNC on a workstation, local only on the EGM or offline USB)."
        )
        self._btn_get_meters.clicked.connect(self._on_get_meters_clicked)
        scan_row.addWidget(self._btn_get_meters)
        root.addLayout(scan_row)

        # Busy progress line removed from the UI. Call sites still invoke the
        # no-op helpers below so Auto fetch / Compare paths stay unchanged.
        self._busy_progress = None
        self._busy_progress_suppressed = False
        self._busy_idle_timer = None

        self._prefetch_status_full = ""
        self._prefetch_status_label = QLabel("")
        self._prefetch_status_label.setWordWrap(False)
        self._prefetch_status_label.setFixedHeight(18)
        self._prefetch_status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._prefetch_status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._prefetch_status_label.setStyleSheet(
            "QLabel { color: #475569; font-size: 11px; padding: 1px 0; }"
        )
        root.addWidget(self._prefetch_status_label)

        self._burst_activity_label = QLabel("")
        self._burst_activity_label.setWordWrap(False)
        self._burst_activity_label.setFixedHeight(0)
        self._burst_activity_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._burst_activity_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._burst_activity_label.setStyleSheet(
            "QLabel { color: #b45309; font-size: 11px; padding: 1px 0; }"
        )
        self._burst_activity_label.hide()
        root.addWidget(self._burst_activity_label)

        self._onehand_warning = QLabel("")
        self._onehand_warning.setWordWrap(True)
        self._onehand_warning.setTextFormat(Qt.TextFormat.RichText)
        self._onehand_warning.setStyleSheet(
            "QLabel { background-color: #fef3c7; color: #78350f; padding: 6px 8px; "
            "border: 1px solid #f59e0b; border-radius: 4px; }"
        )
        self._onehand_warning.hide()
        root.addWidget(self._onehand_warning)

        self._com_blocker_warning = QLabel("")
        self._com_blocker_warning.setWordWrap(True)
        self._com_blocker_warning.setTextFormat(Qt.TextFormat.RichText)
        self._com_blocker_warning.setStyleSheet(
            "QLabel { background-color: #fee2e2; color: #7f1d1d; padding: 6px 8px; "
            "border: 1px solid #ef4444; border-radius: 4px; }"
        )
        self._com_blocker_warning.hide()
        root.addWidget(self._com_blocker_warning)

        self._onehand_check_timer = QTimer(self)
        self._onehand_check_timer.setSingleShot(True)
        self._onehand_check_timer.setInterval(450)
        self._onehand_check_timer.timeout.connect(self._run_onehand_check)
        self._scan_root_edit.textChanged.connect(self._on_scan_root_edit_changed)
        saved_ip = load_persisted_cabinet_ip()
        if saved_ip:
            from network.health_monitor import is_valid_remote_cabinet_ip

            if is_valid_remote_cabinet_ip(saved_ip):
                host = _extract_unc_host(self._scan_root_edit.text())
                if host != saved_ip:
                    self._on_cabinet_ip_changed(saved_ip)

        self._paste = QTextEdit()
        self._paste.setPlaceholderText(
            "Paste TX>= / RX<= hex, or IGT Message Display ($6F = Meter N Code / Meter N)…"
        )
        # Compact strip — the meter tabs (esp. Accounting) need the vertical room.
        self._paste.setMinimumHeight(40)
        self._paste.setMaximumHeight(90)

        self._table = self._make_verify_table_widget()
        self._verify_tables: tuple[QTableWidget, ...] = (self._table,)
        self._accounting_box: QGroupBox | None = None
        self._master_value_labels: dict[str, QLabel] = {}
        self._master_value_label_codes: dict[str, str] = {}
        self._master_credit_in_total: QLabel | None = None
        self._master_credit_out_total: QLabel | None = None
        self._master_total_credit: QLabel | None = None
        self._master_inout_pct: QLabel | None = None
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
        self._magicwheel_tab = self._build_magicwheel_tab()
        self._accounting_tab = self._build_accounting_tab()
        self._load_column_visibility_prefs()
        self._apply_column_visibility()
        for tbl in self._verify_tables:
            tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tbl.customContextMenuRequested.connect(
                lambda pos, t=tbl: self._on_verify_table_context_menu(t, pos)
            )

        bills_tab = self._build_bills_tab()
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
        self._meter_tabs.addTab(self._magicwheel_tab, _METER_TAB_NAMES[TAB_MAGICWHEEL])
        self._meter_tabs.currentChanged.connect(self._on_meter_tab_changed)
        # UNC MagicWheel lookup is deferred — a missing \\ip\slot share used to
        # block the first paint for the Windows SMB timeout (white window).
        if _scan_root_is_unc(self._scan_root):
            if hasattr(self._meter_tabs, "setTabVisible"):
                self._meter_tabs.setTabVisible(TAB_MAGICWHEEL, False)
            else:
                self._meter_tabs.setTabEnabled(TAB_MAGICWHEEL, False)
        else:
            self._sync_magicwheel_tab_visibility()
        self._restore_meter_tab_pref()
        self._sync_columns_menus_for_tab(self._meter_tabs.currentIndex())

        self._content_split = QSplitter(Qt.Orientation.Vertical)

        self._content_split.addWidget(self._paste)
        self._content_split.addWidget(self._meter_tabs)
        # Table should absorb almost all resize; paste stays a compact strip.
        self._content_split.setStretchFactor(0, 1)
        self._content_split.setStretchFactor(1, 14)
        self._content_split.setChildrenCollapsible(False)
        self._split_meter_dominant_applied = False
        self._window_geometry_restored = False
        self._startup_monitor_applied = False
        root.addWidget(self._content_split, stretch=1)

        # Ctrl+C copies FULL rows (all columns) as TSV from whichever meter tab has focus.
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self._meter_tabs)
        copy_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_sc.activated.connect(self._copy_focused_table_row_tsv)

        # In-app fallbacks for the global combos (the system-wide RegisterHotKey
        # in showEvent covers the case where the game window holds focus). Same
        # combos so there is one set to remember.
        kill_sc = QShortcut(QKeySequence("Ctrl+Alt+Shift+K"), self)
        kill_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        kill_sc.activated.connect(self._on_kill_hotkey)
        move_sc = QShortcut(QKeySequence("Ctrl+Alt+Shift+M"), self)
        move_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        move_sc.activated.connect(self._on_toggle_monitor_hotkey)
        self._install_tab_shortcuts()

        row = QHBoxLayout()
        self._fetched_status_label = QLabel("")
        self._fetched_status_label.setStyleSheet("color: gray; font-size: 11px;")
        row.addWidget(self._fetched_status_label)
        row.addStretch(1)
        self._auto_fetch_toggle = QCheckBox("Auto fetch")
        self._auto_fetch_toggle.setToolTip(
            "Watch cabinet state folders (DeviceManagerData + AFT XML) and reload "
            "meters when they change. On a local EGM this is near-instant via the "
            "filesystem watcher; remote cabinets poll over SMB."
        )
        self._auto_fetch_toggle.toggled.connect(self._on_auto_fetch_toggle)
        # Restore last choice (default on). Arm the watcher in showEvent only —
        # construction must stay side-effect free for headless tests.
        blocked = self._auto_fetch_toggle.blockSignals(True)
        self._auto_fetch_toggle.setChecked(self._load_auto_fetch_pref())
        self._auto_fetch_toggle.blockSignals(blocked)
        row.addWidget(self._auto_fetch_toggle)
        self._dollar_toggle = QCheckBox("Show $")
        self._dollar_toggle.setToolTip(
            "Monetary meters: show money instead of credits (100 credits = 1 unit). "
            "Symbol follows the cabinet's configured currency ($/€/…)."
        )
        blocked = self._dollar_toggle.blockSignals(True)
        self._dollar_toggle.setChecked(self._show_dollars)
        self._dollar_toggle.blockSignals(blocked)
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
        if app is not None and not self._about_to_quit_hooks_installed:
            self._about_to_quit_hooks_installed = True
            app.aboutToQuit.connect(self._on_app_about_to_quit)
        get_logger("gui.sas_verify").info("dialog init ready scan_root=%s", self._scan_root)

    def _on_app_about_to_quit(self) -> None:
        self._stop_compare_thread(wait_ms=2000)
        self._stop_meter_fetch_thread(wait_ms=2000)
        self._stop_onehand_check_thread(wait_ms=500)
        self._stop_psexec_verify_thread(wait_ms=500)

    def _resolve_active_scan_root(self) -> str:
        """Re-run hybrid roulette/slot remapping for the current edit-box text."""
        from network.goldclub_paths import (
            extract_ip_from_path,
            is_game_image_drive_path,
            portable_app_dir,
            resolve_sas_verify_scan_root,
        )

        hint = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not hint:
            return ""
        hint_cf = hint.casefold()
        cached = getattr(self, "_resolved_scan_root_cached", "") or ""
        if hint_cf == getattr(self, "_resolved_scan_root_hint_cf", None) and cached:
            self._scan_root = cached
            return cached
        discovery = resolve_sas_verify_scan_root(
            hint,
            remote_ip=extract_ip_from_path(hint) or self._cabinet_ip_from_scan_root() or None,
            exe_dir=portable_app_dir(),
        )
        resolved = (discovery.scan_root or hint).strip()
        # Never silently rewrite a non-G:\ hint onto the G:\ game-image drive.
        if is_game_image_drive_path(resolved) and not is_game_image_drive_path(hint):
            from gui.app_logging import get_logger

            get_logger("gui.sas_verify").info(
                "refusing silent G:\\ remap hint=%s resolved=%s — keeping hint",
                hint,
                resolved,
            )
            resolved = hint
        # Machine state lives under …\var — a var-level hint reads the same
        # DeviceManager XML, so never grow the user's/startup "…\var" to
        # "…\var\log" (that rewrote the Scan root field right after open).
        from network.goldclub_paths import (
            normalize_path_str,
            prefer_var_root_when_meters_under_state,
        )

        hint_norm = normalize_path_str(hint).rstrip("\\/")
        if normalize_path_str(resolved).casefold() == f"{hint_norm}\\log".casefold():
            resolved = hint_norm
        # Prefer …\var when the field still says …\var\log but meters are under
        # …\var\state (common persisted path after older builds / USB copies).
        resolved = prefer_var_root_when_meters_under_state(resolved)
        prev_kind = getattr(self, "_scan_game_kind", None)
        self._scan_root = resolved
        self._resolved_scan_root_hint_cf = hint_cf
        self._resolved_scan_root_cached = resolved
        # Path wins: …\ruleta always means roulette (ignore stale discovery.slot).
        from network.health_monitor import resolve_game_client_kind

        self._scan_game_kind = resolve_game_client_kind(
            hint=discovery.game_kind,
            scan_root=resolved,
        )
        if self._scan_game_kind != prev_kind or resolved != (
            self._scan_root_edit.text() or ""
        ).strip():
            self._onehand_running = None
            self._onehand_smb_reachable = None
            self._onehand_check_ip = ""
        if self._scan_root_edit.text().strip() != resolved:
            blocked = self._scan_root_edit.blockSignals(True)
            self._scan_root_edit.setText(resolved)
            self._scan_root_edit.blockSignals(blocked)
        return resolved

    def _cabinet_ip_from_scan_root(self) -> str:
        """Fleet-allowlisted cabinet IP — gate for remote WinRM / PsExec probes."""
        from network.health_monitor import is_valid_remote_cabinet_ip

        # Use _scan_root_text(): the View menu is built before _scan_root_edit exists.
        raw = _extract_unc_host(self._scan_root_text())
        return raw if is_valid_remote_cabinet_ip(raw) else ""

    def _resolve_initial_cabinet_ip(self) -> str:
        """Cabinet IP shown at open: scan root, CLI --ip, persisted choice, or default."""
        from config import DEFAULT_REMOTE_IP
        from network.health_monitor import is_valid_remote_cabinet_ip

        for candidate in (
            _extract_unc_host(self._scan_root or ""),
            getattr(self, "_startup_remote_ip", "") or "",
            load_persisted_cabinet_ip(),
            DEFAULT_REMOTE_IP,
        ):
            ip = (candidate or "").strip()
            if ip and is_valid_remote_cabinet_ip(ip):
                return ip
        return DEFAULT_REMOTE_IP

    def _schedule_cabinet_ip_change(self, text: str) -> None:
        """Debounce editable combo churn so rapid host switches do not stack workers."""
        if getattr(self, "_cabinet_ip_changing", False):
            return
        self._pending_cabinet_ip = (text or "").strip()
        self._cabinet_ip_debounce_timer.start()

    def _apply_debounced_cabinet_ip_change(self) -> None:
        text = getattr(self, "_pending_cabinet_ip", "") or ""
        if not text:
            combo = getattr(self, "_cabinet_ip_combo", None)
            if combo is not None:
                text = combo.currentText().strip()
        self._on_cabinet_ip_changed(text)

    def _on_cabinet_ip_changed(self, text: str) -> None:
        if getattr(self, "_cabinet_ip_changing", False):
            return
        ip = (text or "").strip()
        save_persisted_cabinet_ip(ip)
        if not ip or not hasattr(self, "_scan_root_edit"):
            return
        from gui.sas_verify_app import default_startup_scan_root

        try:
            new_root = default_startup_scan_root(ip)
        except ValueError:
            return
        self._cabinet_ip_changing = True
        try:
            blocked = self._scan_root_edit.blockSignals(True)
            self._scan_root_edit.setText(new_root)
            self._scan_root_edit.blockSignals(blocked)
            self._scan_root = new_root
            save_persisted_scan_root(new_root)
        finally:
            self._cabinet_ip_changing = False
        self._on_scan_root_edit_changed()

    def _sync_cabinet_ip_from_scan_root(self, scan_root: str) -> None:
        """Keep Cabinet IP aligned when the operator edits Scan root manually."""
        if getattr(self, "_cabinet_ip_changing", False):
            return
        combo = getattr(self, "_cabinet_ip_combo", None)
        if combo is None:
            return
        from network.health_monitor import is_valid_remote_cabinet_ip

        host = _extract_unc_host(scan_root)
        if not host or not is_valid_remote_cabinet_ip(host):
            return
        if combo.currentText().strip() == host:
            return
        self._cabinet_ip_changing = True
        try:
            blocked = combo.blockSignals(True)
            combo.setCurrentText(host)
            combo.blockSignals(blocked)
            save_persisted_cabinet_ip(host)
        finally:
            self._cabinet_ip_changing = False

    def _abort_inflight_for_scan_target_change(self, *, reason: str = "host_change") -> None:
        """Drop stale compare/COM/game probes when the operator switches cabinets."""
        from gui.app_logging import get_logger

        get_logger("gui.sas_verify").info(
            "abort inflight workers (%s) host=%s scan_root=%s",
            reason,
            self._last_scan_root_unc_host,
            (self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else self._scan_root),
        )
        self._compare_job_id += 1
        self._active_compare_job_id = self._compare_job_id
        self._meter_fetch_job_id += 1
        self._active_meter_fetch_job_id = self._meter_fetch_job_id
        self._local_diff_job_id += 1
        self._compare_job_roots.clear()
        self._cabinet_compare_force_pending = False
        self._compare_starting = False
        self._onehand_running = None
        self._onehand_smb_reachable = None
        self._onehand_check_ip = ""
        self._onehand_check_pending = False
        self._psexec_verify_done = False
        self._psexec_verify_running = False
        self._stop_compare_thread(wait_ms=200)
        self._stop_meter_fetch_thread(wait_ms=200)
        self._stop_onehand_check_thread(wait_ms=100)
        self._stop_psexec_verify_thread(wait_ms=0)

    def _scan_target_host_changed(self, new_root: str) -> bool:
        new_host = _extract_unc_host(new_root)
        old_host = getattr(self, "_last_scan_root_unc_host", "") or ""
        if not new_host and not old_host:
            return False
        return new_host != old_host

    def _note_scan_target_host(self, scan_root: str) -> None:
        self._last_scan_root_unc_host = _extract_unc_host(scan_root)

    def _scan_root_text(self) -> str:
        """Scan root as currently shown, falling back to the value we opened with."""
        raw = ""
        if hasattr(self, "_scan_root_edit"):
            raw = self._scan_root_edit.text() or ""
        return (raw or self._scan_root or "").strip()

    def _scan_root_unc_host(self) -> str:
        """
        UNC host of the scan root, allowlist or not.

        The fleet allowlist decides whether we may *drive* a cabinet remotely; it
        must not decide whether the logs are remote. A UNC path to a hostname or
        to a cabinet outside the lab fleet is still someone else's machine.
        """
        return _unc_host_only(self._scan_root_text())

    def _resolved_game_client_kind(self) -> str:
        from network.health_monitor import resolve_game_client_kind

        return resolve_game_client_kind(
            hint=getattr(self, "_scan_game_kind", None),
            scan_root=(self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else "")
            or self._scan_root
            or "",
        )

    def _game_client_exe_label(self) -> str:
        from network.health_monitor import game_client_exe_name

        kind = self._resolved_game_client_kind()
        return game_client_exe_name(kind)  # type: ignore[arg-type]

    def _onehand_check_uses_local(self) -> bool:
        """
        True when this machine *is* the cabinet under investigation.

        Drives the game-client probe (local vs remote) and, more importantly, the
        ``on_cabinet`` flag for SAS capture: on an EGM the SAS line is the internal
        MUX, on a workstation it is a host cable with a different wire order.
        """
        from network.goldclub_paths import GoldclubLayoutKind, resolve_goldclub_layout
        from network.health_monitor import is_running_on_local_egm, is_this_host

        host = self._scan_root_unc_host()
        if host:
            # UNC to a cabinet: local only when Investigator runs on that EGM.
            return is_this_host(host)

        # Investigator running on the cabinet with a local C:\goldclub install.
        if is_running_on_local_egm():
            return True
        sr = self._scan_root_text()
        if not sr:
            return False
        layout = resolve_goldclub_layout(sr)
        if layout is not None and layout.kind == GoldclubLayoutKind.USB_EXPORT:
            # Logs copied off a cabinet — there is no SAS link on this machine.
            return False
        return bool(layout is not None and layout.kind == GoldclubLayoutKind.LOCAL_CABINET)

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


    def _build_bills_tab(self) -> QWidget:
        """EGM-style BILL IN / BILL OUT panels."""
        bills_in_box = QGroupBox("BILL IN")
        self._bills_table = self._make_bills_table_widget()
        self._bills_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bills_table.customContextMenuRequested.connect(self._on_bills_in_table_context_menu)
        center_table_in_group_box(bills_in_box, self._bills_table)
        bills_out_box = QGroupBox("BILL OUT")
        self._bills_out_table = self._make_bills_table_widget()
        self._bills_out_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bills_out_table.customContextMenuRequested.connect(
            self._on_bills_out_table_context_menu
        )
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
        # Seed denominations so Bills is never a header-only blank panel.
        self._render_bills()
        return bills_tab

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

    def _build_magicwheel_tab(self) -> QWidget:
        """EGM Magic Wheel Data panel (config + DeviceManager perf meters)."""
        from gui.magic_wheel_tab import build_magic_wheel_tab

        body, handles = build_magic_wheel_tab()
        self._magicwheel_handles = handles
        game = handles.get("game")
        if game is not None:
            game.currentTextChanged.connect(self._on_magicwheel_game_changed)
        return body

    @staticmethod
    def _make_bills_table_widget() -> QTableWidget:
        table = QTableWidget(0, _BILLS_TABLE_COLUMN_COUNT)
        table.setHorizontalHeaderLabels(list(_BILLS_TABLE_HEADERS))
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        table.horizontalHeader().setFixedHeight(22)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.setShowGrid(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Fixed geometry — AdjustToContents + stretchLast made COUNT jump/widen.
        table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        table.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        fit_bills_coins_table_columns(table)
        return table

    @staticmethod
    def _make_coins_table_widget() -> QTableWidget:
        table = QTableWidget(0, _COINS_TABLE_COLUMN_COUNT)
        table.setHorizontalHeaderLabels(list(_COINS_TABLE_HEADERS))
        table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        )
        table.horizontalHeader().setFixedHeight(22)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.setShowGrid(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        table.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        fit_bills_coins_table_columns(table)
        return table

    def _apply_meter_panel_styles(self) -> None:
        light = surface_is_light(self.palette())
        table_style = bills_table_stylesheet(light=light)
        cancelled_style = master_cancelled_row_stylesheet(light=light)
        panel_style = master_tab_group_box_stylesheet(light=light)

        def _style_box(name: str, style: str) -> None:
            box = getattr(self, name, None)
            if box is not None:
                box.setStyleSheet(style)

        def _style_table(widget) -> None:
            if widget is not None:
                widget.setStyleSheet(table_style)

        _style_box("_bills_in_box", panel_style)
        _style_box("_bills_out_box", panel_style)
        _style_table(getattr(self, "_bills_table", None))
        _style_table(getattr(self, "_bills_out_table", None))
        for panel_id, table in self._coin_tables.items():
            box = self._coin_boxes.get(panel_id)
            if box is not None:
                box.setStyleSheet(panel_style)
            table.setStyleSheet(table_style)
        _style_box("_master_credit_box", panel_style)
        _style_box("_master_handpay_box", panel_style)
        _style_box("_master_wagered_box", panel_style)
        _style_box("_master_cancelled_box", cancelled_style)
        _style_box("_transfer_ticket_box", panel_style)
        _style_box("_transfer_cashless_box", panel_style)
        _style_box("_security_doors_box", panel_style)
        _style_box("_security_games_box", panel_style)
        _style_box("_game_perf_box", panel_style)
        _style_box("_game_residual_box", panel_style)
        _style_box("_game_chart_box", panel_style)
        if self._accounting_box is not None:
            self._accounting_box.setStyleSheet(panel_style)
        self._table.setStyleSheet(table_style)
        reject = getattr(self, "_bill_reject_label", None)
        if reject is not None:
            reject_font = reject.font()
            reject_font.setPointSize(9)
            reject.setFont(reject_font)

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
        kind = self._resolved_game_client_kind()
        if self._onehand_check_uses_local():
            from network.health_monitor import check_game_client_status_local

            status = check_game_client_status_local(kind=kind)  # type: ignore[arg-type]
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
        self._psexec_verify_done = False
        self._onehand_warning.hide()
        # OneHand is advisory — do not pin the main busy bar on WinRM/WMIC.
        self._stop_onehand_check_thread(wait_ms=200)
        self._onehand_check_thread = QThread(self)
        scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        # Fast path only (WinRM → WMIC). PsExec is a separate gated tier that runs
        # only if this is inconclusive and the SAS COM capture also fails.
        self._onehand_check_worker = OneHandCheckWorker(
            ip, game_kind=kind, scan_root=scan_root, allow_psexec=False
        )
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
        # Clear pending even for a stale IP: leaving it set hid the warning label
        # (and blocked the PsExec tier) until a debounced re-probe completed.
        self._onehand_check_pending = False
        if (ip or "").strip() != self._cabinet_ip_from_scan_root():
            return
        self._onehand_check_ip = (ip or "").strip()
        self._onehand_running = running if isinstance(running, bool) else None
        self._onehand_smb_reachable = smb_reachable if isinstance(smb_reachable, bool) else None
        self._update_onehand_warning_label(self._onehand_check_ip)
        self._set_busy_progress_active()

    def _com_meters_ok(self) -> bool:
        """SAS COM/MUX capture returned data — the host link is proven good."""
        return self._cached_meter_result is not None

    def _com_meters_failed(self) -> bool:
        """SAS COM/MUX capture finished with an error and no data (link down/absent)."""
        return (
            bool(self._meter_fetch_error)
            and self._cached_meter_result is None
            and not self._meter_fetch_running()
        )

    def _update_onehand_warning_label(self, ip: str) -> None:
        from network.health_monitor import onehand_warning_text

        if self._onehand_check_pending:
            self._onehand_warning.hide()
            return
        com_ok = self._com_meters_ok()
        com_failed = self._com_meters_failed()
        # Tier 3: if WinRM/WMIC were inconclusive and the COM/MUX capture failed,
        # try PsExec once before deciding the link is truly unverifiable.
        if (
            not com_ok
            and self._onehand_running is None
            and com_failed
            and not self._psexec_verify_done
        ):
            self._onehand_warning.hide()
            self._maybe_run_psexec_verify()
            return
        text = onehand_warning_text(
            ip,
            running=self._onehand_running,
            smb_reachable=self._onehand_smb_reachable,
            com_meters_ok=com_ok,
            com_meters_failed=com_failed,
            kind=self._resolved_game_client_kind(),  # type: ignore[arg-type]
            scan_root=(self._scan_root_edit.text() or self._scan_root or ""),
        )
        if text:
            self._onehand_warning.setText(text)
            self._onehand_warning.show()
        else:
            self._onehand_warning.hide()

    def _maybe_run_psexec_verify(self) -> None:
        """Start the one-shot PsExec verification tier (silent, last resort)."""
        if self._psexec_verify_running or self._psexec_verify_done:
            return
        if self._onehand_check_pending or self._meter_fetch_running():
            return
        if self._onehand_running is not None or not self._com_meters_failed():
            return
        ip = self._cabinet_ip_from_scan_root()
        if not ip or ip != self._onehand_check_ip:
            return
        kind = self._resolved_game_client_kind()
        scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        self._psexec_verify_running = True
        self._stop_psexec_verify_thread(wait_ms=0)
        self._psexec_verify_thread = QThread(self)
        self._psexec_verify_worker = OneHandCheckWorker(
            ip, game_kind=kind, scan_root=scan_root, psexec_only=True
        )
        self._psexec_verify_worker.moveToThread(self._psexec_verify_thread)
        self._psexec_verify_thread.started.connect(self._psexec_verify_worker.run)
        self._psexec_verify_worker.finished.connect(
            self._on_psexec_verify_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._psexec_verify_worker.finished.connect(self._psexec_verify_thread.quit)
        self._psexec_verify_worker.finished.connect(self._psexec_verify_worker.deleteLater)
        self._psexec_verify_thread.finished.connect(self._on_psexec_verify_thread_finished)
        self._psexec_verify_thread.finished.connect(self._psexec_verify_thread.deleteLater)
        self._psexec_verify_thread.start()

    def _on_psexec_verify_finished(self, ip: str, running: object, smb_reachable: object) -> None:
        self._psexec_verify_running = False
        self._psexec_verify_done = True
        if not self._worker_signals_enabled():
            return
        if (ip or "").strip() != self._cabinet_ip_from_scan_root():
            return
        if isinstance(running, bool):
            self._onehand_running = running
        self._update_onehand_warning_label(self._onehand_check_ip or ip)

    def _on_psexec_verify_thread_finished(self) -> None:
        # Only clear refs for *this* thread. An orphaned finished must not wipe
        # a newer PsExec verify that is still in flight.
        sender = self.sender()
        if sender is not None and sender is not self._psexec_verify_thread:
            return
        self._psexec_verify_thread = None
        self._psexec_verify_worker = None

    def _stop_psexec_verify_thread(self, *, wait_ms: int = 0) -> None:
        self._quit_or_orphan_thread(self._psexec_verify_thread, wait_ms)
        self._psexec_verify_thread = None
        self._psexec_verify_worker = None

    def _load_com_port_prefs(self) -> None:
        """Load COM port name preference only — never restore a prior capture profile.

        Wire/baud/RTS from a previous session made meters look \"remembered\" on
        open (same combo, same values). Each launch rediscovers the link fresh;
        a successful capture in *this* session may still cache the profile in
        memory for faster retries until the window closes.
        """
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD, DEFAULT_SAS_COM_PORT

        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            try:
                port = s.value(_KEY_COM_PORT, DEFAULT_SAS_COM_PORT, type=str)
            except (TypeError, ValueError):
                port = DEFAULT_SAS_COM_PORT
            self._preferred_com_port = (port or DEFAULT_SAS_COM_PORT).strip()
            try:
                baud = int(s.value(_KEY_COM_BAUD, DEFAULT_SAS_COM_BAUD, type=int))
            except (TypeError, ValueError):
                baud = int(DEFAULT_SAS_COM_BAUD)
            self._com_baud = baud if baud > 0 else int(DEFAULT_SAS_COM_BAUD)
            # Drop any leftover session profile keys from older builds.
            s.remove(_KEY_COM_WIRE)
            s.remove(_KEY_COM_RTS)
            self._cached_serial_profile = None
        finally:
            s.endGroup()
            s.sync()

    def _current_com_port(self) -> str:
        """Last known / auto-picked SAS port (used for status text and prefs)."""
        from network.sas_serial_meters import normalize_com_port

        return normalize_com_port(getattr(self, "_preferred_com_port", ""))

    def _refresh_com_port_list(self, *, preserve_text: bool = True, background: bool = False) -> None:
        """Auto-detect the best SAS serial port (no manual picker UI)."""
        if background:
            self._schedule_com_port_refresh_background()
            return
        from network.sas_serial_meters import (
            enumerate_serial_ports,
            normalize_com_port,
            pick_sas_com_port,
        )

        # Same game_kind/on_cabinet scoring as the fetch path — without it a
        # saved host COM4 preference could shadow the roulette MUX (COM5) on an EGM.
        picked = pick_sas_com_port(
            getattr(self, "_preferred_com_port", ""),
            enumerate_serial_ports(),
            game_kind=self._resolved_game_client_kind(),
            on_cabinet=self._onehand_check_uses_local(),
        )
        if picked:
            self._preferred_com_port = normalize_com_port(picked)

    def _schedule_com_port_refresh_background(self) -> None:
        """Run list_ports off the UI thread — USB/Bluetooth enum can hang 30+ s."""
        if getattr(self, "_com_port_refresh_running", False):
            return
        self._com_port_refresh_running = True
        game_kind = getattr(self, "_scan_game_kind", None) or "slot"
        try:
            on_cabinet = self._onehand_check_uses_local()
        except Exception:  # noqa: BLE001
            on_cabinet = False
        self._pool.start(
            _ComPortRefreshTask(
                preferred=getattr(self, "_preferred_com_port", ""),
                game_kind=game_kind,
                on_cabinet=on_cabinet,
                signals=self._com_port_refresh_signals,
            )
        )

    def _on_com_port_refreshed(self, port: str) -> None:
        self._com_port_refresh_running = False
        if not self._worker_signals_enabled():
            return
        from network.sas_serial_meters import normalize_com_port

        port_name = normalize_com_port(port)
        if port_name:
            self._preferred_com_port = port_name

    def _schedule_cabinet_reachability_probe(self, host: str) -> None:
        h = (host or "").strip()
        if not h:
            return
        self._pool.start(_CabinetReachabilityTask(h, self._cabinet_reach_signals))

    def _on_cabinet_reachability_probed(self, host: str, alive: bool) -> None:
        if not self._worker_signals_enabled():
            return
        if host != self._scan_root_unc_host():
            return
        self._cabinet_share_unreachable = not alive
        if not alive:
            self._update_prefetch_status(cabinet_unreachable_status(host))

    def _set_com_port_selection(self, port: str) -> None:
        from network.sas_serial_meters import normalize_com_port

        port_name = normalize_com_port(port)
        if port_name:
            self._preferred_com_port = port_name

    def _save_com_port_prefs(self) -> None:
        """Persist COM port name only — never the wire/RTS capture profile."""
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD

        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            port = self._current_com_port()
            if port:
                s.setValue(_KEY_COM_PORT, port)
            s.setValue(_KEY_COM_BAUD, int(getattr(self, "_com_baud", DEFAULT_SAS_COM_BAUD)))
            # Explicitly clear so older builds cannot reload a stale profile.
            s.remove(_KEY_COM_WIRE)
            s.remove(_KEY_COM_RTS)
        finally:
            s.endGroup()
            s.sync()

    def _build_view_menu_bar(self) -> QMenuBar:
        bar = QMenuBar(self)
        view_menu = bar.addMenu("&View")
        self._always_on_top_action = QAction("Always on top", self)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.toggled.connect(self._on_always_on_top_toggled)
        view_menu.addAction(self._always_on_top_action)
        self._move_monitor2_action = QAction("Move to Monitor 2", self)
        self._move_monitor2_action.setCheckable(True)
        self._move_monitor2_action.setToolTip(
            "Fill the second monitor with the meters window so the game stays "
            f"clear on Monitor 1. Toggle from a non-touch monitor with "
            f"{MOVE_HOTKEY_LABEL}; close with {KILL_HOTKEY_LABEL}."
        )
        self._move_monitor2_action.toggled.connect(self._on_move_monitor2_toggled)
        view_menu.addAction(self._move_monitor2_action)
        view_menu.addSeparator()
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
        tools_menu = bar.addMenu("&Tools")
        self._act_ram_clear = QAction("RAM Clear…", self)
        self._act_ram_clear.setToolTip(
            "Stop bootloader + game + GoldClub services, wipe official state, stamp "
            "LogDaemonRamClear (SAS soft meters / 0x7A), then restart. "
            "Same logic as Log Investigator — slot and roulette, no reboot."
        )
        self._act_ram_clear.triggered.connect(self._on_ram_clear_clicked)
        tools_menu.addAction(self._act_ram_clear)
        help_menu = bar.addMenu("&Help")
        help_act = QAction("Setup && troubleshooting…", self)
        help_act.triggered.connect(self._show_help_dialog)
        help_menu.addAction(help_act)
        self._update_ram_clear_button_enabled()
        return bar


    def _ram_clear_target_ip(self) -> str:
        """Cabinet IP for remote RAM Clear (scan-root UNC / startup --ip)."""
        from network.goldclub_paths import extract_ip_from_path
        from network.health_monitor import is_valid_remote_cabinet_ip

        for cand in (
            self._cabinet_ip_from_scan_root(),
            extract_ip_from_path(self._scan_root_text()),
            getattr(self, "_startup_remote_ip", "") or "",
            self._scan_root_unc_host(),
        ):
            tip = (cand or "").strip()
            if tip and is_valid_remote_cabinet_ip(tip):
                return tip
        tip = (self._scan_root_unc_host() or "").strip()
        return tip

    def _ram_clear_allowed_on_local_egm(self) -> bool:
        """True only on a real local EGM cabinet — not USB export or arbitrary folders."""
        from network.goldclub_paths import GoldclubLayoutKind, resolve_goldclub_layout
        from network.health_monitor import is_running_on_local_egm

        if is_running_on_local_egm():
            return True
        if self._onehand_check_uses_local():
            return True
        sr = self._scan_root_text()
        if not sr or sr.startswith("\\\\"):
            return False
        if self._offline_log_scan():
            return False
        layout = resolve_goldclub_layout(sr)
        return bool(layout is not None and layout.kind == GoldclubLayoutKind.LOCAL_CABINET)

    def _update_ram_clear_button_enabled(self) -> None:
        import os

        if not hasattr(self, "_act_ram_clear"):
            return
        act = self._act_ram_clear
        if getattr(self, "_ram_clear_busy", False):
            act.setEnabled(False)
            return
        if os.name != "nt":
            act.setEnabled(False)
            act.setStatusTip("RAM Clear is only available on Windows.")
            return
        tip = self._ram_clear_target_ip()
        if tip:
            act.setEnabled(True)
            act.setStatusTip(
                "Run the maintenance RAM-clear chain (soft meters / SAS 0x7A)."
            )
            return
        if self._ram_clear_allowed_on_local_egm():
            act.setEnabled(True)
            act.setStatusTip(
                "Run the maintenance RAM-clear chain on this EGM (soft meters / SAS 0x7A)."
            )
            return
        act.setEnabled(False)
        act.setStatusTip(
            "Enter a cabinet Scan root UNC (or --ip) to run RAM Clear remotely, "
            "or run SAS Verify on the EGM itself."
        )

    def _on_ram_clear_clicked(self) -> None:
        import os

        from PySide6.QtWidgets import QApplication, QMessageBox

        if getattr(self, "_ram_clear_busy", False):
            return
        if os.name != "nt":
            QMessageBox.information(
                self,
                "RAM Clear",
                "RAM Clear is only supported on Windows.",
            )
            return

        from gui.ram_clear_ui import ram_clear_confirm_text, resolve_ram_clear_run
        from gui.ram_clear_worker import schedule_ram_clear

        tip = self._ram_clear_target_ip()
        if not tip and not self._ram_clear_allowed_on_local_egm():
            QMessageBox.information(
                self,
                "RAM Clear",
                "Set Scan root to a cabinet UNC (\\ip\\c$\\Goldclub\\…) "
                "or launch with --ip first.\n\n"
                "Local RAM Clear is only available when SAS Verify runs on the EGM itself "
                "(not USB export or an arbitrary local folder).",
            )
            return
        remote, ip, plan, label = resolve_ram_clear_run(
            ui_remote=bool(tip),
            target_ip=tip,
        )
        if remote and not ip:
            QMessageBox.information(
                self,
                "RAM Clear",
                "Set Scan root to a cabinet UNC (\\ip\\c$\\Goldclub\\…) "
                "or launch with --ip first.",
            )
            return
        if not remote and not self._ram_clear_allowed_on_local_egm():
            QMessageBox.information(
                self,
                "RAM Clear",
                "Local RAM Clear is only available on a real EGM cabinet "
                "(LOCAL_CABINET layout or Investigator running on the cabinet).",
            )
            return

        confirm_text = ram_clear_confirm_text(
            remote=remote,
            cabinet_label=label if remote else "this EGM",
            plan=plan,
        )
        reply = QMessageBox.warning(
            self,
            "Confirm RAM Clear",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Pause Auto fetch so meter reload does not fight the service restart.
        self._ram_clear_saved_auto_fetch = None
        if hasattr(self, "_auto_fetch_toggle") and self._auto_fetch_toggle.isChecked():
            self._ram_clear_saved_auto_fetch = True
            self._auto_fetch_toggle.setChecked(False)

        self._ram_clear_busy = True
        self._ram_clear_target_label = label
        self._update_ram_clear_button_enabled()
        # Drop pre-clear MATCH rows immediately — do not leave old meters on screen.
        self._blank_meter_ui_for_ram_clear(
            status=f"RAM Clear running ({label}) — meters cleared; waiting for finish…"
        )
        QApplication.processEvents()
        schedule_ram_clear(
            self._pool,
            local=not remote,
            ip=ip if remote else None,
            plan=plan,
            cabinet_label=label if remote else None,
            emitter=self._ram_clear_emitter,
        )

    def _blank_meter_ui_for_ram_clear(self, *, status: str) -> None:
        """Clear SAS + Machine columns so pre-clear values cannot linger."""
        self._discard_stale_machine_after_ram_clear = True
        self._machine_empty_retry_armed = False
        self._machine_empty_retry_count = 0
        self._cabinet_compare_force_pending = False
        self._pending_forced_fetch = None
        self._compare_ui_pending = False
        self._meters_ui_pending = False
        self._manual_meters_refresh = False
        self._local_diff_running = False
        self._auto_fetch_round_active = False
        self._auto_fetch_round_resynced = False
        self._auto_fetch_round_started_mono = 0.0
        # Invalidate in-flight Machine / local-diff / meter-fetch applies.
        self._compare_job_id += 1
        self._active_compare_job_id = self._compare_job_id
        self._local_diff_job_id += 1
        self._meter_fetch_job_id += 1
        self._active_meter_fetch_job_id = self._meter_fetch_job_id
        self._local_sas_state = {}
        self._had_settled_mismatch = False
        try:
            self._stop_meter_fetch_thread(wait_ms=200)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._invalidate_machine_cabinet_cache(wipe=True)
        except Exception:  # noqa: BLE001
            self._machine_state = {}
            self._machine_state_loaded = False
            self._machine_state_loaded_at = 0.0
            self._loaded_cabinet_scan_root = ""
        self._cached_meter_result = None
        self._meter_fetch_error = None
        self._meter_fetch_user_clicked_apply = False
        self._sas_2f_values = {}
        self._last_bill_in_rows = []
        self._last_bill_out_rows = []
        self._last_displayed_paste_fingerprint = ""
        from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

        self._last_parsed_rows = [
            Sas6FRow(meter_id=code, sas_value_text="")
            for code in DEFAULT_6F_VERIFY_POLL_CODES
        ]
        if hasattr(self, "ui") and getattr(self.ui, "compare_btn", None) is not None:
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
        if hasattr(self, "_btn_get_meters"):
            self._btn_get_meters.setEnabled(True)
            self._btn_get_meters.setText("Refresh Meters")
        self._set_busy_progress_active(False)
        try:
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._render_bills(machine_state=None)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._render_coins(machine_state=None, allow_machine_lookup=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._reset_game_summary()
            self._reset_master_summary()
            self._reset_transfer_summary()
            self._reset_security_summary()
        except Exception:  # noqa: BLE001
            pass
        self._set_prefetch_status_text(status)
        self._fetched_status_label.setText("Meters cleared (RAM Clear)")

    def _on_ram_clear_progress(self, message: str) -> None:
        if not self._worker_signals_enabled():
            return
        text = (message or "").strip() or "RAM Clear running…"
        label = getattr(self, "_ram_clear_target_label", "") or ""
        if label and label not in text and not text.startswith(label):
            text = f"{label}: {text}"
        if len(text) > 140:
            text = text[:137] + "…"
        # Keep the table blank while the clear runs; only update the status line.
        self._set_prefetch_status_text(text)

    def _schedule_post_ram_clear_meter_refresh(self) -> None:
        """Force Machine + SAS meter reload after RAM Clear (Auto fetch is often off)."""
        from PySide6.QtCore import QTimer

        self._blank_meter_ui_for_ram_clear(
            status="RAM Clear finished — refreshing meters from cabinet…"
        )
        # Kick Refresh Meters after the modal returns control to the event loop.
        QTimer.singleShot(0, self._on_get_meters_clicked)

    def _on_ram_clear_finished(self, ok: bool, msg: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from gui.ram_clear_ui import format_ram_clear_finished_dialog

        if not self._worker_signals_enabled():
            return
        self._ram_clear_busy = False
        self._update_ram_clear_button_enabled()
        label = getattr(self, "_ram_clear_target_label", "") or ""
        display_ok, body = format_ram_clear_finished_dialog(
            ok=ok, msg=msg, label=label
        )
        # Blank again (in case a late worker painted), then refresh.
        if display_ok:
            self._schedule_post_ram_clear_meter_refresh()
        else:
            self._blank_meter_ui_for_ram_clear(
                status=f"RAM Clear failed ({label}) — meters left cleared"
            )
        if display_ok:
            QMessageBox.information(self, "RAM Clear", body)
        else:
            QMessageBox.critical(self, "RAM Clear", body)
        # Restore Auto fetch if we paused it (stale-snapshot keep stays off until
        # a fresh Machine load lands).
        if getattr(self, "_ram_clear_saved_auto_fetch", None):
            self._ram_clear_saved_auto_fetch = None
            if hasattr(self, "_auto_fetch_toggle"):
                self._auto_fetch_toggle.setChecked(True)
        self._update_prefetch_status()

    def _show_help_dialog(self) -> None:

        from PySide6.QtWidgets import QTextBrowser

        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("SAS Verify Meters — Help")
            dlg.resize(640, 560)
            lay = QVBoxLayout(dlg)
            browser = QTextBrowser(dlg)
            browser.setOpenExternalLinks(True)
            browser.setHtml(SAS_VERIFY_HELP_HTML)
            lay.addWidget(browser)
            btn = QPushButton("Close", dlg)
            btn.clicked.connect(dlg.hide)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(btn)
            lay.addLayout(row)
            self._help_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _read_always_on_top_pref(self) -> bool:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            return bool(s.value(_KEY_ALWAYS_ON_TOP, True, type=bool))
        finally:
            s.endGroup()

    def _save_always_on_top_pref(self, on: bool) -> None:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            s.setValue(_KEY_ALWAYS_ON_TOP, bool(on))
        finally:
            s.endGroup()
        s.sync()

    def _apply_always_on_top(self, on: bool) -> None:
        """Move the window in/out of the topmost band; Qt hint is the fallback.

        ``set_window_always_on_top`` only changes the native z-order band and
        keeps focus/theming intact. When it is unavailable (non-Windows, or no
        HWND yet) ``Qt.WindowStaysOnTopHint`` still works, at the cost of
        recreating the native window.
        """
        if set_window_always_on_top(self, on):
            return
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _reapply_always_on_top(self) -> None:
        """Re-assert the topmost band after a show()/restyle recreates the HWND.

        Never switches the band on when the user has it off.
        """
        if self._always_on_top:
            self._apply_always_on_top(True)

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._always_on_top = bool(checked)
        self._apply_always_on_top(self._always_on_top)
        self._save_always_on_top_pref(self._always_on_top)

    def _read_on_secondary_pref(self) -> bool:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            return bool(s.value(_KEY_ON_SECONDARY_MONITOR, False, type=bool))
        finally:
            s.endGroup()

    def _save_on_secondary_pref(self, on: bool) -> None:
        s = QSettings()
        s.beginGroup(_SAS_VERIFY_SETTINGS_GROUP)
        try:
            s.setValue(_KEY_ON_SECONDARY_MONITOR, bool(on))
        finally:
            s.endGroup()
        s.sync()

    def _secondary_screen(self):
        """The first non-primary QScreen, or None when only one monitor exists."""
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        primary_index = screens.index(primary) if primary in screens else 0
        idx = pick_secondary_screen_index(len(screens), primary_index)
        return screens[idx] if idx >= 0 else None

    def _on_move_monitor2_toggled(self, checked: bool) -> None:
        if checked and self._secondary_screen() is None:
            # No second monitor — undo the check and tell the user.
            self._set_move_action_checked(False)
            self._on_secondary_monitor = False
            self._save_on_secondary_pref(False)
            QMessageBox.information(
                self,
                "Move to Monitor 2",
                "Only one monitor is detected. Connect a second display, then try again.",
            )
            return
        self._apply_monitor_placement(bool(checked))

    def _set_move_action_checked(self, checked: bool) -> None:
        """Set the menu check without re-entering the move handler."""
        blocked = self._move_monitor2_action.blockSignals(True)
        self._move_monitor2_action.setChecked(bool(checked))
        self._move_monitor2_action.blockSignals(blocked)

    def _apply_monitor_placement(self, on_secondary: bool) -> None:
        """Put the window on Monitor 2 or bring it back, and remember which."""
        if on_secondary:
            screen = self._secondary_screen()
            if screen is None:
                return
            self._move_to_screen(screen)
        else:
            self._restore_from_secondary()
        self._on_secondary_monitor = bool(on_secondary)
        self._save_on_secondary_pref(self._on_secondary_monitor)

    def _window_is_on_screen(self, screen) -> bool:
        """True when the window's centre currently lies on *screen*."""
        if screen is None:
            return False
        try:
            return screen.geometry().contains(self.frameGeometry().center())
        except (AttributeError, RuntimeError):
            return False

    def _frame_margins(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) thickness of the native window frame."""
        try:
            frame = self.frameGeometry()
            client = self.geometry()
            return (
                max(0, client.x() - frame.x()),
                max(0, client.y() - frame.y()),
                max(0, frame.right() - client.right()),
                max(0, frame.bottom() - client.bottom()),
            )
        except (AttributeError, RuntimeError):
            return (0, 0, 0, 0)

    def _move_to_screen(self, screen) -> None:
        """Fill the given monitor with the window; remember where it was."""
        # Only worth remembering a spot we would actually want to come back to.
        # A session closed on Monitor 2 reopens there, so saving that geometry
        # would make the return trip restore Monitor 2 onto itself — the toggle
        # would look dead.
        if self._pre_move_geometry is None and not self._window_is_on_screen(screen):
            self._pre_move_geometry = self.saveGeometry()
        if self.isMaximized():
            self.showNormal()
        # Geometry alone decides the monitor on Windows. QWindow.setScreen() is
        # not used: on some drivers it recreates the native window, which drops
        # the topmost band and the registered hotkeys with it.
        self.setGeometry(
            fitted_client_rect(screen.availableGeometry(), self._frame_margins())
        )
        # setGeometry can drop the topmost band on some drivers — re-assert it.
        self._reapply_always_on_top()
        self.raise_()

    def _restore_from_secondary(self) -> None:
        """Bring the window back onto the primary monitor."""
        saved = self._pre_move_geometry
        self._pre_move_geometry = None
        if self.isMaximized():
            self.showNormal()
        if saved is not None:
            self.restoreGeometry(saved)
        primary = QGuiApplication.primaryScreen()
        # Guarantee the window actually left Monitor 2, whatever the saved
        # geometry said — otherwise the hotkey silently does nothing.
        if primary is not None and not self._window_is_on_screen(primary):
            self.setGeometry(
                fitted_client_rect(
                    primary.availableGeometry(), self._frame_margins(), self.size()
                )
            )
        self._reapply_always_on_top()
        self.raise_()

    def _apply_startup_monitor_placement(self) -> None:
        """Honour the saved "Move to Monitor 2" choice on first show."""
        screen = self._secondary_screen()
        if screen is None:
            self._set_move_action_checked(False)
            self._on_secondary_monitor = False
            return
        if self._move_monitor2_action.isChecked():
            self._move_to_screen(screen)
            self._on_secondary_monitor = True
            return
        # The restored session geometry can land on Monitor 2 with the setting
        # off. Believe the window, not the setting, or the first hotkey press
        # "moves" it to the monitor it is already on and only resizes it.
        self._on_secondary_monitor = self._window_is_on_screen(screen)
        self._set_move_action_checked(self._on_secondary_monitor)

    def _install_global_hotkeys(self) -> None:
        """Register the system-wide close + move hotkeys (Windows)."""
        if self._global_hotkeys_installed:
            return
        mgr = GlobalHotkeyManager(self)
        mgr.add(
            hotkey_id=KILL_HOTKEY_ID,
            mods=KILL_HOTKEY_MODS,
            vk=KILL_HOTKEY_VK,
            on_fired=self._on_kill_hotkey,
        )
        mgr.add(
            hotkey_id=MOVE_HOTKEY_ID,
            mods=MOVE_HOTKEY_MODS,
            vk=MOVE_HOTKEY_VK,
            on_fired=self._on_toggle_monitor_hotkey,
        )
        mgr.add(
            hotkey_id=TAB_HOTKEY_ID,
            mods=TAB_HOTKEY_MODS,
            vk=TAB_HOTKEY_VK,
            on_fired=self._on_tab_hotkey,
        )
        self._global_hotkeys = mgr
        self._global_hotkeys_installed = mgr.install()

    def _remove_global_hotkeys(self) -> None:
        if self._global_hotkeys is not None:
            self._global_hotkeys.remove()
            self._global_hotkeys = None
        self._global_hotkeys_installed = False

    def _install_tab_shortcuts(self) -> None:
        """Keyboard navigation across the meter tabs.

        Application-scoped so they work wherever focus sits inside the window —
        a table, the paste box or the scan-root field — rather than only on the
        tab bar itself.
        """
        for seq, delta in (
            ("Ctrl+Tab", 1),
            ("Ctrl+PgDown", 1),
            ("Ctrl+Shift+Tab", -1),
            ("Ctrl+PgUp", -1),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(partial(self._step_meter_tab, delta))
        for index in range(len(_METER_TAB_NAMES)):
            sc = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(partial(self._goto_meter_tab, index))

    def _meter_tab_is_visible(self, index: int) -> bool:
        tabs = self._meter_tabs
        idx = int(index)
        if idx < 0 or idx >= tabs.count():
            return False
        if hasattr(tabs, "isTabVisible"):
            return bool(tabs.isTabVisible(idx))
        return bool(tabs.isTabEnabled(idx))

    def _sync_magicwheel_tab_visibility(self) -> None:
        """Show MagicWheel only when magicwheel_Config.xml is set up for this scan root."""
        tabs = getattr(self, "_meter_tabs", None)
        if tabs is None or TAB_MAGICWHEEL >= tabs.count():
            return
        from network.magic_wheel_loader import is_magic_wheel_config_setup

        sr = ""
        if hasattr(self, "_scan_root_edit"):
            sr = (self._scan_root_edit.text() or "").strip()
        if not sr:
            sr = (getattr(self, "_scan_root", None) or "").strip()
        # Constructor / pre-paint: never block on the first SMB to a cabinet share.
        if _scan_root_is_unc(sr) and (
            not self._prefetch_started or self._compare_running()
        ):
            show = False
        elif _scan_root_is_unc(sr):
            from network.goldclub_paths import _unc_host_answering

            show = bool(sr) and _unc_host_answering(sr) and is_magic_wheel_config_setup(sr)
        else:
            show = bool(sr) and is_magic_wheel_config_setup(sr)
        if hasattr(tabs, "setTabVisible"):
            tabs.setTabVisible(TAB_MAGICWHEEL, show)
        else:
            tabs.setTabEnabled(TAB_MAGICWHEEL, show)
        if not show and tabs.currentIndex() == TAB_MAGICWHEEL:
            for candidate in range(TAB_MAGICWHEEL - 1, -1, -1):
                if self._meter_tab_is_visible(candidate):
                    tabs.setCurrentIndex(candidate)
                    break

    def _step_meter_tab(self, delta: int) -> None:
        """Move *delta* tabs along, wrapping at either end (skip hidden tabs)."""
        count = self._meter_tabs.count()
        if count <= 0:
            return
        step = 1 if int(delta) >= 0 else -1
        cur = self._meter_tabs.currentIndex()
        for _ in range(count):
            cur = (cur + step) % count
            if self._meter_tab_is_visible(cur):
                self._meter_tabs.setCurrentIndex(cur)
                return

    def _goto_meter_tab(self, index: int) -> None:
        idx = int(index)
        if self._meter_tab_is_visible(idx):
            self._meter_tabs.setCurrentIndex(idx)

    def _on_tab_hotkey(self) -> None:
        """Global combo: step to the next tab without focusing the window."""
        from gui.app_logging import get_logger

        self._step_meter_tab(1)
        get_logger("gui.sas_verify").info(
            "tab hotkey pressed — now on %s",
            self._meter_tabs.tabText(self._meter_tabs.currentIndex()),
        )

    def _on_kill_hotkey(self) -> None:
        from gui.app_logging import get_logger

        if getattr(self, "_ram_clear_busy", False):
            get_logger("gui.sas_verify").info(
                "kill hotkey pressed — ignored (RAM Clear in progress)"
            )
            return
        get_logger("gui.sas_verify").info("kill hotkey pressed — closing")
        self.close()

    def _on_toggle_monitor_hotkey(self) -> None:
        """Send the window to the monitor it is *not* on."""
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify")
        secondary = self._secondary_screen()
        if secondary is None:
            log.info("move-monitor hotkey pressed — only one monitor")
            self._move_monitor2_action.setChecked(True)  # surfaces the explanation
            return
        # Decide from where the window physically is. Toggling the action's
        # checked state instead trusted a flag that drifts out of step with the
        # window (restored session geometry, a drag onto the other screen), and
        # then a press re-placed it on the monitor it was already on.
        want_secondary = not self._window_is_on_screen(secondary)
        log.info(
            "move-monitor hotkey pressed — to %s",
            "monitor 2" if want_secondary else "primary",
        )
        self._set_move_action_checked(want_secondary)
        self._apply_monitor_placement(want_secondary)

    def _window_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(self._always_on_top_action)
        menu.addAction(self._move_monitor2_action)
        return menu

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self._window_context_menu().exec(event.globalPos())

    def _on_meter_tab_changed(self, index: int) -> None:
        self._sync_columns_menus_for_tab(index)
        self._save_meter_tab_pref()
        if index == TAB_GAME:
            # Per-game filters need fresh themeId → paytable meters (roulette + slots).
            self._schedule_game_theme_catalog_reload(force=True)

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
        if not checked:
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
    def _quit_or_orphan_thread(th: QThread | None, wait_ms: int) -> bool:
        """Ask a worker thread to quit; if it is stuck in blocking network I/O,
        detach it from the dialog so its destructor never fires while running
        ("QThread: Destroyed while thread is still running"). The thread's
        finished→deleteLater connection cleans it up once the call returns.

        Returns ``True`` when the thread actually stopped, ``False`` when it was
        orphaned and is still holding whatever resource it had open.
        """
        if th is None:
            return True
        try:
            if not th.isRunning():
                return True
            th.quit()
            if th.wait(wait_ms):
                return True
            th.setParent(None)
            return False
        except Exception:
            return False

    def _stop_compare_thread(self, wait_ms: int = 300) -> bool:
        """Stop or orphan the Machine-load thread and drop dialog refs.

        Clearing refs is required: an orphaned QThread that is still referenced
        keeps ``_compare_running()`` True forever, which blocks Auto fetch and
        makes Refresh/Compare look dead while the UI stays on
        "Loading Machine meters…".
        """
        stopped = self._quit_or_orphan_thread(self._compare_thread, wait_ms)
        self._compare_thread = None
        self._compare_worker = None
        self._compare_started_mono = 0.0
        return stopped

    def _stop_meter_fetch_thread(self, wait_ms: int = 300) -> bool:
        stopped = self._quit_or_orphan_thread(self._meter_fetch_thread, wait_ms)
        self._meter_fetch_thread = None
        self._meter_fetch_worker = None
        self._meter_fetch_started_mono = 0.0
        return stopped

    def _orphan_stale_meter_fetch_if_needed(self) -> None:
        """Drop a wedged COM capture so PENDING rows and the busy bar can recover."""
        if not self._meter_fetch_running():
            return
        started = float(getattr(self, "_meter_fetch_started_mono", 0.0) or 0.0)
        if not started or (time.monotonic() - started) < METER_FETCH_STALE_S:
            return
        from gui.app_logging import get_logger

        get_logger("gui.sas_verify").warning(
            "meter fetch stale after %.0fs — orphaning capture",
            METER_FETCH_STALE_S,
        )
        self._meter_fetch_job_id += 1
        self._active_meter_fetch_job_id = self._meter_fetch_job_id
        self._stop_meter_fetch_thread(wait_ms=200)
        self._meters_ui_pending = False
        self._manual_meters_refresh = False
        self._btn_get_meters.setEnabled(True)
        self._btn_get_meters.setText("Refresh Meters")
        self._set_busy_progress_active()
        self._update_prefetch_status()

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

    def _set_prefetch_status_text(self, text: str) -> None:
        """One thin, non-wrapping status row; the full text is on the tooltip.

        A wrapped multi-line status used to push the busy strip up and down as
        the message changed length; a fixed-height single line (Qt clips
        overflow at the widget edge, no eliding needed) keeps
        ``_prefetch_status_label`` a constant height across every state.
        """
        full = str(text or "")
        self._prefetch_status_full = full
        self._prefetch_status_label.setText(full)
        self._prefetch_status_label.setToolTip(full)

    def _busy_progress_should_run(self) -> bool:
        """True while compare / COM / LocalDiff is in flight.

        The thin busy bar is gone; this remains so finish handlers know when
        to refresh the status line (not while another worker is still running).
        """
        return busy_progress_should_run(
            meters_ui_pending=self._meters_ui_pending,
            compare_ui_pending=self._compare_ui_pending,
            compare_running=self._compare_running(),
            meter_fetch_running=self._meter_fetch_running(),
            local_diff_running=self._local_diff_running,
        )

    def _onehand_check_running(self) -> bool:
        th = self._onehand_check_thread
        return th is not None and th.isRunning()

    def _set_busy_progress_active(self, active: bool | None = None) -> None:
        """Obsolete no-op — busy progress bar removed from the UI."""
        del active

    def _apply_busy_progress_idle(self) -> None:
        """Obsolete no-op — busy progress bar removed from the UI."""

    def _suppress_busy_progress(self) -> None:
        """Obsolete no-op — busy progress bar removed from the UI."""
        self._busy_progress_suppressed = True

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if getattr(self, "_ram_clear_busy", False):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "RAM Clear in progress",
                "RAM Clear is still running. Wait for it to finish before closing.",
            )
            event.ignore()
            return
        self._remove_global_hotkeys()
        self._save_session_prefs()
        SettingsManager.save_sas_verify_dialog_geometry(self)
        self._accept_worker_signals = False
        self._cabinet_ip_debounce_timer.stop()
        self._auto_fetch_timer.stop()
        if self._auto_fetch_queue_timer.isActive():
            self._auto_fetch_queue_timer.stop()
        if self._settle_repaint_timer.isActive():
            self._settle_repaint_timer.stop()
        if self._meter_flash_timer.isActive():
            self._meter_flash_timer.stop()
        self._clear_auto_fetch_file_watcher()
        self._recovery_timer.stop()
        self._pending_forced_fetch = None
        self._stop_compare_thread(wait_ms=500)
        self._stop_meter_fetch_thread(wait_ms=500)
        self._stop_onehand_check_thread(wait_ms=500)
        self._stop_psexec_verify_thread(wait_ms=500)
        # Drop every in-session meter result so the next launch cannot reuse them.
        self._cached_meter_result = None
        self._cached_serial_profile = None
        self._com_link_warm = False
        self._last_parsed_rows = []
        self._last_bill_in_rows = []
        self._last_bill_out_rows = []
        self._sas_2f_values = {}
        self._machine_state = {}
        self._machine_state_loaded = False
        self._meter_fetch_error = None
        self._paste.clear()
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
        if not self._split_meter_dominant_applied:
            self._split_meter_dominant_applied = True
            split_h = max(self._content_split.height(), 480)
            paste_h = min(90, max(40, int(split_h * 0.07)))
            self._content_split.setSizes([paste_h, split_h - paste_h])
        # First paint ASAP — UNC theme catalog + serial enum are deferred.
        QTimer.singleShot(0, self._kickoff_after_first_paint)
        QTimer.singleShot(400, self._deferred_startup_warm)
        # Register the system-wide close + move hotkeys and honour the saved
        # monitor choice once the native window and screen list exist.
        self._install_global_hotkeys()
        if not self._startup_monitor_applied:
            self._startup_monitor_applied = True
            QTimer.singleShot(0, self._apply_startup_monitor_placement)

    def _kickoff_after_first_paint(self) -> None:
        """Paint chrome, then start prefetch. Never SMB/COM before this returns paint."""
        if getattr(self, "_startup_kickoff_done", False):
            return
        self._startup_kickoff_done = True
        self.repaint()
        self._start_prefetch()
        self._arm_default_auto_fetch()

    def _arm_default_auto_fetch(self) -> None:
        """Start Auto fetch when the checkbox is on but the watcher is not yet.

        Prefetch already owns the first Machine/COM load — only arm the watcher
        here so we do not invalidate mid-flight (that wiped Machine to NO MACHINE).
        """
        if not self._worker_signals_enabled():
            return
        if not self._auto_fetch_toggle.isChecked():
            return
        if self._auto_fetch_timer.isActive():
            return
        self._busy_progress_suppressed = False
        self._auto_fetch_baseline_mtime = 0.0
        self._auto_fetch_baseline_root = ""
        self._sync_auto_fetch_watch_mode()
        self._auto_fetch_timer.start()
        if (
            self._compare_running()
            or self._meter_fetch_running()
            or self._local_diff_running
            or self._cabinet_compare_force_pending
        ):
            return
        self._queue_auto_fetch_refresh()

    def _deferred_startup_warm(self) -> None:
        """Serial enum + MagicWheel + theme catalog after the window is interactive."""
        if not self._worker_signals_enabled():
            return
        try:
            self._schedule_com_port_refresh_background()
        except Exception:
            traceback.print_exc()
        try:
            self._sync_magicwheel_tab_visibility()
        except Exception:
            traceback.print_exc()
        QTimer.singleShot(0, self._continue_prefetch_com_tier)
        if self._meter_tabs.currentIndex() == TAB_GAME:
            QTimer.singleShot(0, self._reload_game_theme_catalog)

    def _on_scan_root_edit_changed(self) -> None:
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        host_changed = self._scan_target_host_changed(sr)
        if host_changed:
            self._abort_inflight_for_scan_target_change(reason="scan_root_host")
        self._sync_cabinet_ip_from_scan_root(sr)
        self._note_scan_target_host(sr)
        self._update_ram_clear_button_enabled()
        self._resolved_scan_root_hint_cf = None
        self._resolved_scan_root_cached = ""
        self._sync_magicwheel_tab_visibility()

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
        # Scan root changed enough to reload — drop the previous SASControler snapshot.
        self._local_sas_state = {}
        if sr and not self._compare_running():
            self._begin_cabinet_compare(prefetch=True)

    def _invalidate_machine_cabinet_cache(self, *, wipe: bool = False) -> None:
        """Expire Machine cache so the next compare reloads from scan root.

        By default keep the last snapshot in memory and on screen — Auto fetch
        used to clear ``_machine_state_loaded`` (and then wipe ``_machine_state``)
        on every refresh, which flashed NO MACHINE / "Loading…" and left the
        table empty when a reload returned {} or was discarded.
        """
        self._machine_state_loaded_at = 0.0
        if wipe:
            self._machine_state_loaded = False
            self._loaded_cabinet_scan_root = ""
            self._machine_state = {}

    def _paint_immediate_busy_feedback(self) -> None:
        """Repaint status/chrome without re-entering the full event loop."""
        for widget in (
            getattr(self, "_prefetch_status_label", None),
            getattr(self, "_btn_get_meters", None),
            getattr(self.ui, "compare_btn", None) if hasattr(self, "ui") else None,
        ):
            if widget is not None:
                widget.update()
        self.update()

    def _cabinet_cache_valid(self) -> bool:
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        return should_skip_cabinet_reload(
            scan_root=sr,
            loaded_scan_root=self._loaded_cabinet_scan_root,
            machine_state_loaded=self._machine_state_loaded,
            loaded_at=self._machine_state_loaded_at or None,
            ttl_seconds=CABINET_MACHINE_CACHE_TTL_S,
        )

    def _machine_loaded_for_current_root(self) -> bool:
        """Machine XML is in memory for the current scan root (ignores the TTL).

        The 15 s TTL exists only to decide whether *prefetch* may reuse a
        snapshot. "Is the Machine column empty?" must not age out — treating an
        expired-but-loaded snapshot as empty restarted compares in a loop and
        kept the busy bar animating after a slow COM capture.
        """
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        return should_skip_cabinet_reload(
            scan_root=sr,
            loaded_scan_root=self._loaded_cabinet_scan_root,
            machine_state_loaded=self._machine_state_loaded,
            ttl_seconds=None,
        )

    def _offline_log_scan(self) -> bool:
        """USB log export or other offline snapshot — no live COM prefetch."""
        from network.goldclub_paths import (
            GoldclubLayoutKind,
            is_usb_log_export_path,
            resolve_goldclub_layout,
        )

        sr = self._scan_root_text()
        if not sr:
            return False
        if _scan_root_is_unc(sr):
            return False
        if is_usb_log_export_path(sr):
            return True
        layout = resolve_goldclub_layout(sr)
        kind = getattr(layout, "kind", None) if layout is not None else None
        return kind == GoldclubLayoutKind.USB_EXPORT

    def _cabinet_share_meters_mode(self) -> bool:
        """True when the user chose remote-share meters while COM is blocked."""
        return bool(getattr(self, "_cabinet_share_only_mode", False))

    def _share_meters_without_com(self) -> bool:
        """Machine+SAS from files only — local EGM layout or remote share-only."""
        return self._local_files_only_mode() or self._cabinet_share_meters_mode()

    def _local_files_only_mode(self) -> bool:
        """
        Resolving local cabinet files (on the EGM itself or its G:\\ game drive).

        Live SAS/MUX compare is not possible/meaningful here — the machine at the
        end of a SAS cable is not the one these files describe (or there is no
        host cable at all). Only local state folders are cross-checked, and the
        UI says so instead of pretending a live compare happened.
        """
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr or sr.startswith("\\\\"):
            return False
        if self._offline_log_scan():
            return False
        from network.goldclub_paths import (
            GoldclubLayoutKind,
            is_game_image_drive_path,
            resolve_goldclub_layout,
        )

        if is_game_image_drive_path(sr):
            return True
        from network.health_monitor import is_running_on_local_egm

        if is_running_on_local_egm():
            return True
        layout = resolve_goldclub_layout(sr)
        return layout is not None and layout.kind == GoldclubLayoutKind.LOCAL_CABINET

    def _resolved_com_port_for_fetch(self, *, cached_only: bool = False) -> str:
        """Auto-detected SAS/MUX port, or "" when no host cable / MUX is present.

        Empty means no live SAS capture — SAS columns stay empty until COM is
        free. Machine column still loads from Scan root (remote UNC on a
        workstation; local D:\\ / USB only when remote is unavailable).
        ``cached_only`` skips ``list_ports`` (startup prefetch / first paint).
        """
        from network.sas_serial_meters import (
            enumerate_serial_ports,
            normalize_com_port,
            pick_sas_com_port,
        )

        ports = enumerate_serial_ports(cached_only=cached_only)
        if not ports:
            return ""
        kind = getattr(self, "_scan_game_kind", None) or "slot"
        on_cabinet = False
        if not cached_only:
            kind = self._resolved_game_client_kind()
            on_cabinet = self._onehand_check_uses_local()
        picked = pick_sas_com_port(
            getattr(self, "_preferred_com_port", ""),
            ports,
            game_kind=kind,
            on_cabinet=on_cabinet,
        )
        return normalize_com_port(picked or "")

    def _sas_mux_accessible(self, *, cached_ports: bool = False) -> tuple[str, str]:
        """Return ``(port, detail)`` when SAS/MUX looks usable; else ``("", reason)``.

        UI-thread safe: only enumerates COM + a cached/hidden blocker check.
        The exclusive serial probe runs inside ``MeterFetchWorker`` so the dialog
        never freezes as "Not Responding".
        """
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify")
        if self._offline_log_scan():
            return "", "USB/offline log export — live SAS/MUX capture skipped"
        if self._cabinet_share_meters_mode():
            return "", (
                "cabinet share only — live SAS/MUX skipped while COM is blocked "
                "(Machine + SASControler from the remote share)"
            )
        if self._local_files_only_mode():
            # Never spend 4 wire/baud attempts on the EGM's own COM — the SAS
            # line here is not a host view of this machine's meters.
            return "", (
                "local cabinet files — live SAS/MUX capture is not possible on "
                "the machine itself (comparing local files only)"
            )
        port = self._resolved_com_port_for_fetch(cached_only=cached_ports)
        if not port:
            log.info("SAS/MUX: no COM port detected")
            return "", "No SAS/MUX COM port detected"
        from network.sas_serial_meters import find_running_sas_com_blockers

        # Process-name probes are informational only — Access Denied from the
        # serial open is the source of truth for the share-fetch dialog.
        blockers = find_running_sas_com_blockers(cached_only=True)
        self._update_com_blocker_warning(blockers)

        on_cabinet = False if cached_ports else self._onehand_check_uses_local()
        log.info(
            "SAS/MUX port selected port=%s on_cabinet=%s blockers=%s "
            "(serial probe deferred to worker)",
            port,
            on_cabinet,
            blockers or "(none)",
        )
        return port, f"{port} selected for live SAS/MUX capture"

    def _update_com_blocker_warning(self, blockers: list[str] | tuple[str, ...] | None) -> None:
        label = getattr(self, "_com_blocker_warning", None)
        if label is None:
            return
        names = [str(x).strip() for x in (blockers or []) if str(x).strip()]
        if not names:
            label.hide()
            label.setText("")
            return
        who = ", ".join(names)
        has_remote = bool(
            self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
        )
        # Informational only — the share dialog appears only after COM Access Denied.
        label.setText(
            f"<b>Note</b> — {who} is running and may hold a SAS COM port. "
            "If capture fails with Access Denied and a cabinet UNC is set, "
            "you can fetch <b>gm2au + SASControler1</b> from the share."
        )
        label.show()

    def _enable_cabinet_share_only_mode(self) -> None:
        """Skip SAS/MUX; load Machine + SASControler from the remote scan root."""
        self._cabinet_share_only_mode = True
        self._cabinet_share_prompt_asked = True
        self._com_recovery_pending = False
        if (
            not self._share_recovery_pending
            and not self._game_recovery_pending
            and self._recovery_timer.isActive()
        ):
            self._recovery_timer.stop()
        self._update_prefetch_status(
            cabinet_share_only_status(self._scan_root, "COM Access Denied.")
            + " Loading…"
        )
        self._set_busy_progress_active(True)
        self._begin_local_pair_diff()

    def _offer_cabinet_share_when_com_blocked(
        self,
        *,
        force_prompt: bool = False,
        port_busy: bool = False,
        com_detail: str = "",
    ) -> bool:
        """Prompt after COM Access Denied — fetch Machine+SASControler1 via UNC.

        Process names are not consulted. ``port_busy`` must be True (Access
        Denied / port held). The dialog exposes
        ``Fetch from cabinet share (gm2au + SASControler1)``.

        Returns True only when share-only mode is active (or was just accepted).
        """
        if not self._worker_signals_enabled():
            return False
        if self._cabinet_share_meters_mode():
            if not self._local_diff_running:
                self._begin_local_pair_diff()
            self._update_prefetch_status(
                cabinet_share_only_status(self._scan_root, self._local_diff_summary)
            )
            return True
        has_remote = bool(
            self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
        )
        if not should_offer_cabinet_share_when_com_blocked(
            has_remote_unc=has_remote,
            port_busy=port_busy,
        ):
            return False
        if self._cabinet_share_prompt_asked and not force_prompt:
            return False
        self._cabinet_share_prompt_asked = True
        body = cabinet_share_when_com_blocked_prompt_text(
            scan_root=self._scan_root
            or (self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else ""),
            cabinet_ip=self._cabinet_ip_from_scan_root()
            or self._scan_root_unc_host()
            or "",
            com_detail=com_detail or ("Access Denied" if port_busy else ""),
        )
        box = build_com_blocked_cabinet_share_dialog(self, body=body)
        box.exec()
        clicked = box.clickedButton()
        if clicked is not None and CABINET_SHARE_FETCH_BUTTON_LABEL in (
            clicked.text() or ""
        ):
            self._enable_cabinet_share_only_mode()
            return True
        # Declined: keep waiting for live COM; Machine can still load from UNC.
        return False

    def _clear_cabinet_share_only_if_com_free(
        self, blockers: list[str] | tuple[str, ...] | None
    ) -> None:
        """Leave share-only mode once nothing holds the COM port."""
        if not self._cabinet_share_meters_mode():
            return
        names = [str(x).strip() for x in (blockers or []) if str(x).strip()]
        if names:
            return
        self._cabinet_share_only_mode = False
        self._cabinet_share_prompt_asked = False

    def _offer_local_game_image_root(self, candidate: str) -> None:
        """Ask permission before resolving meters from the local G:\\ game drive."""
        if not self._worker_signals_enabled() or not candidate:
            return
        from network.health_monitor import local_egm_game_client_running

        if not local_egm_game_client_running():
            # G:\ is only meaningful when this exe shares the EGM with the game.
            self._update_prefetch_status(local_g_drive_waiting_for_game_status(candidate))
            return
        reply = QMessageBox.question(
            self,
            "SAS Verify Meters",
            (
                "No remote cabinet share is set or reachable.\n\n"
                f"Local game drive found: {candidate}\n\n"
                "OneHand.exe / Ruleta.exe / godot.exe is running on this device.\n"
                "Resolve Machine meters from these local files?\n\n"
                "Note: live SAS/MUX compare is not possible on the machine "
                "itself — only the local snapshot folders will be compared."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._update_prefetch_status(
                "No meters source — set a scan root (remote UNC, local folder, "
                "or USB export) to load the Machine column."
            )
            return
        blocked = self._scan_root_edit.blockSignals(True)
        self._scan_root_edit.setText(candidate)
        self._scan_root_edit.blockSignals(blocked)
        self._scan_root = candidate
        self._invalidate_machine_cabinet_cache(wipe=True)
        self._begin_cabinet_compare(prefetch=True, force=True)

    def _prompt_local_d_scan_root(self, *, reason: str = "") -> bool:
        """Offer a local D:\\ / USB scan root when SAS/MUX and remote share failed."""
        detail = (reason or "").strip()
        body = (
            "Live SAS/MUX and the remote cabinet share are not available.\n\n"
            "Scan a local folder instead?\n"
            "Typical: run this exe from USB on D:\\ and select Goldclub\\var\\log "
            "or a log_DD_MM_YYYY export folder."
        )
        if detail:
            body = f"{detail}\n\n{body}"
        reply = QMessageBox.question(
            self,
            "SAS accounting verification",
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._update_prefetch_status(
                "No meters source — attach SAS/MUX, restore remote share access, "
                "or choose a local D:\\ scan root."
            )
            return False
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select local / USB scan root (usually on D:\\)",
            "D:\\",
        )
        if not folder:
            return False
        blocked = self._scan_root_edit.blockSignals(True)
        self._scan_root_edit.setText(folder)
        self._scan_root_edit.blockSignals(blocked)
        self._scan_root = folder
        self._loaded_cabinet_scan_root = ""
        self._machine_state = {}
        self._machine_state_loaded = False
        self._machine_state_loaded_at = 0.0
        self._prefetch_started = False
        self._cabinet_share_unreachable = False
        self._start_prefetch()
        return True

    def _schedule_local_scan_fallback(
        self,
        *,
        reason: str,
        mux_detail: str = "",
        igt_running: bool | None = None,
    ) -> None:
        """Offer G:\\ (if allowed) or D:\\ / USB after COM/MUX and remote share failed.

        G:\\ requires OneHand / Ruleta / godot on this device. On a local EGM where
        G:\\ exists but the game is down, do not pop the D:\\ picker — say so.
        """
        detail = (mux_detail or reason or "").strip()
        if not should_offer_local_scan_prompt(
            mux_detail=detail, igt_running=igt_running
        ):
            if not (igt_running if igt_running is not None else igt_sas_tester_is_running()):
                self._update_prefetch_status(
                    com_access_denied_online_status(
                        self._resolved_com_port_for_fetch(), detail
                    )
                )
            return
        from network.goldclub_paths import discover_local_game_image_scan_root
        from network.health_monitor import (
            is_running_on_local_egm,
            local_egm_game_client_running,
        )

        g_candidate = discover_local_game_image_scan_root()
        game_up = local_egm_game_client_running()
        if g_candidate and should_offer_g_drive_prompt(
            mux_detail=detail,
            igt_running=igt_running if igt_running is not None else False,
            local_game_running=game_up,
        ):
            QTimer.singleShot(
                0, lambda c=g_candidate: self._offer_local_game_image_root(c)
            )
            return
        if g_candidate and is_running_on_local_egm() and not game_up:
            self._update_prefetch_status(local_g_drive_waiting_for_game_status(g_candidate))
            return
        QTimer.singleShot(
            0,
            lambda r=reason: self._prompt_local_d_scan_root(reason=r),
        )

    def _seed_verify_rows_from_cabinet_if_needed(self) -> None:
        if self._last_parsed_rows or not self._machine_state_loaded:
            return
        from network.sas_serial_meters import DEFAULT_6F_VERIFY_POLL_CODES

        self._last_parsed_rows = [
            Sas6FRow(meter_id=code, sas_value_text="")
            for code in DEFAULT_6F_VERIFY_POLL_CODES
        ]

    def _start_prefetch(self) -> None:
        if self._prefetch_started:
            return
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify")
        self._prefetch_started = True
        self._meter_prefetch_retried = False
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        self._update_prefetch_status("Prefetching: starting…")
        self._set_busy_progress_active(True)
        log.info(
            "prefetch start scan_root=%s game_kind=%s",
            self._scan_root,
            getattr(self, "_scan_game_kind", ""),
        )

        host = self._scan_root_unc_host()
        if host and _scan_root_is_unc(self._scan_root):
            self._cabinet_share_unreachable = False
            self._schedule_cabinet_reachability_probe(host)

        # Machine XML first (worker thread) — before busy repaint. Painting busy
        # used to re-enter the event loop while compare was not marked running yet,
        # so Auto-fetch arm queued a second round and the UI stalled on "starting…".
        if self._scan_root:
            self._begin_cabinet_compare(prefetch=True)
        self._paint_immediate_busy_feedback()
        ip = self._cabinet_ip_from_scan_root()
        if ip:
            QTimer.singleShot(1500, self._run_onehand_check)
        QTimer.singleShot(0, self._continue_prefetch_com_tier)

    def _continue_prefetch_com_tier(self) -> None:
        if not self._worker_signals_enabled():
            return
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify")
        port, mux_detail = self._sas_mux_accessible(cached_ports=True)
        has_remote = bool(self._cabinet_ip_from_scan_root() or self._scan_root_unc_host())
        offline = self._offline_log_scan()
        log.info(
            "prefetch tier probe port=%r mux_detail=%s has_remote=%s offline=%s",
            port,
            mux_detail,
            has_remote,
            offline,
        )

        if self._cabinet_share_meters_mode() and self._scan_root:
            self._update_prefetch_status(
                cabinet_share_only_status(self._scan_root, self._local_diff_summary)
            )
            if not self._local_diff_running:
                self._begin_local_pair_diff()
            self._set_busy_progress_active()
            return

        if port:
            from network.sas_serial_meters import find_running_sas_com_blockers

            # _sas_mux_accessible() just filled the blocker cache. Never force
            # tasklist on the UI thread here — that blocked Machine apply after
            # the remote compare worker had already returned.
            blockers = find_running_sas_com_blockers(cached_only=True)
            if blockers:
                log.info(
                    "prefetch COM blocked by %s — arming port watcher",
                    ", ".join(blockers),
                )
                self._arm_com_recovery()
            if not self._auto_fetch_enabled():
                if blockers:
                    self._update_prefetch_status(
                        com_recovery_waiting_status(self._resolved_com_port_for_fetch())
                    )
                else:
                    self._update_prefetch_status(
                        f"SAS/MUX on {port} ready — Auto fetch off; click Refresh Meters."
                    )
                self._set_busy_progress_active()
                # Remote Machine XML may already be queued — paint it now that
                # we know COM will not start from this prefetch path.
                self._flush_pending_cabinet_ui_refresh()
                return
            self._update_prefetch_status(
                f"Preferring live SAS/MUX on {port} — capturing meters…"
            )
            self._begin_meter_fetch(prefetch=True, status_message=False)
            return

        port_hint = self._resolved_com_port_for_fetch()

        if com_error_is_port_busy(mux_detail):
            igt_running = igt_sas_tester_is_running()
            if has_remote and self._scan_root and igt_running:
                if self._offer_cabinet_share_when_com_blocked(
                    force_prompt=True,
                    port_busy=True,
                    com_detail=mux_detail,
                ):
                    self._set_busy_progress_active()
                    return
            if igt_running:
                self._arm_com_recovery()
                self._update_prefetch_status(
                    com_recovery_waiting_status(self._resolved_com_port_for_fetch())
                )
            else:
                self._disarm_com_recovery_if_no_igt()
                self._update_prefetch_status(
                    com_access_denied_online_status(port_hint, mux_detail)
                )
            self._set_busy_progress_active()
            return

        if self._local_files_only_mode() and self._scan_root:
            self._update_prefetch_status(
                local_files_only_status(self._scan_root, self._local_diff_summary)
            )
            self._set_busy_progress_active()
            return

        if has_remote and self._scan_root:
            self._update_prefetch_status(
                "SAS/MUX not available — falling back to remote cabinet share "
                f"({self._scan_root}). {mux_detail}"
            )
            self._set_busy_progress_active()
            return

        if offline and self._scan_root:
            self._update_prefetch_status(
                "SAS/MUX not available — loading meters from local/USB snapshot "
                f"({self._scan_root})."
            )
            self._set_busy_progress_active()
            return

        if self._scan_root:
            self._update_prefetch_status(
                "SAS/MUX not available — loading local scan root "
                f"({self._scan_root})."
            )
            self._set_busy_progress_active()
            return

        self._set_busy_progress_active()
        self._schedule_local_scan_fallback(
            reason=f"SAS/MUX not available ({mux_detail}) and no remote share path is set.",
            mux_detail=mux_detail,
            igt_running=False,
        )

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
            elif not getattr(self._cached_meter_result, "paste_text", None):
                # Empty capture can never apply — drop it, or the status handler
                # reschedules this auto-apply forever ("Applying prefetched…").
                self._cached_meter_result = None
                self._meter_fetch_error = (
                    "COM capture returned no meter data (empty SAS response)."
                )
        except Exception:
            traceback.print_exc()
        finally:
            self._update_prefetch_status()

    def _update_prefetch_status(self, override: str | None = None) -> None:
        self._sync_get_meters_button_label()
        self._set_busy_progress_active()
        if override:
            self._set_prefetch_status_text(override)
            return
        # Local EGM / USB snapshot: never claim SAS/MUX capture is coming — there
        # is no host cable. Gaps between compare-thread finished and apply used
        # to fall through to "Cabinet loading… SAS/MUX…".
        if self._local_files_only_mode() or self._offline_log_scan():
            if (
                self._compare_running()
                or self._local_diff_running
                or self._cabinet_compare_force_pending
            ):
                self._set_prefetch_status_text(
                    "Refreshing meters from local files…"
                )
                return
            if self._machine_loaded_for_current_root():
                self._set_prefetch_status_text(
                    local_files_only_status(self._scan_root, self._local_diff_summary)
                )
                return
            # Idle + empty is not "still loading" — that wording stuck the UI
            # after an empty CompareWorker result with nothing in flight.
            self._set_prefetch_status_text(
                machine_meters_unavailable_status(
                    self._scan_root_edit.text() or self._scan_root
                )
            )
            return
        # Auto-recovery waiting states: nothing is in flight, so no busy bar —
        # say exactly what is being waited for and that it resolves by itself.
        if not self._meter_fetch_running() and not self._compare_running():
            if self._com_recovery_pending:
                self._set_prefetch_status_text(
                    com_recovery_waiting_status(self._resolved_com_port_for_fetch())
                )
                return
            if self._game_recovery_pending:
                self._set_prefetch_status_text(
                    game_recovery_waiting_status(
                        self._game_client_exe_label(),
                        self._cabinet_ip_from_scan_root() or self._scan_root_unc_host(),
                    )
                )
                return
            if self._share_recovery_pending and not self._machine_loaded_for_current_root():
                self._set_prefetch_status_text(
                    share_recovery_waiting_status(
                        self._scan_root, _extract_unc_host(self._scan_root)
                    )
                )
                return
        if self._cached_meter_result is not None and self._machine_loaded_for_current_root():
            if self._meter_fetch_user_clicked_apply:
                self._set_prefetch_status_text(
                    "Meters displayed — click Refresh Meters to capture again from COM."
                )
            else:
                self._set_prefetch_status_text("Applying prefetched COM meters…")
                QTimer.singleShot(0, self._auto_apply_cached_meters_if_needed)
            return
        if self._meter_fetch_error and not self._cached_meter_result:
            if self._machine_loaded_for_current_root():
                self._set_prefetch_status_text(
                    f"Cabinet loaded — COM capture failed: {self._meter_fetch_error} "
                    "(Machine column from snapshot; click Refresh Meters to retry COM)"
                )
            else:
                self._set_prefetch_status_text(
                    f"COM capture failed: {self._meter_fetch_error} "
                    "(click Refresh Meters to retry)"
                )
            return
        if self._machine_loaded_for_current_root() and self._meter_fetch_running() and not self._compare_running():
            self._set_prefetch_status_text(
                "Cabinet loaded — COM capture in progress "
                "(Machine column filled; SAS fills when capture completes)…"
            )
            return
        parts: list[str] = []
        if self._compare_running():
            parts.append("cabinet")
        if self._meter_fetch_running():
            parts.append("COM capture")
        if parts:
            self._set_prefetch_status_text(
                f"Prefetching: {' and '.join(parts)}…"
            )
            return
        if self._cached_meter_result is not None:
            if self._meter_fetch_user_clicked_apply:
                self._set_prefetch_status_text(
                    "Meters displayed — click Refresh Meters to capture again from COM."
                )
            else:
                self._set_prefetch_status_text("Applying prefetched COM meters…")
                QTimer.singleShot(0, self._auto_apply_cached_meters_if_needed)
            return
        if self._machine_loaded_for_current_root():
            self._set_prefetch_status_text(
                "Cabinet loaded — prefetching COM meters (tables fill automatically)…"
            )
            return
        if not self._resolved_com_port_for_fetch():
            self._set_prefetch_status_text(
                "SAS/MUX not available — cabinet meters load from the scan root "
                "(remote share when reachable, otherwise local/USB). "
                "Attach SAS/MUX for a live capture."
            )
            return
        self._set_prefetch_status_text(
            "Cabinet loading… SAS/MUX meters will appear automatically when capture finishes."
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

    def _begin_meter_fetch(
        self,
        *,
        prefetch: bool,
        force: bool = False,
        full_timing: bool = False,
        status_message: bool = True,
    ) -> bool:
        """Start background COM capture (IGT 200 ms GP cadence in ``sas_serial_meters``).

        Prefetch skips bill long polls for speed; use a manual refresh for full bill LPs.
        """
        from gui.app_logging import get_logger
        from network.sas_serial_meters import find_running_sas_com_blockers

        log = get_logger("gui.sas_verify")
        if self._local_files_only_mode() or self._offline_log_scan():
            # Local EGM / USB snapshot: no host cable to poll. Opening COM here
            # hangs Refresh forever (MUX is not a view of *this* machine).
            log.info(
                "begin_meter_fetch skipped — local/offline files only (no COM)"
            )
            return False
        # Never force_refresh tasklist on the UI thread. Prefetch uses the cache
        # filled by _sas_mux_accessible; the MeterFetchWorker re-checks. A forced
        # tasklist here left the dialog Not Responding after remote Machine XML
        # had already loaded in the compare worker.
        from network.sas_serial_meters import sas_com_blocker_cache_ready

        if sas_com_blocker_cache_ready() or prefetch:
            blockers = find_running_sas_com_blockers(cached_only=True)
        else:
            blockers = find_running_sas_com_blockers()
        if self._cabinet_share_meters_mode():
            log.info(
                "begin_meter_fetch skipped — cabinet share only (COM Access Denied)"
            )
            self._update_com_blocker_warning(blockers)
            if not self._local_diff_running:
                self._begin_local_pair_diff()
            self._update_prefetch_status(
                cabinet_share_only_status(self._scan_root, self._local_diff_summary)
            )
            self._sync_get_meters_button_label()
            self._set_busy_progress_active()
            return False
        self._update_com_blocker_warning(blockers)
        port = self._resolved_com_port_for_fetch()
        # Always try the serial open — Access Denied (any holder) triggers the
        # cabinet-share dialog from _on_meter_fetch_error.
        if not port:
            log.info("begin_meter_fetch aborted — no COM port")
            return False
        if self._meter_fetch_running():
            if not force:
                log.info("begin_meter_fetch skipped — capture already running")
                return False
            # The running worker still owns the COM port and cannot be interrupted
            # mid-read. Opening it again races that handle, so queue the forced
            # capture and let the current one finish first.
            if not self._stop_meter_fetch_thread(wait_ms=1200):
                self._pending_forced_fetch = {
                    "prefetch": prefetch,
                    "full_timing": full_timing,
                }
                self._update_prefetch_status(
                    "Waiting for the running COM capture to release the port…"
                )
                return False
        if self._cached_meter_result is not None and not force:
            return False
        self._pending_forced_fetch = None
        self._meter_fetch_prefetch = prefetch
        self._meter_fetch_error = None
        self._btn_get_meters.setText("Capturing COM…")
        if not prefetch:
            self._btn_get_meters.setEnabled(False)
        elif status_message:
            self._update_prefetch_status()
        log.info(
            "begin_meter_fetch port=%s prefetch=%s force=%s full_timing=%s blockers=%s",
            port,
            prefetch,
            force,
            full_timing,
            blockers or "(none)",
        )
        from network.sas_serial_meters import DEFAULT_SAS_COM_BAUD

        self._meter_fetch_job_id += 1
        job_id = self._meter_fetch_job_id
        self._active_meter_fetch_job_id = job_id
        self._meter_fetch_started_mono = time.monotonic()

        self._meter_fetch_thread = QThread(self)
        self._meter_fetch_worker = MeterFetchWorker(
            com_port=port,
            com_baud=int(getattr(self, "_com_baud", DEFAULT_SAS_COM_BAUD)),
            skip_bill_polls=prefetch,
            cached_profile=getattr(self, "_cached_serial_profile", None),
            prefetch=prefetch,
            game_kind=self._resolved_game_client_kind(),
            on_cabinet=self._onehand_check_uses_local(),
            full_timing=full_timing,
            # A full-timing Refresh is an explicit "try hard" — keep it cold.
            warm=not full_timing
            and bool(getattr(self, "_com_link_warm", False))
            and getattr(self, "_cached_serial_profile", None) is not None,
        )
        self._meter_fetch_worker.moveToThread(self._meter_fetch_thread)
        self._meter_fetch_thread.started.connect(self._meter_fetch_worker.run)
        self._meter_fetch_worker.finished.connect(
            lambda r, j=job_id: self._on_meter_fetch_finished(r, j),
            Qt.ConnectionType.QueuedConnection,
        )
        self._meter_fetch_worker.error.connect(
            lambda msg, j=job_id: self._on_meter_fetch_error(msg, j),
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

    def _machine_paint_waits_for_sas(self) -> bool:
        """True while the SAS half of this Auto fetch round is still capturing.

        Holding the Machine repaint until then means every meter a game moved
        appears in one paint — both columns together — instead of the Machine
        column running seconds ahead of SAS on its much faster file read.
        """
        if not self._auto_fetch_round_active:
            return False
        return self._meter_fetch_running()

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
        # The link answered $6F, so the next capture may skip the GP wakeup.
        self._com_link_warm = True
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
        # Only clear refs for *this* thread. An older finished/orphaned thread must
        # not wipe a newer capture that is still loading meters.
        sender = self.sender()
        if sender is not None and sender is not self._meter_fetch_thread:
            return
        self._meter_fetch_thread = None
        self._meter_fetch_worker = None
        pending = self._pending_forced_fetch
        if pending and self._worker_signals_enabled():
            self._pending_forced_fetch = None
            self._begin_meter_fetch(
                prefetch=bool(pending.get("prefetch")),
                force=True,
                full_timing=bool(pending.get("full_timing")),
            )
            return
        if self._worker_signals_enabled():
            # A Machine paint deferred for pairing must never be stranded: the
            # error path can run while this thread is still winding down, and
            # the flush there would see it as still capturing.
            self._flush_pending_cabinet_ui_refresh()
            self._set_busy_progress_active()
            if not self._busy_progress_should_run():
                self._update_prefetch_status()

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
            exe = self._game_client_exe_label()
            QMessageBox.warning(
                self,
                "Get Meters",
                f"{exe} is not running on {ip}.\n\n"
                "The SAS host link often returns no RX until the game client is started "
                f"on the EGM (Aurum / CommCtrl). Start {exe} on the cabinet, then retry.",
            )
        elif ip and self._onehand_running is None and not self._onehand_check_pending:
            self._run_onehand_check()
        self._save_com_port_prefs()
        self._scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        # Always reload Machine with SAS refresh — same scan_root can hold newer XML.
        if self._scan_root:
            self._invalidate_machine_cabinet_cache()
            self._begin_cabinet_compare(prefetch=True, force=True)
        if not self._begin_meter_fetch(prefetch=False, force=True):
            self._meters_ui_pending = False
            self._manual_meters_refresh = False
            self._btn_get_meters.setEnabled(True)
            self._sync_get_meters_button_label()
            self._set_busy_progress_active()
            QMessageBox.warning(
                self,
                "Get Meters",
                "Could not start COM capture. Check the COM port and try again.",
            )
        else:
            self._meters_ui_pending = False
            self._update_prefetch_status("COM capture in progress…")

    def _on_get_meters_clicked(self) -> None:
        # Instant UI feedback before COM/MUX probe or any heavy work.
        if self._meter_fetch_running():
            # Never ignore a second Refresh press — queue a forced recapture.
            self._pending_forced_fetch = {
                "prefetch": False,
                "full_timing": True,
            }
            self._btn_get_meters.setEnabled(False)
            self._btn_get_meters.setText("Capturing COM…")
            self._set_busy_progress_active(True)
            self._set_prefetch_status_text(
                "Queued — will recapture when the current COM read finishes…"
            )
            self._paint_immediate_busy_feedback()
            if self._compare_running():
                self._begin_cabinet_compare(
                    prefetch=False, force=True, immediate_paint=True
                )
            return
        if self._meters_ui_pending:
            # Stuck UI flag with no live COM thread — clear and restart.
            self._meters_ui_pending = False
            if self._compare_running():
                self._begin_cabinet_compare(
                    prefetch=False, force=True, immediate_paint=True
                )
        self._meters_ui_pending = True
        self._manual_meters_refresh = True
        self._btn_get_meters.setEnabled(False)
        local_only = self._share_meters_without_com()
        if local_only:
            self._btn_get_meters.setText("Refreshing…")
            self._set_busy_progress_active(True)
            self._set_prefetch_status_text(
                "Refreshing meters from cabinet files (no COM)…"
            )
        else:
            self._btn_get_meters.setText("Capturing COM…")
            self._set_busy_progress_active(True)
            self._set_prefetch_status_text("Starting COM capture…")
        self._paint_immediate_busy_feedback()
        QTimer.singleShot(0, self._continue_get_meters_after_ui_feedback)

    def _continue_get_meters_after_ui_feedback(self) -> None:
        if not self._worker_signals_enabled():
            self._meters_ui_pending = False
            self._manual_meters_refresh = False
            return
        port, mux_detail = self._sas_mux_accessible()
        if not port:
            self._scan_root = (
                self._scan_root_edit.text() or self._scan_root or ""
            ).strip()
            has_remote = bool(
                self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
            )
            # Access Denied is the only gate for the share dialog.
            if (
                com_error_is_port_busy(mux_detail)
                and has_remote
                and self._scan_root
                and self._offer_cabinet_share_when_com_blocked(
                    force_prompt=True,
                    port_busy=True,
                    com_detail=mux_detail,
                )
            ):
                self._meters_ui_pending = False
                self._btn_get_meters.setEnabled(True)
                self._sync_get_meters_button_label()
                self._set_busy_progress_active()
                return
            if com_error_is_port_busy(mux_detail):
                if not self._com_recovery_pending:
                    self._arm_com_recovery()
                self._update_prefetch_status(
                    com_access_denied_online_status(
                        self._resolved_com_port_for_fetch(), mux_detail
                    )
                )
            igt_running = igt_sas_tester_is_running()
            # Tier 1 unavailable → remote share when configured. Local G:\ / D:\
            # prompt only when COM is down and IGT is not running / not port-busy.
            local_only = self._share_meters_without_com()
            if self._scan_root and (has_remote or self._offline_log_scan() or local_only):
                if self._cabinet_share_meters_mode():
                    self._update_prefetch_status(
                        cabinet_share_only_status(
                            self._scan_root, self._local_diff_summary
                        )
                    )
                    self._begin_local_pair_diff()
                elif self._local_files_only_mode():
                    self._update_prefetch_status(
                        local_files_only_status(
                            self._scan_root, self._local_diff_summary
                        )
                    )
                    self._invalidate_machine_cabinet_cache()
                    self._begin_cabinet_compare(prefetch=False, force=True)
                elif com_error_is_port_busy(mux_detail) and not igt_running:
                    self._update_prefetch_status(
                        "Access denied on SAS/MUX (online/host likely holds the port) — "
                        "loading cabinet meters from the scan root."
                    )
                    self._invalidate_machine_cabinet_cache()
                    self._begin_cabinet_compare(prefetch=False, force=True)
                else:
                    self._update_prefetch_status(
                        f"SAS/MUX not available ({mux_detail}) — loading cabinet meters "
                        "from the scan root (remote share or local/USB snapshot)."
                    )
                    self._invalidate_machine_cabinet_cache()
                    self._begin_cabinet_compare(prefetch=False, force=True)
                self._meters_ui_pending = False
                # Keep _manual_meters_refresh until Machine (+ local diff) lands
                # so Auto fetch queues instead of restarting this load.
                self._btn_get_meters.setEnabled(True)
                self._sync_get_meters_button_label()
                self._set_busy_progress_active()
                return
            self._meters_ui_pending = False
            self._manual_meters_refresh = False
            self._btn_get_meters.setEnabled(True)
            self._sync_get_meters_button_label()
            self._set_busy_progress_active()
            if not should_offer_local_scan_prompt(
                mux_detail=mux_detail, igt_running=igt_running
            ):
                if not igt_running:
                    QMessageBox.warning(
                        self,
                        "Get Meters",
                        com_access_denied_online_status(
                            self._resolved_com_port_for_fetch(), mux_detail
                        ),
                    )
                return
            self._schedule_local_scan_fallback(
                reason=f"SAS/MUX not available ({mux_detail}).",
                mux_detail=mux_detail,
                igt_running=igt_running,
            )
            return
        self._begin_get_meters_capture()

    def _on_meter_fetch_finished(self, result: object, job_id: int = 0) -> None:
        if not self._worker_signals_enabled():
            return
        if job_id and job_id != self._active_meter_fetch_job_id:
            return
        QTimer.singleShot(
            0, lambda r=result, j=job_id: self._apply_meter_fetch_complete(r, j)
        )

    def _apply_meter_fetch_complete(self, result: object, job_id: int = 0) -> None:
        if not self._worker_signals_enabled():
            return
        if job_id and job_id != self._active_meter_fetch_job_id:
            return
        # In-flight COM from before/during RAM Clear must not repaint old meters.
        if getattr(self, "_ram_clear_busy", False):
            self._meters_ui_pending = False
            self._manual_meters_refresh = False
            self._btn_get_meters.setEnabled(True)
            self._btn_get_meters.setText("Refresh Meters")
            self._set_busy_progress_active()
            return
        self._meters_ui_pending = False
        self._manual_meters_refresh = False
        self._cached_meter_result = result
        self._meter_fetch_error = None
        self._com_recovery_pending = False
        self._game_recovery_pending = False
        recovery_recapture = self._recovery_recapture_pending
        self._recovery_recapture_pending = False
        self._btn_get_meters.setEnabled(True)
        self._meter_fetch_prefetch = False
        trace_event(
            "sas_landed",
            round_active=bool(self._auto_fetch_round_active),
            machine_paint_pending=bool(self._cabinet_ui_refresh_pending),
        )
        # Arm flash before painting so SAS value changes are recorded
        # (marking after apply left only the later Machine column flashing).
        # Keep the arm through the deferred Machine flush so both columns of
        # one game register as pending increases in the same pulse window.
        self._meter_flash_arm = True
        displayed = False
        try:
            displayed = bool(self._apply_meter_fetch_result(result))
        except Exception:
            traceback.print_exc()
            displayed = False
        if displayed:
            self._meter_fetch_user_clicked_apply = True
            self._fetched_status_label.setText(meters_fetched_status_text(datetime.now()))
        self._flush_pending_cabinet_ui_refresh()
        self._meter_flash_arm = False
        if (
            self._scan_root
            and not self._machine_loaded_for_current_root()
            and not self._compare_running()
        ):
            from gui.app_logging import get_logger

            get_logger("gui.sas_verify").info(
                "COM done but Machine empty — restarting cabinet compare"
            )
            self._begin_cabinet_compare(prefetch=True)
        if self._auto_fetch_round_active:
            self._resync_machine_after_auto_fetch_capture()
        elif recovery_recapture and self._scan_root:
            # Post-recovery capture landed outside a round (the COM error that
            # armed recovery already released SYNCING). Open a round so the
            # fresh SAS values pair with a Machine re-read instead of flashing
            # MISMATCH against the older snapshot.
            self._auto_fetch_round_active = True
            self._auto_fetch_round_resynced = False
            self._auto_fetch_round_started_mono = time.monotonic()
            self._resync_machine_after_auto_fetch_capture()
        self._update_prefetch_status()

    def _retry_prefetch_with_full_timing(self) -> None:
        if not self._worker_signals_enabled():
            return
        self._begin_meter_fetch(prefetch=True, force=True, full_timing=True)

    def _restart_capture_after_com_freed(self) -> None:
        """Post COM-recovery recapture: settle elapsed, full IGT-like timing."""
        if not self._worker_signals_enabled() or self._meter_fetch_running():
            return
        # Leave the quick-tier retry available: if even full timing gets nothing,
        # _on_meter_fetch_error still runs one more full-timing pass, then the
        # game-client watcher takes over.
        self._recovery_recapture_pending = True
        self._begin_meter_fetch(prefetch=True, force=True, full_timing=True)

    def _on_meter_fetch_error(self, message: str, job_id: int = 0) -> None:
        if not self._worker_signals_enabled():
            return
        if job_id and job_id != self._active_meter_fetch_job_id:
            return
        self._meters_ui_pending = False
        self._manual_meters_refresh = False
        self._meter_fetch_error = message or "Serial meter fetch failed."
        # Link no longer proven — the next capture pays the full wakeup again.
        self._com_link_warm = False
        # A failed recovery recapture has nothing to pair with Machine.
        self._recovery_recapture_pending = False
        self._btn_get_meters.setEnabled(True)
        was_prefetch = self._meter_fetch_prefetch
        self._meter_fetch_prefetch = False
        self._refresh_com_port_list(preserve_text=True)
        self._flush_pending_cabinet_ui_refresh()
        msg = self._meter_fetch_error or ""
        igt_running = False
        if com_error_is_port_busy(msg):
            igt_running = igt_sas_tester_is_running()
            has_remote = bool(
                self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
            )
            # Share fetch is only offered while IGT holds the port — otherwise
            # Access Denied means another host/tool and auto-recovery is wrong.
            if has_remote and self._scan_root and igt_running:
                if self._offer_cabinet_share_when_com_blocked(
                    force_prompt=True,
                    port_busy=True,
                    com_detail=msg,
                ):
                    self._set_busy_progress_active()
                    return
            if igt_running:
                self._arm_com_recovery()
                self._update_prefetch_status(
                    com_recovery_waiting_status(self._resolved_com_port_for_fetch())
                )
            else:
                self._disarm_com_recovery_if_no_igt()
                self._update_prefetch_status(
                    com_access_denied_online_status(
                        self._resolved_com_port_for_fetch(), msg
                    )
                )
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
                # Bound the timer to this dialog: a retry firing after close would
                # reopen the COM port with no window left to show the result.
                QTimer.singleShot(1000, self, self._retry_prefetch_with_full_timing)
                return
        if com_error_is_link_dead(msg):
            # Port opened but the EGM did not answer — the game client
            # (OneHand.exe / Ruleta.exe+godot) is down or the EGM is booting.
            # Watch it and re-fetch all meters automatically once it is up.
            self._arm_game_recovery()
        if com_error_is_port_busy(msg) and not igt_running:
            # Keep the curated access-denied guidance — a bare recompute here
            # overwrote it with the generic "COM capture failed" text whenever
            # Machine was already loaded.
            self._update_prefetch_status(
                com_access_denied_online_status(
                    self._resolved_com_port_for_fetch(), msg
                )
            )
        else:
            self._update_prefetch_status()
        # COM/MUX capture failed for good — re-evaluate the cabinet banner so the
        # tiered fallback (PsExec) or the final "all failed" warning can react.
        self._update_onehand_warning_label(self._onehand_check_ip)
        if self._auto_fetch_round_active and not self._meter_fetch_running():
            # Always release SYNCING when COM fails. Waiting for COM/game recovery
            # must not hold the Auto-fetch round open — Machine is already loaded
            # and the table was stuck on unsettled verdicts / busy status.
            self._end_auto_fetch_round()
        if was_prefetch:
            # Tier 1 failed. Tier 2 is remote share (cabinet compare). Local D:\ /
            # G:\ prompt only when COM is unavailable and IGT is not the holder
            # (port busy without IGT → online system → access denied, no prompt).
            if not self._machine_loaded_for_current_root() and not self._compare_running():
                has_remote = bool(
                    self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
                )
                if has_remote and self._scan_root and not self._compare_running():
                    self._begin_cabinet_compare(prefetch=True)
                    self._update_prefetch_status(
                        "SAS/MUX capture failed — falling back to remote cabinet share…"
                    )
                elif not self._scan_root or self._offline_log_scan():
                    if should_offer_local_scan_prompt(
                        mux_detail=msg, igt_running=igt_running
                    ):
                        self._schedule_local_scan_fallback(
                            reason=f"SAS/MUX capture failed: {msg}",
                            mux_detail=msg,
                            igt_running=igt_running,
                        )
                    elif com_error_is_port_busy(msg) and not igt_running:
                        self._update_prefetch_status(
                            com_access_denied_online_status(
                                self._resolved_com_port_for_fetch(), msg
                            )
                        )
            return
        msg = self._meter_fetch_error
        if self._onehand_running is False and "SAS link not responding" in msg:
            ip = self._cabinet_ip_from_scan_root() or "the cabinet"
            exe = self._game_client_exe_label()
            msg += (
                f"\n\n{exe} is not running on {ip}. "
                "Start the game client on the EGM, then click Refresh Meters."
            )
        if self._com_recovery_pending and igt_running:
            msg += (
                "\n\nAuto-recovery armed: close the app holding the COM port "
                "(IGT SAS tester / SASHost) and the capture restarts by itself."
            )
        elif com_error_is_port_busy(msg) and not igt_running:
            msg = com_access_denied_online_status(
                self._resolved_com_port_for_fetch(), msg
            )
        QMessageBox.warning(
            self,
            "Get Meters",
            msg,
        )

    def _on_compare_thread_finished(self) -> None:
        # Only clear refs for *this* thread. An older finished/orphaned thread must
        # not wipe a newer compare that is still loading Machine values.
        sender = self.sender()
        if sender is not None and sender is not self._compare_thread:
            return
        self._compare_thread = None
        self._compare_worker = None
        # Apply often evaluates busy while isRunning() is still True; without a
        # recheck here the thin bar stays on forever after Refresh Meters.
        if self._worker_signals_enabled():
            self._set_busy_progress_active()
            if not self._busy_progress_should_run():
                self._update_prefetch_status()

    def _on_compare_clicked(self) -> None:
        if self._compare_ui_pending or self._compare_running():
            self.ui.compare_btn.setEnabled(False)
            self.ui.compare_btn.setText("Scanning Cabinet...")
            self._set_busy_progress_active(True)
            self._set_prefetch_status_text(
                "Cabinet scan already in progress — Machine column updates when it finishes…"
            )
            self._paint_immediate_busy_feedback()
            return

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

        # Immediate feedback before currency detect / cabinet XML reload.
        self._compare_ui_pending = True
        self.ui.compare_btn.setEnabled(False)
        self.ui.compare_btn.setText("Scanning Cabinet...")
        self._set_busy_progress_active(True)
        self._set_prefetch_status_text("Reloading Machine meters from cabinet…")
        self._paint_immediate_busy_feedback()

        self._last_parsed_rows = parsed
        self._sas_2f_values = parse_sas_2f_paste(paste_text)
        QTimer.singleShot(0, self._continue_compare_after_ui_feedback)

    def _continue_compare_after_ui_feedback(self) -> None:
        if not self._worker_signals_enabled():
            self._compare_ui_pending = False
            return
        try:
            # Currency arrives with the cabinet state XML (compare below); no
            # log-grep on the UI thread here.
            self._update_dollar_toggle_label()
            # Always re-read DeviceManager XML on Compare so Machine matches live SAS.
            self._invalidate_machine_cabinet_cache()
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=False)
            self._begin_cabinet_compare(prefetch=False, force=True)
        finally:
            self._compare_ui_pending = False
            # Early exits in _begin_cabinet_compare used to leave the button stuck
            # on "Scanning Cabinet…" when no worker was started and none was queued.
            if (
                not self._compare_running()
                and not self._cabinet_compare_force_pending
            ):
                self._release_compare_busy_ui(reason="compare_click_no_worker")

    def _release_compare_busy_ui(self, *, reason: str = "") -> None:
        """Restore Compare when a cabinet load was abandoned or dropped as stale.

        Do not call while a newer compare thread still owns the busy state —
        use ``_release_compare_busy_ui_if_idle`` for finish/drop handlers.
        """
        try:
            if hasattr(self, "ui") and getattr(self.ui, "compare_btn", None) is not None:
                self.ui.compare_btn.setEnabled(True)
                self.ui.compare_btn.setText("Compare")
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "_btn_get_meters"):
                self._btn_get_meters.setEnabled(True)
        except Exception:  # noqa: BLE001
            pass
        self._cabinet_compare_prefetch = False
        self._compare_ui_pending = False
        try:
            self._set_busy_progress_active()
        except Exception:  # noqa: BLE001
            pass
        if reason:
            trace_event("compare_busy_released", reason=reason)

    def _release_compare_busy_ui_if_idle(self, *, reason: str = "") -> None:
        """Release Scanning Cabinet only when no newer compare still owns it."""
        if self._compare_running():
            return
        if getattr(self, "_ram_clear_busy", False):
            # Blank-on-clear already restored the button; avoid fighting it.
            return
        self._release_compare_busy_ui(reason=reason)

    def _begin_cabinet_compare(
        self,
        *,
        prefetch: bool = False,
        force: bool = False,
        immediate_paint: bool = True,
    ) -> None:
        """Load Machine column from cabinet state XML (Scan root UNC). Runs off the UI thread.

        ``immediate_paint=False`` is for Auto fetch background rounds: keep the
        current table visible while a fresh snapshot loads in the background.
        """
        # Resolve / early-exit checks first. Painting "Scanning Cabinet…" before
        # those used to stick the button forever when we returned without a worker
        # (cache hit, force-pending, no scan root after a prior paint path).
        # Prefetch must not SMB-remap on the UI thread (white window on UNC).
        # CompareWorker resolves layout off-thread. Manual Compare still remaps.
        if prefetch:
            self._scan_root = (
                self._scan_root_edit.text() or self._scan_root or ""
            ).strip()
        else:
            self._scan_root = self._resolve_active_scan_root()
        if not self._scan_root:
            trace_event("compare_skip", reason="no_scan_root")
            if not prefetch:
                QMessageBox.information(
                    self,
                    "SAS accounting verification",
                    "Set Scan root to the cabinet state UNC (e.g. \\\\10.0.0.90\\c$\\Goldclub\\var) "
                    "so Machine values can be loaded from DeviceManagerData.xml.",
                )
                self._release_compare_busy_ui(reason="no_scan_root")
            else:
                self._update_prefetch_status()
            self._set_busy_progress_active()
            return

        if force:
            self._invalidate_machine_cabinet_cache()

        if not force and self._cabinet_cache_valid() and not self._compare_running():
            trace_event("compare_skip", reason="cache_valid")
            self._update_prefetch_status()
            return

        if self._compare_running() or getattr(self, "_compare_starting", False):
            if force:
                elapsed = 0.0
                if self._compare_started_mono:
                    elapsed = time.monotonic() - float(self._compare_started_mono)
                # Restart only when stuck (>=20s). Auto-fetch used to restart
                # immediately (immediate_paint=False), which fought Refresh Meters
                # and left "Scanning Cabinet…" / the busy bar running forever on
                # a busy local EGM. The 45s orphan in _on_auto_fetch_timer still
                # recovers a truly wedged load.
                restart_now = elapsed >= 20.0
                trace_event(
                    "compare_busy",
                    elapsed_s=round(elapsed, 2),
                    restart=bool(restart_now),
                )
                if restart_now:
                    self._cabinet_compare_force_pending = False
                    self._compare_job_id += 1
                    self._active_compare_job_id = self._compare_job_id
                    self._stop_compare_thread(wait_ms=400)
                else:
                    self._cabinet_compare_force_pending = True
                    return
            else:
                trace_event("compare_skip", reason="running_no_force")
                return

        # Paint busy only once a worker is about to start.
        paint_busy = bool(immediate_paint) and (force or not prefetch)
        if paint_busy:
            if not prefetch:
                self.ui.compare_btn.setEnabled(False)
                self.ui.compare_btn.setText("Scanning Cabinet...")
            self._set_busy_progress_active(True)
            self._set_prefetch_status_text("Loading Machine meters from cabinet…")
            self._paint_immediate_busy_feedback()
        elif immediate_paint and not self._cabinet_cache_valid():
            self._set_busy_progress_active(True)
            self._paint_immediate_busy_feedback()

        self._cabinet_compare_force_pending = False
        self._cabinet_compare_prefetch = prefetch
        if prefetch:
            if getattr(self, "_cabinet_share_unreachable", False):
                host = self._scan_root_unc_host()
                self._update_prefetch_status(cabinet_unreachable_status(host))
            else:
                self._update_prefetch_status("Prefetching: cabinet…")
        elif not paint_busy:
            self.ui.compare_btn.setEnabled(False)
            self.ui.compare_btn.setText("Scanning Cabinet...")
            self._set_busy_progress_active(True)

        # Drop only a finished previous QThread object. Never quit(wait=0)+orphan a
        # live worker — that left Machine PENDING forever when the stale finished
        # handler nulled the active thread refs.
        if self._compare_thread is not None and not self._compare_thread.isRunning():
            self._compare_thread = None
            self._compare_worker = None
        # Only flash PENDING when Machine truly has nothing for this root — a
        # TTL-expired refresh keeps showing the old snapshot until new data lands.
        if not self._machine_loaded_for_current_root():
            self._machine_state_loaded = False
            if self._scan_root != self._loaded_cabinet_scan_root:
                self._machine_state = {}

        self._compare_job_id += 1
        job_id = self._compare_job_id
        self._active_compare_job_id = job_id
        self._active_compare_scan_root = self._scan_root
        self._compare_job_roots[job_id] = self._scan_root

        self._compare_thread = QThread(self)
        self._compare_worker = CompareWorker(_extract_unc_host(self._scan_root), self._scan_root)
        self._compare_worker.moveToThread(self._compare_thread)
        self._compare_starting = True

        self._compare_thread.started.connect(self._compare_worker.run)
        self._compare_worker.finished.connect(
            lambda state, j=job_id: self._on_worker_finished(state, j),
            Qt.ConnectionType.QueuedConnection,
        )
        self._compare_worker.error.connect(
            lambda msg, j=job_id: self._on_worker_error(msg, j),
            Qt.ConnectionType.QueuedConnection,
        )
        self._compare_worker.finished.connect(self._compare_thread.quit)
        self._compare_worker.error.connect(self._compare_thread.quit)
        self._compare_worker.finished.connect(self._compare_worker.deleteLater)
        self._compare_worker.error.connect(self._compare_worker.deleteLater)
        self._compare_thread.finished.connect(self._compare_thread.deleteLater)
        self._compare_thread.finished.connect(self._on_compare_thread_finished)
        self._compare_started_mono = time.monotonic()
        trace_event("compare_start", job=job_id, prefetch=bool(prefetch))
        self._compare_thread.start()
        QTimer.singleShot(0, self._clear_compare_starting_latch)

    def _clear_compare_starting_latch(self) -> None:
        self._compare_starting = False

    def _on_worker_finished(self, state_obj: object, job_id: int | None = None) -> None:
        trace_event(
            "compare_finish",
            job=job_id,
            active=self._active_compare_job_id,
            keys=len(state_obj) if isinstance(state_obj, dict) else -1,
        )
        if not self._worker_signals_enabled():
            trace_event("compare_drop", job=job_id, reason="signals_off")
            self._release_compare_busy_ui_if_idle(reason="signals_off")
            return
        if job_id is not None and job_id != self._active_compare_job_id:
            trace_event(
                "compare_drop",
                job=job_id,
                active=self._active_compare_job_id,
                reason="stale_job",
            )
            # RAM-clear blank bumps the job id; without this the Compare button
            # stayed on "Scanning Cabinet…" forever after the orphaned finish.
            self._release_compare_busy_ui_if_idle(reason="stale_job")
            return
        # Defer UI work to the next event-loop tick so we do not re-enter the
        # dialog while OneHand / COM prefetch handlers are still running.
        QTimer.singleShot(
            0, self, lambda s=state_obj, j=job_id: self._apply_cabinet_state(s, j)
        )

    def _compare_job_scan_root_stale(self, job_id: int | None) -> bool:
        """True when the edit box moved to a different root than this job used."""
        if job_id is None:
            return False
        job_root = self._compare_job_roots.get(int(job_id))
        if not job_root:
            return False
        current = (self._scan_root_edit.text() or self._scan_root or "").strip()
        return bool(current) and current != job_root

    def _apply_cabinet_state(self, state_obj: object, job_id: int | None = None) -> None:
        if not self._worker_signals_enabled():
            trace_event("compare_drop", job=job_id, reason="apply_signals_off")
            self._release_compare_busy_ui_if_idle(reason="apply_signals_off")
            return
        if job_id is not None and job_id != self._active_compare_job_id:
            trace_event(
                "compare_drop",
                job=job_id,
                active=self._active_compare_job_id,
                reason="apply_stale_job",
            )
            self._compare_job_roots.pop(int(job_id), None)
            self._release_compare_busy_ui_if_idle(reason="apply_stale_job")
            return
        if self._compare_job_scan_root_stale(job_id):
            trace_event("compare_drop", job=job_id, reason="stale_scan_root")
            if job_id is not None:
                self._compare_job_roots.pop(int(job_id), None)
            self._release_compare_busy_ui_if_idle(reason="stale_scan_root")
            return
        if job_id is not None:
            self._compare_job_roots.pop(int(job_id), None)
        if getattr(self, "_ram_clear_busy", False):
            trace_event("compare_drop", job=job_id, reason="ram_clear_busy")
            # Active job finished during RAM Clear — blank owns the UI, but still
            # clear latches so a later refresh is not blocked by pending flags.
            self._cabinet_compare_prefetch = False
            self._compare_ui_pending = False
            self._cabinet_compare_force_pending = False
            self._manual_meters_refresh = False
            if self._auto_fetch_round_active:
                self._end_auto_fetch_round(repaint=False)
            return
        defer_ui_for_local_pair = False
        try:
            if isinstance(state_obj, dict):
                # IMPORTANT: keep keys normalized/lowercase for alias lookup
                # (loader returns normalized keys like "coinin"; uppercasing breaks lookups).
                incoming = {str(k).strip(): str(v) for k, v in state_obj.items()}
                currency = incoming.pop("__currencyid__", "")
                for meta in [k for k in incoming if str(k).startswith("__")]:
                    incoming.pop(meta, None)
                if incoming:
                    self._machine_state = incoming
                    self._apply_state_currency(currency)
                    self._machine_state_loaded = True
                    self._loaded_cabinet_scan_root = self._scan_root
                    self._machine_state_loaded_at = time.monotonic()
                    self._machine_empty_retry_armed = False
                    self._machine_empty_retry_count = 0
                    self._discard_stale_machine_after_ram_clear = False
                    self._mark_meters_fetched()
                    self._share_recovery_pending = False
                    self._share_recovery_reloading = False
                    defer_ui_for_local_pair = False
                    if self._share_meters_without_com():
                        # Local EGM or remote share-only (COM blocked): compare
                        # gm2au vs SASControler off the UI thread.
                        # Defer the table rebuild until that paired read lands;
                        # painting now (then again from LocalDiff) freezes the UI
                        # under Auto fetch during play.
                        self._local_diff_running = True
                        defer_ui_for_local_pair = True
                        self._set_busy_progress_active(True)
                        self._local_diff_job_id += 1
                        self._pool.start(
                            _LocalDiffTask(
                                self._scan_root,
                                self._local_diff_signals,
                                self._local_diff_job_id,
                            )
                        )
                    elif self._auto_fetch_round_may_close():
                        # Files-only / no COM round: Machine alone settles the pair.
                        # repaint=False: _run_cabinet_ui_refresh() below rebuilds.
                        self._end_auto_fetch_round(repaint=False)
                else:
                    kept = self._keep_machine_on_empty_reload()
                    if not kept:
                        self._machine_state = {}
                        self._machine_state_loaded = False
                        self._machine_state_loaded_at = 0.0
                        # Empty state on a UNC root usually means no share access
                        # (SMB probes fail silently as "missing"). Watch the share and
                        # reload Machine automatically once it becomes reachable —
                        # unless this very load was the recovery retry (share is
                        # reachable but the cabinet genuinely has no state files).
                        if self._share_recovery_reloading:
                            self._share_recovery_reloading = False
                        elif self._should_arm_share_recovery_after_empty():
                            self._arm_share_recovery()
                    if self._auto_fetch_round_may_close():
                        self._end_auto_fetch_round(repaint=False)
            else:
                kept = self._keep_machine_on_empty_reload()
                if not kept:
                    self._machine_state = {}
                    self._machine_state_loaded = False
                    self._machine_state_loaded_at = 0.0
                if self._auto_fetch_round_may_close():
                    self._end_auto_fetch_round(repaint=False)
            # Only the *paired* post-COM Machine re-read may close the round.
            # If a force compare is still pending, this landing is the pre-COM
            # snapshot — keep SYNCING until the queued re-read arrives.
            if (
                self._auto_fetch_round_resynced
                and not self._cabinet_compare_force_pending
            ):
                # One close path only. Hand-clearing the two flags here left
                # `_auto_fetch_round_started_mono` set, so the round clock kept
                # running against a round that had already ended.
                # repaint=False: _run_cabinet_ui_refresh() below rebuilds the
                # table, and a second full rebuild here froze Auto fetch.
                self._end_auto_fetch_round(repaint=False)
            # Refresh Machine as soon as cabinet XML loads — except when the
            # paired side is still on its way: local EGM (LocalDiff paints both
            # columns from one read) and a remote Auto fetch round whose COM
            # capture has not landed yet. The cabinet XML read is ~40 ms while a
            # SAS capture takes seconds, so painting Machine alone there makes
            # the meters of one game increment column by column, seconds apart.
            deferred = defer_ui_for_local_pair or self._machine_paint_waits_for_sas()
            trace_event(
                "machine_landed",
                deferred=bool(deferred),
                reason="local_pair" if defer_ui_for_local_pair else "await_sas",
                round_active=bool(self._auto_fetch_round_active),
            )
            if deferred:
                self._cabinet_ui_refresh_pending = True
            else:
                self._run_cabinet_ui_refresh()
        except Exception:
            traceback.print_exc()
        finally:
            if not self._local_diff_running:
                self._manual_meters_refresh = False
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)
            self._cabinet_compare_prefetch = False
            self._compare_ui_pending = False
            self._update_prefetch_status()
            if self._cabinet_compare_force_pending and self._worker_signals_enabled():
                if self._local_diff_running:
                    # Do not pile a second Machine load on top of the paired
                    # local read — that storm froze the UI during play.
                    pass
                else:
                    self._cabinet_compare_force_pending = False
                    self._invalidate_machine_cabinet_cache()
                    auto_on = bool(
                        getattr(self, "_auto_fetch_toggle", None)
                        and self._auto_fetch_toggle.isChecked()
                    )
                    # Keep the round open across the deferred paired re-read.
                    if self._auto_fetch_round_active:
                        self._auto_fetch_round_resynced = True
                    QTimer.singleShot(
                        0,
                        self,
                        lambda: self._begin_cabinet_compare(
                            prefetch=True,
                            force=True,
                            immediate_paint=not auto_on,
                        ),
                    )

    def _run_cabinet_ui_refresh(self) -> None:
        trace_event("paint", source="cabinet_ui_refresh")
        self._cabinet_ui_refresh_pending = False
        self._seed_verify_rows_from_cabinet_if_needed()
        self.setUpdatesEnabled(False)
        try:
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
        # Theme catalog hits disk/SMB on the UI thread and freezes Accounting
        # right-click under Auto fetch. Throttle; skip when Game tab is unused.
        self._schedule_game_theme_catalog_reload()

    def _on_worker_error(self, msg: str, job_id: int | None = None) -> None:
        if not self._worker_signals_enabled():
            self._release_compare_busy_ui_if_idle(reason="error_signals_off")
            return
        if job_id is not None and job_id != self._active_compare_job_id:
            self._compare_job_roots.pop(int(job_id), None)
            self._release_compare_busy_ui_if_idle(reason="error_stale_job")
            return
        if self._compare_job_scan_root_stale(job_id):
            if job_id is not None:
                self._compare_job_roots.pop(int(job_id), None)
            self._release_compare_busy_ui_if_idle(reason="error_stale_scan_root")
            return
        if job_id is not None:
            self._compare_job_roots.pop(int(job_id), None)
        trace_event("compare_error", job=job_id, msg=str(msg)[:160])
        err_status = f"Machine load failed: {msg}"
        try:
            try:
                sys.__stdout__.write(f"\n[UI-THREAD] Worker error received: {msg}\n")
                sys.__stdout__.flush()
            except Exception:
                pass
            print(f"[ERROR] {msg}")
            was_prefetch = self._cabinet_compare_prefetch
            from gui.app_logging import get_logger

            get_logger("gui.sas_verify").error("cabinet compare error: %s", msg)
            if share_error_is_access(msg):
                self._arm_share_recovery()
            if not was_prefetch:
                QMessageBox.information(self, "SAS accounting verification", msg)
            elif "Connection Failed" in (msg or "") or "SMB" in (msg or "").upper():
                # Tier 2 (remote share) failed during prefetch → offer local D:\.
                QTimer.singleShot(
                    0,
                    self,
                    lambda m=msg: self._prompt_local_d_scan_root(
                        reason=f"Remote share failed: {m}",
                    ),
                )
        finally:
            kept = self._keep_machine_on_empty_reload()
            if not kept:
                self._machine_state = {}
                self._machine_state_loaded = False
                self._loaded_cabinet_scan_root = ""
                self._machine_state_loaded_at = 0.0
                self._render(
                    parsed_rows=self._last_parsed_rows, allow_machine_lookup=False
                )
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)
            self._cabinet_compare_prefetch = False
            self._compare_ui_pending = False
            self._manual_meters_refresh = False
            self._meters_ui_pending = False
            self._cabinet_compare_force_pending = False
            self._auto_fetch_round_resynced = False
            if self._auto_fetch_round_may_close():
                self._end_auto_fetch_round()
            # Do not call bare _update_prefetch_status() — local idle used to
            # overwrite the failure with "Loading Machine meters…".
            self._update_prefetch_status(err_status)

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

    def _apply_state_currency(self, currency_id: str) -> None:
        """
        Adopt the EGM's declared currency (currencyId from DeviceManagerData.xml,
        delivered with the cabinet state — zero extra I/O, never blocks meters).
        """
        cur = _currency_from_id(currency_id)
        if cur is None or cur == self._currency:
            return
        self._currency = cur
        self._update_dollar_toggle_label()
        self._apply_value_headers()

    def _update_dollar_toggle_label(self) -> None:
        sym = self._currency.symbol or "$"
        blocked = self._dollar_toggle.blockSignals(True)
        self._dollar_toggle.setText(f"Show {sym.strip() or '$'}")
        self._dollar_toggle.blockSignals(blocked)

    def _on_dollar_toggle(self, checked: bool) -> None:
        self._show_dollars = checked
        self._save_show_dollars_pref(checked)
        self._refresh_value_columns()

    def _auto_fetch_enabled(self) -> bool:
        """True only while the Auto fetch checkbox is on."""
        toggle = getattr(self, "_auto_fetch_toggle", None)
        return bool(toggle is not None and toggle.isChecked())

    def _load_auto_fetch_pref(self) -> bool:
        """Last Auto fetch choice (default on for first run)."""
        try:
            raw = _sas_verify_settings().value(f"sasVerify/{_KEY_AUTO_FETCH}", True)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(int(raw))
            text = str(raw).strip().lower()
            if text in ("0", "false", "no", "off"):
                return False
            if text in ("1", "true", "yes", "on"):
                return True
        except Exception:
            pass
        return True

    def _save_auto_fetch_pref(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = self._auto_fetch_enabled()
        try:
            s = _sas_verify_settings()
            s.setValue(f"sasVerify/{_KEY_AUTO_FETCH}", bool(checked))
            s.sync()
        except Exception:
            pass

    def _load_show_dollars_pref(self) -> bool:
        """Last Show $ choice (default on for first run)."""
        try:
            raw = _sas_verify_settings().value(f"sasVerify/{_KEY_SHOW_DOLLARS}", True)
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)):
                return bool(int(raw))
            text = str(raw).strip().lower()
            if text in ("0", "false", "no", "off"):
                return False
            if text in ("1", "true", "yes", "on"):
                return True
        except Exception:
            pass
        return True

    def _save_show_dollars_pref(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = bool(self._show_dollars)
        try:
            s = _sas_verify_settings()
            s.setValue(f"sasVerify/{_KEY_SHOW_DOLLARS}", bool(checked))
            s.sync()
        except Exception:
            pass

    def _restore_meter_tab_pref(self) -> None:
        """Select the last meter tab used on this machine (by tab title)."""
        tabs = getattr(self, "_meter_tabs", None)
        if tabs is None:
            return
        try:
            name = str(
                _sas_verify_settings().value(f"sasVerify/{_KEY_METER_TAB}", "") or ""
            ).strip()
        except Exception:
            name = ""
        if not name:
            return
        for i in range(tabs.count()):
            if (tabs.tabText(i) or "").strip() == name:
                if self._meter_tab_is_visible(i):
                    blocked = tabs.blockSignals(True)
                    tabs.setCurrentIndex(i)
                    tabs.blockSignals(blocked)
                break

    def _save_meter_tab_pref(self) -> None:
        tabs = getattr(self, "_meter_tabs", None)
        if tabs is None:
            return
        try:
            idx = tabs.currentIndex()
            if idx < 0 or not self._meter_tab_is_visible(idx):
                return
            name = (tabs.tabText(idx) or "").strip()
            if not name:
                return
            s = _sas_verify_settings()
            s.setValue(f"sasVerify/{_KEY_METER_TAB}", name)
            s.sync()
        except Exception:
            pass

    def _save_session_prefs(self) -> None:
        """Persist checkbox / tab / scan-root choices for the next launch on this PC."""
        self._save_auto_fetch_pref()
        self._save_show_dollars_pref()
        self._save_meter_tab_pref()
        save_persisted_scan_root(self._scan_root_text())

    def _stop_auto_fetch_machinery(self) -> None:
        """Halt timer/queue/watcher and drop any open auto-fetch round."""
        self._auto_fetch_timer.stop()
        if self._auto_fetch_queue_timer.isActive():
            self._auto_fetch_queue_timer.stop()
        if self._settle_repaint_timer.isActive():
            self._settle_repaint_timer.stop()
        if self._meter_flash_timer.isActive():
            self._meter_flash_timer.stop()
        self._auto_fetch_refresh_queued = False
        self._auto_fetch_poll_running = False
        self._auto_fetch_pair_wait_started_mono = 0.0
        self._clear_auto_fetch_file_watcher()
        if self._auto_fetch_round_active:
            self._end_auto_fetch_round(repaint=False)

    def _on_auto_fetch_toggle(self, checked: bool) -> None:
        self._save_auto_fetch_pref(checked)
        if checked:
            self._busy_progress_suppressed = False
            # Fresh baseline: the first poll only records the current mtime.
            self._auto_fetch_baseline_mtime = 0.0
            self._auto_fetch_baseline_root = ""
            self._sync_auto_fetch_watch_mode()
            self._auto_fetch_timer.start()
            # Queued, never run inline — starting a cabinet load / COM capture
            # here would re-enter the dialog from inside the toggled handler.
            self._queue_auto_fetch_refresh()
        else:
            self._stop_auto_fetch_machinery()
            # Mark busy helpers suppressed (bar removed; flag kept for call sites).
            self._suppress_busy_progress()
            self._update_prefetch_status(
                "Auto fetch off — click Refresh Meters for a manual capture."
            )

    def _auto_fetch_uses_local_disk(self) -> bool:
        """True when scan root is on a local drive (instant FS watcher path)."""
        from network.goldclub_paths import (
            local_filesystem_path_for_scan_root,
            path_is_local_filesystem,
        )

        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            return False
        if self._local_files_only_mode():
            return True
        local = local_filesystem_path_for_scan_root(sr)
        return bool(local) and path_is_local_filesystem(local)

    def _clear_auto_fetch_file_watcher(self) -> None:
        watcher = getattr(self, "_auto_fetch_fs_watcher", None)
        if watcher is None:
            return
        for path in list(watcher.directories()) + list(watcher.files()):
            watcher.removePath(path)

    def _sync_auto_fetch_watch_mode(self) -> None:
        """Arm QFileSystemWatcher on local state dirs; fast poll as backup.

        Remote UNC keeps the slower SMB poll only — directory watchers over
        admin shares miss AFT writes and fire spuriously.
        """
        local = self._auto_fetch_uses_local_disk()
        self._auto_fetch_timer.setInterval(
            AUTO_FETCH_POLL_INTERVAL_LOCAL_MS
            if local
            else AUTO_FETCH_POLL_INTERVAL_MS
        )
        self._clear_auto_fetch_file_watcher()
        if not local or not self._auto_fetch_toggle.isChecked():
            return
        from network.accounting_state_loader import device_state_watch_directories

        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        for directory in device_state_watch_directories(sr):
            path = str(directory)
            if path not in self._auto_fetch_fs_watcher.directories():
                self._auto_fetch_fs_watcher.addPath(path)

    def _on_auto_fetch_fs_changed(self, path: str = "") -> None:
        """Instant local-disk notify: queue a meter reload (AFT / DeviceManager)."""
        if not self._worker_signals_enabled() or not self._auto_fetch_toggle.isChecked():
            return
        # Windows sometimes drops a watch after a replace-write — re-arm.
        if path and path not in self._auto_fetch_fs_watcher.directories():
            try:
                if Path(path).is_dir():
                    self._auto_fetch_fs_watcher.addPath(path)
            except OSError:
                pass
        self._queue_auto_fetch_refresh()

    def _on_auto_fetch_timer(self) -> None:
        if not self._worker_signals_enabled() or not self._auto_fetch_toggle.isChecked():
            return
        if self._auto_fetch_round_active:
            started = float(getattr(self, "_auto_fetch_round_started_mono", 0.0) or 0.0)
            age = (time.monotonic() - started) if started else 0.0
            if started and age >= AUTO_FETCH_ROUND_IDLE_S and self._auto_fetch_round_idle():
                # Every worker finished but no completion path closed the round.
                # Waiting for the stale cap held SYNCING for a minute and a half,
                # which also postponed the row highlight until long after the
                # meter moved. Settle now; the cap below stays as a last resort.
                from gui.app_logging import get_logger

                get_logger("gui.sas_verify").info(
                    "auto-fetch round idle after %.1fs — settling", age
                )
                self._end_auto_fetch_round()
                self._set_busy_progress_active()
                self._update_prefetch_status()
            elif started and age >= AUTO_FETCH_ROUND_STALE_S:
                # COM / Machine / LocalDiff round never settled — release latches
                # so SYNCING and the busy bar cannot stick forever.
                from gui.app_logging import get_logger

                get_logger("gui.sas_verify").warning(
                    "auto-fetch round stale after %.0fs — releasing latches "
                    "(local_diff=%s force_pending=%s meter_fetch=%s compare=%s)",
                    AUTO_FETCH_ROUND_STALE_S,
                    self._local_diff_running,
                    self._cabinet_compare_force_pending,
                    self._meter_fetch_running(),
                    self._compare_running(),
                )
                if self._local_diff_running:
                    # Drop the wedged task's late signal — it must not close a
                    # future round with data from this dead one.
                    self._local_diff_job_id += 1
                self._local_diff_running = False
                self._cabinet_compare_force_pending = False
                self._manual_meters_refresh = False
                self._end_auto_fetch_round()
                self._set_busy_progress_active()
                self._update_prefetch_status()
        self._orphan_stale_meter_fetch_if_needed()
        if self._compare_running():
            started = float(getattr(self, "_compare_started_mono", 0.0) or 0.0)
            if started and (time.monotonic() - started) >= 45.0:
                # Wedged Machine load blocks Auto fetch forever — orphan and
                # force a fresh round on the next queue tick.
                self._compare_job_id += 1
                self._active_compare_job_id = self._compare_job_id
                self._stop_compare_thread(wait_ms=200)
                self._cabinet_compare_force_pending = True
                QTimer.singleShot(
                    0,
                    self,
                    lambda: self._begin_cabinet_compare(
                        prefetch=True,
                        force=True,
                        immediate_paint=not self._auto_fetch_toggle.isChecked(),
                    ),
                )
            return
        if self._auto_fetch_poll_running:
            started = float(getattr(self, "_auto_fetch_poll_started_mono", 0.0) or 0.0)
            if started and (time.monotonic() - started) >= AUTO_FETCH_POLL_STALE_S:
                # SMB mtime poll never returned — clear the latch so polling resumes.
                self._auto_fetch_poll_running = False
                self._auto_fetch_poll_started_mono = 0.0
            else:
                return
        # Edit box wins (same precedence as compare/fetch paths) so a freshly
        # typed root is watched immediately, not after the next compare.
        scan_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not scan_root:
            return
        # Re-sync watcher targets if the scan root moved to a local tree.
        if self._auto_fetch_uses_local_disk() and not self._auto_fetch_fs_watcher.directories():
            self._sync_auto_fetch_watch_mode()
        self._auto_fetch_poll_running = True
        self._auto_fetch_poll_started_mono = time.monotonic()
        self._pool.start(_StateMtimePollTask(scan_root, self._auto_fetch_signals))

    def _on_state_mtime_polled(self, mtime: float, scan_root: str) -> None:
        self._auto_fetch_poll_running = False
        self._auto_fetch_poll_started_mono = 0.0
        if not self._worker_signals_enabled() or not self._auto_fetch_toggle.isChecked():
            return
        current_root = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if current_root and scan_root != current_root:
            # Stale in-flight poll from before a scan-root switch — its mtime
            # belongs to another cabinet and must not trigger a fetch here.
            return
        if scan_root != self._auto_fetch_baseline_root:
            # Root changed since the baseline was taken — start over.
            self._auto_fetch_baseline_root = scan_root
            self._auto_fetch_baseline_mtime = mtime if mtime > 0.0 else 0.0
            self._sync_auto_fetch_watch_mode()
            return
        self._auto_fetch_baseline_mtime, should_fetch = evaluate_auto_fetch_poll(
            self._auto_fetch_baseline_mtime, mtime
        )
        if should_fetch:
            self._queue_auto_fetch_refresh()

    def _auto_fetch_min_refresh_s(self) -> float:
        if self._auto_fetch_uses_local_disk():
            if self._burst_mode:
                return AUTO_FETCH_MIN_REFRESH_LOCAL_BURST_S
            return AUTO_FETCH_MIN_REFRESH_LOCAL_S
        return AUTO_FETCH_MIN_REFRESH_S

    def _flash_envelope(self) -> tuple[float, float]:
        """``(duration_s, hold_s)`` for the current play rate."""
        if self._burst_mode:
            return METER_FLASH_BURST_DURATION_S, METER_FLASH_BURST_HOLD_S
        return METER_FLASH_DURATION_S, METER_FLASH_HOLD_S

    def _sync_flash_timer_interval(self) -> None:
        want = METER_FLASH_BURST_TICK_MS if self._burst_mode else METER_FLASH_TICK_MS
        if self._meter_flash_timer.interval() != want:
            self._meter_flash_timer.setInterval(want)

    def _note_meter_increase_for_burst(
        self, code: str, prev_raw: str, new_raw: str
    ) -> None:
        """Accumulate per-paint deltas used by the burst rate estimator / strip."""
        delta = meter_value_delta(prev_raw, new_raw)
        if delta <= 0:
            return
        self._burst_paint_increased = True
        rid = (code or "").strip().upper()
        if rid == _BURST_GAMES_CODE:
            self._burst_paint_delta_games += delta
        elif rid == _BURST_COININ_CODE:
            self._burst_paint_delta_coinin += delta

    def _begin_burst_paint_tracking(self) -> None:
        self._burst_paint_increased = False
        self._burst_paint_delta_games = 0
        self._burst_paint_delta_coinin = 0

    def _finish_burst_paint_tracking(self) -> None:
        """Record a landing when this paint moved meters; refresh burst UI."""
        if not self._burst_paint_increased:
            self._maybe_exit_burst_on_idle()
            self._refresh_burst_activity_strip()
            return
        now = time.monotonic()
        self._burst_landing_times.append(now)
        if len(self._burst_landing_times) > BURST_LANDING_RING_SIZE:
            self._burst_landing_times = self._burst_landing_times[-BURST_LANDING_RING_SIZE:]
        was_on = bool(self._burst_mode)
        now_on = burst_mode_should_be_on(
            self._burst_landing_times, now=now, currently_on=was_on
        )
        if now_on and not was_on:
            self._burst_mode = True
            self._burst_delta_games = 0
            self._burst_delta_coinin = 0
            self._sync_flash_timer_interval()
            trace_event(
                "burst_on",
                rate=round(estimate_landing_rate(self._burst_landing_times), 3),
            )
        elif was_on and not now_on:
            self._burst_mode = False
            self._burst_delta_games = 0
            self._burst_delta_coinin = 0
            self._sync_flash_timer_interval()
            trace_event("burst_off")
        if self._burst_mode:
            self._burst_delta_games += int(self._burst_paint_delta_games)
            self._burst_delta_coinin += int(self._burst_paint_delta_coinin)
            trace_event(
                "burst_rate",
                rate=round(estimate_landing_rate(self._burst_landing_times), 3),
                delta_games=self._burst_delta_games,
                delta_coinin=self._burst_delta_coinin,
            )
        self._refresh_burst_activity_strip()

    def _refresh_burst_activity_strip(self) -> None:
        lbl = getattr(self, "_burst_activity_label", None)
        if lbl is None:
            return
        if not self._burst_mode or not self._burst_landing_times:
            if lbl.isVisible() or lbl.text():
                lbl.clear()
                lbl.setFixedHeight(0)
                lbl.hide()
            return
        now = time.monotonic()
        # Exit hysteresis may clear burst between paints; keep strip while on.
        rate = estimate_landing_rate(self._burst_landing_times)
        last_ago = max(0.0, now - float(self._burst_landing_times[-1]))
        text = format_burst_activity_strip(
            games_per_s=rate,
            delta_games=self._burst_delta_games,
            delta_coinin=self._burst_delta_coinin,
            last_ago_s=last_ago,
        )
        lbl.setText(text)
        lbl.setToolTip(text)
        lbl.setFixedHeight(18)
        lbl.show()

    def _maybe_exit_burst_on_idle(self) -> None:
        """Flash ticks / idle path: drop burst after exit idle with no landings."""
        if not self._burst_mode:
            return
        now = time.monotonic()
        if burst_mode_should_be_on(
            self._burst_landing_times, now=now, currently_on=True
        ):
            return
        self._burst_mode = False
        self._burst_delta_games = 0
        self._burst_delta_coinin = 0
        self._sync_flash_timer_interval()
        trace_event("burst_off")
        self._refresh_burst_activity_strip()

    def _auto_fetch_refresh_delay_now(self) -> float:
        busy = (
            self._meter_fetch_running()
            or self._compare_running()
            or self._local_diff_running
            or self._manual_meters_refresh
            or self._auto_fetch_round_active
        )
        return auto_fetch_refresh_delay_s(
            now_mono=time.monotonic(),
            last_refresh_mono=self._auto_fetch_last_force_mono,
            busy=busy,
            min_refresh_s=self._auto_fetch_min_refresh_s(),
        )

    def _queue_auto_fetch_refresh(self) -> None:
        if not self._auto_fetch_enabled():
            self._auto_fetch_refresh_queued = False
            if self._auto_fetch_queue_timer.isActive():
                self._auto_fetch_queue_timer.stop()
            return
        self._auto_fetch_refresh_queued = True
        delay_s = self._auto_fetch_refresh_delay_now()
        self._auto_fetch_queue_timer.start(max(1, int(round(delay_s * 1000.0))))

    def _run_queued_auto_fetch_refresh(self) -> None:
        if self._auto_fetch_queue_timer.isActive():
            self._auto_fetch_queue_timer.stop()
        if not self._auto_fetch_enabled():
            self._auto_fetch_refresh_queued = False
            return
        if not self._auto_fetch_refresh_queued:
            return
        delay_s = self._auto_fetch_refresh_delay_now()
        if delay_s > 0.0:
            # Still spaced out / still busy — reschedule rather than run now.
            self._auto_fetch_queue_timer.start(max(1, int(round(delay_s * 1000.0))))
            return
        # On-cabinet / local disk: one paired LocalDiff only. The old path ran
        # CompareWorker then LocalDiff (two full parses) and left the round open
        # until the idle fuse — that alone added ~5 s after the file already
        # existed. Also wait until gm2au *and* SASControler1 have written when
        # possible; reading the early side alone is the random gm2u MISMATCH.
        if self._share_meters_without_com() or self._offline_log_scan():
            self._run_queued_local_pair_refresh()
            return
        self._auto_fetch_refresh_queued = False
        self._auto_fetch_last_force_mono = time.monotonic()
        self._auto_fetch_round_active = True
        self._auto_fetch_round_resynced = False
        self._auto_fetch_round_started_mono = time.monotonic()
        trace_event("round_open")
        try:
            self._invalidate_machine_cabinet_cache()
            self._begin_cabinet_compare(
                prefetch=True, force=True, immediate_paint=False
            )
            started = bool(self._begin_meter_fetch(prefetch=True, force=True))
            if (
                not started
                and not self._meter_fetch_running()
                and self._auto_fetch_round_may_close()
            ):
                # COM could not start (port busy / skipped) — settle on Machine.
                self._end_auto_fetch_round()
        except Exception:
            from gui.app_logging import get_logger

            get_logger("gui.sas_verify").exception(
                "auto-fetch refresh failed — ending stuck round"
            )
            self._end_auto_fetch_round()
            self.ui.compare_btn.setEnabled(True)
            self.ui.compare_btn.setText("Compare")
            self._btn_get_meters.setEnabled(True)
            self._sync_get_meters_button_label()
            self._set_busy_progress_active()
            self._update_prefetch_status(
                "Auto fetch failed to start — click Refresh Meters to retry."
            )

    def _run_queued_local_pair_refresh(self) -> None:
        """Local EGM Auto fetch: wait for both state folders, then one LocalDiff."""
        from network.accounting_state_loader import (
            device_source_mtimes,
            local_meter_pair_ready,
        )

        if not self._auto_fetch_enabled():
            self._auto_fetch_refresh_queued = False
            return
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            self._auto_fetch_refresh_queued = False
            return
        if self._local_diff_running or self._compare_running():
            self._auto_fetch_queue_timer.start(AUTO_FETCH_LOCAL_PAIR_RETRY_MS)
            return
        current = device_source_mtimes(sr)
        baseline = getattr(self, "_auto_fetch_source_mtimes", None) or {}
        wait_started = float(
            getattr(self, "_auto_fetch_pair_wait_started_mono", 0.0) or 0.0
        )
        # Start the wait clock on the first pass even when neither folder has
        # written yet — otherwise a silent root spins the 150 ms retry forever.
        if not wait_started:
            self._auto_fetch_pair_wait_started_mono = time.monotonic()
            wait_started = self._auto_fetch_pair_wait_started_mono
        waited_s = time.monotonic() - wait_started
        ready, _saw = local_meter_pair_ready(
            baseline=baseline,
            current=current,
            waited_s=waited_s,
            max_wait_s=AUTO_FETCH_LOCAL_PAIR_WAIT_S,
        )
        if not ready:
            if waited_s >= AUTO_FETCH_LOCAL_PAIR_WAIT_S:
                # Still not ready after the budget — drop the queue latch.
                self._auto_fetch_refresh_queued = False
                self._auto_fetch_pair_wait_started_mono = 0.0
                return
            # Keep the queue latch; retry until the lagging folder writes or
            # the pair-wait budget expires. Do not open a SYNCING round yet.
            self._auto_fetch_refresh_queued = True
            self._auto_fetch_queue_timer.start(AUTO_FETCH_LOCAL_PAIR_RETRY_MS)
            return
        self._auto_fetch_refresh_queued = False
        self._auto_fetch_last_force_mono = time.monotonic()
        self._auto_fetch_pair_wait_started_mono = 0.0
        self._auto_fetch_source_mtimes = current
        self._auto_fetch_round_active = True
        self._auto_fetch_round_resynced = False
        self._auto_fetch_round_started_mono = time.monotonic()
        trace_event("round_open", local_pair=True)
        try:
            self._begin_local_pair_diff()
        except Exception:
            from gui.app_logging import get_logger

            get_logger("gui.sas_verify").exception(
                "local pair refresh failed — ending stuck round"
            )
            self._end_auto_fetch_round()

    def _begin_local_pair_diff(self) -> None:
        """Start the single paired gm2au/SASControler1 read for local Auto fetch."""
        if self._local_diff_running:
            return
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            self._end_auto_fetch_round()
            return
        self._local_diff_running = True
        self._set_busy_progress_active(True)
        self._local_diff_job_id += 1
        self._pool.start(
            _LocalDiffTask(sr, self._local_diff_signals, self._local_diff_job_id)
        )

    def _resync_machine_after_auto_fetch_capture(self) -> None:
        """A forced COM capture just landed mid-round: re-read Machine to pair with it.

        The slow side (SAS/COM) finishes last, so the fast side (Machine
        snapshot) is re-read again once it does, rather than trusting the
        earlier read from before the capture started.
        """
        if not self._auto_fetch_toggle.isChecked():
            # Nothing else is coming (Auto fetch off) — stop holding verdicts.
            self._end_auto_fetch_round()
            return
        self._auto_fetch_round_resynced = True
        QTimer.singleShot(0, self, self._begin_paired_machine_resync)

    def _begin_paired_machine_resync(self) -> None:
        """Start the post-capture Machine re-read, or release the round if it cannot start.

        ``_begin_cabinet_compare`` has several early exits — no scan root, or a
        load already in flight that it declines to restart. Because
        ``_auto_fetch_round_resynced`` blocks ``_auto_fetch_round_may_close()``,
        any exit that leaves nothing in flight used to hold the round open with
        every latch clear, freezing the table on SYNCING and the busy bar until
        the 90 s watchdog released it.
        """
        if not self._worker_signals_enabled():
            return
        self._begin_cabinet_compare(prefetch=True, force=True, immediate_paint=False)
        if self._compare_running() or self._cabinet_compare_force_pending:
            return
        # No load started and none queued: the pairing read will never land, so
        # settle on what is already on screen instead of waiting for nothing.
        self._auto_fetch_round_resynced = False
        if self._auto_fetch_round_may_close():
            self._end_auto_fetch_round()

    def _keep_machine_on_empty_reload(self) -> bool:
        """Keep the last good Machine snapshot when a reload returns nothing.

        Mid-write DeviceManager XML and discarded compare jobs used to apply {}
        and clear the column to NO MACHINE while Auto fetch was still running.
        After RAM Clear the old snapshot must never come back — empty/zeros are
        the correct post-clear view until a fresh load lands.
        """
        if getattr(self, "_discard_stale_machine_after_ram_clear", False):
            return False
        if getattr(self, "_ram_clear_busy", False):
            return False
        auto_on = bool(
            getattr(self, "_auto_fetch_toggle", None)
            and self._auto_fetch_toggle.isChecked()
        )
        has_snapshot = bool(self._machine_state) and bool(
            (self._loaded_cabinet_scan_root or "").strip()
        )
        if not auto_on or not has_snapshot:
            return False
        from gui.app_logging import get_logger

        get_logger("gui.sas_verify").warning(
            "empty Machine reload ignored — keeping previous snapshot keys=%s root=%s",
            len(self._machine_state),
            self._loaded_cabinet_scan_root,
        )
        # Soft-expire so the next forced refresh still reloads from disk.
        self._machine_state_loaded = True
        self._machine_state_loaded_at = 0.0
        # A UNC root going empty usually means the share dropped — arm the
        # share watcher too, since the capped retry below eventually gives up.
        if self._should_arm_share_recovery_after_empty():
            self._arm_share_recovery()
        self._schedule_empty_machine_retry()
        return True

    def _schedule_empty_machine_retry(self) -> None:
        if self._machine_empty_retry_armed:
            return
        if not (
            getattr(self, "_auto_fetch_toggle", None)
            and self._auto_fetch_toggle.isChecked()
        ):
            return
        if self._machine_empty_retry_count >= AUTO_FETCH_EMPTY_MACHINE_RETRY_MAX:
            # Root stayed empty across every retry — stop polling it. The
            # mtime watcher / share recovery reloads when files actually change.
            return
        self._machine_empty_retry_armed = True
        delay_ms = AUTO_FETCH_EMPTY_MACHINE_RETRY_MS * (
            2 ** min(self._machine_empty_retry_count, 3)
        )
        QTimer.singleShot(
            delay_ms,
            self,
            self._retry_empty_machine_reload,
        )

    def _retry_empty_machine_reload(self) -> None:
        self._machine_empty_retry_armed = False
        if not self._worker_signals_enabled():
            return
        if not (
            getattr(self, "_auto_fetch_toggle", None)
            and self._auto_fetch_toggle.isChecked()
        ):
            return
        if self._compare_running() or self._local_diff_running:
            return
        if self._machine_loaded_for_current_root() and self._machine_state_loaded_at > 0.0:
            # A later load already succeeded — nothing left to retry.
            self._machine_empty_retry_count = 0
            return
        self._machine_empty_retry_count += 1
        # Machine-only reload. A full _queue_auto_fetch_refresh() round here
        # re-opened COM for a complete SAS capture every cycle — an empty
        # state file is no reason to touch the serial port.
        self._begin_cabinet_compare(prefetch=True, force=True, immediate_paint=False)

    def _end_auto_fetch_round(self, *, repaint: bool = True) -> None:
        if not self._auto_fetch_round_active:
            # A resync latch outliving its round would block the next round's
            # close, so drop it even on this early exit.
            self._auto_fetch_round_resynced = False
            self._auto_fetch_round_started_mono = 0.0
            return
        self._auto_fetch_round_active = False
        self._auto_fetch_round_resynced = False
        self._auto_fetch_round_started_mono = 0.0
        trace_event("round_close", repaint=bool(repaint))
        if self._settle_repaint_timer.isActive():
            self._settle_repaint_timer.stop()
        # Callers that just painted (local paired read) pass repaint=False —
        # a second full-table rebuild on the UI thread was freezing Auto fetch.
        # But that paint often ran while the round was still open, so MATCH
        # rows sat in `_meter_flash_pending` and `_promote_pending_meter_flash`
        # refused them (`sources_settled` was False). Without a settled pass
        # the orange whole-row pulse never started — "animation bar is gone".
        if repaint and self._last_parsed_rows:
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=True)
        else:
            self._promote_pending_flashes_after_settle()

    def _auto_fetch_round_idle(self) -> bool:
        """True when an open round has no worker left that could ever close it.

        Deliberately ignores ``_auto_fetch_round_resynced``: that latch means a
        paired Machine re-read is expected, and by the time a timer tick runs,
        ``_begin_paired_machine_resync`` has already either started that read or
        dropped the latch. A latch with nothing behind it is the wedge this
        catches.
        """
        if not self._auto_fetch_round_active:
            return False
        return not (
            self._meter_fetch_running()
            or self._compare_running()
            or self._local_diff_running
            or self._cabinet_compare_force_pending
        )

    def _auto_fetch_round_may_close(self) -> bool:
        """True when ending the Auto fetch round will not create a false MISMATCH.

        Keep the round open while COM is still capturing, a local paired read is
        running, a post-COM Machine resync is queued, or a force compare is
        waiting on an in-flight load (the pre-COM snapshot must not settle).
        """
        if not self._auto_fetch_round_active:
            return False
        if self._meter_fetch_running():
            return False
        if self._local_diff_running:
            return False
        if self._auto_fetch_round_resynced:
            return False
        if self._cabinet_compare_force_pending:
            return False
        if self._compare_running():
            return False
        return True

    def _compare_sources_settled(self) -> bool:
        """True once both columns are guaranteed to be from the same round.

        False while a COM capture, a local-folder diff, Machine load, or a
        paired re-read is still in flight — a difference seen in that window
        is not trustworthy yet (see compare_status()).
        """
        if self._meter_fetch_running():
            return False
        if self._local_diff_running:
            return False
        if self._compare_running() or self._cabinet_compare_force_pending:
            return False
        if self._auto_fetch_round_resynced:
            return False
        if self._auto_fetch_round_active:
            return False
        return True

    def _arm_settle_repaint(self) -> None:
        # Rapid play: settle timers pile up into permanent SYNCING flicker.
        # Pair-wait already protects false MISMATCH; skip the delayed repaint.
        if self._burst_mode:
            return
        if not self._settle_repaint_timer.isActive():
            self._settle_repaint_timer.start()

    def _on_settle_repaint(self) -> None:
        if not self._worker_signals_enabled():
            return
        if self._last_parsed_rows:
            self._render(parsed_rows=self._last_parsed_rows, allow_machine_lookup=True)

    def _mark_meters_fetched(self) -> None:
        self._fetched_status_label.setText(meters_fetched_status_text(datetime.now()))
        # Next table paint records value changes; MATCH rows pulse as a whole.
        self._meter_flash_arm = True

    def _stop_meter_flash(self) -> None:
        if self._meter_flash_keys:
            trace_event("flash_stop", codes=sorted(self._meter_flash_keys))
        if self._meter_flash_timer.isActive():
            self._meter_flash_timer.stop()
        # Clear fills so the theme white/black background shows through again.
        if self._meter_flash_keys:
            self._meter_flash_started_mono = 0.0
            self._paint_meter_flash_cells()
        self._meter_flash_keys.clear()
        self._meter_flash_started_mono = 0.0
        self._meter_flash_arm = False

    def _meter_flash_strength_now(self) -> float:
        started = float(self._meter_flash_started_mono or 0.0)
        if started <= 0.0 or not self._meter_flash_keys:
            return 0.0
        duration_s, hold_s = self._flash_envelope()
        return meter_flash_strength(
            time.monotonic() - started,
            duration_s=duration_s,
            hold_s=hold_s,
        )

    def _paint_meter_flash_row(
        self, table: QTableWidget, row: int, bg: QColor | None
    ) -> None:
        brush = QBrush() if bg is None else QBrush(bg)
        for col in range(COL_COUNT):
            item = table.item(row, col)
            if item is not None:
                item.setBackground(brush)

    def _paint_meter_flash_cells(self) -> None:
        strength = self._meter_flash_strength_now()
        bg = meter_flash_background(strength)
        flashing = {c.strip().upper() for c in self._meter_flash_keys if c}
        for tbl in self._verify_tables:
            for row in range(tbl.rowCount()):
                code = self._meter_code_for_row(row, tbl)
                if not code or code not in flashing:
                    continue
                self._paint_meter_flash_row(tbl, row, bg)

    def _begin_or_extend_meter_flash(self, codes: set[str]) -> None:
        wanted = {(c or "").strip().upper() for c in codes if (c or "").strip()}
        if not wanted:
            return
        self._meter_flash_keys.update(wanted)
        trace_event("flash_start", codes=sorted(wanted), burst=bool(self._burst_mode))
        # Restart the envelope so newly confirmed meters are easy to spot.
        self._meter_flash_started_mono = time.monotonic()
        self._sync_flash_timer_interval()
        if not self._meter_flash_timer.isActive():
            self._meter_flash_timer.start()
        self._paint_meter_flash_cells()

    def _promote_pending_meter_flash(self, meter_code: str, status: str) -> None:
        """Promote a pending change to a whole-row pulse once both sides MATCH."""
        rid = (meter_code or "").strip().upper()
        if not rid or rid not in self._meter_flash_pending:
            return
        if status == SAS_STATUS_SYNCING or not self._compare_sources_settled():
            return
        self._meter_flash_pending.discard(rid)
        if status == "MATCH":
            self._begin_or_extend_meter_flash({rid})

    def _promote_pending_flashes_after_settle(self) -> None:
        """Start orange pulses that were armed during SYNCING once the round closed.

        Local EGM Auto fetch paints both columns while the round flag is still
        set, so promotion is deferred; closing with ``repaint=False`` used to
        leave those pending codes stranded and the pulse never appeared.
        """
        if not self._meter_flash_pending or not self._compare_sources_settled():
            return
        pending = {(c or "").strip().upper() for c in self._meter_flash_pending if c}
        if not pending:
            return
        matched: set[str] = set()
        for tbl in self._verify_tables:
            for row in range(tbl.rowCount()):
                code = self._meter_code_for_row(row, tbl)
                if not code or code not in pending:
                    continue
                st = tbl.item(row, COL_STATUS)
                if st is not None and (st.text() or "").strip().upper() == "MATCH":
                    matched.add(code)
        # Drop pending codes that will never flash (not MATCH on any verify tab).
        self._meter_flash_pending -= pending
        if matched:
            self._begin_or_extend_meter_flash(matched)

    def _on_meter_flash_tick(self) -> None:
        if not self._worker_signals_enabled():
            self._stop_meter_flash()
            return
        self._maybe_exit_burst_on_idle()
        strength = self._meter_flash_strength_now()
        if strength <= 0.0:
            self._paint_meter_flash_cells()
            self._stop_meter_flash()
            return
        self._paint_meter_flash_cells()

    def _on_local_diff_done(
        self, summary: str, payload: object = None
    ) -> None:
        if not self._worker_signals_enabled():
            return
        if isinstance(payload, dict):
            job = payload.get("__job__", None)
            if job is not None and int(job) != self._local_diff_job_id:
                # Stale task from before a watchdog release — applying it here
                # would close a newer round against this dead round's data.
                return
            # Pop after the job check so a mismatch still leaves the dict intact
            # for a possible later retry of the same payload shape.
            payload.pop("__job__", None)
        self._local_diff_running = False
        self._manual_meters_refresh = False
        painted = False
        try:
            self._local_diff_summary = (summary or "").strip()
            local_sas_state: dict[str, str] | None
            machine_state: dict[str, str] | None
            if isinstance(payload, dict) and (
                "sas" in payload or "machine" in payload
            ):
                raw_sas = payload.get("sas")
                raw_machine = payload.get("machine")
                local_sas_state = raw_sas if isinstance(raw_sas, dict) else None
                machine_state = raw_machine if isinstance(raw_machine, dict) else None
            else:
                local_sas_state = payload if isinstance(payload, dict) else None
                machine_state = None
            # Prefer the Machine snapshot from the same load as SAS so a game that
            # landed between CompareWorker and LocalDiff cannot flash MISMATCH.
            if machine_state:
                cleaned_machine = {
                    str(k).strip(): str(v)
                    for k, v in machine_state.items()
                    if str(v).strip() or str(k).startswith("__")
                }
                currency = cleaned_machine.pop("__currencyid__", "")
                if cleaned_machine:
                    self._machine_state = cleaned_machine
                    self._apply_state_currency(currency)
                    self._machine_state_loaded = True
                    self._loaded_cabinet_scan_root = self._scan_root
                    self._machine_state_loaded_at = time.monotonic()
                    self._machine_empty_retry_count = 0
                    self._mark_meters_fetched()
            applied = False
            if self._resolved_game_client_kind() != "roulette":
                applied = self._apply_local_sas_source(local_sas_state)
            if self._share_meters_without_com() and self._machine_loaded_for_current_root():
                self._seed_verify_rows_from_cabinet_if_needed()
                if self._cabinet_share_meters_mode():
                    status = cabinet_share_only_status(
                        self._scan_root, self._local_diff_summary
                    )
                    if applied:
                        status = (
                            status
                            + " SAS column filled from the cabinet's SASControler "
                            "snapshot."
                        )
                elif applied:
                    status = (
                        "Local EGM mode — Machine meters loaded from local files "
                        f"({self._scan_root}). SAS column filled from the cabinet's "
                        "own SASControler1 snapshot (No COM capture — no host "
                        f"cable to poll on the machine itself). {self._local_diff_summary}"
                    ).rstrip()
                else:
                    status = local_files_only_status(
                        self._scan_root, self._local_diff_summary
                    )
                self._update_prefetch_status(status)
                if self._last_parsed_rows:
                    self._render(
                        parsed_rows=self._last_parsed_rows, allow_machine_lookup=True
                    )
                    painted = True
            if self._cabinet_compare_force_pending:
                # Coalesce the deferred force into one spaced Auto-fetch refresh.
                self._cabinet_compare_force_pending = False
                if (
                    getattr(self, "_auto_fetch_toggle", None)
                    and self._auto_fetch_toggle.isChecked()
                ):
                    self._queue_auto_fetch_refresh()
        finally:
            # Always release the round. A throw in render used to leave SYNCING
            # up until the idle fuse (~3 s of fake "gathering" after the files
            # were already read).
            if self._auto_fetch_round_active and not self._meter_fetch_running():
                self._end_auto_fetch_round(repaint=not painted)
            self._set_busy_progress_active()

    def _local_sas_lookup(self, code: str) -> str:
        """Local scan root only: SAS value from the SASControler* snapshot."""
        if not self._local_sas_state:
            return ""
        try:
            v = (
                getattr(self._vm, "get_gm2u_value_for_sas_code")(code, self._local_sas_state)
                or ""
            ).strip()
        except Exception:
            v = ""
        return v

    def _apply_local_sas_source(self, state: dict[str, str] | None) -> bool:
        """Adopt the cabinet's SASControler* snapshot as the local SAS side.

        An empty/missing read is ignored rather than clearing a prior value —
        nothing scanned yet and a genuinely empty cabinet folder look
        identical from here, and only the former should blank the column.
        """
        cleaned = {
            str(k).strip(): str(v).strip()
            for k, v in (state or {}).items()
            if str(v).strip()
        }
        if not cleaned:
            return False
        self._local_sas_state = cleaned
        return True

    def _sas_capture_expected(self) -> bool:
        """True while a COM/MUX capture could still fill an empty SAS cell.

        False on a local scan root (no host cable to poll — only the
        SASControler* snapshot, already applied) and once any capture attempt
        has finished. A finished poll that omitted a meter (length-0 RX, or
        not in the paste) will not invent it later — show NOT REPORTED, same
        wording as local snapshots that lack the key.

        Also false while IGT holds the COM port (recovery armed): leave rows
        as NOT REPORTED instead of PENDING forever with a blank SAS column.
        """
        if self._share_meters_without_com():
            return False
        if self._cached_meter_result is not None:
            return False
        if getattr(self, "_com_recovery_pending", False):
            return False
        return True

    def _arm_com_recovery(self) -> None:
        """Wait for the SAS tester to release the COM port, then auto-recapture."""
        if self._offline_log_scan():
            return
        self._com_recovery_pending = True
        if not self._recovery_timer.isActive():
            self._recovery_timer.start()
        self._update_prefetch_status()

    def _disarm_com_recovery_if_no_igt(self) -> None:
        """Clear a stale IGT recovery latch when no SAS tester is running."""
        if not self._com_recovery_pending:
            return
        self._com_recovery_pending = False
        if (
            not self._share_recovery_pending
            and not self._game_recovery_pending
            and self._recovery_timer.isActive()
        ):
            self._recovery_timer.stop()

    def _should_arm_share_recovery_after_empty(self) -> bool:
        from network.goldclub_paths import should_arm_share_recovery_after_empty_load

        sr = (self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else "") or (
            self._scan_root or ""
        )
        return should_arm_share_recovery_after_empty_load(str(sr).strip())

    def _compare_elapsed_s(self) -> float:
        started = float(getattr(self, "_compare_started_mono", 0.0) or 0.0)
        if not started or not self._compare_running():
            return 0.0
        return time.monotonic() - started

    def _compare_stuck_for_share_recovery(self) -> bool:
        return (
            self._compare_running()
            and self._compare_elapsed_s() >= SHARE_RECOVERY_COMPARE_STUCK_S
        )

    def _arm_share_recovery(self) -> None:
        """Wait for the cabinet share to become reachable, then auto-reload Machine."""
        sr = (self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else "") or (
            self._scan_root or ""
        )
        sr = str(sr).strip()
        if not sr or not _scan_root_is_unc(sr):
            return
        self._scan_root = sr
        self._share_recovery_pending = True
        if not self._recovery_timer.isActive():
            self._recovery_timer.start()
        self._update_prefetch_status()

    def _arm_game_recovery(self) -> None:
        """EGM booting / game client down: re-fetch all meters when it comes up."""
        if self._local_files_only_mode() or self._offline_log_scan():
            return
        ip = self._cabinet_ip_from_scan_root() or self._scan_root_unc_host()
        if not ip:
            return
        self._game_recovery_pending = True
        self._game_recovery_inconclusive_count = 0
        # Only a down->up transition triggers the refetch. If the client is
        # already running on the first probe, waiting cannot fix the dead link
        # (cable/COM problem) — refetching in a loop would hog the port.
        self._game_recovery_seen_down = False
        if not self._recovery_timer.isActive():
            self._recovery_timer.start()
        self._update_prefetch_status()

    def _on_recovery_timer(self) -> None:
        if not self._worker_signals_enabled():
            self._recovery_timer.stop()
            return
        if (
            not self._com_recovery_pending
            and not self._share_recovery_pending
            and not self._game_recovery_pending
        ):
            self._recovery_timer.stop()
            return
        if self._recovery_probe_running:
            started = float(getattr(self, "_recovery_probe_started_mono", 0.0) or 0.0)
            if started and (time.monotonic() - started) > 60.0:
                # Probe never emitted done — clear the latch and try again.
                self._recovery_probe_running = False
                self._recovery_probe_started_mono = 0.0
            else:
                return
        self._orphan_stale_meter_fetch_if_needed()
        check_com = self._com_recovery_pending and not self._meter_fetch_running()
        # Probe share reachability even while compare is running — a wedged UNC
        # load must not block detecting that the share is back.
        check_share = bool(self._share_recovery_pending)
        check_game = self._game_recovery_pending and not self._meter_fetch_running()
        if not check_com and not check_share and not check_game:
            return
        port = self._resolved_com_port_for_fetch() if check_com else ""
        if check_com and not port:
            # Port disappeared from the enumeration — keep waiting; the tester may
            # still hold it or the cable was pulled.
            check_com = False
            if not check_share and not check_game:
                return
        ip = (
            (self._cabinet_ip_from_scan_root() or self._scan_root_unc_host())
            if check_game
            else ""
        )
        if check_game and not ip:
            check_game = False
            if not check_com and not check_share:
                return
        share_scan_root = (
            (
                (self._scan_root_edit.text() if hasattr(self, "_scan_root_edit") else "")
                or self._scan_root
                or ""
            ).strip()
            if check_share
            else self._scan_root
        )
        self._recovery_probe_running = True
        self._recovery_probe_started_mono = time.monotonic()
        self._pool.start(
            _RecoveryProbeTask(
                port=port,
                scan_root=share_scan_root,
                check_com=check_com,
                check_share=check_share,
                signals=self._recovery_signals,
                ip=ip,
                game_kind=self._resolved_game_client_kind(),
                check_game=check_game,
            )
        )

    def _on_recovery_probe_done(
        self, com_free: bool, share_ok: bool, game_running: bool | None = None
    ) -> None:
        self._recovery_probe_running = False
        if not self._worker_signals_enabled():
            return
        from gui.app_logging import get_logger

        log = get_logger("gui.sas_verify")
        if self._com_recovery_pending and com_free and not self._meter_fetch_running():
            self._com_recovery_pending = False
            self._cabinet_share_only_mode = False
            self._cabinet_share_prompt_asked = False
            self._meter_fetch_error = None
            log.info("COM recovery: port freed — recapturing after settle (full timing)")
            self._update_prefetch_status(
                "COM port freed — capturing meters (full SAS timing)…"
            )
            # Let the tester's driver handle fully close before re-opening: the
            # first open right after release succeeds but can read only idle
            # bytes. Full IGT-like timing from the start — the short sync budget is
            # what used to give up before a door-open / locked EGM answered.
            # what failed here before.
            QTimer.singleShot(2500, self, self._restart_capture_after_com_freed)
        if (
            self._game_recovery_pending
            and game_running is not None
            and not self._meter_fetch_running()
        ):
            if game_running and self._game_recovery_seen_down:
                # EGM finished booting / game client started: refresh ALL meters
                # — the Machine snapshot and the live SAS capture.
                self._game_recovery_pending = False
                self._meter_prefetch_retried = False
                self._meter_fetch_error = None
                log.info("Game recovery: client came up — re-fetching all meters")
                self._update_prefetch_status(
                    "Game client is up — re-fetching all meters…"
                )
                self._invalidate_machine_cabinet_cache()
                if not self._compare_running():
                    self._begin_cabinet_compare(prefetch=True, force=True)
                self._begin_meter_fetch(prefetch=True, force=True)
            elif game_running:
                # Client already up while the link is dead: waiting cannot fix a
                # cable/COM problem — stop instead of looping COM captures.
                self._game_recovery_pending = False
                log.info("Game recovery: client already running — link problem, not booting")
                self._update_prefetch_status(
                    game_link_dead_status(
                        self._game_client_exe_label(),
                        self._cabinet_ip_from_scan_root() or self._scan_root_unc_host(),
                    )
                )
            else:
                self._game_recovery_seen_down = True
            self._game_recovery_inconclusive_count = 0
        elif (
            self._game_recovery_pending
            and game_running is None
            and not self._meter_fetch_running()
        ):
            self._game_recovery_inconclusive_count += 1
            if self._game_recovery_inconclusive_count >= GAME_RECOVERY_INCONCLUSIVE_MAX:
                self._game_recovery_pending = False
                self._game_recovery_inconclusive_count = 0
                log.info(
                    "Game recovery: WinRM probe inconclusive after %s attempts — disarming",
                    GAME_RECOVERY_INCONCLUSIVE_MAX,
                )
                self._update_prefetch_status(
                    game_recovery_probe_failed_status(
                        self._game_client_exe_label(),
                        self._cabinet_ip_from_scan_root() or self._scan_root_unc_host(),
                    )
                )
        if self._share_recovery_pending and share_ok:
            if self._compare_running() and not self._compare_stuck_for_share_recovery():
                pass  # young compare still in flight — keep watching
            else:
                if self._compare_running():
                    log.info(
                        "Share recovery: share back while compare wedged — orphaning load"
                    )
                    self._compare_job_id += 1
                    self._active_compare_job_id = self._compare_job_id
                    self._stop_compare_thread(wait_ms=400)
                self._share_recovery_pending = False
                # A reachable share that still yields no state must not re-arm the
                # watcher (that would loop compare forever on an empty cabinet).
                self._share_recovery_reloading = True
                log.info("Share recovery: scan root reachable — reloading Machine")
                self._invalidate_machine_cabinet_cache()
                self._begin_cabinet_compare(prefetch=True, force=True)
        if (
            not self._com_recovery_pending
            and not self._share_recovery_pending
            and not self._game_recovery_pending
        ):
            self._recovery_timer.stop()

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
        if (
            has_2f
            and act is not None
            and not act.isChecked()
            and not self._2f_autoshow_done
        ):
            act.blockSignals(True)
            act.setChecked(True)
            act.blockSignals(False)
            self._2f_autoshow_done = True
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
        code = (meter_code or "").strip().upper()
        new_raw = "" if (missing_display is not None and not (raw or "").strip()) else (raw or "")
        prev_item = tbl.item(row, col)
        prev_raw = ""
        if prev_item is not None:
            prev_raw = str(prev_item.data(RAW_VALUE_ROLE) or "")
        if missing_display is not None and not (raw or "").strip():
            item = QTableWidgetItem(missing_display)
            item.setData(RAW_VALUE_ROLE, "")
        else:
            item = QTableWidgetItem(self._format_cell_value(meter_code, raw))
            item.setData(RAW_VALUE_ROLE, raw)
        if tooltip:
            item.setToolTip(tooltip)
        # Never touch foreground alpha — text stays fully opaque while the soft
        # whole-row pulse runs. Record a pending change; MATCH promotes it later.
        if (
            self._meter_flash_arm
            and col in _METER_FLASH_WATCH_COLS
            and code
            and meter_value_increased(prev_raw, new_raw)
        ):
            self._meter_flash_pending.add(code)
            # Machine column is the rapid-play signal (local files / SMB).
            # Counting SAS too would double Δgames when both sides land together.
            if col == COL_MACHINE_VALUE:
                self._note_meter_increase_for_burst(code, prev_raw, new_raw)
        if code and col in _METER_FLASH_WATCH_COLS and prev_raw != new_raw:
            trace_event(
                "value_change",
                code=code,
                column=_METER_TRACE_COLUMN_NAMES.get(col, str(col)),
                old=prev_raw,
                new=new_raw,
                # A reload blanks a column and refills it with the same number;
                # only a real increase is a meter the player moved.
                increased=meter_value_increased(prev_raw, new_raw),
            )
        tbl.setItem(row, col, item)
        if code in self._meter_flash_keys:
            bg = meter_flash_background(self._meter_flash_strength_now())
            if bg is None:
                item.setBackground(QBrush())
            else:
                item.setBackground(QBrush(bg))

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
        return [
            r
            for r in parsed_rows
            if str(getattr(r, "meter_id", "") or "").upper() in wanted
        ]

    def _sas_value_for_code(self, code: str) -> str:
        rid = (code or "").strip().upper()
        for row in self._last_parsed_rows:
            if str(getattr(row, "meter_id", "") or "").upper() != rid:
                continue
            sas_v = str(getattr(row, "sas_value_text", "") or "").strip()
            if sas_v:
                return self._normalize_int_for_compare(sas_v) or sas_v
            break
        # Local SASControler snapshot is only a valid SAS side in share/local-files mode.
        if self._share_meters_without_com():
            local_v = self._local_sas_lookup(rid)
            if local_v:
                return self._normalize_int_for_compare(local_v) or local_v
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
        # A meter the snapshot omits, where SAS reports a real zero, is treated
        # as an agreed zero. Aurum publishes most families (vouchers, WAT) as 0
        # even with no device fitted, so the handful it leaves out are zero too
        # — reporting them as unverified was noise, not information.
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
        from network.accounting_state_loader import (
            aggregate_theme_paytable_meters,
            resolve_theme_perf_meters,
        )

        theme_id = self._selected_game_theme_id()
        if theme_id:
            # Catalog Id ("Roulette Game") may not equal DeviceManagerData themeId
            # ("RouletteGame") — resolve via ThemePath / fuzzy match for all games.
            theme_data = resolve_theme_perf_meters(
                self._theme_perf_by_paytable,
                theme_id,
                folder=self._game_catalog_folders.get(theme_id),
            )
            paytable_id = self._selected_game_paytable_id()
            if paytable_id:
                return dict(theme_data.get(paytable_id, {})), True
            return aggregate_theme_paytable_meters(theme_data), True
        if allow_machine_lookup and self._machine_state:
            return dict(self._machine_state), False
        return {}, False

    def _schedule_game_theme_catalog_reload(self, *, force: bool = False) -> None:
        """Debounce Game-tab catalog reloads so Auto fetch cannot freeze the UI.

        ``force=True`` is used when the operator opens the Game tab so per-game
        meters stay available even while Auto fetch skips background reloads.
        """
        auto_on = bool(
            getattr(self, "_auto_fetch_toggle", None)
            and self._auto_fetch_toggle.isChecked()
        )
        # Catalog load is synchronous disk I/O on the UI thread. Skip background
        # reloads under Auto fetch — Accounting freezes mid-play — unless the
        # Game tab explicitly asked for a refresh.
        if auto_on and not force:
            return
        if self._burst_mode and not force:
            return
        now = time.monotonic()
        last = float(getattr(self, "_theme_catalog_loaded_mono", 0.0) or 0.0)
        if last and (now - last) < 30.0 and not force:
            return
        # force still respects a short latch so tab-spam cannot thrash UNC.
        if force and last and (now - last) < 2.0:
            self._update_game_summary()
            return
        QTimer.singleShot(0, self._reload_game_theme_catalog)

    def _reload_game_theme_catalog(self) -> None:
        from network.accounting_state_loader import (
            load_cabinet_game_catalog,
            load_theme_perf_meters_by_paytable,
        )

        # Do not re-probe scan-root remapping (SMB) on the UI thread.
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        if not sr:
            self._theme_perf_by_paytable = {}
            self._game_catalog_folders = {}
            self._game_theme_ids = []
            return
        self._theme_catalog_loaded_mono = time.monotonic()
        try:
            catalog = load_cabinet_game_catalog(sr)
            self._game_catalog_folders = {theme_id: folder for theme_id, folder in catalog}
            self._theme_perf_by_paytable = load_theme_perf_meters_by_paytable(sr)
        except (OSError, NameError, TypeError, ValueError):
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
        self._update_game_summary()

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
            # bool is a subclass of int — reject it; also drop NaN/Inf.
            yield_pct = totals.get("yield_pct")
            hold_pct = totals.get("hold_pct")
            y = (
                float(yield_pct)
                if isinstance(yield_pct, (int, float))
                and not isinstance(yield_pct, bool)
                and math.isfinite(float(yield_pct))
                else None
            )
            h = (
                float(hold_pct)
                if isinstance(hold_pct, (int, float))
                and not isinstance(hold_pct, bool)
                and math.isfinite(float(hold_pct))
                else None
            )
            self._game_yield_chart.set_values(y, h)

    def _reset_master_summary(self) -> None:
        if not self._master_value_labels and self._master_total_credit is None:
            return
        for lbl in self._master_value_labels.values():
            lbl.setText("—")
        if self._master_credit_in_total is not None:
            self._master_credit_in_total.setText("—")
        if self._master_credit_out_total is not None:
            self._master_credit_out_total.setText("—")
        if self._master_total_credit is not None:
            self._master_total_credit.setText("—")
        if self._master_inout_pct is not None:
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
        if self._master_total_credit is not None:
            self._master_total_credit.setText(f"{sym}{totals['total_credit']:.2f}")
        pct = totals["inout_pct"]
        if self._master_inout_pct is not None:
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

    def _on_magicwheel_game_changed(self, _theme: str = "") -> None:
        if not getattr(self, "_magicwheel_handles", None):
            return
        self._update_magicwheel_summary()

    def _update_magicwheel_summary(self, *, allow_machine_lookup: bool | None = None) -> None:
        """Reload Magic Wheel panel from themes config + DeviceManager perf meters."""
        handles = getattr(self, "_magicwheel_handles", None)
        if not handles:
            return
        from gui.magic_wheel_tab import apply_magic_wheel_data
        from network.magic_wheel_loader import (
            MagicWheelData,
            list_magic_wheel_game_themes,
            load_magic_wheel_data,
        )
        from network.accounting_state_loader import load_theme_perf_meters

        if allow_machine_lookup is None:
            allow_machine_lookup = self._machine_state_loaded
        sr = (self._scan_root_edit.text() or self._scan_root or "").strip()
        theme_choice = ""
        game = handles.get("game")
        if game is not None:
            theme_choice = (game.currentText() or "").strip()
        if not sr or not allow_machine_lookup:
            apply_magic_wheel_data(handles, MagicWheelData())
            return
        try:
            data = load_magic_wheel_data(sr, theme_id=theme_choice or None)
            themes = list_magic_wheel_game_themes(load_theme_perf_meters(sr))
        except Exception:
            traceback.print_exc()
            apply_magic_wheel_data(handles, MagicWheelData())
            return
        apply_magic_wheel_data(handles, data, game_themes=themes)

    def _render_table(
        self,
        table: QTableWidget,
        parsed_rows: list[Sas6FRow],
        *,
        allow_machine_lookup: bool,
    ) -> None:
        table.setRowCount(len(parsed_rows))
        any_syncing = False
        for row, r in enumerate(parsed_rows):
            rid = r.meter_id.upper()
            sas_v = r.sas_value_text.strip()
            from_local_sas = False
            # Local SASControler snapshot is only a valid SAS side in share/local-files mode.
            if not sas_v and self._share_meters_without_com():
                sas_v = self._local_sas_lookup(rid)
                from_local_sas = bool(sas_v)
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
            if not has_sas:
                # After RAM clear / soft meters, IGT often omits zero meters from
                # the 6F paste while Machine already resolves to 0. Treat that as
                # MATCH $0 once capture is finished (same unify as SAS=0 filling
                # a missing Machine cell).
                if (
                    (not machine_missing)
                    and mac_norm == "0"
                    and not self._sas_capture_expected()
                ):
                    has_sas = True
                    sas_v = "0"
                    sas_norm = "0"
                    match = True
                    status = compare_status(
                        match=True, sources_settled=self._compare_sources_settled()
                    )
                else:
                    match = False
                    status = missing_sas_status(
                        sas_capture_expected=self._sas_capture_expected()
                    )
            elif not self._machine_state_loaded:
                match = False
                # PENDING only while the cabinet XML worker is still running.
                status = "PENDING" if self._compare_running() else "NO MACHINE"
            elif machine_missing:
                match = False
                status = "NO MACHINE"
            elif rid == "000B" and machine_v:
                from network.meter_comparator import bills_in_meters_match

                match = bills_in_meters_match(sas_norm, mac_norm)
                status = compare_status(match=match, sources_settled=self._compare_sources_settled())
            else:
                try:
                    match = int(mac_norm) == int(sas_norm)
                except ValueError:
                    match = mac_norm == sas_norm
                status = compare_status(match=match, sources_settled=self._compare_sources_settled())
            if status == SAS_STATUS_SYNCING:
                any_syncing = True
            if (
                has_sas
                and (not machine_missing)
                and (not match)
                and status == "MISMATCH"
            ):
                self._had_settled_mismatch = True
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
            sas_tip = (
                "From local SASControler snapshot (no live COM/MUX)."
                if from_local_sas
                else "From RX<= 6F bulk poll in paste."
            )
            self._set_value_item(
                row,
                COL_SAS_6F_VALUE,
                meter_code=rid,
                raw=sas_norm or sas_v,
                missing_display="—" if not has_sas else None,
                tooltip=sas_tip,
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
            elif status in {"PENDING", "NO MACHINE", SAS_STATUS_NOT_REPORTED, SAS_STATUS_SYNCING}:
                st.setForeground(Qt.GlobalColor.darkGray)
            else:
                f = st.font()
                f.setBold(True)
                st.setFont(f)
                st.setForeground(Qt.GlobalColor.red)
            table.setItem(row, COL_STATUS, st)
            # Confirm pending increments only when both sides agree (MATCH).
            self._promote_pending_meter_flash(rid, status)
            if rid in self._meter_flash_keys:
                self._paint_meter_flash_row(
                    table, row, meter_flash_background(self._meter_flash_strength_now())
                )
        if any_syncing:
            self._arm_settle_repaint()
        fit_verify_table_columns(table)
        if self._meter_flash_arm:
            self._meter_flash_arm = False
        if self._meter_flash_keys:
            self._paint_meter_flash_cells()

    def _render(self, *, parsed_rows: list[Sas6FRow], allow_machine_lookup: bool = True) -> None:
        self._begin_burst_paint_tracking()
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
            # Bills used to refresh only after a COM LP / cabinet_ui_refresh.
            # Local Auto fetch paints through ``_render`` alone, so the Bills
            # tab stayed at zeros even while note meters were in Machine state.
            self._render_bills(machine_state=self._machine_state or None)
            self._update_game_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_master_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_transfer_summary(allow_machine_lookup=allow_machine_lookup)
            self._update_security_summary(allow_machine_lookup=allow_machine_lookup)
            self._sync_magicwheel_tab_visibility()
            self._update_magicwheel_summary(allow_machine_lookup=allow_machine_lookup)
        finally:
            self.setUpdatesEnabled(True)
            self._finish_burst_paint_tracking()

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
        if self._bills_table is None or self._bills_out_table is None:
            return
        from network.accounting_state_loader import cabinet_bill_reject_count
        from network.sas_serial_meters import (
            build_bill_display_rows,
            build_bill_out_display_rows,
        )

        if rows is not None:
            self._last_bill_in_rows = list(rows)
        if out_rows is not None:
            self._last_bill_out_rows = list(out_rows)
        state = machine_state if machine_state is not None else self._machine_state
        if self._bill_reject_label is not None:
            self._bill_reject_label.setText(
                format_bill_reject_count_label(cabinet_bill_reject_count(state or {}))
            )
        # Always paint a full catalog. An empty capture used to clear the tables
        # and leave the previous fixed height, which looked like a blank damaged
        # Bills panel (headers only, huge white body).
        in_display = build_bill_display_rows(
            bill_rows=self._last_bill_in_rows or (),
            machine_state=state or None,
        )
        out_display = build_bill_out_display_rows(
            bill_rows=self._last_bill_out_rows or (),
        )
        self._render_bill_table(self._bills_table, list(in_display), direction="in")
        self._render_bill_table(self._bills_out_table, list(out_display), direction="out")

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
        # Always refit — skipping this after an empty clear left a tall blank box.
        self._fit_bill_table_height(table)

    @staticmethod
    def _fit_bill_table_height(table: QTableWidget) -> None:
        """Shrink table to row content; keep column widths stable across refreshes."""
        fit_bills_coins_table_columns(table)
        header_h = table.horizontalHeader().height()
        row_h = table.verticalHeader().defaultSectionSize()
        frame = table.frameWidth() * 2
        target_h = header_h + row_h * max(table.rowCount(), 1) + frame
        if table.minimumHeight() != target_h or table.maximumHeight() != target_h:
            table.setFixedHeight(target_h)

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
        self._last_bill_in_rows = []
        self._last_bill_out_rows = []
        self._render_bills(machine_state=None)
        for table in self._coin_tables.values():
            table.clearContents()
            table.setRowCount(0)
        self._render_coins(machine_state=None, allow_machine_lookup=False)
        self._sas_2f_values = {}
        self._update_2f_column_visibility()
        self._last_parsed_rows = []
        self._cached_meter_result = None
        self._meter_fetch_error = None
        self._meter_fetch_user_clicked_apply = False
        self._last_displayed_paste_fingerprint = ""
        self._loaded_cabinet_scan_root = ""
        self._machine_state = {}
        self._machine_state_loaded = False
        self._machine_state_loaded_at = 0.0
        self._local_sas_state = {}
        self._had_settled_mismatch = False
        self._meters_ui_pending = False
        self._compare_ui_pending = False
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
        open_file_actions: dict[int, tuple[str, Callable[[], object]]] | None = None,
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
        if open_file_actions and col in open_file_actions:
            open_label, open_resolver = open_file_actions[col]
            menu.addSeparator()
            act_open = menu.addAction(open_label)
            act_open.triggered.connect(
                lambda _checked=False, r=open_resolver: self._open_source_file(r())
            )
        menu.exec(table.viewport().mapToGlobal(pos))

    def _copy_bill_table_row_tsv(self, table: QTableWidget) -> None:
        headers = self._bills_copy_headers()
        self._copy_table_row_tsv(table, headers, None)

    def _copy_focused_table_row_tsv(self) -> None:
        focus = QApplication.focusWidget()
        if self._bills_out_table is not None and (
            focus is self._bills_out_table
            or (focus is not None and self._bills_out_table.isAncestorOf(focus))
        ):
            self._copy_bill_table_row_tsv(self._bills_out_table)
            return
        if self._bills_table is not None and (
            focus is self._bills_table
            or (focus is not None and self._bills_table.isAncestorOf(focus))
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

    def _machine_source_file(self):
        """Newest existing ``DeviceManagerData.xml`` backing the Machine column (gm2au)."""
        sr = self._scan_root_text()
        if not sr:
            return None
        from network.goldclub_paths import device_manager_data_files, resolve_goldclub_layout

        try:
            layout = resolve_goldclub_layout(sr)
        except OSError:
            return None
        if layout is None or layout.state_gcmessenger is None:
            return None
        candidates = [
            df
            for df in device_manager_data_files(layout.state_gcmessenger)
            if df.parent.name.lower() == "gm2au"
        ]
        return _newest_existing_file(candidates)

    def _sas_source_file(self):
        """
        File backing the SAS column, when one exists.

        On a local scan root (no host cable) the SAS side is the cabinet's own
        ``SASControler*`` snapshot; on a live COM/MUX capture there is no such
        file, so this falls back to this tool's own diagnostics log, which
        records the TX/RX traffic that produced the values on screen.
        """
        sr = self._scan_root_text()
        if sr:
            from network.goldclub_paths import (
                device_manager_data_files,
                resolve_goldclub_layout,
            )

            try:
                layout = resolve_goldclub_layout(sr)
            except OSError:
                layout = None
            if layout is not None and layout.state_gcmessenger is not None:
                candidates = [
                    df
                    for df in device_manager_data_files(layout.state_gcmessenger)
                    if "sascontrol" in df.parent.name.lower()
                ]
                found = _newest_existing_file(candidates)
                if found is not None:
                    return found
        from gui.app_logging import log_file_path

        for name in ("SasVerifyMeters.log", "LogInvestigator.log"):
            candidate = log_file_path(filename=name)
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _open_source_file(self, path) -> None:
        if path is None:
            QMessageBox.information(
                self,
                "Open file",
                "No source file found for this column yet (nothing scanned or captured).",
            )
            return
        from gui.notepad_pp import open_with_notepad_or_npp

        ok, msg = open_with_notepad_or_npp(path)
        if not ok:
            QMessageBox.warning(self, "Open file", msg)

    def _on_verify_table_context_menu(self, table: QTableWidget, pos: QPoint) -> None:
        self._show_table_copy_menu(
            table,
            pos,
            all_label="Copy all meters",
            headers_fn=self._accounting_copy_headers,
            excel_text_cols=_EXCEL_TEXT_COLS,
            text_resolver=self._verify_cell_resolver(table),
            open_file_actions={
                COL_SAS_6F_VALUE: ("Open SAS source file...", self._sas_source_file),
                COL_SAS_2F_VALUE: ("Open SAS source file...", self._sas_source_file),
                COL_MACHINE_VALUE: (
                    "Open Machine source file (DeviceManagerData.xml)...",
                    self._machine_source_file,
                ),
            },
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
            if self._coin_tables:
                self._render_coins(machine_state=self._machine_state or None)

    def mismatch_detected(self) -> bool:
        if getattr(self, "_had_settled_mismatch", False):
            return True
        for tbl in self._verify_tables:
            for i in range(tbl.rowCount()):
                it = tbl.item(i, COL_STATUS)
                if it and it.text().strip().upper() == "MISMATCH":
                    return True
        return False

