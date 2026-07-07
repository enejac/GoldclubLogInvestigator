"""Fetch SAS extended meters (6F poll) over a local COM port like the IGT SAS tester."""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass

# Verify-table meter ids polled by the IGT SAS tester accounting fetch (lab default set).
DEFAULT_6F_VERIFY_POLL_CODES: tuple[str, ...] = (
    "0005", "0006", "0007", "0000", "0001", "001C", "001D", "000B",
    "0017", "0015", "006E", "0023", "0016", "0018", "0003", "0002",
    "001F", "0020", "0004",
    "0080", "0082", "0084", "0086", "0088",
    "00A0", "00A2", "00A4", "00B8", "00BA", "00BC",
)

# IGT SAS tester sends verify meters as five separate 6F polls (wire-order pairs only).
IGT_TESTER_6F_POLL_BATCHES: tuple[tuple[str, ...], ...] = (
    ("0005", "0006", "0007", "0000", "0001", "001C", "001D"),
    ("000B", "0017", "0015", "006E", "0023", "0016", "0018"),
    ("0003", "0002", "001F", "0020", "0004"),
    ("0015", "0080", "0082", "0084", "0016", "0086", "0088"),
    ("0017", "00A0", "00A2", "00A4", "0018", "00B8", "00BA", "00BC"),
)

DEFAULT_SAS_COM_PORT = "COM4"
# SAS 6.02 / IGT SAS tester: 19200 on EIA-232; GoldClub MUX bridge may use 921600.
DEFAULT_SAS_COM_BAUD = 19200
DEFAULT_SAS_BAUD_CANDIDATES: tuple[int, ...] = (19200, 921600)
DEFAULT_SAS_ADDRESS = 0x01
DEFAULT_WIRE_MODE = "raw"  # IGT SAS tester Mark/Space; "mux" = GoldClub 1B 80/00 wrap
DEFAULT_PORT_WAIT_S = 12.0
DEFAULT_PORT_RETRY_DELAY_S = 0.45
DEFAULT_FORCE_CAPTURE_WAIT_S = 15.0
DEFAULT_FORCE_LINK_SYNC_S = 6.0
# Windows executables that commonly hold the host SAS COM port open.
_IGT_COM_BLOCKER_EXE_NAMES: tuple[str, ...] = (
    "SASTest.exe",
    "SASHost.exe",
    "SASComm.exe",
    "IGTSASTest.exe",
    "SlotSAS.exe",
)
# sastest.ini [SAS Protocols] Wakeup Delay = 2, Poll Rate = 200 (ms)
DEFAULT_WAKEUP_DELAY_S = 2.0
DEFAULT_LINK_SYNC_S = 3.0
DEFAULT_LINK_POLL_INTERVAL_S = 0.2
DEFAULT_RESPONSE_TIMEOUT_S = 15.0
DEFAULT_RESPONSE_IDLE_MS = 350
DEFAULT_AUTO_PROBE_RESPONSE_S = 15.0
# Lab COM4: IGT raw @ 19200 is the only path that returns real 6F meter data.
AUTO_WIRE_BAUD_RTS_COMBOS: tuple[tuple[str, int, bool], ...] = (
    ("raw", 19200, False),
    ("raw", 19200, True),
)
# Legacy 2-tuple view for callers/tests.
AUTO_WIRE_BAUD_COMBOS: tuple[tuple[str, int], ...] = tuple(
    (mode, baud) for mode, baud, _rts in AUTO_WIRE_BAUD_RTS_COMBOS
)
_LINK_SILENT_MARKER = "SAS link not responding"

# IGT SAS tester host COM: raw SAS + Mark/Space parity.
# GoldClub MUX (EGM / CommCtrl path): 1B 80 / 1B 00 escape pairs on 8N1 serial.


class SasWire:
    """Host-side SAS transmit encoding (MUX or raw 9-bit parity)."""

    __slots__ = ("_mode", "_ser")

    def __init__(self, ser, *, mode: str = DEFAULT_WIRE_MODE) -> None:
        self._ser = ser
        self._mode = (mode or DEFAULT_WIRE_MODE).strip().lower()

    @property
    def mode(self) -> str:
        return self._mode

    def send_general_poll(self, poll_byte: int) -> None:
        if self._mode == "mux":
            _write_mux(self._ser, mux_encode_general_poll(poll_byte))
            return
        _send_sas(self._ser, bytes([poll_byte & 0xFF]))

    def send_frame(self, frame: bytes) -> None:
        if not frame:
            return
        if self._mode == "mux":
            _write_mux(self._ser, mux_encode_sas_frame(frame))
            return
        _send_sas(self._ser, frame)

_SAS_PORT_HINTS = (
    "usb serial",
    "ftdi",
    "prolific",
    "cp210",
    "ch340",
    "sas",
    "mux",
    "commctrl",
    "serial",
)


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str = ""
    hwid: str = ""


def normalize_com_port(name: str | None) -> str:
    raw = (name or "").strip().upper()
    if not raw:
        return ""
    m = re.match(r"^COM(\d+)$", raw)
    if m:
        return f"COM{int(m.group(1))}"
    if raw.isdigit():
        return f"COM{int(raw)}"
    return raw


def _com_sort_key(device: str) -> tuple[int, int]:
    m = re.match(r"^COM(\d+)$", normalize_com_port(device))
    return (0, int(m.group(1))) if m else (1, 0)


