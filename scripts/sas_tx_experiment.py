"""TX strategy experiment on COM4 @ 19200: find how to make the EGM answer.

Usage: python scripts/sas_tx_experiment.py [COM4]
"""
from __future__ import annotations

import sys
import time

import serial

IGT_6F = bytes.fromhex("016f100000050006000700000001001c001d004c82")


def tx_seconds(nbytes: int, baud: int = 19200) -> float:
    return nbytes * 11.0 / baud


def wait_gap(ser: serial.Serial, timeout: float = 1.2) -> bool:
    """Wait for the next chatter burst to finish; return in the idle window."""
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
        if saw and time.monotonic() - last > 0.005:
            return True
        time.sleep(0.001)
    return False


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


def send_markspace(ser: serial.Serial, pkt: bytes, settle: float = 0.003) -> None:
    ser.parity = serial.PARITY_MARK
    ser.write(pkt[:1])
    ser.flush()
    time.sleep(settle)
    ser.parity = serial.PARITY_SPACE
    if len(pkt) > 1:
        ser.write(pkt[1:])
        ser.flush()


def send_8n1(ser: serial.Serial, pkt: bytes) -> None:
    ser.parity = serial.PARITY_NONE
    ser.write(pkt)
    ser.flush()
    ser.parity = serial.PARITY_SPACE


def run(name: str, port: str, payload: bytes, *, rts_mode: str, framing: str) -> None:
    ser = serial.Serial(
        port, 19200,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_SPACE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.05,
        write_timeout=2.0,
    )
    ser.dtr = True
    ser.rts = rts_mode == "on"
    time.sleep(0.3)
    gap = wait_gap(ser)
    if rts_mode == "pulse":
        ser.rts = True
        time.sleep(0.001)
    if framing == "markspace":
        send_markspace(ser, payload)
    else:
        send_8n1(ser, payload)
    if rts_mode == "pulse":
        time.sleep(tx_seconds(len(payload)) + 0.002)
        ser.rts = False
    stamped = read_stamped(ser, 0.6)
    ser.close()
    rx = " | ".join(f"{t*1000:.0f}ms:{b.hex(' ')}" for t, b in stamped) or "(nothing)"
    print(f"{name:44s} gap={'y' if gap else 'n'}  {rx}")


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "COM4"
    gp = bytes([0x81])
    print(f"== {port} @ 19200, chatter-gap timed TX ==")
    run("gp81 markspace RTS=off", port, gp, rts_mode="off", framing="markspace")
    run("gp81 markspace RTS=pulse", port, gp, rts_mode="pulse", framing="markspace")
    run("gp81 8n1 RTS=pulse", port, gp, rts_mode="pulse", framing="8n1")
    run("6F markspace RTS=off", port, IGT_6F, rts_mode="off", framing="markspace")
    run("6F markspace RTS=pulse", port, IGT_6F, rts_mode="pulse", framing="markspace")
    run("6F 8n1 RTS=pulse", port, IGT_6F, rts_mode="pulse", framing="8n1")
    run("6F 8n1 RTS=off", port, IGT_6F, rts_mode="off", framing="8n1")
    run("6F markspace RTS=on", port, IGT_6F, rts_mode="on", framing="markspace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
