"""
IGT Simulator ``.dat`` log helpers: decode SAS command **6F** (Extended Meters) lines,
and extract timestamped TX/RX rows for the state timeline.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone

SAS_WEEKDAY_TIMESTAMP_FMT = "%a %b %d %H:%M:%S %Y"


class SASDecoder:
    def parse_6f_command(self, hex_string: str) -> str:
        """Parse a SAS 6F (Extended Meters) RX/TX hex string into a readable format."""
        clean_hex = hex_string.replace("RX<=", "").replace("TX>=", "").strip()
        tokens = clean_hex.split()

        if len(tokens) < 5 or tokens[1] != "6F":
            return f"Invalid 6F packet: {hex_string}"

        output: list[str] = []
        game_no = f"{tokens[3]}{tokens[4]}"
        output.append(f"$6F = Game Number      = {game_no}")

        idx = 5
        meter_count = 1
        end_idx = len(tokens) - 2

        while idx < end_idx and (idx + 2) < end_idx:
            meter_code = f"{tokens[idx]}{tokens[idx + 1]}"
            meter_len = int(tokens[idx + 2], 16)

            idx += 3
            if idx + meter_len > len(tokens):
                break

            meter_value = "".join(tokens[idx : idx + meter_len])

            output.append(f"$6F = Meter {meter_count} Code     = {meter_code}")
            output.append(f"$6F = Meter {meter_count:<13} = {meter_value}")

            idx += meter_len
            meter_count += 1

        return "\n".join(output)


def parse_sas_log_file(file_path: str) -> str:
    if not os.path.exists(file_path):
        return "File not found."

    output: list[str] = []
    current_time = ""
    decoder = SASDecoder()

    with open(file_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if len(line) > 20 and line[:3] in (
                "Mon",
                "Tue",
                "Wed",
                "Thu",
                "Fri",
                "Sat",
                "Sun",
            ):
                current_time = line
                continue

            if "RX<= 01 6F" in line:
                output.append(f"--- [ {current_time} ] ---")
                output.append(f"RAW: {line}")
                output.append(decoder.parse_6f_command(line))
                output.append("")

    if not output:
        return "No SAS 6F packets found in log."

    return "\n".join(output)


def extract_sas_timeline_events(file_path: str) -> Iterator[tuple[datetime, str]]:
    """
    Read ``TXRXData.dat``-style SAS logs: weekday timestamp lines followed by packet lines.

    Yields ``(utc_datetime, display_line)`` for each ``RX<=`` / ``TX>=`` row after a parsed time.
    Naive datetimes from the file are treated as UTC.
    """
    current_time: datetime | None = None
    with open(file_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if len(line) > 20 and line[:3] in (
                "Mon",
                "Tue",
                "Wed",
                "Thu",
                "Fri",
                "Sat",
                "Sun",
            ):
                try:
                    dt = datetime.strptime(line, SAS_WEEKDAY_TIMESTAMP_FMT)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    current_time = dt
                except ValueError:
                    current_time = None
                continue

            if current_time is not None and ("RX<=" in line or "TX>=" in line):
                yield (current_time, f"[SAS PACKET] {line}")