def enumerate_serial_ports() -> list[SerialPortInfo]:
    serial = _require_pyserial()
    from serial.tools import list_ports  # type: ignore

    out: list[SerialPortInfo] = []
    for p in list_ports.comports():
        device = normalize_com_port(getattr(p, "device", "") or "")
        if not device:
            continue
        out.append(
            SerialPortInfo(
                device=device,
                description=(getattr(p, "description", "") or "").strip(),
                hwid=(getattr(p, "hwid", "") or "").strip(),
            )
        )
    out.sort(key=lambda info: _com_sort_key(info.device))
    return out


def pick_sas_com_port(
    requested: str | None,
    available: list[SerialPortInfo] | None = None,
) -> str | None:
    """Pick the best available COM device for SAS meter fetch."""
    ports = available if available is not None else enumerate_serial_ports()
    if not ports:
        return None
    by_name = {info.device.upper(): info.device for info in ports}
    req = normalize_com_port(requested or "")
    if req and req in by_name:
        return by_name[req]
    default = normalize_com_port(DEFAULT_SAS_COM_PORT)
    if default in by_name:
        return by_name[default]
    if len(ports) == 1:
        return ports[0].device
    scored: list[tuple[int, str]] = []
    for info in ports:
        blob = f"{info.description} {info.hwid}".lower()
        score = sum(1 for hint in _SAS_PORT_HINTS if hint in blob)
        if info.device.upper() == default:
            score += 2
        scored.append((score, info.device))
    scored.sort(key=lambda item: (-item[0], _com_sort_key(item[1])))
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return None


def format_serial_ports_message(ports: list[SerialPortInfo]) -> str:
    if not ports:
        return "No COM ports are currently visible to Windows."
    lines = [f"  {p.device}: {p.description or '(no description)'}" for p in ports]
    return "Available COM ports:\n" + "\n".join(lines)


def _is_retryable_serial_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in (2, 5, 13):
            return True
        msg = str(exc).lower()
        return "access is denied" in msg or "could not open port" in msg or "filenotfounderror" in msg
    return False


def _format_serial_open_error(
    requested: str,
    last_exc: BaseException | None,
    ports: list[SerialPortInfo],
) -> str:
    req = normalize_com_port(requested) or DEFAULT_SAS_COM_PORT
    detail = str(last_exc).strip() if last_exc else "Port not available."
    stuck_note = ""
    if last_exc is not None and _is_retryable_serial_error(last_exc):
        stuck_note = (
            f"\n\nWindows reports the port is busy (often another app, or a meter fetch "
            f"that was interrupted before COM was released). Close IGT SAS tester, wait a "
            f"few seconds, then restart this app if the error persists.\n"
        )
    msg = (
        f"Could not open {req} for SAS meter fetch.\n\n"
        f"{detail}{stuck_note}\n"
        f"If the IGT SAS tester was using this port, close it completely and wait a few "
        f"seconds for Windows to release the COM device, then click Get Meters again.\n\n"
        f"{format_serial_ports_message(ports)}"
    )
    return msg


def crc16_kermit(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) if (crc & 1) else (crc >> 1)
            crc &= 0xFFFF
    return crc


def append_sas_crc(body: bytes) -> bytes:
    crc = crc16_kermit(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def verify_code_to_wire_bytes(code: str) -> tuple[int, int]:
    c = (code or "").strip().upper()
    if len(c) != 4:
        raise ValueError(f"Invalid verify meter code: {code!r}")
    wire = f"{c[2:4]}{c[0:2]}"
    return int(wire[0:2], 16), int(wire[2:4], 16)


def build_igt_tester_6f_poll_frame(
    meter_codes: tuple[str, ...] | list[str] | None = None,
    *,
    address: int = DEFAULT_SAS_ADDRESS,
    game_number: int = 0,
) -> bytes:
    """Build one SAS 6F poll frame exactly like the IGT tester log lines."""
    codes = tuple(meter_codes or ())
    if not codes:
        raise ValueError("meter_codes required (IGT tester sends batched polls)")
    data = bytearray([game_number & 0xFF, (game_number >> 8) & 0xFF])
    for code in codes:
        b0, b1 = verify_code_to_wire_bytes(code)
        data.extend((b0, b1))
    if len(data) > 255:
        raise ValueError(f"6F poll data too large ({len(data)} bytes)")
    body = bytes([address & 0xFF, 0x6F, len(data)]) + bytes(data)
    return append_sas_crc(body)


def format_sas_traffic_line(prefix: str, frame: bytes) -> str:
    return f"{prefix} " + " ".join(f"{b:02X}" for b in frame)


def mux_encode_general_poll(poll_byte: int) -> bytes:
    return bytes([0x1B, 0x80, poll_byte & 0xFF])


def mux_encode_sas_frame(frame: bytes) -> bytes:
    if not frame:
        return b""
    out = bytearray([0x1B, 0x80, frame[0]])
    if len(frame) > 1:
        out.extend((0x1B, 0x00, frame[1]))
        out.extend(frame[2:])
    return bytes(out)


def mux_decode_all(packet: bytes) -> list[bytes] | None:
    if not packet or len(packet) < 3 or packet[0] != 0x1B or packet[1] != 0x80:
        return None
    result: list[bytes] = []
    current: bytearray | None = None
    i = 0
    n = len(packet)
    while i < n:
        b = packet[i]
        if b == 0x1B and i + 2 < n and packet[i + 1] in (0x80, 0x00):
            is_address = packet[i + 1] == 0x80
            value = packet[i + 2]
            if is_address:
                if current is not None and len(current) > 0:
                    result.append(bytes(current))
                current = bytearray([value])
            else:
                if current is None:
                    return None
                current.append(value)
            i += 3
        else:
            if current is None:
                return None
            current.append(b)
            i += 1
    if current is not None and len(current) > 0:
        result.append(bytes(current))
    return result if result else None


def _coalesce_rx_candidates(raw: bytes) -> list[bytes]:
    """Return candidate SAS byte buffers extracted from MUX and/or raw RX."""
    cleaned = raw.strip(b"\x00")
    if not cleaned:
        return []
    candidates: list[bytes] = []
    mux_frames = mux_decode_all(cleaned)
    if mux_frames:
        candidates.extend(mux_frames)
    candidates.append(cleaned)
    return candidates


def _find_6f_response(raw: bytes, *, address: int = DEFAULT_SAS_ADDRESS) -> bytes:
    for blob in _coalesce_rx_candidates(raw):
        frame = _extract_6f_response_frame(blob, address=address)
        if frame:
            return frame
    return b""


@dataclass(frozen=True, slots=True)
class SasMeterFetchResult:
    tx_frame: bytes
    rx_frame: bytes
    paste_text: str
    port_used: str = ""
    wire_mode: str = ""
    baud: int = 0
    bill_rows: tuple = ()  # tuple[SasBillDenomRow, ...] — filled after bill LP fetch


def _extract_6f_response_frame(raw: bytes, *, address: int = DEFAULT_SAS_ADDRESS) -> bytes:
    """Return the first complete addr/6F/len/data/CRC frame inside ``raw``."""
    addr = address & 0xFF
    for i in range(len(raw)):
        if raw[i] != addr or i + 3 >= len(raw) or raw[i + 1] != 0x6F:
            continue
        data_len = int(raw[i + 2])
        end = i + 3 + data_len + 2
        if end > len(raw):
            continue
        frame = raw[i:end]
        body = frame[:-2]
        crc_got = frame[-2] | (frame[-1] << 8)
        if crc16_kermit(body) == crc_got:
            return frame
    return b""


def _require_pyserial():
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is not installed. Run: pip install pyserial"
        ) from exc
    return serial


