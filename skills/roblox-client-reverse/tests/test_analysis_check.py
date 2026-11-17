"""check-analysis: citation resolution and module mention coverage on a synthetic repository."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analysis_check import check_analysis

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

    def test_bad_citations_are_reported(self):
        result = self.check("`ReplicatedStorage/Modules/Rebirth:9` `ReplicatedStorage/Modules/Missing:1` "
                            "`ReplicatedStorage/Modules/OfflineIncome:4` `ReplicatedStorage/Modules/OfflineIncome:5` `x:1`")
        self.assertEqual(result["status"], "invalid_citations")
        issues = {i["citation"]: i["issue"] for i in result["citations"]["issues"]}
        self.assertEqual(issues, {"ReplicatedStorage/Modules/Rebirth:9": "line_out_of_range",
                                  "ReplicatedStorage/Modules/Missing:1": "unknown_module",
                                  "ReplicatedStorage/Modules/OfflineIncome:5": "line_out_of_range"})
        self.assertEqual(result["citations"]["total"], 4)  # last line without a final newline counts

    def test_coverage_dedupes_bytecode_and_skips_default_scripts(self):
        result = self.check("Rebirth costs double; LemonPrice and OrangePrice come from config.")
        coverage = result["coverage"]
        self.assertEqual((coverage["modules"], coverage["mentioned"], coverage["unmentioned"]), (3, 2, 1))
        self.assertEqual([r["path"] for r in coverage["largest_unmentioned"]], ["ReplicatedStorage/Modules/OfflineIncome"])
        self.assertEqual(coverage["unmentioned_by_folder"], {"ReplicatedStorage/Modules": 1})

    def test_one_shared_name_is_not_a_mention(self):
        coverage = self.check("Only LemonPrice is described.")["coverage"]
        self.assertIn("ReplicatedStorage/Config/Prices", [r["path"] for r in coverage["largest_unmentioned"]])

    def test_limit_bounds_lists(self):
        coverage = self.check("nothing", limit=1)["coverage"]
        self.assertEqual((coverage["unmentioned"], len(coverage["largest_unmentioned"])), (3, 1))


if __name__ == "__main__":
    unittest.main()
