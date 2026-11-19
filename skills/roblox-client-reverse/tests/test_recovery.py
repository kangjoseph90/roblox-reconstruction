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
from analysis_check import check_analysis, repository_map
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
        self.assertNotIn("cleanup_error", result["summary"])  # nothing was replaced on a first recovery
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

    def test_later_natural_name_displaces_variant_and_remaps_its_citation(self):
        one, two, three = b"\x0e\x03\x01", b"\x0e\x03\x02", b"\x0e\x03\x03"
        original = self.add(1001, ("Config", one), ("Unrelated", one))
        self.add(2002, ("Config", two))
        recover_game(self.game)
        document = self.game / "analysis.md"
        document.write_text("The first version returns 1. `ReplicatedStorage/Config@1001:1`\n", encoding="utf-8")
        self.assertEqual(check_analysis(self.game, document)["status"], "pass")

        self.add(3003, ("Config@1001", three), ("Config", one))
        result = recover_game(self.game)
        records = {r["instance_path"][-1]: r for r in result["scripts"]}
        self.assertFalse(records["Config@1001"]["metadata"].get("variant"))
        remap, = result["changes"]["citation_remaps"]
        self.assertEqual(remap["before"][-1], "Config@1001")
        self.assertTrue(remap["after"][-1].startswith("Config@1001-"))
        self.assertEqual(set(records), {"Config@1001", "Config@2002", "Unrelated", remap["after"][-1]})
        self.assertEqual(remap["before_source_sha256"], remap["source_sha256"])
        self.assertEqual(result["summary"]["reused_modules"], 3)
        self.assertIn(remap["after"][-1], repository_map(self.game))
        document.write_text("The first version returns 1. `" + "/".join(remap["after"][1:]) + ":1`\n", encoding="utf-8")
        checked = check_analysis(self.game, document)
        self.assertEqual(checked["status"], "pass")
        self.assertEqual(checked["coverage"]["mentioned"], 1)
        self.assertEqual((self.game / "repository" / records[remap["after"][-1]]["source"]).read_text().strip(), "return 1")
        self.assertEqual((self.game / "repository" / records["Config@1001"]["source"]).read_text().strip(), "return 3")
        self.assertTrue((original / "client-dump.json").is_file())
        self.assertEqual(validate_path(self.game)["status"], "pass")
        self.assertEqual(recover_game(self.game)["changes"]["citation_remaps"], [])

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

    def test_rebuild_that_loses_recovered_sources_keeps_the_existing_repository(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        first = recover_game(self.game)
        source = self.game / "repository" / first["scripts"][0]["source"]
        before = source.read_bytes()
        self.tools.return_value = dict(TOOLS, policy="authored changed config")  # forces re-decompilation
        self.decompiler.side_effect = lambda *a: ({"status": "tool_failed", "exit_code": 1}, b"")
        with self.assertRaisesRegex(ValueError, "existing repository retained"):
            recover_game(self.game)
        self.assertEqual(source.read_bytes(), before)

    def test_repository_replaced_during_rebuild_is_not_overwritten(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        import recovery
        real = recovery._build
        def concurrent(*args, **kwargs):
            report = real(*args, **kwargs)
            marker = self.game / "repository/report.json"
            data = json.loads(marker.read_text()); data["created_at"] = "another run"
            marker.write_text(json.dumps(data)); seal(marker.parent)
            return report
        with patch("recovery._build", side_effect=concurrent), self.assertRaisesRegex(ValueError, "changed during recovery"):
            recover_game(self.game)
        self.assertEqual(json.loads((self.game / "repository/report.json").read_text())["created_at"], "another run")

    def test_leftover_previous_repository_is_removed_only_beside_a_valid_repository(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        (self.game / PREVIOUS).mkdir()
        (self.game / PREVIOUS / "kept.txt").write_text("x")
        (self.game / "repository/extra.txt").write_text("untracked")
        with self.assertRaisesRegex(ValueError, PREVIOUS):
            recover_game(self.game)
        self.assertTrue((self.game / PREVIOUS / "kept.txt").exists())
        self.assertEqual(validate_path(self.game)["status"], "failed")

    def test_interrupted_swap_is_reported_by_validate(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        (self.game / "repository").rename(self.game / PREVIOUS)
        self.assertEqual(validate_path(self.game)["status"], "failed")

    def test_changes_survive_an_interruption_before_recovery_json(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        self.add(3003, ("Config", b"\x0e\x03\x02"))
        with patch("recovery._save", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                recover_game(self.game)
        self.add(4004, ("Other", b"\x0e\x03\x04"))
        recover_game(self.game)
        history = json.loads((self.game / "recovery.json").read_text())
        remaps = [r for run in history.get("earlier_changes", []) for r in run["changes"]["citation_remaps"]]
        self.assertEqual(remaps[0]["after"][-1], "Config@1001")

    def test_source_change_at_the_same_path_is_reported(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        self.tools.return_value = dict(TOOLS, policy="authored changed config")
        self.decompiler.side_effect = lambda config, path, *a: ({"status": "ok", "exit_code": 0}, b"return 'changed'\n")
        result = recover_game(self.game)
        self.assertEqual([c["path"][-1] for c in result["changes"]["changed_sources"]], ["Config"])

    def test_no_usable_capture_reports_why(self):
        failed = self.add(3003, ("Third", b"\x0e\x03\x03"))
        report = json.loads((failed / "report.json").read_text())
        report.update(summary={"status": "failed"}, errors=[{"stage": "identity", "error": "authored identity failure"}])
        (failed / "report.json").write_text(json.dumps(report)); seal(failed)
        with self.assertRaisesRegex(ValueError, "authored identity failure"):
            recover_game(self.game)

    def test_failed_capture_is_not_recovered_standalone(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        report = json.loads((root / "report.json").read_text())
        report.update(summary={"status": "failed"}, place_validation={"status": "failed"})
        (root / "report.json").write_text(json.dumps(report)); seal(root)
        out = self.game.parent / "offline"
        result = recover_capture(root, out)
        self.assertEqual((result["summary"]["status"], result["summary"]["failure_stage"]), ("failed", "capture_failed"))
        self.decompiler.assert_not_called()
        self.assertFalse((out / "repository").exists())

    def test_build_failure_leaves_no_partial_repository(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        out = self.game.parent / "failed-output"
        def partial(captures, target, *args, **kwargs):
            Path(target).mkdir(parents=True); (Path(target) / "partial.txt").write_text("x")
            raise ValueError("authored build failure")
        with patch("recovery._build", side_effect=partial):
            recover_capture(root, out)
        self.assertFalse((out / "repository").exists())

    def test_previous_directory_that_is_not_a_sealed_repository_is_kept(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        (self.game / PREVIOUS).mkdir()
        (self.game / PREVIOUS / "backup.txt").write_text("user data")
        with self.assertRaisesRegex(ValueError, "remove it"):
            recover_game(self.game)
        self.assertTrue((self.game / PREVIOUS / "backup.txt").exists())
        import shutil
        shutil.rmtree(self.game / PREVIOUS)
        shutil.copytree(self.game / "repository", self.game / PREVIOUS)  # even a sealed copy is never deleted
        with self.assertRaisesRegex(ValueError, "remove it"):
            recover_game(self.game)
        self.assertTrue((self.game / PREVIOUS / "files.json").exists())

    def test_concurrent_recovery_is_refused(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        (self.game / ".recover.lock").write_text("{}")
        with self.assertRaisesRegex(ValueError, "another recover GAME"):
            recover_game(self.game)
        (self.game / ".recover.lock").unlink()
        recover_game(self.game)
        self.assertFalse((self.game / ".recover.lock").exists())

    def test_two_interruptions_keep_both_change_records_once(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        for place, code in ((3003, b"\x0e\x03\x02"), (4004, b"\x0e\x03\x03")):
            self.add(place, ("Config", code))
            with patch("recovery._save", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                recover_game(self.game)
        self.add(5005, ("Other", b"\x0e\x03\x05"))
        recover_game(self.game)
        history = json.loads((self.game / "recovery.json").read_text())
        self.assertEqual(len(history["earlier_changes"]), 2)
        self.add(6006, ("Another", b"\x0e\x03\x06"))
        recover_game(self.game)
        self.assertNotIn("earlier_changes", json.loads((self.game / "recovery.json").read_text()))

    def test_failed_cfg_audit_makes_game_recovery_partial(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        tools = dict(TOOLS, decompiler=dict(TOOLS["decompiler"], cfg_audit=True))
        self.tools.return_value = tools
        from cfg_verify import ToolFailure
        with patch("cfg_verify.audit_source", side_effect=ToolFailure("missing disassembler")):
            result = recover_game(self.game)
        self.assertEqual(result["summary"]["status"], "partial")

    def test_failed_capture_without_a_dump_is_reported_as_failed(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        (root / "client-dump.json").unlink()
        report = json.loads((root / "report.json").read_text())
        report.update(summary={"status": "failed", "failure_stage": "structure"})
        (root / "report.json").write_text(json.dumps(report)); seal(root)
        from recovery import recover_input
        result = recover_input(root, self.game.parent / "offline")
        self.assertEqual(result["summary"]["failure_stage"], "capture_failed")
        self.tools.assert_not_called()

    def test_tools_directory_inside_inputs_is_refused(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        with self.assertRaisesRegex(ValueError, "tools-dir"):
            recover_capture(root, self.game.parent / "offline", tools_dir=root / ".tools")
        with self.assertRaisesRegex(ValueError, "tools-dir"):
            recover_game(self.game, tools_dir=self.game / "repository/.tools")

    def test_validate_uses_the_root_manifest_of_a_recovery_output(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        out = self.game.parent / "offline"
        recover_capture(root, out)
        (out / "repository").rename(self.game.parent / "moved")
        import pipeline
        pipeline.finish(out, {"summary": {"status": "pass"}, "scripts": []})  # a regression-style root manifest
        (self.game.parent / "moved").rename(out / "repository")
        self.assertEqual(validate_path(out)["status"], "failed")  # repository/ was added after the root was sealed


    def test_unchanged_run_between_interruptions_keeps_earlier_changes(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        recover_game(self.game)  # the recorded run now has no changes
        self.add(3003, ("Config", b"\x0e\x03\x02"))
        with patch("recovery._save", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            recover_game(self.game)  # creates a remap, recovery.json not written
        with patch("recovery._save", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            recover_game(self.game)  # no new changes, recovery.json not written again
        recover_game(self.game)
        history = json.loads((self.game / "recovery.json").read_text())
        remaps = [r for run in history.get("earlier_changes", []) for r in run["changes"]["citation_remaps"]]
        self.assertEqual(remaps[0]["after"][-1], "Config@1001")

    def test_failed_capture_in_an_older_layout_is_reported_as_failed(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        report = json.loads((root / "report.json").read_text())
        report.update(summary={"status": "failed"})
        report.pop("schema")
        (root / "report.json").unlink()
        (root / "capture-report.json").write_text(json.dumps(report)); seal(root)
        result = recover_capture(root, self.game.parent / "offline")
        self.assertEqual(result["summary"]["failure_stage"], "capture_failed")

    def test_regression_output_without_root_manifest_fails_validation(self):
        out = self.game.parent / "regression"
        (out / "evidence").mkdir(parents=True)
        (out / "evidence/regression.json").write_text("{}")
        self.assertEqual(validate_path(out)["status"], "failed")

    def test_supplied_directory_tools_inside_input_are_refused(self):
        src = self.game.parent / "tree"
        src.mkdir()
        (src / "A.luau").write_text("return 1\n")
        from recovery import recover_input
        with self.assertRaisesRegex(ValueError, "tools-dir"):
            recover_input(src, self.game.parent / "out", tools_dir=src / ".tools")

    def test_invalid_worker_setting_stops_capture_recovery_before_output(self):
        root = self.add(1001, ("Config", b"\x0e\x03\x01"))
        self.workers.side_effect = ValueError("RCR_WORKERS must be an integer: 'many'")
        with self.assertRaisesRegex(ValueError, "RCR_WORKERS"):
            recover_capture(root, self.game.parent / "offline")
        self.assertFalse((self.game.parent / "offline").exists())

    def test_legacy_summary_keeps_changes_of_unrecorded_runs(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        recover_game(self.game)
        legacy = json.loads((self.game / "recovery.json").read_text())
        legacy.pop("repository_created_at")  # written by an earlier version
        (self.game / "recovery.json").write_text(json.dumps(legacy))
        self.add(3003, ("Config", b"\x0e\x03\x02"))
        for _ in range(2):
            with patch("recovery._save", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                recover_game(self.game)
        recover_game(self.game)
        history = json.loads((self.game / "recovery.json").read_text())
        remaps = [r for run in history.get("earlier_changes", []) for r in run["changes"]["citation_remaps"]]
        self.assertEqual(remaps[0]["after"][-1], "Config@1001")

    def test_failed_cleanup_after_the_swap_is_reported(self):
        self.add(1001, ("Config", b"\x0e\x03\x01"))
        recover_game(self.game)
        with patch("recovery._rmtree", side_effect=PermissionError("locked")):
            result = recover_game(self.game)
        self.assertIn("locked", result["summary"]["cleanup_error"])
        self.assertIn("remove", validate_path(self.game)["checks"][-1]["failures"][0])


if __name__ == "__main__":
    unittest.main()
