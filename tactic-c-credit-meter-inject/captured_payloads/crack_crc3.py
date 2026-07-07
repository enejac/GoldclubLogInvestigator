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

# Analyze the CRC bytes more carefully
# The CRC is the last 2 bytes of the full payload
for h, c in pairs:
    b = h2b(h)
    # The CRC bytes are at positions -2 and -1
    print(f"CRC bytes: [{b[-2]:02X}] [{b[-1]:02X}] -> expected 0x{c:04X}")
    # What if CRC is stored as [low][high]?
    le_crc = (b[-1] << 8) | b[-2]
    print(f"  If LE: 0x{le_crc:04X}, if BE: 0x{c:04X}")

# The CRC bytes in the hex string are the last 4 chars
print("\n=== Last 4 hex chars (CRC) ===")
for h, c in pairs:
    last4 = h[-4:]
    print(f"  {last4} -> 0x{last4}")

# Let's look at what the CRC actually is in the raw hex
# The hex string ends with the CRC bytes
# For pair 0: ...0C0000C6C7 -> CRC bytes are C6 C7
# For pair 1: ...0C00007946 -> CRC bytes are 79 46
# So CRC is stored as [high][low] in the frame

# Try CRC-16/ARC (same as IBM but different name)
def crc16_arc(data):
    crc = 0x0000
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

# Try with different byte orderings of the input
print("\n=== Try reversed byte order ===")
for h, c in pairs[:3]:
    b = h2b(h)
    rev = b[::-1]
    result = crc16_arc(rev)
    print(f"  Reversed: 0x{result:04X} vs 0x{c:04X} {'MATCH!' if result == c else ''}")

