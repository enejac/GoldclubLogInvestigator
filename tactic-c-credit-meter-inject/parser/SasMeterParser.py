#!/usr/bin/env python3
"""
SAS Meter-Write Parser for Tactic C.

Parses WinDivert captures (.txt format) and extracts SAS frames using command codes
like 0x0F "Send selected meters (single)". This is Phase 1 discovery: reverse-engineering
the exact byte format for meter writes.

Usage:
    python SasMeterParser.py <capture_file.txt> [--limit N] [--verbose]

Output:
    - TACPARSE meter-write discovery log
    - Frames identified with SAS command, length, and decoded payload
    - Candidate SAS 0x0F meter-write patterns
"""

import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SasFrame:
    """Represents a SAS frame (byte sequence) from the wire."""
    command_code: int
    length: int
    payload: bytes
    hex_str: str
    src_port: Optional[int] = None
    dst_port: Optional[int] = None

    @property
    def cmd_name(self) -> str:
        """Return command name from decode_stage0.py SAS_CMD mapping."""
        SAS_CMD_MAP = {
            0x0F: "Send selected meters (single)",
            0x01: "Shutdown",
            0x02: "Startup",
            0x10: "Send total cancelled credits meter",
            0x1A: "Send current credits",
            0x20: "Send total bill meters (cancelled credits)",
            0x06: "Get/Set coin hopper status",
            0x07: "Send authentication info",
            0x72: "AFT transfer funds",
            0x80: "General poll (host)",
            0x81: "General poll (slave)",
        }
        return SAS_CMD_MAP.get(self.command_code, f"Unknown (0x{self.command_code:02X})")

    @property
    def payload_str(self) -> str:
        """String representation of payload bytes."""
        return ' '.join(f'{b:02X}' for b in self.payload)

    @property
    def payload_hexdump(self) -> str:
        """Hexdump with ASCII representation."""
        hex_part = ' '.join(f'{self.payload[i:i+16]:02X}' for i in range(0, min(32, len(self.payload)), 16))
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in self.payload[:32])
        return f"{hex_part:<45} |{ascii_part}|".rstrip()


def parse_hex_data(hex_str: str) -> bytes:
    """Parse hex string (space-separated) into bytes."""
    hex_str = hex_str.strip().replace(' ', '')
    return bytes.fromhex(hex_str)


def parse_wd_sniff_capture(filepath: Path, limit: Optional[int] = None) -> List[SasFrame]:
    """
    Parse WinDivert sniffer output (.txt from WdSniff.exe).

    Format (time-ordered, one line per packet):
        PKT t=<ms> wall=<datetime dir=DIR sport=SRC dport=DST seq=SEQ ack=ACK flags=FLAGS ob=OUT lb=LOOP len=LEN hex=<HEX>
    """
    frames = []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line_num, line in enumerate(f):
            line = line.strip()

            # Parse PKT header line
            if not line.startswith('PKT '):
                continue

            # Extract direction (S2C or C2S = CommCtrlSAS direction)
            dir_match = re.search(r'dir=(\S+)', line)
            payload_match = re.search(r'hex=([0-9A-Fa-f\s]+)', line)

            if not dir_match or not payload_match:
                continue

            direction = dir_match.group(1)
            hex_payload = payload_match.group(1)

            # Skip loopback B2B (both ports are bridge)
            if direction == 'B2B':
                continue

            # Parse hex payload
            try:
                payload_bytes = parse_hex_data(hex_payload)
            except ValueError:
                continue  # Invalid hex

            # WdSniff adds direction tag to payload hex
            # For S2C: direction 'S2C' is COMMCTRLSAS->AURUM (host poll + commands)
            # For C2S: direction 'C2S' is AURUM->COMMCTRLSAS (responses)
            if direction == 'C2S' and len(payload_bytes) > 0:
                direction_flag = payload_bytes[0]
                actual_payload = payload_bytes[1:]

                # Decode frame assuming direction byte is present
                if len(actual_payload) >= 3:
                    cmd_byte, length = actual_payload[0], actual_payload[1]
                    frame_data = actual_payload[2:2 + length]

                    frames.append(SasFrame(
                        command_code=cmd_byte,
                        length=length,
                        payload=frame_data,
                        hex_str=hex_payload,
                        src_port=None,  # Not in capture
                        dst_port=None   # Not in capture
                    ))

            elif direction == 'S2C' and len(payload_bytes) > 0:
                # For S2C, we need to skip the direction byte (0x01/0x80/0x81) that WdSniff injects
                frame_len = len(payload_bytes)
                direction_byte = payload_bytes[0]

                # Skip direction byte + any framing
                if frame_len >= 3:
                    # Try to detect bridge framing (0x1B prefix)
                    if payload_bytes[0] == 0x1B and len(payload_bytes) >= 3:
                        # SAR: 0x1B + SAS frame (cmd|length|data|crc)
                        cmd_byte = payload_bytes[1]
                        payload_len = payload_bytes[2]

                        if 3 + payload_len <= frame_len:
                            frame_data = payload_bytes[3:3 + payload_len]

                            frames.append(SasFrame(
                                command_code=cmd_byte,
                                length=payload_len,
                                payload=frame_data,
                                hex_str=hex_payload
                            ))

                # Also check for plain SAS commands (no 0x1B) — those are from ACTUAL AFT transfers
                else:
                    for i in range(0, min(len(payload_bytes) - 2, frame_len)):
                        cmd_byte = payload_bytes[i]
                        if cmd_byte in (0x72, 0x0F, 0x10, 0x20, 0x1A):
                            payload_len = payload_bytes[i + 1] if i + 1 < frame_len else 0
                            data_end = i + 2 + payload_len
                            if data_end <= frame_len:
                                frame_data = payload_bytes[i + 2:data_end]

                                frames.append(SasFrame(
                                    command_code=cmd_byte,
                                    length=payload_len,
                                    payload=frame_data,
                                    hex_str=hex_payload
                                ))

            if limit and len(frames) >= limit:
                break

    return frames


