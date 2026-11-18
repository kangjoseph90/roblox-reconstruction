"""Authored pixel/control cases; these do not establish behavior of Roblox scenes."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import player_capture
try:
    import numpy as np
    import cv2
except ImportError:
    np = cv2 = None


@unittest.skipUnless(np is not None and cv2 is not None, "optional screenshot dependencies required")
class FrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "frame.png"

    def run_frame(self, pixels=None, close=False):
        control = Mock()
        class Capture:
            def __init__(self, **kwargs):
                self.handlers = {}
                self.target = kwargs
            def event(self, function):
                self.handlers[function.__name__] = function
                return function
            def start_free_threaded(self):
                if close:
                    self.handlers["on_closed"]()
                elif pixels is not None:
                    h, w = pixels.shape[:2]
                    self.handlers["on_frame_arrived"](SimpleNamespace(frame_buffer=pixels, height=h, width=w, timespan=123), control)
                return control
        module = SimpleNamespace(WindowsCapture=Capture)
        with patch.dict(sys.modules, {"windows_capture": module}):
            return player_capture.capture_frame(88, self.output, 0.001)

    def test_black_and_uniform_colors_are_not_accepted(self):
        for color in [(0, 0, 0, 255), (255, 255, 255, 255), (255, 0, 255, 255)]:
            pixels = np.zeros((100, 100, 4), dtype=np.uint8)
            pixels[:] = color
            result = self.run_frame(pixels)
            self.assertEqual(result["reason"], "blank_frame")
            self.assertFalse(self.output.exists())

    def test_titlebar_does_not_make_black_content_valid(self):
        pixels = np.zeros((100, 100, 4), dtype=np.uint8)
        pixels[:10] = 255
        result = self.run_frame(pixels)
        self.assertEqual(result["reason"], "blank_frame")

    def test_authored_pattern_produces_decodable_png(self):
        pixels = np.zeros((100, 100, 4), dtype=np.uint8)
        pixels[::2, ::2] = 255
        result = self.run_frame(pixels)
        self.assertEqual(result["status"], "pass")
        decoded = cv2.imdecode(np.frombuffer(self.output.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, (100, 100, 3))
        self.assertTrue(np.array_equal(decoded, pixels[:, :, :3]))

    def test_window_closure_is_explicit(self):
        self.assertEqual(self.run_frame(close=True)["reason"], "window_closed")

    def test_absent_frames_time_out(self):
        self.assertEqual(self.run_frame()["reason"], "frame_timeout")

    def test_failed_encoder_does_not_publish_image(self):
        pixels = np.zeros((100, 100, 4), dtype=np.uint8)
        pixels[::2] = 255
        with patch.object(cv2, "imencode", return_value=(False, None)):
            self.assertEqual(self.run_frame(pixels)["reason"], "frame_encoding_failed")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
