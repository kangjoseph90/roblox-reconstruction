"""Bounded HWND-targeted WGC worker. Parent terminates this worker on timeout."""
import argparse
import json
from pathlib import Path
import threading
import time


def capture_frame(hwnd, output, timeout):
    try:
        import cv2
        from windows_capture import WindowsCapture
    except ImportError as exc:
        return {"status": "failed", "reason": "screenshot_dependency_missing", "error": str(exc),
                "install": "python -m pip install -r requirements-windows.txt"}
    done, result, lock = threading.Event(), {}, threading.Lock()
    deadline = [None]  # set when capture starts; a frame finishing after it is never published
    seen_blank, finished = [False], [False]  # finished: a result is final; later frames change nothing
    capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=False)

    @capture.event
    def on_frame_arrived(frame, control):
        if finished[0]:
            control.stop()
            return
        try:
            image = frame.frame_buffer[:, :, :3]
            h, w = image.shape[:2]
            center = image[h // 4:max(h // 4 + 1, h * 3 // 4), w // 4:max(w // 4 + 1, w * 3 // 4)]
            # Reject black/uniform central content even if the title bar is nonblank.
            # Passing this heuristic is not evidence of a game scene or successful entry.
            spatial_std = float(center.reshape(-1, 3).std(axis=0).max())
            if w < 64 or h < 64 or int(center.max()) <= 4 or spatial_std < 0.5:
                seen_blank[0] = True
                return
            ok, png = cv2.imencode(".png", image)
            if not ok:
                raise OSError("PNG encoder failed")
            outcome = dict(status="pass", width=w, height=h, center_std=spatial_std,
                           center_max=int(center.max()), timespan=frame.timespan)
            data = png.tobytes()
        except Exception as exc:
            outcome, data = dict(status="failed", reason="frame_encoding_failed", error=str(exc)), None
        with lock:
            # The timeout or window closure may have decided the result while this frame was encoded,
            # or the deadline may have passed before the waiting thread took the lock.
            if not finished[0] and deadline[0] is not None and time.monotonic() > deadline[0]:
                result.update(status="failed", reason="frame_timeout")
                finished[0] = True
                done.set()
            if not finished[0]:
                if data is not None:
                    try:
                        Path(output).write_bytes(data)
                    except OSError as exc:
                        outcome = dict(status="failed", reason="frame_encoding_failed", error=str(exc))
                result.update(outcome)
                finished[0] = True
                done.set()
        control.stop()

    @capture.event
    def on_closed():
        with lock:
            if not finished[0]:
                result.update(status="failed", reason="window_closed")
                finished[0] = True
                done.set()

    deadline[0] = time.monotonic() + timeout
    control = capture.start_free_threaded()
    done.wait(timeout)
    with lock:
        if not finished[0]:
            result.update(status="failed", reason="blank_frame" if seen_blank[0] else "frame_timeout")
            finished[0] = True
    control.stop()
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hwnd", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--timeout", type=float, required=True)
    args = p.parse_args()
    try:
        result = capture_frame(args.hwnd, args.out, args.timeout)
    except Exception as exc:
        result = {"status": "failed", "reason": "capture_backend_failed", "error": str(exc)}
    Path(args.result).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
