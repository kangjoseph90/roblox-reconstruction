"""Developer scripts must not overwrite their inputs or report checks that did not run."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
import compare_snapshots


def capture(root, rows, counts=True):
    root.mkdir(parents=True)
    report = {"schema": "rcr-capture/v1", "place_id": 1, "scripts": rows}
    if counts:
        report["status_counts"] = {"opaque_buffer": len(rows)}
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "client-dump.json").write_text(json.dumps({"capture": {}}), encoding="utf-8")
    return root


class CompareSnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        row = {"path": ["Game", "A"], "class": "ModuleScript", "status": "opaque_buffer"}
        self.left = capture(self.root / "left", [dict(row, raw_sha256="1")])
        self.right = capture(self.root / "right", [dict(row, raw_sha256="2")], counts=False)

    def run_main(self, out):
        argv = ["compare_snapshots.py", "--reference", str(self.left), "--candidate", str(self.right), "--out", str(out)]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return compare_snapshots.main()

    def test_raw_buffer_change_is_reported_and_missing_counts_are_accepted(self):
        result = compare_snapshots.compare(self.left, self.right)
        self.assertEqual(result["path_group_counts"]["changed"], 1)

    def test_output_never_replaces_an_input_or_an_existing_file(self):
        report = self.left / "report.json"
        before = report.read_bytes()
        with self.assertRaises(SystemExit):
            self.run_main(report)
        self.assertEqual(report.read_bytes(), before)
        existing = self.root / "existing.json"
        existing.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.run_main(existing)
        self.assertEqual(existing.read_text(), "keep")


class ExperimentCheckTests(unittest.TestCase):
    def test_checks_survive_optimized_python(self):
        for name in ("run_experiments", "run_host_experiment"):
            code = ("import sys; sys.path[:0] = [%r, %r]; import %s as m\n"
                    "try:\n    m.check(False, 'authored failure')\nexcept SystemExit as e:\n    print(e)" % (str(HERE), str(HERE.parent / "scripts"), name))
            output = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=60)
            self.assertIn("experiment failed: authored failure", output.stdout, output.stderr)


class ExpandedSuiteTests(unittest.TestCase):
    def test_non_empty_output_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "out").mkdir()
            (root / "out" / "old.json").write_text("{}")
            tools = root / "tools.json"
            config = {k: {"argv": [sys.executable]} for k in ("compiler", "runner", "decompiler")}
            tools.write_text(json.dumps(config))
            run = subprocess.run([sys.executable, str(HERE / "semantic_expanded/run_semantic_expanded.py"),
                                  "--tools", str(tools), "--out", str(root / "out")], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn("new or empty", run.stderr)

    def test_adversarial_matrix_refuses_a_non_empty_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "out").mkdir()
            (root / "out" / "summary.json").write_text("{}")
            run = subprocess.run([sys.executable, str(HERE / "normalizer_adversarial/run_matrix.py"), "--tools",
                                  str(root / "tools.json"), "--out", str(root / "out")], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn("new or empty", run.stderr)


class SuiteSyntaxStateTests(unittest.TestCase):
    def test_parser_results_that_judged_nothing_are_not_syntax_results(self):
        sys.path.insert(0, str(HERE / "semantic"))
        sys.path.insert(0, str(HERE / "semantic_expanded"))
        import run_suite
        import run_semantic_expanded
        done = lambda code, err=b"": subprocess.CompletedProcess([], code, b"", err)
        self.assertEqual(run_suite.syntax_state(1, done(0)), "NOT_CHECKED")
        self.assertEqual(run_suite.syntax_state(0, done(-11)), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_suite.syntax_state(0, done(124, b"x\nfixture timed out")), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_suite.syntax_state(0, done(124, b"x.luau(1,1): SyntaxError: y\nfixture timed out")), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_suite.syntax_state(0, done(1, b"x.luau(1,1): SyntaxError: y")), "SYNTAX_INVALID")
        self.assertEqual(run_suite.syntax_state(0, done(1, b"Error opening recovered.luau")), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_semantic_expanded.syntax_state(0, (1, b"", b"Error opening recovered.luau", False)), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_semantic_expanded.syntax_state(0, (1, b"", b"x.luau(1,1): SyntaxError: y", False)), "SYNTAX_INVALID")
        self.assertEqual(run_semantic_expanded.syntax_state(0, (-11, b"", b"", False)), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_semantic_expanded.syntax_state(1, (0, b"", b"", False)), "NOT_CHECKED")

    def test_windows_exception_codes_are_not_syntax_results(self):
        sys.path.insert(0, str(HERE / "semantic"))
        sys.path.insert(0, str(HERE / "semantic_expanded"))
        import run_suite
        import run_semantic_expanded
        self.assertEqual(run_suite.syntax_state(0, subprocess.CompletedProcess([], 0xC0000005, b"", b"")), "SYNTAX_UNVERIFIED")
        self.assertEqual(run_semantic_expanded.syntax_state(0, (0xC0000005, b"", b"", False)), "SYNTAX_UNVERIFIED")
        for code in (0x40000015, 0x80000003, 0xC0000005):
            self.assertEqual(run_suite.syntax_state(0, subprocess.CompletedProcess([], code, b"", b"")), "SYNTAX_UNVERIFIED")
            self.assertEqual(run_semantic_expanded.syntax_state(0, (code, b"", b"", False)), "SYNTAX_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
