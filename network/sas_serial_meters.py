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
# IGT SAS tester (sastest.ini Poll Rate = 200 ms): steady GP cadence, not long sync floods.
DEFAULT_IGT_POLL_INTERVAL_S = 0.2
DEFAULT_IGT_LINK_SYNC_POLLS = 10
DEFAULT_IGT_INTER_BATCH_POLLS = 3
DEFAULT_IGT_PORT_SETTLE_S = 0.5
DEFAULT_6F_BATCH_READ_S = 3.0
DEFAULT_6F_BATCH_RETRIES = 4
DEFAULT_LINK_POLL_INTERVAL_S = DEFAULT_IGT_POLL_INTERVAL_S  # poll keeper scripts
DEFAULT_RESPONSE_TIMEOUT_S = 15.0
DEFAULT_RESPONSE_IDLE_MS = 350
DEFAULT_AUTO_PROBE_RESPONSE_S = 15.0
# Lab COM4: raw @ 19200 + RTS=on is the usual IGT-host path; then MUX @ 921600.
AUTO_WIRE_BAUD_RTS_COMBOS: tuple[tuple[str, int, bool], ...] = (
    ("raw", 19200, True),
    ("raw", 19200, False),
    ("mux", 921600, True),
    ("mux", 921600, False),
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


def sas_com_blocker_process_names() -> tuple[str, ...]:
    """Windows executables that commonly hold the host SAS COM port open."""
    return _IGT_COM_BLOCKER_EXE_NAMES


def find_running_sas_com_blockers() -> list[str]:
    """Return IGT SAS tester process image names currently running (best effort)."""
    if sys.platform != "win32":
        return []
    found: list[str] = []
    for exe in _IGT_COM_BLOCKER_EXE_NAMES:
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if exe.lower() in (proc.stdout or "").lower():
                found.append(exe)
        except Exception:
            continue
    return found


def probe_com_port_available(
    port: str,
    *,
    baud: int = DEFAULT_SAS_COM_BAUD,
    wire_mode: str = DEFAULT_WIRE_MODE,
) -> tuple[bool, str]:
    """Return whether the SAS host COM port can be opened exclusively (not held by another app)."""
    port_name = normalize_com_port(port) or DEFAULT_SAS_COM_PORT
    serial_module = _require_pyserial()
    mode = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    parity = (
        serial_module.PARITY_NONE
        if mode == "mux"
        else serial_module.PARITY_SPACE
    )
    blockers = find_running_sas_com_blockers()
    try:
        ser = serial_module.Serial(
            **_serial_open_kwargs(
                serial_module,
                port=port_name,
                baud=int(baud),
                parity=parity,
                exclusive=True,
            )
        )
        ser.close()
        return True, f"{port_name} is available for SAS host polling"
    except Exception as exc:
        if blockers:
            who = ", ".join(blockers)
            return False, (
                f"{port_name} is in use ({who}). "
                "Leave the IGT SAS tester running or close it so auto-poll can open the port."
            )
        if _is_retryable_serial_error(exc):
            return False, f"{port_name} is in use or unavailable ({exc})"
        return False, f"Could not probe {port_name}: {exc}"


def run_sas_general_poll_loop(
    ser,
    *,
    wire: SasWire | None = None,
    address: int = DEFAULT_SAS_ADDRESS,
    poll_interval_s: float = DEFAULT_IGT_POLL_INTERVAL_S,
    stop_event=None,
) -> None:
    """IGT-tester-style alternating 80/81 general polls until ``stop_event`` is set."""
    if wire is None:
        wire = SasWire(ser, mode=DEFAULT_WIRE_MODE)
    polls = _general_poll_alternation(address)
    idx = 0
    while stop_event is None or not stop_event.is_set():
        wire.send_general_poll(polls[idx % 2])
        idx += 1
        time.sleep(max(0.05, float(poll_interval_s)))
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
        if waiting:
            try:
                ser.read(waiting)
            except Exception:
                pass


def open_sas_poll_keeper_serial(
    port: str,
    *,
    baud: int = DEFAULT_SAS_COM_BAUD,
    wire_mode: str = DEFAULT_WIRE_MODE,
    rts: bool = False,
    wakeup_delay_s: float = DEFAULT_WAKEUP_DELAY_S,
):
    """Open COM for sustained SAS general polling (IGT tester host role)."""
    serial_module = _require_pyserial()
    port_name = normalize_com_port(port) or DEFAULT_SAS_COM_PORT
    mode = (wire_mode or DEFAULT_WIRE_MODE).strip().lower()
    parity = (
        serial_module.PARITY_NONE
        if mode == "mux"
        else serial_module.PARITY_SPACE
    )
    ser = serial_module.Serial(
        **_serial_open_kwargs(
            serial_module,
            port=port_name,
            baud=int(baud),
            parity=parity,
            exclusive=True,
        )
    )
    _configure_serial_port(ser, wire_mode=mode, rts=rts)
    _wakeup_serial(ser, delay_s=wakeup_delay_s)
    return ser, port_name, SasWire(ser, mode=mode)


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
            f"\n\nWindows reports {req} is busy or unavailable. "
            f"Close IGT SAS tester or any app using that COM port, wait a few seconds, "
            f"then click Get Meters again.\n"
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
    rts: bool = False
    bill_rows: tuple = ()  # tuple[SasBillDenomRow, ...] — bill-in LP fetch
    bill_out_rows: tuple = ()  # tuple[SasBillDenomRow, ...] — bill-out (placeholder / future LP)


def _extract_6f_response_frame(raw: bytes, *, address: int = DEFAULT_SAS_ADDRESS) -> bytes:
    """Return the first complete addr/6F/len/data/CRC frame inside ``raw``."""
    addr = address & 0xFF
    addr_mark = addr | 0x80
    for i in range(len(raw)):
        if raw[i] not in (addr, addr_mark) or i + 3 >= len(raw) or raw[i + 1] != 0x6F:
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
    """True when RX shows the EGM/MUX answered (any non-idle byte)."""
    if not rx:
        return False
    return any(b != 0 for b in rx)


def _drain_serial_rx(ser) -> bytes:
    """Read and discard any bytes already queued (never reset_input_buffer)."""
    chunks: list[bytes] = []
    try:
        while True:
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
            if waiting <= 0:
                break
            chunks.append(ser.read(waiting))
    except Exception:
        pass
    return b"".join(chunks)


def _sync_sas_link_igt(
    ser,
    *,
    wire: SasWire,
    address: int = DEFAULT_SAS_ADDRESS,
    poll_count: int = DEFAULT_IGT_LINK_SYNC_POLLS,
    poll_interval_s: float = DEFAULT_IGT_POLL_INTERVAL_S,
    min_stable_gps: int = 2,
) -> tuple[bool, bytes]:
    """IGT SAS tester cadence: alternating GP @ ~200 ms until the link answers."""
    polls = _general_poll_alternation(address)
    saw_rx = False
    last_rx = b""
    stable = 0
    need_stable = max(1, int(min_stable_gps))
    for idx in range(max(4, int(poll_count))):
        wire.send_general_poll(polls[idx % 2])
        time.sleep(max(0.05, float(poll_interval_s)))
        rx = _read_after_poll(ser, idle_ms=80)
        if rx:
            saw_rx = True
            last_rx = rx
            if _gp_response_is_stable(rx) and len(rx) >= 3:
                stable += 1
                if stable >= need_stable:
                    return True, last_rx
            else:
                stable = max(0, stable - 1)
    if saw_rx and last_rx and any(b != 0 for b in last_rx):
        return True, last_rx
    return False, last_rx


def _inter_poll_between_6f_batches(
    ser,
    *,
    wire: SasWire,
    address: int = DEFAULT_SAS_ADDRESS,
    poll_count: int = DEFAULT_IGT_INTER_BATCH_POLLS,
    poll_interval_s: float = DEFAULT_IGT_POLL_INTERVAL_S,
) -> None:
    """Resume IGT general-poll cadence between 6F batches (scripts/sas_host_loop.py)."""
    polls = _general_poll_alternation(address)
    for idx in range(max(1, int(poll_count))):
        wire.send_general_poll(polls[idx % 2])
        time.sleep(max(0.05, float(poll_interval_s)))
        _read_after_poll(ser, idle_ms=60)


def _read_6f_response(
    ser,
    *,
    wire: SasWire,
    address: int = DEFAULT_SAS_ADDRESS,
    overall_timeout_s: float = DEFAULT_RESPONSE_TIMEOUT_S,
) -> tuple[bytes, bytes, float]:
    """Read a 6F reply while maintaining IGT ~200 ms GP cadence.

    Some EGMs/MUX paths defer the full 6F meter frame until the host keeps
    polling. A short listen window runs first so an immediate reply is not
    corrupted by an early GP.
    """
    started = time.monotonic()
    deadline = started + max(0.5, float(overall_timeout_s))
    polls = _general_poll_alternation(address)
    idx = 0
    chunks: list[bytes] = []
    _ensure_space_rx(ser)
    listen_until = started + min(0.15, float(overall_timeout_s) * 0.05)
    while time.monotonic() < deadline:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
        if waiting:
            chunks.append(ser.read(waiting))
            raw = b"".join(chunks)
            frame = _find_6f_response(raw, address=address)
            if frame:
                return frame, raw, time.monotonic() - started
        now = time.monotonic()
        if now >= listen_until:
            wire.send_general_poll(polls[idx % 2])
            idx += 1
            time.sleep(max(0.05, DEFAULT_IGT_POLL_INTERVAL_S))
            rx = _read_after_poll(ser, idle_ms=80)
            if rx:
                chunks.append(rx)
                raw = b"".join(chunks)
                frame = _find_6f_response(raw, address=address)
                if frame:
                    return frame, raw, time.monotonic() - started
        else:
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
            f"\n\nNo bytes were received{batch_note}. "
            f"{port_used} is not blocked — the SAS link is offline or the wrong COM port was selected."
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
    """One general poll immediately before a long poll (IGT cadence slot)."""
    wire.send_general_poll(_general_poll_alternation(address)[0])
    time.sleep(DEFAULT_IGT_POLL_INTERVAL_S)
    _read_after_poll(wire._ser, idle_ms=80)


def _wakeup_serial(
    ser,
    *,
    delay_s: float = DEFAULT_WAKEUP_DELAY_S,
    reset_buffers: bool = False,
) -> None:
    """sastest.ini Wakeup Delay before polling (DTR already asserted on open)."""
    delay = max(0.0, float(delay_s))
    if delay > 0:
        time.sleep(delay)
    if reset_buffers:
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
        # pyserial on win32 rejects exclusive=False; omit the kwarg unless explicitly True.
        if "exclusive" in params and sys.platform != "win32":
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
        wait_s = max(float(wait_s), DEFAULT_FORCE_CAPTURE_WAIT_S)
    deadline = time.monotonic() + max(0.5, float(wait_s))
    last_exc: BaseException | None = None
    nudged = False
    blockers_killed = False
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
        if force_capture and last_exc is not None and not nudged:
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
            if force_capture:
                time.sleep(DEFAULT_IGT_PORT_SETTLE_S)
            _drain_serial_rx(ser)
            return ser, target
        except Exception as exc:  # noqa: BLE001
            if _is_retryable_serial_error(exc):
                last_exc = exc
                if force_capture and not blockers_killed:
                    force_release_com_port_blockers(wait_s=1.0)
                    blockers_killed = True
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
        f"{port_used} opened successfully — nothing else is holding the port.\n"
        f"The EGM/MUX simply returned 0 bytes to general polls. Check:\n"
        f"• Correct COM port (SAS host cable / MUX, not another USB serial device)\n"
        f"• Cabinet powered on; OneHand / Aurum / CommCtrl running\n"
        f"• SAS/MUX cable seated; game not stuck in a dead link state\n"
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
    wakeup_delay_s: float = DEFAULT_WAKEUP_DELAY_S,
    wire_mode: str = DEFAULT_WIRE_MODE,
    auto_baud: bool = True,
    auto_wire: bool = True,
    force_capture: bool = True,
    skip_bill_polls: bool = True,
    cached_profile: tuple[str, int, bool] | None = None,
) -> SasMeterFetchResult:
    if force_capture:
        port_wait_s = max(float(port_wait_s), DEFAULT_FORCE_CAPTURE_WAIT_S)
    if cached_profile:
        mode_key, baud_try, rts_try = cached_profile
        combos: tuple[tuple[str, int, bool], ...] = (
            ((mode_key or DEFAULT_WIRE_MODE).strip().lower(), int(baud_try), bool(rts_try)),
        )
        combos += tuple(
            c for c in AUTO_WIRE_BAUD_RTS_COMBOS if c not in combos
        )
    elif auto_baud and auto_wire:
        combos = AUTO_WIRE_BAUD_RTS_COMBOS
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
                wakeup_delay_s=wakeup_delay_s,
                wire_mode=mode_key,
                rts=rts_try,
                force_capture=force_capture,
                skip_bill_polls=skip_bill_polls,
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
        port_norm = normalize_com_port(port) or port
        all_link_silent = all(_LINK_SILENT_MARKER in line for line in attempt_notes)
        prefix = ""
        if all_link_silent:
            prefix = (
                f"{port_norm} opened on every attempt — the port is not held by another app.\n"
                f"The EGM/MUX returned no bytes (link offline, wrong COM, or cabinet not running).\n\n"
            )
        raise RuntimeError(
            f"{prefix}SAS meter fetch failed on {port_norm} after trying "
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
    wakeup_delay_s: float = DEFAULT_WAKEUP_DELAY_S,
    wire_mode: str = DEFAULT_WIRE_MODE,
    rts: bool = False,
    force_capture: bool = False,
    skip_bill_polls: bool = True,
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
            "; IGT host capture: 200 ms GP cadence, 6F batches, no buffer reset"
            + ("; 6F only (bill LPs skipped)" if skip_bill_polls else "")
        )
    first_tx = b""
    last_rx = b""
    bill_rows: tuple = ()
    bill_out_rows = catalog_bill_out_rows()
    try:
        if not force_capture:
            _wakeup_serial(ser, delay_s=wakeup_delay_s, reset_buffers=False)
        link_ok, sync_rx = _sync_sas_link_igt(
            ser,
            wire=wire,
            address=address,
            poll_count=DEFAULT_IGT_LINK_SYNC_POLLS,
            min_stable_gps=2,
        )
        if not link_ok:
            raise RuntimeError(
                _format_link_silent_error(
                    port_used,
                    baud=baud,
                    wire_mode=mode_key,
                    sync_s=DEFAULT_IGT_LINK_SYNC_POLLS * DEFAULT_IGT_POLL_INTERVAL_S,
                    rts=rts,
                    last_rx=sync_rx,
                )
            )
        batch_read_s = min(float(timeout_s), DEFAULT_6F_BATCH_READ_S)
        for batch_index, batch in enumerate(batches):
            tx = build_igt_tester_6f_poll_frame(batch, address=address)
            if not first_tx:
                first_tx = tx
            rx = b""
            rx_raw = b""
            elapsed = 0.0
            for attempt in range(DEFAULT_6F_BATCH_RETRIES):
                if attempt:
                    _inter_poll_between_6f_batches(
                        ser,
                        wire=wire,
                        address=address,
                        poll_count=4,
                    )
                wire.send_frame(tx)
                rx, rx_raw, elapsed = _read_6f_response(
                    ser,
                    wire=wire,
                    address=address,
                    overall_timeout_s=batch_read_s,
                )
                if rx:
                    break
            if not rx:
                raise RuntimeError(
                    _format_no_response_error(
                        port_used,
                        timeout_s=batch_read_s,
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
                _inter_poll_between_6f_batches(ser, wire=wire, address=address)
        if not skip_bill_polls:
            time.sleep(DEFAULT_POST_6F_BILL_DELAY_S)
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
            bill_out_rows = catalog_bill_out_rows()
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
        rts=bool(rts),
        bill_rows=bill_rows,
        bill_out_rows=bill_out_rows,
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
# Bill-out uses the same face values; per-denom hopper LPs are not on all EGMs (table shows zeros until supported).
SAS_BILL_OUT_DENOMINATIONS: tuple[tuple[int, str, int], ...] = SAS_BILL_STANDARD_DENOMINATIONS
DEFAULT_BILL_POLL_TIMEOUT_S = 1.25
DEFAULT_POST_6F_BILL_DELAY_S = 0.15


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
    direction: str = "in"  # "in" | "out"

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


def infer_aggregate_bill_face_cents(
    amount_cents: int,
    count: int,
    *,
    catalog: tuple[tuple[int, str, int], ...] = SAS_BILL_STANDARD_DENOMINATIONS,
) -> int | None:
    """When stacker/000B totals map to one catalog denomination, return its face value."""
    amt = int(amount_cents)
    cnt = max(0, int(count))
    if amt <= 0:
        return None
    faces = [face for _, _, face in catalog]
    for try_cnt in ([cnt] if cnt > 0 else []) + [1]:
        matches = [face for face in faces if face * try_cnt == amt]
        if len(matches) == 1:
            return matches[0]
    # Some EGMs report 000B in whole dollars (5 = $5.00, not 500 credits).
    if cnt <= 1 and amt < max(faces, default=0):
        scaled = amt * 100
        matches = [face for face in faces if face == scaled]
        if len(matches) == 1:
            return matches[0]
    return None


def expand_aggregate_bill_to_catalog(
    agg: SasBillDenomRow,
    *,
    direction: str = "in",
    catalog: tuple[tuple[int, str, int], ...] = SAS_BILL_STANDARD_DENOMINATIONS,
) -> tuple[SasBillDenomRow, ...]:
    """Expand a single 000B aggregate row into the full catalog, attributing when unique."""
    display = merge_bill_rows_with_catalog((), catalog, direction=direction)
    amount = int(agg.amount_cents)
    count = int(agg.count)
    face = infer_aggregate_bill_face_cents(amount, count, catalog=catalog)
    if face is None and count <= 0:
        face = infer_aggregate_bill_face_cents(amount, 1, catalog=catalog)
    if face is None:
        return display
    if count <= 0:
        count = 1
    line_amount = face * count
    rows: list[SasBillDenomRow] = []
    for row in display:
        if row.face_cents == face:
            rows.append(
                SasBillDenomRow(
                    sas_cmd=row.sas_cmd,
                    label=row.label,
                    face_cents=face,
                    enabled=True,
                    count=count,
                    amount_cents=line_amount,
                    sas_code=agg.sas_code,
                    source="aggregate",
                    direction=direction,
                )
            )
        else:
            rows.append(row)
    return tuple(rows)


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
            direction="in",
        ),
    )


