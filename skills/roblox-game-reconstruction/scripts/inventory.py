"""Build a source-backed, conservative inventory from an RCR repository report."""
import argparse
import collections
import decimal
import hashlib
import json
import math
import re
import struct
from pathlib import Path, PurePosixPath

try:
    from .luau_static import Parser, plain
except ImportError:  # direct CLI invocation
    from luau_static import Parser, plain


def sha(data):
    return hashlib.sha256(data).hexdigest()


def skill_version(config):
    """Parse config.skill_version. A missing or malformed value is reported, never treated as legacy."""
    text = config.get("skill_version")
    if text is None:
        return None, "skill_version_missing"
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", text) if isinstance(text, str) else None
    if not match:
        return None, "skill_version_invalid"
    return tuple(map(int, match.groups())), None


def legacy_worker_contract(version):
    """0.1.0-0.1.1 predate the worker/function-trace contract introduced in 0.1.2."""
    return version is not None and version < (0, 1, 2)


def legacy_semantic_contract(version):
    """0.1.0 predates the semantic-coverage ledger introduced in 0.1.1."""
    return version is not None and version < (0, 1, 1)


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def safe_file(root, rel, required=False):
    """Resolve a report-relative file while rejecting absolute and escaping paths."""
    if not isinstance(rel, str) or not rel.strip():
        if required:
            raise ValueError("missing required relative path")
        return None
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or re.match(r"^[A-Za-z]:", rel) or ".." in p.parts:
        raise ValueError(f"unsafe or path-traversing report path: {rel!r}")
    full = (root / Path(*p.parts)).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"report path escapes repository: {rel!r}")
    if not full.is_file():
        if required:
            raise FileNotFoundError(f"required repository file is missing: {rel}")
        return None
    return full


def simple_path(expr, path, aliases):
    expr = re.sub(r"\s+", "", expr)
    expr = re.sub(r'(?:game:)?GetService\([\"\']([^\"\']+)[\"\']\)', r"game.\1", expr)
    expr = expr.replace("game.game.", "game.")
    expr = re.sub(r':(?:WaitForChild|FindFirstChild)\([\"\']([^\"\']+)[\"\'](?:,[^)]*)?\)', r".\1", expr)
    if not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[\w]+)*", expr):
        return None
    parts = expr.split(".")
    base = parts.pop(0)
    if base == "script":
        result = list(path)
    elif base == "game":
        result = [path[0]] if path else []
    elif base in aliases:
        result = list(aliases[base])
    else:
        return None
    for field in parts:
        if field == "Parent":
            if not result:
                return None
            result.pop()
        else:
            result.append(field)
    return result


def requirements(parser, path):
    aliases = {}
    for m in re.finditer(r'\blocal\s+(\w+)\s*=\s*(game:GetService\([\"\'][^\"\']+[\"\']\)|game(?:\.\w+)+|script(?:\.\w+)+)', parser.s):
        resolved = simple_path(m[2], path, aliases)
        if resolved:
            aliases[m[1]] = resolved
    out = []
    for n, token in enumerate(parser.t[:-1]):
        if token.text != "require" or parser.t[n + 1].text != "(":
            continue
        p, depth = n + 2, 1
        while p < len(parser.t):
            if parser.t[p].text == "(":
                depth += 1
            elif parser.t[p].text == ")":
                depth -= 1
                if not depth:
                    break
            p += 1
        if p >= len(parser.t):
            continue
        expr = parser.s[parser.t[n + 1].end:parser.t[p].start]
        out.append({"expression": expr, "resolved_instance_path": simple_path(expr, path, aliases),
                    "scope": "static potential dependency; runtime existence/conditional execution not proved",
                    **parser.loc(token.start, parser.t[p].end)})
    return out


def _validate_declared_hash(record, key, label):
    value = record.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(f"script {label} is missing a valid {key}")
    return value.lower()


