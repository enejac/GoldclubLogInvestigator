#!/usr/bin/env python3
"""
Build a SAS poll -> slave-response table from a Stage 0 WdSniff dump.

This is OFFLINE, READ-ONLY prep for the 10.0.0.171 AFT-landing path. It does NOT
talk to any cabinet, does NOT inject, and does NOT invent a single byte: every
response it records is a verbatim slave reply that was actually observed on the
working reference cabinet (default 10.0.0.90) during a Stage 0 capture.

Input
-----
A dump file produced by Invoke-Stage0SasSniff.ps1 / WdSniff.exe, one ``PKT ...``
line per captured TCP packet (same format decode_stage0.py consumes):

    PKT t=<ms> wall=HH:MM:SS.fff dir=S2C|C2S sport=.. dport=.. seq=.. ack=.. flags=..
        ob=.. lb=.. len=.. hex=<payloadHex>

Direction (assigned by the sniffer from bridge-port ownership):
  * S2C = CommCtrlSAS -> Aurum  (SAS HOST POLL direction; frames are 0x1B-framed)
  * C2S = Aurum -> CommCtrlSAS  (SAS SLAVE RESPONSE direction; NOT 0x1B-framed)

Method (no guessing)
--------------------
Walk the packets time-ordered. Each S2C payload frame is a host poll/command; the
slave responses are the C2S payload frame(s) that follow it before the next S2C
payload frame. We key the table by the *host poll frame with its 0x1B bridge byte
stripped* (the SAS frame proper), and record every distinct response observed for
that poll together with how many times it occurred and whether its CRC-16/KERMIT
checks out. Polls with no observed response (idle gap) are recorded with an empty
response list so the gap is explicit rather than fabricated.

The emitted JSON is the *only* source of truth a future SAS-slave responder may
use to answer polls on 10.0.0.171. If a poll is not present in the table, the
responder MUST stay silent for it -- never synthesize a reply.

Usage
-----
    python build_sas_response_table.py <dumpfile> [-o out.json] [--min-count N]

Writes <dumpfile>.responsetable.json by default and prints a summary to stdout.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

PKT_RE = re.compile(
    r"^PKT\s+t=\s*(?P<t>\d+)\s+wall=(?P<wall>\S+)\s+dir=(?P<dir>\S+)\s+"
    r"sport=(?P<sport>\d+)\s+dport=(?P<dport>\d+)\s+seq=(?P<seq>\d+)\s+ack=(?P<ack>\d+)\s+"
    r"flags=(?P<flags>\S+)\s+ob=(?P<ob>\d+)\s+lb=(?P<lb>\d+)\s+len=(?P<len>\d+)\s+hex=(?P<hex>\S+)"
)


@dataclass
class Pkt:
    t: int
    wall: str
    dir: str
    flags: str
    length: int
    hex: str


@dataclass
class PollStats:
    poll_unframed_hex: str
    poll_raw_hex: str
    framed_1b: bool
    count: int = 0
    responses: Counter = field(default_factory=Counter)
    no_response: int = 0


def crc16_kermit(data: bytes) -> int:
    """SAS CRC-16/KERMIT (poly 0x1021 reflected 0x8408), little-endian on the wire."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
            crc &= 0xFFFF
    return crc


def crc_status(payload: bytes) -> str:
    """OK/BAD/NA for a frame whose last two bytes are a little-endian CRC-16/KERMIT."""
    if len(payload) < 4:
        return "NA"
    body, crc_le = payload[:-2], payload[-2:]
    got = crc_le[0] | (crc_le[1] << 8)
    return "OK" if crc16_kermit(body) == got else "BAD"


def detect_encoding(path: str) -> str:
    """PowerShell '>' redirection writes UTF-16 LE (with BOM) on Windows PowerShell 5.1."""
    with open(path, "rb") as fb:
        head = fb.read(4)
    if head[:2] == b"\xff\xfe":
        return "utf-16"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if b"\x00" in head:
        return "utf-16"
    return "utf-8"


def parse_dump(path: str):
    header: list[str] = []
    pkts: list[Pkt] = []
    enc = detect_encoding(path)
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
                        flags=m["flags"],
                        length=int(m["len"]),
                        hex=m["hex"].upper(),
                    )
                )
            elif line.startswith(
                ("WDSNIFF", "FILTER", "PORTS", "DURATION_MS", "DIRLEGEND", "START_WALL")
            ):
                header.append(line)
    return header, pkts


def hexbytes(h: str) -> bytes:
    if h in ("-", ""):
        return b""
    try:
        return bytes.fromhex(h)
    except ValueError:
        return b""


def strip_1b(payload: bytes) -> tuple[bytes, bool]:
    """Strip one leading 0x1B bridge byte from an S2C host frame, if present."""
    if payload and payload[0] == 0x1B:
        return payload[1:], True
    return payload, False


