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
    def make_run(self,root,extra_optimization=False):
        repo=repo_fixture(root,cfg=True);run=root/'run'
        if extra_optimization:
            for kind in ('comparison','recompiled'):
                (repo/'src/module/cfg-audit'/f'{kind}-O2.json').write_bytes((repo/'src/module/cfg-audit'/f'{kind}-O1.json').read_bytes())
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
        analysis['coverage']['functions'] = ledger['functions']
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
            self.assertEqual(result['summary']['skill_version'],reconstruct.VERSION)
            self.assertEqual(result['summary']['semantic_status'],'PARTIAL')
            self.assertEqual(result['analysis_status'],'PARTIAL')
            self.assertEqual(result['completion']['functions_reviewed'],0)
            self.assertEqual(result['completion']['functions_total'],3)
            self.assertEqual(result['completion']['unknowns_remaining'],1)
            html=(root/'package/index.html').read_text(encoding='utf-8')
            self.assertLess(html.index('Analysis: PARTIAL'),html.index('Systems') if 'Systems' in html else html.index('<article>'))
            self.assertIn('Semantic scope: 1/1 catalog-owned modules; reviewed functions in scope: 0/3',html)
            self.assertIn('open questions: 1',html)
            markdown=(root/'package/implementation-spec.md').read_text(encoding='utf-8')
            self.assertIn('Analysis: PARTIAL',markdown)
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

    def test_runs_from_other_releases_are_rejected_without_relaxing_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            config = read_json(run/'config.json')
            analysis = read_json(run/'analysis/sample/analysis.json')
            analysis['draft'] = True
            write_json(run/'analysis/sample/analysis.json', analysis)
            for version in ('0.1.0', '0.1.2', '0.2.0'):
                with self.subTest(version=version):
                    config['skill_version'] = version
                    write_json(run/'config.json', config)
                    issues = {e['issue'] for e in reconstruct.audit_run(run)['errors']}
                    self.assertIn('skill_version_mismatch', issues)
                    self.assertIn('worker_contract_invalid', issues)

    def test_missing_or_malformed_version_fails_the_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self.make_run(Path(tmp))
            config = read_json(run/'config.json')
            analysis = read_json(run/'analysis/sample/analysis.json')
            analysis['draft'] = True
            write_json(run/'analysis/sample/analysis.json', analysis)
            for version, issue in [(None, 'skill_version_missing'), ('not-a-version', 'skill_version_invalid')]:
                with self.subTest(version=version):
                    if version is None: config.pop('skill_version', None)
                    else: config['skill_version'] = version
                    write_json(run/'config.json', config)
                    result = reconstruct.audit_run(run)
                    issues = {e['issue'] for e in result['errors']}
                    self.assertIn(issue, issues)
                    self.assertIn('worker_contract_invalid', issues)
                    self.assertEqual(result['status'], 'failed')

    def test_package_refuses_missing_review(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run=self.make_run(root);(run/'review.json').unlink()
            with self.assertRaisesRegex(ValueError,'audit failed'):reconstruct.package(run,root/'package')

    def test_prerelease_version_keeps_current_validation_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=self.make_run(Path(tmp));config=read_json(run/'config.json')
            config['skill_version']=reconstruct.VERSION+'-rc1+build.5';write_json(run/'config.json',config)
            self.assertEqual(reconstruct.audit_run(run)['status'],'pass')

    def test_package_includes_a_requested_alternate_comparison_without_transferring_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=self.make_run(root,extra_optimization=True)
            analysis=read_json(run/'analysis/sample/analysis.json')
            functions=read_json(run/'shared/functions.json')['functions']
            certified=next(f for f in functions if f['cfg_status']=='CFG_VALIDATED')
            analysis['claims'][0]['evidence'][0].update(optimization=2,function_id=certified['function_id'])
            write_json(run/'analysis/sample/analysis.json',analysis)
            reconstruct.package(run,root/'package')
            rule=next(r for r in read_json(root/'package/rules.json')['rules']if r['id']=='sample:returns')
            citation=rule['evidence'][0]
            self.assertTrue(citation['package_comparison'].endswith('comparison-O2.json'))
            self.assertTrue((root/'package'/citation['package_comparison']).is_file())
            self.assertEqual(citation['function_cfg_status'],'UNVERIFIED')
            self.assertNotEqual(citation['module_cfg_status'],'CFG_VALIDATED')
            self.assertEqual(reconstruct.validate_package(root/'package')['status'],'pass')

    def test_an_unpinned_or_missing_requested_comparison_blocks_packaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=self.make_run(Path(tmp));analysis=read_json(run/'analysis/sample/analysis.json')
            analysis['claims'][0]['evidence'][0]['optimization']=2
            write_json(run/'analysis/sample/analysis.json',analysis)
            self.assertIn('comparison_optimization_unavailable',{e['issue']for e in reconstruct.audit_run(run)['errors']})

    def test_unavailable_shared_registry_never_labels_a_function_certified(self):
        from inventory import sha
        with tempfile.TemporaryDirectory() as tmp:
            run=self.make_run(Path(tmp));manifest=read_json(run/'shared-manifest.json')
            manifest['files'].append({'path':'not-present.json','sha256':'0'*64})
            write_json(run/'shared-manifest.json',manifest)
            config=read_json(run/'config.json');config['shared_manifest_sha256']=sha((run/'shared-manifest.json').read_bytes())
            write_json(run/'config.json',config)
            result=reconstruct.audit_run(run)
            self.assertIn('shared_registry_file_unavailable',{e['issue']for e in result['errors']})
            self.assertTrue(all(e['function_cfg_status']!='CFG_VALIDATED'for r in read_json(run/'rules.json')['rules']for e in r['evidence']))


if __name__=='__main__':unittest.main()
