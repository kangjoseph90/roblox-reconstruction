"""Parallel recovery must produce the same report as sequential recovery."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pipeline
from pipeline import extract


def dump(path):
    # Authored payloads: X and Y differ but decompile to the same source; Z duplicates X.
    payloads = [("X", b"\x0e\x03first payload"), ("Y", b"\x0e\x03second payload"),
                ("Z", b"\x0e\x03first payload"), ("W", b"\x0e\x03third payload")]
    path.write_text(json.dumps({"schema": "rcr-dump/v1", "instances": [
        {"id": name, "class": "ModuleScript", "name": name, "bytecode_format": "roblox",
         "bytecode_base64": base64.b64encode(data).decode()} for name, data in payloads]}), encoding="utf-8")


class ParallelRecoveryTests(unittest.TestCase):
    def run_extract(self, root, workers):
        calls, lock = {"decompile": 0, "compile": 0}, threading.Lock()

        def decompile(config, source, destination=None):
            with lock:
                calls["decompile"] += 1
            text = b"return 'third'\n" if b"third" in Path(source).read_bytes() else b"return 1\n"
            return {"status": "ok", "exit_code": 0, "argv": [str(source)]}, text

        def compile_check(path, tools):
            with lock:
                calls["compile"] += 1
            return {"status": "syntax_pass", "argv": [str(path)]}

        tools = {"decompiler": {"argv": ["decoder"], "roblox_argv": ["decoder"], "bytecode_versions": [14], "type_versions": [3]},
                 "compiler": {"argv": ["compiler"]}}
        source = root / "dump.json"
        dump(source)
        out = root / ("out-%d" % workers)
        with patch.dict(os.environ, {"RCR_WORKERS": str(workers)}), \
                patch("pipeline.EXECUTOR", ThreadPoolExecutor), \
                patch("pipeline.tool_run", side_effect=decompile), \
                patch("pipeline.compile_check", side_effect=compile_check):
            report = extract(source, out, tools)
        def normalize(value):
            # Replace the run-specific output directory in every string, on any path flavor.
            if isinstance(value, dict):
                return {k: normalize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(v) for v in value]
            if not isinstance(value, str):
                return value
            # extract writes resolved paths; the temp dir may be spelled differently
            # (Windows 8.3 short names, macOS /var -> /private/var).
            return value.replace(str(out.resolve()), "OUT").replace(str(out), "OUT")
        return [normalize(s) for s in report["scripts"]], report["summary"], calls

    def test_parallel_matches_sequential_including_cross_bytecode_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sequential, seq_summary, seq_calls = self.run_extract(root, 1)
            parallel, par_summary, par_calls = self.run_extract(root, 3)
        self.assertEqual(parallel, sequential)
        self.assertEqual(par_summary, seq_summary)
        by_id = {s["id"]: s for s in parallel}
        self.assertEqual(by_id["Y"]["syntax_check_reused_from"], "X")
        self.assertEqual(by_id["Z"]["duplicate_bytecode_of"], "X")
        self.assertEqual(seq_calls, {"decompile": 3, "compile": 2})
        # Workers may repeat the syntax check that the merge then replaces; decompiles are not repeated.
        self.assertEqual(par_calls["decompile"], 3)

    def test_conflicting_syntax_results_are_not_merged_into_one_record(self):
        def decompile(config, source, destination=None):
            return {"status": "ok", "exit_code": 0, "argv": [str(source)]}, b"return 1\n"
        results = iter([{"status": "syntax_pass"}, {"status": "syntax_failed"}] * 4)
        lock = threading.Lock()
        def compile_check(path, tools):
            with lock:
                return dict(next(results))
        tools = {"decompiler": {"argv": ["decoder"], "roblox_argv": ["decoder"], "bytecode_versions": [14], "type_versions": [3]},
                 "compiler": {"argv": ["compiler"]}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump(root / "dump.json")
            with patch.dict(os.environ, {"RCR_WORKERS": "3"}), patch("pipeline.EXECUTOR", ThreadPoolExecutor), \
                    patch("pipeline.tool_run", side_effect=decompile), patch("pipeline.compile_check", side_effect=compile_check):
                report = extract(root / "dump.json", root / "out", tools)
        for s in report["scripts"]:
            failed = s["validation"]["status"] == "syntax_failed"
            self.assertEqual(s["verification"]["syntax"], "SYNTAX_INVALID" if failed else "SYNTAX_VALID", s)
            self.assertEqual(s["recovery"], "invalid_decompilation" if failed else "decompiled_syntax_valid", s)

    def test_worker_count_is_bounded_and_validated(self):
        with patch.dict(os.environ, {"RCR_WORKERS": "many"}), self.assertRaises(ValueError):
            pipeline.default_workers()
        with patch.dict(os.environ, {"RCR_WORKERS": "200"}), patch("pipeline.sys.platform", "win32"):
            self.assertEqual(pipeline.default_workers(), 61)

    def test_invalid_worker_setting_is_a_configuration_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump(root / "dump.json")
            with patch.dict(os.environ, {"RCR_WORKERS": "many"}), self.assertRaisesRegex(ValueError, "RCR_WORKERS"):
                extract(root / "dump.json", root / "out")
            self.assertFalse((root / "out").exists())

    def test_wrapper_does_not_run_the_cli_when_reimported_by_a_worker(self):
        import runpy
        with patch("rcr.main", side_effect=AssertionError("CLI must not run in a spawned worker")):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/rcr"), run_name="__mp_main__")



if __name__ == "__main__":
    unittest.main()