def paste_has_bill_lp_attempts(paste_text: str) -> bool:
    """True when pasted traffic includes bill-in LP polls ($31-$37)."""
    text = paste_text or ""
    if re.search(r"(?i)Bill-in long polls", text):
        return True
    return bool(re.search(r"(?i)TX\s*>=\s*01\s+3[1-7]\b", text))


def merge_bill_rows_with_catalog(
    responded: tuple[SasBillDenomRow, ...] | list[SasBillDenomRow],
    catalog: tuple[tuple[int, str, int], ...] = SAS_BILL_STANDARD_DENOMINATIONS,
    *,
    direction: str = "in",
) -> tuple[SasBillDenomRow, ...]:
    """Always return one row per catalog denomination (zeros when LP had no RX)."""
    by_cmd = {int(r.sas_cmd): r for r in responded if r.sas_cmd}
    rows: list[SasBillDenomRow] = []
    for cmd, label, face_cents in catalog:
        hit = by_cmd.get(cmd)
        if hit is not None and hit.enabled:
            rows.append(
                SasBillDenomRow(
                    sas_cmd=cmd,
                    label=label,
                    face_cents=face_cents,
                    enabled=True,
                    count=hit.count,
                    amount_cents=hit.amount_cents,
                    sas_code=hit.sas_code,
                    source=hit.source,
                    direction=direction,
                )
            )
        else:
            rows.append(
                SasBillDenomRow(
                    sas_cmd=cmd,
                    label=label,
                    face_cents=face_cents,
                    enabled=False,
                    count=0,
                    amount_cents=0,
                    source="missing" if hit is None else hit.source,
                    direction=direction,
                )
            )
    return tuple(rows)


