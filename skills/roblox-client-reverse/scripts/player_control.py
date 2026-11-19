"""Player CLI orchestration; process control never builds native recovery tools."""
from datetime import datetime
from functools import wraps
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from compatibility import fingerprint
from network import target_id
from pipeline import now
from place_catalog import _load_game, update_place_state
from windows_player import WindowsPlayer

ENTRY_OUTCOMES = ("loaded_scene", "access_denied", "home_screen", "timed_out")


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
             "process": None, "launched_by_open": False, "error": None, "error_code": None, "game_entry": "not_observed",
             "observation": None, "redirected_to": None, "evidence": None, "launch": None}
    update_place_state(game, place_id, entry=entry)
    try:
        launch = backend.launch(uri, executable)
    except OSError as exc:
        update_place_state(game, place_id, entry={"status": "failed", "error": str(exc)})
        return {"status": "failed", "reason": "launch_failed", "error": str(exc), "place_id": place_id}
    deadline, candidates, errors = time.monotonic() + timeout, [], []
    try:
        while time.monotonic() < deadline:
            observed = backend.players()
            errors = observed["errors"]
            candidates = [p for p in observed["players"] if p["windows"] and (
                (p["pid"], p["created_at_ticks"]) not in seen or
                any(w["hwnd"] not in seen_windows.get((p["pid"], p["created_at_ticks"]), set()) for w in p["windows"]))]
            if len(candidates) == 1 and not errors:
                player = candidates[0]
                # Only a process that did not exist before the launch request belongs to this workflow.
                new_process = (player["pid"], player["created_at_ticks"]) not in seen
                update_place_state(game, place_id, entry={"status": "player_identified", "pid": player["pid"],
                                   "process": player, "launch": launch, "launched_by_open": new_process})
                meaning = ("New Player process identified; inspect a screenshot before collection." if new_process else
                           "A new window appeared in a Player that was already running; it was not launched by this "
                           "command, so close refuses it. Inspect a screenshot before collection.")
                return {"status": "pass", "place_id": place_id, "process": player, "new_process": new_process,
                        "launch": launch, "game_entry": "not_observed", "meaning": meaning}
            if len(candidates) > 1:
                break
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    except OSError as exc:
        # The launch was already requested; record that this attempt ended.
        update_place_state(game, place_id, entry={"status": "failed", "launch": launch, "error": str(exc)})
        return {"status": "failed", "reason": "player_operation_failed", "error": str(exc), "place_id": place_id, "launch": launch}
    except BaseException:
        update_place_state(game, place_id, entry={"status": "failed", "launch": launch, "error": "interrupted while identifying the Player"})
        raise
    reason = ("ambiguous_players" if len(candidates) > 1 else
              "incomplete_process_inventory" if errors else "no_new_player_identified")
    update_place_state(game, place_id, entry={"status": "launch_requested", "launch": launch, "error": reason})
    return {"status": "partial", "reason": reason, "place_id": place_id, "launch": launch,
            "candidates": candidates, "errors": errors, "game_entry": "not_observed"}


