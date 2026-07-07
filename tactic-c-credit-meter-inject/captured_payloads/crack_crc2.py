pairs = [
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3438053020200C0000", 0xC6C7),
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3439053020200C0000", 0x7946),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3530053020200C0000", 0x3760),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3531053020200C0000", 0x88E1),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3532053020200C0000", 0x586B),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3533053020200C0000", 0xE7EA),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3534053020200C0000", 0xE976),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3535053020200C0000", 0x56F7),
    ("017245000000010000000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3536053020200C0000", 0x23A3),
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3537053020200C0000", 0x893B),
]

def h2b(h):
    return bytes.fromhex(h)

# Try CRC with swapped byte order (little-endian CRC in frame)
print("=== Try swapped CRC bytes ===")
for name, fn in [("IBM", lambda d: _ibm(d)), ("KERMIT", lambda d: _kermit(d))]:
    pass

def _ibm(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def _kermit(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

def _modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

# Try with swapped CRC (low byte first in frame)
print("\n=== Full frame with swapped CRC interpretation ===")
for h, c in pairs[:3]:
    b = h2b(h)
    crc_hi = b[-2]
    crc_lo = b[-1]
    crc_le = (crc_lo << 8) | crc_hi  # little-endian
    crc_be = (crc_hi << 8) | crc_lo  # big-endian (original)
    print(f"  CRC bytes: 0x{crc_hi:02X} 0x{crc_lo:02X} -> BE=0x{crc_be:04X} LE=0x{crc_le:04X}")
    
    # Try various algorithms with both interpretations
    for algo_name, algo_fn in [("IBM", _ibm), ("KERMIT", _kermit), ("MODBUS", _modbus)]:
        result = algo_fn(b)
        match_be = "MATCH!" if result == crc_be else ""
        match_le = "MATCH!" if result == crc_le else ""
        if match_be or match_le:
            print(f"    {algo_name}: 0x{result:04X} {match_be}{match_le}")

# Try data portion only (after addr+cmd+len = bytes 3 onwards)
print("\n=== Data portion (bytes 3+) with swapped CRC ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[3:]  # skip addr, cmd, len
    crc_hi = b[-2]
    crc_lo = b[-1]
    crc_le = (crc_lo << 8) | crc_hi
    crc_be = (crc_hi << 8) | crc_lo
    
    for algo_name, algo_fn in [("IBM", _ibm), ("KERMIT", _kermit), ("MODBUS", _modbus)]:
        result = algo_fn(data)
        match_be = "MATCH!" if result == crc_be else ""
        match_le = "MATCH!" if result == crc_le else ""
        if match_be or match_le:
            print(f"    {algo_name}: 0x{result:04X} {match_be}{match_le}")

# Try with CRC appended to data (verify mode - should give 0)
print("\n=== Verify mode (CRC appended, should yield 0) ===")
for h, c in pairs[:3]:
    b = h2b(h)
    for algo_name, algo_fn in [("IBM", _ibm), ("KERMIT", _kermit), ("MODBUS", _modbus)]:
        result = algo_fn(b)
        if result == 0:
            print(f"    {algo_name}: 0x0000 MATCH!")

# Brute force with different init values
print("\n=== Brute-force init value search (IBM poly) ===")
d0 = h2b(pairs[0][0])
c0 = pairs[0][1]

for init in range(0, 0x10000, 256):
    crc = init
    for b in d0:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    if crc == c0:
        print(f"  Init 0x{init:04X} matches pair 0")
        # Verify against other pairs
        matches = 0
        for h, c in pairs:
            data = h2b(h)
            crc2 = init
            for b in data:
                crc2 ^= b
                for _ in range(8):
                    crc2 = (crc2 >> 1) ^ 0xA001 if crc2 & 1 else crc2 >> 1
            if crc2 == c:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)} matches")

# Also try Kermit poly with different inits
print("\n=== Brute-force init value search (Kermit poly) ===")
for init in range(0, 0x10000, 256):
    crc = init
    for b in d0:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    if crc == c0:
        print(f"  Init 0x{init:04X} matches pair 0")
        matches = 0
        for h, c in pairs:
            data = h2b(h)
            crc2 = init
            for b in data:
                crc2 ^= b
                for _ in range(8):
                    crc2 = (crc2 >> 1) ^ 0x8408 if crc2 & 1 else crc2 >> 1
            if crc2 == c:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)} matches")

# Try with just the data portion (skip addr/cmd/len)
print("\n=== Brute-force init (data-only, IBM poly) ===")
d0_data = h2b(pairs[0][0])[3:]
c0 = pairs[0][1]
for init in range(0, 0x10000, 256):
    crc = init
    for b in d0_data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    if crc == c0:
        print(f"  Init 0x{init:04X} matches pair 0")
        matches = 0
        for h, c in pairs:
            data = h2b(h)[3:]
            crc2 = init
            for b in data:
                crc2 ^= b
                for _ in range(8):
                    crc2 = (crc2 >> 1) ^ 0xA001 if crc2 & 1 else crc2 >> 1
            if crc2 == c:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)} matches")

print("\n=== Brute-force init (data-only, Kermit poly) ===")
for init in range(0, 0x10000, 256):
    crc = init
    for b in d0_data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    if crc == c0:
        print(f"  Init 0x{init:04X} matches pair 0")
        matches = 0
        for h, c in pairs:
            data = h2b(h)[3:]
            crc2 = init
            for b in data:
                crc2 ^= b
                for _ in range(8):
                    crc2 = (crc2 >> 1) ^ 0x8408 if crc2 & 1 else crc2 >> 1
            if crc2 == c:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)} matches")