def catalog_bill_out_rows(
    catalog: tuple[tuple[int, str, int], ...] = SAS_BILL_OUT_DENOMINATIONS,
) -> tuple[SasBillDenomRow, ...]:
    """Placeholder bill-out rows (per-denom hopper polls not yet wired on this EGM path)."""
    return merge_bill_rows_with_catalog((), catalog, direction="out")


def build_bill_rows_from_cabinet_note_meters(
    machine_state: dict[str, object] | None,
) -> tuple[SasBillDenomRow, ...]:
    """Map DeviceManagerData note curInCnt/curInAmt rows to SAS bill-in catalog rows."""
    from network.accounting_state_loader import extract_cabinet_bill_note_meters

    by_face = extract_cabinet_bill_note_meters(machine_state or {})
    if not by_face:
        return ()
    rows: list[SasBillDenomRow] = []
    for cmd, label, face_cents in SAS_BILL_STANDARD_DENOMINATIONS:
        data = by_face.get(face_cents)
        if not data:
            continue
        count = int(data.get("count") or 0)
        amount_cents = int(data.get("amount_cents") or 0)
        if count <= 0 and amount_cents <= 0:
            continue
        if amount_cents <= 0 and count > 0:
            amount_cents = count * face_cents
        if count <= 0 and amount_cents > 0 and face_cents > 0:
            count = amount_cents // face_cents
        rows.append(
            SasBillDenomRow(
                sas_cmd=cmd,
                label=label,
                face_cents=face_cents,
                enabled=True,
                count=count,
                amount_cents=amount_cents,
                source="cabinet",
                direction="in",
            )
        )
    return tuple(rows)


