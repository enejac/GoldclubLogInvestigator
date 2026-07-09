"""Dev-only COM probe: IGT-style 6F batches using production SAS helpers.

Normal use: LogInvestigator SAS Verify or ``fetch_meters_over_serial``.

Usage: python scripts/sas_host_loop.py [COM4]
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from network.sas_serial_meters import (  # noqa: E402
    DEFAULT_6F_BATCH_READ_S,
    DEFAULT_6F_BATCH_RETRIES,
    DEFAULT_IGT_LINK_SYNC_POLLS,
    DEFAULT_SAS_COM_BAUD,
    DEFAULT_SAS_COM_PORT,
    DEFAULT_WIRE_MODE,
    IGT_TESTER_6F_POLL_BATCHES,
    SasWire,
    _inter_poll_between_6f_batches,
    _open_serial_with_retry,
    _read_6f_response,
    _require_pyserial,
    _sync_sas_link_igt,
    build_igt_tester_6f_poll_frame,
    format_sas_traffic_line,
)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SAS_COM_PORT
    serial = _require_pyserial()
    ser, port_used = _open_serial_with_retry(
        serial,
        port=port,
        baud=DEFAULT_SAS_COM_BAUD,
        wire_mode=DEFAULT_WIRE_MODE,
        rts=True,
        force_capture=True,
    )
    wire = SasWire(ser, mode=DEFAULT_WIRE_MODE)
    try:
        print(f"== link sync on {port_used} ({DEFAULT_IGT_LINK_SYNC_POLLS} GP @ 200 ms) ==")
        link_ok, sync_rx = _sync_sas_link_igt(ser, wire=wire)
        preview = sync_rx.hex(" ") if sync_rx else "-"
        print(f"sync: {'OK' if link_ok else 'FAIL'} last_rx={preview}")
        if not link_ok:
            return 1

        for batch_index, batch in enumerate(IGT_TESTER_6F_POLL_BATCHES):
            tx = build_igt_tester_6f_poll_frame(batch)
            print(f"== batch {batch_index}: {format_sas_traffic_line('TX>=', tx)} ==")
            rx = b""
            rx_raw = b""
            elapsed = 0.0
            for attempt in range(DEFAULT_6F_BATCH_RETRIES):
                if attempt:
                    _inter_poll_between_6f_batches(
                        ser, wire=wire, poll_count=4,
                    )
                wire.send_frame(tx)
                rx, rx_raw, elapsed = _read_6f_response(
                    ser,
                    wire=wire,
                    overall_timeout_s=DEFAULT_6F_BATCH_READ_S,
                )
                if rx:
                    break
            if rx:
                print(
                    f"batch {batch_index} {format_sas_traffic_line('RX<=', rx)} "
                    f"({elapsed:.2f}s, attempts={attempt + 1})"
                )
            else:
                raw_preview = rx_raw.hex(" ") if rx_raw else "-"
                print(
                    f"batch {batch_index} RX: (no 6F frame after "
                    f"{DEFAULT_6F_BATCH_RETRIES} attempts) raw={raw_preview}"
                )
            if batch_index + 1 < len(IGT_TESTER_6F_POLL_BATCHES):
                _inter_poll_between_6f_batches(ser, wire=wire)
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
