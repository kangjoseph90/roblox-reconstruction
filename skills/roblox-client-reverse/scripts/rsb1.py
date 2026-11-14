"""Integrity-checked RSB1 envelope decoding, reused from the live capture."""
import struct
import zstandard

def xxh32(data, seed=42):
    mask = 0xffffffff
    p1, p2, p3, p4, p5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
    def rol(n, bits):
        return ((n << bits) | (n >> (32 - bits))) & mask
    def lane(v, value):
        return rol((v + value * p2) & mask, 13) * p1 & mask
    pos, size = 0, len(data)
    if size >= 16:
        values = [(seed + p1 + p2) & mask, (seed + p2) & mask, seed & mask, (seed - p1) & mask]
        while pos <= size - 16:
            for i in range(4):
                values[i] = lane(values[i], struct.unpack_from("<I", data, pos + i * 4)[0])
            pos += 16
        h = sum(rol(value, bits) for value, bits in zip(values, (1, 7, 12, 18))) & mask
    else:
        h = (seed + p5) & mask
    h = (h + size) & mask
    while pos <= size - 4:
        h = rol((h + struct.unpack_from("<I", data, pos)[0] * p3) & mask, 17) * p4 & mask
        pos += 4
    while pos < size:
        h = rol((h + data[pos] * p5) & mask, 11) * p1 & mask
        pos += 1
    h ^= h >> 15
    h = h * p2 & mask
    h ^= h >> 13
    h = h * p3 & mask
    return (h ^ h >> 16) & mask

def unwrap(data):
    if len(data) < 16:
        raise ValueError("buffer too short")
    key = bytes(((data[i] ^ b"RSB1"[i]) - i * 41) & 255 for i in range(4))
    envelope = bytes(value ^ ((key[i % 4] + i * 41) & 255) for i, value in enumerate(data))
    # The assumed magic alone proves nothing: the independent hash and frame
    # decoder, bounded output length and serialized Luau header must all agree.
    if xxh32(envelope, 42) != int.from_bytes(key, "little"):
        raise ValueError("RSB1 XXH32 integrity mismatch")
    size = int.from_bytes(envelope[4:8], "little")
    if not 2 <= size <= 32 * 1024**2:
        raise ValueError("uncompressed size outside limit")
    payload = zstandard.ZstdDecompressor().decompress(envelope[8:], max_output_size=size)
    if len(payload) != size:
        raise ValueError("uncompressed size mismatch")
    if payload[0] not in range(3, 15):
        raise ValueError("unsupported serialized bytecode version")
    return payload, {"envelope": "RSB1", "xxh32_seed42_verified": True,
                     "decoded_size": size, "bytecode_version": payload[0],
                     "type_version": payload[1], "encoding": "Roblox opcode encoding; not vanilla"}
