"""Player CLI orchestration; process control never builds native recovery tools."""
import hashlib
from functools import wraps
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from compatibility import fingerprint
from network import target_id
from pipeline import now
from place_catalog import _load_game, _save, update_place_state
from windows_player import WindowsPlayer


def runtime_result(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as exc:
            return {"status": "failed", "reason": "player_operation_failed", "error": str(exc)}
    return call


def _pid(pid):
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("PID must be a positive Windows process ID")


def _timeout(value):
    if not 0 < value <= 120:
        raise ValueError("timeout must be greater than 0 and at most 120 seconds")


@runtime_result
def list_players(backend=None):
    result = (backend or WindowsPlayer()).players()
    return dict(result, status="partial" if result["errors"] else "pass", observed_at=now())


@runtime_result
def open_player(target, game, executable=None, timeout=30, backend=None):
    place_id = target_id(target)
    _timeout(timeout)
    game = Path(game).resolve()
    _, catalog = _load_game(game)
    if not any(row["id"] == place_id for row in catalog["places"]):
        raise ValueError("Place ID is not in places.json; refresh the official list first")
    backend = backend or WindowsPlayer()
    backend.require_available()
    if executable:
        executable = Path(executable).resolve(strict=True)
        if executable.name.lower() != "robloxplayerbeta.exe":
            raise ValueError("--player-exe must name RobloxPlayerBeta.exe")
        if fingerprint(executable)["pe_machine"] != 0x8664:
            raise ValueError("--player-exe must be a Windows x64 Player")
    before = backend.players()
    if before["errors"]:
        return {"status": "failed", "reason": "incomplete_process_inventory", "errors": before["errors"]}
    seen = {(p["pid"], p["created_at_ticks"]) for p in before["players"]}
    seen_windows = {(p["pid"], p["created_at_ticks"]): {w["hwnd"] for w in p["windows"]}
                    for p in before["players"]}
    uri = "roblox://experiences/start?placeId=" + str(place_id)
    entry = {"status": "launching", "uri": uri, "requested_at": now(), "pid": None,
             "process": None, "error": None, "error_code": None, "game_entry": "not_observed"}
    update_place_state(game, place_id, entry=entry)
    try:
        launch = backend.launch(uri, executable)
    except OSError as exc:
        update_place_state(game, place_id, entry={"status": "failed", "error": str(exc)})
        return {"status": "failed", "reason": "launch_failed", "error": str(exc), "place_id": place_id}
    deadline, candidates, errors = time.monotonic() + timeout, [], []
    while time.monotonic() < deadline:
        observed = backend.players()
        errors = observed["errors"]
        candidates = [p for p in observed["players"] if p["windows"] and (
            (p["pid"], p["created_at_ticks"]) not in seen or
            any(w["hwnd"] not in seen_windows.get((p["pid"], p["created_at_ticks"]), set()) for w in p["windows"]))]
        if len(candidates) == 1 and not errors:
            player = candidates[0]
            update_place_state(game, place_id, entry={"status": "player_identified", "pid": player["pid"],
                               "process": player, "launch": launch})
            return {"status": "pass", "place_id": place_id, "process": player, "launch": launch,
                    "game_entry": "not_observed", "meaning": "New Player process/window identified; inspect a screenshot before collection."}
        if len(candidates) > 1:
            break
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    reason = "ambiguous_players" if len(candidates) > 1 else "no_new_player_identified"
    update_place_state(game, place_id, entry={"status": "launch_requested", "launch": launch, "error": reason})
    return {"status": "partial", "reason": reason, "place_id": place_id, "launch": launch,
            "candidates": candidates, "errors": errors, "game_entry": "not_observed"}


def _capture(hwnd, output, timeout):
    result_path = Path(output).with_suffix(".worker.json")
    try:
        child = subprocess.run([sys.executable, str(Path(__file__).with_name("player_capture.py")),
                                "--hwnd", str(hwnd), "--out", str(output), "--result", str(result_path),
                                "--timeout", str(timeout)], capture_output=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": "capture_worker_timeout"}
    if not result_path.is_file():
        return {"status": "failed", "reason": "capture_worker_failed", "exit_code": child.returncode,
                "error": child.stderr.decode("utf-8", "replace")[-2000:]}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("status") not in {"pass", "failed"}:
            raise ValueError("invalid worker result")
    except (ValueError, OSError) as exc:
        return {"status": "failed", "reason": "invalid_worker_result", "error": str(exc)}
    if child.returncode:
        return {"status": "failed", "reason": "capture_worker_failed", "exit_code": child.returncode, "worker": result}
    return result


@runtime_result
def screenshot(pid, output, timeout=10, hwnd=None, backend=None, capture=None):
    _pid(pid)
    _timeout(timeout)
    if hwnd is not None and (type(hwnd) is not int or hwnd <= 0):
        raise ValueError("HWND must be positive")
    output = Path(output).resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("screenshot output must use .png")
    metadata = output.with_suffix(output.suffix + ".json")
    if output.exists() or metadata.exists():
        raise ValueError("screenshot output or sidecar already exists; choose a new output")
    backend, capture = backend or WindowsPlayer(), capture or _capture
    with backend.session(pid) as process:
        windows = process.windows()
        choices = [w for w in windows if hwnd is None or w["hwnd"] == hwnd]
        result = {"status": "failed", "process": process.identity, "observed_at": now(),
                  "method": "windows_graphics_capture_hwnd", "game_entry": "not_observed"}
        if len(choices) != 1:
            return dict(result, reason="no_window" if not choices else "ambiguous_windows", windows=windows)
        window = process.window_info(choices[0]["hwnd"])
        if window["minimized"] or not window["visible"]:
            return dict(result, reason="window_minimized_or_hidden", window=window)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rcr-frame-", dir=output.parent) as folder:
            temp = Path(folder) / "frame.png"
            frame = capture(window["hwnd"], temp, timeout)
            if frame.get("status") != "pass":
                return dict(result, window=window, **{k: v for k, v in frame.items() if k != "status"})
            after = process.window_info(window["hwnd"])
            if (after["minimized"] or not after["visible"] or after["class"] != window["class"]
                    or after["rect"] != window["rect"]):
                return dict(result, reason="window_changed_during_capture", window=after)
            data = temp.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return dict(result, reason="invalid_png")
            result.update(status="pass", window=after, frame=frame, image=str(output),
                          sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), captured_at=now())
            # Exclusive creation avoids replacing a screenshot from another command.
            with output.open("xb") as stream:
                stream.write(data)
            _save(metadata, result)
            return dict(result, metadata=str(metadata))


@runtime_result
def close_player(pid, force=False, timeout=10, backend=None):
    _pid(pid)
    _timeout(timeout)
    backend = backend or WindowsPlayer()
    with backend.session(pid) as process:
        result = {"process": process.identity, "requested_at": now(), "forced": False}
        if process.wait():
            return dict(result, status="pass", method="already_exited", closed_at=now())
        windows = process.windows()
        close_errors = []
        for window in windows:
            if not process.wait():
                try:
                    process.request_close(window["hwnd"])
                except OSError as exc:
                    close_errors.append({"hwnd": window["hwnd"], "error": str(exc)})
        result["close_errors"] = close_errors
        if process.wait(timeout if windows else 0):
            return dict(result, status="pass", method="sc_close", closed_at=now())
        if force:
            process.terminate()
            if process.wait(min(5, timeout)):
                return dict(result, status="pass", method="terminate_process", forced=True, closed_at=now())
        return dict(result, status="failed", reason="close_timeout" if windows else "no_window",
                    windows=windows)