def build_bill_display_rows(
    *,
    bill_rows: tuple[SasBillDenomRow, ...] | list[SasBillDenomRow] | None = None,
    paste_text: str = "",
    machine_state: dict[str, object] | None = None,
) -> tuple[SasBillDenomRow, ...]:
    """Bill-in table rows: SAS LP/paste, else cabinet curMeter notes, else 000B aggregate."""
    from_paste = parse_sas_bill_paste(paste_text)
    responded = tuple(bill_rows or ()) + from_paste
    if responded or paste_has_bill_lp_attempts(paste_text):
        return merge_bill_rows_with_catalog(responded, direction="in")
    cabinet_rows = build_bill_rows_from_cabinet_note_meters(machine_state)
    if cabinet_rows:
        return merge_bill_rows_with_catalog(cabinet_rows, direction="in")
    agg = build_aggregate_bill_rows(paste_text=paste_text, machine_state=machine_state)
    if agg:
        return agg
    return merge_bill_rows_with_catalog((), direction="in")


def build_bill_out_display_rows(
    *,
    bill_rows: tuple[SasBillDenomRow, ...] | list[SasBillDenomRow] | None = None,
) -> tuple[SasBillDenomRow, ...]:
    """Bill-out table rows: full catalog (zeros until hopper/dispenser LPs are supported)."""
    if bill_rows:
        return merge_bill_rows_with_catalog(bill_rows, SAS_BILL_OUT_DENOMINATIONS, direction="out")
    return catalog_bill_out_rows()


