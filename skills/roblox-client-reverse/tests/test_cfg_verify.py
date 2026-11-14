import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cfg_verify import compare, disassemble


@unittest.skipUnless(os.environ.get("RCR_TEST_TOOLS"), "requires pinned native tools")
class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = json.loads(Path(os.environ["RCR_TEST_TOOLS"]).read_text(encoding="utf-8"))
        self.count = 0

    def compile(self, source):
        self.count += 1
        src = self.root / (str(self.count) + ".luau")
        src.write_text(source, encoding="utf-8")
        p = subprocess.run([self.tools["compiler"]["argv"][0], "--binary", "-O1", str(src)], capture_output=True, check=True)
        target = src.with_suffix(".bytecode")
        target.write_bytes(p.stdout)
        return disassemble(self.tools["decompiler"]["argv"][0], target, "standard")

    def assertDifferent(self, a, b):
        self.assertIn(compare(self.compile(a), self.compile(b))["status"], {"UNVERIFIED", "CFG_MISMATCH"})

    def test_identity_and_local_alpha_rename_are_certified(self):
        a = 'return function(x,y) if x then return f(y) else return g(x) end end'
        b = 'return function(first,second) if first then return f(second) else return g(first) end end'
        self.assertEqual(compare(self.compile(a), self.compile(b))["status"], "CFG_VALIDATED")

    def test_same_branch_counts_do_not_certify_wrong_predicate(self):
        a = 'return function(x,y) if x then return f(y) else return g(x) end end'
        self.assertDifferent(a, a.replace('if x then', 'if y then'))

    def test_long_constant_suffix_is_checked(self):
        prefix = 'same prefix ' * 10
        self.assertDifferent('return "' + prefix + 'A"', 'return "' + prefix + 'B"')

    def test_call_removal_and_argument_swap_are_rejected(self):
        self.assertDifferent('return function(x,y) return f(x,y) end', 'return function(x,y) return f(y,x) end')
        self.assertDifferent('f(); return 1', 'return 1')

    def test_return_values_and_loop_bound_are_checked(self):
        self.assertDifferent('return function(x,y) return x,y end', 'return function(x,y) return x,x end')
        self.assertDifferent('for i=1,3 do f(i) end', 'for i=1,4 do f(i) end')

    def test_closure_capture_binding_is_checked_at_parent(self):
        self.assertDifferent('local x,y=f(),g(); return function() return x end',
                             'local x,y=f(),g(); return function() return y end')

    def test_fastcall_identity_and_target_are_not_erased(self):
        original = self.compile('return function(x) return math.abs(x) end')
        mutated = copy.deepcopy(original)
        fast = next(i for p in mutated['protos'] for i in p['instructions'] if i['op'].startswith('FASTCALL'))
        fast['text'] = fast['text'].replace(' 2 ', ' 12 ', 1)
        fast['a'] = 12
        self.assertEqual(compare(original, mutated)['status'], 'UNVERIFIED')
        mutated = copy.deepcopy(original)
        fast = next(i for p in mutated['protos'] for i in p['instructions'] if i['op'].startswith('FASTCALL'))
        fast['target'] += 1
        self.assertEqual(compare(original, mutated)['status'], 'UNVERIFIED')

    def test_distinct_cached_closures_cannot_be_merged(self):
        original = self.compile('local a=function() return 1 end; local b=function() return 1 end; return a==b')
        mutated = copy.deepcopy(original)
        main = mutated['protos'][mutated['mainProto']]
        closures = [i for i in main['instructions'] if i['op']=='DUPCLOSURE']
        self.assertEqual(len(closures), 2)
        first, second = closures
        second['text'] = second['text'].replace('K'+str(second['d']), 'K'+str(first['d']), 1)
        second['d'] = first['d']
        self.assertEqual(compare(original, mutated)['status'], 'UNVERIFIED')

    def test_distinct_nested_prototype_caches_cannot_be_merged(self):
        source='local function a() return function() return 1 end end; local function b() return function() return 1 end end; return a()==b()'
        original = self.compile(source)
        mutated = copy.deepcopy(original)
        main = mutated['protos'][mutated['mainProto']]
        closures = [c for c in main['constants'] if c['kind']=='closure']
        self.assertEqual(len(closures), 2)
        closures[1]['text'] = closures[0]['text']
        self.assertEqual(compare(original, mutated)['status'], 'UNVERIFIED')

    def test_non_lossless_numeric_metadata_is_not_certified(self):
        module = self.compile('return 1.25')
        constant = next(c for p in module['protos'] for c in p['constants'] if c['kind']=='number')
        constant['text'] = 'nan'
        constant.pop('numericBitsLE', None)
        constant.pop('numericEncoding', None)
        self.assertEqual(compare(module, module)['status'], 'UNVERIFIED')

    def test_raw_numeric_bits_preserve_nan_payload(self):
        module = self.compile('return 1.25')
        constant = next(c for p in module['protos'] for c in p['constants'] if c['kind']=='number')
        self.assertEqual(constant['numericBitsLE'], '000000000000f43f')
        self.assertEqual(constant['numericEncoding'], 'f64')
        constant.update(text='nan', numericBitsLE='010000000000f87f')
        self.assertEqual(compare(module, module)['status'], 'CFG_VALIDATED')
        other=copy.deepcopy(module)
        next(c for p in other['protos'] for c in p['constants'] if c['kind']=='number')['numericBitsLE']='020000000000f87f'
        self.assertNotEqual(compare(module, other)['status'], 'CFG_VALIDATED')

    def test_cached_vector_payload_is_not_truncated_to_display_text(self):
        module=self.compile('return 1.25')
        constant=next(c for p in module['protos'] for c in p['constants'] if c['kind']=='number')
        constant.update(kind='vector', text='vector(0,0,0)', numericEncoding='f32x4', numericBitsLE='00'*16)
        self.assertEqual(compare(module,module)['status'], 'CFG_VALIDATED')
        other=copy.deepcopy(module)
        next(c for p in other['protos'] for c in p['constants'] if c['kind']=='vector')['numericBitsLE']='00'*12+'0000803f'
        self.assertNotEqual(compare(module,other)['status'], 'CFG_VALIDATED')


if __name__ == "__main__":
    unittest.main()
