"""Published CFG statuses must accept reports emitted by the recovery pipeline."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from pipeline import extract
from cfg_verify import ToolFailure


class ReportSchemaTests(unittest.TestCase):
    def test_emitted_cfg_outcomes_fit_the_published_schema(self):
        schema = json.loads((ROOT / 'references/report.schema.json').read_text(encoding='utf-8'))
        allowed = schema['properties']['scripts']['items']['properties']['verification']['properties']['cfg']['enum']
        for outcome in ('CFG_VALIDATED', 'UNVERIFIED', 'CFG_MISMATCH', 'audit_exception', 'tool_failure'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'input.json'
                source.write_text(json.dumps({'schema':'rcr-dump/v1', 'instances':[
                    {'id':'m', 'class':'ModuleScript', 'name':'Fixture', 'bytecode_format':'roblox',
                     'bytecode_base64':base64.b64encode(b'\x0e\x03authored interface fixture').decode()}]}), encoding='utf-8')
                tools = {'decompiler':{'argv':['decoder'], 'roblox_argv':['decoder'], 'cfg_audit':True,
                         'bytecode_versions':[14], 'type_versions':[3]}, 'compiler':{'argv':['compiler']}}
                audit = {'status':outcome, 'functions':[], 'original_proto_count':1, 'optimization':1}
                with patch('pipeline.tool_run', return_value=({'status':'ok','exit_code':0}, b'return 1\n')), \
                     patch('pipeline.compile_check', return_value={'status':'syntax_pass'}), \
                     patch('cfg_verify.audit_source', side_effect={'audit_exception': ValueError('unsupported fixture'), 'tool_failure': ToolFailure('unknown option')}.get(outcome), return_value=audit):
                    report = extract(source, root / 'out', tools)
                failed = outcome in ('audit_exception', 'tool_failure')
                self.assertEqual(report['scripts'][0]['verification']['cfg'], 'UNVERIFIED' if failed else outcome)
                if failed:
                    self.assertEqual(report['scripts'][0]['cfg_audit']['failure'],
                                     'tool_error' if outcome == 'tool_failure' else 'audit_error')
                self.assertIn(report['scripts'][0]['verification']['cfg'], allowed)


class SyntaxStateTests(unittest.TestCase):
    def test_compile_failure_and_missing_compiler_are_distinct_published_states(self):
        schema = json.loads((ROOT / 'references/report.schema.json').read_text(encoding='utf-8'))
        allowed = schema['properties']['scripts']['items']['properties']['verification']['properties']['syntax']['enum']
        expected = {'syntax_pass': 'SYNTAX_VALID', 'syntax_failed': 'SYNTAX_INVALID', 'not_configured': 'NOT_CHECKED'}
        for validation, state in expected.items():
            with self.subTest(validation=validation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'input.json'
                source.write_text(json.dumps({'schema': 'rcr-dump/v1', 'instances': [
                    {'id': 'm', 'class': 'ModuleScript', 'name': 'Fixture', 'bytecode_format': 'roblox',
                     'bytecode_base64': base64.b64encode(b'\x0e\x03authored interface fixture').decode()}]}), encoding='utf-8')
                tools = {'decompiler': {'argv': ['decoder'], 'roblox_argv': ['decoder'],
                         'bytecode_versions': [14], 'type_versions': [3]}, 'compiler': {'argv': ['compiler']}}
                with patch('pipeline.tool_run', return_value=({'status': 'ok', 'exit_code': 0}, b'return 1\n')), \
                     patch('pipeline.compile_check', return_value={'status': validation}):
                    report = extract(source, root / 'out', tools)
                self.assertEqual(report['scripts'][0]['verification']['syntax'], state)
                self.assertIn(state, allowed)

    def fixture(self, root):
        source = root / 'input.json'
        source.write_text(json.dumps({'schema': 'rcr-dump/v1', 'instances': [
            {'id': 'm', 'class': 'ModuleScript', 'name': 'Fixture', 'bytecode_format': 'roblox',
             'bytecode_base64': base64.b64encode(b'\x0e\x03authored interface fixture').decode()}]}), encoding='utf-8')
        return source

    def test_compiler_that_did_not_run_is_not_a_syntax_result(self):
        from pipeline import compile_check
        for result in ({'status': 'tool_error', 'error': 'timed out'}, {'status': 'tool_failed', 'exit_code': -9}):
            with self.subTest(result=result), patch('pipeline.tool_run', return_value=(dict(result), b'')):
                self.assertEqual(compile_check(Path('x.luau'), {'compiler': {'argv': ['c']}})['status'], 'tool_error')
        with patch('pipeline.tool_run', return_value=({'status': 'tool_failed', 'exit_code': 1}, b'')):
            self.assertEqual(compile_check(Path('x.luau'), {'compiler': {'argv': ['c']}})['status'], 'syntax_failed')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = {'decompiler': {'argv': ['decoder'], 'roblox_argv': ['decoder'], 'bytecode_versions': [14], 'type_versions': [3]},
                     'compiler': {'argv': ['compiler']}}
            with patch('pipeline.tool_run', return_value=({'status': 'ok', 'exit_code': 0}, b'return 1\n')), \
                 patch('pipeline.compile_check', return_value={'status': 'tool_error', 'error': 'timed out'}):
                report = extract(self.fixture(root), root / 'out', tools)
        script = report['scripts'][0]
        self.assertEqual((script['verification']['syntax'], script['recovery']), ('SYNTAX_UNVERIFIED', 'decompiled_candidate'))
        self.assertEqual(report['summary']['status'], 'partial')

    def test_failed_cfg_audit_makes_recovery_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = {'decompiler': {'argv': ['decoder'], 'roblox_argv': ['decoder'], 'cfg_audit': True,
                     'bytecode_versions': [14], 'type_versions': [3]}, 'compiler': {'argv': ['compiler']}}
            with patch('pipeline.tool_run', return_value=({'status': 'ok', 'exit_code': 0}, b'return 1\n')), \
                 patch('pipeline.compile_check', return_value={'status': 'syntax_pass'}), \
                 patch('cfg_verify.audit_source', side_effect=ToolFailure('missing disassembler')):
                report = extract(self.fixture(root), root / 'out', tools)
        self.assertEqual(report['summary']['status'], 'partial')

    def test_compiler_usage_errors_and_crashes_are_not_syntax_results(self):
        from pipeline import compile_check, PINNED_COMPILER
        pinned = {'compiler': {'argv': ['c'], 'revision': PINNED_COMPILER}}
        cases = [({'status': 'tool_failed', 'exit_code': 1, 'stderr': "Error: Unrecognized option '--bogus'."}, pinned, 'tool_error'),
                 ({'status': 'tool_failed', 'exit_code': 1, 'stderr': 'x.luau(1,7): SyntaxError: Expected identifier'}, pinned, 'syntax_failed'),
                 ({'status': 'tool_failed', 'exit_code': 0xC0000005, 'stderr': ''}, pinned, 'tool_error'),
                 ({'status': 'tool_failed', 'exit_code': 0x80000003, 'stderr': ''}, {'compiler': {'argv': ['c']}}, 'tool_error'),
                 ({'status': 'tool_failed', 'exit_code': 0x40000015, 'stderr': ''}, {'compiler': {'argv': ['c']}}, 'tool_error'),
                 ({'status': 'tool_failed', 'exit_code': 2, 'stderr': 'custom parser error'}, {'compiler': {'argv': ['c']}}, 'syntax_failed')]
        for result, tools, expected in cases:
            with self.subTest(result=result), patch('pipeline.tool_run', return_value=(dict(result), b'')):
                self.assertEqual(compile_check(Path('x.luau'), tools)['status'], expected)

    def test_one_failed_optimization_level_makes_recovery_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = {'decompiler': {'argv': ['decoder'], 'roblox_argv': ['decoder'], 'cfg_audit': True,
                     'bytecode_versions': [14], 'type_versions': [3]}, 'compiler': {'argv': ['compiler']}}
            audit = {'status': 'CFG_VALIDATED', 'functions': [], 'original_proto_count': 1, 'optimization': 2,
                     'optimization_levels': [{'optimization': 1, 'status': 'UNVERIFIED', 'error': 'roundtrip compile failed'},
                                             {'optimization': 2, 'status': 'CFG_VALIDATED'}]}
            with patch('pipeline.tool_run', return_value=({'status': 'ok', 'exit_code': 0}, b'return 1\n')), \
                 patch('pipeline.compile_check', return_value={'status': 'syntax_pass'}), \
                 patch('cfg_verify.audit_source', return_value=audit):
                report = extract(self.fixture(root), root / 'out', tools)
        self.assertEqual(report['summary']['status'], 'partial')


    def test_compiler_diagnostics_are_matched_by_format(self):
        from pipeline import compile_check, PINNED_COMPILER
        pinned = {'compiler': {'argv': ['c'], 'revision': PINNED_COMPILER}}
        for stderr, expected in (("Error: Unrecognized option '--SyntaxError'.", 'tool_error'),
                                 ("Error opening SyntaxError-missing.luau", 'tool_error'),
                                 ("x.luau(3,1): CompileError: Out of local registers", 'syntax_failed')):
            with self.subTest(stderr=stderr), patch('pipeline.tool_run', return_value=({'status': 'tool_failed', 'exit_code': 1, 'stderr': stderr}, b'')):
                self.assertEqual(compile_check(Path('x.luau'), pinned)['status'], expected)


if __name__ == '__main__':
    unittest.main()
