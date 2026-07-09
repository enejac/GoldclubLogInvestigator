"""SAS poll keeper for AFT inject."""
from __future__ import annotations
import argparse, signal, sys, threading
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from network.sas_serial_meters import (
    DEFAULT_LINK_POLL_INTERVAL_S, DEFAULT_SAS_COM_BAUD, DEFAULT_SAS_COM_PORT,
    DEFAULT_WAKEUP_DELAY_S, DEFAULT_WIRE_MODE, open_sas_poll_keeper_serial,
    probe_com_port_available, run_sas_general_poll_loop,
)

def _main_probe(port: str) -> int:
    ok, msg = probe_com_port_available(port)
    print(msg)
    return 0 if ok else 1

def _main_poll(port: str, *, interval_s: float, warmup_s: float) -> int:
    ser, port_used, wire = open_sas_poll_keeper_serial(
        port, baud=DEFAULT_SAS_COM_BAUD, wire_mode=DEFAULT_WIRE_MODE, wakeup_delay_s=warmup_s)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"SAS poll keeper on {port_used} @ {DEFAULT_SAS_COM_BAUD} (interval {interval_s:.2f}s)", flush=True)
    try:
        run_sas_general_poll_loop(ser, wire=wire, poll_interval_s=interval_s, stop_event=stop)
    finally:
        try: ser.close()
        except Exception: pass
    return 0

def main() -> int:
    p = argparse.ArgumentParser(description="SAS host general-poll keeper (80/81)")
    p.add_argument("port", nargs="?", default=DEFAULT_SAS_COM_PORT)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--interval-ms", type=int, default=int(DEFAULT_LINK_POLL_INTERVAL_S * 1000))
    p.add_argument("--warmup-s", type=float, default=DEFAULT_WAKEUP_DELAY_S)
    a = p.parse_args()
    if a.probe:
        return _main_probe(a.port)
    return _main_poll(a.port, interval_s=max(0.05, a.interval_ms / 1000.0), warmup_s=max(0.0, a.warmup_s))

if __name__ == "__main__":
    raise SystemExit(main())