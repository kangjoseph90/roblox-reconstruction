import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import extract, validate_repository

HEADER = "-- Saved by UniversalSynSaveInstance (Join to Copy Games) https://example.invalid\n\n"


class ClientImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def model(self, source):
        root = ET.Element("roblox", version="4")
        storage = ET.SubElement(root, "Item", {"class": "ReplicatedStorage", "referent": "r"})
        props = ET.SubElement(storage, "Properties")
        ET.SubElement(props, "string", name="Name").text = "ReplicatedStorage"
        item = ET.SubElement(storage, "Item", {"class": "ModuleScript", "referent": "m"})
        props = ET.SubElement(item, "Properties")
        ET.SubElement(props, "string", name="Name").text = "Captured"
        ET.SubElement(props, "ProtectedString", name="Source").text = source
        p = self.root / "capture.rbxmx"
        ET.ElementTree(root).write(p, encoding="utf-8")
        return p

    def test_disabled_and_missing_decompiler_are_not_source(self):
        for i, diagnostic in enumerate(["-- Decompiling is disabled", "-- Your Executor does NOT have a Decompiler", "-- The Script is Empty"]):
            p = self.model(HEADER + diagnostic)
            r = extract(p, self.root / str(i))
            self.assertEqual(r["summary"]["source_files"], 0)
            self.assertEqual(r["scripts"][0]["recovery"], "source_unavailable")
            self.assertEqual(r["artifacts"][0]["client_import"]["live_collection_verified"], False)

    def test_bytecode_is_preserved_even_when_decompilation_disabled(self):
        raw = b"\x0e\x03test-only"
        p = self.model(HEADER + "-- Bytecode (Base64):\n-- " + base64.b64encode(raw).decode() + "\n\n-- Decompiling is disabled")
        r = extract(p, self.root / "out")
        s = r["scripts"][0]
        self.assertEqual(s["recovery"], "opaque_bytecode")
        self.assertEqual((self.root / "out" / s["bytecode"]["path"]).read_bytes(), raw)
        self.assertNotIn("source", s)
        self.assertEqual(s["instance_path"], ["ReplicatedStorage", "Captured"])
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")

    def test_marker_inside_source_code_is_not_bytecode(self):
        payload = base64.b64encode(b"\x0e\x03inner").decode()
        source = "return [[\n-- Bytecode (Base64):\n-- " + payload + "\n]]\n"
        r = extract(self.model(HEADER + source), self.root / "out")
        s = r["scripts"][0]
        self.assertNotIn("bytecode", s)
        self.assertEqual((self.root / "out" / s["source"]).read_text().strip(), source.strip())

    def test_marker_inside_an_unclosed_leading_comment_is_not_bytecode(self):
        payload = base64.b64encode(b"\x0e\x03inner").decode()
        source = "--[[ notes\n-- Bytecode (Base64):\n-- " + payload + "\n"
        r = extract(self.model(HEADER + source), self.root / "out2")
        self.assertNotIn("bytecode", r["scripts"][0])

    def test_collector_source_is_labeled_and_normalized(self):
        p = self.model(HEADER + "return {answer = 42}\n")
        r = extract(p, self.root / "out")
        s = r["scripts"][0]
        self.assertEqual(s["capture_status"], "collector_supplied_source")
        self.assertIn("provenance unverified", s["source_origin"])
        self.assertEqual((self.root / "out" / s["source"]).read_text().strip(), "return {answer = 42}")

    def test_corrupt_bytecode_is_not_silently_discarded(self):
        p = self.model(HEADER + "-- Bytecode (Base64):\n-- !!!\n\nreturn 42")
        r = extract(p, self.root / "out")
        self.assertEqual(r["summary"]["status"], "partial")
        self.assertTrue(r["scripts"][0]["capture_errors"])
        self.assertIn("source", r["scripts"][0])

    def test_failed_decompiler_comment_is_not_recovered_source(self):
        p = self.model(HEADER + "--[[ Failed to decompile. Reason:\nDecompiler timed out\n]]")
        r = extract(p, self.root / "out")
        self.assertEqual(r["summary"]["source_files"], 0)
        self.assertEqual(r["scripts"][0]["capture_status"], "decompilation_failed")

    def test_plain_source_is_not_reinterpreted(self):
        p = self.model('-- Bytecode (Base64):\n-- !!!\nreturn "ordinary source"')
        r = extract(p, self.root / "out")
        self.assertNotIn("client_import", r["artifacts"][0])
        self.assertEqual(r["summary"]["status"], "recovered")

    def test_comment_only_output_is_not_implementation(self):
        p = self.model(HEADER + "--[[ cached asset source ]project] not executable ]]\n-- unknown collector failure")
        r = extract(p, self.root / "out")
        self.assertEqual(r["summary"]["source_files"], 0)
        self.assertEqual(r["scripts"][0]["capture_status"], "collector_comment_only_output")




if __name__ == "__main__":
    unittest.main(verbosity=2)
