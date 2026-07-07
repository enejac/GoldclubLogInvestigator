#!/usr/bin/env python3
"""
Decode a Stage 0 WdSniff.exe dump into a LABELED, human-readable SAS transcript.

Input: a dump file produced by Invoke-Stage0SasSniff.ps1 / WdSniff.exe, whose body is
one ``PKT ...`` line per captured TCP packet:

    PKT t=<ms> wall=HH:MM:SS.fff dir=S2C|C2S sport=.. dport=.. seq=.. ack=.. flags=..
        ob=.. lb=.. len=.. hex=<payloadHex>

Direction (assigned by the sniffer from bridge-port ownership):
  * S2C = CommCtrlSAS -> Aurum  (SAS HOST POLL direction; frames are 0x1B-framed)
  * C2S = Aurum -> CommCtrlSAS  (SAS SLAVE RESPONSE direction; NOT 0x1B-framed)

This script:
  * decodes each non-trivial SAS frame (address/command + best-effort interpretation),
  * verifies CRC-16/KERMIT on full frames as framing evidence,
  * pairs each host poll with the immediately following slave response,
  * summarizes the steady-state cycle and lists which SAS long polls were observed.

Usage:  python decode_stage0.py <dumpfile> [--max-pairs N]
Writes: <dumpfile>.transcript.txt  (and prints a summary to stdout)
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass

PKT_RE = re.compile(
    r"^PKT\s+t=\s*(?P<t>\d+)\s+wall=(?P<wall>\S+)\s+dir=(?P<dir>\S+)\s+"
    r"sport=(?P<sport>\d+)\s+dport=(?P<dport>\d+)\s+seq=(?P<seq>\d+)\s+ack=(?P<ack>\d+)\s+"
    r"flags=(?P<flags>\S+)\s+ob=(?P<ob>\d+)\s+lb=(?P<lb>\d+)\s+len=(?P<len>\d+)\s+hex=(?P<hex>\S+)"
)

# SAS long-poll / command code names (best-effort; from the SAS 6.02-style command set).
# Anything not listed is printed as "Unknown (0xNN)" so it is clearly flagged.
SAS_CMD = {
    0x01: "Shutdown",
    0x02: "Startup",
    0x0F: "Send selected meters (single)",  # 0x0F send meter, contextual
    0x10: "Send total cancelled credits meter",
    0x11: "Send total coin-in meter",
    0x12: "Send total coin-out meter",
    0x13: "Send total drop meter",
    0x14: "Send total jackpot meter",
    0x15: "Send games-played meter",
    0x16: "Send games-won meter",
    0x17: "Send games-lost meter",
    0x18: "Send games-since-last-power-up / door",
    0x19: "Send meters 11..15",
    0x1A: "Send current credits",
    0x1B: "Send handpay information",
    0x1C: "Send meters",
    0x1F: "Send gaming machine ID & information",
    0x20: "Send total bill meters (cancelled credits)",
    0x21: "ROM signature verification",
    0x2A: "Send true coin-in / extended meters",
    0x2D: "Send extended meters for game N",
    0x2F: "Send selected meters for game N",
    0x31: "Send total drop / number of games",
    0x36: "Send legacy bonus win amount",
    0x46: "Send selected meters (extended, multi)",
    0x48: "Send last accepted bill",
    0x4C: "Set/extend ticket / validation",
    0x4D: "Send current hopper status",
    0x50: "Send total number of games implemented",
    0x51: "Send game N meters / config",
    0x52: "Send game N configuration",
    0x53: "Send selected game-N meters",
    0x54: "Send SAS version & gaming machine serial id",
    0x55: "Send selected game number",
    0x56: "Send enabled game numbers",
    0x57: "Send pending cashout info",
    0x6E: "Send authentication info",
    0x6F: "Send extended meters (selected, multi)",
    0x70: "Send ticket validation data",
    0x71: "Redeem ticket",
    0x72: "AFT transfer funds",
    0x73: "AFT register gaming machine",
    0x74: "AFT game lock & status request",
    0x75: "Set AFT receipt data",
    0x76: "Set custom AFT ticket data",
    0x7B: "Extended validation status",
    0x7C: "Set extended ticket data",
    0x7D: "Set ticket data",
    0x7E: "Send current date and time / set",
    0x7F: "Receive date and time",
    0x8A: "Remote handpay reset",
    0x94: "Remote shutdown / enable",
    0xA0: "Send enabled features / game numbers",
    0xA8: "Enable jackpot handpay reset",
    0xAF: "Send selected game combinations",
    0xB5: "Send legacy bonus meters",
}

# Real-time / general-poll EGM exception codes (single-byte responses on C2S).
SAS_EXCEPTION = {
    0x00: "ACK / no activity (idle)",
    0x01: "Slot door opened",
    0x02: "Slot door closed",
    0x03: "Drop door opened",
    0x04: "Drop door closed",
    0x05: "Card cage opened",
    0x06: "Card cage closed",
    0x07: "AC power applied",
    0x08: "AC power lost",
    0x09: "Cashbox door opened",
    0x0A: "Cashbox door closed",
    0x0B: "Cashbox removed",
    0x0C: "Cashbox installed",
    0x0F: "Bill validator door opened",
    0x11: "Reel tilt",
    0x17: "Handpay is pending",
    0x18: "Handpay was reset",
    0x19: "No progressive information",
    0x1F: "Printer communication error",
    0x20: "Printer paper out",
    0x27: "Power off card cage access",
    0x28: "Bill jam",
    0x31: "Coin in tilt",
    0x3D: "Cash out button pressed",
    0x3E: "Ticket has been inserted",
    0x40: "Reel N tilt",
    0x51: "Handpay validated",
    0x52: "AFT transfer complete",
    0x53: "AFT request for host cashout",
    0x54: "AFT request for host to cashout win",
    0x55: "AFT request to register",
    0x56: "AFT registration acknowledged",
    0x57: "AFT registration cancelled",
    0x66: "Game locked",
    0x67: "Game lock released / exception buffer overflow",
    0x68: "Lamp test",
    0x69: "Operator changed options",
    0x6F: "EGM reset / NV memory restart",
    0x7E: "Game has started",
    0x7F: "Game has ended",
}


@dataclass
class Pkt:
    t: int
    wall: str
    dir: str
    sport: int
    dport: int
    seq: int
    ack: int
    flags: str
    ob: int
    lb: int
    length: int
    hex: str


def crc16_kermit(data: bytes) -> int:
    """SAS CRC-16/KERMIT (poly 0x1021 reflected 0x8408), little-endian on the wire."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def _detect_encoding(path: str) -> str:
    """PowerShell '>' redirection writes UTF-16 LE (with BOM) on Windows PowerShell 5.1."""
    with open(path, "rb") as fb:
        head = fb.read(4)
    if head[:2] == b"\xff\xfe":
        return "utf-16"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    # Heuristic: lots of NUL bytes -> UTF-16 without BOM.
    if b"\x00" in head:
        return "utf-16"
    return "utf-8"


