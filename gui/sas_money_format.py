"""SAS verify dollar/credit formatting helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CREDITS_PER_DOLLAR = 100

SAS_VERIFY_MONETARY_CODES = frozenset({
    "0000", "0001", "0002", "0003", "0004", "000B",
    "0015", "0016", "0017", "0018", "001C", "001D", "001F", "0020", "0023",
    "006E", "0080", "0082", "0084", "0086", "0088",
    "00A0", "00A2", "00A4", "00B8", "00BA", "00BC",
    "HPIN",  # Master Handpay In (DeviceManager handpay*InAmt; not a SAS LP)
})

_CURRENCY_LINE_PATTERNS = (
    (re.compile(r"\$\s*[\d,]+\.\d{2}"), "$", "USD"),
    (re.compile(r"\$\s*[\d,]+"), "$", "USD"),
)


@dataclass(frozen=True, slots=True)
class EgmCurrency:
    symbol: str = "$"
    code: str = "USD"
    credits_per_dollar: int = CREDITS_PER_DOLLAR


# ISO currencyId (as found in DeviceManagerData.xml processorStatus/curMeter)
# -> display symbol. Credit scale is unchanged: only the symbol/code differ.
_CURRENCY_ID_SYMBOLS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "COP": "$",
    "MXN": "$",
    "ARS": "$",
    "CLP": "$",
    "BRL": "R$",
    "CHF": "CHF ",
    "RSD": "din ",
    "RON": "lei ",
    "HUF": "Ft ",
    "CZK": "K\u010d ",
    "PLN": "z\u0142 ",
}


def currency_from_id(currency_id: str) -> EgmCurrency | None:
    """
    Map a cabinet-declared ISO currencyId (e.g. from DeviceManagerData.xml) to an
    EgmCurrency. Returns None for empty/unknown ids so callers keep their default.
    """
    cid = (currency_id or "").strip().upper()
    if not cid:
        return None
    symbol = _CURRENCY_ID_SYMBOLS.get(cid)
    if symbol is None:
        # Unknown but explicit ISO code: show the code itself rather than a wrong $.
        symbol = f"{cid} " if len(cid) == 3 and cid.isalpha() else None
    if symbol is None:
        return None
    return EgmCurrency(symbol=symbol, code=cid)


def is_monetary_sas_code(code: str) -> bool:
    return (code or "").strip().upper() in SAS_VERIFY_MONETARY_CODES


def credits_to_dollar_amount(raw: str):
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


def format_dollar_amount(amount: float, *, symbol: str) -> str:
    if amount == int(amount):
        return f"{symbol}{int(amount):,}"
    return f"{symbol}{amount:,.2f}"


def format_meter_value_display(
    raw: str,
    *,
    meter_code: str,
    currency: EgmCurrency,
    show_dollars: bool,
) -> str:
    text = (raw or "").strip()
    if not text:
        return "0"
    if not show_dollars and is_monetary_sas_code(meter_code) and "." in text:
        try:
            text = str(int(float(text) * float(CREDITS_PER_DOLLAR)))
        except ValueError:
            text = text.replace(".", "")
    if show_dollars and is_monetary_sas_code(meter_code):
        dollars = credits_to_dollar_amount(text)
        if dollars is not None:
            return format_dollar_amount(dollars, symbol=currency.symbol)
    return text


def detect_egm_currency(scan_root: str, vm=None) -> EgmCurrency:
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
