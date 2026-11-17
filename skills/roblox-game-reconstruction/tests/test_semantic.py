"""Regression controls for source-first semantic accounting."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reconstruct
from inventory import sha
from semantic import audit_semantics, freeze_review
from test_inventory import repo_fixture


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def trace_fixture(ev):
    return {'inputs': [], 'steps': [{'action': 'Return the literal or invoke the local helper in this fixture body.',
                                    'evidence': [dict(ev)]}],
            'outputs': ['The literal return value.'], 'limits': ['Synthetic source slice only; no behavior certificate.']}


def make_semantic_ledger(run, module, functions, source, *, unknown_status="NOT_FOUND_IN_SEARCHED_SCOPE",
                         claim="PARTIAL", include_content=False, unresolved=False):
    """Pin a generic source-only inventory, then integrate it into a partial ledger."""
    function = functions[0]
    ev = {"source_file": source, "line_start": function["source_line_start"],
          "line_end": function["source_line_start"], "function_id": function["function_id"]}
    review = {
        "schema": "source-review/v1", "draft": False, "reviewer": "fixture-reviewer", "specification_seen": False,
        "scope": {"module_ids": [module["module_id"]], "entrypoints": [], "limits": "bounded fixture slice"},
        "functions": [{"function_id": fn["function_id"], "status": "TRACED" if fn == function else "UNANALYZED",
                       "observation_ids": ["O1", "O2"] if fn == function else [],
                       "relation_ids": ["E1"] if fn == function else [], "remaining": []} for fn in functions],
        "observations": [
            {"id": "O1", "category": "state", "importance": "material", "statement": "A generic value is read.", "evidence": [ev]},
            {"id": "O2", "category": "effect", "importance": "material", "statement": "A generic effect follows the read.", "evidence": [ev]},
        ],
        "relations": [{"id": "E1", "from": "O1", "to": "O2", "kind": "read_controls_effect",
                       "importance": "material", "statement": "The read controls the effect.",
                       "trace_function_ids": [function["function_id"]], "evidence": [ev]}],
        "unknown_candidates": [], "cost": {"files_read": 1, "functions_traced": 1, "searches": 1,
                                               "elapsed_seconds": 0}, "limitations": [],
    }
    if unresolved:
        review['relations'][0].update(trace_function_ids=[], trace_status='UNRESOLVED', trace_limit='Follow-up caller attribution required.')
    review['functions'][0]['trace'] = trace_fixture(ev)
    source_path = Path(run) / "source-review-input.json"
    write_json(source_path, review)
    freeze_review(run, "review-a", source_path)

    obs1, obs2, rel = "review-a:O1", "review-a:O2", "review-a:E1"
    funcs = []
    for fn in functions:
        if fn["function_id"] == function["function_id"]:
            funcs.append({"function_id": fn["function_id"], "status": "TRACED",
                          "observation_ids": [obs1, obs2], "relation_ids": [rel], "remaining": []})
        else:
            funcs.append({"function_id": fn["function_id"], "status": "UNANALYZED",
                          "observation_ids": [], "relation_ids": [], "remaining": ["outside traced fixture slice"]})
    bindings = []
    if include_content:
        bindings = [{"system_id": "sample", "content_id": "entry", "status": "PARTIAL",
                     "relation_ids": [rel], "rationale": "The fixture leaves this content mapping open."}]
    unknown_audit = {"system_id": "sample", "unknown_id": "outside", "status": unknown_status,
                     "question": "Which behavior exists beyond the bounded source slice?",
                     "searched_sources": [source], "queries": ["bounded source lookup"],
                     "inspected_function_ids": [function["function_id"]], "evidence": [ev],
                     "resolved_rule_ids": [], "remaining_question": "", "rationale": "The search is bounded."}
    if unknown_status == "NOT_INVESTIGATED":
        unknown_audit["searched_sources"] = []
        unknown_audit["queries"] = []
        unknown_audit["inspected_function_ids"] = []
        unknown_audit["evidence"] = []

    ledger = {
        "schema": "rgr-semantic-coverage/v1",
        "scope": {"module_ids": [module["module_id"]], "rationale": "Generic bounded fixture scope.", "declared_full_game": False},
        "functions": funcs,
        "observations": [
            {"id": obs1, "category": "state", "importance": "material", "statement": "A generic value is read.", "evidence": [ev], "origin": {"review_id": "review-a", "item_id": "O1"}},
            {"id": obs2, "category": "effect", "importance": "material", "statement": "A generic effect follows the read.", "evidence": [ev], "origin": {"review_id": "review-a", "item_id": "O2"}},
        ],
        "relations": [{"id": rel, "from": obs1, "to": obs2, "kind": "read_controls_effect",
                       "importance": "material", "statement": "The read controls the effect.", "evidence": [ev],
                       "trace_function_ids": [function["function_id"]], "trace_status": "TRACED",
                       "origin": {"review_id": "review-a", "item_id": "E1"}}],
        "mappings": [{"subject_id": ident, "status": "REPRESENTED", "rule_ids": ["sample:returns"],
                      "rationale": "A generic rule carries this obligation."} for ident in (obs1, obs2, rel)],
        "source_reviews": [{"review_id": "review-a"}],
        "unknown_audits": [unknown_audit], "data_bindings": bindings,
        "completeness_claim": claim,
    }
    ledger['functions'][0]['trace'] = trace_fixture(ev)
    if unresolved:
        ledger['relations'][0].update(trace_function_ids=[], trace_status='UNRESOLVED')
    write_json(Path(run) / "semantic-coverage.json", ledger)
    rule = {"id": "sample:returns", "detail": {"semantic_refs": [obs1, obs2, rel]}}
    return ledger, [rule], ev, review


class SemanticAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = repo_fixture(root, cfg=True)
        self.run = root / "run"
        reconstruct.prepare(self.repo, self.run)
        self.module = read_json(self.run / "shared/modules.json")["modules"][0]
        self.functions = read_json(self.run / "shared/functions.json")["functions"]
        self.source = self.module["original_source"]
        analysis = {"system_id": "sample", "unknowns": [{"id": "outside", "question": "Which behavior exists beyond the bounded source slice?"}],
                    "content": []}
        self.analyses = {"sample": analysis}
        self.ledger, self.rules, self.ev, self.review = make_semantic_ledger(
            self.run, self.module, self.functions, self.source)
        self.config = read_json(self.run / "config.json")
        self.modules = [self.module]

    def audit(self):
        return audit_semantics(self.run, self.config, self.modules, self.functions, self.rules, self.analyses)

    def mutate(self, change):
        ledger = read_json(self.run / "semantic-coverage.json")
        change(ledger)
        write_json(self.run / "semantic-coverage.json", ledger)

    def issues(self):
        return {e["issue"] for e in self.audit()["errors"]}

    def test_valid_reciprocal_observed_relationship_passes_as_partial(self):
        result = self.audit()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["summary"]["function_states"]["UNANALYZED"], len(self.functions) - 1)
        self.assertEqual(result['completion']['functions_trace_supported'], 1)
        self.assertEqual(result['completion']['functions_trace_unsupported'], 0)
        self.assertEqual(result['completion']['source_review_functions_traced'], 1)

    def test_bulk_rationale_is_not_supported_function_analysis(self):
        self.mutate(lambda d: (d['functions'][0].pop('trace'), d['functions'][0].update(rationale='Traced within the domain analysis.')))
        result = self.audit()
        self.assertIn('function_trace_required', {e['issue'] for e in result['errors']})
        self.assertEqual(result['completion']['functions_trace_supported'], 0)
        self.assertEqual(result['completion']['functions_trace_unsupported'], 1)

    def test_reviewed_requires_an_author_trace_as_well_as_a_frozen_trace(self):
        self.mutate(lambda d: d['functions'][0].update(status='REVIEWED'))
        self.assertIn('semantic_function_author_trace_missing', self.issues())
        self.assertEqual(self.audit()['completion']['functions_declared_reviewed'], 1)
        self.assertEqual(self.audit()['completion']['functions_reviewed'], 0)
        self.analyses['sample']['coverage'] = {'functions': [self.ledger['functions'][0]]}
        self.assertEqual(self.audit()['errors'], [])
        self.assertEqual(self.audit()['completion']['functions_reviewed'], 1)
        self.analyses['sample']['coverage']['functions'][0]['trace']['steps'][0]['evidence'][0]['function_id'] = None
        self.assertIn('function_trace_wrong_body', self.issues())

    def test_material_mapping_cannot_be_left_unreviewed(self):
        self.mutate(lambda d: d["mappings"][0].update(status="MISSING", rule_ids=[]))
        self.assertIn("semantic_material_obligation_open", self.issues())

    def test_material_exclusion_requires_declared_scope_and_keeps_result_partial(self):
        subject_id = self.ledger["mappings"][0]["subject_id"]
        self.mutate(lambda d: (d["mappings"][0].update(status="EXCLUDED", rule_ids=[],
                                                       impact="This generic obligation remains outside the bounded slice."),
                             self.rules[0]["detail"]["semantic_refs"].remove(subject_id)))
        self.assertIn("semantic_material_exclusion_not_declared", self.issues())
        self.mutate(lambda d: d["scope"].update(excluded_subject_ids=[subject_id]))
        result = self.audit()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["status"], "PARTIAL")

    def test_frozen_relationship_cannot_be_dropped(self):
        self.mutate(lambda d: d["relations"].clear())
        self.assertIn("semantic_source_obligation_dropped", self.issues())

    def test_frozen_relationship_cannot_be_rebound(self):
        self.mutate(lambda d: d["relations"][0].update(**{"from": "review-a:O2"}))
        self.assertIn("semantic_source_relation_rebound", self.issues())

    def test_wrong_reciprocal_rule_link_is_rejected(self):
        self.rules[0]["detail"]["semantic_refs"].remove("review-a:E1")
        self.assertIn("semantic_mapping_not_reciprocal", self.issues())

    def test_false_unknown_with_source_evidence_retained_is_rejected(self):
        self.mutate(lambda d: d["unknown_audits"][0].update(status="SOURCE_FOUND", resolved_rule_ids=["sample:returns"]))
        self.rules[0]["detail"]["semantic_refs"].append("review-a:O1")
        retained = next(e for e in self.audit()["errors"] if e["issue"] == "semantic_blanket_unknown_retained")
        self.assertIn("RESOLVED", retained["hint"])
        self.assertEqual(retained["audit_question"], "Which behavior exists beyond the bounded source slice?")

    def test_unknown_cannot_be_resolved_without_source_found(self):
        self.analyses["sample"]["unknowns"][0]["status"] = "RESOLVED"
        for status in ("UNAVAILABLE_SOURCE", "NOT_FOUND_IN_SEARCHED_SCOPE", "PARTIALLY_SUPPORTED"):
            with self.subTest(status=status):
                self.mutate(lambda d: d["unknown_audits"][0].update(status=status, resolved_rule_ids=["sample:returns"]))
                result = self.audit()
                self.assertIn("semantic_unknown_resolved_without_source", {e["issue"] for e in result["errors"]})
                self.assertEqual(result["status"], "FAILED")
        self.mutate(lambda d: d["unknown_audits"][0].update(status="SOURCE_FOUND", resolved_rule_ids=["sample:returns"]))
        self.assertNotIn("semantic_unknown_resolved_without_source", self.issues())

    def test_unknown_without_investigation_is_an_open_gap(self):
        self.mutate(lambda d: d["unknown_audits"][0].update(status="NOT_INVESTIGATED", searched_sources=[], queries=[], inspected_function_ids=[]))
        result = self.audit()
        self.assertIn("unknown_not_investigated", {e["issue"] for e in result["remaining"]})

    def test_scope_narrower_than_catalog_ownership_stays_partial(self):
        other = {"module_id": "other-module", "bytecode_sha256": "0" * 64, "original_source": "src/other.luau"}
        self.modules.append(other)
        catalog = {"systems": [{"id": "sample", "module_ids": [self.module["module_id"]]},
                               {"id": "other", "module_ids": [other["module_id"]]}], "exclusions": []}
        write_json(self.run / "catalog.json", catalog)
        result = self.audit()
        omitted = [g for g in result["remaining"] if g["issue"] == "scope_omits_owned_module"]
        self.assertEqual([g["module_id"] for g in omitted], [other["module_id"]])
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual((result["completion"]["scope_modules"], result["completion"]["owned_modules"]), (1, 2))
        # An explicit catalog exclusion leaves the module outside the completion denominator.
        catalog["systems"].pop()
        catalog["exclusions"] = [{"module_id": other["module_id"], "reason": "vendor", "impact": "none"}]
        write_json(self.run / "catalog.json", catalog)
        result = self.audit()
        self.assertNotIn("scope_omits_owned_module", {g["issue"] for g in result["remaining"]})
        self.assertEqual((result["completion"]["scope_modules"], result["completion"]["owned_modules"]), (1, 1))

    def test_unaccounted_function_and_inflated_completion_are_rejected(self):
        self.mutate(lambda d: (d["functions"].pop(), d.update(completeness_claim="IN_SCOPE_REVIEWED")))
        issues = self.issues()
        self.assertIn("semantic_function_unaccounted", issues)
        self.assertIn("semantic_false_completion", issues)

    def test_material_content_without_binding_is_rejected(self):
        self.analyses["sample"]["content"] = [{"id": "entry", "value": 3}]
        self.assertIn("semantic_content_unaccounted", self.issues())

    def test_bound_content_cannot_use_an_unresolved_relation(self):
        self.analyses["sample"]["content"] = [{"id": "entry", "value": 3}]
        self.mutate(lambda d: (d["relations"][0].update(trace_status="UNRESOLVED"),
                             d.update(data_bindings=[{"system_id": "sample", "content_id": "entry",
                                 "status": "BOUND", "relation_ids": ["review-a:E1"],
                                 "rationale": "This generic item is bound through its observed relation."}])))
        self.assertIn("semantic_bound_content_trace_open", self.issues())

    def test_tampered_frozen_review_is_detected(self):
        path = self.run / "source-reviews/review-a/source-first.json"
        doc = read_json(path); doc["observations"][0]["statement"] = "changed"
        write_json(path, doc)
        self.assertIn("semantic_review_pin_mismatch", self.issues())

    def test_frozen_review_without_traces_is_rejected(self):
        # A pin whose traced function lacks a trace cannot come from freeze-review.
        folder = self.run/'source-reviews/review-a'
        review = read_json(folder/'source-first.json')
        review['functions'][0].pop('trace')
        write_json(folder/'source-first.json', review)
        manifest = read_json(folder/'manifest.json')
        manifest['source_sha256'] = sha((folder/'source-first.json').read_bytes())
        write_json(folder/'manifest.json', manifest)
        self.config['source_review_pins']['review-a'] = sha((folder/'manifest.json').read_bytes())
        result = self.audit()
        self.assertIn('semantic_source_review_trace_missing', {e['issue'] for e in result['errors']})
        self.assertEqual(result['status'], 'FAILED')

    def test_freeze_rejects_unsafe_id_and_double_freeze(self):
        with self.assertRaisesRegex(ValueError, "unsafe review ID"):
            freeze_review(self.run, "../bad", self.run / "source-review-input.json")
        with self.assertRaisesRegex(ValueError, "already frozen"):
            freeze_review(self.run, "review-a", self.run / "source-review-input.json")

    def test_freeze_rejects_specification_seen_source_review(self):
        review = read_json(self.run / "source-review-input.json")
        review["specification_seen"] = True
        bad_input = self.run / "answer-aware-review.json"
        write_json(bad_input, review)
        with self.assertRaisesRegex(ValueError, "source_review_not_blind"):
            freeze_review(self.run, "review-b", bad_input)

    def test_frozen_relation_trace_function_cannot_be_dropped(self):
        self.mutate(lambda d: d["relations"][0].update(trace_function_ids=[]))
        self.assertIn("semantic_source_obligation_changed", self.issues())

    def test_current_run_without_semantic_ledger_fails_closed(self):
        (self.run / "semantic-coverage.json").unlink()
        self.assertEqual(self.config["skill_version"], reconstruct.VERSION)
        result = self.audit()
        self.assertIn("semantic_coverage_missing", {e["issue"] for e in result["errors"]})
        self.assertEqual(result["status"], "FAILED")

    def install_fixture_with_frozen_attribution_typo(self):
        # Construct a signed input fixture whose original reviewer misspelled
        # one body ID. Real frozen artifacts are never rewritten to fix errors.
        folder = self.run / "source-reviews/review-a"
        original = read_json(folder / "source-first.json")
        original["observations"][0]["evidence"][0]["function_id"] = "misspelled-body-id"
        write_json(folder / "source-first.json", original)
        manifest = read_json(folder / "manifest.json")
        manifest["source_sha256"] = sha((folder / "source-first.json").read_bytes())
        write_json(folder / "manifest.json", manifest)
        self.config["source_review_pins"]["review-a"] = sha((folder / "manifest.json").read_bytes())
        def change(ledger):
            item = ledger["observations"][0]
            item["evidence"][0]["function_id"] = "misspelled-body-id"
            item["citation_erratum"] = {"rationale": "Fixture body-ID transcription correction; source span preserved.", "evidence": [self.ev]}
        self.mutate(change)

    def test_attribution_erratum_keeps_original_pinned_and_checks_corrected_body(self):
        self.install_fixture_with_frozen_attribution_typo()
        result = self.audit()
        self.assertEqual(result["errors"], [])
        self.assertIn("citation_attribution_corrected", {w["issue"] for w in result["warnings"]})
        frozen = read_json(self.run / "source-reviews/review-a/source-first.json")
        self.assertEqual(frozen["observations"][0]["evidence"][0]["function_id"], "misspelled-body-id")
        self.assertEqual(result["checked_evidence"][0]["function_id"], self.ev["function_id"])

    def test_attribution_erratum_cannot_move_source_span(self):
        self.install_fixture_with_frozen_attribution_typo()
        self.mutate(lambda d: d["observations"][0]["citation_erratum"]["evidence"][0].update(line_end=999))
        self.assertIn("semantic_citation_erratum_moves_source", self.issues())

    def test_attribution_erratum_cannot_invent_body_id(self):
        self.install_fixture_with_frozen_attribution_typo()
        self.mutate(lambda d: d["observations"][0]["citation_erratum"]["evidence"][0].update(function_id="invented"))
        self.assertIn("semantic_function_source_mismatch", self.issues())

    def test_classification_erratum_cannot_demote_material_obligation(self):
        self.mutate(lambda d: d["observations"][0].update(classification_erratum={"importance": "supporting", "rationale": "Attempt to demote a material finding."}))
        self.assertIn("semantic_classification_erratum_invalid", self.issues())

    def test_trace_id_erratum_needs_citation_support(self):
        self.mutate(lambda d: d["relations"][0].update(trace_id_erratum={"function_ids": ["invented"], "rationale": "Invalid attribution fixture."}))
        self.assertIn("semantic_trace_id_erratum_invalid", self.issues())

    def unresolved_fixture(self):
        root = Path(self.temp.name)/'followup'; root.mkdir()
        self.repo = repo_fixture(root, cfg=True); self.run = root/'run'
        reconstruct.prepare(self.repo, self.run)
        self.modules = read_json(self.run/'shared/modules.json')['modules']; self.module = self.modules[0]
        self.functions = read_json(self.run/'shared/functions.json')['functions']; self.source = self.module['original_source']
        self.ledger, self.rules, self.ev, self.review = make_semantic_ledger(
            self.run, self.module, self.functions, self.source, unresolved=True)
        self.config = read_json(self.run/'config.json')

    def resolve_trace(self):
        self.mutate(lambda d: d['relations'][0].update(trace_status='TRACED', trace_resolution={
            'rationale':'Follow-up source trace establishes the missing body reference.',
            'function_ids':[self.ev['function_id']], 'evidence':[self.ev]}))

    def test_unresolved_frozen_relation_accepts_evidenced_followup_without_rewriting_the_review(self):
        self.unresolved_fixture()
        frozen = self.run/'source-reviews/review-a/source-first.json'
        before = frozen.read_bytes()
        self.assertEqual(self.audit()['completion']['material_relations_untraced'], 1)
        self.resolve_trace()
        result = self.audit()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['completion']['material_relations_untraced'], 0)
        self.assertIn('trace_resolved_by_followup', {w['issue']for w in result['warnings']})
        self.assertEqual(frozen.read_bytes(), before)
        self.assertEqual(read_json(self.run/'semantic-coverage.json')['relations'][0]['trace_function_ids'], [])

    def test_followup_does_not_allow_frozen_trace_ids_or_statements_to_change(self):
        self.unresolved_fixture(); self.resolve_trace()
        self.mutate(lambda d: d['relations'][0].update(trace_function_ids=[self.ev['function_id']], statement='A changed semantic assertion.'))
        self.assertIn('semantic_source_obligation_changed', self.issues())

    def test_followup_requires_valid_cited_functions_and_source_evidence(self):
        self.unresolved_fixture(); self.resolve_trace()
        self.mutate(lambda d: d['relations'][0]['trace_resolution'].update(function_ids=['invented']))
        self.assertIn('semantic_trace_resolution_invalid', self.issues())

    def test_followup_of_an_already_traced_frozen_relation_is_rejected(self):
        self.resolve_trace()
        self.assertIn('semantic_trace_resolution_not_unresolved', self.issues())

    def test_malformed_trace_errata_report_errors_instead_of_crashing(self):
        for bad in (None, 'string', 42, [None], [{'invalid':'id'}]):
            with self.subTest(function_ids=bad):
                self.mutate(lambda d: d['relations'][0].update(trace_id_erratum={'function_ids':bad, 'rationale':'Malformed fixture'}))
                self.assertIn('semantic_trace_id_erratum_invalid', self.issues())


if __name__ == "__main__":
    unittest.main()