def _module_cfg_status(comparison, compiled, original):
    """Certify only an explicit original-main to compiled-main proof mapping."""
    original_main = original.get("mainProto")
    compiled_main = compiled.get("mainProto")
    if not isinstance(original_main, int) or not isinstance(compiled_main, int):
        return "UNVERIFIED"

    # Older exact certificates store the main/root mapping in the comparison's
    # per-original-prototype records. matching_recovered_protos contains the
    # actual recompiled prototype indices, so this does not assume equal indices.
    if comparison.get("status") == "CFG_VALIDATED":
        root_matches = [f for f in comparison.get("functions", [])
                        if f.get("proto") == original_main
                        and f.get("status") == "CFG_VALIDATED"
                        and compiled_main in f.get("matching_recovered_protos", [])]
        if root_matches:
            return "CFG_VALIDATED"

    # Newer normalized relational certificates store an explicit module proof.
    module_proof = comparison.get("module_proof") or {}
    if module_proof.get("status") != "CFG_VALIDATED":
        return "UNVERIFIED"
    mapping = module_proof.get("prototype_mapping") or {}
    mapped_main = mapping.get(str(original_main), mapping.get(original_main))
    if mapped_main == compiled_main:
        return "CFG_VALIDATED"
    root_certificates = [f for f in module_proof.get("functions", [])
                         if f.get("original_proto") == original_main
                         and f.get("recompiled_proto") == compiled_main]
    return "CFG_VALIDATED" if any(f.get("status") == "CFG_VALIDATED" for f in root_certificates) else "UNVERIFIED"


