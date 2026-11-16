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


class ReportSchemaTests(unittest.TestCase):
    def test_emitted_cfg_outcomes_fit_the_published_schema(self):
        schema = json.loads((ROOT / 'references/report.schema.json').read_text(encoding='utf-8'))
        allowed = schema['properties']['scripts']['items']['properties']['verification']['properties']['cfg']['enum']
        for outcome in ('CFG_VALIDATED', 'UNVERIFIED', 'CFG_MISMATCH', 'audit_exception'):
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
                     patch('cfg_verify.audit_source', side_effect=ValueError('unsupported fixture') if outcome == 'audit_exception' else None, return_value=audit):
                    report = extract(source, root / 'out', tools)
                self.assertEqual(report['scripts'][0]['verification']['cfg'], 'UNVERIFIED' if outcome == 'audit_exception' else outcome)
                self.assertIn(report['scripts'][0]['verification']['cfg'], allowed)
