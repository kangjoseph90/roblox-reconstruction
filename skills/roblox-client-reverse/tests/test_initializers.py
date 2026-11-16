"""Behavior regression for incoming values that survive a conditional overwrite."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOLS_PATH = os.environ.get('RCR_TEST_TOOLS')
SOURCE = Path(__file__).resolve().parent / 'semantic/cases/branch_entry_values.luau'


@unittest.skipUnless(TOOLS_PATH, 'RCR_TEST_TOOLS is required for native behavior tests')
class InitializerTests(unittest.TestCase):
    def test_branch_entry_values_with_and_without_debug_locals(self):
        tools = json.loads(Path(TOOLS_PATH).read_text(encoding='utf-8'))
        compiler = tools['compiler']['argv'][0]
        decoder = tools['decompiler']['argv'][0]
        runner = tools['runner']['argv'][0]
        for debug in (0, 1):
            for opt in (0, 1, 2):
                with self.subTest(debug=debug, optimization=opt), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    compiled = subprocess.run([compiler, '--binary', '-O' + str(opt), '-g' + str(debug), str(SOURCE)], capture_output=True, timeout=20)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr.decode(errors='replace'))
                    bytecode = directory / 'input.luauc'
                    bytecode.write_bytes(compiled.stdout)
                    decoded = subprocess.run([decoder, '--standard', '--no-header', str(bytecode)], capture_output=True, timeout=20)
                    self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors='replace'))
                    recovered = directory / 'recovered.luau'
                    recovered.write_bytes(decoded.stdout)
                    syntax = subprocess.run([compiler, '--only-parse', str(recovered)], capture_output=True, timeout=20)
                    self.assertEqual(syntax.returncode, 0, syntax.stderr.decode(errors='replace'))
                    original = subprocess.run([runner, '-O' + str(opt), str(SOURCE)], capture_output=True, timeout=20)
                    restored = subprocess.run([runner, '-O' + str(opt), str(recovered)], capture_output=True, timeout=20)
                    self.assertEqual(original.returncode, 0, original.stderr.decode(errors='replace'))
                    self.assertEqual(restored.returncode, original.returncode, restored.stderr.decode(errors='replace') + '\n' + decoded.stdout.decode(errors='replace'))
                    self.assertEqual(restored.stdout.replace(b'\r\n', b'\n'), original.stdout.replace(b'\r\n', b'\n'))


if __name__ == '__main__': unittest.main()
