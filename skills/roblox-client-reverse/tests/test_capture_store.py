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

    current = None  # Place the authored Player is in; None means the requested Place

    def authored(self, pid, executable, profile, place, out, *args, place_ids=None, select=None, **kwargs):
        actual = self.current or place
        assert actual in place_ids
        if select is not None and not select(actual):
            return None, {"status": "not_selected", "place_id": actual}
        write_capture(out, actual, [("Config", b"\x0e\x03\x01")])
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
        result = self.capture()
        self.assertEqual((result["status"], result["place_id"]), ("skipped", 1001))
        self.assertIn("already has a capture", result["reason"])
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_capture_sealed_before_an_interrupted_catalog_update_is_recorded_on_the_next_visit(self):
        with patch("capture_store.update_place_state", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.capture()
        self.assertIsNone(self.state()["capture"]["path"])
        result = self.capture()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual((self.state()["capture"]["status"], self.state()["capture"]["path"]), ("pass", "captures/1001"))

    def test_successful_retry_does_not_keep_failure_fields(self):
        self.collect.side_effect = OSError("authored pre-buffer failure")
        self.capture()
        self.collect.side_effect = self.authored
        self.capture(retry_failed=True)
        self.assertNotIn("failure_stage", self.state()["capture"])

    def test_symlink_inside_a_failed_capture_blocks_retry(self):
        self.collect.side_effect = OSError("authored pre-buffer failure")
        self.capture()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.game / "captures/1001/evidence").rename(self.game / "captures/1001/evidence-moved")
        (self.game / "captures/1001/evidence").symlink_to(outside, target_is_directory=True)
        self.collect.side_effect = self.authored
        result = self.capture(retry_failed=True)
        self.assertNotEqual(result["status"], "pass")
        self.assertIn("symlink", result["reason"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_attempt_kept_aside_is_not_reported_as_a_stored_capture(self):
        def occupied(*args, **kwargs):
            dump = self.authored(*args, **kwargs)
            target = self.game / "captures/1001"
            target.mkdir(parents=True, exist_ok=True)
            (target / "other.json").write_text("{}")  # another attempt claimed the Place meanwhile
            return dump
        self.collect.side_effect = occupied
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["collection_status"], "pass")
        self.assertIn("kept_aside", result)

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
        result = self.capture(retry_failed=True)
        self.assertEqual(result["status"], "skipped")  # a successful capture is never replaced
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
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("did not finish; use --retry-failed", result["reason"])
        self.assertEqual(self.capture(retry_failed=True)["status"], "pass")
        journal = json.loads((self.game / "captures/1001/evidence/attempt-errors.json").read_text())
        self.assertEqual([f["path"] for f in journal["attempts"][0]["files"]], ["evidence/environment.json"])

    def test_wrong_place_and_process_exit_never_report_success(self):
        self.process.wait.return_value = True
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertIn("process exited", json.loads((self.game / "captures/1001/report.json").read_text())["errors"][0]["error"])

    def add_place(self, place_id):
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"].append({"id": place_id, "entry": {"status": "not_attempted"}, "capture": {"status": "not_captured", "path": None}})
        _save(self.game / "places.json", catalog)

    def place(self, place_id):
        return next(p for p in json.loads((self.game / "places.json").read_text())["places"] if p["id"] == place_id)

    def attempts_left(self):
        return [p.name for p in (self.game / "captures").iterdir() if p.name.startswith(".attempt-")]

    def launched(self, pid=123, ticks=10):
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["entry"].update(process={"pid": pid, "created_at_ticks": ticks}, launched_by_open=True)
        _save(self.game / "places.json", catalog)

    def test_transfer_to_another_catalogue_place_is_captured_there_and_recorded(self):
        self.add_place(2002)
        self.launched()
        self.current = 2002
        result = self.capture()
        self.assertEqual((result["status"], result["place_id"], result["requested_place_id"]), ("pass", 2002, 1001))
        self.assertEqual(result["path"], "captures/2002")
        self.assertFalse((self.game / "captures/1001").exists())
        self.assertEqual(self.place(1001)["entry"]["status"], "redirected")
        self.assertEqual(self.place(1001)["entry"]["redirected_to"], 2002)
        self.assertEqual(self.place(1001)["capture"]["status"], "not_captured")
        self.assertEqual(self.place(2002)["capture"]["status"], "pass")
        self.assertEqual(self.place(2002)["entry"], {"status": "not_attempted"})
        self.assertEqual(validate_repository(self.game / "captures/2002")["status"], "pass")
        self.assertEqual(self.attempts_left(), [])

    def test_transfer_to_an_already_captured_place_is_skipped_before_buffers(self):
        self.add_place(2002)
        self.launched()
        self.current = 2002
        self.capture()
        root = self.game / "captures/2002"
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        result = self.capture()
        self.assertEqual((result["status"], result["place_id"], result["requested_place_id"]), ("skipped", 2002, 1001))
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
        self.assertEqual(self.place(1001)["entry"]["status"], "redirected")
        self.assertEqual(self.attempts_left(), [])

    def test_other_players_place_is_not_recorded_as_a_redirect(self):
        self.add_place(2002)
        self.launched(pid=555)  # the requested Place was opened in another Player
        self.current = 2002
        result = self.capture()
        self.assertEqual((result["status"], result["place_id"]), ("pass", 2002))
        self.assertEqual(result["requested_place_unconfirmed"], 1001)
        self.assertEqual(self.place(1001)["entry"]["status"], "failed")

    def test_failed_capture_with_buffers_and_unfinished_buffers_are_explained(self):
        def fail(*args, **kwargs):
            self.authored(*args, **kwargs)
            raise OSError("authored post-capture error")
        self.collect.side_effect = fail
        self.capture()
        self.collect.side_effect = self.authored
        result = self.capture()
        self.assertIn("--retry-failed", result["reason"])
        result = self.capture(retry_failed=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("move it aside", result["reason"])
        (self.game / "captures/1001/files.json").unlink()  # unfinished attempt that holds buffers
        result = self.capture(retry_failed=True)
        self.assertIn("holds raw/bytecode buffers", result["reason"])

    def test_capture_without_a_requested_place_uses_the_current_one(self):
        self.add_place(2002)
        self.current = 2002
        with contextlib.redirect_stderr(io.StringIO()):
            result = capture_player(123, None, self.game)
        self.assertEqual((result["status"], result["place_id"]), ("pass", 2002))
        self.assertNotIn("requested_place_id", result)
        self.assertEqual(self.place(1001)["entry"], {"status": "failed", "error_code": 524})

    def test_dump_place_id_disagreement_fails_under_the_identified_place(self):
        self.add_place(2002)
        def moved(pid, executable, profile, place, out, *args, place_ids=None, select=None, **kwargs):
            select(1001)
            write_capture(out, 2002, [("Config", b"\x0e\x03\x01")])
            return out / "client-dump.json", json.loads((out / "report.json").read_text())
        self.collect.side_effect = moved
        result = self.capture()
        self.assertEqual((result["status"], result["place_id"]), ("failed", 1001))
        report = json.loads((self.game / "captures/1001/report.json").read_text())
        self.assertIn("dump Place ID differs", report["errors"][0]["error"])
        self.assertFalse((self.game / "captures/2002").exists())

    def test_failure_before_identification_never_touches_an_existing_capture(self):
        self.capture()
        root = self.game / "captures/1001"
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.collect.side_effect = OSError("authored structure failure")
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["path"].startswith("captures/.attempt-"))
        self.assertIn("already has a capture", result["kept_aside"])
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
        self.assertEqual(validate_repository(self.game / result["path"])["status"], "pass")

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
            self.assertEqual(error.exception.code, 1)
        with patch.object(sys, "argv", ["rcr.py", "capture", "--pid", "123", "--place", "1001", "--game", str(self.game)]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rcr.main(), 0)
        self.build.assert_not_called()

    def test_window_in_a_player_open_did_not_launch_is_not_a_redirect(self):
        self.add_place(2002)
        self.launched()
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["entry"]["launched_by_open"] = False
        _save(self.game / "places.json", catalog)
        self.current = 2002
        self.capture()
        result = self.capture()  # 2002 is now captured: the skipped path also reports the unconfirmed request
        self.assertEqual((result["status"], result["requested_place_unconfirmed"]), ("skipped", 1001))
        self.assertEqual(self.place(1001)["entry"]["status"], "failed")

    def test_retry_sealed_before_its_catalog_update_replaces_the_failed_state(self):
        self.collect.side_effect = OSError("authored pre-buffer failure")
        self.capture()
        self.collect.side_effect = self.authored
        with patch("capture_store.update_place_state", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.capture(retry_failed=True)
        self.assertEqual(self.state()["capture"]["status"], "failed")
        self.assertEqual(self.capture()["status"], "skipped")
        self.assertEqual(self.state()["capture"]["status"], "pass")

    def test_linked_capture_directory_is_never_recorded_or_written(self):
        outside = Path(self.temp.name) / "outside"
        self.capture()
        (self.game / "captures/1001").rename(outside)
        (self.game / "captures/1001").symlink_to(outside, target_is_directory=True)
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["capture"] = {"status": "not_captured", "path": None}
        _save(self.game / "places.json", catalog)
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("link", result["reason"])
        self.assertIsNone(self.state()["capture"]["path"])

    def test_a_place_being_stored_by_another_attempt_is_not_overwritten(self):
        (self.game / "captures").mkdir(parents=True, exist_ok=True)
        (self.game / "captures/.1001.storing").write_text("")
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("another capture is storing", result["reason"])
        self.assertFalse((self.game / "captures/1001").exists())
        (self.game / "captures/.1001.storing").unlink()
        self.assertEqual(self.capture()["status"], "pass")
        self.assertFalse((self.game / "captures/.1001.storing").exists())

    def test_failed_directory_check_releases_the_storing_lock(self):
        self.capture()
        (self.game / "captures/1001/report.json").write_text("not json")  # corrupt existing report
        with self.assertRaises(ValueError):
            self.capture()
        self.assertFalse((self.game / "captures/.1001.storing").exists())

    def test_link_to_a_missing_target_is_not_written_through(self):
        (self.game / "captures").mkdir(parents=True)
        (self.game / "captures/1001").symlink_to(Path(self.temp.name) / "missing", target_is_directory=True)
        result = self.capture()
        self.assertNotEqual(result["status"], "pass")
        self.assertFalse((Path(self.temp.name) / "missing").exists())

    def test_existing_capture_with_a_linked_subdirectory_is_not_recorded(self):
        self.capture()
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir()
        (self.game / "captures/1001/extra").symlink_to(outside, target_is_directory=True)
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["capture"] = {"status": "not_captured", "path": None}
        _save(self.game / "places.json", catalog)
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(self.state()["capture"]["path"])

    def test_identification_in_another_universe_is_neither_skip_nor_redirect(self):
        self.add_place(2002)
        self.launched()
        self.current = 2002
        self.capture()
        self.launched()  # a new launch for 1001 resets its entry
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["entry"]["status"] = "player_identified"
        _save(self.game / "places.json", catalog)
        def other_universe(*args, select=None, **kwargs):
            return None, {"status": "not_selected", "place_id": 2002, "universe_id": 999}
        self.collect.side_effect = other_universe
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(self.place(1001)["entry"]["status"], "redirected")

    def test_dump_from_another_universe_is_not_a_redirect(self):
        self.add_place(2002)
        self.launched()
        self.current = 2002
        def foreign(*args, **kwargs):
            dump, report = self.authored(*args, **kwargs)
            report["dump_capture"] = {"place_id": 2002, "universe_id": 999}
            return dump, report
        self.collect.side_effect = foreign
        result = self.capture()
        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(self.place(1001)["entry"]["status"], "redirected")

    def test_header_passed_by_the_collector_is_used(self):
        def with_header(*args, **kwargs):
            dump, report = self.authored(*args, **kwargs)
            report["dump_capture"] = {"place_id": 1001, "universe_id": 200}
            dump.write_text("{not parsed}")  # the large dump is not parsed again
            return dump, report
        self.collect.side_effect = with_header
        self.assertEqual(self.capture()["status"], "pass")

    def test_structure_cache_inside_captures_is_refused(self):
        with self.assertRaisesRegex(ValueError, "structure-cache"):
            self.capture(structure_cache=self.game / "captures/1001")

    def test_capture_with_a_cut_manifest_is_blocked_with_a_reason(self):
        self.capture()
        (self.game / "captures/1001/files.json").write_text('{"schema": "rcr-fi')  # interrupted while sealing
        catalog = json.loads((self.game / "places.json").read_text())
        catalog["places"][0]["capture"] = {"status": "not_captured", "path": None}
        _save(self.game / "places.json", catalog)
        result = self.capture()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("integrity", result["reason"])

    def test_structure_cache_inside_another_games_captures_is_refused(self):
        other = Path(self.temp.name) / "other-game"
        _save(other / "game.json", {"schema": "rcr-game/v1", "universe_id": 300})
        with self.assertRaisesRegex(ValueError, "structure-cache"):
            self.capture(structure_cache=other / "captures/1001/cache")
        self.assertFalse((other / "captures").exists())

    def test_structure_cache_beside_an_unrelated_game_json_is_allowed(self):
        project = Path(self.temp.name) / "project"
        project.mkdir()
        (project / "game.json").write_text('{"name": "another tool"}')
        self.assertEqual(self.capture(structure_cache=project / "repository/.cache")["status"], "pass")

    def test_structure_cache_inside_another_sealed_directory_is_refused(self):
        other = Path(self.temp.name) / "other-game/captures/1001"
        other.mkdir(parents=True)
        (other / "files.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "sealed"):
            self.capture(structure_cache=other / "cache")


if __name__ == "__main__":
    unittest.main()
