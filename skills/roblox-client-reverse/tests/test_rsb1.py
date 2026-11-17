"""Zstd decoding is bounded by the declared size and accepts frames without a content size."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import zstandard
except ImportError:  # optional runtime dependency; see INSTALL.md
    zstandard = None


def wrap(envelope):
    from rsb1 import xxh32
    key = xxh32(envelope).to_bytes(4, "little")
    return bytes(v ^ ((key[i % 4] + i * 41) & 255) for i, v in enumerate(envelope))


def unsized(data):
    stream = zstandard.ZstdCompressor(write_content_size=False).compressobj()
    return stream.compress(data) + stream.flush()


@unittest.skipUnless(zstandard, "zstandard required")
class Rsb1BoundsTests(unittest.TestCase):
    payload = b"\x0e\x03" + b"authored fixture " * 2000

    def unwrap(self, claimed, frame):
        from rsb1 import unwrap
        return unwrap(wrap(b"RSB1" + struct.pack("<I", claimed) + frame))

    def test_sized_and_unsized_frames_decode(self):
        for frame in (zstandard.ZstdCompressor().compress(self.payload), unsized(self.payload)):
            with self.subTest(sized=zstandard.frame_content_size(frame) != -1):
                self.assertEqual(self.unwrap(len(self.payload), frame)[0], self.payload)

    def test_output_cannot_exceed_the_envelope_size(self):
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.unwrap(10, zstandard.ZstdCompressor().compress(self.payload))
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            self.unwrap(10, unsized(self.payload))

    def test_trailing_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "after Zstd frame"):
            self.unwrap(len(self.payload), zstandard.ZstdCompressor().compress(self.payload) + b"tail")


@unittest.skipUnless(zstandard, "zstandard required")
class BinaryChunkTests(unittest.TestCase):
    def test_unsized_zstd_chunk_is_accepted(self):
        from formats import MAGIC, parse_binary
        sstr = struct.pack("<II", 0, 0)
        frame = unsized(sstr)
        data = (MAGIC + b"\0\0" + struct.pack("<II", 0, 0) + b"\0" * 8
                + b"SSTR" + struct.pack("<III", len(frame), len(sstr), 0) + frame
                + b"END\0" + struct.pack("<III", 0, 9, 0) + b"</roblox>")
        # Decoding succeeds; the empty authored file then fails hierarchy checks, not Zstd checks.
        with self.assertRaisesRegex(ValueError, "incomplete hierarchy"):
            parse_binary(data)
