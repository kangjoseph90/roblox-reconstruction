"""Integration invariants without joining or inspecting a user's game process."""
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import struct
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compatibility import profiles, select_profile, choose_profile, runtime
from windows_collector import resolve_metadata, CollectionError
from pipeline import extract, validate_repository
from rsb1 import unwrap, xxh32

FIXTURE = Path(os.environ["RCR_TEST_CAPTURE"]) if os.environ.get("RCR_TEST_CAPTURE") else None
CAPTURE_REPORT = (FIXTURE / "report.json" if (FIXTURE / "report.json").is_file() else FIXTURE / "capture-report.json") if FIXTURE else None


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_known_profile_lookup_keeps_verified_and_unverified_distinct(self):
        profile = profiles()[0]
        image = {"sha256": profile["player_sha256"], "pe_machine": profile["pe_machine"]}
        self.assertEqual(select_profile(image, "Windows", "AMD64")["id"], profile["id"])
        self.assertIsNone(select_profile(dict(image, sha256="0" * 64), "Windows", "AMD64"))
        self.assertIsNone(select_profile(image, "Darwin", "AMD64"))
        self.assertIsNone(select_profile(image, "Windows", "ARM64"))

    def test_unknown_hash_gets_an_unverified_attempt_profile(self):
        known = profiles()[0]
        image = {"sha256": "0" * 64, "pe_machine": known["pe_machine"]}
        candidate = choose_profile(image, "Windows", "AMD64")
        self.assertEqual(candidate["compatibility_status"], "unverified")
        self.assertEqual(candidate["player_sha256"], image["sha256"])
        self.assertEqual(candidate["baseline_profile"], known["id"])
        self.assertIsNone(choose_profile(image, "Darwin", "AMD64"))
        self.assertEqual(choose_profile(dict(image, sha256=known["player_sha256"]), "Windows", "AMD64")["compatibility_status"], "verified")

    def test_unknown_build_runtime_continues_instead_of_rejecting_version(self):
        image = {"path": "new-player.exe", "sha256": "0" * 64, "pe_machine": 34404}
        with patch("compatibility.platform.system", return_value="Windows"), \
                patch("compatibility.platform.machine", return_value="AMD64"), \
                patch("compatibility.fingerprint", return_value=image):
            result = runtime("new-player.exe")
        self.assertTrue(result["compatible"])
        self.assertEqual(result["compatibility_status"], "unverified")
        self.assertIsNone(result["reason"])

    def test_relocated_descriptors_are_discovered_without_a_known_image_hash(self):
        base, heap, size = 0x100000, 0x800000, 0x3000
        table = base + 0x800
        image = bytearray(size)
        struct.pack_into("<Q", image, 0x800, base + 0x100)
        strings, expected = {}, {}
        for i, name in enumerate(("DataModel", "ModuleScript", "LocalScript")):
            address, pointer = base + 0x1800 + i * 0x100, heap + i * 0x100
            struct.pack_into("<QQQ", image, address - base, table, pointer, 0)
            strings[pointer] = name.encode().ljust(16, b"\0") + struct.pack("<QQ", len(name), 15)
            expected[name] = address - base
        class FakeReader:
            def read(self, address, count):
                if base <= address and address + count <= base + size:
                    return bytes(image[address - base:address - base + count])
                return strings.get(address, b"")[:count]
            def regions(self):
                return iter([{"base": base, "size": size, "state": 0x1000, "protect": 4, "type": 0x1000000}])
        profile = choose_profile({"sha256": "0" * 64, "pe_machine": 34404}, "Windows", "AMD64")
        resolved = resolve_metadata(FakeReader(), profile, base, size)
        self.assertEqual(resolved["class_descriptor_rvas"], expected)
        self.assertEqual(resolved["descriptor_vtable_rva"], table - base)
        self.assertEqual(resolved["metadata_resolution"]["method"], "live_descriptor_discovery")
        self.assertEqual(resolved["compatibility_status"], "unverified")

    def test_actual_unresolvable_metadata_reports_structure_failure(self):
        class EmptyReader:
            def read(self, *args):
                return b""
            def regions(self):
                return iter([])
        profile = choose_profile({"sha256": "0" * 64, "pe_machine": 34404}, "Windows", "AMD64")
        with self.assertRaises(CollectionError) as failed:
            resolve_metadata(EmptyReader(), profile, 0x100000, 0x3000)
        self.assertEqual(failed.exception.stage, "class_metadata")
        self.assertIn("resolved uniquely", failed.exception.detail["reason"])

    def test_known_metadata_keeps_the_validated_fast_path(self):
        profile, base = profiles()[0], 0x100000
        table = base + profile["descriptor_vtable_rva"]
        memory = {}
        for i, (name, rva) in enumerate(profile["class_descriptor_rvas"].items()):
            pointer = 0x800000 + i * 0x100
            memory[base + rva] = struct.pack("<QQQQ", table, pointer, 0, 0)
            memory[pointer] = name.encode().ljust(16, b"\0") + struct.pack("<QQ", len(name), 15)
        class Reader:
            def read(self, address, size):
                return memory.get(address, b"")[:size]
            def regions(self):
                raise AssertionError("matching metadata must not require image rediscovery")
        resolved = resolve_metadata(Reader(), profile, base, 0x10000000)
        self.assertEqual(resolved["class_descriptor_rvas"], profile["class_descriptor_rvas"])
        self.assertEqual(resolved["metadata_resolution"]["method"], "candidate_RVAs_validated")




    @unittest.skipUnless(CAPTURE_REPORT is not None and CAPTURE_REPORT.is_file(), "RCR_TEST_CAPTURE must name a capture directory")
    def test_real_native_envelope_matches_recorded_payload_and_rejects_corruption(self):
        record = next(r for r in json.loads(CAPTURE_REPORT.read_text())["scripts"] if "raw_sha256" in r)
        raw = (FIXTURE / "originals" / (record["raw_sha256"] + ".roblox-buffer")).read_bytes()
        payload, info = unwrap(raw)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), record["bytecode_sha256"])
        self.assertTrue(info["xxh32_seed42_verified"])
        changed = bytearray(raw)
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            unwrap(bytes(changed))

    def test_roblox_never_falls_back_to_standard_opcode_adapter(self):
        data = {"schema": "rcr-dump/v1", "instances": [{"id": "a", "class": "ModuleScript", "name": "A",
                "bytecode_base64": base64.b64encode(b"\x0e\x03binary").decode(), "bytecode_format": "roblox"}]}
        path = self.root / "dump.json"
        path.write_text(json.dumps(data))
        tools = {"decompiler": {"argv": ["standard-only", "{input}"], "bytecode_versions": [14]}}
        with patch("pipeline.tool_run", side_effect=AssertionError("must not call a standard-only adapter")):
            result = extract(path, self.root / "out", tools)
        self.assertEqual(result["scripts"][0]["recovery"], "unsupported_bytecode_encoding")
        self.assertEqual(result["summary"]["source_files"], 0)

    def test_duplicate_code_retains_separate_paths_and_one_tool_invocation(self):
        payload = base64.b64encode(b"\x0e\x03binary").decode()
        path = self.root / "dump.json"
        path.write_text(json.dumps({"schema": "rcr-dump/v1", "instances": [
            {"id": name, "class": "ModuleScript", "name": name, "bytecode_base64": payload, "bytecode_format": "roblox"}
            for name in ("One", "Two")]}))
        tools = {"decompiler": {"argv": ["standard"], "roblox_argv": ["roblox"], "bytecode_versions": [14], "type_versions": [3]},
                 "compiler": {"argv": ["compiler"]}}
        with patch("pipeline.tool_run", return_value=({"status": "ok", "exit_code": 0}, b"return 42\n")) as decompiler, \
                patch("pipeline.compile_check", return_value={"status": "syntax_pass", "semantic_equivalence": "not_tested"}) as compiler:
            result = extract(path, self.root / "out", tools)
        self.assertEqual(decompiler.call_count, 1)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual({tuple(s["instance_path"]) for s in result["scripts"]}, {("One",), ("Two",)})
        self.assertEqual(len({s["source"] for s in result["scripts"]}), 2)
        self.assertEqual(result["summary"]["duplicate_bytecode_instances"], 1)
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")












if __name__ == "__main__":
    unittest.main()
