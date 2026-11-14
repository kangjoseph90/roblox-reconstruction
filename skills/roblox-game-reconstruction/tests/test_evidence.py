import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evidence
import inventory


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    source = b"local function helper()\n return 1\nend\nreturn helper()\n"
    bytecode = b"original-bytecode"
    (repo / "src" / "module.luau").write_bytes(source)
    (repo / "src" / "module.bc").write_bytes(bytecode)
    cfg = repo / "src" / "cfg"
    cfg.mkdir()
    (cfg / "comparison-O1.json").write_text(json.dumps({"functions": [
        {"proto": 99, "matching_recovered_protos": [1], "status": "CFG_VALIDATED", "certificate": "body"},
        {"proto": 0, "matching_recovered_protos": [], "status": "UNVERIFIED"},
    ]}))
    (cfg / "recompiled-O1.json").write_text(json.dumps({"protos": [
        {"index": 0, "name": "root", "line": 1, "instructions": [{"line": 4}]},
        {"index": 1, "name": "helper", "line": 1, "instructions": [{"line": 2}]},
    ]}))
    (cfg / "original.json").write_text(json.dumps({"protos": [{"index": 0}]}))
    report = {"scripts": [{"id": "m1", "class": "ModuleScript", "instance_path": ["Root", "M"],
        "source": "src/module.luau", "source_sha256": sha(source),
        "bytecode": {"path": "src/module.bc", "sha256": sha(bytecode)},
        "verification": {"syntax": "SYNTAX_VALID", "cfg": "UNVERIFIED"},
        "cfg_audit": {"evidence": "src/cfg", "best_optimization": 1}}]}
    (repo / "report.json").write_text(json.dumps(report))
    run = tmp_path / "run"
    shared = run / "shared"
    shared.mkdir(parents=True)
    inventory.build(repo, shared)
    modules = json.loads((shared / "modules.json").read_text())["modules"]
    functions = json.loads((shared / "functions.json").read_text())["functions"]
    mid, fn = modules[0]["module_id"], next(f for f in functions if f["recompiled_proto"] == 1)
    shared_files = [{"path": name, "sha256": sha((shared / name).read_bytes())}
                    for name in ("modules.json", "functions.json", "input-manifest.json")]
    (run / "shared-manifest.json").write_text(json.dumps({"files": shared_files}))
    (run / "config.json").write_text(json.dumps({"repo": str(repo.resolve()),
        "input_report_sha256": sha((repo / "report.json").read_bytes()),
        "shared_manifest_sha256": sha((run / "shared-manifest.json").read_bytes())}))
    (run / "catalog.json").write_text(json.dumps({"systems": [{"id": "economy", "module_ids": [mid]}], "exclusions": []}))
    (run / "analysis-plan.json").write_text(json.dumps({"assignments": [{"system_id": "economy"}]}))
    analysis_dir = run / "analysis" / "economy"
    analysis_dir.mkdir(parents=True)
    analysis = {"system_id": "economy", "claims": [{"id": "C1", "kind": "CODE_OBSERVED",
        "statement": "Helper returns a literal.", "evidence": [{"source_file": "src/module.luau",
            "function_id": fn["function_id"], "line_start": 1, "line_end": 2}]}],
        "contracts": [], "tests": []}
    (analysis_dir / "analysis.json").write_text(json.dumps(analysis))
    (analysis_dir / "spec.md").write_text("# Economy\n")
    (run / "review.json").write_text(json.dumps({"mode": "coordinator_only", "checked_system_ids": ["economy"], "findings": []}))
    (run / "conflicts.json").write_text(json.dumps({"conflicts": []}))
    return run, repo, fn


def load_json(path):
    return json.loads(path.read_text())