# --- Coin panels (COIN IN / OUT / TO DROP BOX / TO HOPPER) -----------------------

SAS_COIN_CATALOG: tuple[tuple[str, int], ...] = (
    ("$0.01", 1),
    ("$0.05", 5),
    ("$0.10", 10),
    ("$0.25", 25),
    ("$0.50", 50),
    ("$1.00", 100),
)

COIN_PANEL_SPECS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "in": ("0000", ("coinin", "gamecoinin", "totalcoinin"), ("curincnt", "coinincnt")),
    "out": ("0001", ("coinout", "totalcoinout", "gamecoinout"), ("coinoutcnt", "curoutcnt")),
    "drop": ("0002", ("curtodropamt", "cointodropboxamt", "totaltodrop"), ("curtodropcnt", "cointodropboxcnt")),
    "hopper": ("", ("cointohopperamt", "hoppercoinoutamt", "hopperoutamt"), ("cointohoppercnt", "hoppercoinoutcnt")),
}


@dataclass(frozen=True, slots=True)
class SasCoinDenomRow:
    label: str
    face_cents: int
    count: int = 0
    amount_cents: int = 0
    source: str = "catalog"
    panel: str = "in"


def _norm_map_lookup(state: dict[str, object], keys: tuple[str, ...]) -> str:
    if not state or not keys:
        return ""
    norm: dict[str, str] = {}
    for k, v in state.items():
        nk = re.sub(r"[^a-z0-9]+", "", str(k).lower())
        if nk:
            norm[nk] = str(v).strip()
    for key in keys:
        nk = re.sub(r"[^a-z0-9]+", "", key.lower())
        if nk in norm and norm[nk]:
            return norm[nk]
    return ""