def build_table(pkts: list[Pkt]):
    """
    Pair each S2C host poll with the C2S response(s) that follow it (before the
    next S2C payload). Returns an ordered dict keyed by the unframed poll hex.
    """
    table: "OrderedDict[str, PollStats]" = OrderedDict()
    payload_pkts = [p for p in pkts if p.length > 0]

    i = 0
    n = len(payload_pkts)
    while i < n:
        p = payload_pkts[i]
        if p.dir != "S2C":
            # A C2S with no preceding S2C in this window: unsolicited / startup.
            i += 1
            continue

        raw = hexbytes(p.hex)
        unframed, framed = strip_1b(raw)
        key = unframed.hex().upper()
        stats = table.get(key)
        if stats is None:
            stats = PollStats(
                poll_unframed_hex=key,
                poll_raw_hex=p.hex,
                framed_1b=framed,
            )
            table[key] = stats
        stats.count += 1

        # Collect every C2S response until the next S2C payload.
        j = i + 1
        saw_resp = False
        while j < n and payload_pkts[j].dir == "C2S":
            resp = payload_pkts[j]
            stats.responses[resp.hex] += 1
            saw_resp = True
            j += 1
        if not saw_resp:
            stats.no_response += 1
        i = j if j > i + 1 else i + 1

    return table


def serialize(header, pkts, table, src_path, min_count):
    entries = []
    for key, st in table.items():
        if st.count < min_count:
            continue
        unframed = bytes.fromhex(key) if key else b""
        # addr/cmd only apply to multi-byte addressed long polls; a single-byte
        # frame (e.g. 0x80/0x81 general poll) is the poll itself, not addr+cmd.
        addr = unframed[0] if len(unframed) >= 2 else None
        cmd = unframed[1] if len(unframed) >= 2 else None
        responses = []
        for resp_hex, cnt in st.responses.most_common():
            rb = hexbytes(resp_hex)
            responses.append(
                {
                    "hex": resp_hex,
                    "count": cnt,
                    "len": len(rb),
                    "crc": crc_status(rb),
                }
            )
        entries.append(
            OrderedDict(
                [
                    ("poll_unframed_hex", st.poll_unframed_hex),
                    ("poll_raw_hex", st.poll_raw_hex),
                    ("bridge_1b_framed", st.framed_1b),
                    ("addr", None if addr is None else f"0x{addr:02X}"),
                    ("cmd", None if cmd is None else f"0x{cmd:02X}"),
                    ("poll_count", st.count),
                    ("polls_with_no_response", st.no_response),
                    ("distinct_responses", len(st.responses)),
                    ("responses", responses),
                ]
            )
        )

    # Stable, human-scannable order: most-frequent polls first.
    entries.sort(key=lambda e: e["poll_count"], reverse=True)

    return OrderedDict(
        [
            ("schema", "sas-response-table/v1"),
            ("generated_utc", datetime.now(timezone.utc).isoformat()),
            ("source_dump", src_path),
            ("source_header", header),
            ("note", (
                "Responses are verbatim slave replies observed on the reference "
                "cabinet. A responder MUST NOT answer any poll absent from this "
                "table, and MUST NOT synthesize bytes. CRC=OK marks a frame whose "
                "trailing 2 bytes pass CRC-16/KERMIT; single-byte polls/acks are NA."
            )),
            ("packets_parsed", len(pkts)),
            ("distinct_polls", len(entries)),
            ("entries", entries),
        ]
    )


def main() -> int:
    argv = sys.argv[1:]
    out_path = None
    min_count = 1
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-o", "--out"):
            i += 1
            if i < len(argv):
                out_path = argv[i]
        elif a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        elif a.startswith("--min-count"):
            try:
                min_count = int(a.split("=", 1)[1]) if "=" in a else int(argv[i + 1])
                if "=" not in a:
                    i += 1
            except (IndexError, ValueError):
                pass
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            positional.append(a)
        i += 1

    if not positional:
        print("usage: python build_sas_response_table.py <dumpfile> [-o out.json] [--min-count N]")
        return 1

    src = positional[0]
    header, pkts = parse_dump(src)
    if not pkts:
        print(f"[!] No PKT lines parsed from {src} -- is it a Stage 0 WdSniff dump?")
        return 2

    table = build_table(pkts)
    doc = serialize(header, pkts, table, src, min_count)

    out = out_path or (src + ".responsetable.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")

    # ---- stdout summary ----
    print(f"source dump      : {src}")
    print(f"packets parsed   : {doc['packets_parsed']}")
    print(f"distinct polls   : {doc['distinct_polls']}")
    print("")
    print(f"{'poll (unframed)':<24} {'addr':<5} {'cmd':<5} {'polls':>6} {'noResp':>7} {'resps':>6}")
    print("-" * 60)
    for e in doc["entries"]:
        poll = e["poll_unframed_hex"] or "(empty)"
        if len(poll) > 22:
            poll = poll[:19] + "..."
        print(
            f"{poll:<24} {str(e['addr']):<5} {str(e['cmd']):<5} "
            f"{e['poll_count']:>6} {e['polls_with_no_response']:>7} {e['distinct_responses']:>6}"
        )
    print("")
    print(f"[written] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