class EvidenceAuditTests(unittest.TestCase):
  def make_fixture(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    return fixture(Path(self.temp.name))

  def test_audit_uses_body_status_without_promoting_module(self):
    run, _, _ = self.make_fixture()
    result = evidence.audit(run)
    self.assertEqual(result["status"], "pass")
    row = load_json(run / "rules.json")["rules"][0]["evidence"][0]
    self.assertEqual(row["function_cfg_status"], "CFG_VALIDATED")
    self.assertEqual(row["module_cfg_status"], "UNVERIFIED")
    self.assertEqual(row["behavior"], "NOT_TESTED")


  def test_detects_input_hash_change_since_inventory(self):
    run, repo, _ = self.make_fixture()
    with (repo / "src" / "module.luau").open("ab") as stream:
        stream.write(b"-- changed\n")
    result = evidence.audit(run)
    self.assertEqual(result["status"], "failed")
    self.assertTrue(any(e["issue"] in {"input_file_hash_mismatch", "source_snapshot_hash_mismatch"} for e in result["errors"]))


  def test_forged_worker_cfg_label_is_ignored(self):
    run, _, _ = self.make_fixture()
    path = run / "analysis" / "economy" / "analysis.json"
    data = load_json(path)
    data["claims"][0]["evidence"][0]["claimed_cfg"] = "CFG_VALIDATED"
    path.write_text(json.dumps(data))
    # Use root's function, whose existing registry status is UNVERIFIED.
    functions = load_json(run / "shared" / "functions.json")["functions"]
    root_fn = next(f for f in functions if f["recompiled_proto"] == 0)
    data["claims"][0]["evidence"][0]["function_id"] = root_fn["function_id"]
    data["claims"][0]["evidence"][0]["line_start"] = 4
    data["claims"][0]["evidence"][0]["line_end"] = 4
    path.write_text(json.dumps(data))
    result = evidence.audit(run)
    row = load_json(run / "rules.json")["rules"][0]["evidence"][0]
    self.assertEqual(row["function_cfg_status"], "UNVERIFIED")
    self.assertTrue(any(w["issue"] == "worker_cfg_label_ignored" for w in result["warnings"]))

  def test_tampered_function_registry_cannot_grant_cfg(self):
    run, _, _ = self.make_fixture()
    functions_path = run / "shared" / "functions.json"
    functions = load_json(functions_path)
    helper = next(f for f in functions["functions"] if f["recompiled_proto"] == 1)
    helper["cfg_status"] = "CFG_VALIDATED"
    functions_path.write_text(json.dumps(functions))
    result = evidence.audit(run)
    row = load_json(run / "rules.json")["rules"][0]["evidence"][0]
    self.assertEqual(result["status"], "failed")
    self.assertEqual(row["function_cfg_status"], "UNVERIFIED")
    self.assertTrue(any(e["issue"] == "shared_registry_hash_mismatch" for e in result["errors"]))


  def test_rejects_wrong_function_mapping_and_out_of_scope_range(self):
    run, _, _ = self.make_fixture()
    path = run / "analysis" / "economy" / "analysis.json"
    data = load_json(path)
    ev = data["claims"][0]["evidence"][0]
    ev["line_start"], ev["line_end"] = 3, 3
    path.write_text(json.dumps(data))
    result = evidence.audit(run)
    issues = {e["issue"] for e in result["errors"]}
    self.assertIn("citation_outside_function_scope", issues)
    ev["function_id"] = "different-source-function"
    path.write_text(json.dumps(data))
    result = evidence.audit(run)
    self.assertTrue(any(e["issue"] == "unknown_function_id" for e in result["errors"]))


  def test_rejects_executed_or_behavior_verified_game_test(self):
    run, _, _ = self.make_fixture()
    path = run / "analysis" / "economy" / "analysis.json"
    data = load_json(path)
    data["tests"] = [{"id": "T1", "status": "EXECUTED", "executed": True, "behavior": "BEHAVIOR_VERIFIED"}]
    path.write_text(json.dumps(data))
    result = evidence.audit(run)
    issues = {e["issue"] for e in result["errors"]}
    self.assertLessEqual({"test_execution_claim_forbidden", "behavior_verification_claim_forbidden"}, issues)

  def test_rejects_unknown_modules_and_duplicate_system_ownership(self):
    run, _, _ = self.make_fixture()
    path = run / "catalog.json"
    catalog = load_json(path)
    catalog["exclusions"].append({"module_id": "not-in-input"})
    catalog["systems"].append(dict(catalog["systems"][0]))
    path.write_text(json.dumps(catalog))
    issues = {e["issue"] for e in evidence.audit(run)["errors"]}
    self.assertLessEqual({"unknown_inventory_module_id", "duplicate_system_id", "module_ownership"}, issues)

  def test_inventory_alone_and_unsafe_system_paths_cannot_be_packaged(self):
    run, _, _ = self.make_fixture()
    path = run / "catalog.json"
    catalog = load_json(path)
    catalog["systems"][0]["id"] = "../outside"
    path.write_text(json.dumps(catalog))
    issues = {e["issue"] for e in evidence.audit(run)["errors"]}
    self.assertIn("invalid_system_id", issues)
    catalog["systems"] = []
    path.write_text(json.dumps(catalog))
    issues = {e["issue"] for e in evidence.audit(run)["errors"]}
    self.assertIn("systems_missing", issues)

