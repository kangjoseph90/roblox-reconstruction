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
from agent_io import normalize_worker, query, summarize
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
