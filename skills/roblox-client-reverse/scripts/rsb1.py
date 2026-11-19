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
    # The XOR stream (key[i % 4] + i * 41) & 255 repeats every 256 bytes; one big-integer
    # XOR applies it at C speed instead of a per-byte Python loop.
    period = bytes((key[i % 4] + i * 41) & 255 for i in range(256))
    stream = (period * (len(data) // 256 + 1))[:len(data)]
    envelope = (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little")
    # The assumed magic alone proves nothing: the independent hash and frame
    # decoder, bounded output length and serialized Luau header must all agree.
    if xxh32(envelope, 42) != int.from_bytes(key, "little"):
        raise ValueError("RSB1 XXH32 integrity mismatch")
    size = int.from_bytes(envelope[4:8], "little")
    if not 2 <= size <= 32 * 1024**2:
        raise ValueError("uncompressed size outside limit")
    frame = envelope[8:]
    # max_output_size is ignored when the frame header declares a content size, so the
    # header must agree and the decoded length is capped by a bounded stream read.
    try:
        declared = zstandard.frame_content_size(frame)
    except zstandard.ZstdError as exc:
        raise ValueError("invalid Zstd frame header") from exc
    # zstandard reports an undeclared size as -1 (older releases: CONTENTSIZE_UNKNOWN).
    if declared not in (size, -1, zstandard.CONTENTSIZE_UNKNOWN):
        raise ValueError("Zstd frame size disagrees with RSB1 header")
    # One pass: output is capped at the envelope size, and the envelope must be exactly
    # one frame (trailing bytes are rejected, not ignored).
    try:
        payload = zstandard.ZstdDecompressor().decompress(frame, max_output_size=size, allow_extra_data=False)
    except zstandard.ZstdError as exc:
        if "unused data" in str(exc):
            raise ValueError("unexpected data after Zstd frame") from exc
        raise ValueError("uncompressed size mismatch") from exc
    if len(payload) != size:
        raise ValueError("uncompressed size mismatch")
    if payload[0] not in range(3, 15):
        raise ValueError("unsupported serialized bytecode version")
    return payload, {"envelope": "RSB1", "xxh32_seed42_verified": True,
                     "decoded_size": size, "bytecode_version": payload[0],
                     "type_version": payload[1], "encoding": "Roblox opcode encoding; not vanilla"}
