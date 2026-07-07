"""Diagnostic: MUX-framed SAS at 921600 on COM4 — poll, then 6F, dump all RX.

Usage: python scripts/capture_6f_mux.py [COM4]
"""
from __future__ import annotations

import sys
import time

import serial

IGT_6F = bytes.fromhex("016f100000050006000700000001001c001d004c82")


def mux_frame(frame: bytes) -> bytes:
    out = bytearray([0x1B, 0x80, frame[0]])
    if len(frame) > 1:
        out.extend((0x1B, 0x00, frame[1]))
        out.extend(frame[2:])
    return bytes(out)


def dump_rx(ser: serial.Serial, seconds: float, label: str) -> bytes:
    deadline = time.monotonic() + seconds
    buf = bytearray()
    while time.monotonic() < deadline:
        waiting = ser.in_waiting
        if waiting:
            buf += ser.read(waiting)
        else:
            time.sleep(0.005)
    print(f"{label}: {buf.hex(' ') if buf else '(nothing)'}  [{len(buf)} bytes]")
    return bytes(buf)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    print(f"open {port} @ 921600 8N1 DTR=1 RTS=0")
    ser = serial.Serial(
        port, 921600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=2.0,
    )
    ser.dtr = True
    ser.rts = False

    dump_rx(ser, 2.0, "spontaneous RX after open")

    print("-- MUX general polls, 200 ms apart, 3 s --")
    for i in range(15):
        poll = 0x81 if i % 2 == 0 else 0x80
        ser.write(bytes([0x1B, 0x80, poll]))
        ser.flush()
        dump_rx(ser, 0.2, f"poll {i} ({poll:02x})")

    print("-- MUX-framed 6F --")
    wire = mux_frame(IGT_6F)
    print(f"TX wire: {wire.hex(' ')}")
    ser.write(wire)
    ser.flush()
    dump_rx(ser, 3.0, "RX after 6F (mux)")

    print("-- keep polling after 6F --")
    for i in range(10):
        poll = 0x80 if i % 2 == 0 else 0x81
        ser.write(bytes([0x1B, 0x80, poll]))
        ser.flush()
        dump_rx(ser, 0.2, f"post poll {i} ({poll:02x})")

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
