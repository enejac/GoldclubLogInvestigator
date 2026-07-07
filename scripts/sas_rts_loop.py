"""Repeat 6F with RTS=on markspace @19200, timed into chatter gaps.

Usage: python scripts/sas_rts_loop.py [COM4]
"""
from __future__ import annotations

import sys
import time

import serial

IGT_6F = bytes.fromhex("016f100000050006000700000001001c001d004c82")


def wait_gap(ser: serial.Serial, timeout: float = 1.5) -> bool:
    deadline = time.monotonic() + timeout
    saw = False
    last = 0.0
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            ser.read(n)
            saw = True
            last = time.monotonic()
            continue
        if saw and time.monotonic() - last > 0.004:
            return True
        time.sleep(0.001)
    return False


def send_markspace(ser: serial.Serial, pkt: bytes) -> None:
    ser.parity = serial.PARITY_MARK
    ser.write(pkt[:1])
    ser.flush()
    time.sleep(0.002)
    ser.parity = serial.PARITY_SPACE
    if len(pkt) > 1:
        ser.write(pkt[1:])
        ser.flush()


def read_stamped(ser: serial.Serial, secs: float) -> list[tuple[float, bytes]]:
    t0 = time.monotonic()
    out: list[tuple[float, bytes]] = []
    while time.monotonic() - t0 < secs:
        n = ser.in_waiting
        if n:
            out.append((time.monotonic() - t0, ser.read(n)))
        else:
            time.sleep(0.002)
    return out


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    ser = serial.Serial(
        port, 19200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_SPACE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=2.0,
    )
    ser.dtr = True
    ser.rts = True
    time.sleep(0.5)

    print("== general polls 81/80, RTS=on, gap-timed ==")
    for i in range(6):
        gap = wait_gap(ser)
        poll = 0x81 if i % 2 == 0 else 0x80
        send_markspace(ser, bytes([poll]))
        rx = read_stamped(ser, 0.4)
        s = " | ".join(f"{t*1000:.0f}ms:{b.hex(' ')}" for t, b in rx) or "(nothing)"
        print(f"poll {poll:02x} gap={'y' if gap else 'n'}: {s}")

    print("== 6F x8, RTS=on, gap-timed ==")
    for i in range(8):
        gap = wait_gap(ser)
        send_markspace(ser, IGT_6F)
        rx = read_stamped(ser, 1.0)
        s = " | ".join(f"{t*1000:.0f}ms:{b.hex(' ')}" for t, b in rx) or "(nothing)"
        print(f"try {i} gap={'y' if gap else 'n'}: {s}")

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