def coin_credits_raw_to_amount_cents(raw: str) -> int:
    """Convert SAS/cabinet credit integer (100 credits = $1) to amount column cents."""
    text = (raw or "").strip()
    if not text:
        return 0
    if "." in text:
        try:
            return int(round(float(text) * 100))
        except ValueError:
            return 0
    if not re.fullmatch(r"\d+", text):
        return 0
    return int(text)


def catalog_coin_panel_rows(panel: str) -> tuple[SasCoinDenomRow, ...]:
    return tuple(
        SasCoinDenomRow(label=label, face_cents=face_cents, panel=panel)
        for label, face_cents in SAS_COIN_CATALOG
    )


def build_coin_panel_display_rows(
    panel: str,
    *,
    machine_state: dict[str, object] | None = None,
    sas_raw_for_code: str = "",
) -> tuple[tuple[SasCoinDenomRow, ...], dict[str, int] | None]:
    """
    Coin panel body rows (full denomination catalog) plus optional aggregate TOTAL.

    Per-denom coin LPs are not wired on all EGMs; when only cabinet/SAS totals exist,
    the table shows the catalog with zeros and TOTAL from the aggregate meter.
    """
    rows = catalog_coin_panel_rows(panel)
    spec = COIN_PANEL_SPECS.get(panel)
    if not spec:
        return rows, None
    sas_code, amount_keys, count_keys = spec
    raw_amt = _norm_map_lookup(machine_state or {}, amount_keys)
    if not raw_amt and sas_raw_for_code:
        raw_amt = str(sas_raw_for_code).strip()
    if not raw_amt or raw_amt == "0":
        return rows, None
    amount_cents = coin_credits_raw_to_amount_cents(raw_amt)
    count_raw = _norm_map_lookup(machine_state or {}, count_keys)
    count = int(count_raw) if count_raw and count_raw.isdigit() else 0
    if sas_code:
        return rows, {"amount_cents": amount_cents, "count": count, "sas_code": sas_code}
    return rows, {"amount_cents": amount_cents, "count": count}