def parse_dump(path: str):
    header: list[str] = []
    pkts: list[Pkt] = []
    footer: list[str] = []
    enc = _detect_encoding(path)
    with open(path, encoding=enc, errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            m = PKT_RE.match(line)
            if m:
                pkts.append(
                    Pkt(
                        t=int(m["t"]),
                        wall=m["wall"],
                        dir=m["dir"],
                        sport=int(m["sport"]),
                        dport=int(m["dport"]),
                        seq=int(m["seq"]),
                        ack=int(m["ack"]),
                        flags=m["flags"],
                        ob=int(m["ob"]),
                        lb=int(m["lb"]),
                        length=int(m["len"]),
                        hex=m["hex"],
                    )
                )
            elif line.startswith(("WDSNIFF", "FILTER", "PORTS", "DURATION_MS", "DIRLEGEND", "START_WALL", "OPEN")):
                header.append(line)
            elif line.startswith(("END_WALL", "PACKETS", "CLOSE", "EXITCODE")):
                footer.append(line)
    return header, pkts, footer


def hexbytes(h: str) -> bytes:
    if h in ("-", ""):
        return b""
    try:
        return bytes.fromhex(h)
    except ValueError:
        return b""


def cmd_name(code: int) -> str:
    n = SAS_CMD.get(code)
    return f"0x{code:02X} {n}" if n else f"0x{code:02X} Unknown long poll"


def exc_name(code: int) -> str:
    n = SAS_EXCEPTION.get(code)
    return f"0x{code:02X} {n}" if n else f"0x{code:02X} Unknown exception/response"


def decode_frame(direction: str, payload: bytes) -> tuple[str, str]:
    """
    Return (short_label, detail) for a SAS frame.

    S2C (host->Aurum) frames are 0x1B-framed; strip one leading 0x1B.
    C2S (Aurum->host) frames are NOT framed.
    """
    if not payload:
        return ("(no payload)", "")

    framed = payload
    framing = ""
    if direction == "S2C" and framed and framed[0] == 0x1B:
        framing = "0x1B-framed; "
        framed = framed[1:]

    if not framed:
        return ("(empty after framing)", framing)

    # General poll: a single byte 0x80/0x81 (alternating sync) addressed at the EGM.
    if len(framed) == 1:
        b = framed[0]
        if b in (0x80, 0x81):
            return (f"General Poll {b:02X}", f"{framing}host general poll (alternating sync bit), EGM addr 01")
        if direction == "C2S":
            return (f"RT/exception {b:02X}", f"{framing}{exc_name(b)}")
        # Single-byte host poll that isn't 80/81
        return (f"Single byte {b:02X}", f"{framing}single SAS byte")

    # Multi-byte frame: addr, cmd, [data...], possibly CRC16 at the end.
    addr = framed[0]
    cmd = framed[1]
    crc_note = ""
    if len(framed) >= 4:
        body, crc_le = framed[:-2], framed[-2:]
        calc = crc16_kermit(body)
        got = crc_le[0] | (crc_le[1] << 8)
        crc_note = " CRC=OK" if calc == got else f" CRC=BAD(calc {calc:04X} got {got:04X})"

    if direction == "S2C":
        label = f"Host long poll {cmd:02X}"
        detail = f"{framing}addr={addr:02X} cmd={cmd_name(cmd)} datalen={len(framed)-2}{crc_note}"
    else:
        label = f"Slave response {cmd:02X}"
        detail = f"{framing}addr={addr:02X} cmd={cmd_name(cmd)} datalen={len(framed)-2}{crc_note}"
    return (label, detail)


def is_trivial(direction: str, payload: bytes) -> bool:
    """Steady-state noise: general poll 80/81 (S2C) and the idle 00 ack (C2S)."""
    framed = payload
    if direction == "S2C" and framed and framed[0] == 0x1B:
        framed = framed[1:]
    if len(framed) == 1:
        if direction == "S2C" and framed[0] in (0x80, 0x81):
            return True
        if direction == "C2S" and framed[0] == 0x00:
            return True
    return False


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_pairs = 14
    for a in sys.argv[1:]:
        if a.startswith("--max-pairs"):
            try:
                max_pairs = int(a.split("=", 1)[1])
            except (IndexError, ValueError):
                pass
    if not args:
        print("usage: python decode_stage0.py <dumpfile> [--max-pairs=N]")
        return 1
    path = args[0]

    header, pkts, footer = parse_dump(path)
    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)

    emit("=" * 78)
    emit("STAGE 0 SAS BUS TRANSCRIPT (read-only sniff, direction-labeled)")
    emit("=" * 78)
    emit(f"source dump : {path}")
    for h in header:
        emit(f"  {h}")
    for h in footer:
        emit(f"  {h}")
    emit(f"  parsed PKT lines: {len(pkts)}")
    emit("")

    # ---- control-plane (connection) timeline: SYN/SYNACK/FIN/RST ----
    emit("-" * 78)
    emit("CONNECTION / CONTROL TIMELINE (SYN/FIN/RST -- bring-up & teardown evidence)")
    emit("-" * 78)
    ctrl = [p for p in pkts if any(c in p.flags for c in ("S", "F", "R"))]
    if not ctrl:
        emit("  (none -- steady-state, no connection changes during the window)")
    for p in ctrl:
        emit(
            f"  {p.wall} dir={p.dir} {p.sport}->{p.dport} flags={p.flags} "
            f"seq={p.seq} ack={p.ack} len={p.length}"
        )
    emit("")

    # ---- per-direction / per-port payload frame frequency ----
    emit("-" * 78)
    emit("PAYLOAD FRAME FREQUENCY (by direction + decoded type)")
    emit("-" * 78)
    freq: Counter = Counter()
    flow_freq: Counter = Counter()
    for p in pkts:
        if p.length == 0:
            continue
        payload = hexbytes(p.hex)
        label, _ = decode_frame(p.dir, payload)
        freq[(p.dir, label)] += 1
        flow_freq[(p.dir, f"{p.sport}->{p.dport}")] += 1
    for (d, label), n in freq.most_common():
        emit(f"  {d}  {label:<26} x{n}")
    emit("")
    emit("  payload-bearing flows (src->dst):")
    for (d, flow), n in flow_freq.most_common():
        emit(f"    {d}  {flow:<16} x{n}")
    emit("")

    # ---- which SAS long polls / responses were observed ----
    emit("-" * 78)
    emit("SAS LONG POLLS / NON-TRIVIAL COMMANDS OBSERVED")
    emit("-" * 78)
    seen_cmds: dict[int, str] = {}
    for p in pkts:
        if p.length == 0:
            continue
        payload = hexbytes(p.hex)
        framed = payload[1:] if (p.dir == "S2C" and payload[:1] == b"\x1b") else payload
        if len(framed) >= 2 and not (len(framed) == 1):
            # treat as addr+cmd only when more than a single sync byte
            if not (len(framed) == 1):
                if len(framed) >= 2 and framed[0] in (0x00, 0x01) and framed[1] not in (0x80, 0x81):
                    seen_cmds[framed[1]] = cmd_name(framed[1])
    if seen_cmds:
        for code in sorted(seen_cmds):
            emit(f"  {cmd_name(code)}")
    else:
        emit("  (none beyond general poll 80/81 + idle 00 in this window)")
    emit("")

    # ---- full time-ordered NON-TRIVIAL transcript, paired ----
    emit("-" * 78)
    emit("NON-TRIVIAL FRAMES (time-ordered; host polls paired with following responses)")
    emit("-" * 78)
    nontrivial = [
        p for p in pkts
        if p.length > 0 and not is_trivial(p.dir, hexbytes(p.hex))
    ]
    if not nontrivial:
        emit("  (none -- only general poll 80/81 + idle 00 seen)")
    else:
        for p in nontrivial:
            payload = hexbytes(p.hex)
            label, detail = decode_frame(p.dir, payload)
            arrow = "HOST  >>" if p.dir == "S2C" else "SLAVE <<"
            emit(f"  {p.wall} {arrow} [{p.dir}] {p.sport}->{p.dport} flags={p.flags} len={p.length}")
            emit(f"           raw={p.hex}")
            emit(f"           {label}: {detail}")
    emit("")

    # ---- steady-state sample: first N host-poll / response pairs ----
    emit("-" * 78)
    emit(f"STEADY-STATE SAMPLE (first {max_pairs} poll/response pairs, time-ordered)")
    emit("-" * 78)
    pairs_shown = 0
    i = 0
    while i < len(pkts) and pairs_shown < max_pairs:
        p = pkts[i]
        if p.length > 0 and p.dir == "S2C":
            payload = hexbytes(p.hex)
            label, detail = decode_frame(p.dir, payload)
            emit(f"  {p.wall} HOST  >> {p.hex:<10} {label}  ({detail})")
            # find next C2S payload packet
            j = i + 1
            while j < len(pkts):
                q = pkts[j]
                if q.length > 0 and q.dir == "C2S":
                    ql, qd = decode_frame(q.dir, hexbytes(q.hex))
                    emit(f"  {q.wall} SLAVE << {q.hex:<10} {ql}  ({qd})")
                    break
                j += 1
            pairs_shown += 1
        i += 1
    emit("")

    text = "\n".join(out)
    print(text)
    out_path = path + ".transcript.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n[written] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
