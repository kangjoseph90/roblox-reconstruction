import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cfg_diagnose import classify

class DiagnosticTests(unittest.TestCase):
    def test_acyclic_join_reuse_is_not_labeled_as_loop(self):
        f={'status':'CFG_VALIDATED','rules':{'relational_loop_invariant_closure':1},
           'original_analysis':{'backedges':[]},'recompiled_analysis':{'backedges':[]}}
        proof={'status':'CFG_VALIDATED','functions':[f]}
        self.assertNotIn('loop_structuring',classify({}, {},proof,True)['categories'])
        f['original_analysis']['backedges']=[(5,1)]
        self.assertIn('loop_structuring',classify({}, {},proof,True)['categories'])

if __name__=='__main__':unittest.main()