def _ensure_space_rx(ser) -> None:
    serial = _require_pyserial()
    try:
        ser.parity = serial.PARITY_SPACE
    except Exception:
        pass


def _configure_serial_port(
    ser, *, wire_mode: str = DEFAULT_WIRE_MODE, rts: bool = False
) -> None:
    """MUX: 8N1. Raw SAS: 8-space + Mark/Space on send. sastest.ini: DTR=1."""
    serial = _require_pyserial()
    mode = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    try:
        ser.parity = (
            serial.PARITY_NONE if mode == "mux" else serial.PARITY_SPACE
        )
    except Exception:
        pass
    try:
        ser.dtr = True
        ser.rts = bool(rts)
    except Exception:
        pass


def _read_after_poll(ser, *, idle_ms: int = 80) -> bytes:
    """Read any bytes the EGM returned after a general poll."""
    chunks: list[bytes] = []
    deadline = time.monotonic() + max(0.05, idle_ms / 1000.0)
    last = time.monotonic()
    old_timeout = getattr(ser, "timeout", 0.05)
    try:
        ser.timeout = 0.12
        while time.monotonic() < deadline:
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
            if waiting:
                chunks.append(ser.read(waiting))
                last = time.monotonic()
                continue
            if chunks and (time.monotonic() - last) * 1000.0 >= idle_ms:
                break
            extra = ser.read(1)
            if extra:
                chunks.append(extra)
                last = time.monotonic()
            else:
                time.sleep(0.01)
    finally:
        try:
            ser.timeout = old_timeout
        except Exception:
            pass
    return b"".join(chunks)


def _send_sas(ser, packet: bytes) -> None:
    """Send raw SAS on the host serial line (Mark on addr byte, Space on data).

    Always leaves the port at SPACE parity: EGM response bytes arrive with the
    wakeup (9th) bit low, so reading while the port is still at MARK parity
    makes Windows flag every byte as a parity error and drop/mangle it.
    """
    if not packet:
        return
    serial = _require_pyserial()
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
    _ensure_space_rx(ser)


def _send_general_poll(ser, poll_byte: int) -> None:
    _send_sas(ser, bytes([poll_byte & 0xFF]))


def _write_mux(ser, wire: bytes) -> None:
    """Write MUX-framed bytes (8N1 — 9th bit is encoded in 1B 80/00 pairs)."""
    ser.write(wire)
    ser.flush()


def _read_available(
    ser,
    *,
    idle_ms: int = DEFAULT_RESPONSE_IDLE_MS,
    overall_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
) -> bytes:
    deadline = time.monotonic() + overall_timeout_s
    chunks: list[bytes] = []
    last = time.monotonic()
    while time.monotonic() < deadline:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
        if waiting:
            chunks.append(ser.read(waiting))
            last = time.monotonic()
            continue
        if chunks and (time.monotonic() - last) * 1000.0 >= idle_ms:
            break
        time.sleep(0.01)
    return b"".join(chunks)


def _general_poll_alternation(address: int = DEFAULT_SAS_ADDRESS) -> tuple[int, int]:
    """Return the two alternating GP bytes for a SAS address (IGT: 80/81 for addr 1)."""
    a = address & 0x7F
    return (0x80 | a, 0x80 | (a ^ 1))


def _gp_response_is_stable(rx: bytes) -> bool:
    """True when RX looks like a clean general-poll reply (not warmup garbage)."""
    if not rx:
        return False
    if all(b == 0 for b in rx):
        return True
    if len(rx) == 1 and rx[0] not in (0x8C, 0x01):
        return True
    return False


