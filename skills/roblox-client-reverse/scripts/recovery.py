"""One recovery entry point: raw captures, a game catalog, or supplied offline files."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import tempfile

import pipeline
from bootstrap_tools import build_tools, validate_tools
from capture_store import read_capture
from formats import VERSION
from place_catalog import _load_game, _save


def tools_config(path=None, directory=None, require_pair=True):
    if path:
        return validate_tools(json.loads(Path(path).read_text(encoding="utf-8-sig")), require_pair=require_pair)
    return build_tools(directory, decompiler=True)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _inside(root, relative):
    value = PureWindowsPath(relative)
    path = root / relative
    if value.drive or value.is_absolute() or ".." in value.parts or path.is_symlink() or root not in path.resolve().parents:
        raise ValueError("unsafe repository/capture reference: " + relative)
    return path


def _context(tools):
    # A tool/config/adapter/verifier change invalidates cross-run reuse.
    files = ["pipeline.py", "recovery.py", "capture_store.py", "bootstrap_tools.py", "cfg_verify.py",
             "cfg_normalize.py", "cfg_diagnose.py", "formats.py", "client_import.py", "rsb1.py"]
    content = {"version": VERSION, "tools": tools,
               "scripts": {name: _sha(Path(__file__).with_name(name).read_bytes()) for name in files}}
    return {"sha256": _sha(json.dumps(content, sort_keys=True).encode()), "configuration": content}


def _key(node):
    return (node.get("bytecode_format", "unknown"), _sha(node["bytecode"])) if "bytecode" in node else None


def _variant_path(path, code, place, shared_place, occupied, prior):
    prefix = path[-1] + "@" + str(place)
    hashed = prefix + "-" + _sha(json.dumps(code).encode())[:12]
    if prior:
        previous = tuple(prior["module_path"])
        if (previous[:-1] == path[:-1] and previous not in occupied
                and (previous[-1] == prefix or previous[-1].startswith(prefix + "-"))):
            occupied.add(previous)
            return previous
    label = hashed if shared_place else prefix
    candidate = (*path[:-1], label)
    if candidate in occupied:
        candidate = (*path[:-1], hashed)
    ordinal = 2
    while candidate in occupied:
        candidate = (*path[:-1], f"{hashed}-{ordinal}")
        ordinal += 1
    occupied.add(candidate)
    return candidate


def _modules(captures, previous_variants=None):
    grouped, missing, variants = defaultdict(dict), defaultdict(list), []
    for capture in captures:
        place = capture["capture"].get("place_id")
        for node in capture["scripts"]:
            path = tuple(node["logical_path"])
            key = _key(node)
            if key is None:
                missing[path].append({"place_id": place, "id": node["id"], "class": node["class"],
                                      "scope": node["scope"], "errors": node.get("capture_errors", [])})
                continue
            group = grouped[path].setdefault((node["class"], *key), {"node": node, "places": set()})
            if type(place) is int and place > 0:
                group["places"].add(place)
    old = {(tuple(v["path"]), v["class"], v["format"], v["sha256"]): v for v in previous_variants or []}
    natural_paths = set(grouped) | set(missing)
    occupied = {path[:length] for path in natural_paths for length in range(1, len(path) + 1)}
    result = []
    for path in sorted(natural_paths):
        groups = grouped.get(path, {})
        if not groups:
            # No code equivalence claim is made for unavailable observations.
            result.append({"id": "unavailable-" + _sha(json.dumps(path).encode())[:16], "class": missing[path][0]["class"],
                           "logical_path": list(path), "scope": missing[path][0]["scope"],
                           "capture_errors": [e for row in missing[path] for e in row["errors"]], "metadata": {}})
            continue
        representatives = {}
        for key, group in groups.items():
            prior = old.get((path, *key))
            available = sorted(group["places"])
            if not available:
                raise ValueError("capture code requires a positive Place ID")
            representatives[key] = prior["representative_place_id"] if prior and prior["representative_place_id"] in available else available[0]
        counts = Counter(representatives.values())
        for key, group in sorted(groups.items()):
            node = deepcopy(group["node"])
            node["logical_path"] = list(path)
            # Detailed per-instance provenance remains in the immutable raw captures.
            node["metadata"] = {"bytecode_sha256": key[2]}
            node.pop("capture_errors", None)
            if len(groups) > 1:
                representative = representatives[key]
                node["logical_path"] = list(_variant_path(path, key, representative, counts[representative] > 1,
                                                         occupied, old.get((path, *key))))
                row = {"path": list(path), "module_path": node["logical_path"], "class": key[0], "format": key[1],
                       "sha256": key[2], "representative_place_id": representative, "place_ids": sorted(group["places"])}
                variants.append(row)
                node["metadata"]["variant"] = row
            result.append(node)
    return result, variants, [{"path": list(path), "observations": rows} for path, rows in sorted(missing.items())]


def _record_identity(record):
    bytecode = record.get("bytecode", {})
    original_path = record.get("metadata", {}).get("variant", {}).get("path", record["instance_path"])
    return tuple(original_path), record["class"], bytecode.get("format"), bytecode.get("sha256")


def _reuse(previous, record, node, out, audits):
    if not previous:
        return False
    base, old = previous
    source = _inside(base, old["source"])
    bytecode = _inside(base, old["bytecode"]["path"])
    if _sha(source.read_bytes()) != old["source_sha256"] or bytecode.read_bytes() != node["bytecode"]:
        return False
    old_directory = _inside(base, old["directory"])
    directory = out / record["directory"]
    shutil.copytree(old_directory, directory, ignore=shutil.ignore_patterns("cfg-audit"))
    names = ("recovery", "source_origin", "source_sha256", "decompilation", "validation", "verification", "recovery_warnings", "reason")
    for name in names:
        if name in old:
            record[name] = deepcopy(old[name])
    record["verification"]["behavior"] = "NOT_TESTED"  # Static reuse never transfers an execution-context claim.
    record["source"] = (directory / "init.luau").relative_to(out).as_posix()
    record["bytecode"] = dict(old["bytecode"], path=(directory / "original.bytecode").relative_to(out).as_posix())
    if old.get("cfg_audit"):
        audit = deepcopy(old["cfg_audit"])
        if audit.get("evidence"):
            audit_key = (*_key(node), old["source_sha256"])
            if audit_key not in audits:
                origin = _inside(base, audit["evidence"])
                destination = directory / "cfg-audit"
                shutil.copytree(origin, destination)
                audits[audit_key] = destination.relative_to(out).as_posix()
            audit["evidence"] = audits[audit_key]
        record["cfg_audit"] = audit
    record["recovery_reused"] = True
    return True


def _build(captures, out, tools, previous=None, game_state=None):
    out = pipeline.new_output(out)
    context = _context(tools)
    old_report, old_variants, reusable = {}, [], {}
    if previous and previous.exists():
        if not (previous / "files.json").is_file():
            raise ValueError("existing repository has no files.json; move it aside to rebuild")
        integrity = pipeline.validate_repository(previous)
        if integrity["status"] != "pass":
            raise ValueError("existing repository integrity failed; refusing to reuse or replace it")
        old_report = json.loads((previous / "report.json").read_text(encoding="utf-8-sig"))
        if (previous / "variants.json").is_file():
            old_variants = json.loads((previous / "variants.json").read_text(encoding="utf-8-sig"))["variants"]
        if old_report.get("recovery_context", {}).get("sha256") == context["sha256"]:
            for row in old_report.get("scripts", []):
                if row.get("source") and row.get("recovery") == "decompiled_syntax_valid" and not row.get("cfg_audit", {}).get("failure"):
                    b = row.get("bytecode", {})
                    reusable[(b.get("format"), b.get("sha256"))] = (previous, row)
    nodes, variants, missing = _modules(captures, old_variants)
    approved = {(tuple(n.get("metadata", {}).get("variant", {}).get("path", n["logical_path"])), n["class"], *_key(n))
                for n in nodes if _key(n)}
    for old in old_report.get("scripts", []):
        if old.get("source") and old.get("bytecode"):
            if _record_identity(old) not in approved:
                raise ValueError("previously recovered code no longer has a validated original; existing repository retained")
    records, pending, cache, reused_audits = [], [], {}, {}
    for node in nodes:
        logical = node["logical_path"]
        key = _key(node)
        uid = _sha(json.dumps([logical, node["class"], key], sort_keys=True).encode())[:16]
        name = pipeline.safe_component(logical[-1]).replace("%40", "@")[:64]
        record = {"id": uid, "class": node["class"], "scope": node["scope"], "instance_path": logical,
                  "directory": "src/" + name + "--" + uid, "metadata": node.get("metadata", {}),
                  "recovery": "processing_failed", "recovery_warnings": [],
                  "verification": {"syntax": "NOT_CHECKED", "cfg": "NOT_CHECKED", "behavior": "NOT_TESTED"}}
        if node.get("capture_errors"):
            record["capture_errors"] = node["capture_errors"]
        records.append(record)
        if not _reuse(reusable.get(key), record, node, out, reused_audits):
            pending.append((node, record))
    pipeline.recover_all(pending, out, tools, None, cache)
    if variants:
        pipeline.save_json(out / "variants.json", {"schema": "rcr-variants/v1", "variants": variants})
    errors = [{"capture": str(c["source"]), **row} for c in captures for row in c["errors"]]
    status = "recovered" if any(r.get("source") for r in records) else "no_source"
    if errors or any(c["report"].get("summary", c["report"].get("collection_result", {})).get("status") in {"partial", "failed"} for c in captures):
        status = "partial" if status == "recovered" else "failed"
    if any(r.get("recovery") not in {"decompiled_syntax_valid", "source_present", "empty_source"} or r.get("recovery_warnings") or
           r.get("verification", {}).get("cfg") == "CFG_MISMATCH" for r in records):
        status = "partial" if any(r.get("source") for r in records) else "failed"
    old_records = old_report.get("scripts", [])
    old_paths = {(tuple(r["instance_path"]), _record_identity(r)) for r in old_records}
    current_by_identity = {_record_identity(r): r for r in records}
    remaps = []
    for previous in sorted(old_records, key=lambda row: row["instance_path"]):
        identity = _record_identity(previous)
        row = current_by_identity.get(identity)
        if identity[-1] and row and previous["instance_path"] != row["instance_path"]:
            remaps.append({"before": previous["instance_path"], "after": row["instance_path"], "bytecode_sha256": identity[-1],
                           "before_source_sha256": previous.get("source_sha256"), "source_sha256": row.get("source_sha256")})
    summary = {"status": status, "scripts": len(records), "source_files": sum("source" in r for r in records),
               "syntax_validated": sum(r.get("verification", {}).get("syntax") == "SYNTAX_VALID" for r in records),
               "unique_bytecode_payloads": len({_key(n) for n in nodes if _key(n)}),
               "reused_modules": sum(r.get("recovery_reused", False) for r in records),
               "recovery_counts": dict(Counter(r["recovery"] for r in records)), "captured_places": len(captures),
               "variant_modules": len(variants), "unavailable_observations": sum(len(x["observations"]) for x in missing),
               "semantic_equivalence": "not_tested"}
    summary["verification_counts"] = {stage: dict(Counter(r["verification"][stage] for r in records))
                                      for stage in ("syntax", "cfg", "behavior")}
    report = {"schema": "rcr-report/v1", "operation": "recover", "created_at": pipeline.now(), "summary": summary,
              "scripts": records, "artifacts": [], "errors": errors, "recovery_context": context,
              "unavailable_instances": missing, "changes": {"citation_remaps": remaps,
                  "added_modules": [r["instance_path"] for r in records if (tuple(r["instance_path"]), _record_identity(r)) not in old_paths]},
              "coverage": {"game_completeness": "unknown", "behavior": "NOT_TESTED"}}
    if game_state:
        report["capture_failures"] = game_state["capture_failures"]
        summary.update(game_state["summary"])
        if report["capture_failures"] and summary["status"] == "recovered":
            summary["status"] = "partial"
    pipeline.finish(out, report)
    if pipeline.validate_repository(out)["status"] != "pass":
        raise ValueError("new repository integrity failed")
    return report


PREVIOUS = ".repository-previous"


def _restore_interrupted_swap(game, repository):
    """A swap stopped between its two renames leaves only the previous repository; put it back."""
    old = game / PREVIOUS
    if old.is_dir() and not old.is_symlink():
        if repository.exists():
            shutil.rmtree(old, ignore_errors=True)  # the swap completed; only cleanup was missed
        else:
            old.rename(repository)


def _saved(source):
    return (source / "client-dump.json").is_file() or (source / "capture/client-dump.json").is_file()


def recover_capture(source, output, tools=None, tools_dir=None):
    captured = read_capture(source)  # Validate original buffers before invoking tools.
    target = Path(output).resolve()
    if target == captured["source"] or captured["source"] in target.parents:
        raise ValueError("recovery output cannot be inside the raw capture")
    config = tools_config(tools, tools_dir or target.parent / ".rcr-tools")
    out = pipeline.new_output(target)
    try:
        report = _build([captured], out / "repository", config)
    except Exception as exc:
        result = {"schema": "rcr-recovery/v1", "operation": "recover", "created_at": pipeline.now(),
                  "summary": {"status": "failed", "source_files": 0, "failure_stage": "recovery"},
                  "scripts": [], "artifacts": [], "recovery_errors": captured["errors"],
                  "errors": [{"stage": "recovery", "error": str(exc)}]}
        pipeline.finish(out, result)
        return result
    result = {"schema": "rcr-recovery/v1", "operation": "recover", "created_at": pipeline.now(),
              "summary": report["summary"], "extraction_report": "repository/report.json", "scripts": [], "artifacts": [],
              "recovery_errors": report["errors"], "errors": report["errors"], "input": str(captured["source"])}
    _save(out / "recovery.json", result)
    return result


def recover_game(game, tools=None, tools_dir=None):
    game = Path(game).resolve()
    identity, catalog = _load_game(game)
    captures, failed_captures = [], []
    for place in catalog["places"]:
        relative = place.get("capture", {}).get("path")
        if relative is None:
            continue
        if relative != "captures/" + str(place["id"]):
            raise ValueError("capture path does not match its Place ID")
        path = _inside(game, relative)
        if not (path / "report.json").is_file() or not (path / "files.json").is_file():
            failed_captures.append({"place_id": place["id"], "path": relative, "errors": [
                {"stage": "unfinished", "error": "capture did not finish (no report.json/files.json); retry the capture"}]})
            continue
        status = json.loads((path / "report.json").read_text(encoding="utf-8-sig")).get("summary", {}).get("status")
        if status == "failed":
            # A failed attempt may still hold a dump (for example after the Player exited); it is never merged.
            failed = json.loads((path / "report.json").read_text(encoding="utf-8-sig"))
            failed_captures.append({"place_id": place["id"], "path": relative, "errors": failed.get("errors", []),
                                    "integrity": pipeline.validate_repository(path)["status"]})
            continue
        capture = read_capture(path)
        if capture["capture"].get("place_id") != place["id"] or capture["capture"].get("universe_id") != identity["universe_id"]:
            raise ValueError("capture Place/Universe does not match the game catalog")
        captures.append(capture)
    if not captures:
        raise ValueError("no raw Place captures are available")
    config = tools_config(tools, tools_dir or game / ".tools")
    repository = game / "repository"
    if repository.is_symlink() or repository.resolve().parent != game:
        raise ValueError("repository must remain inside its game")
    _restore_interrupted_swap(game, repository)
    temporary = tempfile.mkdtemp(prefix=".recovery-", dir=game)
    try:
        stage = Path(temporary) / "repository"
        captured_ids = {c["capture"]["place_id"] for c in captures}
        state = {"capture_failures": failed_captures, "summary": {"known_places": len(catalog["places"]),
                 "uncollected_places": [p["id"] for p in catalog["places"] if p["id"] not in captured_ids]}}
        report = _build(captures, stage, config, repository, state)
        old = game / PREVIOUS
        if repository.exists():
            repository.rename(old)
        try:
            stage.rename(repository)
        except BaseException:
            if old.exists() and not repository.exists():
                old.rename(repository)  # if this also fails, the previous repository stays at PREVIOUS
            raise
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    _save(game / "recovery.json", {"summary": report["summary"], "changes": report["changes"], "updated_at": pipeline.now()})
    return report


def recover_input(source, output=None, tools=None, tools_dir=None, include_server=False, bytecode_format=None):
    if Path(source).is_symlink():
        raise ValueError("recovery input must not be a symlink")
    source = Path(source).resolve()
    if not source.exists():
        raise ValueError("recovery input does not exist")
    if source.is_dir() and (source / "game.json").is_file():
        if output is not None:
            raise ValueError("recover GAME writes GAME/repository; omit --out")
        return recover_game(source, tools, tools_dir)
    if output is None:
        raise ValueError("--out is required for a supplied file or capture")
    if source.is_dir() and _saved(source):
        return recover_capture(source, output, tools, tools_dir)
    target = Path(output).resolve()
    if source.is_dir() and (source == target or source in target.parents):
        raise ValueError("recovery output cannot be inside input")
    if target.exists() and any(target.iterdir()):
        raise ValueError("output must be new or empty")
    config = tools_config(tools, tools_dir or target.parent / ".rcr-tools", require_pair=False)
    out = pipeline.new_output(target)
    report = pipeline.extract(source, out / "repository", config, include_server, bytecode_format)
    result = {"schema": "rcr-recovery/v1", "operation": "recover", "created_at": pipeline.now(),
              "summary": report["summary"], "scripts": [], "artifacts": [], "extraction_report": "repository/report.json",
              "recovery_errors": report["errors"]}
    _save(out / "recovery.json", result)
    return result


def validate_path(source):
    source = Path(source).resolve()
    if not (source / "game.json").is_file():
        repository = source / "repository"
        return pipeline.validate_repository(repository if (repository / "files.json").is_file() else source)
    identity, catalog = _load_game(source)
    checks = []
    for place in catalog["places"]:
        relative = place.get("capture", {}).get("path")
        if relative:
            if relative != "captures/" + str(place["id"]):
                raise ValueError("capture path does not match Place ID")
            directory = _inside(source, relative)
            checked = dict(path=relative, **pipeline.validate_repository(directory))
            dump = directory / "client-dump.json"
            if dump.exists():
                header = json.loads(dump.read_text(encoding="utf-8-sig")).get("capture", {})
                if header.get("place_id") != place["id"] or header.get("universe_id") != identity["universe_id"]:
                    checked["status"] = "failed"
                    checked["failures"].append("capture Place/Universe does not match catalog")
            checks.append(checked)
    if (source / "repository").is_dir():
        checks.append(dict(path="repository", **pipeline.validate_repository(source / "repository")))
    return {"status": "pass" if checks and all(c["status"] == "pass" for c in checks) else "failed", "checks": checks,
            "meaning": "File integrity only; entry, collection coverage, CFG and behavior remain separate."}