def build_all_coin_panel_rows(
    *,
    machine_state: dict[str, object] | None = None,
    sas_values: dict[str, str] | None = None,
) -> dict[str, tuple[tuple[SasCoinDenomRow, ...], dict[str, int] | None]]:
    sas_values = sas_values or {}
    out: dict[str, tuple[tuple[SasCoinDenomRow, ...], dict[str, int] | None]] = {}
    for panel in COIN_PANEL_SPECS:
        spec = COIN_PANEL_SPECS[panel]
        sas_code = spec[0]
        out[panel] = build_coin_panel_display_rows(
            panel,
            machine_state=machine_state,
            sas_raw_for_code=sas_values.get(sas_code, "") if sas_code else "",
        )
    return out

def _resync_link_before_bill_polls(
    ser,
    wire: SasWire,
    *,
    address: int = DEFAULT_SAS_ADDRESS,
) -> None:
    """GP cadence after heavy 6F batches so simple bill LPs can answer."""
    polls = _general_poll_alternation(address)
    for idx in range(4):
        wire.send_general_poll(polls[idx % 2])
        time.sleep(0.12)
        _read_after_poll(ser, idle_ms=60)
    _ensure_space_rx(ser)


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
        _prime_before_long_poll(wire, address=address)
        tx = build_simple_long_poll(address=address, cmd=cmd)
        _ensure_space_rx(ser)
        wire.send_frame(tx)
        rx, rx_raw, _elapsed = _read_simple_meter_response(
            ser,
            address=address,
            cmd=cmd,
            overall_timeout_s=per_poll_timeout,
        )
        if not rx:
            cleaned = rx_raw.strip(b"\x00")
            if cleaned:
                _prime_before_long_poll(wire, address=address)
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
                    direction="in",
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
        time.sleep(0.08)

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