def record_entry(place_id, game, outcome, image=None, detail=None, error_code=None):
    """Store an operator's screen observation, independent of Place identity and capture."""
    place_id = target_id(place_id)
    if outcome not in ENTRY_OUTCOMES:
        raise ValueError("invalid entry outcome")
    if image is None and outcome != "timed_out":
        raise ValueError("this entry outcome requires --screenshot")
    if detail is not None and (not detail.strip() or len(detail) > 500):
        raise ValueError("--detail must be nonempty and at most 500 characters")
    if error_code is not None and (outcome != "access_denied" or type(error_code) is not int or error_code <= 0):
        raise ValueError("--error-code requires access_denied and a positive code")
    game = Path(game).resolve()
    _, catalog = _load_game(game)
    row = next((row for row in catalog["places"] if row["id"] == place_id), None)
    if row is None:
        raise ValueError("Place ID is not in places.json; refresh the official list first")
    entry = row.get("entry", {})
    requested_at = entry.get("requested_at")
    observation = {"outcome": outcome, "reported_at": now(), "requested_at": requested_at}
    if detail is not None:
        observation["detail"] = detail.strip()
    if error_code is not None:
        observation["error_code"] = error_code
    if image is not None:
        image = Path(image).resolve(strict=True)
        if not image.is_relative_to(game):
            # A relative reference keeps machine paths out of places.json and moves with GAME.
            raise ValueError("--screenshot must be saved inside GAME (for example GAME/evidence/)")
        meta = json.loads(image.with_suffix(image.suffix + ".json").read_text(encoding="utf-8"))
        if (not isinstance(meta, dict) or meta.get("status") != "pass"
                or meta.get("method") != "windows_graphics_capture_hwnd" or image.suffix.lower() != ".png"):
            raise ValueError("--screenshot requires a successful rcr screenshot with its sidecar")
        process = meta.get("process")
        if not isinstance(process, dict) or any(type(process.get(k)) is not int or process[k] <= 0
                                                 for k in ("pid", "created_at_ticks")):
            raise ValueError("screenshot sidecar lacks a valid Player process identity")
        bound = entry.get("process")
        if bound and any(process[k] != bound.get(k) for k in ("pid", "created_at_ticks")):
            raise ValueError("screenshot belongs to a different Player than the launch")
        captured_at = meta.get("captured_at")
        if not isinstance(captured_at, str):
            raise ValueError("screenshot sidecar lacks a capture time")
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        if captured.utcoffset() is None:
            raise ValueError("screenshot capture time requires a time zone")
        if requested_at and captured < datetime.fromisoformat(requested_at.replace("Z", "+00:00")):
            raise ValueError("screenshot predates the current entry attempt")
        data = image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or digest != meta.get("sha256") or len(data) != meta.get("bytes"):
            raise ValueError("screenshot bytes differ from the sidecar")
        observation.update(observed_at=captured_at, screenshot={
            "path": image.relative_to(game).as_posix(),
            "sha256": digest, "pid": process["pid"], "created_at_ticks": process["created_at_ticks"]})
    update_place_state(game, place_id, entry={"status": entry.get("status", "not_attempted"), "observation": observation})
    return {"status": "pass", "place_id": place_id, "observation": observation,
            "catalog": str(game / "places.json")}


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
            # Exclusive creation avoids replacing a screenshot or sidecar from another command. The image is
            # published only together with its sidecar, which record-entry requires.
            created = []
            try:
                with output.open("xb") as stream:
                    created.append(output)
                    stream.write(data)
                with metadata.open("x", encoding="utf-8", newline="\n") as sidecar:
                    created.append(metadata)
                    sidecar.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            except BaseException:
                for path in created:  # only files this command created
                    path.unlink()
                raise
            return dict(result, metadata=str(metadata))


@runtime_result
def close_player(pid, game, place_id, force=False, timeout=10, backend=None):
    """Close only the Player process that `open` recorded as new for this Place, identified by PID and creation time."""
    _pid(pid)
    _timeout(timeout)
    place_id = target_id(place_id)
    _, catalog = _load_game(Path(game).resolve())
    row = next((row for row in catalog["places"] if row["id"] == place_id), None)
    if row is None:
        raise ValueError("Place ID is not in places.json")
    entry = row.get("entry", {})
    owned = entry.get("process") or {}
    if not entry.get("launched_by_open") or owned.get("pid") != pid:
        raise ValueError("PID %d was not launched by `open` for Place %d (no matching new-process record); "
                         "close refuses Players it did not launch" % (pid, place_id))
    backend = backend or WindowsPlayer()
    with backend.session(pid) as process:
        result = {"process": process.identity, "requested_at": now(), "forced": False}
        if process.identity.get("created_at_ticks") != owned.get("created_at_ticks"):
            # The PID now names a different process (reuse after the launched Player exited).
            return dict(result, status="failed", reason="process_identity_changed", expected=owned)
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
