"""Single run/replay workflow; the host agent supplies Computer Use observation."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import time

from bootstrap_tools import build_tools, validate_tools
from compatibility import running_players, runtime
from network import target_id
from pipeline import extract, finish, new_output, now, save_json, validate_repository


def tools_config(path=None, directory=None):
    if path:
        return validate_tools(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    return build_tools(directory, decompiler=True)


def ui_confirmation_problem(value, place_id, started):
    if not isinstance(value, dict):
        return "observation_not_object"
    if value.get("game_scene_visible") is not True:
        return "game_scene_not_visible"
    if str(value.get("place_id")) != str(place_id):
        return "place_id_mismatch"
    window = value.get("window", {})
    if not isinstance(window, dict):
        return "window_not_object"
    if type(window.get("id")) is not int:
        return "window_id_invalid"
    if not str(window.get("app", "")).lower().endswith("robloxplayerbeta.exe"):
        return "window_app_mismatch"
    if not isinstance(value.get("observed_at"), str):
        return "observed_at_invalid"
    try:
        observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return "observed_at_timezone_missing"
        if not datetime.fromisoformat(started) <= observed <= datetime.now(timezone.utc):
            return "observation_not_fresh"
    except (KeyError, TypeError, ValueError):
        return "observed_at_invalid"
    return None


def ui_confirmation(value, place_id, started):
    return ui_confirmation_problem(value, place_id, started) is None


def wait_for_ui(path, place_id, started, timeout):
    deadline = time.monotonic() + timeout
    last_problem = None
    while time.monotonic() < deadline:
        if path.is_file() and not path.is_symlink():
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(value, dict) and value.get("terminal_error") and str(value.get("place_id")) == str(place_id):
                    raise RuntimeError("Computer Use observed entry failure: " + str(value["terminal_error"]))
                problem = ui_confirmation_problem(value, place_id, started)
                if problem is None:
                    return value
            except OSError:
                problem = "confirmation_unreadable"
            except ValueError:
                problem = "invalid_json"
            if problem != last_problem:
                print(json.dumps({"schema": "rcr-ui-confirmation/v1", "status": "rejected",
                                  "reason": problem, "path": str(path)}), file=sys.stderr, flush=True)
                last_problem = problem
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    detail = "; last rejection: " + last_problem if last_problem else ""
    raise TimeoutError("Computer Use confirmation missing, stale or invalid" + detail + "; see state.json and references/computer-use.md")


def restore(dump, out, config, capture=None, operation="run"):
    repository = extract(dump, out / "repository", tools=config)
    integrity = validate_repository(out / "repository")
    if integrity["status"] != "pass":
        raise ValueError("repository integrity failed: " + json.dumps(integrity))
    summary = dict(repository["summary"])
    summary["source_candidates"] = summary.pop("source_files")
    summary["source_files"] = summary["syntax_validated"]
    summary["semantic_equivalence"] = "not_tested"
    if capture:
        summary["capture_counts"] = capture.get("status_counts", {})
        summary["coverage"] = capture.get("coverage", {})
        c = summary["coverage"]
        summary['collection_result'] = capture.get('collection_result')
        if capture.get('collection_result', {}).get('status') in ('partial', 'failed'):
            summary['status'] = capture['collection_result']['status']
        if summary.get('status') != 'failed' and any(c.get(k) for k in ("budget_exhausted", "candidate_limit_reached", "unreadable_bulk_bytes", "discovery_deadline_reached")):
            summary["status"] = "partial"
    return {"schema": "rcr-run/v1", "operation": operation, "created_at": now(),
            "summary": summary, "repository_integrity": integrity, "extraction_report": "repository/report.json",
            "coverage": {"game_completeness": "unknown", "classes": ["LocalScript", "ModuleScript"],
                         "RunContext_Client_Script": "not_supported", "source_equivalence": "not_tested"},
            "artifacts": [], "scripts": [], "errors": []}


def run_live(target, output, tools=None, tools_dir=None, player_exe=None, pid=None,
             ui_timeout=180, memory_budget_mib=4096, structure_cache=None):
    place_id = target_id(target)
    out = new_output(output)
    (out / "evidence").mkdir()
    started = now()
    stage = "environment"
    def progress(name, details=None):
        nonlocal stage
        stage = name
        state = {"schema": "rcr-state/v1", "stage": name, "place_id": place_id,
                 "started_at": started, "updated_at": now(), "details": details or {}}
        # Readers never observe a partially written state file.
        temporary = out / "state.json.tmp"
        save_json(temporary, state)
        temporary.replace(out / "state.json")
        print(json.dumps(state), file=sys.stderr, flush=True)
    try:
        progress("environment")
        environment = runtime(player_exe)
        save_json(out / "evidence/environment.json", environment)
        if environment.get("notice"):
            progress("compatibility_notice", {"notice": environment["notice"], "compatibility_status": environment.get("compatibility_status")})
        if not environment["compatible"]:
            raise RuntimeError(environment["reason"])
        progress("building_or_checking_tools")
        config = tools_config(tools, tools_dir or out.parent / ".rcr-tools")
        save_json(out / "evidence/tools.json", config)
        uri = "roblox://experiences/start?placeId=" + str(place_id)
        confirmation = out / "ui-confirmed.json"
        save_json(out / "launch.json", {"uri": uri, "target": str(target), "place_id": place_id,
                  "confirmation_file": str(confirmation.resolve()), "started_at": started})
        progress("launching", {"uri": uri})
        if player_exe:
            # Same positional URI used by the registered Roblox handler. This
            # does not register another protocol handler or disable updates.
            executable = Path(environment['image']['path']).resolve()
            child = subprocess.Popen([str(executable), uri], cwd=str(executable.parent))
            save_json(out / 'evidence/launch-process.json', dict(pid=child.pid, executable=str(executable), argv=[str(executable), uri]))
        else:
            os.startfile(uri)
        progress("awaiting_computer_use", {"confirmation_file": str(confirmation.resolve()), "timeout_seconds": ui_timeout})
        observation = wait_for_ui(confirmation, place_id, started, ui_timeout)
        save_json(out / "evidence/computer-use.json", observation)
        progress("selecting_process")
        expected = Path(environment["image"]["path"]).resolve()
        players = [p for p in running_players() if Path(p["path"]).resolve() == expected and (pid is None or p["pid"] == pid)]
        save_json(out / "evidence/processes.json", players)
        if len(players) != 1:
            raise RuntimeError("expected one compatible Player process; rerun in a fresh output with --pid if ambiguous: " + json.dumps(players))
        from windows_collector import collect
        dump, capture = collect(players[0]["pid"], expected, environment["profile"], place_id, out,
                                memory_budget_mib, progress, structure_cache=structure_cache)
        progress("decompiling_and_validating")
        report = restore(dump, out, config, capture)
        report.update(target={"url": str(target), "place_id": place_id}, new_live_capture=True,
                      profile=environment["profile"]["id"], compatibility_status=environment.get("compatibility_status", "unverified"))
        structure = json.loads((out / 'evidence/structure-validation.json').read_text(encoding='utf-8'))
        report['structure_validation'] = dict(method=structure.get('method'), cache_status=structure.get('cache_status'),
                                               passed=structure.get('validation', {}).get('passed', False))
        progress("complete" if report['summary'].get('status') in ('pass', 'recovered') else report['summary'].get('status', 'partial'), report["summary"])
        finish(out, report, validated="repository")
        return report
    except Exception as exc:
        failure_stage = getattr(exc, "stage", stage)
        detail = getattr(exc, "detail", str(exc))
        report = {"schema": "rcr-run/v1", "operation": "run", "created_at": now(),
                  "target": {"url": str(target), "place_id": place_id}, "new_live_capture": (out / "capture/client-dump.json").is_file(),
                  "summary": {"status": "failed", "source_files": 0, "failure_stage": failure_stage},
                  "artifacts": [], "scripts": [], "errors": [{"stage": failure_stage, "error": detail}]}
        progress("failed", {"failure_stage": failure_stage, "error": detail})
        finish(out, report)
        return report


def replay_capture(source, output, tools=None, tools_dir=None):
    """Revalidate saved raw buffers, then use exactly the live restore pipeline."""
    # Unreadable inputs and tool configuration are invocation/configuration errors
    # (CLI exit 1); they are resolved before any output directory exists.
    source = Path(source).resolve()
    if (source / "capture/client-dump.json").is_file():
        dump_source, raw_dir = source / "capture/client-dump.json", source / "capture/originals"
        report_source = source / "evidence/capture.json"
    else:
        dump_source, raw_dir = source / "client-dump.json", source / "originals"
        report_source = source / "capture-report.json"
    dump = json.loads(dump_source.read_text(encoding="utf-8"))
    capture_raw = report_source.read_bytes()
    capture = json.loads(capture_raw.decode("utf-8-sig"))
    target = Path(output).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("output must be new or empty: " + str(target))
    config = tools_config(tools, tools_dir or target.parent / ".rcr-tools")
    out = new_output(target)
    (out / "evidence").mkdir()
    failures = []
    try:
        return _replay_into(out, source, dump, capture, capture_raw, raw_dir, config, failures)
    except Exception as exc:
        # Integrity or recovery failures after the output exists produce a terminal report (exit 2).
        stage = getattr(exc, "stage", "replay")
        report = {"schema": "rcr-run/v1", "operation": "replay", "created_at": now(), "new_live_capture": False,
                  "replay_source": str(source), "replay_errors": failures,
                  "summary": {"status": "failed", "source_files": 0, "failure_stage": stage},
                  "artifacts": [], "scripts": [], "errors": [{"stage": stage, "error": str(exc)}]}
        finish(out, report)
        return report


class StageError(ValueError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def _replay_into(out, source, dump, capture, capture_raw, raw_dir, config, failures):
    from rsb1 import unwrap
    # A replay is itself a reusable run input. Keep the original observations
    # byte-for-byte, including unavailable instances and collection limits.
    (out / "evidence/capture.json").write_bytes(capture_raw)
    records = {hex(r["address"]): r for r in capture["scripts"]}
    target_dir = out / "capture/originals"
    target_dir.mkdir(parents=True)
    for node in dump["instances"]:
        # The dump is an inventory, not an alternate code source for replay.
        # Reconstruct code exclusively from the content-addressed raw buffer.
        for field in ("source", "bytecode", "bytecode_base64", "bytecode_format"):
            node.pop(field, None)
        record = records.get(node["id"])
        if not record or not record.get("raw_sha256"):
            if record:
                node["capture_errors"] = [record.get("error", "buffer unavailable in original capture")]
            elif node.get("class") in {"LocalScript", "ModuleScript"}:
                node["capture_errors"] = ["script absent from original capture report"]
            continue
        digest = record["raw_sha256"]
        try:
            # Only content-addressed local names are opened; embedded paths are never trusted.
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid raw content hash")
            raw = raw_dir / (digest + ".roblox-buffer")
            if raw.is_symlink():
                raise ValueError("raw input is a symlink")
            encoded = raw.read_bytes()
            if hashlib.sha256(encoded).hexdigest() != digest:
                raise ValueError("raw capture hash mismatch")
            shutil.copy2(raw, target_dir / raw.name)
        except Exception as exc:
            node["capture_errors"] = [str(exc)]
            failures.append({"id": node["id"], "error": str(exc)})
            continue
        if record.get("status") != "roblox_bytecode_captured":
            # Replay repeats the live decision: only buffers the collector approved as
            # Roblox bytecode are decoded. The original stays preserved for inspection.
            node["capture_errors"] = [record.get("error") or "recorded as %s at capture; not decoded" % record.get("status")]
            continue
        try:
            decoded, metadata = unwrap(encoded)
            expected = record.get("bytecode_sha256")
            if expected and hashlib.sha256(decoded).hexdigest() != expected:
                raise ValueError("decoded payload hash mismatch")
            import base64
            node.update(bytecode_base64=base64.b64encode(decoded).decode(), bytecode_format="roblox")
            node.setdefault("metadata", {}).update(raw_sha256=digest, rsb1=metadata,
                    source_capture=str(source), source_origin="actual Player capture replay; no new live acquisition")
        except Exception as exc:
            node.pop("bytecode_base64", None)
            node["capture_errors"] = [str(exc)]
            failures.append({"id": node["id"], "error": str(exc)})
    dump.setdefault("capture", {})["replay"] = {"source": str(source), "created_at": now(), "new_live_capture": False}
    dump_path = out / "capture/client-dump.json"
    save_json(dump_path, dump)
    save_json(out / "evidence/replay-errors.json", failures)
    save_json(out / "evidence/tools.json", config)
    try:
        report = restore(dump_path, out, config, capture, operation="replay")
    except Exception as exc:
        raise StageError("restore", str(exc)) from exc
    report.update(new_live_capture=False, replay_source=str(source), replay_errors=failures)
    finish(out, report, validated="repository")
    return report