def _establish_sas_link(
    ser,
    *,
    wire: SasWire,
    address: int = DEFAULT_SAS_ADDRESS,
    duration_s: float = DEFAULT_LINK_SYNC_S,
    poll_interval_s: float = DEFAULT_LINK_POLL_INTERVAL_S,
    min_stable_gps: int = 2,
) -> tuple[bool, bytes]:
    """Sync with alternating general polls; return (saw_rx, last_rx_chunk)."""
    polls = _general_poll_alternation(address)
    deadline = time.monotonic() + max(0.3, float(duration_s))
    idx = 0
    saw_rx = False
    last_rx = b""
    stable = 0
    need_stable = max(1, int(min_stable_gps))
    while time.monotonic() < deadline:
        poll = polls[idx % 2]
        idx += 1
        wire.send_general_poll(poll)
        # EGM replies on the ~200 ms poll cadence (sastest.ini Poll Rate = 200).
        time.sleep(max(0.05, poll_interval_s))
        rx = _read_after_poll(ser, idle_ms=80)
        if rx:
            saw_rx = True
            last_rx = rx
            if _gp_response_is_stable(rx):
                stable += 1
                if stable >= need_stable:
                    return True, last_rx
            else:
                stable = 0
    return saw_rx and stable >= need_stable, last_rx


def _read_6f_response(
    ser,
    *,
    wire: SasWire,
    address: int = DEFAULT_SAS_ADDRESS,
    overall_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
) -> tuple[bytes, bytes, float]:
    """Read the 6F reply without interleaving general polls.

    IGT tester capture (TXRXData.dat) shows the 6F reply immediately after
    the long poll. Sending GP during the response window flips parity to MARK
    and corrupts the incoming frame on Windows.
    """
    del wire
    started = time.monotonic()
    deadline = started + max(0.5, float(overall_timeout_s))
    chunks: list[bytes] = []
    _ensure_space_rx(ser)
    while time.monotonic() < deadline:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
        if waiting:
            chunks.append(ser.read(waiting))
            raw = b"".join(chunks)
            frame = _find_6f_response(raw, address=address)
            if frame:
                return frame, raw, time.monotonic() - started
            continue
        time.sleep(0.005)
    raw = b"".join(chunks)
    return _find_6f_response(raw, address=address), raw, time.monotonic() - started


def _format_no_response_error(
    port_used: str,
    *,
    timeout_s: float,
    elapsed_s: float,
    baud: int,
    wire_mode: str,
    tx_frame: bytes,
    rx_raw: bytes,
    batch_index: int | None = None,
) -> str:
    batch_note = f" (batch {batch_index + 1})" if batch_index is not None else ""
    tx_preview = format_sas_traffic_line("TX>=", tx_frame) if tx_frame else ""
    if rx_raw and all(b == 0 for b in rx_raw):
        extra = (
            f"\n\nOnly idle 0x00 bytes were received{batch_note}. "
            "General polls are reaching the EGM but the 6F long poll was not accepted."
        )
    elif rx_raw:
        preview = format_sas_traffic_line("RX<=", rx_raw[:48])
        if len(rx_raw) > 48:
            preview += " …"
        extra = (
            f"\n\nPartial/non-6F bytes were seen{batch_note}:\n{preview}\n\n"
            f"Check baud ({baud}), MUX cable, and that the cabinet SAS link is up."
        )
    else:
        extra = (
            f"\n\nNo bytes were received{batch_note}. The SAS link may still be offline — "
            "confirm the MUX cable, cabinet power, and that nothing else is polling this COM port."
        )
    timing = (
        f"waited {elapsed_s:.1f}s of {timeout_s:.1f}s"
        if elapsed_s + 0.05 < timeout_s
        else f"timeout {timeout_s:.1f}s"
    )
    detail = f"Baud {baud}, wire {wire_mode}, {timing}."
    if tx_preview:
        detail += f"\n{tx_preview}"
    return f"No SAS 6F response on {port_used} ({detail}).{extra}"


def _prime_before_long_poll(wire: SasWire, *, address: int = DEFAULT_SAS_ADDRESS) -> None:
    """One general poll immediately before the 6F (matches TXRXData.dat)."""
    wire.send_general_poll(_general_poll_alternation(address)[0])
    time.sleep(0.02)
    ser = wire._ser
    _ensure_space_rx(ser)
    waiting = int(getattr(ser, "in_waiting", 0) or 0)
    if waiting:
        ser.read(waiting)


def _wakeup_serial(ser, *, delay_s: float = DEFAULT_WAKEUP_DELAY_S) -> None:
    """sastest.ini Wakeup Delay before polling (DTR already asserted on open)."""
    delay = max(0.0, float(delay_s))
    if delay > 0:
        time.sleep(delay)
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except Exception:
        pass


