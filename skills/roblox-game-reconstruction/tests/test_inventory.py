import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inventory


def sha(data):
    return hashlib.sha256(data).hexdigest()


def repo_fixture(tmp_path, *, cfg=True):
    repo = tmp_path / "repo"
    module = repo / "src" / "module"
    module.mkdir(parents=True)
    source = b"local function helper() return 1 end\nreturn helper()\n"
    bytecode = b"original-bytecode"
    (module / "init.luau").write_bytes(source)
    (module / "original.bytecode").write_bytes(bytecode)
    record = {
        "id": "id-1", "class": "ModuleScript", "instance_path": ["Root", "Module"],
        "source": "src/module/init.luau", "source_sha256": sha(source),
        "bytecode": {"path": "src/module/original.bytecode", "sha256": sha(bytecode)},
        "verification": {"syntax": "SYNTAX_VALID", "cfg": "UNVERIFIED"},
    }
    if cfg:
        audit = module / "cfg-audit"
        audit.mkdir()
        (audit / "comparison-O1.json").write_text(json.dumps({"functions": [
            {"proto": 99, "matching_recovered_protos": [1], "status": "CFG_VALIDATED", "name": "helper", "certificate": "body"},
            {"proto": 1, "matching_recovered_protos": [], "status": "UNVERIFIED", "name": "possible"},
        ], "module_proof": {"status": "UNVERIFIED"}}))
        (audit / "recompiled-O1.json").write_text(json.dumps({"mainProto": 9, "protos": [
            {"index": 0, "name": "helper0", "line": 1, "params": 0, "upvalues": 0, "children": [], "instructions": []},
            {"index": 1, "name": "helper", "line": 1, "params": 0, "upvalues": 0, "children": [], "instructions": []},
            {"index": 9, "name": "root", "line": 1, "params": 0, "upvalues": 0, "children": [], "instructions": []},
        ]}))
        (audit / "original.json").write_text(json.dumps({"mainProto": 7, "protos": [{"index": 0}, {"index": 7}]}))
        record["cfg_audit"] = {"evidence": "src/module/cfg-audit", "best_optimization": 1, "status": "UNVERIFIED"}
    (repo / "report.json").write_text(json.dumps({"scripts": [record]}))
    return repo


