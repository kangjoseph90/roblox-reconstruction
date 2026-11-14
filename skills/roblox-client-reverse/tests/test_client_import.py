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
from backends import player_acquire
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

    def test_player_backend_does_not_substitute_metadata(self):
        with patch("network.get", side_effect=AssertionError("must remain offline")), patch("workflow.runtime", return_value={"compatible": False, "reason": "unknown test build"}):
            r = player_acquire("123", self.root / "out")
        self.assertEqual(r["summary"]["status"], "failed")
        self.assertEqual(r["summary"]["failure_stage"], "environment")
        self.assertEqual(r["summary"]["source_files"], 0)
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")

    def test_cli_import_and_decompile_commands(self):
        cli = Path(__file__).resolve().parents[1] / "scripts/rcr.py"
        p = self.model(HEADER + "return 42")
        for i, command in enumerate(["import", "decompile"]):
            run = subprocess.run([sys.executable, str(cli), command, str(p), "--out", str(self.root / str(i))], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["source_files"], 1)
        run = subprocess.run([sys.executable, str(cli), "acquire", "123", "--backend", "auto", "--player-exe", str(self.root / "not-installed.exe"), "--out", str(self.root / "auto")], capture_output=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["failure_stage"], "environment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
