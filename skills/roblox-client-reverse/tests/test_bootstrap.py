"""Pinned source bytes must not depend on a user's Git line-ending settings."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bootstrap_tools import checkout


@unittest.skipUnless(shutil.which('git'), 'Git required')
class CheckoutTests(unittest.TestCase):
    def test_checkout_keeps_lf_with_global_crlf_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = root / 'upstream'
            upstream.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(upstream), *args], check=True,
                                      capture_output=True).stdout.decode().strip()
            git('init', '-q')
            git('config', 'core.autocrlf', 'false')
            git('config', 'core.eol', 'lf')
            (upstream / '.gitattributes').write_bytes(b'* text=auto\n')
            original = b'first line\nsecond line\n'
            (upstream / 'source.cpp').write_bytes(original)
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
            revision = git('rev-parse', 'HEAD')
            global_config = root / 'gitconfig'
            global_config.write_text('[core]\n autocrlf = false\n eol = crlf\n', encoding='utf-8')
            with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(global_config)}):
                checkout(str(upstream), revision, root / 'copy')
            self.assertEqual((root / 'copy/source.cpp').read_bytes(), original)


if __name__ == '__main__': unittest.main()
