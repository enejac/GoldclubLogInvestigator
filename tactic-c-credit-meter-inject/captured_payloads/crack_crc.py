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

def crc16_ibm(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def crc16_ccitt(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def crc16_xmodem(data):
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def crc16_kermit(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

def crc16_mcrf4xx(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc

def crc16_dect_r(data):
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x0589) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc ^ 0x0001

algos = {
    'IBM': crc16_ibm,
    'CCITT': crc16_ccitt,
    'XMODEM': crc16_xmodem,
    'MODBUS': crc16_modbus,
    'KERMIT': crc16_kermit,
    'MCRF4XX': crc16_mcrf4xx,
    'DECT_R': crc16_dect_r,
}

print("=== Full frame ===")
for name, fn in algos.items():
    m = sum(1 for h, c in pairs if fn(h2b(h)) == c)
    print(f"  {name}: {m}/{len(pairs)}")

print("\n=== Data-only (skip 3 bytes) ===")
for name, fn in algos.items():
    m = sum(1 for h, c in pairs if fn(h2b(h)[3:]) == c)
    print(f"  {name}: {m}/{len(pairs)}")

print("\n=== Brute-force poly search ===")
d0 = h2b(pairs[0][0])
d1 = h2b(pairs[1][0])
c0 = pairs[0][1]
c1 = pairs[1][1]
for i in range(len(d0)):
    if d0[i] != d1[i]:
        print(f"  Diff byte at idx {i}: 0x{d0[i]:02X} vs 0x{d1[i]:02X}")
        delta = d0[i] ^ d1[i]
        print(f"  Delta: 0x{delta:02X}")
        crc_xor = c0 ^ c1
        print(f"  CRC XOR: 0x{crc_xor:04X}")
        break

found = []
for poly in range(0x10000, 0x1FFFF):
    def test(data, p=poly):
        crc = 0
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ p if crc & 1 else crc >> 1
        return crc
    ok = True
    for h, c in pairs[:3]:
        if test(h2b(h)) != c:
            ok = False
            break
    if ok:
        found.append(poly)
        if len(found) >= 10:
            break

if found:
    print(f"  Found {len(found)} candidates")
    for p in found:
        def test(data, pp=p):
            crc = 0
            for b in data:
                crc ^= b
                for _ in range(8):
                    crc = (crc >> 1) ^ pp if crc & 1 else crc >> 1
            return crc
        m = sum(1 for h, c in pairs if test(h2b(h)) == c)
        print(f"  Poly 0x{p:05X}: {m}/{len(pairs)}")
else:
    print("  No match with init=0")