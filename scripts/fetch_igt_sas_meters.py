#!/usr/bin/env python3
"""Fetch SAS 6F meter polls over COM (IGT SAS tester batches)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from network.sas_parser import parse_rx_response_ordered
from network.sas_serial_meters import (
    DEFAULT_SAS_COM_BAUD,
    DEFAULT_SAS_COM_PORT,
    DEFAULT_WIRE_MODE,
    enumerate_serial_ports,
    fetch_meters_over_serial,
    format_serial_ports_message,
)


def _wire_to_verify(code: str) -> str:
    w = (code or "").strip().upper()
    if len(w) == 4:
        return f"{w[2:4]}{w[0:2]}"
    return w


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default=DEFAULT_SAS_COM_PORT)
    p.add_argument("--baud", type=int, default=DEFAULT_SAS_COM_BAUD)
    p.add_argument("--wire", choices=("mux", "raw", "auto"), default="auto")
    p.add_argument("--timeout", type=float, default=6.0)
    p.add_argument("--out", type=Path)
    p.add_argument("--list-ports", action="store_true")
    args = p.parse_args()

    if args.list_ports:
        print(format_serial_ports_message(enumerate_serial_ports()))
        return 0

    auto_baud = auto_wire = args.wire == "auto"
    wire_mode = DEFAULT_WIRE_MODE if args.wire == "auto" else args.wire

    try:
        result = fetch_meters_over_serial(
            port=args.port,
            baud=args.baud,
            timeout_s=args.timeout,
            wire_mode=wire_mode,
            auto_baud=auto_baud,
            auto_wire=auto_wire,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    paste = result.paste_text
    print(paste)
    print()
    print(f"# Port: {result.port_used}")
    print("# Decoded meters (verify-table ids):")
    for line in paste.splitlines():
        if not line.startswith("RX<="):
            continue
        for wire_code, value in parse_rx_response_ordered(line):
            verify = _wire_to_verify(wire_code)
            print(f"  {verify} ({wire_code} wire): {value}")

    if args.out:
        args.out.write_text(paste + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
