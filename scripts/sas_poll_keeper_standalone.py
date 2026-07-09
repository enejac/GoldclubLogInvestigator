"""Minimal SAS 80/81 poll keeper for AFT WinDivert inject (no repo dependencies)."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

DEFAULT_PORT = "COM4"
DEFAULT_BAUD = 19200
DEFAULT_INTERVAL_S = 0.2
DEFAULT_WAKEUP_S = 2.0
DEFAULT_ADDRESS = 0x01


def normalize_port(name: str | None) -> str:
    raw = (name or "").strip().upper()
    if not raw:
        return DEFAULT_PORT
    if raw.startswith("COM"):
        return raw
    if raw.isdigit():
        return f"COM{raw}"
    return raw


def _require_pyserial():
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise SystemExit("pyserial is required: pip install pyserial") from exc
    return serial


def _serial_kwargs(serial, *, port: str, baud: int, parity, exclusive: bool) -> dict:
    kwargs = {
        "port": port,
        "baudrate": int(baud),
        "bytesize": serial.EIGHTBITS,
        "parity": parity,
        "stopbits": serial.STOPBITS_ONE,
        "timeout": 0.05,
        "write_timeout": 2.0,
    }
    try:
        import inspect

        if "exclusive" in inspect.signature(serial.Serial).parameters:
            kwargs["exclusive"] = exclusive
    except (TypeError, ValueError):
        pass
    return kwargs


def send_sas(ser, packet: bytes) -> None:
    serial = _require_pyserial()
    if not packet:
        return
    ser.parity = serial.PARITY_MARK
    ser.write(packet[0:1])
    ser.flush()
    time.sleep(0.002)
    ser.parity = serial.PARITY_SPACE
    if len(packet) > 1:
        ser.write(packet[1:])
        ser.flush()
    deadline = time.monotonic() + 0.05
    while getattr(ser, "out_waiting", 0) and time.monotonic() < deadline:
        time.sleep(0.001)


def general_poll_bytes(address: int = DEFAULT_ADDRESS) -> tuple[int, int]:
    addr = address & 0x7F
    return (0x80 | addr, 0x80 | (addr ^ 1))


def probe_port(port: str) -> int:
    serial = _require_pyserial()
    port_name = normalize_port(port)
    try:
        ser = serial.Serial(
            **_serial_kwargs(
                serial,
                port=port_name,
                baud=DEFAULT_BAUD,
                parity=serial.PARITY_SPACE,
                exclusive=True,
            )
        )
        ser.close()
        print(f"{port_name} is available for SAS host polling")
        return 0
    except Exception as exc:
        print(f"{port_name} is in use or unavailable ({exc})")
        return 1


def run_poll_loop(port: str, *, interval_s: float, warmup_s: float) -> int:
    serial = _require_pyserial()
    port_name = normalize_port(port)
    ser = serial.Serial(
        **_serial_kwargs(
            serial,
            port=port_name,
            baud=DEFAULT_BAUD,
            parity=serial.PARITY_SPACE,
            exclusive=True,
        )
    )
    time.sleep(max(0.0, float(warmup_s)))
    polls = general_poll_bytes(DEFAULT_ADDRESS)
    stop = threading.Event()

    def _stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print(
        f"SAS poll keeper on {port_name} @ {DEFAULT_BAUD} "
        f"(interval {interval_s:.2f}s)",
        flush=True,
    )
    idx = 0
    try:
        while not stop.is_set():
            send_sas(ser, bytes([polls[idx % 2]]))
            idx += 1
            time.sleep(max(0.05, float(interval_s)))
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
            if waiting:
                try:
                    ser.read(waiting)
                except Exception:
                    pass
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SAS host general-poll keeper (80/81)")
    parser.add_argument("port", nargs="?", default=DEFAULT_PORT)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--interval-ms", type=int, default=int(DEFAULT_INTERVAL_S * 1000))
    parser.add_argument("--warmup-s", type=float, default=DEFAULT_WAKEUP_S)
    args = parser.parse_args()
    if args.probe:
        return probe_port(args.port)
    interval_s = max(0.05, args.interval_ms / 1000.0)
    return run_poll_loop(args.port, interval_s=interval_s, warmup_s=args.warmup_s)


if __name__ == "__main__":
    raise SystemExit(main())
