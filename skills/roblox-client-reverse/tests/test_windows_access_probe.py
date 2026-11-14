"""Native owned-process check: never opens Roblox or another user process."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows native API integration")
class WindowsAccessProbeTests(unittest.TestCase):
    def test_owned_marker_belongs_to_interpreter_pid(self):
        probe = Path(__file__).resolve().parents[1] / "scripts" / "windows_access_probe.py"
        run = subprocess.run([sys.executable, str(probe)], capture_output=True,
                             text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        result = json.loads(run.stdout)
        control = result["owned_control"]
        self.assertEqual(control["pid"], control["descriptor"]["pid"])
        self.assertTrue(control["memory_read"]["matches_expected"])
        self.assertEqual(control["memory_read"]["bytes_read"], 16)
        self.assertFalse(result["live_player_collector_implemented"])
        self.assertNotIn("player", result)
