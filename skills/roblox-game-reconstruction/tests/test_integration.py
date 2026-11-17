"""Integration controls use authored synthetic inputs, never game answers."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from integration import check_plan, integrate, scaffold_integration
from test_semantic import read_json, write_json
import test_workflow


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = test_workflow.WorkflowTests().make_run(self.root)
        self.old = read_json(self.run/'semantic-coverage.json')

    def decisions(self):
        scaffold_integration(self.run)
        path = self.run/'integration-decisions.json'
        doc = read_json(path)
        doc['draft'] = False
        doc['scope']['rationale'] = 'Bounded source fixture with explicit dispositions.'
        doc['mappings'] = deepcopy(self.old['mappings'])
        doc['unknown_audits'] = deepcopy(self.old['unknown_audits'])
        write_json(path, doc)
        return path, doc

    def test_plan_checks_unowned_duplicate_and_unknown_modules_and_extra_assignments(self):
        self.assertEqual(check_plan(self.run)['status'], 'pass')
        catalog = read_json(self.run/'catalog.json')
        catalog['systems'][0]['module_ids'] *= 2
        catalog['systems'][0]['module_ids'].append('not-a-module')
        write_json(self.run/'catalog.json', catalog)
        plan = read_json(self.run/'analysis-plan.json')
        plan['assignments'].append({'system_id':'not-a-system'})
        write_json(self.run/'analysis-plan.json', plan)
        issues = {e['issue'] for e in check_plan(self.run)['errors']}
        self.assertTrue({'module_ownership','unknown_inventory_module_id','system_plan_coverage'} <= issues)
        catalog['systems'][0]['module_ids'] = []
        write_json(self.run/'catalog.json', catalog)
        self.assertIn('module_ownership', {e['issue'] for e in check_plan(self.run)['errors']})

    def test_scaffold_marks_semantic_decisions_unfinished_and_refuses_overwrite(self):
        result = scaffold_integration(self.run)
        self.assertEqual(result['status'], 'draft_created')
        doc = read_json(self.run/'integration-decisions.json')
        self.assertTrue(doc['draft'])
        self.assertTrue(all(m['status'] == 'MISSING' for m in doc['mappings']))
        self.assertTrue(all(a['status'] == 'NOT_INVESTIGATED' for a in doc['unknown_audits']))
        self.assertEqual(doc['reviewed_function_ids'], [])
        self.assertEqual(doc['relations'][0]['from'], 'review-a:O1')
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'): scaffold_integration(self.run)

    def test_integrates_reciprocal_links_without_promotion_or_source_changes(self):
        path, _ = self.decisions()
        frozen = self.run/'source-reviews/review-a/source-first.json'
        before = frozen.read_bytes()
        analysis_before = read_json(self.run/'analysis/sample/analysis.json')
        repo = Path(read_json(self.run/'config.json')['repo'])
        source = repo/'src/module/init.luau'
        source_before = source.read_bytes()
        result = integrate(self.run, path)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['analysis_status'], 'PARTIAL')
        self.assertEqual(result['completion']['functions_reviewed'], 0)
        self.assertEqual(result['completion']['functions_trace_supported'], 1)
        self.assertEqual(read_json(self.run/'semantic-coverage.json')['functions'][0]['status'], 'TRACED')
        analysis_after = read_json(self.run/'analysis/sample/analysis.json')
        for analysis in (analysis_before, analysis_after):
            for rule in analysis['claims'] + analysis['contracts']: rule.pop('semantic_refs', None)
        self.assertEqual(analysis_before, analysis_after)
        self.assertEqual(frozen.read_bytes(), before)
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(integrate(self.run, path)['status'], 'pass')

    def test_failed_adjudication_writes_neither_ledger_nor_worker_metadata(self):
        path, doc = self.decisions()
        before = {p: p.read_bytes() for p in (self.run/'semantic-coverage.json', self.run/'analysis/sample/analysis.json')}
        doc['mappings'][0]['rule_ids'] = ['sample:not-a-rule']
        write_json(path, doc)
        result = integrate(self.run, path)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['writes'], 0)
        for p, raw in before.items(): self.assertEqual(p.read_bytes(), raw)

    def test_review_promotion_is_explicit_and_needs_both_traces(self):
        path, doc = self.decisions()
        fid = self.old['functions'][0]['function_id']
        doc['reviewed_function_ids'] = [fid]
        write_json(path, doc)
        result = integrate(self.run, path)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['completion']['functions_reviewed'], 1)
        # Claiming a merely assigned function was reviewed cannot manufacture a trace.
        doc['reviewed_function_ids'] = [self.old['functions'][1]['function_id']]
        write_json(path, doc)
        with self.assertRaisesRegex(ValueError, 'lacks author trace'): integrate(self.run, path)

    def test_tampered_frozen_review_blocks_integration(self):
        path, _ = self.decisions()
        frozen = self.run/'source-reviews/review-a/source-first.json'
        frozen.write_bytes(frozen.read_bytes() + b' ')
        before = (self.run/'semantic-coverage.json').read_bytes()
        result = integrate(self.run, path)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('semantic_review_pin_mismatch', {e['issue'] for e in result['errors']})
        self.assertEqual((self.run/'semantic-coverage.json').read_bytes(), before)

    def test_shared_payload_requires_explicit_function_owner_and_namespaced_rule_ids(self):
        # Two different instances can share bytecode/functions but have distinct system owners.
        # Re-prepare an input with that alias; never edit a pinned shared registry.
        import reconstruct
        config = read_json(self.run/'config.json')
        repo = Path(config['repo'])
        report = read_json(repo/'report.json')
        alias = deepcopy(report['scripts'][0])
        alias.update(id='alias-instance', instance_path=['Root','Alias'])
        report['scripts'].append(alias)
        write_json(repo/'report.json', report)
        other_run = self.root/'alias-run'
        reconstruct.prepare(repo, other_run)
        modules = read_json(other_run/'shared/modules.json')['modules']
        source_analysis = read_json(self.run/'analysis/sample/analysis.json')
        systems = []
        for sid, module in zip(('one','two'), modules):
            systems.append({'id': sid, 'module_ids': [module['module_id']]})
            analysis = deepcopy(source_analysis)
            analysis['system_id'] = sid
            analysis['coverage']['module_ids'] = [module['module_id']]
            write_json(other_run/'analysis'/sid/'analysis.json', analysis)
        write_json(other_run/'catalog.json', {'systems': systems, 'exclusions': []})
        write_json(other_run/'analysis-plan.json', {'assignments':[{'system_id':s['id']} for s in systems]})
        from semantic import freeze_review
        review = read_json(self.run/'source-review-input.json')
        review['scope']['module_ids'] = [modules[0]['module_id']]
        write_json(other_run/'review-input.json', review)
        freeze_review(other_run, 'review-a', other_run/'review-input.json')
        scaffold_integration(other_run)
        path = other_run/'integration-decisions.json'
        doc = read_json(path)
        doc.update(draft=False)
        doc['scope']['rationale'] = 'Two aliases, one explicit primary function owner.'
        doc['unknown_audits'] = [dict(self.old['unknown_audits'][0], system_id=sid) for sid in ('one','two')]
        for m in doc['mappings']: m.update(status='REPRESENTED', rule_ids=['one:returns','two:returns'], rationale='Explicit fixture comparison.')
        write_json(path, doc)
        with self.assertRaisesRegex(ValueError, 'invalid function owner'): integrate(other_run, path)
        doc['function_owners'] = {fid: 'one' for fid in doc['function_owners']}
        write_json(path, doc)
        self.assertEqual(integrate(other_run, path)['status'], 'pass')
        for sid in ('one','two'):
            actual = read_json(other_run/'analysis'/sid/'analysis.json')
            self.assertEqual(len(actual['claims'][0]['semantic_refs']), 3)

    def test_cli_plan_and_integration_exit_codes(self):
        script = Path(__file__).resolve().parents[1]/'scripts/reconstruct.py'
        path, doc = self.decisions()
        for command in (['check-plan','--run',str(self.run)], ['integrate','--run',str(self.run),'--input',str(path)]):
            result = subprocess.run([sys.executable,str(script),*command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'pass')
        doc['mappings'][0].update(status='MISSING')
        write_json(path, doc)
        result = subprocess.run([sys.executable,str(script),'integrate','--run',str(self.run),'--input',str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['writes'], 0)

    def test_malformed_decisions_are_structured_cli_errors_without_writes(self):
        script = Path(__file__).resolve().parents[1]/'scripts/reconstruct.py'
        path, doc = self.decisions()
        before = (self.run/'semantic-coverage.json').read_bytes()
        doc['function_owners'] = None
        write_json(path, doc)
        result = subprocess.run([sys.executable,str(script),'integrate','--run',str(self.run),'--input',str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)['status'], 'error')
        self.assertEqual((self.run/'semantic-coverage.json').read_bytes(), before)

    def test_unknown_ref_string_is_a_structured_failed_check_without_writes(self):
        path, doc = self.decisions()
        doc['mappings'][0].update(status='UNKNOWN', unknown_ref='sample:outside', rule_ids=[])
        write_json(path, doc)
        before = (self.run/'semantic-coverage.json').read_bytes()
        result = integrate(self.run, path)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['writes'], 0)
        self.assertIn('semantic_unknown_ref_invalid', {e['issue'] for e in result['errors']})
        self.assertEqual((self.run/'semantic-coverage.json').read_bytes(), before)

    def test_integrated_package_preserves_decisions_and_completion_metrics(self):
        import reconstruct
        path, _ = self.decisions()
        self.assertEqual(integrate(self.run, path)['status'], 'pass')
        result = reconstruct.package(self.run, self.root/'package')
        self.assertEqual(result['status'], 'pass')
        self.assertEqual((self.root/'package/integration-decisions.json').read_bytes(), path.read_bytes())
        self.assertEqual(result['completion']['functions_trace_supported'], 1)
        self.assertEqual(result['completion']['source_review_functions_traced'], 1)
        self.assertEqual(result['completion']['functions_unanalyzed'], 2)
        self.assertIn('supported function traces: 1', (self.root/'package/index.html').read_text(encoding='utf-8'))
        self.assertEqual(reconstruct.validate_package(self.root/'package')['status'], 'pass')


if __name__ == '__main__': unittest.main()