class InventoryTests(unittest.TestCase):
    def test_remote_event_connections_and_callback_assignments_are_inventoried(self):
        tmp = self.make_tmp(); repo = repo_fixture(tmp, cfg=False)
        source = b'''remote:FireServer(1)
remote.OnClientEvent:Connect(function() end)
remote.OnServerEvent:Connect(function() end)
remote.OnClientInvoke = function() return 1 end
remote.OnServerInvoke = function() return 2 end
local text = "OnClientEvent:Connect(fake)"
'''
        (repo/'src/module/init.luau').write_bytes(source)
        report = json.loads((repo/'report.json').read_text())
        report['scripts'][0]['source_sha256'] = sha(source)
        (repo/'report.json').write_text(json.dumps(report))
        inventory.build(repo, tmp/'out')
        calls = json.loads((tmp/'out/remote-candidates/calls.json').read_text())
        self.assertEqual({c['method']for c in calls}, {'FireServer','OnClientEvent','OnServerEvent','OnClientInvoke','OnServerInvoke'})
        self.assertEqual(len(calls), 5)
        self.assertEqual(sum(c['form']=='callback_assignment'for c in calls), 2)

    def make_tmp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_rejects_source_hash_mismatch(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        report_path = repo / "report.json"
        report = json.loads(report_path.read_text())
        report["scripts"][0]["source_sha256"] = "0" * 64
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            inventory.build(repo, tmp / "out")

    def test_source_only_input_is_rejected_with_the_documented_limitation(self):
        tmp=self.make_tmp();repo=repo_fixture(tmp,cfg=False)
        report=json.loads((repo/'report.json').read_text());report['scripts'][0].pop('bytecode')
        (repo/'report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,'source-only.*bytecode.sha256'):
            inventory.build(repo,tmp/'out')

    def test_rejects_path_escape(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        report_path = repo / "report.json"
        report = json.loads(report_path.read_text())
        report["scripts"][0]["source"] = "../outside.luau"
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "unsafe|escapes"):
            inventory.build(repo, tmp / "out")

    def test_missing_cfg_is_unverified_and_module_unknown(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        out = tmp / "out"
        inventory.build(repo, out)
        modules = json.loads((out / "modules.json").read_text())
        functions = json.loads((out / "functions.json").read_text())
        self.assertEqual(modules["modules"][0]["declared_cfg"], "UNVERIFIED")
        self.assertEqual(modules["modules"][0]["proof_status"], "UNKNOWN")
        self.assertEqual(functions["functions"], [])

    def test_function_map_uses_comparison_proto_mapping(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp)
        out = tmp / "out"
        inventory.build(repo, out)
        functions = json.loads((out / "functions.json").read_text())["functions"]
        self.assertEqual([(f["recompiled_proto"], f["cfg_status"]) for f in functions],
                         [(0, "UNVERIFIED"), (1, "CFG_VALIDATED"), (9, "UNVERIFIED")])
        self.assertEqual(functions[1]["certified_original_body_matches"][0]["proto"], 99)
        module = json.loads((out / "modules.json").read_text())["modules"][0]
        self.assertEqual(module["proof_status"], "UNVERIFIED")

    def test_rejects_bytecode_hash_mismatch(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        report_path = repo / "report.json"
        report = json.loads(report_path.read_text())
        report["scripts"][0]["bytecode"]["sha256"] = "0" * 64
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "bytecode hash mismatch"):
            inventory.build(repo, tmp / "out")

    def test_arbitrary_method_call_does_not_resolve_child(self):
        self.assertEqual(inventory.simple_path('script:WaitForChild("Child")', ["Root", "Module"], {}),
                         ["Root", "Module", "Child"])
        self.assertIsNone(inventory.simple_path('script:Foo("Child")', ["Root", "Module"], {}))

    def test_helper_proto_zero_match_does_not_certify_module_main(self):
        comparison = {"module_proof": {"status": "CFG_VALIDATED", "prototype_mapping": {"7": 8}},
                      "functions": [{"proto": 0, "matching_recovered_protos": [0], "status": "CFG_VALIDATED"}]}
        compiled = {"mainProto": 9, "protos": [{"index": 0}, {"index": 9}]}
        original = {"mainProto": 7, "protos": [{"index": 0}, {"index": 7}]}
        self.assertEqual(inventory._module_cfg_status(comparison, compiled, original), "UNVERIFIED")

    def test_exact_comparison_main_seven_to_nine_certificate_validates_module(self):
        comparison = {"status": "CFG_VALIDATED", "functions": [
            {"proto": 0, "matching_recovered_protos": [0], "status": "CFG_VALIDATED"},
            {"proto": 7, "matching_recovered_protos": [9], "status": "CFG_VALIDATED"}]}
        compiled = {"mainProto": 9, "protos": [{"index": 0}, {"index": 9}]}
        original = {"mainProto": 7, "protos": [{"index": 0}, {"index": 7}]}
        self.assertEqual(inventory._module_cfg_status(comparison, compiled, original), "CFG_VALIDATED")

    def test_explicit_main_seven_to_nine_certificate_validates_module(self):
        comparison = {"module_proof": {"status": "CFG_VALIDATED", "prototype_mapping": {"7": 9}},
                      "functions": [{"proto": 0, "matching_recovered_protos": [0], "status": "CFG_VALIDATED"}]}
        compiled = {"mainProto": 9, "protos": [{"index": 0}, {"index": 9}]}
        original = {"mainProto": 7, "protos": [{"index": 0}, {"index": 7}]}
        self.assertEqual(inventory._module_cfg_status(comparison, compiled, original), "CFG_VALIDATED")

    def test_explicit_root_certificate_can_supply_main_mapping(self):
        comparison = {"module_proof": {"status": "CFG_VALIDATED", "functions": [
            {"original_proto": 7, "recompiled_proto": 9, "status": "CFG_VALIDATED"}]}}
        compiled = {"mainProto": 9, "protos": [{"index": 9}]}
        original = {"mainProto": 7, "protos": [{"index": 7}]}
        self.assertEqual(inventory._module_cfg_status(comparison, compiled, original), "CFG_VALIDATED")

    def test_module_cfg_requires_original_main_certificate(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp)
        report = json.loads((repo / "report.json").read_text())
        audit = repo / "src/module/cfg-audit/comparison-O1.json"
        comparison = json.loads(audit.read_text())
        comparison["module_proof"] = {"status": "CFG_VALIDATED", "prototype_mapping": {"7": 9}}
        audit.write_text(json.dumps(comparison))
        out = tmp / "out"
        inventory.build(repo, out)
        module = json.loads((out / "modules.json").read_text())["modules"][0]
        self.assertEqual(module["declared_cfg"], "UNVERIFIED")
        self.assertEqual(module["module_cfg"], "CFG_VALIDATED")
        self.assertEqual(module["best_optimization"], 1)
        self.assertEqual(module["cfg_evidence"], "src/module/cfg-audit")
        self.assertEqual(module["original_bytecode"], "src/module/original.bytecode")

    def test_output_must_be_outside_input_repo(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        with self.assertRaisesRegex(ValueError, "outside the input repository"):
            inventory.build(repo, repo / "inventory")

    def test_rejects_one_bytecode_digest_with_different_sources(self):
        tmp = self.make_tmp()
        repo = repo_fixture(tmp, cfg=False)
        report_path = repo / "report.json"
        report = json.loads(report_path.read_text())
        other_source = b"return 2\n"
        other_path = repo / "src/module/other.luau"
        other_path.write_bytes(other_source)
        duplicate = dict(report["scripts"][0])
        duplicate.update({"id": "id-2", "source": "src/module/other.luau", "source_sha256": sha(other_source)})
        report["scripts"].append(duplicate)
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "associated with differing source hashes"):
            inventory.build(repo, tmp / "out")


if __name__ == "__main__":
    unittest.main()