def force_release_com_port_blockers(*, wait_s: float = 2.0) -> list[str]:
    """Best-effort: stop IGT SAS tester apps that keep the host COM port open."""
    notes: list[str] = []
    if sys.platform != "win32":
        return notes
    for exe in _IGT_COM_BLOCKER_EXE_NAMES:
        try:
            proc = subprocess.run(
                ["taskkill", "/F", "/IM", exe],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            if proc.returncode == 0:
                notes.append(f"Stopped {exe}")
        except Exception:
            continue
    time.sleep(max(0.5, float(wait_s)))
    return notes


def _nudge_com_port_driver(serial_module, port: str) -> None:
    """Open/close once so Windows releases a stale handle on the same port name."""
    try:
        probe = serial_module.Serial(port=port, timeout=0.05)
        try:
            probe.reset_input_buffer()
        except Exception:
            pass
        probe.close()
        time.sleep(0.35)
    except Exception:
        pass


def _serial_open_kwargs(
    serial_module,
    *,
    port: str,
    baud: int,
    parity,
    exclusive: bool,
) -> dict:
    kwargs = {
        "port": port,
        "baudrate": int(baud),
        "bytesize": serial_module.EIGHTBITS,
        "parity": parity,
        "stopbits": serial_module.STOPBITS_ONE,
        "timeout": 0.05,
        "write_timeout": 2.0,
    }
    if exclusive:
        import inspect

        try:
            params = inspect.signature(serial_module.Serial).parameters
        except (TypeError, ValueError):
            params = {}
        if "exclusive" in params:
            kwargs["exclusive"] = True
    return kwargs


def _open_serial_with_retry(
    serial_module,
    *,
    port: str,
    baud: int,
    wire_mode: str = DEFAULT_WIRE_MODE,
    rts: bool = False,
    wait_s: float = DEFAULT_PORT_WAIT_S,
    retry_delay_s: float = DEFAULT_PORT_RETRY_DELAY_S,
    force_capture: bool = False,
):
    requested = normalize_com_port(port) or DEFAULT_SAS_COM_PORT
    mode = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    parity = (
        serial_module.PARITY_NONE
        if mode == "mux"
        else serial_module.PARITY_SPACE
    )
    if force_capture:
        force_release_com_port_blockers(wait_s=2.0)
    deadline = time.monotonic() + max(0.5, float(wait_s))
    last_exc: BaseException | None = None
    nudged = False
    while time.monotonic() < deadline:
        available = enumerate_serial_ports()
        target = pick_sas_com_port(requested, available)
        if not target:
            last_exc = FileNotFoundError(
                errno=2,
                filename=requested,
                strerror="The system cannot find the file specified.",
            )
            time.sleep(retry_delay_s)
            continue
        if force_capture and not nudged:
            _nudge_com_port_driver(serial_module, target)
            nudged = True
        try:
            ser = serial_module.Serial(
                **_serial_open_kwargs(
                    serial_module,
                    port=target,
                    baud=baud,
                    parity=parity,
                    exclusive=force_capture,
                )
            )
            _configure_serial_port(ser, wire_mode=mode, rts=rts)
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass
            return ser, target
        except Exception as exc:  # noqa: BLE001
            if _is_retryable_serial_error(exc):
                last_exc = exc
                if force_capture:
                    force_release_com_port_blockers(wait_s=1.0)
                time.sleep(retry_delay_s)
                continue
            raise
    raise RuntimeError(_format_serial_open_error(requested, last_exc, enumerate_serial_ports()))


def _format_link_silent_error(
    port_used: str,
    *,
    baud: int,
    wire_mode: str,
    sync_s: float,
    rts: bool = False,
    last_rx: bytes = b"",
) -> str:
    rx_note = ""
    if last_rx:
        preview = format_sas_traffic_line("RX<=", last_rx[:16])
        rx_note = f"\nLast RX before timeout: {preview}"
    return (
        f"{_LINK_SILENT_MARKER} on {port_used} "
        f"(no RX during {sync_s:.1f}s sync, baud {baud}, wire {wire_mode}, "
        f"RTS={'on' if rts else 'off'}).{rx_note}\n\n"
        f"The COM port opened, but the EGM/MUX returned 0 bytes to general polls.\n"
        f"• Close IGT SAS tester completely (it must release {port_used})\n"
        f"• Cabinet powered on and SAS/MUX cable seated\n"
        f"• Run: python scripts/probe_com4.py {port_used}\n"
        f"• If IGT SAS tester also shows no RX on this port, the link is offline"
    )


def _probe_should_continue(msg: str) -> bool:
    """True when another wire/baud combo is worth trying."""
    if _LINK_SILENT_MARKER in msg:
        return True
    markers = (
        "No bytes were received",
        "Only idle 0x00",
        "Partial/non-6F",
        "No SAS 6F response",
    )
    return any(m in msg for m in markers)


def fetch_meters_over_serial(
    *,
    port: str,
    baud: int = DEFAULT_SAS_COM_BAUD,
    address: int = DEFAULT_SAS_ADDRESS,
    meter_codes: tuple[str, ...] | list[str] | None = None,
    poll_batches: tuple[tuple[str, ...], ...] | None = None,
    timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
    port_wait_s: float = 4.0,
    link_sync_s: float = DEFAULT_LINK_SYNC_S,
    wakeup_delay_s: float = DEFAULT_WAKEUP_DELAY_S,
    wire_mode: str = DEFAULT_WIRE_MODE,
    auto_baud: bool = True,
    auto_wire: bool = True,
    force_capture: bool = True,
) -> SasMeterFetchResult:
    if force_capture:
        port_wait_s = max(float(port_wait_s), DEFAULT_FORCE_CAPTURE_WAIT_S)
        link_sync_s = max(float(link_sync_s), DEFAULT_FORCE_LINK_SYNC_S)
    if auto_baud and auto_wire:
        combos: tuple[tuple[str, int, bool], ...] = AUTO_WIRE_BAUD_RTS_COMBOS
    elif auto_baud:
        mode_key = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
        combos = tuple((mode_key, b, False) for b in DEFAULT_SAS_BAUD_CANDIDATES)
    elif auto_wire:
        combos = (
            ("raw", int(baud), False),
            ("mux", int(baud), False),
            ("raw", int(baud), True),
        )
    else:
        combos = (
            ((wire_mode or DEFAULT_WIRE_MODE).strip().lower(), int(baud), False),
        )
    probe_timeout = float(timeout_s)
    last_err: RuntimeError | None = None
    attempt_notes: list[str] = []
    for mode_key, baud_try, rts_try in combos:
        mode_key = (mode_key or DEFAULT_WIRE_MODE).strip().lower()
        try:
            return _fetch_meters_once(
                port=port,
                baud=baud_try,
                address=address,
                meter_codes=meter_codes,
                poll_batches=poll_batches,
                timeout_s=probe_timeout,
                port_wait_s=port_wait_s,
                link_sync_s=link_sync_s,
                wakeup_delay_s=wakeup_delay_s,
                wire_mode=mode_key,
                rts=rts_try,
                force_capture=force_capture,
            )
        except RuntimeError as exc:
            last_err = exc
            msg = str(exc)
            attempt_notes.append(
                f"{mode_key}@{baud_try} RTS={'on' if rts_try else 'off'}: "
                f"{msg.split(chr(10))[0]}"
            )
            if _probe_should_continue(msg):
                continue
            raise
    if last_err is not None:
        detail = "\n".join(f"  • {line}" for line in attempt_notes)
        raise RuntimeError(
            f"SAS meter fetch failed on {normalize_com_port(port) or port} after trying "
            f"{len(combos)} wire/baud setting(s).\n\n{detail}"
        ) from last_err
    raise RuntimeError("SAS meter fetch failed")


def _fetch_meters_once(
    *,
    port: str,
    baud: int,
    address: int = DEFAULT_SAS_ADDRESS,
    meter_codes: tuple[str, ...] | list[str] | None = None,
    poll_batches: tuple[tuple[str, ...], ...] | None = None,
    timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
    port_wait_s: float = DEFAULT_PORT_WAIT_S,
    link_sync_s: float = DEFAULT_LINK_SYNC_S,
    wakeup_delay_s: float = DEFAULT_WAKEUP_DELAY_S,
    wire_mode: str = DEFAULT_WIRE_MODE,
    rts: bool = False,
    force_capture: bool = False,
) -> SasMeterFetchResult:
    serial = _require_pyserial()
    mode_key = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    batches = poll_batches
    if batches is None:
        if meter_codes:
            batches = (tuple(meter_codes),)
        else:
            batches = IGT_TESTER_6F_POLL_BATCHES
    port_name = normalize_com_port(port) or DEFAULT_SAS_COM_PORT
    ser, port_used = _open_serial_with_retry(
        serial,
        port=port_name,
        baud=baud,
        wire_mode=mode_key,
        rts=rts,
        wait_s=port_wait_s,
        force_capture=force_capture,
    )
    wire = SasWire(ser, mode=mode_key)
    paste_lines: list[str] = []
    if force_capture:
        paste_lines.append(
            "; Force COM capture: exclusive open, IGT tester stop, 15s port retry, 6s GP sync"
        )
    first_tx = b""
    last_rx = b""
    bill_rows: tuple = ()
    try:
        _wakeup_serial(ser, delay_s=wakeup_delay_s)
        link_ok, sync_rx = _establish_sas_link(
            ser,
            wire=wire,
            address=address,
            duration_s=link_sync_s,
            min_stable_gps=1 if force_capture else 2,
        )
        if not link_ok:
            raise RuntimeError(
                _format_link_silent_error(
                    port_used,
                    baud=baud,
                    wire_mode=mode_key,
                    sync_s=link_sync_s,
                    rts=rts,
                    last_rx=sync_rx,
                )
            )
        for batch_index, batch in enumerate(batches):
            tx = build_igt_tester_6f_poll_frame(batch, address=address)
            if not first_tx:
                first_tx = tx
            _prime_before_long_poll(wire, address=address)
            wire.send_frame(tx)
            rx, rx_raw, elapsed = _read_6f_response(
                ser, wire=wire, address=address, overall_timeout_s=timeout_s
            )
            if not rx:
                raise RuntimeError(
                    _format_no_response_error(
                        port_used,
                        timeout_s=timeout_s,
                        elapsed_s=elapsed,
                        baud=baud,
                        wire_mode=mode_key,
                        tx_frame=tx,
                        rx_raw=rx_raw,
                        batch_index=batch_index,
                    )
                )
            last_rx = rx
            paste_lines.append(format_sas_traffic_line("TX>=", tx))
            paste_lines.append(format_sas_traffic_line("RX<=", rx))
            if batch_index + 1 < len(batches):
                time.sleep(0.05)
        bill_result = fetch_bill_meters_in_session(
            ser,
            wire,
            address=address,
            timeout_s=DEFAULT_BILL_POLL_TIMEOUT_S,
            commands=SAS_BILL_STANDARD_DENOMINATIONS,
        )
        if bill_result.paste_lines:
            paste_lines.append("")
            paste_lines.append("; --- Bill-in long polls ($31-$45, enabled only) ---")
            paste_lines.extend(bill_result.paste_lines)
        bill_rows = bill_result.rows
    finally:
        ser.close()
    paste = "\n".join(paste_lines)
    return SasMeterFetchResult(
        tx_frame=first_tx,
        rx_frame=last_rx,
        paste_text=paste,
        port_used=port_used,
        wire_mode=mode_key,
        baud=int(baud),
        bill_rows=bill_rows,
    )


# --- Bill-in long polls ($31-$37 standard; optional $38-$45) -----------------

SAS_BILL_IN_DENOMINATIONS: tuple[tuple[int, str, int], ...] = (
    (0x31, "$1.00", 100),
    (0x32, "$2.00", 200),
    (0x33, "$5.00", 500),
    (0x34, "$10.00", 1000),
    (0x35, "$20.00", 2000),
    (0x36, "$50.00", 5000),
    (0x37, "$100.00", 10000),
    (0x38, "$500.00", 50000),
    (0x39, "$1,000.00", 100000),
    (0x3A, "$200.00", 20000),
    (0x3B, "$25.00", 2500),
    (0x3C, "$2,000.00", 200000),
    (0x3E, "$2,500.00", 250000),
    (0x3F, "$5,000.00", 500000),
    (0x40, "$10,000.00", 1000000),
    (0x41, "$20,000.00", 2000000),
    (0x42, "$25,000.00", 2500000),
    (0x43, "$50,000.00", 5000000),
    (0x44, "$100,000.00", 10000000),
    (0x45, "$250.00", 25000),
)

# Default COM fetch: standard US bill acceptor denoms ($1-$100) only.
SAS_BILL_STANDARD_DENOMINATIONS: tuple[tuple[int, str, int], ...] = SAS_BILL_IN_DENOMINATIONS[:7]
DEFAULT_BILL_POLL_TIMEOUT_S = 2.5


@dataclass(frozen=True, slots=True)
class SasBillDenomRow:
    sas_cmd: int
    label: str
    face_cents: int
    enabled: bool
    count: int
    amount_cents: int
    sas_code: str = ""
    source: str = "lp"

    @property
    def sas_cmd_hex(self) -> str:
        if self.sas_cmd:
            return f"{self.sas_cmd:02X}"
        return (self.sas_code or "").upper()


@dataclass(frozen=True, slots=True)
class SasBillMeterFetchResult:
    rows: tuple[SasBillDenomRow, ...]
    paste_lines: tuple[str, ...] = ()


def build_simple_long_poll(*, address: int = DEFAULT_SAS_ADDRESS, cmd: int) -> bytes:
    return append_sas_crc(bytes([address & 0xFF, cmd & 0xFF]))


def decode_bill_bcd_count(data: bytes) -> int:
    from network.meter_comparator import _meter_value_to_int

    return int(_meter_value_to_int((data or b"").hex().upper()))


def _extract_simple_meter_frame(raw: bytes, *, address: int, cmd: int) -> bytes:
    addr = address & 0xFF
    want_cmd = cmd & 0xFF
    for blob in _coalesce_rx_candidates(raw):
        for i in range(len(blob)):
            if blob[i] != addr or i + 7 >= len(blob) or blob[i + 1] != want_cmd:
                continue
            frame = blob[i : i + 8]
            body = frame[:-2]
            crc_got = frame[-2] | (frame[-1] << 8)
            if crc16_kermit(body) != crc_got:
                continue
            return frame
    return b""


def parse_bill_in_response_frame(
    frame: bytes,
    *,
    address: int = DEFAULT_SAS_ADDRESS,
    cmd: int,
) -> int | None:
    addr = address & 0xFF
    want_cmd = cmd & 0xFF
    if len(frame) < 8 or frame[0] != addr or frame[1] != want_cmd:
        return None
    body = frame[:-2]
    crc_got = frame[-2] | (frame[-1] << 8)
    if crc16_kermit(body) != crc_got:
        return None
    return decode_bill_bcd_count(frame[2:6])


def _read_simple_meter_response(
    ser,
    *,
    address: int,
    cmd: int,
    overall_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
) -> tuple[bytes, bytes, float]:
    started = time.monotonic()
    deadline = started + max(0.35, float(overall_timeout_s))
    chunks: list[bytes] = []
    _ensure_space_rx(ser)
    while time.monotonic() < deadline:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
        if waiting:
            chunks.append(ser.read(waiting))
            raw = b"".join(chunks)
            frame = _extract_simple_meter_frame(raw, address=address, cmd=cmd)
            if frame:
                return frame, raw, time.monotonic() - started
            continue
        time.sleep(0.005)
    raw = b"".join(chunks)
    return _extract_simple_meter_frame(raw, address=address, cmd=cmd), raw, time.monotonic() - started


def _wire_meter_to_verify_code(wire_code: str) -> str:
    c = (wire_code or "").strip().upper()
    if len(c) != 4:
        return c
    return c[2:4] + c[0:2]


def parse_6f_meter_values_from_paste(paste_text: str) -> dict[str, str]:
    """Map verify-table 6F codes (``000B``, …) to integer meter values from pasted RX."""
    from network.sas_parser import parse_rx_response_ordered

    merged: dict[str, str] = {}
    for chunk in re.findall(r"RX<=\s*([0-9a-fA-F ]+)", paste_text or ""):
        line = f"RX<= {chunk.strip()}"
        if not re.search(r"\b6F\b", line, re.I):
            continue
        for wire_code, value in parse_rx_response_ordered(line):
            merged[_wire_meter_to_verify_code(wire_code)] = str(value)
    return merged


def build_aggregate_bill_rows(
    *,
    paste_text: str = "",
    machine_state: dict[str, object] | None = None,
) -> tuple[SasBillDenomRow, ...]:
    """
    Fallback when per-denom bill LPs ($31–$37) return no RX.

    Uses SAS 6F ``000B`` (total bills in credits) and cabinet stacker totals from
    DeviceManagerData (``notesInStackerCnt`` / ``notesInStackerAmt``).
    """
    from network.accounting_state_loader import (
        cabinet_bill_stacker_amount_credits,
        cabinet_bill_stacker_count,
    )

    state = machine_state or {}
    stacker_amt_raw = cabinet_bill_stacker_amount_credits(state)
    stacker_cnt_raw = cabinet_bill_stacker_count(state)
    sas_amt_raw = (parse_6f_meter_values_from_paste(paste_text).get("000B") or "").strip()
    if sas_amt_raw and stacker_amt_raw:
        from network.meter_comparator import align_bills_in_sas_credits

        sas_amt_raw = align_bills_in_sas_credits(sas_amt_raw, stacker_amt_raw)

    amount_credits = 0
    for raw in (stacker_amt_raw, sas_amt_raw):
        if not raw:
            continue
        try:
            amount_credits = int(str(raw).strip())
            break
        except ValueError:
            continue

    count = 0
    if stacker_cnt_raw:
        try:
            count = int(str(stacker_cnt_raw).strip())
        except ValueError:
            count = 0

    if amount_credits <= 0 and count <= 0:
        return ()

    return (
        SasBillDenomRow(
            sas_cmd=0,
            label="Total Bills In",
            face_cents=0,
            enabled=True,
            count=count,
            amount_cents=amount_credits,
            sas_code="000B",
            source="aggregate",
        ),
    )


def build_bill_display_rows(
    *,
    bill_rows: tuple[SasBillDenomRow, ...] | list[SasBillDenomRow] | None = None,
    paste_text: str = "",
    machine_state: dict[str, object] | None = None,
) -> tuple[SasBillDenomRow, ...]:
    per_denom = tuple(row for row in (bill_rows or ()) if row.enabled)
    if per_denom:
        return per_denom
    return build_aggregate_bill_rows(paste_text=paste_text, machine_state=machine_state)


def _resync_link_before_bill_polls(
    ser,
    wire: SasWire,
    *,
    address: int = DEFAULT_SAS_ADDRESS,
) -> None:
    """Brief GP cadence after heavy 6F batches so simple bill LPs can answer."""
    polls = _general_poll_alternation(address)
    for idx in range(4):
        wire.send_general_poll(polls[idx % 2])
        time.sleep(0.2)
        _read_after_poll(ser, idle_ms=60)
    _ensure_space_rx(ser)
    try:
        ser.reset_input_buffer()
    except Exception:
        pass


def fetch_bill_meters_in_session(
    ser,
    wire: SasWire,
    *,
    address: int = DEFAULT_SAS_ADDRESS,
    timeout_s: float = 3.0,
    commands: tuple[tuple[int, str, int], ...] | None = None,
) -> SasBillMeterFetchResult:
    """Poll bill-in meters; only denominations with a valid response are returned."""
    catalog = commands or SAS_BILL_IN_DENOMINATIONS
    rows: list[SasBillDenomRow] = []
    paste_lines: list[str] = []
    per_poll_timeout = max(float(timeout_s), DEFAULT_BILL_POLL_TIMEOUT_S)
    _resync_link_before_bill_polls(ser, wire, address=address)

    for cmd, label, face_cents in catalog:
        tx = build_simple_long_poll(address=address, cmd=cmd)
        _ensure_space_rx(ser)
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        wire.send_frame(tx)
        rx, rx_raw, _elapsed = _read_simple_meter_response(
            ser,
            address=address,
            cmd=cmd,
            overall_timeout_s=per_poll_timeout,
        )
        if not rx:
            _ensure_space_rx(ser)
            wire.send_frame(tx)
            rx, rx_raw, _elapsed = _read_simple_meter_response(
                ser,
                address=address,
                cmd=cmd,
                overall_timeout_s=per_poll_timeout,
            )

        if rx:
            count = parse_bill_in_response_frame(rx, address=address, cmd=cmd) or 0
            rows.append(
                SasBillDenomRow(
                    sas_cmd=cmd,
                    label=label,
                    face_cents=face_cents,
                    enabled=True,
                    count=count,
                    amount_cents=count * face_cents,
                )
            )
            paste_lines.append(format_sas_traffic_line("TX>=", tx))
            paste_lines.append(format_sas_traffic_line("RX<=", rx))
        else:
            paste_lines.append(format_sas_traffic_line("TX>=", tx))
            hint = f"RX<= (no response - bill {label} LP {cmd:02X})"
            cleaned = rx_raw.strip(b"\x00")
            if cleaned:
                preview = format_sas_traffic_line("RX<=", cleaned[:32])
                if len(cleaned) > 32:
                    preview += " …"
                hint += f" partial: {preview.split(' ', 1)[-1]}"
            paste_lines.append(hint)
        time.sleep(0.05)

    return SasBillMeterFetchResult(rows=tuple(rows), paste_lines=tuple(paste_lines))


def parse_sas_bill_paste(text: str) -> tuple[SasBillDenomRow, ...]:
    cmd_to_meta = {cmd: (label, face) for cmd, label, face in SAS_BILL_IN_DENOMINATIONS}
    merged: dict[int, int] = {}
    chunks = re.findall(
        r"TX>=\s*([0-9a-fA-F ]+).*?RX<=\s*([0-9a-fA-F ]+)",
        text or "",
        flags=re.S,
    )
    for tx_hex, rx_hex in chunks:
        try:
            tx = bytes(int(b, 16) for b in tx_hex.split())
            rx = bytes(int(b, 16) for b in rx_hex.split())
        except ValueError:
            continue
        if len(tx) < 2:
            continue
        cmd = tx[1]
        if cmd not in cmd_to_meta:
            continue
        count = parse_bill_in_response_frame(rx, address=tx[0], cmd=cmd)
        if count is None:
            continue
        merged[cmd] = count

    rows: list[SasBillDenomRow] = []
    for cmd in sorted(merged):
        label, face_cents = cmd_to_meta[cmd]
        count = merged[cmd]
        rows.append(
            SasBillDenomRow(
                sas_cmd=cmd,
                label=label,
                face_cents=face_cents,
                enabled=True,
                count=count,
                amount_cents=count * face_cents,
            )
        )
    return tuple(rows)