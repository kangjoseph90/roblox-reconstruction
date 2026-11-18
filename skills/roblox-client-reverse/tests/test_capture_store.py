"""Capture-only CLI boundary and preservation of raw observations on failure."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_fixtures import write_capture
from capture_store import capture_player
from pipeline import validate_repository
from place_catalog import _save
import rcr


class CaptureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name) / "game"
        _save(self.game / "game.json", {"schema": "rcr-game/v1", "universe_id": 200})
        _save(self.game / "places.json", {"schema": "rcr-places/v1", "universe_id": 200, "places": [
            {"id": 1001, "entry": {"status": "failed", "error_code": 524}, "capture": {"status": "not_captured", "path": None}}]})
        self.process = SimpleNamespace(identity={"pid": 123, "path": "RobloxPlayerBeta.exe", "created_at_ticks": 10}, wait=Mock(return_value=False))
        self.session = patch("capture_store.Session").start()
        self.session.return_value.__enter__.return_value = self.process
        self.environment = patch("compatibility.runtime", return_value={"compatible": True, "profile": {}}).start()
        self.collect = patch("windows_collector.collect", side_effect=self.authored).start()
        self.build = patch("bootstrap_tools.build_tools", side_effect=AssertionError("capture must not build recovery tools")).start()
        self.addCleanup(patch.stopall)

    def authored(self, pid, executable, profile, place, out, *args, **kwargs):
        write_capture(out, place, [("Config", b"\x0e\x03\x01")])
        return out / "client-dump.json", json.loads((out / "report.json").read_text())

    def capture(self, **options):
        with contextlib.redirect_stderr(io.StringIO()):
            return capture_player(123, 1001, self.game, **options)

    def state(self):
        return json.loads((self.game / "places.json").read_text())["places"][0]

    def test_capture_only_writes_one_raw_place_directory_and_keeps_entry_failure(self):
        result = self.capture()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["path"], "captures/1001")
        self.assertEqual(self.state()["entry"], {"status": "failed", "error_code": 524})
        self.assertEqual(self.state()["capture"]["status"], "pass")
        self.assertEqual(validate_repository(self.game / "captures/1001")["status"], "pass")
        self.assertFalse((self.game / "repository").exists())
        self.build.assert_not_called()

    def test_existing_capture_is_not_overwritten(self):
        self.capture()
        root = self.game / "captures/1001"
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(ValueError, "already has a capture"):
            self.capture()
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_explicit_empty_failure_retry_preserves_prior_diagnostics(self):
        self.collect.side_effect = OSError("authored pre-buffer failure")
        self.assertEqual(self.capture()["status"], "failed")
        previous = (self.game / "captures/1001/report.json").read_bytes()
        self.collect.side_effect = self.authored
        self.assertEqual(self.capture(retry_failed=True)["status"], "pass")
        journal = json.loads((self.game / "captures/1001/evidence/attempt-errors.json").read_text())
        import base64
        record = next(r for r in journal["attempts"][0]["files"] if r["path"] == "report.json")
        self.assertEqual(base64.b64decode(record["base64"]), previous)
        self.assertEqual(validate_repository(self.game / "captures/1001")["status"], "pass")

    def test_retry_flag_cannot_replace_a_capture_with_buffers(self):
        self.capture()
        with self.assertRaisesRegex(ValueError, "without any raw/bytecode"):
            self.capture(retry_failed=True)
        self.assertTrue(list((self.game / "captures/1001/originals").iterdir()))

    def test_failure_keeps_provisional_observations_and_writes_terminal_state(self):
        def fail(*args, **kwargs):
            self.authored(*args, **kwargs)
            raise OSError("authored post-capture error")
        self.collect.side_effect = fail
        result = self.capture()
        report = json.loads((self.game / "captures/1001/report.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(report["scripts"]), 1)
        self.assertEqual(self.state()["capture"]["status"], "failed")
        self.assertTrue(list((self.game / "captures/1001/originals").iterdir()))
        self.assertEqual(validate_repository(self.game / "captures/1001")["status"], "pass")

    def test_interrupt_records_a_failed_attempt_and_is_reraised(self):
        self.collect.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.capture()
        self.assertEqual(self.state()["capture"]["status"], "failed")
        self.assertEqual(validate_repository(self.game / "captures/1001")["status"], "pass")
        self.collect.side_effect = self.authored
        self.assertEqual(self.capture(retry_failed=True)["status"], "pass")

    def test_unfinished_attempt_without_report_can_only_be_retried(self):
        (self.game / "captures/1001/evidence").mkdir(parents=True)
        (self.game / "captures/1001/evidence/environment.json").write_text("{}")  # killed before any report
        with self.assertRaisesRegex(ValueError, "did not finish; use --retry-failed"):
            self.capture()
        self.assertEqual(self.capture(retry_failed=True)["status"], "pass")
        journal = json.loads((self.game / "captures/1001/evidence/attempt-errors.json").read_text())
        self.assertEqual([f["path"] for f in journal["attempts"][0]["files"]], ["evidence/environment.json"])

    def test_wrong_place_and_process_exit_never_report_success(self):
        self.process.wait.return_value = True
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertIn("process exited", json.loads((self.game / "captures/1001/report.json").read_text())["errors"][0]["error"])

    def test_unknown_place_and_invalid_budget_fail_before_process_access(self):
        with self.assertRaisesRegex(ValueError, "official list"):
            capture_player(123, 2002, self.game)
        with self.assertRaises(ValueError):
            capture_player(123, 1001, self.game, memory_budget_mib=0)
        self.session.assert_not_called()

    def test_removed_commands_and_capture_tool_flags_are_rejected(self):
        for command in ["run", "acquire", "extract", "replay", "import", "decompile"]:
            with self.subTest(command=command), patch.object(sys, "argv", ["rcr.py", command]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                rcr.main()
            self.assertEqual(error.exception.code, 2)
        with patch.object(sys, "argv", ["rcr.py", "capture", "--pid", "123", "--place", "1001", "--game", str(self.game)]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rcr.main(), 0)
        self.build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
