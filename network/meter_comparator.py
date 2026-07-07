"""
Cross-reference SAS extended-meter (6F) payloads against Aurum ``gm2au`` meter XML.

XML values are **aggregated** (summed) per ``meterName`` across themes and meter tag types.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

# Maps SAS Code -> ("UI Display Name", "gm2au perfMeter meterName")
SAS_TO_XML: dict[str, tuple[str, str]] = {
    # Base Game
    "0500": ("Games Played", "gamesPlayed"),
    "0600": ("Games Won", "gamesWon"),
    "0700": ("Games Lost", "gamesLost"),
    "0000": ("Total Coin In", "coinIn"),
    "0100": ("Total Coin Out", "coinOut"),
    "1C00": ("Mach. Paid Paytable", "coinOut"),
    "1D00": ("Mach. Paid Prog.", "ProgScatterCoinOut"),
    # Master -> Total Credit
    "0B00": ("Total Bills In", "TotalBillsIn"),
    "1700": ("Transfer To EGM", "TotalCashlessIn"),
    "1500": ("Total Ticket In", "TotalVoucherIn"),
    "6E00": ("Total Bills Disp", "TotalBillsDispensed"),
    "2300": ("Total Hand Paid", "HandpayAmount"),
    "1600": ("Total Ticket Out", "TotalVoucherOut"),
    "1800": ("Transfer To Host", "TotalCashlessOut"),
    # TAB = HandpayOut, TotalCancelled
    "0300": ("TotalHandPaidCancelled", "HandPaidCancelled"),
    "0200": ("TotalJackpot", "TotalJackpot"),
    "1F00": ("TotalAttendantPaidPayTableWin", "AttendantPaidPaytable"),
    "2000": ("TotalAttendantPaidProg.Win", "AttendantPaidProg"),
    "0400": ("TotalCancelledCredits", "CancelledCredits"),
    # Transfer -> TICKET
    "8000": ("Reg Cashable Tkt In", "RegCashableTktIn"),
    "8200": ("Restricted Tkt In", "RestrictedTktIn"),
    "8400": ("NonRestricted Tkt In", "NonRestrictedTktIn"),
    "8600": ("Reg Cashable Tkt Out", "RegCashableTktOut"),
    "8800": ("Restricted Tkt Out", "RestrictedTktOut"),
    # Transfer -> CASHLESS
    "A000": ("Reg Cashable C-Less In", "RegCashableCLessIn"),
    "A200": ("Restricted C-Less In", "RestrictedCLessIn"),
    "A400": ("NonRestricted C-Less In", "NonRestrictedCLessIn"),
    "B800": ("Reg Cashable C-Less Out", "RegCashableCLessOut"),
    "BA00": ("Restricted C-Less Out", "RestrictedCLessOut"),
    "BC00": ("NonRestricted C-Less Out", "NonRestrictedCLessOut"),
}


def _meter_value_to_int(val_str: str) -> int:
    """
    Parses a string of BCD (Binary Coded Decimal) bytes into an integer.
    Since BCD maps 0x00-0x99 directly to decimal 0-99, a hex string representation
    like '000034' can be natively cast as a base-10 integer.
    """
    s = (val_str or "").strip()
    if not s:
        return 0
    try:
        return int(s, 10)
    except ValueError:
        # If the string contains A-F, it is a malformed BCD packet
        return 0


def _extract_sas_meters(sas_path: str) -> dict[str, int]:
    meters: dict[str, int] = {}
    if not os.path.exists(sas_path):
        return meters

    last_6f_line = ""
    with open(sas_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "RX<= 01 6F" in line:
                last_6f_line = line.strip()

    if not last_6f_line:
        return meters

    clean = last_6f_line.replace("RX<=", "").replace("TX>=", "").strip()
    tokens = clean.split()
    if len(tokens) < 5 or tokens[1].upper() != "6F":
        return meters

    idx = 5
    end = len(tokens) - 2
    while idx < end and (idx + 2) < end:
        code = f"{tokens[idx]}{tokens[idx + 1]}".upper()
        length = int(tokens[idx + 2], 16)
        idx += 3
        if idx + length > len(tokens):
            break

        val_str = "".join(tokens[idx : idx + length])
        meters[code] = _meter_value_to_int(val_str)
        idx += length
    return meters


def _extract_xml_meters(xml_text: str) -> dict[str, int]:
    """Sum meter values per ``meterName`` across themes (perf/cab/trans/cur meters).

    Synthesizes ``gamesLost`` as ``gamesPlayed - gamesWon`` when both sources exist.
    """
    meters: dict[str, int] = {}
    xml_start = xml_text.find("<?xml")
    if xml_start == -1:
        return meters

    device_classes = ("meters", "cabinet", "WAT", "voucher", "handpay")

    try:
        root = ET.fromstring(xml_text[xml_start:])

        def strip_ns(t: str) -> str:
            return t.split("}")[-1] if t else ""

        def get_attr(el: ET.Element, n: str) -> str | None:
            for k, v in el.attrib.items():
                if strip_ns(k) == n:
                    return v
            return None

        for dev in root.iter():
            if strip_ns(dev.tag) == "Devices" and get_attr(dev, "DeviceClass") in device_classes:
                for child in dev.iter():
                    tag_name = strip_ns(child.tag)
                    if tag_name in ("perfMeter", "cabMeter", "transMeter", "curMeter"):
                        name = get_attr(child, "meterName")
                        val = get_attr(child, "meterValue")
                        if name and val is not None:
                            try:
                                meters[name] = meters.get(name, 0) + int(val)
                            except ValueError:
                                pass
    except ET.ParseError:
        pass

    # Calculate synthetic meters that the cabinet does not explicitly track
    if "gamesPlayed" in meters and "gamesWon" in meters:
        meters["gamesLost"] = meters["gamesPlayed"] - meters["gamesWon"]

    return meters


def compare_sas_and_xml(sas_path: str, xml_text: str) -> str:
    sas_meters = _extract_sas_meters(sas_path)
    xml_meters = _extract_xml_meters(xml_text)

    if not sas_meters:
        return "⚠️ No SAS 6F commands found for comparison.\n"

    out: list[str] = [
        "\n" + "=" * 85,
        "=== ⚖️ AUTOMATED METER COMPARISON ===",
        "=" * 85,
        f"{'METER NAME (SAS CODE)':<32} | {'CABINET (XML)':<15} | "
        f"{'SAS (SIMULATOR)':<15} | {'STATUS'}",
        "-" * 85,
    ]

    for code, sas_val in sas_meters.items():
        if code in SAS_TO_XML:
            ui_name, x_name = SAS_TO_XML[code]
            display_name = f"{ui_name} ({code})"

            if x_name in xml_meters:
                x_val = xml_meters[x_name]
                match = "✅ MATCH" if sas_val == x_val else "❌ MISMATCH"
                out.append(
                    f"{display_name:<32} | {str(x_val):<15} | {str(sas_val):<15} | {match}"
                )
            else:
                out.append(
                    f"{display_name:<32} | {'N/A':<15} | {str(sas_val):<15} | "
                    f"⚠️ XML TAG MISSING"
                )
        else:
            display_unk = f"Unknown ({code})"
            out.append(
                f"{display_unk:<32} | {'N/A':<15} | {str(sas_val):<15} | "
                f"⚠️ UNMAPPED CODE"
            )

    out.append("=" * 85 + "\n")
    return "\n".join(out)
