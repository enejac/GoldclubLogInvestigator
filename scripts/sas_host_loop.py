"""IGT-tester-style host loop: steady 200 ms polls, 6F batches in poll slots.

Usage: python scripts/sas_host_loop.py [COM4]
"""
from __future__ import annotations

import sys
import time

import serial

BATCHES = (
    "016f100000050006000700000001001c001d004c82",
    "016f1000000b00170015006e00230016001800",  # CRC appended below
)


def crc16_kermit(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def with_crc(hexs: str) -> bytes:
    body = bytes.fromhex(hexs)
    if len(body) >= 5 and len(body) == 3 + body[2] + 2:
        return body  # already has CRC
    crc = crc16_kermit(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def send_markspace(ser: serial.Serial, pkt: bytes) -> None:
    ser.parity = serial.PARITY_MARK
    ser.write(pkt[:1])
    ser.flush()
    time.sleep(0.002)
    ser.parity = serial.PARITY_SPACE
    if len(pkt) > 1:
        ser.write(pkt[1:])
        ser.flush()


def read_for(ser: serial.Serial, secs: float) -> bytes:
    t0 = time.monotonic()
    buf = bytearray()
    while time.monotonic() - t0 < secs:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.002)
    return bytes(buf)


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
    ser.reset_input_buffer()

    # steady polls to bring the link up
    print("== link sync (polls @200ms) ==")
    for i in range(8):
        send_markspace(ser, bytes([0x81 if i % 2 == 0 else 0x80]))
        rx = read_for(ser, 0.2)
        print(f"poll {i}: {rx.hex(' ') or '-'}")

    for bi, hexs in enumerate(BATCHES):
        frame = with_crc(hexs)
        print(f"== batch {bi}: TX {frame.hex(' ')} ==")
        send_markspace(ser, frame)
        rx = read_for(ser, 0.5)
        print(f"batch {bi} RX: {rx.hex(' ') or '-'}  [{len(rx)} bytes]")
        # resume polls between batches (IGT tester keeps the 200 ms cadence)
        for i in range(3):
            send_markspace(ser, bytes([0x80 if i % 2 == 0 else 0x81]))
            rx2 = read_for(ser, 0.2)
            print(f"  inter-poll {i}: {rx2.hex(' ') or '-'}")

    ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
