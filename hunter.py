from __future__ import annotations

import os
import sys
from pathlib import Path


def _normalize_target_to_base_dir(ip_or_unc: str) -> Path:
    s = (ip_or_unc or "").strip()
    if not s:
        return Path()

    # UNC path provided (support both // and \\)
    if s.startswith("\\\\") or s.startswith("//"):
        s = s.replace("/", "\\")
        if s.startswith("\\\\"):
            return Path(s)
        # starts with // but not \\ (after replace) -> enforce UNC prefix
        return Path("\\\\" + s.lstrip("\\"))

    # Otherwise assume it's an IP (or hostname) and build the standard cabinet state path
    return Path(rf"\\{s}\c$\Goldclub\var\state")


def _should_skip(root: str, filename: str) -> bool:
    r = (root or "").lower()
    f = (filename or "").lower()
    # Skip obvious huge log directories/files (keep the scan focused on state artifacts)
    if f.endswith((".log", ".txt")):
        return True
    if any(x in r for x in ("\\var\\log", "\\logs\\", "\\slotlog", "\\onehand")):
        return True
    return False


def search_cabinet_for_meters(ip_or_unc: str) -> int:
    base_dir = _normalize_target_to_base_dir(ip_or_unc)
    if not str(base_dir):
        print("[!] Empty target.")
        return 0

    print(f"[*] Starting aggressive search in: {base_dir}")
    try:
        if not base_dir.exists():
            print("[!] Path does not exist or is unreachable. Check network/SMB.")
            return 0
    except OSError as e:
        print(f"[!] Could not probe path (OS error): {e}")
        return 0

    # Keywords that indicate we found the right accounting/meter artifacts.
    # Keep these as bytes so we can scan binary-ish files safely.
    keywords: list[bytes] = [
        b'meterName="coinIn"',
        b"metername",
        b"metervalue",
        b"coinin",
        b"coinout",
        b"gamesplayed",
        b"devicemanagerdata",
        b"gm2au",
        b"gm2u",
        # If you have a known expected meter value, include it here.
        b"2962794",
    ]

    match_count = 0
    scanned = 0
    errors = 0

    for root, _dirs, files in os.walk(base_dir):
        for fn in files:
            if _should_skip(root, fn):
                continue

            file_path = Path(root) / fn
            scanned += 1

            try:
                content = file_path.read_bytes()
            except Exception:
                errors += 1
                continue

            lower = content.lower()
            hit_kw: bytes | None = None
            for kw in keywords:
                if kw.lower() in lower:
                    hit_kw = kw
                    break

            if not hit_kw:
                continue

            match_count += 1
            pretty_kw = hit_kw.decode("utf-8", errors="ignore")
            print(f"\n[+] FOUND KEYWORD '{pretty_kw}' IN:")
            print(f"    Path: {file_path}")
            print(f"    Size: {len(content)} bytes")

            # Try to print a small surrounding context snippet (best-effort).
            try:
                text = content.decode("utf-8", errors="ignore")
                needle = pretty_kw.lower() if pretty_kw else hit_kw.decode("utf-8", errors="ignore").lower()
                idx = text.lower().find(needle)
                if idx != -1:
                    start = max(0, idx - 120)
                    end = min(len(text), idx + 120)
                    snippet = text[start:end].replace("\r", " ").replace("\n", " ")
                    print(f"    Context: ...{snippet.strip()}...")
                else:
                    print("    Context: [keyword hit in bytes but not found in decoded text]")
            except Exception:
                print("    Context: [Binary file or decoding failed]")

    print("\n[*] Search complete.")
    print(f"    Files scanned: {scanned}")
    print(f"    Read errors  : {errors}")
    print(f"    Matches      : {match_count}")
    return match_count


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1].strip():
        target = argv[1].strip()
    else:
        target = input("Enter target IP or UNC path (e.g., 10.0.0.90): ").strip()

    search_cabinet_for_meters(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

