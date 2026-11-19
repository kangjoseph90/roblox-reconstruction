import base64
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from formats import MAGIC, Reader, parse_artifact, parse_binary
from pipeline import extract, hierarchy, validate_repository
from network import target_id
from make_fixtures import fixture


def repack(data, codec="none", bytecode=None):
    """Mutate independent rbx-dom output for decompressor and Bytecode tests."""
    r = Reader(data)
    header = r.take(32)
    output = bytearray(header)
    import lz4.block
    import zstandard
    while r.pos < len(data):
        tag, compressed, expanded, reserved = r.take(4), r.u32(), r.u32(), r.u32()
        payload = r.take(compressed or expanded)
        if compressed:
            payload = lz4.block.decompress(payload, uncompressed_size=expanded)
        if tag == b"PROP" and bytecode is not None:
            c = Reader(payload)
            cid, prop = c.u32(), c.string()
            offset = c.pos
            typ = c.u8()
            if prop == b"Source":
                vals = []
                while c.pos < len(payload):
                    c.string()
                    vals.append(bytecode)
                payload = payload[:offset] + b"\x1d" + b"".join(struct.pack("<I", len(v)) + v for v in vals)
        expanded = len(payload)
        compressed = 0
        if tag != b"END\0" and codec != "none":
            payload = zstandard.ZstdCompressor().compress(payload) if codec == "zstd" else lz4.block.compress(payload, store_size=False)
            compressed = len(payload)
        output.extend(tag + struct.pack("<III", compressed, expanded, 0) + payload)
    return bytes(output)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def dump(self, items, name="dump.json"):
        p = self.root / name
        p.write_text(json.dumps({"schema": "rcr-dump/v1", "instances": items}))
        return p

    def test_xml_paths_scope_and_missing_source(self):
        p = fixture(self.root, "Comets")
        r = extract(p, self.root / "out")
        self.assertEqual(r["summary"]["scripts"], 10)
        self.assertEqual(r["summary"]["source_files"], 7)
        self.assertEqual(r["summary"]["status"], "partial")
        self.assertEqual(r["summary"]["recovery_counts"]["excluded_server_scope"], 2)
        duplicate = [s for s in r["scripts"] if s["instance_path"][-1] == "Duplicate"]
        self.assertEqual(len({s["source"] for s in duplicate}), 2)
        dangerous = next(s for s in r["scripts"] if s["instance_path"][-1].startswith("../"))
        self.assertIn("source", dangerous)
        self.assertNotIn("..", Path(dangerous["source"]).parts)
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")

    def test_no_source_is_not_a_success(self):
        p = self.dump([{"id": "x", "class": "LocalScript", "name": "Late"}])
        r = extract(p, self.root / "out")
        self.assertEqual(r["summary"]["source_files"], 0)
        self.assertEqual(r["scripts"][0]["recovery"], "source_unavailable")

    def test_cycle_and_dangling_parents_rejected(self):
        for items in [[{"id": "x", "parent": "x", "name": "x"}], [{"id": "x", "parent": "y", "name": "x"}]]:
            with self.assertRaises(ValueError):
                hierarchy(items)

    def test_hierarchy_depth_limit_does_not_depend_on_input_order(self):
        chain = [{"id": str(i), "name": "n", "parent": str(i - 1) if i else None} for i in range(257)]
        for items in (chain, list(reversed(chain))):
            with self.assertRaises(ValueError):
                hierarchy(items)
        self.assertEqual(len(hierarchy(chain[:256])["255"]), 256)

    def test_unreadable_input_does_not_stop_independent_inputs(self):
        src = self.root / "tree"
        src.mkdir()
        (src / "A.luau").write_text("return 1\n")
        (src / "B.luau").write_text("return 2\n")
        original = Path.read_bytes
        def read_bytes(path):
            if path.name == "A.luau":
                raise PermissionError("denied")
            return original(path)
        with patch.object(Path, "read_bytes", read_bytes):
            r = extract(src, self.root / "out")
        status = {a["input_path"]: a["status"] for a in r["artifacts"]}
        self.assertEqual(status["A.luau"], "failed")
        self.assertEqual(status["B.luau"], "parsed")
        self.assertEqual(r["summary"]["status"], "partial")

    def test_unreadable_directory_entry_is_recorded(self):
        src = self.root / "tree"
        src.mkdir()
        (src / "A.luau").write_text("return 1\n")
        (src / "B.luau").write_text("return 2\n")
        original = Path.lstat
        def lstat(path):
            if path.name == "A.luau":
                raise PermissionError("denied")
            return original(path)
        with patch.object(Path, "lstat", lstat):
            r = extract(src, self.root / "out")
        self.assertEqual([a["input_path"] for a in r["artifacts"]], ["B.luau"])
        self.assertIn("denied", r["errors"][0]["error"])
        self.assertEqual(r["summary"]["status"], "partial")

    def test_unreadable_subdirectory_is_reported(self):
        src = self.root / "tree"
        (src / "locked").mkdir(parents=True)
        (src / "A.luau").write_text("return 1\n")
        import os as _os
        real = _os.walk
        def walk(top, onerror=None, **kwargs):
            onerror(PermissionError(13, "denied", str(src / "locked")))
            return real(top, onerror=onerror, **kwargs)
        with patch("pipeline.os.walk", walk):
            r = extract(src, self.root / "out")
        self.assertTrue(any("denied" in e["error"] for e in r["errors"]))
        self.assertEqual(r["summary"]["status"], "partial")

    def test_dtd_and_malformed_files_rejected(self):
        for data in [b'<!DOCTYPE roblox [<!ENTITY x "expanded">]><roblox version="4"/>', MAGIC + b"\x00"]:
            with self.assertRaises(Exception):
                parse_artifact(data, ".rbxl")

    def test_xml_shared_opaque_source_and_bytecode_are_preserved(self):
        raw = b"\xff\x00opaque"
        data = ('<roblox version="4"><Item class="ModuleScript" referent="x"><Properties>'
                '<SharedString name="Source">key</SharedString><SharedString name="Bytecode">key</SharedString>'
                '</Properties></Item><SharedStrings><SharedString md5="key">' + base64.b64encode(raw).decode() +
                '</SharedString></SharedStrings></roblox>').encode()
        nodes, info = parse_artifact(data, ".rbxmx")
        self.assertEqual(nodes[0]["opaque_source"], raw)
        self.assertEqual(nodes[0]["bytecode"], raw)
        self.assertNotIn("source", nodes[0])

    def test_opaque_and_unsupported_bytecode_preserved(self):
        for i, declared in enumerate([None, "luau"]):
            p = self.dump([{"id": "x", "class": "ModuleScript", "name": "X", "bytecode_base64": base64.b64encode(b"\x7funknown").decode()}], str(i) + ".json")
            r = extract(p, self.root / ("out" + str(i)), {"decompiler": {"argv": ["must-not-run"], "bytecode_versions": [6]}}, bytecode_format=declared)
            s = r["scripts"][0]
            self.assertEqual(s["recovery"], "opaque_bytecode" if declared is None else "unsupported_bytecode_version")
            self.assertEqual((self.root / ("out" + str(i)) / s["bytecode"]["path"]).read_bytes(), b"\x7funknown")
            self.assertNotIn("source", s)

    def test_source_tree_does_not_claim_game_provenance(self):
        src = self.root / "tree"
        src.mkdir()
        (src / "Main.client.luau").write_text("return 42\n")
        (src / "note.txt").write_text("unrecognized but preserved")
        r = extract(src, self.root / "out")
        self.assertEqual(r["scripts"][0]["scope"], "supplied-file-unknown-scope")
        self.assertEqual(r["coverage"]["game_completeness"], "unknown")
        self.assertEqual(len(r["artifacts"]), 2)

    def test_output_nesting_and_existing_output_rejected(self):
        src = self.root / "tree"
        src.mkdir()
        with self.assertRaises(ValueError):
            extract(src, src / "out")
        p = self.dump([])
        extract(p, self.root / "out")
        with self.assertRaises(ValueError):
            extract(p, self.root / "out")

    def test_symlink_input_is_excluded(self):
        src = self.root / "tree"
        src.mkdir()
        p = src / "x.luau"
        p.write_text("return 42")
        try:
            (src / "link.luau").symlink_to(p)
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror == 1314:
                self.skipTest("Windows token lacks symbolic-link creation privilege (1314)")
            raise
        r = extract(src, self.root / "out")
        self.assertEqual(r["summary"]["status"], "partial")

    def test_integrity_tampering(self):
        p = self.root / "x.luau"
        p.write_text("return 42")
        r = extract(p, self.root / "out")
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")
        source = self.root / "out" / r["scripts"][0]["source"]
        source.write_text("return 99")
        self.assertEqual(validate_repository(self.root / "out")["status"], "failed")

    def test_target_parsing(self):
        self.assertEqual(target_id("https://www.roblox.com/ko/games/123/name"), 123)
        self.assertEqual(target_id("https://create.roblox.com/store/asset/42/name", "asset"), 42)
        for bad in ["https://evil.com/games/123", "0", "https://www.roblox.com.evil.com/games/123", "https://www.roblox.com@evil.com/games/123"]:
            with self.assertRaises(ValueError):
                target_id(bad)




    def test_real_compiler_flags_invalid_source(self):
        if not os.environ.get("RCR_TEST_TOOLS"):
            self.skipTest("RCR_TEST_TOOLS is required for real compiler integration")
        config = Path(os.environ["RCR_TEST_TOOLS"])
        p = self.dump([{"id": "x", "class": "ModuleScript", "name": "Broken", "source": "local = ???"}])
        r = extract(p, self.root / "out", tools=json.loads(config.read_text()))
        self.assertEqual(r["scripts"][0]["recovery"], "invalid_source")
        self.assertEqual(r["scripts"][0]["validation"]["status"], "syntax_failed")

    def test_type_version_gate(self):
        p = self.dump([{"id": "x", "class": "ModuleScript", "name": "X", "bytecode_format": "luau",
                       "bytecode_base64": base64.b64encode(b"\x0e\x7funknown").decode()}])
        r = extract(p, self.root / "out", tools={"decompiler": {"argv": ["must-not-run"], "bytecode_versions": [14], "type_versions": [3]}})
        self.assertEqual(r["scripts"][0]["recovery"], "unsupported_type_version")



class BinaryInteropTests(unittest.TestCase):
    def test_independent_binary_lz4_zstd_and_opaque_bytecode(self):
        fixtures = Path(__file__).resolve().parents[1] / "assets" / "interop-fixtures"
        path = fixtures / "Workshop.rbxl"
        if not path.exists():
            self.skipTest("generate independent fixtures using make_fixtures.py --rbx-util")
        try:
            import lz4.block
            import zstandard
        except ImportError:
            self.skipTest("optional compression packages not installed")
        original = path.read_bytes()
        baseline, info = parse_binary(original)
        self.assertEqual(len(baseline), 16)
        self.assertIn("lz4", info["compression"])
        for codec in ["none", "lz4", "zstd"]:
            nodes, info = parse_binary(repack(original, codec))
            self.assertEqual(nodes, baseline)
            self.assertIn(codec, info["compression"])
        bytecode = b"\x06\x03synthetic-opaque"
        nodes, _ = parse_binary(repack(original, "zstd", bytecode))
        code_nodes = [n for n in nodes if "bytecode" in n]
        self.assertEqual(len(code_nodes), 10)
        self.assertTrue(all(n["bytecode"] == bytecode for n in code_nodes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