def extract_0x0f_commands(frames: List[SasFrame]) -> List[SasFrame]:
    """Filter frames for SAS 0x0F (Send selected meters) commands."""
    return [f for f in frames if f.command_code == 0x0F]


def extract_0x72_commands(frames: List[SasFrame]) -> List[SasFrame]:
    """Filter frames for SAS 0x72 (AFT transfer) commands."""
    return [f for f in frames if f.command_code == 0x72]


def print_frame_analysis(frames: List[SasFrame], label: str):
    """Print analysis of frames."""
    print(f"\n{'=' * 80}")
    print(f"{label}")
    print(f"{'=' * 80}")

    if not frames:
        print(f"[!] NO frames of type {label} found in capture")
        return

    print(f"\nTotal {label} frames: {len(frames)}")
    print(f"\nFrame breakdown by command code:")
    cmd_counts = {}
    for f in frames:
        cmd_counts[f.command_code] = cmd_counts.get(f.command_code, 0) + 1

    for cmd_code in sorted(cmd_counts.keys(), key=lambda x: hex(x)):
        cmd_name = [k for k, v in SasFrame.__dataclass_fields__.values() if k.lower() == 'cmd_name'][0]  # Actually from SAS_CMD_MAP
        cmd_name = SasFrame.__dict__.get('cmd_name', lambda self: f"0x{self.command_code:02X}")(f'SAS_CMD_MAP'? SasFrame().__class__.__dict__['SAS_CMD_MAP'].get(f.command_code, f"Unknown (0x{f.command_code:02X})"))  # Simplified
        print(f"  0x{cmd_code:02X} — {SasFrame.__dict__.get('SAS_CMD_MAP', {}).get(cmd_code, f'Unknown (0x{cmd_code:02X})')} — {cmd_counts[cmd_code]} frames")

    print(f"\nSample frames (first 5):")
    for i, f in enumerate(frames[:5]):
        print(f"\n  [{i}] SAS 0x{f.command_code:02X} (length: {f.length}, payload: {f.length} bytes)")
        print(f"      Frame prefix (hex):  {' '.join(f'{b:02X}' for b in f.payload[:10])}...")
        print(f"                        (ascii): {''.join(chr(b) if 32 <= b < 127 else '.' for b in f.payload[:10])}...")
        print(f"      Full hex:            {f.payload_hexdump[:80]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python SasMeterParser.py <capture_file.txt> [--limit N] [--verbose]")
        print("")
        print("This parser analyzes WinDivert captures (.txt format from WdSniff.exe)")
        print("to discover SAS meter-write command formats for Tactic C.")
        print("")
        print("Output: Finds SAS 0x0F frames (Send selected meters) that OneHand uses")
        print("        to update credit meters directly.")
        sys.exit(1)

    capture_file = Path(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].startswith('--limit') and sys.argv[3].isdigit() else None

    if not capture_file.exists():
        print(f"[!] Capture file not found: {capture_file}")
        sys.exit(1)

    print(f"[*] Parsing capture: {capture_file}")
    print(f"[*] Limit: {limit or 'ALL'} frames")

    frames = parse_wd_sniff_capture(capture_file, limit=limit)

    print(f"[+] Found {len(frames)} total SAS frames")

    # Analyze all command codes
    all_commands = extract_0x72_commands(frames) + extract_0x0f_commands(frames)
    if all_commands:
        print_frame_analysis(all_commands, "SAS COMMANDS IN CAPTURE")
    else:
        print("\n[!] No SAS command frames (0x01-0xFF) found in capture")
        print("    Verify that:")

    # Check 0x72 frames (existing AFT injects)
    t72_frames = extract_0x72_commands(frames)
    if t72_frames:
        print_frame_analysis(t72_frames, "DISCOVERED SAS 0x72 (AFT TRANSFER FUNDS COMMANDS)")

        # Analyzer for meter-write discovery: Build template from 0x72
        print("\n" + "=" * 80)
        print("{} DISCOVERY: TEMPLATE CONSTRUCTION")
        print("=" * 80)
        print("\nAnalyzing existing 0x72 frames to determine expected meter-write format...")
        print("\nExpected format based on decode_stage0.py, hop2-commctrlsas-bridge-to-aurum.md:")
        print("  1. Direction byte (S2C): 0x01")
        print("  2. Frame prefix: 0x1B (CommCtrlSAS bridge framing)")
        print("  3. SAS frame: <cmd> <length> <payload> <crc>")
        print("     - cmd: 0x72 (AFT transfer funds)")
        print("     - length: field length (~0x45)")
        print("     - payload: structure from protocol-raw-traffic.md §1-2")
        print("     - crc: CRC-16/KERMIT (poly 0x1021, reflected 0x8408)")
        print("\nIf 0x0F commands are missing from this capture, these may already be encoded in")
        print("the 0x72 frame's payload (e.g., AFT uses a separate status poll 0xFF to request")
        print("meter updates). Check the AFT status response (command 0xFF) for meter updates.")

    # Check 0x0F frames (target for meter write)
    t0f_frames = extract_0x0f_commands(frames)
    if t0f_frames:
        print_frame_analysis(t0f_frames, "TARGET: SAS 0x0F (SEND SELECTED METERS)")

        print("\n" + "=" * 80)
        print("{} DISCOVERY: SUCCESS - METER-WRITE COMMANDS FOUND")
        print("=" * 80)
        print(f"\n[+] Found {len(t0f_frames)} meters-write commands")
        print("\nMeter-write file template will be:")
        print("  0x1B 0x0F <meterCode> <encodedMeterValue> <CRC>")

        # Auto-generate template
        print(f"\nSample meter-write frame:")
        if t0f_frames:
            f = t0f_frames[0]
            print(f"  Bridge prefix:  1B")
            print(f"  Command:        0x{f.command_code:02X}")
            print(f"  Length:         {f.length} (must match actual meter size)")
            print(f"  Meter code:     {f.payload[:1].hex() if len(f.payload) > 0 else 'N/A'}")
            print(f"  Encoded value:  {f.payload[1:6].hex() if len(f.payload) > 6 else 'N/A'}")
            print(f"  CRC:            {f.payload[-2:].hex() if len(f.payload) >= 2 else 'N/A'}")
    else:
        print("\n[!] NO SAS 0x0F commands found")
        print("    Expected meter-write commands not seen in capture yet")
        print("    Next steps:")
        print("      1. Trigger AFT transfer manually (IGT tester)")
        print("      2. Run detailed capture with '泉-AurumTrafficCapture.ps1 -ForceCommand'")
        print("      3. Or attempt to directly read OneHand.exe memory (Phase 2)")

    print("\n✓ Parse complete")


if __name__ == '__main__':
    main()