# Try CRC on just the data portion (after addr+cmd+len)
print("\n=== CRC on data portion (bytes 3 to -2) ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[3:-2]  # skip addr/cmd/len and CRC
    result = crc16_arc(data)
    print(f"  IBM: 0x{result:04X} vs 0x{c:04X} {'MATCH!' if result == c else ''}")

# Try with the length byte included in CRC
print("\n=== CRC on len+data (bytes 2 to -2) ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[2:-2]
    result = crc16_arc(data)
    print(f"  IBM: 0x{result:04X} vs 0x{c:04X} {'MATCH!' if result == c else ''}")

# Try with cmd+len+data
print("\n=== CRC on cmd+len+data (bytes 1 to -2) ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[1:-2]
    result = crc16_arc(data)
    print(f"  IBM: 0x{result:04X} vs 0x{c:04X} {'MATCH!' if result == c else ''}")

# Try with full frame minus CRC
print("\n=== CRC on full frame minus CRC (bytes 0 to -2) ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[:-2]
    result = crc16_arc(data)
    print(f"  IBM: 0x{result:04X} vs 0x{c:04X} {'MATCH!' if result == c else ''}")

# Maybe the CRC is computed differently - try XOR-based checksum
print("\n=== Simple checksums ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[:-2]
    # Sum of all bytes mod 65536
    s = sum(data) & 0xFFFF
    # XOR of all 16-bit words
    xor16 = 0
    for i in range(0, len(data) - 1, 2):
        xor16 ^= (data[i] << 8) | data[i+1]
    if len(data) % 2:
        xor16 ^= data[-1] << 8
    print(f"  Sum: 0x{s:04X}, XOR16: 0x{xor16:04X} vs 0x{c:04X}")

# Try Fletcher-16
print("\n=== Fletcher-16 ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[:-2]
    sum1 = 0
    sum2 = 0
    for byte in data:
        sum1 = (sum1 + byte) % 255
        sum2 = (sum2 + sum1) % 255
    fletcher = (sum2 << 8) | sum1
    print(f"  Fletcher-16: 0x{fletcher:04X} vs 0x{c:04X} {'MATCH!' if fletcher == c else ''}")

# Try Fletcher-16 with different mod
print("\n=== Fletcher-16 variants ===")
for mod in [251, 253, 255, 256, 257]:
    for h, c in pairs[:3]:
        b = h2b(h)
        data = b[:-2]
        sum1 = 0
        sum2 = 0
        for byte in data:
            sum1 = (sum1 + byte) % mod
            sum2 = (sum2 + sum1) % mod
        fletcher = (sum2 << 8) | sum1
        if fletcher == c:
            print(f"  mod={mod}: MATCH for pair 0!")
            break

# Try Adler-16
print("\n=== Adler-16 ===")
for h, c in pairs[:3]:
    b = h2b(h)
    data = b[:-2]
    a = 1
    b_val = 0
    for byte in data:
        a = (a + byte) % 251
        b_val = (b_val + a) % 251
    adler = (b_val << 8) | a
    print(f"  Adler-16: 0x{adler:04X} vs 0x{c:04X} {'MATCH!' if adler == c else ''}")

# Maybe it's a custom SAS CRC - try looking at SAS spec
# SAS uses CRC-16-IBM but with specific framing
# Let's try with the "qGMID1:" prefix stripped and just the hex data
print("\n=== Try with SAS framing considerations ===")
# The SAS frame might include the "qGMID1:" prefix in CRC calculation
# Or the CRC might be computed over a different portion

# Let's look at the actual byte values more carefully
for h, c in pairs[:5]:
    b = h2b(h)
    print(f"\nPayload analysis:")
    print(f"  Full length: {len(b)} bytes")
    print(f"  Addr: 0x{b[0]:02X}, Cmd: 0x{b[1]:02X}, Len: 0x{b[2]:02X} ({b[2]})")
    print(f"  Data length: {len(b) - 5}")  # minus addr, cmd, len, crc
    print(f"  Expected CRC: 0x{c:04X}")
    print(f"  CRC bytes in frame: 0x{b[-2]:02X} 0x{b[-1]:02X}")
    
    # The Len field says 0x45 = 69 data bytes
    # But we have 73 total - 3 (addr+cmd+len) - 2 (crc) = 68 data bytes
    # Wait, let me recount
    # 01 72 45 = addr cmd len
    # Then 69 data bytes (as per len field)
    # Then 2 CRC bytes
    # Total should be 3 + 69 + 2 = 74 bytes
    # But we have 73 bytes (146 hex chars / 2)
    print(f"  Hex string length: {len(h)} chars -> {len(h)//2} bytes")
    # 146 hex chars = 73 bytes
    # But len field says 69 data bytes
    # 3 + 69 + 2 = 74, but we have 73
    # Maybe the len field includes itself? Or the CRC is 1 byte?
    # Or maybe the last 2 bytes are NOT the CRC
    
    # Let's check: 0x45 = 69
    # If len = data bytes, then data = bytes[3:3+69] = bytes[3:72]
    # CRC = bytes[72:74] but we only have 73 bytes
    # So either len is wrong or there's only 1 CRC byte
    
    # Actually wait - maybe the len field doesn't include the CRC
    # Let's count: 3 header + 69 data + 2 CRC = 74
    # But we have 73 bytes
    # Maybe the len field = total - 3 (header) = 70?
    # 0x45 = 69, not 70
    
    # Let me recount the hex string
    data_bytes = b[3:]
    print(f"  After header: {len(data_bytes)} bytes")
    # If len=69, then data=69, crc=2, total after header=71
    # But we have 70 bytes after header
    # So maybe len includes CRC? 69 = 67 data + 2 CRC
    # Or len is off by 1
    
    # Actually, let me look at the short payload
    # 017202FF000F22 = 7 bytes
    # 01 72 02 FF 00 0F 22
    # addr=01, cmd=72, len=02, data=FF 00, crc=0F 22? 
    # Or addr=01, cmd=72, len=02, data=FF 00, then 0F 22 is something else
    # len=02 means 2 data bytes: FF 00
    # Then 0F 22 could be CRC
    
    # For the long payload:
    # len=0x45=69
    # If len=data bytes, then 69 data + 2 CRC = 71 after header
    # But we have 70 after header
    # So len might be data+CRC = 69, meaning 67 data + 2 CRC
    # Or len might be wrong
    
    # Let me just try CRC on different ranges
    for start in range(4):
        for end_offset in range(1, 4):
            data = b[start:len(b)-end_offset]
            result = crc16_arc(data)
            crc_bytes = b[len(b)-end_offset:]
            if end_offset == 2:
                expected = (crc_bytes[0] << 8) | crc_bytes[1]
                if result == expected:
                    print(f"  MATCH! CRC on bytes [{start}:{len(b)-end_offset}] = 0x{result:04X}")