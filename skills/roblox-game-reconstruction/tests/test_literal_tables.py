"""Exercise the inherited literal assembler's accepted and rejected instruction cases."""
import contextlib
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import literal_tables


class LiteralAssemblerControls(unittest.TestCase):
    def test_inherited_twelve_controls(self):
        with contextlib.redirect_stdout(io.StringIO()):
            literal_tables.run_self_tests()
