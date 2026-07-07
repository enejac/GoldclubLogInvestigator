"""
SAS Long Poll **6F** (extended meters): parse ``TX>=`` polls and ``RX<=`` responses.

Meter codes are **four hex characters** (two bytes), consistent with
:mod:`network.meter_comparator` (e.g. ``0000`` Total Coin In, ``0100`` Total Coin Out).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from network.meter_comparator import SAS_TO_XML, _meter_value_to_int

_WEEKDAY_START = frozenset({"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"})


def _strip_direction(line: str) -> str:
    return line.replace("RX<=", "", 1).replace("TX>=", "", 1).strip()


def _tokens_from_hex_line(line: str) -> list[str] | None:
    clean = _strip_direction(line)
    tokens = clean.split()
    if len(tokens) < 5:
        return None
    if tokens[1].upper() != "6F":
        return None
    return tokens


def parse_tx_poll_meter_codes(tx_line: str) -> list[str]:
    """
    Extract requested meter codes from a ``TX>=`` 6F poll line (ordered).

    Uses the same length-prefixed meter layout as RX parsing when present.
    """
    tokens = _tokens_from_hex_line(tx_line)
    if not tokens:
        return []
    idx = 5
    end = len(tokens) - 2
    codes: list[str] = []
    while idx < end and (idx + 2) < end:
        code = f"{tokens[idx]}{tokens[idx + 1]}".upper()
        try:
            length = int(tokens[idx + 2], 16)
        except ValueError:
            break
        idx += 3
        if idx + length > len(tokens):
            break
        codes.append(code)
        idx += length
    return codes


def parse_rx_response_ordered(rx_line: str) -> list[tuple[str, int]]:
    """Parse ``RX<=`` ``01 6F`` response: ordered (meter_code, value) pairs."""
    if "RX<=" not in rx_line:
        return []
    if not re.search(r"\b6F\b", rx_line, re.I):
        return []

    # Prefer a byte-accurate parse so we handle variable length meters (including 0-length)
    # without desync (e.g., "... 6E 00 00 23 00 09 ...").
    m = re.search(r"RX<=\s*([0-9a-fA-F ]+)", rx_line)
    if m:
        compact = re.sub(r"\s+", "", m.group(1) or "")
        if compact and len(compact) % 2 == 0:
            try:
                b = bytes.fromhex(compact)
            except ValueError:
                b = b""
            # Expected: Addr(1), 6F(1), Len(1), GameNum(2) then repeating:
            # MeterCode(2 LE) + DataLen(1) + BCD bytes
            if len(b) >= 6 and b[1] == 0x6F:
                cursor = 5
                out_b: list[tuple[str, int]] = []
                while cursor + 3 <= len(b):
                    code_raw = b[cursor : cursor + 2]
                    # Wire order (matches SAS_TO_XML keys / sas_decoder / TX poll parser):
                    # bytes "17 00" -> "1700", "A0 00" -> "A000". Do NOT byte-swap here;
                    # the comparator subsystem keys on wire order, not the SAS-doc form.
                    code = f"{code_raw[0]:02X}{code_raw[1]:02X}"
                    m_len = int(b[cursor + 2])
                    cursor += 3
                    if m_len <= 0:
                        out_b.append((code, 0))
                        continue
                    if cursor + m_len > len(b):
                        break
                    data = b[cursor : cursor + m_len]
                    cursor += m_len
                    val_str = data.hex().upper()
                    out_b.append((code, _meter_value_to_int(val_str)))
                if out_b:
                    return out_b

    # Fallback to token-based parsing for legacy formats.
    tokens = _tokens_from_hex_line(rx_line)
    if not tokens:
        return []
    idx = 5
    end = len(tokens) - 2
    out: list[tuple[str, int]] = []
    while idx < end and (idx + 2) < end:
        code = f"{tokens[idx]}{tokens[idx + 1]}".upper()
        try:
            length = int(tokens[idx + 2], 16)
        except ValueError:
            break
        idx += 3
        if idx + length > len(tokens):
            break
        val_str = "".join(tokens[idx : idx + length])
        out.append((code, _meter_value_to_int(val_str)))
        idx += length
    return out


def parse_rx_response_meters(rx_line: str) -> dict[str, int]:
    """Parse ``RX<=`` 6F line to ``{meter_code: value}`` (insertion order preserved)."""
    return dict(parse_rx_response_ordered(rx_line))


def pair_tx_rx_blocks(lines: Iterable[str]) -> list[tuple[list[str], dict[str, int]]]:
    """
    Walk log lines (file order); pair a ``TX>=`` 6F poll with the next ``RX<=`` 6F response.

    Timestamp lines (weekday prefix) are ignored. If RX has no preceding TX, TX list is empty.
    """
    pending_tx: list[str] | None = None
    pairs: list[tuple[list[str], dict[str, int]]] = []
    for raw in lines:
        line = raw.strip()
        if len(line) > 20 and line[:3] in _WEEKDAY_START:
            continue
        if "TX>=" in line and re.search(r"\b6F\b", line, re.I):
            pending_tx = parse_tx_poll_meter_codes(line)
            continue
        if "RX<=" in line and re.search(r"\b01\s+6F\b", line, re.I):
            rx_d = parse_rx_response_meters(line)
            tx_c = pending_tx if pending_tx is not None else []
            pairs.append((tx_c, rx_d))
            pending_tx = None
    return pairs


def code_to_display_name(code: str) -> str:
    t = SAS_TO_XML.get(code.upper())
    if t:
        return f"{t[0]} ({code})"
    return f"Unknown ({code})"


def last_rx_named_values_from_text(sas_text: str) -> dict[str, int]:
    """
    From multi-line SAS simulator text, use the **last** line containing ``RX<=`` and ``01 6F``.

    Returns ``{display_label: int}`` using SAS_TO_XML UI names.
    """
    last_line = ""
    for line in sas_text.splitlines():
        if "RX<=" in line and "6F" in line.upper() and re.search(r"\b01\s+6F\b", line, re.I):
            last_line = line.strip()
    if not last_line:
        return {}
    out: dict[str, int] = {}
    for code, val in parse_rx_response_ordered(last_line):
        label = code_to_display_name(code)
        out[label] = val
    return out


def sas_code_to_xml_field(code: str) -> str | None:
    t = SAS_TO_XML.get(code.upper())
    return t[1] if t else None


def last_rx_code_values_from_text(sas_text: str) -> dict[str, int]:
    """Last RX line with ``01 6F`` as ``{4-char_code: int}``."""
    last_line = ""
    for line in sas_text.splitlines():
        if "RX<=" in line and re.search(r"\b01\s+6F\b", line, re.I):
            last_line = line.strip()
    if not last_line:
        return {}
    return parse_rx_response_meters(last_line)
