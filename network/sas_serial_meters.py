"""Fetch SAS extended meters (6F poll) over a local COM port like the IGT SAS tester."""

from __future__ import annotations

import re
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
    msg = (
        f"Could not open {req} for SAS meter fetch.\n\n"
        f"{detail}\n\n"
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
) -> tuple[bool, bytes]:
    """Sync with alternating general polls; return (saw_rx, last_rx_chunk)."""
    polls = _general_poll_alternation(address)
    deadline = time.monotonic() + max(0.3, float(duration_s))
    idx = 0
    saw_rx = False
    last_rx = b""
    stable = 0
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
            else:
                stable = 0
    return saw_rx and stable >= 2, last_rx


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
    except Exception:
        pass


def _open_serial_with_retry(
    serial_module,
    *,
    port: str,
    baud: int,
    wire_mode: str = DEFAULT_WIRE_MODE,
    rts: bool = False,
    wait_s: float = DEFAULT_PORT_WAIT_S,
    retry_delay_s: float = DEFAULT_PORT_RETRY_DELAY_S,
):
    requested = normalize_com_port(port) or DEFAULT_SAS_COM_PORT
    mode = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    parity = (
        serial_module.PARITY_NONE
        if mode == "mux"
        else serial_module.PARITY_SPACE
    )
    deadline = time.monotonic() + max(0.5, float(wait_s))
    last_exc: BaseException | None = None
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
        try:
            ser = serial_module.Serial(
                port=target,
                baudrate=int(baud),
                bytesize=serial_module.EIGHTBITS,
                parity=parity,
                stopbits=serial_module.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=2.0,
            )
            _configure_serial_port(ser, wire_mode=mode, rts=rts)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            return ser, target
        except Exception as exc:  # noqa: BLE001
            if _is_retryable_serial_error(exc):
                last_exc = exc
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
) -> SasMeterFetchResult:
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
    )
    wire = SasWire(ser, mode=mode_key)
    paste_lines: list[str] = []
    first_tx = b""
    last_rx = b""
    try:
        _wakeup_serial(ser, delay_s=wakeup_delay_s)
        link_ok, sync_rx = _establish_sas_link(
            ser, wire=wire, address=address, duration_s=link_sync_s
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
    )