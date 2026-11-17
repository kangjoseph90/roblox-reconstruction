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
from bootstrap_tools import PATCH, checkout, digest, validate_tools


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


class ToolConfigTests(unittest.TestCase):
    """Explicit --tools configs get the same patch and adapter checks as the setup cache."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def tool(self, name, content=b'tool'):
        path = self.root / name
        path.write_bytes(content)
        return str(path), digest(path)

    def config(self, patch_sha256):
        compiler, compiler_sha = self.tool('compiler')
        decoder, decoder_sha = self.tool('decoder')
        return {'compiler': {'argv': [compiler], 'sha256': compiler_sha},
                'decompiler': {'argv': [decoder], 'sha256': decoder_sha, 'patch_sha256': patch_sha256,
                               'roblox_argv': [decoder, '--roblox']}}

    def test_explicit_config_must_match_bundled_patch(self):
        self.assertTrue(validate_tools(self.config(digest(PATCH))))
        for stale in ('0' * 64, None):
            with self.subTest(patch=stale):
                config = self.config(stale)
                if stale is None: del config['decompiler']['patch_sha256']
                with self.assertRaisesRegex(ValueError, 'bundled patch'):
                    validate_tools(config)

    def test_separate_roblox_adapter_requires_its_own_hash(self):
        config = self.config(digest(PATCH))
        adapter, adapter_sha = self.tool('adapter', b'adapter')
        config['decompiler']['roblox_argv'] = [adapter, '{input}']
        with self.assertRaisesRegex(ValueError, 'roblox_argv'):
            validate_tools(config)
        config['decompiler']['roblox_sha256'] = adapter_sha
        self.assertTrue(validate_tools(config))
