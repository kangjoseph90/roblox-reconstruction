"""Offline extraction, provenance, tool adapters, and browsable reports."""
import collections
from concurrent.futures import ProcessPoolExecutor
import datetime
import html
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote

from formats import MAX_BYTES, MAX_NODES, SCRIPT_CLASSES, parse_artifact, sha
from client_import import adapt

EXTENSIONS = {".rbxl", ".rbxm", ".rbxlx", ".rbxmx", ".lua", ".luau", ".json", ".luac", ".bc", ".bytecode"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


# A compile failure is a definite result, distinct from a check that never ran.
SYNTAX_STATES = {"syntax_pass": "SYNTAX_VALID", "syntax_failed": "SYNTAX_INVALID", "not_configured": "NOT_CHECKED"}


def new_output(path):
    path = Path(path).resolve()
    if path.exists() and any(path.iterdir()):
        raise ValueError("output must be new or empty: " + str(path))
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_component(name):
    # Percent escaping is reversible for names; node identity prevents duplicate/case collisions.
    v = quote(name, safe="-_ ")[:90].rstrip(" .") or "unnamed"
    if v in {".", ".."} or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", v.upper().split(".")[0]):
        v = "_" + v
    return v


def metadata_value(node, key, default=None):
    v = node.get("metadata", {}).get(key, default)
    if isinstance(v, dict) and "text" in v:
        return v["text"]
    return v


def hierarchy(nodes):
    by_id = {}
    for n in nodes:
        if n["id"] in by_id:
            raise ValueError("duplicate instance ID: " + n["id"])
        by_id[n["id"]] = n
    paths = {}
    for n in nodes:
        chain, seen, cur = [], set(), n
        while cur is not None and cur["id"] not in paths:
            if cur["id"] in seen:
                raise ValueError("cyclic instance hierarchy")
            seen.add(cur["id"])
            chain.append(cur)
            if len(chain) > 256:
                raise ValueError("instance hierarchy exceeds 256 levels")
            parent = cur.get("parent")
            if parent is not None and parent not in by_id:
                raise ValueError("dangling parent reference: " + parent)
            cur = by_id.get(parent)
        prefix = paths[cur["id"]][:] if cur else []
        for item in reversed(chain):
            prefix = prefix + [item["name"]]
            paths[item["id"]] = prefix
    return paths


def scope(n, path, fmt, by_id):
    if fmt in {"source-file", "opaque-bytecode-file"}:
        return "supplied-file-unknown-scope"
    ancestor = by_id.get(n.get("parent"))
    while ancestor:
        if ancestor["class"] in {"ServerStorage", "ServerScriptService"}:
            return "server-only"
        ancestor = by_id.get(ancestor.get("parent"))
    if n["class"] == "LocalScript":
        return "client-candidate"
    if n["class"] == "ModuleScript":
        # Replication and use cannot be proven from a static place file.
        return "shared-or-client-candidate"
    if n["class"] == "Script":
        context = str(metadata_value(n, "RunContext", 0))
        return "client-candidate" if context in {"2", "Client", "Enum.RunContext.Client"} else "server-or-plugin"
    return "non-script"


_EXECUTABLE_HASHES = {}


def executable_sha256(path):
    """Hash a tool executable once per process unless the file changes on disk."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _EXECUTABLE_HASHES:
        _EXECUTABLE_HASHES[key] = sha(path.read_bytes())
    return _EXECUTABLE_HASHES[key]


def tool_run(config, source, destination=None):
    """Explicit local executable configuration only. No shell and no game execution."""
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("tool argv must be a nonempty string array")
    args = [x.replace("{input}", str(source)).replace("{output}", str(destination or "")) for x in argv]
    executable = shutil.which(args[0])
    executable_hash = executable_sha256(Path(executable)) if executable and Path(executable).is_file() else None
    timeout = min(max(float(config.get("timeout", 30)), 1), 120)
    try:
        # Pipes let communicate() wait with select() instead of a sleep/poll loop.
        p = subprocess.run(args, capture_output=True, timeout=timeout, shell=False)
        code = p.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "tool_error", "error": str(e), "argv": args}, b""
    output = p.stdout[:MAX_BYTES + 1]
    diagnostic = p.stderr[:65536].decode("utf-8", "replace")
    result = {"status": "ok" if code == 0 else "tool_failed", "exit_code": code,
              "argv": args, "stderr": diagnostic, "tool_revision": config.get("revision", "unrecorded"),
              "executable_sha256": executable_hash}
    if config.get("output", "stdout") == "file" and code == 0:
        if destination is None or not destination.is_file() or destination.is_symlink():
            return dict(result, status="tool_error", error="tool did not create a regular output file"), b""
        if destination.stat().st_size > MAX_BYTES:
            return dict(result, status="tool_error", error="tool output exceeds limit"), b""
        output = destination.read_bytes()
    if len(output) > MAX_BYTES:
        return dict(result, status="tool_error", error="tool output exceeds limit"), b""
    return result, output


def compile_check(path, tools):
    if not tools.get("compiler"):
        return {"status": "not_configured", "semantic_equivalence": "not_tested"}
    result, output = tool_run(tools["compiler"], path)
    result["status"] = "syntax_pass" if result["status"] == "ok" else "syntax_failed"
    result["semantic_equivalence"] = "not_tested"
    return result


def recover(n, record, out, tools, bytecode_format, cache=None):
    cache = cache if cache is not None else {}
    source = n.get("source")
    bytecode = n.get("bytecode")
    folder = out / record["directory"]
    folder.mkdir(parents=True, exist_ok=True)
    if "opaque_source" in n:
        p = folder / "Source.opaque"
        p.write_bytes(n["opaque_source"])
        record["opaque_source"] = p.relative_to(out).as_posix()
    if bytecode is not None:
        raw_path = folder / "original.bytecode"
        raw_path.write_bytes(bytecode)
        record["bytecode"] = {"path": raw_path.relative_to(out).as_posix(), "sha256": sha(bytecode),
            "bytes": len(bytecode), "format": bytecode_format or n.get("bytecode_format", "unknown"),
            "header_byte": bytecode[0] if bytecode else None,
            "type_version_candidate": bytecode[1] if len(bytecode) > 1 and bytecode[0] >= 4 else None}
    if source is not None and (source or not bytecode):
        record["recovery"] = "source_present" if source else "empty_source"
        record["source_origin"] = n.get("source_origin", "supplied_artifact; authenticity_unverified")
    elif bytecode is not None:
        info = record["bytecode"]
        config = tools.get("decompiler")
        if info["format"] not in {"luau", "roblox"}:
            record["recovery"] = "opaque_bytecode"
            record["reason"] = "encoding not declared vanilla Luau; no wrapper/opcode transformation assumed"
            return
        if not bytecode or bytecode[0] == 0:
            record["recovery"] = "invalid_bytecode_header"
            return
        if not config:
            record["recovery"] = "decompiler_not_configured"
            return
        if info["format"] == "roblox":
            if not config.get("roblox_argv"):
                record["recovery"] = "unsupported_bytecode_encoding"
                record["reason"] = "Roblox bytecode requires an explicit roblox_argv adapter"
                return
            config = dict(config, argv=config["roblox_argv"])
        if bytecode[0] not in config.get("bytecode_versions", []):
            record["recovery"] = "unsupported_bytecode_version"
            return
        if config.get("type_versions") and info["type_version_candidate"] not in config["type_versions"]:
            record["recovery"] = "unsupported_type_version"
            return
        key = ("decompile", info["format"], info["sha256"], json.dumps(config, sort_keys=True))
        if key in cache:
            result, recovered, first = cache[key]
            record["duplicate_bytecode_of"] = first
        else:
            with tempfile.TemporaryDirectory(prefix="rcr-decompile-") as tmp:
                result, recovered = tool_run(config, raw_path.resolve(), Path(tmp) / "decompiled.luau")
            cache[key] = (result, recovered, record["id"])
        record["decompilation"] = result
        (folder / "decompiler.stdout").write_bytes(recovered)
        if result["status"] != "ok" or not recovered.strip():
            record["recovery"] = "decompilation_failed"
            return
        try:
            source = recovered.decode("utf-8")
        except UnicodeDecodeError:
            record["recovery"] = "decompilation_nontext"
            return
        record["recovery"] = "decompiled_candidate"
        record["source_origin"] = "decompiler_output; semantics_unverified"
    else:
        record["recovery"] = "source_unavailable"
        return
    # Source remains byte-for-byte in original artifact; only newline style is normalized here.
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    path = folder / "init.luau"
    path.write_bytes(source.encode("utf-8"))
    record["source"] = path.relative_to(out).as_posix()
    record["source_sha256"] = sha(path.read_bytes())
    key = ("compile", record["source_sha256"], json.dumps(tools.get("compiler", {}), sort_keys=True))
    if key in cache:
        record["validation"], first = cache[key]
        record["syntax_check_reused_from"] = first
    else:
        record["validation"] = compile_check(path.resolve(), tools)
        cache[key] = (record["validation"], record["id"])
    if record["validation"]["status"] == "syntax_failed":
        record["recovery"] = "invalid_decompilation" if record["recovery"] == "decompiled_candidate" else "invalid_source"
    if record["recovery"] == "decompiled_candidate" and record["validation"]["status"] == "syntax_pass":
        record["recovery"] = "decompiled_syntax_valid"
    record["verification"] = {"syntax": SYNTAX_STATES.get(record["validation"]["status"], "SYNTAX_UNVERIFIED"),
                              "cfg": "NOT_CHECKED", "behavior": "NOT_TESTED"}
    record["recovery_warnings"] = [line.strip() for line in source.splitlines() if "-- luaudec:" in line]
    if (record["recovery"] == "decompiled_syntax_valid" and tools.get("decompiler", {}).get("cfg_audit")):
        audit_key = ("cfg", info["format"], info["sha256"], record["source_sha256"])
        if audit_key not in cache:
            try:
                from cfg_verify import audit_source
                report = audit_source(raw_path.resolve(), path.resolve(), tools, info["format"], folder / "cfg-audit")
                audit = {"status": report["status"], "evidence": (folder / "cfg-audit").relative_to(out).as_posix(),
                         "function_summary": report.get("function_summary", {}),
                         "module_scope": "all reachable referenced prototypes required; standalone function matches do not certify a module",
                         "matched_functions": sum(f["status"] == "CFG_VALIDATED" for f in report["functions"]),
                         "total_functions": report["original_proto_count"], "best_optimization": report["optimization"]}
            except Exception as exc:
                from cfg_verify import ToolFailure
                # The status stays UNVERIFIED (nothing was proved), but a tool failure is not
                # an inconclusive comparison: record which one occurred.
                audit = {"status": "UNVERIFIED", "error": str(exc),
                         "failure": "tool_error" if isinstance(exc, ToolFailure) else "audit_error"}
            cache[audit_key] = audit
        record["cfg_audit"] = cache[audit_key]
        record["verification"]["cfg"] = record["cfg_audit"]["status"]


# Replaceable in tests; process pool because CFG comparison is CPU-bound Python.
EXECUTOR = ProcessPoolExecutor


def default_workers():
    """RCR_WORKERS overrides the worker count; 1 restores fully sequential recovery."""
    configured = os.environ.get("RCR_WORKERS")
    return max(1, int(configured)) if configured else (os.cpu_count() or 1)


def _recover_job(n, record, out, tools, bytecode_format):
    cache = {}
    recover(n, record, out, tools, bytecode_format, cache)
    return record, cache


def _merge_job(record, done, local, cache):
    """Fold one worker's result in input order, reproducing sequential cache reuse."""
    done = dict(done)  # in-process executors may return the very object being replaced
    for key, value in local.items():
        if key[0] == "compile" and key in cache:
            # An earlier script already produced this exact source: reuse its syntax check,
            # exactly as the sequential cache would have.
            validation, first = cache[key]
            rebuilt = {}
            for name, item in done.items():
                rebuilt[name] = validation if name == "validation" else item
                if name == "validation":
                    rebuilt["syntax_check_reused_from"] = first
            done = rebuilt
        cache.setdefault(key, value)
    record.clear()
    record.update(done)


def recover_all(items, out, tools, bytecode_format, cache, workers=None):
    """Recover scripts in input order. The first script of each distinct bytecode runs in a
    worker process; later duplicates reuse its cached results sequentially, so reports and
    files match a sequential run."""
    firsts, seen = [], set()
    for index, (n, _) in enumerate(items):
        if n.get("bytecode") is not None and not n.get("source"):
            key = (sha(n["bytecode"]), bytecode_format or n.get("bytecode_format", "unknown"))
            if key not in seen:
                seen.add(key)
                firsts.append(index)
    workers = default_workers() if workers is None else workers
    if workers <= 1 or len(firsts) < 2:
        for n, record in items:
            recover(n, record, out, tools, bytecode_format, cache)
        return
    with EXECUTOR(max_workers=min(workers, len(firsts))) as pool:
        futures = {i: pool.submit(_recover_job, items[i][0], items[i][1], out, tools, bytecode_format) for i in firsts}
        for index, (n, record) in enumerate(items):
            if index in futures:
                _merge_job(record, *futures[index].result(), cache)
            else:
                recover(n, record, out, tools, bytecode_format, cache)


def write_index(out, report):
    rows = []
    for n in report.get("scripts", []):
        label = html.escape(" / ".join(n["instance_path"]))
        target = n.get("source") or n.get("bytecode", {}).get("path")
        if target:
            label = '<a href="' + html.escape(quote(target, safe="/"), quote=True) + '">' + label + "</a>"
        rows.append("<tr><td>" + label + "</td><td>" + html.escape(n["class"]) + "</td><td>" +
                    html.escape(n["scope"]) + "</td><td>" + html.escape(n["recovery"]) + "</td><td>" +
                    html.escape(" / ".join(n.get("verification", {}).values())) + "</td></tr>")
    recovered_link = '<p><a href="repository/index.html">Browse extracted repository</a></p>' if report.get("extraction_report") else ""
    content = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Roblox artifact recovery</title><style>body{font:16px system-ui;margin:3rem;max-width:1200px;background:#101820;color:#edf2f7}a{color:#8fd4ff}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.7rem;border-bottom:1px solid #456}pre{white-space:pre-wrap}</style>
<h1>Roblox artifact recovery</h1><p>Coverage applies to supplied artifacts. It does not establish complete game-source recovery.</p>
<p><a href="report.json">Machine-readable report</a> · <a href="instances.json">Instance inventory</a> · <a href="files.json">File integrity manifest</a></p>
""" + recovered_link + "<pre>" + html.escape(json.dumps(report.get("summary", {}), indent=2)) + "</pre>" + \
        "<table><tr><th>Instance path</th><th>Class</th><th>Scope estimate</th><th>Recovery</th><th>Verification</th></tr>" + "".join(rows) + "</table></html>"
    (out / "index.html").write_text(content, encoding="utf-8")


def finish(out, report, instances=None, validated=None):
    """Write the root manifest. `validated` names a subdirectory whose files.json was just
    checked by validate_repository; its entries are reused instead of rehashing every file."""
    save_json(out / "report.json", report)
    save_json(out / "instances.json", instances or [])
    write_index(out, report)
    known = {}
    if validated:
        listed = json.loads((out / validated / "files.json").read_text(encoding="utf-8-sig"))["files"]
        known = {validated + "/" + item["path"]: (item["bytes"], item["sha256"]) for item in listed}
    entries = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p != out / "files.json":
            rel = p.relative_to(out).as_posix()
            if rel in known and p.stat().st_size == known[rel][0]:
                entries.append({"path": rel, "bytes": known[rel][0], "sha256": known[rel][1]})
                continue
            b = p.read_bytes()
            entries.append({"path": rel, "bytes": len(b), "sha256": sha(b)})
    save_json(out / "files.json", {"schema": "rcr-files/v1", "files": entries})


def extract(input_path, output, tools=None, include_server=False, bytecode_format=None):
    root = Path(input_path).absolute()
    if root.is_symlink() or not root.exists():
        raise ValueError("input must be an existing regular file or directory, not a symlink")
    output_resolved = Path(output).resolve()
    if root.is_dir() and (output_resolved == root.resolve() or root.resolve() in output_resolved.parents):
        raise ValueError("output cannot be inside input directory")
    from bootstrap_tools import validate_tools
    tools = validate_tools(tools or {}, require_pair=False)
    out = new_output(output)
    paths = sorted(root.rglob("*")) if root.is_dir() else [root]
    report = {"schema": "rcr-report/v1", "operation": "extract", "created_at": now(),
        "network_used": False, "input": str(root), "artifacts": [], "scripts": [], "errors": [],
        "coverage": {"scope": "supplied artifacts only", "game_completeness": "unknown",
                     "runtime_executed": False, "streaming_and_conditional_replication": "not_observed"}}
    all_nodes, total, recovery_cache = [], 0, {}
    for path in paths:
        if path.is_symlink():
            report["errors"].append({"path": str(path), "error": "symlink skipped"})
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        artifact = {"input_path": rel, "status": "pending"}
        report["artifacts"].append(artifact)
        if path.stat().st_size > MAX_BYTES or total + path.stat().st_size > MAX_BYTES * 4:
            artifact.update(status="failed", error="input file or total byte limit exceeded")
            continue
        data = path.read_bytes()
        total += len(data)
        aid = "a" + str(len(report["artifacts"])).zfill(4) + "-" + sha(data)[:12]
        artifact.update(id=aid, sha256=sha(data), bytes=len(data))
        raw = out / "originals" / aid / safe_component(path.name)
        raw.parent.mkdir(parents=True)
        raw.write_bytes(data)
        artifact["preserved_path"] = raw.relative_to(out).as_posix()
        if path.suffix.lower() not in EXTENSIONS and not data.startswith(b"<roblox"):
            artifact.update(status="preserved_unrecognized", error="unsupported artifact type")
            continue
        try:
            nodes, info = parse_artifact(data, path.suffix.lower())
            nodes, info = adapt(nodes, info)
            node_paths = hierarchy(nodes)
            artifact.update(info, status="parsed", instance_count=len(nodes))
            by_id = {n["id"]: n for n in nodes}
            pending = []
            for n in nodes:
                logical = node_paths[n["id"]]
                if info["format"] in {"source-file", "opaque-bytecode-file"}:
                    logical = list(Path(rel).parts)
                nscope = scope(n, logical, info["format"], by_id)
                uid = sha((aid + "\0" + n["id"]).encode())[:16]
                # All metadata and hierarchy identities survive independently of script filtering.
                meta = {k: v for k, v in n.items() if k not in {"source", "bytecode", "opaque_source"}}
                meta.update(artifact_id=aid, instance_path=logical, scope=nscope)
                all_nodes.append(meta)
                if n["class"] not in SCRIPT_CLASSES:
                    continue
                # Keep directory length bounded even for deep hierarchy; full path is in report.
                directory = "src/" + aid + "/" + safe_component(logical[-1])[:48] + "--" + uid
                record = {"id": n["id"], "artifact_id": aid, "class": n["class"], "scope": nscope,
                          "instance_path": logical, "directory": directory, "recovery": "processing_failed",
                          "verification": {"syntax": "NOT_CHECKED", "cfg": "NOT_CHECKED", "behavior": "NOT_TESTED"}, "recovery_warnings": []}
                record["metadata"] = n.get("metadata", {})
                for key in ("capture_status", "capture_errors"):
                    if key in n:
                        record[key] = n[key]
                report["scripts"].append(record)
                if not include_server and nscope in {"server-only", "server-or-plugin"}:
                    record["recovery"] = "excluded_server_scope"
                else:
                    pending.append((n, record))
            recover_all(pending, out, tools, bytecode_format, recovery_cache)
        except Exception as e:
            # Artifact failures are retained as structured data; continue with independent inputs.
            artifact.update(status="failed", error=type(e).__name__ + ": " + str(e))
    counts = dict(collections.Counter(s["recovery"] for s in report["scripts"]))
    good = {"source_present", "empty_source", "decompiled_candidate", "decompiled_syntax_valid", "excluded_server_scope"}
    partial = any(a["status"] == "failed" for a in report["artifacts"]) or bool(report["errors"]) or any(s["recovery"] not in good or s.get("capture_errors") or s.get("recovery_warnings") or s.get('verification',{}).get('cfg')=='CFG_MISMATCH' for s in report["scripts"])
    recovered = sum("source" in s for s in report["scripts"])
    report["summary"] = {"status": "partial" if partial else "recovered" if recovered else "no_source",
        "artifacts": len(report["artifacts"]), "instances": len(all_nodes), "scripts": len(report["scripts"]),
        "source_files": recovered, "recovery_counts": counts,
        "syntax_validated": sum(s.get("validation", {}).get("status") == "syntax_pass" for s in report["scripts"])}
    report["summary"].update(
        unique_bytecode_payloads=len({s["bytecode"]["sha256"] for s in report["scripts"] if "bytecode" in s}),
        unique_source_payloads=len({s["source_sha256"] for s in report["scripts"] if "source_sha256" in s}),
        unique_syntax_valid_sources=len({s["source_sha256"] for s in report["scripts"] if s.get("validation", {}).get("status") == "syntax_pass"}),
        unique_syntax_valid_bytecode_payloads=len({s["bytecode"]["sha256"] for s in report["scripts"] if "bytecode" in s and s.get("validation", {}).get("status") == "syntax_pass"}),
        duplicate_bytecode_instances=sum("duplicate_bytecode_of" in s for s in report["scripts"]))
    report["summary"]["verification_counts"] = {stage: dict(collections.Counter(s["verification"][stage] for s in report["scripts"]))
                                                  for stage in ("syntax", "cfg", "behavior")}
    finish(out, report, all_nodes)
    return report


def validate_repository(path):
    root = Path(path).resolve()
    manifest = json.loads((root / "files.json").read_text(encoding="utf-8-sig"))
    failures, listed = [], set()
    for item in manifest["files"]:
        rel = item["path"]
        p = root / rel
        if PureWindowsPath(rel).drive or Path(rel).is_absolute() or ".." in PureWindowsPath(rel).parts or root not in p.resolve().parents or p.is_symlink():
            failures.append(rel + ": unsafe path")
            continue
        listed.add(rel)
        if not p.is_file() or p.stat().st_size != item["bytes"] or sha(p.read_bytes()) != item["sha256"]:
            failures.append(rel + ": missing or hash/size mismatch")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p != root / "files.json"}
    failures.extend(p + ": untracked file" for p in sorted(actual - listed))
    report = json.loads((root / "report.json").read_text(encoding="utf-8-sig"))
    for s in report.get("scripts", []):
        if "source" in s and s["source"] not in listed:
            failures.append(s["id"] + ": source not in integrity manifest")
    return {"status": "pass" if not failures else "failed", "files_checked": len(listed), "failures": failures,
            "meaning": "integrity only; syntax/semantics statuses remain in report.json"}
