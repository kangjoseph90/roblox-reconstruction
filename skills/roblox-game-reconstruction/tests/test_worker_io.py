"""Forward tests for worker scaffolding and validation interfaces.

All assertions use the synthetic repository fixture from test_inventory; they
check document shape, paths and provenance checks, not game-specific meaning.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import reconstruct
from test_inventory import repo_fixture
from test_semantic import trace_fixture


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class WorkerIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = repo_fixture(root, cfg=True)
        self.run = root / "run"
        reconstruct.prepare(self.repo, self.run)
        self.modules = read_json(self.run / "shared/modules.json")["modules"]
        self.functions = read_json(self.run / "shared/functions.json")["functions"]
        self.module = self.modules[0]
        self.function = self.functions[1]
        write_json(self.run / "catalog.json", {
            "systems": [{"id": "sample", "module_ids": [self.module["module_id"]]}]
        })

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "reconstruct.py"), *map(str, args)],
            text=True, capture_output=True, check=False,
        )

    def cli_json(self, *args):
        result = self.cli(*args)
        stream = result.stdout if result.stdout.strip() else result.stderr
        return result, json.loads(stream)

    def scaffold_review_cli(self, review_id="review-a"):
        return self.cli(
            "scaffold", "--run", self.run, "--kind", "source-review",
            "--id", review_id, "--module-id", self.module["module_id"],
            "--reviewer", "fixture reviewer",
        )

    def complete_review(self, *, trace_status="TRACED", trace=None, trace_limit=None):
        result = self.scaffold_review_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.run / "review-drafts/review-a.json"
        doc = read_json(path)
        doc["draft"] = False
        doc["scope"]["limits"] = "Bounded synthetic fixture."
        fnid = self.function["function_id"]
        ev = {"source_file": self.module["original_source"],
              "function_id": fnid,
              "line_start": self.function["source_line_start"],
              "line_end": self.function["source_line_start"]}
        doc["observations"] = [{
            "id": "O1", "statement": "A generic value is observed.",
            "importance": "material", "evidence": [ev],
        }, {
            "id": "O2", "statement": "A generic effect is observed.",
            "importance": "supporting", "evidence": [ev],
        }]
        relation = {
            "id": "R1", "from": "O1", "to": "O2", "kind": "generic_link",
            "statement": "The observations are linked in the fixture.",
            "importance": "material", "evidence": [ev],
            "trace_function_ids": [fnid] if trace is None else trace,
            "trace_status": trace_status,
        }
        if trace_limit is not None:
            relation["trace_limit"] = trace_limit
        doc["relations"] = [relation]
        for fn in doc["functions"]:
            if fn["function_id"] == fnid:
                fn.update(status="TRACED", observation_ids=["O1", "O2"], relation_ids=["R1"], trace=trace_fixture(ev))
            else:
                fn["remaining"] = ["Outside this bounded fixture pass."]
        doc["cost"] = {"files_read": 1, "functions_traced": 1,
                       "searches": 1, "elapsed_seconds": 0}
        write_json(path, doc)
        return path, doc

    def test_removing_the_draft_key_does_not_finish_a_worker(self):
        path, _ = self.complete_review()
        doc = read_json(path)
        del doc["draft"]
        write_json(path, doc)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertIn("draft_not_finished", {e["issue"] for e in payload["errors"]})

    def test_cli_scaffolds_analysis_and_review_at_canonical_paths(self):
        result, payload = self.cli_json(
            "scaffold", "--run", self.run, "--kind", "analysis", "--system", "sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["status"], "draft_created")
        self.assertEqual(Path(payload["output"]), (self.run / "analysis/sample/analysis.json").resolve())
        self.assertTrue((self.run / "analysis/sample/spec.md").is_file())
        analysis = read_json(self.run / "analysis/sample/analysis.json")
        self.assertEqual(analysis["system_id"], "sample")
        self.assertEqual(analysis["coverage"]["module_ids"], [self.module["module_id"]])

        result, payload = self.cli_json(
            "scaffold", "--run", self.run, "--kind", "source-review", "--id", "review-b",
            "--module-id", self.module["module_id"], "--reviewer", "fixture reviewer")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(payload["output"]), (self.run / "review-drafts/review-b.json").resolve())
        self.assertTrue((self.run / "review-drafts/review-b.json").is_file())

    def test_scaffold_refuses_to_overwrite_either_worker_output(self):
        args = ("scaffold", "--run", self.run, "--kind", "analysis", "--system", "sample")
        self.assertEqual(self.cli(*args).returncode, 0)
        before = (self.run / "analysis/sample/analysis.json").read_bytes()
        result, payload = self.cli_json(*args)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("refusing overwrite", payload["error"])
        self.assertEqual((self.run / "analysis/sample/analysis.json").read_bytes(), before)

        args = ("scaffold", "--run", self.run, "--kind", "source-review", "--id", "review-c",
                "--module-id", self.module["module_id"], "--reviewer", "fixture reviewer")
        self.assertEqual(self.cli(*args).returncode, 0)
        before = (self.run / "review-drafts/review-c.json").read_bytes()
        result, payload = self.cli_json(*args)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("refusing overwrite", payload["error"])
        self.assertEqual((self.run / "review-drafts/review-c.json").read_bytes(), before)

    def test_cli_reports_unsafe_worker_ids_as_structured_errors(self):
        result, payload = self.cli_json(
            "scaffold", "--run", self.run, "--kind", "source-review", "--id", "../bad",
            "--module-id", self.module["module_id"], "--reviewer", "fixture reviewer")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("safe --id", payload["error"])

    def test_validator_cli_reports_malformed_shapes_as_structured_errors(self):
        path, doc = self.complete_review()
        malformed = dict(doc)
        malformed["observations"] = ["not an object"]
        input_path = self.run / "malformed.json"
        write_json(input_path, malformed)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", input_path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("object_required", {error["issue"] for error in payload["errors"]})
        self.assertTrue(all("location" in error for error in payload["errors"]))

    def test_validator_rejects_wrong_function_source_and_citation_range(self):
        path, doc = self.complete_review()
        foreign = json.loads(json.dumps(doc))
        foreign["observations"][0]["evidence"][0]["function_id"] = "unknown-function"
        write_json(path, foreign)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("function_source_mismatch", {error["issue"] for error in payload["errors"]})

        outside = json.loads(json.dumps(doc))
        outside["observations"][0]["evidence"][0]["line_start"] = 999
        outside["observations"][0]["evidence"][0]["line_end"] = 999
        write_json(path, outside)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid_source_span", {error["issue"] for error in payload["errors"]})

        wrong_function_range = json.loads(json.dumps(doc))
        outside_line = 1 if self.function["source_line_start"] > 1 else 2
        wrong_function_range["observations"][0]["evidence"][0]["line_start"] = outside_line
        wrong_function_range["observations"][0]["evidence"][0]["line_end"] = outside_line
        write_json(path, wrong_function_range)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("citation_outside_function", {error["issue"] for error in payload["errors"]})

    def test_empty_traced_relation_is_rejected_before_freeze_side_effects(self):
        path, _ = self.complete_review(trace=[])
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("trace_functions_required", {error["issue"] for error in payload["errors"]})

        result, payload = self.cli_json(
            "freeze-review", "--run", self.run, "--id", "review-empty-trace", "--input", path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("preflight failed", payload["error"])
        self.assertFalse((self.run / "source-reviews/review-empty-trace").exists())
        config = read_json(self.run / "config.json")
        self.assertNotIn("review-empty-trace", config.get("source_review_pins", {}))

    def test_explicit_unresolved_trace_with_limit_is_accepted(self):
        path, _ = self.complete_review(
            trace_status="UNRESOLVED", trace=[], trace_limit="The generic control path remains open.")
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "source-review", "--input", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["status"], "pass")

    def test_rationale_and_subject_links_cannot_replace_a_function_trace(self):
        path, doc = self.complete_review()
        fn = next(f for f in doc['functions'] if f['status'] == 'TRACED')
        fn.pop('trace')
        fn['rationale'] = 'Traced in the domain analysis.'
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'source-review', '--input', path)
        self.assertEqual(result.returncode, 2)
        self.assertIn('function_trace_required', {e['issue'] for e in payload['errors']})
        result, payload = self.cli_json('freeze-review', '--run', self.run, '--id', 'unsupported', '--input', path)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.run/'source-reviews/unsupported').exists())

    def test_trace_steps_require_own_body_citations_and_in_range_lines(self):
        path, original = self.complete_review()
        cases = [('wrong_body', 'function_trace_wrong_body'), ('wrong_lines', 'invalid_source_span'),
                 ('no_steps', 'function_trace_steps_required'), ('empty_evidence', 'function_trace_evidence_required')]
        for case, expected in cases:
            with self.subTest(case=case):
                doc = json.loads(json.dumps(original))
                trace = next(f for f in doc['functions'] if f['status'] == 'TRACED')['trace']
                if case == 'wrong_body': trace['steps'][0]['evidence'][0]['function_id'] = self.functions[0]['function_id']
                elif case == 'wrong_lines': trace['steps'][0]['evidence'][0].update(line_start=999, line_end=999)
                elif case == 'no_steps': trace['steps'] = []
                else: trace['steps'][0]['evidence'] = []
                write_json(path, doc)
                result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'source-review', '--input', path)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, {e['issue'] for e in payload['errors']})

    def test_author_coverage_cannot_omit_functions_or_use_a_bulk_rationale(self):
        self.assertEqual(self.cli('scaffold', '--run', self.run, '--kind', 'analysis', '--system', 'sample').returncode, 0)
        path = self.run/'analysis/sample/analysis.json'
        doc = read_json(path)
        doc['draft'] = False
        doc['coverage']['functions'][0].update(status='TRACED', rationale='The whole module was traced.')
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 2)
        self.assertIn('function_trace_required', {e['issue'] for e in payload['errors']})
        doc['coverage'].pop('functions')
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 2)
        self.assertIn('array_required', {e['issue'] for e in payload['errors']})

    def test_malformed_status_is_reported_and_unresolved_evidence_lists_are_supported(self):
        path, doc = self.complete_review()
        doc['functions'][0]['status'] = {'invalid': 'shape'}
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'source-review', '--input', path)
        self.assertEqual(result.returncode, 2)
        self.assertIn('function_status_invalid', {e['issue'] for e in payload['errors']})
        self.assertEqual(self.cli('scaffold', '--run', self.run, '--kind', 'analysis', '--system', 'sample').returncode, 0)
        path = self.run / 'analysis/sample/analysis.json'
        doc = read_json(path)
        doc.update(draft=False, unknowns=[{'id':'U', 'question':'Which handler?', 'impact':'Authority unknown', 'missing_evidence':['Server handler']}])
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 0, payload)

    def test_cross_system_reference_is_deferred_only_for_catalog_owner(self):
        catalog = read_json(self.run / 'catalog.json')
        catalog['systems'].append({'id':'other', 'module_ids':[]})
        write_json(self.run / 'catalog.json', catalog)
        self.assertEqual(self.cli('scaffold', '--run', self.run, '--kind', 'analysis', '--system', 'sample').returncode, 0)
        path = self.run / 'analysis/sample/analysis.json'
        doc = read_json(path)
        doc.update(draft=False, interfaces=[{'id':'I', 'claim_ids':['other:R']}])
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 0, payload)
        self.assertIn('cross_system_reference_requires_integration', {e['issue'] for e in payload['warnings']})
        doc['interfaces'][0]['claim_ids'] = ['invented:R']
        write_json(path, doc)
        result, payload = self.cli_json('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 2)
        self.assertIn('claim_reference_missing', {e['issue'] for e in payload['errors']})

    def test_analysis_scaffold_unknown_contract_fields_fail_validation_structurally(self):
        result = self.cli("scaffold", "--run", self.run, "--kind", "analysis", "--system", "sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.run / "analysis/sample/analysis.json"
        doc = read_json(path)
        doc["draft"] = False
        write_json(path, doc)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "analysis", "--system", "sample")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["status"], "pass")

        doc = read_json(path)
        doc["coverage"]["functions"] = [
            {"function_id": "invented-function", "status": "TRACED", "remaining": []}
        ]
        doc["unknowns"] = [{"id": "U1", "question": "A bounded question?"}]
        write_json(path, doc)
        result, payload = self.cli_json(
            "validate-worker", "--run", self.run, "--kind", "analysis", "--system", "sample")
        self.assertEqual(result.returncode, 2)
        issues = {error["issue"] for error in payload["errors"]}
        self.assertIn("function_outside_scope", issues)
        self.assertIn("function_unaccounted", issues)
        self.assertIn("text_required", issues)


if __name__ == "__main__":
    unittest.main()
