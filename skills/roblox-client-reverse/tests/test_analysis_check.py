"""check-analysis: citation resolution and module mention coverage on a synthetic repository."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analysis_check import check_analysis, repository_map

SOURCES = {
    "Prices": ("Game/ReplicatedStorage/Config/Prices", "bc1",
               "local v1 = {LemonPrice = 10, OrangePrice = 25}\nreturn v1\n"),
    "Prices-copy": ("Game/Workspace/Plot1/Config/Prices", "bc1",
                    "local v1 = {LemonPrice = 10, OrangePrice = 25}\nreturn v1\n"),
    "Rebirth": ("Game/ReplicatedStorage/Modules/Rebirth", "bc2",
                "local v1 = {}\nfunction v1.GetRebirthCost(p1)\n    return p1 * 2\nend\nreturn v1\n"),
    "Offline": ("Game/ReplicatedStorage/Modules/OfflineIncome", "bc3",
                "local v1 = {}\nfunction v1.ClaimOfflineCash(p1)\nend\nreturn v1"),
    "Camera": ("Game/StarterPlayer/StarterPlayerScripts/PlayerModule/CameraModule", "bc4", "return {}\n"),
}


class AnalysisCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run = Path(self.temp.name)
        repo = self.run / "repository"
        scripts = []
        for key, (path, sha, text) in SOURCES.items():
            directory = "src/a1/" + key
            (repo / directory).mkdir(parents=True)
            (repo / directory / "init.luau").write_text(text, encoding="utf-8")
            scripts.append({"instance_path": path.split("/"), "class": "ModuleScript", "directory": directory,
                            "metadata": {"bytecode_sha256": sha}})
        (repo / "report.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def check(self, text, limit=40):
        doc = self.run / "doc.md"
        doc.write_text(text, encoding="utf-8")
        return check_analysis(self.run, doc, limit)

    def test_citations_resolve_with_or_without_datamodel_root(self):
        result = self.check("Cost `ReplicatedStorage/Modules/Rebirth:2-4` and `Game/ReplicatedStorage/Config/Prices:1`.")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["citations"], {"total": 2, "invalid": 0, "issues": []})

    def test_unquoted_citations_in_tables_are_checked(self):
        result = self.check("|Rebirth|x2|ReplicatedStorage/Modules/Rebirth:2, ReplicatedStorage/Modules/Rebirth:7|\n"
                            "ratio 3:1 and http://host:80 are not citations")
        self.assertEqual(result["citations"]["total"], 2)
        self.assertEqual([i["citation"] for i in result["citations"]["issues"]], ["ReplicatedStorage/Modules/Rebirth:7"])

    def test_bad_citations_are_reported(self):
        result = self.check("`ReplicatedStorage/Modules/Rebirth:9` `ReplicatedStorage/Modules/Missing:1` "
                            "`ReplicatedStorage/Modules/OfflineIncome:4` `ReplicatedStorage/Modules/OfflineIncome:5` `x:1`")
        self.assertEqual(result["status"], "invalid_citations")
        issues = {i["citation"]: i["issue"] for i in result["citations"]["issues"]}
        self.assertEqual(issues, {"ReplicatedStorage/Modules/Rebirth:9": "line_out_of_range",
                                  "ReplicatedStorage/Modules/Missing:1": "unknown_module",
                                  "ReplicatedStorage/Modules/OfflineIncome:5": "line_out_of_range"})
        self.assertEqual(result["citations"]["total"], 4)  # last line without a final newline counts

    def test_coverage_preserves_distinct_paths_and_skips_default_scripts(self):
        result = self.check("Rebirth costs double; LemonPrice and OrangePrice come from config.")
        coverage = result["coverage"]
        self.assertEqual((coverage["modules"], coverage["mentioned"], coverage["unmentioned"]), (4, 3, 1))
        self.assertEqual([r["path"] for r in coverage["largest_unmentioned"]], ["ReplicatedStorage/Modules/OfflineIncome"])
        self.assertEqual(coverage["unmentioned_by_folder"], {"ReplicatedStorage/Modules": 1})

    def test_one_shared_name_is_not_a_mention(self):
        coverage = self.check("Only LemonPrice is described.")["coverage"]
        self.assertIn("ReplicatedStorage/Config/Prices", [r["path"] for r in coverage["largest_unmentioned"]])

    def test_limit_bounds_lists(self):
        coverage = self.check("nothing", limit=1)["coverage"]
        self.assertEqual((coverage["unmentioned"], len(coverage["largest_unmentioned"])), (4, 1))

    def test_repository_map_counts_distinct_modules_per_folder(self):
        text = repository_map(self.run)
        self.assertIn("    1  ReplicatedStorage/Config  - Prices\n", text)
        self.assertIn("    2  ReplicatedStorage/Modules  - OfflineIncome, Rebirth\n", text)
        self.assertIn("Workspace/Plot1/Config  - Prices", text)
        self.assertNotIn("CameraModule", text)

    def test_unmentioned_map_is_written(self):
        out = self.run / "analysis/unmentioned.md"
        doc = self.run / "doc.md"
        doc.write_text("Rebirth costs double.", encoding="utf-8")
        check_analysis(self.run, doc, map_path=out)
        text = out.read_text(encoding="utf-8")
        self.assertIn("    1  ReplicatedStorage/Config  - Prices\n", text)
        self.assertIn("    1  ReplicatedStorage/Modules  - OfflineIncome\n", text)

    def test_variant_function_names_do_not_cover_another_version(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        for index, label in enumerate(["Logic@1001", "Logic@2002"]):
            directory = "src/" + label
            target = self.run / "repository" / directory
            target.mkdir()
            (target / "init.luau").write_text("function ComputeValue() end\nfunction ApplyValue() end\n")
            report["scripts"].append({"instance_path": ["Game", "ReplicatedStorage", label], "class": "ModuleScript",
                                      "directory": directory, "metadata": {"variant": {"representative_place_id": index},
                                      "bytecode_sha256": "variant-" + str(index)}})
        path.write_text(json.dumps(report))
        result = self.check("ComputeValue ApplyValue ReplicatedStorage/Logic@1001:1\n")
        self.assertEqual(result["citations"]["invalid"], 0)
        self.assertEqual(result["citations"]["total"], 1)
        unmentioned = {r["path"] for r in result["coverage"]["largest_unmentioned"]}
        self.assertNotIn("ReplicatedStorage/Logic@1001", unmentioned)
        self.assertIn("ReplicatedStorage/Logic@2002", unmentioned)
        result = self.check("Logic@10010")
        self.assertIn("ReplicatedStorage/Logic@1001", {r["path"] for r in result["coverage"]["largest_unmentioned"]})

    def test_ambiguous_original_path_is_not_resolved_arbitrarily(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        second = dict(report["scripts"][0], metadata={"bytecode_sha256": "different-content"})
        report["scripts"].append(second)
        path.write_text(json.dumps(report))
        result = self.check("`ReplicatedStorage/Config/Prices:1`")
        self.assertEqual(result["citations"]["issues"][0]["issue"], "ambiguous_module")

    def test_citation_like_text_that_cannot_be_checked_is_reported(self):
        result = self.check("`ReplicatedStorage/Modules/Rebirth:2, 40` and ReplicatedStorage/Modules/Rebirth:1-999x "
                            "and `ReplicatedStorage/Price:Bonus:9` and ReplicatedStorage/Price+Bonus:9 "
                            "ratio 3:1 and http://host:80/x are not citations")
        self.assertEqual(result["status"], "invalid_citations")
        issues = {i["citation"]: i["issue"] for i in result["citations"]["issues"]}
        self.assertEqual(issues, {"ReplicatedStorage/Modules/Rebirth:2, 40": "malformed_citation",
                                  "ReplicatedStorage/Modules/Rebirth:1-999x": "malformed_citation",
                                  "ReplicatedStorage/Price:Bonus:9": "malformed_citation",
                                  "ReplicatedStorage/Price+Bonus:9": "malformed_citation"})

    def test_game_folders_named_like_default_scripts_are_kept(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        target = self.run / "repository/src/health"
        target.mkdir(parents=True)
        (target / "init.luau").write_text("return {}\n")
        report["scripts"].append({"instance_path": ["Game", "ReplicatedStorage", "Health", "Config"], "class": "ModuleScript",
                                  "directory": "src/health", "metadata": {"bytecode_sha256": "health"}})
        path.write_text(json.dumps(report))
        self.assertIn("ReplicatedStorage/Health/Config", {r["path"] for r in self.check("x")["coverage"]["largest_unmentioned"]})
        self.assertIn("ReplicatedStorage/Health", repository_map(self.run))

    def test_supplied_file_paths_keep_their_first_element(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["operation"] = "extract"
        report["scripts"] = [dict(report["scripts"][2], instance_path=["Top", "Rebirth.luau"]),
                             dict(report["scripts"][3], instance_path=["Single.luau"])]
        path.write_text(json.dumps(report))
        text = repository_map(self.run)
        self.assertIn("    1  Top  - Rebirth.luau\n", text)
        self.assertIn("    1  (root)  - Single.luau\n", text)

    def test_outputs_never_replace_inputs_or_repository_files(self):
        doc = self.run / "doc.md"
        doc.write_text("Rebirth", encoding="utf-8")
        for target in (doc, self.run / "repository/report.json", self.run / "repository/new.md"):
            with self.assertRaises(ValueError):
                check_analysis(self.run, doc, map_path=target)
        self.assertEqual(doc.read_text(encoding="utf-8"), "Rebirth")
        self.assertFalse((self.run / "repository/new.md").exists())

    def test_valid_citations_followed_by_punctuation_are_not_malformed(self):
        result = self.check("- ReplicatedStorage/Modules/Rebirth:2: cost\n**ReplicatedStorage/Modules/Rebirth:3**:\n"
                            "[cost](ReplicatedStorage/Modules/Rebirth:4) ReplicatedStorage/Modules/Rebirth:2—note")
        self.assertEqual((result["status"], result["citations"]["total"]), ("pass", 4))

    def test_dotted_instance_paths_are_reported(self):
        result = self.check("`game.ReplicatedStorage.Modules.Rebirth:2`")
        self.assertEqual(result["citations"]["issues"][0]["issue"], "malformed_citation")

    def test_case_variant_of_an_input_or_repository_is_refused(self):
        doc = self.run / "doc.md"
        doc.write_text("Rebirth", encoding="utf-8")
        probe = self.run / "DOC.md"
        if not probe.exists():
            self.skipTest("case-sensitive file system")
        for target in (probe, self.run / "REPOSITORY" / "map.md"):
            with self.assertRaises(ValueError):
                check_analysis(self.run, doc, map_path=target)
        self.assertEqual(doc.read_text(encoding="utf-8"), "Rebirth")

    def test_only_default_script_locations_are_excluded(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        target = self.run / "repository/src/h"
        target.mkdir(parents=True)
        (target / "init.luau").write_text("return {}\n")
        for i, parts in enumerate([["Game", "Workspace", "Map", "Health", "Config"],
                                   ["Game", "ReplicatedStorage", "StarterPlayerScripts", "Health", "Config"],
                                   ["Game", "Players", "Alice", "PlayerScripts", "PlayerModule", "Camera"]]):
            report["scripts"].append({"instance_path": parts, "class": "ModuleScript", "directory": "src/h",
                                      "metadata": {"bytecode_sha256": "h%d" % i}})
        path.write_text(json.dumps(report))
        listed = {r["path"] for r in self.check("x", limit=None)["coverage"]["largest_unmentioned"]}
        self.assertIn("Workspace/Map/Health/Config", listed)
        self.assertIn("ReplicatedStorage/StarterPlayerScripts/Health/Config", listed)
        self.assertNotIn("Players/Alice/PlayerScripts/PlayerModule/Camera", listed)

    def test_supplied_file_paths_are_cited_in_full(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["operation"] = "extract"
        report["scripts"][2]["instance_path"] = ["Root", "Modules", "Rebirth"]
        path.write_text(json.dumps(report))
        issues = {i["citation"]: i["issue"] for i in self.check("`Modules/Rebirth:1` `Root/Modules/Rebirth:1`")["citations"]["issues"]}
        self.assertEqual(issues, {"Modules/Rebirth:1": "unknown_module"})

    def test_modules_without_recovered_source_are_not_mapped(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["scripts"][2]["recovery"] = "decompilation_failed"
        path.write_text(json.dumps(report))
        self.assertNotIn("Rebirth", repository_map(self.run))

    def test_en_dash_between_line_numbers_is_a_range(self):
        result = self.check("`ReplicatedStorage/Modules/Rebirth:2\u20134` and ReplicatedStorage/Modules/Rebirth:1\u20133.")
        self.assertEqual((result["status"], result["citations"]["total"]), ("pass", 2))
        issues = self.check("ReplicatedStorage/Modules/Rebirth:2\u2013999")["citations"]["issues"]
        self.assertEqual(issues, [{"citation": "ReplicatedStorage/Modules/Rebirth:2-999", "issue": "line_out_of_range", "source_lines": 5}])

    def test_unquoted_range_spellings_are_not_truncated(self):
        for text in ("ReplicatedStorage/Modules/Rebirth:2\u2013999", "ReplicatedStorage/Modules/Rebirth:2:999",
                     "ReplicatedStorage/Modules/Rebirth:2..999"):
            with self.subTest(text=text):
                self.assertEqual(self.check(text)["status"], "invalid_citations")

    def test_dotted_paths_follow_recovered_module_names(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["operation"] = "extract"
        report["scripts"][2]["instance_path"] = ["Main.client.luau"]
        path.write_text(json.dumps(report))
        self.assertEqual(self.check("`Main.client.luau:1`")["status"], "pass")
        self.assertEqual(self.check("`ReplicatedStorage.Rebirth:1`")["citations"]["issues"][0]["issue"], "malformed_citation")

    def test_default_script_names_below_another_service_are_kept(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["scripts"].append({"instance_path": ["Game", "ReplicatedStorage", "StarterPlayer", "StarterPlayerScripts",
                                                    "PlayerModule", "Camera"], "class": "ModuleScript",
                                  "directory": "src/a1/Prices", "metadata": {"bytecode_sha256": "nested"}})
        path.write_text(json.dumps(report))
        listed = {r["path"] for r in self.check("x", limit=None)["coverage"]["largest_unmentioned"]}
        self.assertIn("ReplicatedStorage/StarterPlayer/StarterPlayerScripts/PlayerModule/Camera", listed)

    def test_spaced_ranges_and_lists_after_known_paths_are_malformed(self):
        path = self.run / "repository/report.json"
        report = json.loads(path.read_text())
        report["operation"] = "extract"
        report["scripts"][2]["instance_path"] = ["Main.client.luau"]
        path.write_text(json.dumps(report))
        for text in ("`Main.client.luau:2, 9`", "Folders/Main.client.luau:2 \u2014 999", "Top/Main:2 - 9"):
            with self.subTest(text=text):
                self.assertEqual(self.check(text)["status"], "invalid_citations")

    def test_list_on_the_next_line_is_not_a_range(self):
        self.assertEqual(self.check("See ReplicatedStorage/Modules/Rebirth:2\n- 9 levels are available.")["status"], "pass")
        self.assertEqual(self.check("ReplicatedStorage/Modules/Rebirth:2 `a\nb` - 9 levels")["status"], "pass")
        self.assertEqual(self.check("ReplicatedStorage/Modules/Rebirth:2\u00a0\u2013\u00a09")["status"], "invalid_citations")


if __name__ == "__main__":
    unittest.main()
