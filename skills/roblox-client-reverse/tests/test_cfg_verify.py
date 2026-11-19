import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unittest.mock import patch
from cfg_verify import ToolFailure, compare, compare_exact, disassemble


class DisassemblerFlagTests(unittest.TestCase):
    def run_with(self, encoding):
        done = subprocess.CompletedProcess([], 0, stdout=b"{}", stderr=b"")
        with patch("cfg_verify.subprocess.run", return_value=done) as run:
            disassemble("luaudec", "input.bytecode", encoding)
        return run.call_args[0][0][1]

    def test_declared_formats_map_to_supported_disassembler_flags(self):
        self.assertEqual(self.run_with("luau"), "--standard")
        self.assertEqual(self.run_with("standard"), "--standard")
        self.assertEqual(self.run_with("roblox"), "--roblox")

    def test_unknown_encoding_and_tool_exit_are_tool_failures(self):
        with self.assertRaises(ToolFailure):
            disassemble("luaudec", "input.bytecode", "unknown")
        failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"unknown option")
        with patch("cfg_verify.subprocess.run", return_value=failed), self.assertRaises(ToolFailure):
            disassemble("luaudec", "input.bytecode", "luau")


class OptimizationSelectionTests(unittest.TestCase):
    def audit(self, results, compile_codes=(0, 0)):
        from cfg_verify import audit_source
        codes = iter(compile_codes)
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, next(codes), stdout=b"bytecode", stderr=b"compile error")
        reports = iter(results)
        tools = {"decompiler": {"argv": ["luaudec"]}, "compiler": {"argv": ["luau-compile", "--binary", "{input}"]}}
        with tempfile.TemporaryDirectory() as tmp, patch("cfg_verify.subprocess.run", side_effect=run), \
                patch("cfg_verify.disassemble", return_value={}), patch("cfg_verify.compare", side_effect=lambda a, b: dict(next(reports))):
            return audit_source("in.bytecode", "in.luau", tools, "luau", Path(tmp))

    def test_counterexample_at_one_level_is_not_hidden_by_another(self):
        mismatch = {"status": "CFG_MISMATCH", "functions": [{"status": "UNVERIFIED"}]}
        unverified = {"status": "UNVERIFIED", "functions": [{"status": "CFG_VALIDATED"}, {"status": "CFG_VALIDATED"}]}
        result = self.audit([mismatch, unverified])
        self.assertEqual((result["status"], result["optimization"]), ("CFG_MISMATCH", 1))
        self.assertEqual([l["status"] for l in result["optimization_levels"]], ["CFG_MISMATCH", "UNVERIFIED"])

    def test_failed_second_level_keeps_the_first_result(self):
        validated = {"status": "CFG_VALIDATED", "functions": [{"status": "CFG_VALIDATED"}]}
        result = self.audit([validated], compile_codes=(0, 1))
        self.assertEqual((result["status"], result["optimization"]), ("CFG_VALIDATED", 1))
        self.assertIn("roundtrip compile failed", result["optimization_levels"][1]["error"])

    def test_unexpected_error_at_one_level_keeps_the_other(self):
        validated = {"status": "CFG_VALIDATED", "functions": [{"status": "CFG_VALIDATED"}]}
        from cfg_verify import audit_source
        calls = iter([dict(validated), RecursionError("deep metadata")])
        def compare_once(a, b):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value
        tools = {"decompiler": {"argv": ["luaudec"]}, "compiler": {"argv": ["luau-compile", "--binary", "{input}"]}}
        done = subprocess.CompletedProcess([], 0, stdout=b"bc", stderr=b"")
        with tempfile.TemporaryDirectory() as tmp, patch("cfg_verify.subprocess.run", return_value=done), \
                patch("cfg_verify.disassemble", return_value={}), patch("cfg_verify.compare", side_effect=compare_once):
            result = audit_source("in.bytecode", "in.luau", tools, "luau", Path(tmp))
        self.assertEqual(result["status"], "CFG_VALIDATED")
        self.assertIn("deep metadata", result["optimization_levels"][1]["error"])

    def test_exception_without_a_message_still_marks_the_level_failed(self):
        validated = {"status": "CFG_VALIDATED", "functions": [{"status": "CFG_VALIDATED"}]}
        from cfg_verify import audit_source
        from pipeline import not_completed
        calls = iter([dict(validated), AssertionError()])
        def compare_once(a, b):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value
        tools = {"decompiler": {"argv": ["luaudec"]}, "compiler": {"argv": ["luau-compile", "--binary", "{input}"]}}
        done = subprocess.CompletedProcess([], 0, stdout=b"bc", stderr=b"")
        with tempfile.TemporaryDirectory() as tmp, patch("cfg_verify.subprocess.run", return_value=done), \
                patch("cfg_verify.disassemble", return_value={}), patch("cfg_verify.compare", side_effect=compare_once):
            result = audit_source("in.bytecode", "in.luau", tools, "luau", Path(tmp))
        self.assertTrue(result["optimization_levels"][1]["error"].startswith("AssertionError"))
        self.assertTrue(not_completed({"cfg_audit": {"optimization_levels": result["optimization_levels"]}}))

    def test_both_levels_failing_reports_both(self):
        with self.assertRaisesRegex(ValueError, "O1 .*O2 "):
            self.audit([], compile_codes=(1, 1))

    def test_all_levels_failing_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "roundtrip compile failed"):
            self.audit([], compile_codes=(1, 1))


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

    def test_inverted_jump_flag_does_not_rewrite_string_constants(self):
        self.assertDifferent('local m=...; if m=="first NOT" then return 1 end; return 2',
                             'local m=...; if m=="first" then return 1 end; return 2')
        a, b = (self.compile('local m=...; if m=="%s" then return 1 end; return 2' % k) for k in ("first NOT", "first"))
        self.assertEqual(compare_exact(a, b)["status"], "UNVERIFIED")

    def test_module_proof_correspondence_is_kept_for_renamed_children(self):
        a = self.compile('local function foo(x) local b=not not x; if b then return 1 else return 2 end end; '
                         'local function bar(y) return f(y) end; return foo, bar')
        b = self.compile('local function bar(x) if x then return 1 else return 2 end end; '
                         'local function foo(y) return f(y) end; return bar, foo')
        result = compare(a, b)
        self.assertEqual(result["status"], "CFG_VALIDATED")
        foo = next(f for f in result["functions"] if f["name"] == "foo")
        self.assertEqual((foo["status"], foo["matching_recovered_protos"]), ("CFG_VALIDATED", [0]))

    def test_module_correspondence_overrides_a_standalone_body_match(self):
        a = self.compile('local function foo(x) local b=not not x; if b then return 1 else return 2 end end; '
                         'local function bar(y) if y then return 1 else return 2 end end; return foo, bar')
        b = self.compile('local function foo(x) if x then return 1 else return 2 end end; '
                         'local function bar(y) local c=not not y; if c then return 1 else return 2 end end; return foo, bar')
        result = compare(a, b)
        self.assertEqual(result["status"], "CFG_VALIDATED")
        mapping = {f["name"]: f["matching_recovered_protos"] for f in result["functions"]}
        self.assertEqual(mapping["foo"], [0])
        self.assertEqual(mapping["bar"], [1])

    def test_child_counterexample_is_not_attributed_to_the_entry(self):
        a = self.compile('local function g(x) if x then return 1 end; return 2 end; return g(true)')
        b = self.compile('local function g(x) if x then return 1 end; return 3 end; return g(true)')
        result = compare(a, b)
        entry = next(f for f in result["functions"] if f["proto"] == a["mainProto"])
        child = next(f for f in result["functions"] if f["proto"] != a["mainProto"])
        self.assertEqual((result["status"], child["status"]), ("CFG_MISMATCH", "CFG_MISMATCH"))
        self.assertEqual((entry["status"], entry["module_status"]), ("UNVERIFIED", "CFG_MISMATCH"))

    def test_entry_diagnosis_does_not_claim_a_child_counterexample(self):
        a = self.compile('local function g(x) if x then return 1 end; return 2 end; return g(true)')
        b = self.compile('local function g(x) if x then return 1 end; return 3 end; return g(true)')
        entry = next(f for f in compare(a, b)["functions"] if f["proto"] == a["mainProto"])
        self.assertTrue(entry["diagnosis"]["actual_decompiler_error"].startswith("not_established"))

    def test_entry_records_no_child_counterexample_details(self):
        a = self.compile('local function g(x) if x then return 1 end; return 2 end; return g(true)')
        b = self.compile('local function g(x) if x then return 1 end; return 3 end; return g(true)')
        entry = next(f for f in compare(a, b)["functions"] if f["proto"] == a["mainProto"])
        self.assertEqual(entry["diagnosis"]["certainty"], "not_established")
        for attempt in entry.get("normalization_attempts", []):
            self.assertEqual(attempt["diagnosis"]["certainty"], "not_established")

    def test_call_removal_and_argument_swap_are_rejected(self):
        self.assertDifferent('return function(x,y) return f(x,y) end', 'return function(x,y) return f(y,x) end')
        self.assertDifferent('f(); return 1', 'return 1')

    def test_return_values_and_loop_bound_are_checked(self):
        self.assertDifferent('return function(x,y) return x,y end', 'return function(x,y) return x,x end')
        self.assertDifferent('for i=1,3 do f(i) end', 'for i=1,4 do f(i) end')

    def test_closure_capture_binding_is_checked_at_parent(self):
        self.assertDifferent('local x,y=f(),g(); return function() return x end',
                             'local x,y=f(),g(); return function() return y end')

    def test_exact_certificate_preserves_generic_loop_iterator_mode(self):
        original = self.compile('local t=...; for k,v in t do f(k,v) end')
        mutated = copy.deepcopy(original)
        loop = next(i for p in mutated['protos'] for i in p['instructions'] if i['op']=='FORGLOOP')
        loop['aux'] ^= 0x80000000
        loop['text'] = loop['text'].split(' [', 1)[0] + (' [inext]' if loop['aux'] & 0x80000000 else '')
        self.assertEqual(compare_exact(original, mutated)['status'], 'UNVERIFIED')

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