def _load_cfg(repo, record):
    audit_rel = (record.get("cfg_audit") or {}).get("evidence")
    if not audit_rel:
        return None
    audit = safe_file(repo, audit_rel)
    if audit is None:
        # Evidence is optional; paths that exist but are directories are expected.
        p = PurePosixPath(str(audit_rel).replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", str(audit_rel)):
            raise ValueError(f"unsafe or path-traversing report path: {audit_rel!r}")
        audit_dir = (repo / Path(*p.parts)).resolve()
        try:
            audit_dir.relative_to(repo.resolve())
        except ValueError:
            raise ValueError(f"report path escapes repository: {audit_rel!r}")
        if not audit_dir.is_dir():
            return None
    else:
        raise ValueError(f"CFG evidence path must be a directory: {audit_rel!r}")
    opt = (record.get("cfg_audit") or {}).get("best_optimization")
    if not isinstance(opt, int):
        return None
    names = [f"comparison-O{opt}.json", f"recompiled-O{opt}.json", "original.json"]
    paths = [safe_file(repo, f"{audit_rel}/{name}") for name in names]
    if not all(paths):
        return None
    comparison, compiled, original = map(load, paths)
    return audit_rel, comparison, compiled, original


def build(repo: Path, out: Path) -> dict:
    repo, out = Path(repo).resolve(), Path(out).resolve()
    try:
        out.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ValueError("output directory must be outside the input repository")
    report_path = repo / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"expected repository/report.json: {report_path}")
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8-sig"))
    scripts = report.get("scripts")
    if not isinstance(scripts, list):
        raise ValueError("report.json must contain a scripts array")

    modules, functions, missing = [], [], []
    sources, source_hash_by_digest = {}, {}
    pathmap, manifest_records = collections.defaultdict(list), []
    calls, attributes, tablecounts, tableerrors, numerics = [], [], {}, [], []
    for record in scripts:
        if not isinstance(record, dict):
            raise ValueError("report scripts entries must be objects")
        instance_path = record.get("instance_path")
        if not isinstance(instance_path, list) or not all(isinstance(x, str) for x in instance_path):
            raise ValueError("script has missing or invalid instance_path")
        mid = "m:" + sha(("/".join(instance_path) + "\0" + str(record.get("id", ""))).encode())[:16]
        pathmap["/".join(instance_path)].append(mid)
        source_rel = record.get("source")
        if not source_rel:
            missing.append({"module_id": mid, "instance_path": instance_path,
                            "recovery": record.get("recovery"), "capture_errors": record.get("capture_errors"),
                            "cause": "no source supplied in repository report"})
            continue
        bytecode = record.get("bytecode") or {}
        if not isinstance(bytecode, dict) or not bytecode.get('sha256'):
            raise ValueError('source-only records are not supported by this adapter; bytecode.sha256 is required for '+source_rel)
        digest = _validate_declared_hash(bytecode, "sha256", "bytecode")
        rel_bytecode = bytecode.get("path")
        if rel_bytecode:
            bytecode_file = safe_file(repo, rel_bytecode)
            if bytecode_file:
                actual = sha(bytecode_file.read_bytes())
                if actual != digest:
                    raise ValueError(f"bytecode hash mismatch for {rel_bytecode}")
                manifest_records.append({"kind": "bytecode", "path": rel_bytecode.replace("\\", "/"), "sha256": actual})
        source_hash = _validate_declared_hash(record, "source_sha256", "source")
        source_file = safe_file(repo, source_rel, required=True)
        source = source_file.read_bytes()
        if sha(source) != source_hash:
            raise ValueError(f"source hash mismatch for {source_rel}")
        manifest_records.append({"kind": "source", "path": source_rel.replace("\\", "/"), "sha256": source_hash})
        previous_source_hash = source_hash_by_digest.setdefault(digest, source_hash)
        if previous_source_hash != source_hash:
            raise ValueError(f"bytecode digest {digest} is associated with differing source hashes")
        cfg = _load_cfg(repo, record)
        cfg_rel = (record.get("cfg_audit") or {}).get("evidence")
        module_cfg = _module_cfg_status(cfg[1], cfg[2], cfg[3]) if cfg else "UNKNOWN"
        module = {"module_id": mid, "bytecode_sha256": digest, "source_sha256": source_hash,
                  "class": record.get("class"), "instance_path": instance_path,
                  "original_source": source_rel.replace("\\", "/"),
                  "source_snapshot": "sources/" + digest + ".luau",
                  "declared_cfg": (record.get("verification") or {}).get("cfg", "UNVERIFIED"),
                  "module_cfg": module_cfg, "proof_status": module_cfg,
                  "best_optimization": (record.get("cfg_audit") or {}).get("best_optimization"),
                  "cfg_evidence": cfg_rel.replace("\\", "/") if cfg_rel else None,
                  "original_bytecode": rel_bytecode.replace("\\", "/") if rel_bytecode else None,
                  "syntax_status": (record.get("verification") or {}).get("syntax", "UNVERIFIED"),
                  "behavior_status": "NOT_TESTED", "source_line_count": source.count(b"\n") + 1}
        modules.append(module)
        parser = Parser(source.decode("utf-8"))
        module["requires"] = requirements(parser, instance_path)
        if cfg:
            audit_rel = cfg[0]
            filenames = {'original.json'} | {f'{kind}-O{opt}.json' for kind in ('comparison', 'recompiled') for opt in (0, 1, 2)}
            for filename in sorted(filenames):
                fpath = safe_file(repo, f"{audit_rel}/{filename}")
                if fpath:
                    manifest_records.append({"kind": "cfg_evidence", "path": f"{audit_rel}/{filename}", "sha256": sha(fpath.read_bytes())})
        if digest in sources:
            # Preserve every instance module while deduplicating byte content snapshots.
            pass
        else:
            sources[digest] = module
            dest = out / module["source_snapshot"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source)

            if cfg:
                audit_rel, comparison, compiled, original = cfg
                comparisons = comparison.get("functions", [])
                for proto in compiled.get("protos", []):
                    matches = [f for f in comparisons if proto.get("index") in f.get("matching_recovered_protos", []) and f.get("status") == "CFG_VALIDATED"]
                    possible = [f for f in comparisons if f.get("recompiled_proto") == proto.get("index") and f.get("status") != "CFG_VALIDATED"]
                    line = proto.get("line") or 1
                    ownlines = [i.get("line", 0) for i in proto.get("instructions", []) if i.get("line", 0) > 0]
                    functions.append({"function_id": f"{digest}:O{record['cfg_audit']['best_optimization']}:r{proto['index']}",
                        "bytecode_sha256": digest, "source_snapshot": module["source_snapshot"],
                        "recompiled_proto": proto["index"], "name": proto.get("name", ""),
                        "source_line_start": line, "source_line_end": max([line] + ownlines),
                        "params": proto.get("params"), "upvalues": proto.get("upvalues"), "vararg": proto.get("vararg"), "children": proto.get("children", []),
                        "cfg_status": "CFG_VALIDATED" if matches else "UNVERIFIED",
                        "behavior": "NOT_TESTED", "mapping_scope": "recompiled proto mapped by comparison evidence; standalone body certificate only",
                        "certified_original_body_matches": [{"proto": x.get("proto"), "name": x.get("name"), "certificate": x.get("certificate"), "scope": x.get("scope"), "correspondence": x.get("correspondence")} for x in matches],
                        "unverified_correspondence_candidates": [{"proto": x.get("proto"), "name": x.get("name"), "diagnosis": x.get("diagnosis")} for x in possible]})
                numeric = []
                for original_proto in original.get("protos", []):
                    for constant in original_proto.get("constants", []):
                        if constant.get("kind") not in {"number", "integer", "vector"}:
                            continue
                        item = {"original_proto": original_proto.get("index"), "constant_index": constant.get("index"),
                                "kind": constant["kind"], "text": constant.get("text"),
                                "numericBitsLE": constant.get("numericBitsLE"), "numericEncoding": constant.get("numericEncoding"),
                                "association": "raw original constant; field association requires data-flow evidence"}
                        if constant.get("numericEncoding") == "f64" and constant.get("numericBitsLE"):
                            value = struct.unpack("<d", bytes.fromhex(constant["numericBitsLE"]))[0]
                            item["binary64_exact_decimal"] = str(decimal.Decimal.from_float(value))
                        numeric.append(item)
                save(out / "numeric-constants" / f"{digest}.json", {"bytecode_sha256": digest, "original_constants": numeric})
                numerics.append({"bytecode_sha256": digest, "numeric_constants": len(numeric)})
            else:
                save(out / "numeric-constants" / f"{digest}.json", {"bytecode_sha256": digest, "proof_status": "UNKNOWN", "original_constants": []})
            tables, errors = parser.constructors()
            tablecounts[digest] = {"constructors": len(tables), "parse_errors": len(errors)}
            save(out / "tables" / f"{digest}.json", {"bytecode_sha256": digest, "source_snapshot": module["source_snapshot"],
                 "scope": "syntactic table constructors only; later mutation/override and function execution not evaluated",
                 "constructors": tables, "plain_constructors": [plain(t) for t in tables], "parse_errors": errors})
            tableerrors.extend({"bytecode_sha256": digest, **e} for e in errors)

        for n, token in enumerate(parser.t):
            if token.kind != "identifier":
                continue
            next_token = parser.t[n + 1].text if n + 1 < len(parser.t) else None
            is_call = next_token == "("
            is_remote_member = (n > 0 and parser.t[n - 1].text == "." and
                                token.text in {"OnClientEvent", "OnServerEvent", "OnClientInvoke", "OnServerInvoke"})
            if is_remote_member or (is_call and token.text in {"FireServer", "InvokeServer", "FireClient", "FireAllClients", "CallServer", "SendToServer"}):
                calls.append({"bytecode_sha256": digest, "method": token.text,
                              "form": "callback_assignment" if is_remote_member and next_token == "=" else "event_or_callback_reference" if is_remote_member else "call",
                              "source_context": parser.s[max(0, token.start - 110):min(len(parser.s), token.end + 180)],
                              **parser.loc(token.start, token.end), "status": "candidate remote symbol; receiver/authority must be traced"})
            if is_call:
                if token.text in {"GetAttribute", "SetAttribute"}:
                    arg = parser.t[n + 2] if n + 2 < len(parser.t) else None
                    attributes.append({"bytecode_sha256": digest, "method": token.text,
                                      "attribute_literal": arg.text if arg and arg.kind == "string" else None,
                                      **parser.loc(token.start, token.end)})
    for module in modules:
        for edge in module["requires"]:
            edge["target_module_ids"] = pathmap.get("/".join(edge["resolved_instance_path"]), []) if edge["resolved_instance_path"] else []
            edge["resolution"] = "static_path_matched" if len(edge["target_module_ids"]) == 1 else "path_ambiguous" if edge["target_module_ids"] else "not_resolved_to_captured_module"
    save(out / "modules.json", {"schema": "rgr-evidence-modules/v1", "modules": modules, "missing": missing})
    save(out / "functions.json", {"schema": "rgr-evidence-functions/v1", "functions": functions,
         "line_scope": "compiler recompiled-proto declaration and own-instruction lines; not a parsed lexical end range"})
    save(out / "dependency-graph.json", {"nodes": [{"id": m["module_id"], "path": "/".join(m["instance_path"]), "content_id": m["bytecode_sha256"]} for m in modules],
         "edges": [{"source": m["module_id"], **e} for m in modules for e in m["requires"]]})
    save(out / "remote-candidates" / "calls.json", calls)
    save(out / "remote-candidates" / "attribute-accesses.json", attributes)
    summary = {"script_instances": len(scripts), "source_instances": len(modules), "unique_source_payloads": len(sources),
               "missing": len(missing), "compiled_functions": len(functions),
               "function_cfg_counts": dict(collections.Counter(f["cfg_status"] for f in functions)),
               "require_calls": sum(len(m["requires"]) for m in modules),
               "resolved_require_calls": sum(e["resolution"] == "static_path_matched" for m in modules for e in m["requires"]),
               "table_constructors": sum(c["constructors"] for c in tablecounts.values()),
               "table_parse_errors": len(tableerrors), "numeric_constants": sum(x["numeric_constants"] for x in numerics)}
    save(out / "table-extraction-coverage.json", {"modules": tablecounts, "errors": tableerrors})
    save(out / "input-manifest.json", {"report_sha256": sha(report_bytes), "report_path": "report.json",
         "files_used": manifest_records})
    save(out / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="RCR repository directory or run root containing repository/report.json")
    parser.add_argument("--out", required=True, help="inventory output directory")
    args = parser.parse_args()
    repo = Path(args.repo)
    if (repo / "repository" / "report.json").is_file():
        repo = repo / "repository"
    print(json.dumps(build(repo, Path(args.out)), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

