"""Diagnostic: send the exact IGT tester 6F frame on COM4 and dump raw RX.

Usage: python scripts/capture_6f_raw.py [COM4] [19200]
"""
from __future__ import annotations

import sys
import time

import serial

# Exact first 6F frame from the IGT tester capture (TXRXData.dat).
IGT_6F = bytes.fromhex("016f10000005000600070000000100 1c001d004c82".replace(" ", ""))


def send_sas(ser: serial.Serial, packet: bytes) -> None:
    ser.parity = serial.PARITY_MARK
    ser.write(packet[0:1])
    ser.flush()
    time.sleep(0.002)
    ser.parity = serial.PARITY_SPACE
    if len(packet) > 1:
        ser.write(packet[1:])
        ser.flush()


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
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 19200
    print(f"open {port} @ {baud} 8/SPACE/1 DTR=1 RTS=0")
    ser = serial.Serial(
        port, baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_SPACE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=2.0,
    )
    ser.dtr = True
    ser.rts = False

    print("-- chatter after open/DTR (3 s, no flush) --")
    dump_rx(ser, 3.0, "spontaneous RX")

    print("-- raw general polls --")
    for i in range(6):
        send_sas(ser, bytes([0x81 if i % 2 == 0 else 0x80]))
        dump_rx(ser, 0.25, f"raw poll {i} ({'81' if i % 2 == 0 else '80'})")

    print("-- MUX-framed general polls (1B 80 XX) --")
    ser.parity = serial.PARITY_NONE
    for i in range(6):
        ser.write(bytes([0x1B, 0x80, 0x81 if i % 2 == 0 else 0x80]))
        ser.flush()
        dump_rx(ser, 0.25, f"mux poll {i}")
    ser.parity = serial.PARITY_SPACE

    print("-- 6F long poll --")
    print(f"TX: {IGT_6F.hex(' ')}")
    send_sas(ser, IGT_6F)
    dump_rx(ser, 3.0, "RX after 6F")

    print("-- follow-up general poll --")
    send_sas(ser, bytes([0x80]))
    dump_rx(ser, 0.5, "poll after 6F")

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
