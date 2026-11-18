"""Bounded HWND-targeted WGC worker. Parent terminates this worker on timeout."""
import argparse
import json
from pathlib import Path
import threading


def capture_frame(hwnd, output, timeout):
    try:
        import cv2
        from windows_capture import WindowsCapture
    except ImportError as exc:
        return {"status": "failed", "reason": "screenshot_dependency_missing", "error": str(exc),
                "install": "python -m pip install -r requirements-windows.txt"}
    done, result = threading.Event(), {}
    seen_blank = [False]
    capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=False)

    @capture.event
    def on_frame_arrived(frame, control):
        if done.is_set():
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
            Path(output).write_bytes(png.tobytes())
            result.update(status="pass", width=w, height=h, center_std=spatial_std,
                          center_max=int(center.max()), timespan=frame.timespan)
        except Exception as exc:
            result.update(status="failed", reason="frame_encoding_failed", error=str(exc))
        done.set()
        control.stop()

    @capture.event
    def on_closed():
        if not done.is_set():
            result.update(status="failed", reason="window_closed")
            done.set()

    control = capture.start_free_threaded()
    if not done.wait(timeout):
        result.update(status="failed", reason="blank_frame" if seen_blank[0] else "frame_timeout")
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
