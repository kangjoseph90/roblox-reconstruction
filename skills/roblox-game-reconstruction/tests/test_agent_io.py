"""Agent-facing helpers: bounded results, registry queries and citation shorthand."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_io import cfg_records, cite, normalize_worker, query, summarize
from inventory import certified_match, save_records
from worker_io import validate_worker
from test_semantic import read_json, write_json
import test_workflow

SCRIPT = ROOT / 'scripts/reconstruct.py'


class AgentIOTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = test_workflow.WorkflowTests().make_run(self.root)
        self.path = self.run / 'analysis/sample/analysis.json'
        self.canonical = read_json(self.path)

    def shorthand(self):
        doc = copy.deepcopy(self.canonical)
        count = 0
        for fn in doc['coverage']['functions']:
            for step in (fn.get('trace') or {}).get('steps', []):
                step['evidence'] = [{'lines': [ev['line_start'], ev['line_end']]} for ev in step['evidence']]
                count += len(step['evidence'])
        self.assertGreater(count, 0)
        write_json(self.path, doc)
        return count

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)

    def test_unnormalized_shorthand_is_rejected_with_a_hint(self):
        self.shorthand()
        result = self.cli('validate-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 2)
        issues = {e['issue'] for e in json.loads(result.stdout)['errors']}
        self.assertIn('citation_shorthand_not_normalized', issues)

    def test_normalize_restores_the_canonical_document_and_validates(self):
        count = self.shorthand()
        result = self.cli('normalize-worker', '--run', self.run, '--kind', 'analysis', '--system', 'sample')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload['status'], payload['expanded'], payload['written']), ('pass', count, True))
        self.assertEqual(read_json(self.path), self.canonical)
        # Already canonical: nothing to expand, nothing written.
        again = normalize_worker(self.run, 'analysis', self.path, 'sample')
        self.assertEqual((again['expanded'], again['written']), (0, False))

    def test_source_review_shorthand_normalizes_to_the_frozen_form(self):
        path = self.run / 'source-review-input.json'
        canonical = read_json(path)
        doc = copy.deepcopy(canonical)
        for fn in doc['functions']:
            for step in (fn.get('trace') or {}).get('steps', []):
                step['evidence'] = [{'lines': [ev['line_start'], ev['line_end']]} for ev in step['evidence']]
        write_json(path, doc)
        result = normalize_worker(self.run, 'source-review', path)
        self.assertEqual(result['status'], 'pass', result.get('errors'))
        self.assertGreater(result['expanded'], 0)
        self.assertEqual(read_json(path), canonical)

    def test_malformed_shorthand_writes_nothing(self):
        self.shorthand()
        doc = read_json(self.path)
        step = next(fn for fn in doc['coverage']['functions'] if fn.get('trace'))['trace']['steps'][0]
        step['evidence'][0]['function_id'] = 'someone-else'
        write_json(self.path, doc)
        before = self.path.read_bytes()
        result = normalize_worker(self.run, 'analysis', self.path, 'sample')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual({e['issue'] for e in result['errors']}, {'citation_shorthand_invalid'})
        self.assertEqual(self.path.read_bytes(), before)

    def test_summary_bounds_lists_but_keeps_counts(self):
        result = {'status': 'failed', 'errors': [{'issue': 'a'}] * 25 + [{'issue': 'b'}] * 5, 'warnings': [{'issue': 'w'}]}
        out = summarize(result, '/run/audit.json')
        self.assertEqual(len(out['errors']), 20)
        self.assertEqual(out['truncated']['errors'], {'total': 30, 'shown': 20, 'by_issue': {'a': 25, 'b': 5}})
        self.assertEqual(out['warnings'], result['warnings'])
        self.assertEqual(out['full_result'], '/run/audit.json')
        self.assertEqual(summarize({'status': 'pass', 'errors': []}), {'status': 'pass', 'errors': []})

    def test_cite_produces_citations_the_validator_accepts(self):
        module = read_json(self.run / 'shared/modules.json')['modules'][0]
        functions = read_json(self.run / 'shared/functions.json')['functions']
        line = functions[0]['source_line_start']
        citation = cite(self.run, '%s:%d' % (module['original_source'], line))
        self.assertIsNotNone(citation['function_id'])
        doc = copy.deepcopy(self.canonical)
        doc['claims'][0]['evidence'] = [citation]
        self.assertEqual(validate_worker(self.run, 'analysis', doc, 'sample')['errors'], [])
        with self.assertRaisesRegex(ValueError, 'outside'):
            cite(self.run, '%s:%d' % (module['original_source'], 10 ** 6))
        with self.assertRaisesRegex(ValueError, 'not in the registry'):
            cite(self.run, 'src/missing.luau:1')

    def test_unmapped_citation_needs_an_authored_limit(self):
        module = read_json(self.run / 'shared/modules.json')['modules'][0]
        bodies = read_json(self.run / 'shared/functions.json')['functions']
        covered = {n for f in bodies for n in range(f['source_line_start'], f['source_line_end'] + 1)}
        free = next((n for n in range(1, module['source_line_count'] + 1) if n not in covered), None)
        if free is None:
            self.skipTest('fixture has no line outside every function body')
        citation = cite(self.run, '%s:%d' % (module['original_source'], free))
        self.assertEqual((citation['function_id'], citation['mapping_limit']), (None, ''))
        doc = copy.deepcopy(self.canonical)
        doc['claims'][0]['evidence'] = [citation]
        issues = {e['issue'] for e in validate_worker(self.run, 'analysis', doc, 'sample')['errors']}
        self.assertIn('unmapped_evidence_without_limit', issues)
        citation['mapping_limit'] = 'Module-level statement outside every registered body.'
        self.assertNotIn('unmapped_evidence_without_limit', {e['issue'] for e in validate_worker(self.run, 'analysis', doc, 'sample')['errors']})

    def test_unowned_lists_modules_the_catalog_does_not_account_for(self):
        module = read_json(self.run / 'shared/modules.json')['modules'][0]
        self.assertEqual(query(self.run, 'modules', unowned=True).splitlines()[1:], [])
        catalog = read_json(self.run / 'catalog.json')
        catalog['systems'][0]['module_ids'] = []
        write_json(self.run / 'catalog.json', catalog)
        rows = query(self.run, 'modules', unowned=True).splitlines()[1:]
        self.assertEqual([r.split('\t')[0] for r in rows], [module['module_id']])

    def test_query_cfg_returns_the_full_comparison_records(self):
        functions = read_json(self.run / 'shared/functions.json')['functions']
        certified = next(f for f in functions if f['cfg_status'] == 'CFG_VALIDATED')
        result = cfg_records(self.run, certified['function_id'])
        self.assertTrue(result['comparison'].endswith('comparison-O1.json'))
        self.assertEqual([r['proto'] for r in result['certified']],
                         [m['proto'] for m in certified['certified_original_body_matches']])
        self.assertTrue(all(r['status'] == 'CFG_VALIDATED' for r in result['certified']))
        with self.assertRaisesRegex(ValueError, 'unknown function_id'):
            cfg_records(self.run, 'missing:O1:r0')

    def test_certificate_scope_text_is_shared_unless_it_differs(self):
        scopes = {}
        first = certified_match({'proto': 1, 'name': 'a', 'certificate': 'exact', 'scope': 's', 'correspondence': 'c'}, scopes)
        same = certified_match({'proto': 2, 'name': 'b', 'certificate': 'exact', 'scope': 's', 'correspondence': 'c'}, scopes)
        other = certified_match({'proto': 3, 'name': 'c', 'certificate': 'exact', 'scope': 'different', 'correspondence': 'c'}, scopes)
        self.assertEqual(scopes, {'exact': {'scope': 's', 'correspondence': 'c'}})
        self.assertEqual((first, same), ({'proto': 1, 'name': 'a', 'certificate': 'exact'}, {'proto': 2, 'name': 'b', 'certificate': 'exact'}))
        self.assertEqual(other, {'proto': 3, 'name': 'c', 'certificate': 'exact', 'scope': 'different'})

    def test_record_lines_round_trip(self):
        value = {'schema': 'x/v1', 'items': [{'a': 1, 'b': [1, 2]}, {'a': float('nan')}], 'empty': [], 'note': 'ä'}
        path = self.root / 'records.json'
        save_records(path, value)
        text = path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(text), {'schema': 'x/v1', 'items': [{'a': 1, 'b': [1, 2]}, {'a': 'nan'}], 'empty': [], 'note': 'ä'})
        self.assertIn('\n{"a":1,"b":[1,2]},\n', text)  # one record per line
        save_records(path, [{'k': 1}, {'k': 2}])
        self.assertEqual(path.read_text(encoding='utf-8').splitlines(), ['[', '{"k":1},', '{"k":2}', ']'])

    def test_remote_interface_fields_are_validated_where_documented(self):
        doc = copy.deepcopy(self.canonical)
        claim = doc['claims'][0]['id']
        remote = {'id': 'remote', 'direction': 'client-to-server', 'inputs': [], 'outputs': [], 'state_owner': 'server',
                  'claim_ids': [claim], 'endpoint': {'name': 'BuyEvent', 'parent': 'ReplicatedStorage.Remotes'},
                  'positional_args': ['item id'], 'result': 'none observed', 'authority_limit': 'client request only', 'unknowns': []}
        doc['interfaces'] = [remote]
        self.assertEqual(validate_worker(self.run, 'analysis', doc, 'sample')['errors'], [])
        # The field-test case: payload fields nested inside endpoint instead of beside it.
        misplaced = dict(remote, endpoint=dict(remote['endpoint'], **{k: remote[k] for k in ('positional_args', 'result', 'authority_limit', 'unknowns')}))
        for k in ('positional_args', 'result', 'authority_limit', 'unknowns'):
            del misplaced[k]
        doc['interfaces'] = [misplaced]
        errors = validate_worker(self.run, 'analysis', doc, 'sample')['errors']
        self.assertEqual({(e['issue'], e['location'].rsplit('.', 1)[1]) for e in errors},
                         {('remote_field_misplaced', k) for k in ('positional_args', 'result', 'authority_limit', 'unknowns')})
        doc['interfaces'] = [{k: v for k, v in remote.items() if k not in ('result', 'claim_ids', 'state_owner')}]
        issues = {e['issue'] for e in validate_worker(self.run, 'analysis', doc, 'sample')['errors']}
        self.assertEqual(issues, {'remote_field_required', 'interface_support_required', 'interface_field_required'})

    def test_cited_but_unanalyzed_functions_are_flagged_not_promoted(self):
        doc = copy.deepcopy(self.canonical)
        cited = doc['claims'][0]['evidence'][0]['function_id']
        entry = next(f for f in doc['coverage']['functions'] if f['function_id'] == cited)
        entry.clear()
        entry.update(function_id=cited, status='UNANALYZED', remaining=['not analysed'])
        result = validate_worker(self.run, 'analysis', doc, 'sample')
        warning = next(w for w in result['warnings'] if w['issue'] == 'cited_function_unanalyzed')
        self.assertEqual(warning['function_ids'], [cited])
        self.assertEqual(entry['status'], 'UNANALYZED')  # reported, never changed
        self.assertNotIn('cited_function_unanalyzed', {w['issue'] for w in validate_worker(self.run, 'analysis', self.canonical, 'sample')['warnings']})

    def test_query_filters_registry_rows(self):
        module = read_json(self.run / 'shared/modules.json')['modules'][0]
        functions = read_json(self.run / 'shared/functions.json')['functions']
        rows = query(self.run, 'modules', system='sample').splitlines()
        self.assertEqual(rows[0].split('\t')[0], 'module_id')
        self.assertEqual([r.split('\t')[0] for r in rows[1:]], [module['module_id']])
        line = functions[0]['source_line_start']
        found = query(self.run, 'functions', line='%s:%d' % (module['original_source'], line)).splitlines()[1:]
        spans = [int(r.split('\t')[4]) - int(r.split('\t')[3]) for r in found]
        self.assertTrue(found)
        self.assertEqual(spans, sorted(spans))  # innermost body first
        limited = query(self.run, 'functions', system='sample', limit=1).splitlines()
        self.assertEqual(len(limited), 3 if len(functions) > 1 else 2)
        with self.assertRaisesRegex(ValueError, 'one catalog entry'):
            query(self.run, 'functions', system='missing')


if __name__ == '__main__':
    unittest.main()
