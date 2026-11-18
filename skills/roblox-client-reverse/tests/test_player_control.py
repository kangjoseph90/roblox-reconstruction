"""Player command boundaries, stable identity and lifecycle state (no game joins)."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import player_control as pc
import rcr
from place_catalog import _save


def window(hwnd=88, **extra):
    return dict({"hwnd": hwnd, "pid": 123, "title": "Example", "class": "GameWindow",
                 "visible": True, "minimized": False, "owned": False, "rect": [0, 0, 100, 100]}, **extra)


def player(pid=123, ticks=456, windows=None):
    return {"pid": pid, "created_at_ticks": ticks, "path": "C:/Player/RobloxPlayerBeta.exe",
            "windows": windows if windows is not None else [window(pid=pid)]}


def inventory(*players, errors=None):
    return {"players": list(players), "errors": errors or []}


class FakeSession:
    def __init__(self, windows=None, normal=True):
        self.identity = {k: v for k, v in player().items() if k != "windows"}
        self.items = windows if windows is not None else [window()]
        self.closed, self.normal, self.forced, self.requests = False, normal, False, []
        self.after = None
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def windows(self):
        return deepcopy(self.items)

    def window_info(self, hwnd):
        self.reads += 1
        if self.reads > 1 and self.after is not None:
            if isinstance(self.after, Exception):
                raise self.after
            return self.after
        return next(deepcopy(w) for w in self.items if w["hwnd"] == hwnd)

    def wait(self, seconds=0):
        return self.closed

    def request_close(self, hwnd):
        self.requests.append(hwnd)
        self.closed = self.normal

    def terminate(self):
        self.forced, self.closed = True, True


class PlayerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        _save(self.game / "game.json", {"schema": "rcr-game/v1", "universe_id": 200})
        _save(self.game / "places.json", {"schema": "rcr-places/v1", "universe_id": 200, "places": [
            {"id": 1001, "entry": {"status": "failed", "error_code": 524},
             "capture": {"status": "partial", "path": "captures/1001"}}]})
        self.backend = Mock()
        self.backend.launch.return_value = {"launcher_pid": 999, "method": "registered_protocol"}
        self.session = FakeSession()
        self.backend.session.return_value = self.session
        self.output = self.root / "경로 🌙" / "screen.png"

    def state(self):
        return json.loads((self.game / "places.json").read_text(encoding="utf-8"))["places"][0]

    def open(self, *snapshots, **options):
        self.backend.players.side_effect = snapshots
        return pc.open_player("1001", self.game, backend=self.backend, **options)

    def capture(self, hwnd, output, timeout):
        output.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test fixture")
        return {"status": "pass", "width": 100, "height": 100}

    def shot(self, **options):
        return pc.screenshot(123, self.output, backend=self.backend, capture=self.capture, **options)

    def test_inventory_exposes_partial_access_and_windows(self):
        self.backend.players.return_value = inventory(player(), errors=[{"pid": 55, "error": "access denied"}])
        result = pc.list_players(self.backend)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["players"][0]["windows"][0]["hwnd"], 88)
        self.assertEqual(result["errors"][0]["pid"], 55)

    def test_open_returns_new_player_not_launcher_and_preserves_capture(self):
        result = self.open(inventory(player(10)), inventory(player(10), player()))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["process"]["pid"], 123)
        self.assertNotEqual(result["process"]["pid"], result["launch"]["launcher_pid"])
        self.assertEqual(result["game_entry"], "not_observed")
        self.assertEqual(self.state()["entry"]["status"], "player_identified")
        self.assertIsNone(self.state()["entry"]["error_code"])
        self.assertEqual(self.state()["capture"], {"status": "partial", "path": "captures/1001"})

    def test_reused_pid_is_distinguished_by_creation_time(self):
        result = self.open(inventory(player(ticks=1)), inventory(player(ticks=2)))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["process"]["created_at_ticks"], 2)

    def test_new_window_in_existing_player_is_identified(self):
        result = self.open(inventory(player(windows=[])), inventory(player()))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["process"]["pid"], 123)

    def test_existing_singleton_is_not_assumed_to_be_launched_player(self):
        with patch("player_control.time.monotonic", side_effect=[0, 0, 2, 2]):
            result = self.open(inventory(player()), inventory(player()), timeout=1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "no_new_player_identified")
        self.assertIsNone(self.state()["entry"]["pid"])

    def test_multiple_new_players_report_ambiguity(self):
        result = self.open(inventory(), inventory(player(123), player(456)))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reason"], "ambiguous_players")
        self.assertEqual(len(result["candidates"]), 2)

    def test_incomplete_initial_inventory_does_not_launch_or_modify_state(self):
        before = self.state()
        result = self.open(inventory(errors=[{"pid": 20, "error": "unreadable"}]))
        self.assertEqual(result["status"], "failed")
        self.backend.launch.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_unknown_place_is_rejected_before_launch(self):
        with self.assertRaisesRegex(ValueError, "official list"):
            pc.open_player("2002", self.game, backend=self.backend)
        self.backend.launch.assert_not_called()

    def test_launch_failure_recorded_without_losing_capture(self):
        self.backend.launch.side_effect = OSError("handler unavailable")
        result = self.open(inventory())
        self.assertEqual(result["reason"], "launch_failed")
        self.assertEqual(self.state()["entry"]["status"], "failed")
        self.assertEqual(self.state()["capture"]["status"], "partial")

    def test_unsupported_runtime_is_execution_failure(self):
        self.backend.require_available.side_effect = OSError("requires Windows")
        result = pc.open_player("1001", self.game, backend=self.backend)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.state()["entry"]["error_code"], 524)

    def test_hidden_launch_process_is_not_returned(self):
        result = self.open(inventory(), inventory(player(windows=[])), inventory(player()))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(self.backend.players.call_count, 3)

    def test_screenshot_keeps_bound_identity_hash_and_sidecar(self):
        result = self.shot()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(result["window"]["hwnd"], 88)
        self.assertEqual(result["game_entry"], "not_observed")
        saved = json.loads(Path(result["metadata"]).read_text())
        self.assertEqual(saved["process"], self.session.identity)

    def test_record_entry_keeps_direct_observation_separate_from_capture(self):
        self.open(inventory(), inventory(player()))
        image = self.game / "evidence/screen.png"
        pc.screenshot(123, image, backend=self.backend, capture=self.capture)
        with patch.object(sys, "argv", ["rcr.py", "record-entry", "--game", str(self.game), "--place", "1001",
                                        "--outcome", "access_denied", "--screenshot", str(image), "--error-code", "524"]), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(rcr.main(), 0)
        observation = json.loads(output.getvalue())["observation"]
        self.assertEqual(observation["error_code"], 524)
        self.assertEqual(observation["screenshot"]["path"], "evidence/screen.png")
        state = self.state()
        self.assertEqual(state["entry"]["observation"], observation)
        self.assertEqual(state["entry"]["status"], "player_identified")
        self.assertEqual(state["capture"], {"status": "partial", "path": "captures/1001"})

    def test_record_entry_rejects_stale_wrong_process_and_altered_evidence(self):
        shot = self.shot()
        self.open(inventory(), inventory(player()))
        meta = Path(shot["metadata"])
        with self.assertRaisesRegex(ValueError, "predates"):
            pc.record_entry(1001, self.game, "home_screen", self.output)
        shot["captured_at"] = self.state()["entry"]["requested_at"]
        shot["process"]["created_at_ticks"] += 1
        _save(meta, shot)
        with self.assertRaisesRegex(ValueError, "different Player"):
            pc.record_entry(1001, self.game, "home_screen", self.output)
        shot["process"]["created_at_ticks"] -= 1
        _save(meta, shot)
        self.output.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            pc.record_entry(1001, self.game, "home_screen", self.output)
        self.assertIsNone(self.state()["entry"]["observation"])

    def test_missing_or_ambiguous_windows_do_not_capture(self):
        for windows, reason in [([], "no_window"), ([window(1), window(2)], "ambiguous_windows")]:
            with self.subTest(reason=reason):
                self.session.items = windows
                result = self.shot()
                self.assertEqual(result["reason"], reason)
                self.assertFalse(self.output.exists())

    def test_explicit_hwnd_resolves_multiple_windows(self):
        self.session.items = [window(1), window(2)]
        result = self.shot(hwnd=2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["window"]["hwnd"], 2)

    def test_minimized_window_is_not_restored_or_captured(self):
        self.session.items = [window(minimized=True)]
        result = self.shot()
        self.assertEqual(result["reason"], "window_minimized_or_hidden")
        self.assertFalse(self.output.exists())

    def test_existing_image_or_sidecar_is_not_overwritten(self):
        self.output.parent.mkdir()
        for path in [self.output, self.output.with_suffix(".png.json")]:
            path.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.shot()
            self.assertEqual(path.read_bytes(), b"keep")
            path.unlink()

    def test_capture_failure_never_publishes_image(self):
        result = pc.screenshot(123, self.output, backend=self.backend,
                               capture=lambda *args: {"status": "failed", "reason": "blank_frame"})
        self.assertEqual(result["reason"], "blank_frame")
        self.assertFalse(self.output.exists())

    def test_disappearing_or_changed_window_rejects_frame(self):
        for after in [OSError("target disappeared"), window(minimized=True), window(**{"class": "Other"}),
                      window(rect=[0, 0, 200, 200])]:
            with self.subTest(after=after):
                self.session.reads, self.session.after = 0, after
                result = self.shot()
                self.assertEqual(result["status"], "failed")
                self.assertFalse(self.output.exists())

    def test_worker_invalid_png_is_rejected(self):
        def invalid(hwnd, output, timeout):
            output.write_bytes(b"not a PNG")
            return {"status": "pass"}
        result = pc.screenshot(123, self.output, backend=self.backend, capture=invalid)
        self.assertEqual(result["reason"], "invalid_png")
        self.assertFalse(self.output.exists())

    def test_capture_worker_timeout_does_not_target_player(self):
        with patch("player_control.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 1)) as run:
            result = pc._capture(88, self.root / "frame.png", 1)
        self.assertEqual(result["reason"], "capture_worker_timeout")
        argv = run.call_args.args[0]
        self.assertIn("player_capture.py", argv[1])
        self.assertNotIn("--pid", argv)

    def test_normal_close_targets_selected_window_only(self):
        result = pc.close_player(123, backend=self.backend)
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["forced"])
        self.assertEqual(self.session.requests, [88])
        self.assertFalse(self.session.forced)

    def test_close_timeout_does_not_force_by_default(self):
        self.session.normal = False
        result = pc.close_player(123, backend=self.backend)
        self.assertEqual(result["reason"], "close_timeout")
        self.assertFalse(self.session.forced)

    def test_explicit_force_runs_only_after_normal_close_fails(self):
        self.session.normal = False
        result = pc.close_player(123, force=True, backend=self.backend)
        self.assertEqual(self.session.requests, [88])
        self.assertEqual(result["method"], "terminate_process")
        self.assertTrue(result["forced"])

    def test_explicit_force_can_follow_a_rejected_close_message(self):
        self.session.request_close = Mock(side_effect=OSError("message rejected"))
        result = pc.close_player(123, force=True, backend=self.backend)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["forced"])
        self.assertIn("message rejected", result["close_errors"][0]["error"])

    def test_no_window_is_not_treated_as_closed(self):
        self.session.items = []
        result = pc.close_player(123, backend=self.backend)
        self.assertEqual(result["reason"], "no_window")
        self.assertFalse(self.session.forced)

    def test_bad_pid_timeout_and_extension_rejected_before_actions(self):
        for pid in [0, -1, True, 2 ** 32]:
            with self.assertRaises(ValueError):
                pc.close_player(pid, backend=self.backend)
        for value in [0, -1, 121, float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                pc.open_player("1001", self.game, timeout=value, backend=self.backend)
        with self.assertRaisesRegex(ValueError, ".png"):
            pc.screenshot(123, self.root / "file.jpg", backend=self.backend)
        self.backend.launch.assert_not_called()
        self.backend.session.assert_not_called()

    def test_cli_dispatch_without_native_builds(self):
        for argv, function in [(["players"], "list_players"),
                               (["open", "1001", "--game", str(self.game)], "open_player"),
                               (["screenshot", "--pid", "123", "--out", str(self.output)], "screenshot"),
                               (["close", "--pid", "123"], "close_player")]:
            with self.subTest(argv=argv), patch.object(sys, "argv", ["rcr.py"] + argv), \
                    patch("player_control." + function, return_value={"status": "failed"}) as action, \
                    patch("bootstrap_tools.build_tools") as build, redirect_stdout(io.StringIO()):
                self.assertEqual(rcr.main(), 2)
                action.assert_called_once()
                build.assert_not_called()
        with patch.object(sys, "argv", ["rcr.py", "close", "--pid", "0"]), redirect_stderr(io.StringIO()):
            self.assertEqual(rcr.main(), 1)


if __name__ == "__main__":
    unittest.main()
