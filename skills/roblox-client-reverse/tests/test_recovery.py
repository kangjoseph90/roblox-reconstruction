"""Capture integrity, multi-Place merge and cross-run tool/config-bound reuse."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_fixtures import write_capture, seal
from capture_store import read_capture
from place_catalog import _save
from recovery import PREVIOUS, _context, recover_game, recover_capture, validate_path

TOOLS = {"compiler": {"argv": ["authored-compiler"]}, "decompiler": {
    "argv": ["authored-standard"], "roblox_argv": ["authored-roblox"], "bytecode_versions": [14], "type_versions": [3]}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name) / "game"
        self.rows = []
        _save(self.game / "game.json", {"schema": "rcr-game/v1", "universe_id": 200})
        self.tools = patch("recovery.tools_config", return_value=TOOLS).start()
        self.workers = patch("pipeline.default_workers", return_value=1).start()
        self.compiler = patch("pipeline.compile_check", return_value={"status": "syntax_pass"}).start()
        self.decompiler = patch("pipeline.tool_run", side_effect=lambda config, path, *a: (
            {"status": "ok", "exit_code": 0}, b"return " + str(path.read_bytes()[-1]).encode() + b"\n")).start()
        self.addCleanup(patch.stopall)

    def add(self, place, *modules, **options):
        root = write_capture(self.game / "captures" / str(place), place, modules, **options)
        self.rows.append({"id": place, "capture": {"status": "pass", "path": "captures/" + str(place)}})
        _save(self.game / "places.json", {"schema": "rcr-places/v1", "universe_id": 200, "places": self.rows})
        return root

    def test_single_place_has_one_repository_and_no_merge_copy_or_variants(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["scripts"], 1)
        self.assertEqual(result["summary"]["syntax_validated"], 1)
        self.assertFalse((self.game / "repository/variants.json").exists())
        self.assertFalse((self.game / "captures/1001/repository").exists())
        self.assertEqual(validate_path(self.game)["status"], "pass")

    def test_three_places_merge_exact_code_and_version_only_conflicts(self):
        one, two = b"\x0e\x03\x01", b"\x0e\x03\x02"
        self.add(1001, ("Config", one))
        self.add(2002, ("Config", one), root_name="Different DataModel label")
        self.add(3003, ("Config", two), ("Unique", one))
        result = recover_game(self.game)
        self.assertEqual({r["instance_path"][-1] for r in result["scripts"]}, {"Config@1001", "Config@3003", "Unique"})
        self.assertEqual(self.decompiler.call_count, 2)
        variants = json.loads((self.game / "repository/variants.json").read_text())["variants"]
        self.assertEqual(next(v for v in variants if v["representative_place_id"] == 1001)["place_ids"], [1001, 2002])
        unique = next(r for r in result["scripts"] if r["instance_path"][-1] == "Unique")
        self.assertNotIn("variant", unique["metadata"])
        self.assertNotIn("place_id", unique["metadata"])

    def test_same_place_same_path_different_code_is_not_lost(self):
        self.add(1001, ("Dialogue", b"\x0e\x03\x01"), ("Dialogue", b"\x0e\x03\x02"))
        result = recover_game(self.game)
        names = [r["instance_path"][-1] for r in result["scripts"]]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(n.startswith("Dialogue@1001-") for n in names))

    def test_reserved_variant_name_collision_stops_without_losing_originals(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"), ("Config@1001", b"\x0e\x03\x03"))
        self.add(2002, ("Config", b"\x0e\x03\x02"))
        with self.assertRaisesRegex(ValueError, "variant name collides"):
            recover_game(self.game)
        self.assertTrue((root / "client-dump.json").is_file())
        self.assertFalse((self.game / "repository").exists())

    def test_static_reuse_does_not_transfer_behavior_claim(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        path = self.game / "repository/report.json"
        report = json.loads(path.read_text())
        report["scripts"][0]["verification"]["behavior"] = "BEHAVIOR_VERIFIED"
        path.write_text(json.dumps(report)); seal(path.parent)
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["reused_modules"], 1)
        self.assertEqual(result["scripts"][0]["verification"]["behavior"], "NOT_TESTED")

    def test_incremental_new_place_reuses_old_code_and_emits_citation_remap(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        before = recover_game(self.game)
        self.add(3003, ("Config", b"\x0e\x03\x02"))
        after = recover_game(self.game)
        self.assertEqual(self.decompiler.call_count, 2)
        self.assertEqual(after["summary"]["reused_modules"], 1)
        remap = after["changes"]["citation_remaps"][0]
        self.assertEqual(remap["before"], before["scripts"][0]["instance_path"])
        self.assertEqual(remap["after"][-1], "Config@1001")
        self.add(999, ("Config", b"\x0e\x03\x01"))
        final = recover_game(self.game)
        self.assertEqual(self.decompiler.call_count, 2)
        self.assertEqual({r["instance_path"][-1] for r in final["scripts"]}, {"Config@1001", "Config@3003"})

    def test_config_change_invalidates_reuse(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        self.tools.return_value = dict(TOOLS, policy="authored changed config")
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["reused_modules"], 0)
        self.assertEqual(self.decompiler.call_count, 2)

    def test_tampered_previous_repository_is_not_overwritten(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        before = recover_game(self.game)
        source = self.game / "repository" / before["scripts"][0]["source"]
        source.write_text("return 'tampered'", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "existing repository integrity"):
            recover_game(self.game)
        self.assertEqual(source.read_text(), "return 'tampered'")

    def test_capture_integrity_failure_precedes_tools(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        next((root / "originals").iterdir()).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "capture integrity"):
            recover_game(self.game)
        self.tools.assert_not_called()
        self.decompiler.assert_not_called()

    def test_decoded_convenience_bytes_cannot_override_original(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        dump = json.loads((root / "client-dump.json").read_text())
        dump["instances"][-1].update(source="return 999", bytecode_base64="!!!")
        (root / "client-dump.json").write_text(json.dumps(dump))
        seal(root)
        result = recover_game(self.game)
        source = self.game / "repository" / result["scripts"][0]["source"]
        self.assertEqual(source.read_text(), "return 1\n")

    def test_unavailable_observation_is_not_claimed_equal_to_known_code(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        self.add(2002, ("Config", None))
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["status"], "partial")
        self.assertEqual(result["summary"]["scripts"], 1)
        self.assertEqual(result["summary"]["unavailable_observations"], 1)
        self.assertEqual(result["scripts"][0]["instance_path"][-1], "Config")
        self.assertFalse((self.game / "repository/variants.json").exists())

    def test_known_unavailable_snapshot_can_match_a_regression_baseline(self):
        from regression import run_regression
        root = self.add(1001, ("Config", b"\x0e\x03\x01"), ("Missing", None))
        first = recover_capture(root, self.game.parent / "baseline-run")
        repo = json.loads((self.game.parent / "baseline-run/repository/report.json").read_text())
        baseline = self.game.parent / "baseline.json"
        baseline.write_text(json.dumps({"payloads": {r["bytecode"]["sha256"]:
            {"status": r["recovery"], "source_sha256": r.get("source_sha256")} for r in repo["scripts"] if "bytecode" in r}}))
        result = run_regression(root, self.game.parent / "regression", baseline=baseline)
        self.assertEqual(first["summary"]["status"], "partial")
        self.assertEqual(result["regression"]["status"], "pass")
        self.assertEqual(result["summary"]["recovery_status"], "partial")

    def test_unexpected_build_failure_has_a_terminal_integrity_report(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        out = self.game.parent / "failed-output"
        with patch("recovery._build", side_effect=ValueError("authored build failure")):
            result = recover_capture(root, out)
        self.assertEqual(result["summary"]["failure_stage"], "recovery")
        self.assertIn("authored build failure", result["errors"][0]["error"])
        self.assertEqual(validate_path(out)["status"], "pass")

    def test_wrong_universe_and_unsafe_capture_reference_are_rejected(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"), universe=999)
        with self.assertRaisesRegex(ValueError, "Place/Universe"):
            recover_game(self.game)
        self.rows[0]["capture"]["path"] = "../outside"
        _save(self.game / "places.json", {"schema": "rcr-places/v1", "universe_id": 200, "places": self.rows})
        with self.assertRaisesRegex(ValueError, "capture path"):
            recover_game(self.game)
        self.tools.assert_not_called()

    def test_supplied_capture_remains_unchanged_and_output_is_integrity_valid(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        out = self.game.parent / "offline"
        report = recover_capture(root, out)
        self.assertEqual(report["summary"]["status"], "recovered")
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})
        self.assertEqual(validate_path(out)["status"], "pass")

    def test_malformed_raw_hash_is_not_used_as_a_path(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        report = json.loads((root / "report.json").read_text())
        report["scripts"][0]["raw_sha256"] = "../outside"
        (root / "report.json").write_text(json.dumps(report)); seal(root)
        capture = read_capture(root)
        self.assertIn("invalid raw content hash", capture["errors"][0]["error"])
        self.assertNotIn("bytecode", capture["scripts"][0])

    def test_resealed_bad_raw_buffer_cannot_hide_behind_valid_decoded_field(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        next((root / "originals").iterdir()).write_bytes(b"corrupted original")
        seal(root)
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["summary"]["source_files"], 0)
        self.assertIn("raw capture hash mismatch", result["errors"][0]["error"])
        self.decompiler.assert_not_called()

    def test_bad_original_after_prior_success_cannot_erase_old_recovery(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        previous = recover_game(self.game)
        source = self.game / "repository" / previous["scripts"][0]["source"]
        before = source.read_bytes()
        next((root / "originals").iterdir()).write_bytes(b"corrupted original"); seal(root)
        with self.assertRaisesRegex(ValueError, "existing repository retained"):
            recover_game(self.game)
        self.assertEqual(source.read_bytes(), before)

    def test_unapproved_and_wrong_decoded_hash_never_reach_decompiler(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        report = json.loads((root / "report.json").read_text())
        for change, error in [({"status": "opaque_buffer"}, "not approved"),
                              ({"status": "roblox_bytecode_captured", "bytecode_sha256": "0" * 64}, "decoded payload hash mismatch")]:
            report["scripts"][0].update(change)
            (root / "report.json").write_text(json.dumps(report)); seal(root)
            capture = read_capture(root)
            self.assertIn(error, capture["errors"][0]["error"])
            self.assertNotIn("bytecode", capture["scripts"][0])

    def test_unfinished_and_failed_captures_are_listed_but_never_merged_or_blocking(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        unfinished = self.add(2002, ("Other", b"\x0e\x03\x02"))
        (unfinished / "report.json").unlink(); (unfinished / "files.json").unlink()
        failed = self.add(3003, ("Third", b"\x0e\x03\x03"), universe=999)  # e.g. the Player moved to another game
        report = json.loads((failed / "report.json").read_text())
        report.update(summary={"status": "failed"}, errors=[{"stage": "identity", "error": "captured Universe differs"}])
        (failed / "report.json").write_text(json.dumps(report)); seal(failed)
        result = recover_game(self.game)
        self.assertEqual([r["instance_path"][-1] for r in result["scripts"]], ["Config"])
        self.assertEqual(result["summary"]["status"], "partial")
        failures = {f["place_id"]: f for f in result["capture_failures"]}
        self.assertEqual(failures[2002]["errors"][0]["stage"], "unfinished")
        self.assertEqual(failures[3003]["errors"][0]["stage"], "identity")

    def test_swap_interrupted_between_renames_restores_the_previous_repository(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        (self.game / "repository").rename(self.game / PREVIOUS)  # state after a kill between the two renames
        result = recover_game(self.game)
        self.assertEqual(result["summary"]["reused_modules"], 1)
        self.assertFalse((self.game / PREVIOUS).exists())

    def test_failed_swap_and_rollback_keep_the_previous_repository_on_disk(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        first = recover_game(self.game)
        source = first["scripts"][0]["source"]
        before = (self.game / "repository" / source).read_bytes()
        real = Path.rename
        def flaky(path, target):
            if Path(target).name == "repository" and Path(path).name in {"repository", PREVIOUS}:
                raise PermissionError("authored locked file")
            return real(path, target)
        with patch.object(Path, "rename", flaky), self.assertRaises(PermissionError):
            recover_game(self.game)
        self.assertEqual((self.game / PREVIOUS / source).read_bytes(), before)
        recover_game(self.game)
        self.assertEqual((self.game / "repository" / source).read_bytes(), before)
        self.assertEqual([p.name for p in self.game.iterdir() if p.name.startswith(".recovery-")], [])

    def test_reuse_fingerprint_covers_the_cfg_candidate_selection(self):
        self.assertIn("cfg_diagnose.py", _context(TOOLS)["configuration"]["scripts"])


if __name__ == "__main__":
    unittest.main()
