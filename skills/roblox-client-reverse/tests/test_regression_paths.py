"""External capture/baseline paths do not depend on an installed fixture tree."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rcr
from regression import run_regression


class RegressionPathTests(unittest.TestCase):
    def test_cli_requires_input_and_baseline(self):
        for argv in (['rcr', 'regression', '--out', 'output'],
                     ['rcr', 'regression', 'capture', '--out', 'output']):
            with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                rcr.main()
            self.assertEqual(caught.exception.code, 2)

    def test_cli_forwards_arbitrary_paths(self):
        argv = ['rcr', 'regression', 'capture with spaces', '--baseline', 'expected elsewhere.json', '--out', 'output']
        with patch.object(sys, 'argv', argv), patch('regression.run_regression', return_value={'summary': {'status': 'pass'}}) as replay, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rcr.main(), 0)
        replay.assert_called_once_with('capture with spaces', 'output', None, None, 'expected elsewhere.json')

    def test_external_baseline_used_and_inputs_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture, out = root / 'capture', root / 'output'
            capture.mkdir()
            (out / 'repository').mkdir(parents=True)
            (out / 'evidence').mkdir()
            baseline = root / 'separate-baseline.json'
            expected = {'payloads': {'example': {'status': 'recovered', 'source_sha256': 'source'}}}
            baseline.write_text(json.dumps(expected), encoding='utf-8')
            before = baseline.read_bytes()
            (out / 'repository/report.json').write_text(json.dumps({'scripts': [{'bytecode': {'sha256': 'example'}, 'recovery': 'recovered', 'source_sha256': 'source'}]}))
            report = {'summary': {'status': 'partial'}, 'replay_errors': []}
            with patch('regression.replay_capture', return_value=report) as replay, patch('regression.finish'):
                result = run_regression(capture, out, baseline=baseline)
            replay.assert_called_once_with(capture.resolve(), out, None, None)
            self.assertEqual(result['regression']['status'], 'pass')
            self.assertEqual(baseline.read_bytes(), before)
            self.assertEqual(list(capture.iterdir()), [])
            self.assertNotIn('dataset', result['regression'])

    def test_missing_baseline_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as tmp, patch('regression.replay_capture') as replay:
            with self.assertRaisesRegex(ValueError, 'explicit baseline'):
                run_regression(tmp, Path(tmp) / 'output')
            replay.assert_not_called()


if __name__ == '__main__': unittest.main()
