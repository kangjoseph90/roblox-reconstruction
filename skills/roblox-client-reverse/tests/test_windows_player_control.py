"""Owned renamed Python children exercise Win32 identity/termination, not Roblox."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from player_control import close_player
from windows_player import Session


@unittest.skipUnless(sys.platform == "win32", "Windows process handles required")
class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.children = []
        self.addCleanup(self.cleanup)
        original = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        self.executable = self.root / "RobloxPlayerBeta.exe"
        shutil.copy2(original, self.executable)
        for dll in [*original.parent.glob("python3*.dll"), *original.parent.glob("vcruntime*.dll")]:
            shutil.copy2(dll, self.root / dll.name)

    def cleanup(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.temp.cleanup()

    def start_owned(self):
        ready = self.root / ("ready-" + str(len(self.children)))
        code = "import sys,time,pathlib;pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(60)"
        child = subprocess.Popen([str(self.executable), "-c", code, str(ready)],
                                 env=dict(os.environ, PYTHONHOME=sys.base_prefix),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=0x08000000)
        self.children.append(child)
        deadline = time.monotonic() + 5
        while child.poll() is None and not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(ready.exists(), "owned Python child did not become ready")
        return child

    def test_session_rejects_unrelated_executable(self):
        with self.assertRaisesRegex(ValueError, "RobloxPlayerBeta"):
            Session(os.getpid())

    def test_owned_process_has_stable_creation_identity(self):
        child = self.start_owned()
        with Session(child.pid) as first, Session(child.pid) as second:
            self.assertEqual(first.identity, second.identity)
            self.assertGreater(first.identity["created_at_ticks"], 0)
            self.assertFalse(first.wait())

    def test_normal_close_does_not_force_headless_owned_process(self):
        child = self.start_owned()
        result = close_player(child.pid, timeout=1)
        self.assertEqual(result["reason"], "no_window")
        self.assertIsNone(child.poll())

    def test_force_terminates_only_selected_owned_process(self):
        selected, other = self.start_owned(), self.start_owned()
        result = close_player(selected.pid, force=True, timeout=2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["method"], "terminate_process")
        self.assertTrue(result["forced"])
        selected.wait(timeout=5)
        self.assertIsNone(other.poll())


if __name__ == "__main__":
    unittest.main()
