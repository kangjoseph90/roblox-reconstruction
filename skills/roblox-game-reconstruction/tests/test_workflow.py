import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import reconstruct
from test_inventory import repo_fixture
from test_semantic import make_semantic_ledger, read_json, write_json


class WorkflowTests(unittest.TestCase):
    def make_run(self,root):
        repo=repo_fixture(root,cfg=True);run=root/'run'
        reconstruct.prepare(repo,run)
        def put(name,value):
            p=run/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text(json.dumps(value),encoding='utf-8')
        modules=json.loads((run/'shared/modules.json').read_text())['modules']
        funcs=json.loads((run/'shared/functions.json').read_text())['functions']
        fn=funcs[0];ev={'source_file':modules[0]['original_source'],'function_id':fn['function_id'],
                       'line_start':fn['source_line_start'],'line_end':fn['source_line_start']}
        put('catalog.json',{'title':'Fixture','systems':[{'id':'sample','title':'Observed fixture','module_ids':[modules[0]['module_id']],'discovery_basis':'fixture source'}],'exclusions':[]})
        put('analysis-plan.json',{'assignments':[{'system_id':'sample','worker':'coordinator','scope':'fixture','mode':'coordinator','output_dir':'analysis/sample'}]})
        analysis={'system_id':'sample','claims':[{'id':'returns','kind':'CODE_OBSERVED','statement':'helper returns a literal','evidence':[ev]}],
            'contracts':[{'id':'helper','kind':'CODE_OBSERVED','signature':'helper()','inputs':{},'steps':['Return the literal'],'outputs':{'value':1},'evidence':[ev]}],
            'content':[],'interfaces':[],'relationships':[],'tests':[],'unknowns':[{'id':'outside','question':'Which behavior exists beyond the bounded source slice?','impact':'fixture only','missing_evidence':'runtime'}],
            'coverage':{'module_ids':[modules[0]['module_id']],'function_ids_analyzed':[fn['function_id']],'remaining':[]}}
        put('analysis/sample/analysis.json',analysis)
        ledger,semantic_rules,_,_=make_semantic_ledger(run,modules[0],funcs,modules[0]['original_source'])
        analysis['claims'][0]['semantic_refs']=semantic_rules[0]['detail']['semantic_refs']
        put('analysis/sample/analysis.json',analysis)
        (run/'analysis/sample/spec.md').write_text('Fixture-specific source contract; no game execution.',encoding='utf-8')
        put('review.json',{'mode':'coordinator_only','reviewer':'unit fixture','checked_system_ids':['sample'],'checked_scope':'fixture','findings':[],'limitations':['not an independent semantic evaluation']})
        put('conflicts.json',{'conflicts':[]})
        return run

    def test_prepared_run_is_not_claimed_complete(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);repo=repo_fixture(root,cfg=False);result=reconstruct.prepare(repo,root/'run')
            self.assertEqual(result['status'],'awaiting_agent_analysis')
            self.assertEqual(json.loads((root/'run/workflow.json').read_text())['analysis_execution'],'NOT_STARTED')

    def test_end_to_end_package_survives_relocation_and_detects_tampering(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run=self.make_run(root)
            result=reconstruct.package(run,root/'package')
            self.assertEqual(result['status'],'pass')
            self.assertEqual(result['summary']['skill_version'],'1.1.0')
            self.assertEqual(result['summary']['semantic_status'],'PARTIAL')
            semantic=read_json(root/'package/semantic-coverage.json')
            self.assertTrue(any(item['status']=='UNANALYZED' for item in semantic['functions']))
            semantic_audit=read_json(root/'package/semantic-audit.json')
            self.assertEqual(semantic_audit['status'],'PARTIAL')
            self.assertEqual(semantic_audit['summary']['unknown_audits'],1)
            self.assertEqual(semantic['unknown_audits'][0]['status'],'NOT_FOUND_IN_SEARCHED_SCOPE')
            self.assertTrue((root/'package/source-reviews/review-a/source-first.json').is_file())
            self.assertFalse(read_json(root/'package/source-reviews/review-a/source-first.json')['specification_seen'])
            import shutil
            shutil.copytree(root/'package',root/'moved')
            self.assertEqual(reconstruct.validate_package(root/'moved')['status'],'pass')
            source=next((root/'moved/evidence/shared/sources').glob('*.luau'));source.write_text('changed')
            self.assertEqual(reconstruct.validate_package(root/'moved')['status'],'failed')

    def test_package_refuses_missing_review(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run=self.make_run(root);(run/'review.json').unlink()
            with self.assertRaisesRegex(ValueError,'audit failed'):reconstruct.package(run,root/'package')


if __name__=='__main__':unittest.main()
