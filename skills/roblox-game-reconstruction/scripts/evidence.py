"""Conservative evidence audit over the inventory's existing source/body registry."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    from .inventory import safe_file, save
except ImportError:  # direct script import
    from inventory import safe_file, save


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(run: Path) -> dict:
    """Audit a prepared run; only run/audit.json and run/rules.json are written."""
    run = Path(run).resolve()
    errors, warnings, rules = [], [], []

    def error(code, **detail):
        errors.append({"issue": code, **detail})

    def warning(code, **detail):
        warnings.append({"issue": code, **detail})

    config = read(run / "config.json")
    repo = Path(config["repo"]).resolve()
    report_path = repo / "report.json"
    report_bytes = report_path.read_bytes()
    report_sha = digest(report_bytes)
    if config.get("input_report_sha256") != report_sha:
        error("report_hash_mismatch")
    report = json.loads(report_bytes.decode("utf-8-sig"))
    modules_doc = read(run / "shared" / "modules.json")
    functions_doc = read(run / "shared" / "functions.json")
    manifest = read(run / "shared" / "input-manifest.json")
    modules = modules_doc.get("modules", [])
    missing_modules = modules_doc.get("missing", [])
    functions = functions_doc.get("functions", [])
    module_by_id = {m.get("module_id"): m for m in modules}
    function_by_id = {f.get("function_id"): f for f in functions}
    report_by_source = {s.get("source"): s for s in report.get("scripts", []) if s.get("source")}
    registry_trusted = False
    shared_manifest_path = run / "shared-manifest.json"
    if not shared_manifest_path.is_file():
        error("shared_manifest_missing")
    elif not config.get("shared_manifest_sha256"):
        error("shared_manifest_pin_missing")
    elif digest(shared_manifest_path.read_bytes()) != config["shared_manifest_sha256"]:
        error("shared_manifest_pin_mismatch")
    else:
        try:
            shared_manifest = read(shared_manifest_path)
            listed = shared_manifest.get("files", [])
            if not isinstance(listed, list):
                raise ValueError("shared-manifest files must be an array")
            declared = {str(item.get("path", "")).replace("\\", "/"): item.get("sha256") for item in listed}
            for rel, expected in declared.items():
                try:
                    shared_path = safe_file(run / "shared", rel, required=True)
                    if digest(shared_path.read_bytes()) != expected:
                        error("shared_registry_hash_mismatch", path=rel)
                except (ValueError, OSError) as exc:
                    error("shared_registry_file_unavailable", path=rel, detail=str(exc))
            required = {"modules.json", "functions.json", "input-manifest.json"}
            for name in required:
                if name not in declared:
                    error("shared_registry_hash_mismatch", path=name)
            registry_trusted = not any(e["issue"] == "shared_registry_hash_mismatch" for e in errors)
        except (AttributeError, TypeError, ValueError) as exc:
            error("shared_manifest_malformed", detail=str(exc))
    for module in modules:
        if module.get("module_cfg", "UNKNOWN") == "UNKNOWN":
            warning("module_cfg_unavailable", module_id=module.get("module_id"))

    if manifest.get("report_sha256") != report_sha:
        error("manifest_report_hash_mismatch")
    file_hashes = {}
    for item in manifest.get("files_used", []):
        rel, expected = item.get("path"), item.get("sha256")
        try:
            path = safe_file(repo, rel, required=True)
            if rel not in file_hashes:
                file_hashes[rel] = digest(path.read_bytes())
            actual = file_hashes[rel]
            if actual != expected:
                error("input_file_hash_mismatch", path=rel, kind=item.get("kind"))
        except (ValueError, OSError) as exc:
            if item.get("kind") == "cfg_evidence":
                warning("cfg_evidence_unavailable", path=rel, detail=str(exc))
            else:
                error("input_file_unavailable", path=rel, detail=str(exc))

    catalog = read(run / "catalog.json")
    plan = read(run / "analysis-plan.json")
    declared_system_ids = [s.get("id") for s in catalog.get("systems", [])]
    if not declared_system_ids:
        error("systems_missing")
    valid_system_ids = []
    for sid in declared_system_ids:
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sid):
            error("invalid_system_id", system_id=sid)
        else:
            valid_system_ids.append(sid)
            if declared_system_ids.count(sid) != 1:
                error("duplicate_system_id", system_id=sid)
    owned, excluded = [], []
    for system in catalog.get("systems", []):
        sid = system.get("id")
        for mid in system.get("module_ids", []):
            owned.append(mid)
    for exclusion in catalog.get("exclusions", []):
        excluded.append(exclusion.get("module_id"))
    inventory_module_ids = set(module_by_id) | {m.get("module_id") for m in missing_modules}
    for mid in inventory_module_ids | set(owned) | set(excluded):
        if mid not in inventory_module_ids:
            error("unknown_inventory_module_id", module_id=mid)
        count = owned.count(mid) + excluded.count(mid)
        if count != 1:
            error("module_ownership", module_id=mid, count=count)
    system_ids = set(valid_system_ids)
    planned = [a.get("system_id") for a in plan.get("assignments", [])]
    for sid in system_ids:
        if planned.count(sid) != 1:
            error("system_plan_coverage", system_id=sid, count=planned.count(sid))

    claim_count = contract_count = citations = 0
    analyses = {}
    kind_counts = {"CODE_OBSERVED": 0, "STATIC_INFERENCE": 0,
                   "UNVERIFIED_BEHAVIOR": 0, "INDEPENDENT_DESIGN": 0}
    for sid in sorted(system_ids):
        path = run / "analysis" / str(sid) / "analysis.json"
        spec_path = run / "analysis" / str(sid) / "spec.md"
        if not path.is_file() or not spec_path.is_file():
            error("analysis_output_missing", system_id=sid)
            continue
        analysis = read(path)
        analyses[sid] = analysis
        # Legacy 1.0/1.1 runs retain their original document contract.
        version = tuple(int(v) for v in config.get('skill_version', '1.0.0').split('.')[:3])
        if (0, 1, 2) <= version < (1, 0, 0) or version >= (1, 2, 0):
            try:
                from .worker_io import validate_worker
            except ImportError:
                from worker_io import validate_worker
            preflight = validate_worker(run, 'analysis', analysis, sid)
            for item in preflight['errors']:
                error('worker_contract_invalid', system_id=sid, detail=item)
        if analysis.get("system_id") != sid:
            error("analysis_system_id_mismatch", system_id=sid)
        seen = set()
        for collection, entry_kind in (("claims", "implementation_rule"), ("contracts", "function_contract")):
            for item in analysis.get(collection, []):
                if collection == "claims":
                    claim_count += 1
                else:
                    contract_count += 1
                cid = item.get("id")
                rid = f"{sid}:{cid}"
                if not cid or rid in seen:
                    error("duplicate_or_missing_claim_id", system_id=sid, claim_id=cid)
                seen.add(rid)
                kind = item.get("kind", "CODE_OBSERVED")
                if kind not in kind_counts:
                    error("unknown_claim_kind", id=rid, kind=kind)
                else:
                    kind_counts[kind] += 1
                rationale_ok = kind == "INDEPENDENT_DESIGN" and bool(item.get("rationale"))
                if kind == "STATIC_INFERENCE" and (not item.get("derivation") or not item.get("assumptions")):
                    error("inference_missing_derivation_or_assumptions", id=rid)
                evidence = item.get("evidence", [])
                if not isinstance(evidence, list) or (not evidence and not rationale_ok):
                    error("evidence_missing", id=rid)
                    evidence = []
                normalized = []
                for ev in evidence:
                    citations += 1
                    source = ev.get("source_file") if isinstance(ev, dict) else None
                    rec = report_by_source.get(source)
                    if rec is None:
                        error("source_not_exact_report_path", id=rid, source_file=source)
                        continue
                    try:
                        src_path = safe_file(repo, source, required=True)
                    except (ValueError, OSError) as exc:
                        error("source_unavailable", id=rid, source_file=source, detail=str(exc))
                        continue
                    raw = src_path.read_bytes()
                    actual_source = digest(raw)
                    if actual_source != rec.get("source_sha256"):
                        error("source_snapshot_hash_mismatch", id=rid, source_file=source)
                    if ev.get("source_sha256") and ev["source_sha256"] != actual_source:
                        error("citation_source_hash_mismatch", id=rid, source_file=source)
                    start, end = ev.get("line_start"), ev.get("line_end")
                    line_count = raw.count(b"\n") + 1
                    if not isinstance(start, int) or not isinstance(end, int) or not (1 <= start <= end <= max(1, line_count)):
                        error("invalid_source_range", id=rid, source_file=source, lines=[start, end])
                        continue
                    raw_fnid = ev.get("function_id")
                    mapping_status = "UNVERIFIED"
                    fn = function_by_id.get(raw_fnid) if isinstance(raw_fnid, str) else None
                    if raw_fnid is None:
                        if not ev.get("mapping_limit"):
                            error("null_function_id_without_mapping_limit", id=rid)
                    elif fn is None:
                        error("unknown_function_id", id=rid, function_id=raw_fnid)
                    else:
                        module_hash = (rec.get("bytecode") or {}).get("sha256")
                        if fn.get("bytecode_sha256") != module_hash:
                            error("function_source_mismatch", id=rid, function_id=raw_fnid)
                        if not (fn.get("source_line_start", 0) <= start <= end <= fn.get("source_line_end", -1)):
                            error("citation_outside_function_scope", id=rid, function_id=raw_fnid,
                                  cited=[start, end], scope=[fn.get("source_line_start"), fn.get("source_line_end")])
                        mapping_status = "CFG_VALIDATED" if registry_trusted and fn.get("cfg_status") == "CFG_VALIDATED" else "UNVERIFIED"
                        if ev.get("claimed_cfg") and ev["claimed_cfg"] != mapping_status:
                            warning("worker_cfg_label_ignored", id=rid, claimed=ev["claimed_cfg"], computed=mapping_status)
                    bytecode = (rec.get("bytecode") or {})
                    bytecode_hash = bytecode.get("sha256")
                    if bytecode_hash and ev.get("bytecode_sha256") and ev["bytecode_sha256"] != bytecode_hash:
                        error("bytecode_hash_mismatch", id=rid)
                    bytecode_path = bytecode.get("path")
                    if bytecode_path:
                        try:
                            bp = safe_file(repo, bytecode_path, required=True)
                            actual_bytecode = digest(bp.read_bytes())
                            if actual_bytecode != bytecode_hash:
                                error("reported_bytecode_hash_mismatch", id=rid, path=bytecode_path)
                        except (ValueError, OSError) as exc:
                            error("bytecode_unavailable", id=rid, path=bytecode_path, detail=str(exc))
                    else:
                        warning("original_bytecode_missing", id=rid, source_file=source)
                    normalized.append({"source_file": source, "source_sha256": actual_source,
                        "bytecode_sha256": bytecode_hash, "line_start": start, "line_end": end,
                        "function_id": raw_fnid, "function_cfg_status": mapping_status,
                        "module_cfg_status": (next((m.get("module_cfg", "UNKNOWN") for m in modules
                            if m.get("bytecode_sha256") == bytecode_hash), "UNKNOWN") if registry_trusted else "UNKNOWN"),
                        "behavior": "NOT_TESTED", "proof_scope": "existing body correspondence only; does not prove lexical scope, module semantics or game behavior"})
                if collection == "contracts" and item.get("kind") not in kind_counts:
                    error("contract_kind_missing_or_invalid", id=rid)
                if collection == "claims" and kind == "INDEPENDENT_DESIGN" and not item.get("rationale"):
                    error("design_missing_rationale", id=rid)
                rules.append({"id": rid, "system_id": sid, "entry_kind": item.get("entry_kind", entry_kind),
                    "kind": kind, "statement": item.get("statement", item.get("signature", "")),
                    "detail": item, "evidence": normalized})
        for test in analysis.get("tests", []):
            if test.get("executed") is not False or test.get("status") != "SPECIFIED_NOT_EXECUTED":
                error("test_execution_claim_forbidden", system_id=sid, test_id=test.get("id"))
            if test.get("kind") == "BEHAVIOR_VERIFIED" or test.get("behavior") == "BEHAVIOR_VERIFIED":
                error("behavior_verification_claim_forbidden", system_id=sid, test_id=test.get("id"))

    review_path = run / "review.json"
    if not review_path.is_file():
        error("review_missing")
    else:
        review = read(review_path)
        if review.get("mode") not in {"independent_agent", "coordinator_only", "mixed"}:
            error("review_mode_invalid")
        for sid in system_ids - set(review.get("checked_system_ids", [])):
            error("review_system_not_covered", system_id=sid)
        for finding in review.get("findings", []):
            if finding.get("status") in {"conflict", "unresolved", "missing_coverage"} and not (finding.get("resolution") or finding.get("unknown_id")):
                error("open_finding_untracked", finding_id=finding.get("id"))
    if not (run / "conflicts.json").is_file():
        error("conflicts_ledger_missing")
    else:
        conflicts = read(run / "conflicts.json")
        if not isinstance(conflicts.get("conflicts"), list):
            error("conflicts_ledger_malformed")

    try:
        from .semantic import audit_semantics
    except ImportError:
        from semantic import audit_semantics
    semantic = audit_semantics(run, config, modules, functions, rules, analyses)
    errors.extend(semantic["errors"])
    warnings.extend(semantic["warnings"])
    if semantic["status"] == "PARTIAL":
        warning("semantic_scope_partial", remaining=len(semantic["remaining"]))
    save(run / "semantic-audit.json", semantic)
    result = {"status": "pass" if not errors else "failed", "analysis_status": semantic['status'],
        "completion": semantic['completion'], "errors": errors, "warnings": warnings,
        "summary": {"systems": len(system_ids), "claims": claim_count, "contracts": contract_count,
            "citations": citations, "rules": len(rules), "function_cfg": {
                "CFG_VALIDATED": sum(e["function_cfg_status"] == "CFG_VALIDATED" for r in rules for e in r["evidence"]),
                "UNVERIFIED": sum(e["function_cfg_status"] == "UNVERIFIED" for r in rules for e in r["evidence"])},
            "claim_kinds": kind_counts, "semantic_status": semantic["status"], "semantic": semantic["summary"],
            "review_mode": (read(review_path).get("mode") if review_path.exists() else None)}}
    save(run / "audit.json", result)
    save(run / "rules.json", {"schema": "rgr-rules/v1", "policy": "worker assertions retained; source hashes and body CFG derived from existing registry; behavior NOT_TESTED", "rules": rules})
    return result

