# Correct pairs: 72 bytes data + 2 bytes CRC = 74 bytes total
pairs = [
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3438053020200C0000", "C6C7"),
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3439053020200C0000", "7946"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3530053020200C0000", "3760"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3531053020200C0000", "88E1"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3532053020200C0000", "586B"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3533053020200C0000", "E7EA"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3534053020200C0000", "E976"),
    ("017245000000000100000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3535053020200C0000", "56F7"),
    ("017245000000010000000000000000000000000000000903000000000000000000000000000000000000000000001200657374205472616E73616374696F6E3536053020200C0000", "23A3"),
    ("0172450000000000000000000000000000001000000009030000000000000000000000000000000000000000001200657374205472616E73616374696F6E3537053020200C0000", "893B"),
]

def h2b(h):
    return bytes.fromhex(h)

def crc16_ibm(data, init=0x0000):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def crc16_kermit(data, init=0x0000):
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

def crc16_ccitt(data, init=0xFFFF):
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

# Test all pairs with different algorithms and data ranges
print("=== Testing all algorithms on full data (72 bytes) ===")
for algo_name, algo_fn in [("IBM", crc16_ibm), ("KERMIT", crc16_kermit), ("CCITT", crc16_ccitt)]:
    matches = 0
    for data_hex, crc_hex in pairs:
        data = h2b(data_hex)
        expected = int(crc_hex, 16)
        result = algo_fn(data)
        if result == expected:
            matches += 1
    print(f"  {algo_name}: {matches}/{len(pairs)}")

# Try with data minus last 2 bytes (the 0C0000 at end)
print("\n=== Testing on data minus trailing bytes ===")
for algo_name, algo_fn in [("IBM", crc16_ibm), ("KERMIT", crc16_kermit)]:
    for trim in [2, 4, 6]:
        matches = 0
        for data_hex, crc_hex in pairs:
            data = h2b(data_hex)[:-trim]
            expected = int(crc_hex, 16)
            result = algo_fn(data)
            if result == expected:
                matches += 1
        if matches > 0:
            print(f"  {algo_name} (trim {trim}): {matches}/{len(pairs)}")

# Brute force: try all init values for IBM and Kermit
print("\n=== Brute force init values (IBM) ===")
d0 = h2b(pairs[0][0])
c0 = int(pairs[0][1], 16)
for init in range(0, 0x10000):
    if crc16_ibm(d0, init) == c0:
        print(f"  Found init 0x{init:04X} for pair 0")
        # Verify all pairs
        matches = 0
        for data_hex, crc_hex in pairs:
            data = h2b(data_hex)
            expected = int(crc_hex, 16)
            if crc16_ibm(data, init) == expected:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)}")
        if matches == len(pairs):
            print(f"    *** PERFECT MATCH: CRC-16/IBM with init=0x{init:04X} ***")

print("\n=== Brute force init values (Kermit) ===")
for init in range(0, 0x10000):
    if crc16_kermit(d0, init) == c0:
        print(f"  Found init 0x{init:04X} for pair 0")
        matches = 0
        for data_hex, crc_hex in pairs:
            data = h2b(data_hex)
            expected = int(crc_hex, 16)
            if crc16_kermit(data, init) == expected:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)}")
        if matches == len(pairs):
            print(f"    *** PERFECT MATCH: CRC-16/Kermit with init=0x{init:04X} ***")

print("\n=== Brute force init values (CCITT) ===")
for init in range(0, 0x10000, 256):
    if crc16_ccitt(d0, init) == c0:
        print(f"  Found init 0x{init:04X} for pair 0")
        matches = 0
        for data_hex, crc_hex in pairs:
            data = h2b(data_hex)
            expected = int(crc_hex, 16)
            if crc16_ccitt(data, init) == expected:
                matches += 1
        print(f"    Verified: {matches}/{len(pairs)}")