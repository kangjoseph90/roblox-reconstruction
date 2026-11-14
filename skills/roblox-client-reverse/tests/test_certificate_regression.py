import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regression import certificate_regression

class CertificateRegressionTests(unittest.TestCase):
    def test_equal_counts_cannot_hide_a_lost_certificate(self):
        old={'status':'UNVERIFIED','matched_functions':2,'total_functions':3,'validated_original_protos':[0,1]}
        new={'status':'UNVERIFIED','matched_functions':2,'total_functions':3}
        self.assertTrue(certificate_regression(old,new,{0,2}))
        self.assertFalse(certificate_regression(old,new,{0,1}))
        self.assertFalse(certificate_regression(old,dict(new,matched_functions=3),{0,1,2}))

    def test_module_and_context_counterexamples_override_counts(self):
        old={'status':'CFG_VALIDATED','matched_functions':1,'total_functions':1,'validated_original_protos':[0]}
        self.assertTrue(certificate_regression(old,dict(old,status='UNVERIFIED'),{0}))
        self.assertTrue(certificate_regression(old,dict(old,status='CFG_MISMATCH'),{0}))

if __name__=='__main__':unittest.main()
