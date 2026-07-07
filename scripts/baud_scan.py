"""Listen-only baud scan on COM4: read spontaneous chatter at each baud.

Usage: python scripts/baud_scan.py [COM4] [seconds_per_baud]
"""
from __future__ import annotations

import sys
import time

import serial

BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    for baud in BAUDS:
        ser = serial.Serial(
            port, baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
        )
        ser.dtr = True
        ser.rts = False
        ser.reset_input_buffer()
        deadline = time.monotonic() + secs
        buf = bytearray()
        stamps: list[float] = []
        t0 = time.monotonic()
        while time.monotonic() < deadline:
            waiting = ser.in_waiting
            if waiting:
                buf += ser.read(waiting)
                stamps.append(time.monotonic() - t0)
            else:
                time.sleep(0.002)
        ser.close()
        gaps = [f"{stamps[i] - stamps[i-1]:.2f}" for i in range(1, min(len(stamps), 8))]
        hexs = buf[:48].hex(" ") + (" ..." if len(buf) > 48 else "")
        print(f"baud {baud:>7}: {len(buf):3d} bytes  gaps={gaps}  {hexs or '(silent)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
