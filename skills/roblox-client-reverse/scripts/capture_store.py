"""Raw capture storage and mandatory buffer revalidation; never decompile here."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time

from formats import MAX_BYTES
from pipeline import hierarchy, new_output, now, save_json, scope, validate_repository
from place_catalog import _load_game, update_place_state
from windows_player import Session


def read_capture(source):
    """Read current or supplied older capture layouts; trust only hashed approved buffers."""
    from rsb1 import unwrap
    source = Path(source).resolve()
    if (source / "capture/client-dump.json").is_file():
        dump_path, raw_dir, report_path = source / "capture/client-dump.json", source / "capture/originals", source / "evidence/capture.json"
    else:
        dump_path, raw_dir = source / "client-dump.json", source / "originals"
        report_path = source / "report.json" if (source / "report.json").is_file() else source / "capture-report.json"
    for path in (dump_path, report_path, raw_dir):
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("unsafe capture input path")
    if (source / "files.json").exists():
        integrity = validate_repository(source)
        if integrity["status"] != "pass":
            raise ValueError("capture integrity failed: " + json.dumps(integrity["failures"]))
    dump = json.loads(dump_path.read_text(encoding="utf-8-sig"))
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if dump.get("schema") != "rcr-dump/v1" or not isinstance(dump.get("instances"), list):
        raise ValueError("invalid capture inventory")
    records = {}
    for row in report["scripts"]:
        key = hex(row["address"])
        if key in records:
            raise ValueError("duplicate script address in capture report")
        records[key] = row
    nodes = copy.deepcopy(dump["instances"])
    paths, failures = hierarchy(nodes), []
    by_id = {n["id"]: n for n in nodes}
    roots = {n["name"] for n in nodes if n["class"] == "DataModel" and n.get("parent") is None}
    scripts = []
    for node in nodes:
        for key in ("source", "bytecode", "bytecode_base64", "bytecode_format", "opaque_source"):
            node.pop(key, None)
        if node["class"] not in {"LocalScript", "ModuleScript"}:
            continue
        row = records.get(node["id"])
        failure_kind = "integrity_error"
        try:
            if not row or not row.get("raw_sha256"):
                if not row or row.get("status") != "roblox_bytecode_captured":
                    failure_kind = "capture_unavailable"
                raise ValueError((row or {}).get("error") or "raw buffer unavailable")
            digest = row["raw_sha256"]
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("invalid raw content hash")
            raw = raw_dir / (digest + ".roblox-buffer")
            if raw.is_symlink():
                raise ValueError("raw input is a symlink")
            if raw.stat().st_size > MAX_BYTES:
                raise ValueError("raw buffer exceeds size limit")
            encoded = raw.read_bytes()
            if hashlib.sha256(encoded).hexdigest() != digest:
                raise ValueError("raw capture hash mismatch")
            if row.get("status") != "roblox_bytecode_captured":
                failure_kind = "capture_unavailable"
                raise ValueError(row.get("decode_error") or row.get("error") or "buffer was not approved as Roblox bytecode")
            decoded, metadata = unwrap(encoded)
            if not re.fullmatch("[0-9a-f]{64}", str(row.get("bytecode_sha256", ""))):
                raise ValueError("missing decoded payload hash")
            if hashlib.sha256(decoded).hexdigest() != row["bytecode_sha256"]:
                raise ValueError("decoded payload hash mismatch")
            node.update(bytecode=decoded, bytecode_format="roblox")
            node.setdefault("metadata", {}).update(raw_sha256=digest, rsb1=metadata)
        except (ValueError, OSError) as exc:
            node["capture_errors"] = [str(exc)]
            failures.append({"id": node["id"], "path": paths[node["id"]], "kind": failure_kind, "error": str(exc)})
        node["logical_path"] = paths[node["id"]]
        if node["logical_path"][0] in roots:
            node["logical_path"] = ["game", *node["logical_path"][1:]]
        node["scope"] = scope(node, node["logical_path"], "rcr-dump/v1", by_id)
        scripts.append(node)
    return {"source": source, "capture": dump.get("capture", {}), "report": report,
            "scripts": scripts, "errors": failures}


def _blocked(out, retry_failed):
    """Why an existing Place directory cannot take a new attempt, or None if it can."""
    if not out.exists() or not any(out.iterdir()):
        return None
    finished = (out / "report.json").is_file() and (out / "files.json").is_file()
    if not retry_failed:
        return ("this Place already has a capture; existing data is not overwritten" +
                ("" if finished else " (the previous attempt did not finish; use --retry-failed)"))
    files = [p for p in out.rglob("*") if p.is_file()]
    buffers = (any(p.suffix != ".json" for p in files)
               or any(p.is_file() for name in ("originals", "bytecode") for p in (out / name).rglob("*")))
    if finished:
        report = json.loads((out / "report.json").read_text(encoding="utf-8-sig"))
        if report.get("schema") != "rcr-capture/v1" or report.get("summary", {}).get("status") != "failed" or buffers:
            return "retry requires a failed capture without any raw/bytecode buffers"
        if validate_repository(out)["status"] != "pass":
            return "failed capture integrity must pass before retry"
    elif buffers:
        # An unfinished attempt with buffers has no manifest to vouch for them; keep it for manual review.
        return "the unfinished capture holds raw/bytecode buffers; move it aside to capture again"
    return None


def _has_capture(out):
    """A finished attempt that is not a failure: never replaced, so a later visit is skipped."""
    if not ((out / "report.json").is_file() and (out / "files.json").is_file()):
        return False
    report = json.loads((out / "report.json").read_text(encoding="utf-8-sig"))
    return report.get("summary", {}).get("status") != "failed"


def _prepare_capture(out, retry_failed):
    problem = _blocked(out, retry_failed)
    if problem:
        raise ValueError(problem)
    if not out.exists() or not any(out.iterdir()):
        new_output(out)
        (out / "evidence").mkdir(exist_ok=True)
        return
    files = [p for p in out.rglob("*") if p.is_file()]
    journal = out / "evidence/attempt-errors.json"
    attempts = json.loads(journal.read_text(encoding="utf-8"))["attempts"] if journal.is_file() else []
    attempts.append({"preserved_at": now(), "files": [
        {"path": p.relative_to(out).as_posix(), "base64": base64.b64encode(p.read_bytes()).decode("ascii")}
        for p in sorted(files) if p != journal]})
    # Save the complete prior diagnostics before replacing their active file names.
    journal.parent.mkdir(exist_ok=True)
    from place_catalog import _save
    _save(journal, {"schema": "rcr-attempt-errors/v1", "attempts": attempts})
    for path in files:
        if path != journal:
            path.unlink()
    for path in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()


def _seal(out):
    entries = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "files.json":
            data = p.read_bytes()
            entries.append({"path": p.relative_to(out).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    save_json(out / "files.json", {"schema": "rcr-files/v1", "files": entries})
    integrity = validate_repository(out)
    if integrity["status"] != "pass":
        raise ValueError("capture file integrity failed")
    return integrity


def _move_into(staging, target, retry_failed):
    """Place a finished attempt at captures/<PlaceID>, journalling a replaceable failed attempt first."""
    _prepare_capture(target, retry_failed)
    for path in sorted((p for p in staging.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        destination = target / path.relative_to(staging)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
    shutil.rmtree(staging, ignore_errors=True)


def capture_player(pid, place_id, game, memory_budget_mib=4096, structure_cache=None, retry_failed=False):
    """Capture the Place the Player is in, which may differ from place_id after a Place transfer.

    The current Place is read from the Player's DataModel and must be one of places.json. If it differs from
    the requested Place, the request is recorded as redirected; a Place that already has a capture is skipped
    before any buffer is read. place_id may be None to capture whichever catalogue Place is open."""
    from compatibility import runtime
    from windows_collector import collect
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or (place_id is not None and (type(place_id) is not int or place_id <= 0)):
        raise ValueError("positive PID and Place ID required")
    if not 1 <= memory_budget_mib <= 65536:
        raise ValueError("memory budget must be between 1 and 65536 MiB")
    game = Path(game).resolve()
    identity, catalog = _load_game(game)
    listed = {row["id"] for row in catalog["places"]}
    if not listed or (place_id is not None and place_id not in listed):
        raise ValueError("Place ID is not in places.json; refresh the official list first")
    captures = game / "captures"
    if captures.is_symlink() or (captures.exists() and not captures.resolve().is_relative_to(game)):
        raise ValueError("capture directory must remain inside its game")
    # Fetch the official Universe identity through a Place known to belong to it.
    identity_place = place_id or (identity.get("entry_place_id") if identity.get("entry_place_id") in listed else min(listed))
    stage, decision = "process_identity", {}
    def progress(name, details=None):
        nonlocal stage
        stage = name
        print(json.dumps({"stage": name, "requested_place_id": place_id, "details": details or {}}), file=sys.stderr, flush=True)
    def select(actual):
        target = captures / str(actual)
        decision.update(place_id=actual, captured=_has_capture(target), blocked=_blocked(target, retry_failed))
        return decision["blocked"] is None
    with Session(pid) as process:
        environment = runtime(process.identity["path"])
        if not environment["compatible"]:
            raise ValueError(environment["reason"])
        staging = new_output(captures / (".attempt-%d-%d" % (pid, time.time_ns())))
        (staging / "evidence").mkdir()
        save_json(staging / "evidence/environment.json", environment)
        interrupted, dump_path = None, None
        try:
            dump_path, report = collect(pid, process.identity["path"], environment["profile"], identity_place, staging,
                                        memory_budget_mib, progress, structure_cache or game / ".structures",
                                        place_ids=sorted(listed), select=select)
            if dump_path is not None:
                dump = json.loads(dump_path.read_text(encoding="utf-8-sig"))
                if process.wait() or dump["capture"].get("universe_id") != identity["universe_id"]:
                    raise ValueError("process exited or captured Universe differs from game.json")
                if dump["capture"].get("place_id") != decision.get("place_id"):
                    raise ValueError("dump Place ID differs from the selected Place")
                report.update(schema="rcr-capture/v1", process=process.identity, universe_id=identity["universe_id"],
                              summary=dict(report["collection_result"]))
        except BaseException as exc:
            # Interrupts are recorded as failed attempts, then re-raised after the bookkeeping below.
            interrupted = None if isinstance(exc, Exception) else exc
            dump_path = True  # a failed attempt is stored, not skipped
            provisional = staging / "report.json"
            try:
                report = json.loads(provisional.read_text(encoding="utf-8-sig")) if provisional.is_file() else {"scripts": []}
            except (OSError, ValueError):
                report = {"scripts": []}
            report.update(schema="rcr-capture/v1", place_id=decision.get("place_id", place_id), universe_id=identity["universe_id"],
                          process=process.identity, summary={"status": "failed", "failure_stage": getattr(exc, "stage", stage)},
                          errors=[{"stage": getattr(exc, "stage", stage), "error": str(exc)}])
        actual = decision.get("place_id")
        if actual is not None and place_id is not None and actual != place_id:
            # Only the requested Place's entry changes; the actual Place keeps its own entry record.
            update_place_state(game, place_id, entry={"status": "redirected", "redirected_to": actual, "pid": pid,
                                                      "evidence": "Player DataModel Place ID during capture"})
        if dump_path is None:
            # Identified but not captured: the Place already has data. Only staging evidence is discarded.
            shutil.rmtree(staging, ignore_errors=True)
            return {"status": "skipped" if decision["captured"] else "blocked", "reason": decision["blocked"], "place_id": actual,
                    "requested_place_id": place_id, "path": "captures/" + str(actual)}
        # Capture reports contain observations, not recovered source records.
        save_json(staging / "report.json", report)
        target_place = actual if actual is not None else place_id
        target = captures / str(target_place) if target_place is not None else None
        problem = _blocked(target, retry_failed) if target is not None else "the current Place was not identified"
        if problem:
            # Keep the attempt under its staging name rather than touch existing data.
            integrity = _seal(staging)
            if interrupted is not None:
                raise interrupted
            return dict(report["summary"], place_id=target_place, requested_place_id=place_id,
                        path=staging.relative_to(game).as_posix(), kept_aside=problem, integrity=integrity)
        _move_into(staging, target, retry_failed)
        integrity = _seal(target)
        result = dict(report["summary"], place_id=target_place, path=target.relative_to(game).as_posix(), integrity=integrity)
        if place_id is not None and target_place != place_id:
            result["requested_place_id"] = place_id
        update_place_state(game, target_place, capture=result)
        if interrupted is not None:
            raise interrupted
        return dict(result, report=str(target / "report.json"))
