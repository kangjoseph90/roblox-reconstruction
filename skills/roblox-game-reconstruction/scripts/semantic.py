"""Agent-authored semantic obligation accounting; no automatic semantic inference."""

from __future__ import annotations

from collections import Counter
import datetime
import json
from pathlib import Path
import re

from inventory import load, save, safe_file, sha, source_line_count
from function_trace import validate_trace


def freeze_review(run, review_id, source):
    run = Path(run).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", review_id):
        raise ValueError("unsafe review ID")
    config = load(run / "config.json")
    raw = Path(source).read_bytes()
    doc = json.loads(raw.decode("utf-8-sig"))
    folder = run / "source-reviews" / review_id
    if (folder / "manifest.json").exists() or review_id in config.get("source_review_pins", {}):
        raise ValueError("review already frozen; preserve it and use a new ID")
    if folder.exists():
        raise ValueError("source-reviews/%s exists but holds no frozen review; source-reviews/ is written only by "
                         "freeze-review, so keep the draft at review-drafts/%s.json and remove this directory" % (review_id, review_id))
    if sha((Path(config["repo"]) / "report.json").read_bytes()) != config["input_report_sha256"]:
        raise ValueError("input changed before source review freeze")
    from worker_io import validate_worker

    preflight = validate_worker(run, 'source-review', doc)
    if preflight['status'] != 'pass':
        raise ValueError('source review preflight failed: ' + json.dumps(preflight['errors'], ensure_ascii=False))
    folder.mkdir(parents=True)
    (folder / "source-first.json").write_bytes(raw)
    manifest = {
        "schema": "rgr-source-review-pin/v1",
        "review_id": review_id,
        "input_report_sha256": config["input_report_sha256"],
        "source_sha256": sha(raw),
        "reviewer": doc["reviewer"],
        "frozen_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "independence": "reviewer declaration and host access log; not OS-enforced isolation",
    }
    save(folder / "manifest.json", manifest)
    config.setdefault("source_review_pins", {})[review_id] = sha((folder / "manifest.json").read_bytes())
    save(run / "config.json", config)
    return {
        "status": "frozen",
        "review_id": review_id,
        "source_sha256": sha(raw),
        "validation_status": "pass",
        "analysis_status": "NOT_ASSESSED",
        "validation": preflight['summary'],
    }


def audit_semantics(run, config, modules, functions, rules, analyses, ledger=None):
    """Validate recorded obligations/links/scope, not the truth or exhaustiveness of prose."""
    run = Path(run)
    errors, warnings, remaining = [], [], []

    def err(issue, **detail):
        errors.append({"issue": issue, **detail})

    def gap(issue, **detail):
        remaining.append({"issue": issue, **detail})

    path = run / "semantic-coverage.json"
    if ledger is None and not path.is_file():
        err("semantic_coverage_missing")
        return {
            "status": "FAILED",
            "errors": errors,
            "warnings": warnings,
            "remaining": [],
            "summary": {},
            "completion": {
                "functions_reviewed": None,
                "functions_total": None,
                "material_relations_untraced": None,
                "unknowns_remaining": None,
            },
        }
    doc = load(path) if ledger is None else ledger
    if doc.get("schema") != "rgr-semantic-coverage/v1":
        err("semantic_schema_invalid")
    by_module = {v["module_id"]: v for v in modules}
    by_fn = {v["function_id"]: v for v in functions}
    by_rule = {v["id"]: v for v in rules}
    by_source = {v["original_source"]: v for v in modules}

    def indexed(items, key, label):
        result = {}
        for item in items:
            ident = item.get(key)
            if not isinstance(ident, str) or not ident or ident in result:
                err("semantic_duplicate_or_missing_id", collection=label, id=ident)
            else:
                result[ident] = item
        return result

    observations = indexed(doc.get("observations", []), "id", "observations")
    relations = indexed(doc.get("relations", []), "id", "relations")
    if set(observations) & set(relations):
        err("semantic_subject_id_collision")
    subjects = {**observations, **relations}
    entries = indexed(doc.get("functions", []), "function_id", "functions")
    mappings = indexed(doc.get("mappings", []), "subject_id", "mappings")
    scope = doc.get("scope", {})
    module_ids = scope.get("module_ids", [])
    if not module_ids or not scope.get("rationale"):
        err("semantic_scope_missing")
    for mid in module_ids:
        if mid not in by_module:
            err("semantic_unknown_module", module_id=mid)
    hashes = {by_module[m]["bytecode_sha256"] for m in module_ids if m in by_module}
    expected = {fid for fid, f in by_fn.items() if f["bytecode_sha256"] in hashes}
    for fid in expected - set(entries):
        err("semantic_function_unaccounted", function_id=fid)
    for fid in set(entries) - expected:
        err("semantic_function_outside_scope", function_id=fid)
    if scope.get("declared_full_game"):
        if set(module_ids) != set(by_module):
            err("semantic_false_full_game_scope")
        # Missing-source inventory and unknown instance properties are never certified here.
        gap("full_game_claim_excludes_unavailable_runtime_and_server_state")
    function_bytecodes = {f["bytecode_sha256"] for f in functions} if module_ids else set()
    for mid in module_ids:
        if mid in by_module and by_module[mid]["bytecode_sha256"] not in function_bytecodes:
            gap("module_function_mapping_unavailable", module_id=mid)
    # Completion is relative to every source-backed module the catalog assigns to a system.
    # A narrower semantic scope is allowed, but it keeps the analysis PARTIAL. Catalog exclusions
    # of kind "framework" (engine/vendor/tooling/telemetry) are outside the denominator; kind
    # "deferred" (game code not analysed in this run) stays open work and keeps the result PARTIAL.
    catalog_path = run / "catalog.json"
    catalog = load(catalog_path) if catalog_path.is_file() else {}
    owned = {mid for s in catalog.get("systems", []) for mid in s.get("module_ids", []) if mid in by_module}
    for mid in sorted(owned - set(module_ids)):
        gap("scope_omits_owned_module", module_id=mid)
    excluded_by_kind = {"framework": set(), "deferred": set()}
    for entry in catalog.get("exclusions", []):
        if entry.get("kind") in excluded_by_kind and entry.get("module_id") in by_module:
            excluded_by_kind[entry["kind"]].add(entry["module_id"])
    for mid in sorted(excluded_by_kind["deferred"]):
        gap("deferred_module", module_id=mid)
    in_scope_hashes = {by_module[m]["bytecode_sha256"] for m in module_ids if m in by_module}
    deferred_hashes = {by_module[m]["bytecode_sha256"] for m in excluded_by_kind["deferred"]} - in_scope_hashes

    source_cache, checked_evidence = {}, []

    def importance(item, ident):
        value = item.get("importance")
        correction = item.get("classification_erratum")
        if correction:
            fixed = correction.get("importance")
            if (
                not correction.get("rationale")
                or fixed not in {"material", "supporting", "candidate"}
                or (value == "material" and fixed != value)
            ):
                err("semantic_classification_erratum_invalid", id=ident)
            else:
                warnings.append(
                    {
                        "issue": "classification_label_corrected",
                        "id": ident,
                        "original": value,
                        "corrected": fixed,
                        "rationale": correction["rationale"],
                    }
                )
                return fixed
        return value

    def effective_evidence(item, ident):
        original = item.get("evidence", [])
        correction = item.get("citation_erratum")
        if not correction:
            return original
        fixed = correction.get("evidence", [])
        if not correction.get("rationale") or not isinstance(fixed, list) or len(fixed) != len(original):
            err("semantic_citation_erratum_invalid", id=ident)
            return original
        # Errata may correct source-body attribution, never move a citation to
        # different code. Semantic corrections require new, reviewed obligations.
        for before, after in zip(original, fixed):
            if {k: before.get(k) for k in ("source_file", "line_start", "line_end")} != {
                k: after.get(k) for k in ("source_file", "line_start", "line_end")
            }:
                err("semantic_citation_erratum_moves_source", id=ident)
                return original
        warnings.append({"issue": "citation_attribution_corrected", "id": ident, "rationale": correction["rationale"]})
        return fixed

    def cited_functions(item, ident):
        evs = effective_evidence(item, ident)
        return (
            {e['function_id'] for e in evs if isinstance(e, dict) and isinstance(e.get('function_id'), str)}
            if isinstance(evs, list)
            else set()
        )

    def evidence(item, ident):
        evs = effective_evidence(item, ident)
        if not isinstance(evs, list) or not evs:
            err("semantic_evidence_missing", id=ident)
            return
        for ev in evs:
            if not isinstance(ev, dict):
                err("semantic_evidence_invalid", id=ident)
                continue
            source = ev.get("source_file")
            m = by_source.get(source)
            if not m:
                err("semantic_source_not_in_registry", id=ident, source_file=source)
                continue
            if source not in source_cache:
                p = safe_file(Path(config["repo"]), source, required=True)
                raw = p.read_bytes()
                source_cache[source] = source_line_count(raw)
                if sha(raw) != m["source_sha256"]:
                    err("semantic_source_hash_mismatch", source_file=source)
            start, end = ev.get("line_start"), ev.get("line_end")
            if type(start) is not int or type(end) is not int or not (1 <= start <= end <= source_cache[source]):
                err("semantic_invalid_source_span", id=ident, cited=[start, end], source_lines=source_cache[source])
                continue
            fid = ev.get("function_id")
            if fid is None:
                if not ev.get("mapping_limit"):
                    err("semantic_unmapped_without_limit", id=ident)
            elif not isinstance(fid, str) or fid not in by_fn or by_fn[fid]["bytecode_sha256"] != m["bytecode_sha256"]:
                err("semantic_function_source_mismatch", id=ident, function_id=fid,
                    hint="function_id must be a body of this source file; `cite FILE:START-END` finds it")
            elif not by_fn[fid].get("source_line_start", 0) <= start <= end <= by_fn[fid].get("source_line_end", -1):
                err("semantic_citation_outside_function", id=ident, function_id=fid, cited=[start, end],
                    function_lines=[by_fn[fid].get("source_line_start"), by_fn[fid].get("source_line_end")])
            checked_evidence.append(
                {
                    "subject_id": ident,
                    "source_file": source,
                    "source_sha256": m["source_sha256"],
                    "bytecode_sha256": m["bytecode_sha256"],
                    "line_start": start,
                    "line_end": end,
                    "function_id": fid,
                    "function_cfg": (by_fn.get(fid, {}) if isinstance(fid, str) else {}).get(
                        "cfg_status", "UNVERIFIED"
                    ),
                    "module_cfg": m.get("module_cfg", "UNVERIFIED"),
                    "behavior": "NOT_TESTED",
                    "scope": "existing source-body correspondence, not module context or runtime behavior",
                }
            )

    for ident, item in subjects.items():
        if importance(item, ident) not in {"material", "supporting", "candidate"} or not item.get("statement"):
            err("semantic_subject_invalid", id=ident)
        evidence(item, ident)
    for ident, rel in relations.items():
        if rel.get("from") not in observations or rel.get("to") not in observations:
            err("semantic_relation_endpoint_missing", id=ident)
        if not rel.get("kind"):
            err("semantic_relation_kind_missing", id=ident)
        trace = rel.get("trace_function_ids", [])
        if not isinstance(trace, list) or not all(isinstance(fid, str) for fid in trace):
            err("semantic_trace_ids_invalid", id=ident)
            trace = []
        trace_erratum = rel.get("trace_id_erratum")
        if trace_erratum is not None:
            corrected = trace_erratum.get("function_ids") if isinstance(trace_erratum, dict) else None
            cited = cited_functions(rel, ident)
            if (
                not isinstance(trace_erratum, dict)
                or not isinstance(trace_erratum.get("rationale"), str)
                or not trace_erratum["rationale"].strip()
                or not isinstance(corrected, list)
                or not all(isinstance(f, str) and f in cited for f in corrected)
                or len(corrected) != len(trace)
            ):
                err("semantic_trace_id_erratum_invalid", id=ident)
            else:
                trace = corrected
                warnings.append(
                    {"issue": "trace_registry_ids_corrected", "id": ident, "rationale": trace_erratum["rationale"]}
                )
        resolution = rel.get("trace_resolution")
        if resolution is not None:
            # Follow-up evidence extends the integrated trace without changing
            # any statement, endpoint, citation or ID in the frozen source pass.
            resolved = resolution.get("function_ids") if isinstance(resolution, dict) else None
            follow_evidence = resolution.get("evidence") if isinstance(resolution, dict) else None
            cited = cited_functions(rel, ident)
            if isinstance(follow_evidence, list):
                cited.update(
                    e.get("function_id")
                    for e in follow_evidence
                    if isinstance(e, dict) and isinstance(e.get("function_id"), str)
                )
            if (
                not isinstance(resolution, dict)
                or not isinstance(resolution.get("rationale"), str)
                or not resolution["rationale"].strip()
                or not rel.get("origin")
                or not isinstance(resolved, list)
                or not resolved
                or not all(isinstance(f, str) and f in by_fn and f in cited for f in resolved)
                or len(resolved) != len(set(resolved))
                or not set(trace).issubset(resolved)
                or not isinstance(follow_evidence, list)
                or not follow_evidence
                or rel.get("trace_status") != "TRACED"
            ):
                err("semantic_trace_resolution_invalid", id=ident)
            else:
                evidence(resolution, ident + ":trace_resolution")
                trace = resolved
                warnings.append(
                    {"issue": "trace_resolved_by_followup", "id": ident, "rationale": resolution["rationale"]}
                )
        for fid in trace:
            if fid not in by_fn:
                err("semantic_unknown_trace_function", id=ident, function_id=fid)
        state = rel.get("trace_status")
        if state not in {"TRACED", "UNRESOLVED", "EXCLUDED"}:
            err("semantic_trace_status_invalid", id=ident)
        if state == "TRACED" and not trace:
            err("semantic_trace_functions_missing", id=ident)
        if state != "TRACED" and importance(rel, ident) == "material":
            gap("material_trace_open", id=ident)
    states = {"UNANALYZED", "SURVEYED", "TRACED", "REVIEWED"}
    trace_supported = set()

    def trace_check(fn, location):
        before = len(errors)
        validate_trace(
            fn, evidence, lambda issue, pos: err(issue, location=pos, function_id=fn.get('function_id')), location
        )
        return fn.get('status') in {'TRACED', 'REVIEWED'} and len(errors) == before

    for fid, fn in entries.items():
        if fn.get("status") not in states:
            err("semantic_function_status_invalid", function_id=fid)
        if fn.get("status") != "REVIEWED":
            gap("function_not_reviewed", function_id=fid, status=fn.get("status"))
        linked = fn.get("observation_ids", []) + fn.get("relation_ids", [])
        for ident in linked:
            if ident not in subjects:
                err("semantic_function_subject_missing", function_id=fid, id=ident)
        if trace_check(fn, 'functions.' + fid):
            trace_supported.add(fid)
        if isinstance(fn.get('trace'), dict) and fn['trace'].get('limits'):
            gap('function_trace_limits', function_id=fid, detail=fn['trace']['limits'])
        if fn.get("remaining"):
            gap("function_remaining", function_id=fid, detail=fn["remaining"])

    # The frozen source-only obligation set must survive the integration step.
    reviews = indexed(doc.get("source_reviews", []), "review_id", "source_reviews")
    pins = config.get("source_review_pins", {})
    if not reviews:
        err("semantic_independent_source_review_missing")
    for rid in set(pins) - set(reviews):
        err("semantic_frozen_review_omitted", review_id=rid)
    reviewed_functions = set()
    origins = {}
    for ident, item in subjects.items():
        origin = item.get("origin", {})
        if origin:
            key = (origin.get("review_id"), origin.get("item_id"))
            if key in origins:
                err("semantic_duplicate_origin", id=ident)
            origins[key] = item
            if key[0] not in reviews:
                err("semantic_unknown_review_origin", id=ident)
    for rid in reviews:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", rid):
            err("semantic_unsafe_review_id")
            continue
        folder = run / "source-reviews" / rid
        try:
            manifest_raw = (folder / "manifest.json").read_bytes()
            manifest = json.loads(manifest_raw)
            raw = (folder / "source-first.json").read_bytes()
            if pins.get(rid) != sha(manifest_raw) or manifest["source_sha256"] != sha(raw):
                err("semantic_review_pin_mismatch", review_id=rid)
            if manifest.get("input_report_sha256") != config["input_report_sha256"]:
                err("semantic_review_input_mismatch", review_id=rid)
            review = json.loads(raw.decode("utf-8-sig"))
            if review.get("specification_seen") is not False:
                err("semantic_review_not_source_first", review_id=rid)
            originals = review.get("observations", []) + review.get("relations", [])
            original_ids = {v["id"] for v in originals}
            original_to_new = {k[1]: v.get("id") for k, v in origins.items() if k[0] == rid}
            for original in originals:
                actual = origins.get((rid, original["id"]))
                if not actual:
                    err("semantic_source_obligation_dropped", review_id=rid, id=original["id"])
                    continue
                for key in ("statement", "importance", "evidence", "category", "kind", "trace_function_ids"):
                    if original.get(key) != actual.get(key):
                        err("semantic_source_obligation_changed", review_id=rid, id=original["id"], field=key)
                if (
                    actual.get("trace_resolution") is not None
                    and original.get("trace_status", "TRACED") != "UNRESOLVED"
                ):
                    err("semantic_trace_resolution_not_unresolved", review_id=rid, id=original["id"])
                for key in ("from", "to"):
                    if key in original and actual.get(key) != original_to_new.get(original[key]):
                        err("semantic_source_relation_rebound", review_id=rid, id=original["id"])
            for key in origins:
                if key[0] == rid and key[1] not in original_ids:
                    err("semantic_invented_origin", review_id=rid, id=key[1])
            for fn in review.get("functions", []):
                if fn.get("status") in {"TRACED", "REVIEWED"}:
                    if 'trace' not in fn:
                        # freeze-review preflights traces, so a frozen TRACED/REVIEWED entry without
                        # one did not come from this release's freeze.
                        err('semantic_source_review_trace_missing', review_id=rid, function_id=fn['function_id'])
                    elif trace_check(fn, 'source_reviews.' + rid + '.' + fn['function_id']):
                        reviewed_functions.add(fn["function_id"])
        except (OSError, KeyError, ValueError) as exc:
            err("semantic_review_unavailable", review_id=rid, detail=str(exc))
    author_supported = set()
    authored_by_function = {}  # author traces per function, in analysis/coverage order
    for a in analyses.values():
        for v in a.get('coverage', {}).get('functions', []):
            if v.get('status') in {'TRACED', 'REVIEWED'}:
                authored_by_function.setdefault(v.get('function_id'), []).append(v)
    for fid, fn in entries.items():
        if fn.get("status") == "REVIEWED" and fid not in reviewed_functions:
            err("semantic_function_review_not_supported", function_id=fid)
        if fn.get('status') == 'REVIEWED':
            authored = authored_by_function.get(fid, [])
            if not authored:
                err('semantic_function_author_trace_missing', function_id=fid)
            if authored and all([trace_check(author, 'author_functions.' + fid) for author in authored]):
                author_supported.add(fid)

    unknowns = {(sid, u.get("id")): u for sid, a in analyses.items() for u in a.get("unknowns", [])}
    audits = {}
    for item in doc.get("unknown_audits", []):
        key = (item.get("system_id"), item.get("unknown_id"))
        if key in audits:
            err("semantic_duplicate_unknown_audit", unknown_ref=key)
        audits[key] = item
        status = item.get("status")
        if status not in {
            "SOURCE_FOUND",
            "PARTIALLY_SUPPORTED",
            "NOT_FOUND_IN_SEARCHED_SCOPE",
            "UNAVAILABLE_SOURCE",
            "NOT_INVESTIGATED",
        }:
            err("semantic_unknown_status_invalid", unknown_ref=key)
        if not item.get("question") or not item.get("rationale"):
            err("semantic_unknown_audit_incomplete", unknown_ref=key)
        if status == "NOT_INVESTIGATED":
            gap("unknown_not_investigated", unknown_ref=key)
        elif not item.get("searched_sources") or not item.get("queries") or not item.get("inspected_function_ids"):
            err("semantic_unknown_search_unrecorded", unknown_ref=key)
        for fid in item.get("inspected_function_ids", []):
            if fid not in by_fn:
                err("semantic_unknown_search_function_invalid", function_id=fid)
        if status in {"SOURCE_FOUND", "PARTIALLY_SUPPORTED"}:
            evidence(item, str(key))
            if not item.get("resolved_rule_ids") or any(r not in by_rule for r in item.get("resolved_rule_ids", [])):
                err("semantic_found_source_not_linked", unknown_ref=key, resolved_rule_ids=item.get("resolved_rule_ids"),
                    hint="SOURCE_FOUND/PARTIALLY_SUPPORTED needs resolved_rule_ids naming existing rules that now carry the found client facts")
            unresolved = unknowns.get(key)
            if (
                unresolved
                and unresolved.get("status") != "RESOLVED"
                and not (
                    item.get("remaining_question")
                    and unresolved.get("question") == item["remaining_question"]
                    and item["remaining_question"] != item["question"]
                )
            ):
                err("semantic_blanket_unknown_retained", unknown_ref=key, audit_question=item.get("question"),
                    analysis_question=unresolved.get("question"), remaining_question=item.get("remaining_question"),
                    hint="either set the analysis unknown status RESOLVED, or narrow it: its question must equal the audit's "
                         "remaining_question, which must differ from the audit question")
        if key not in unknowns:
            err("semantic_unknown_audit_orphan", unknown_ref=key)
        elif unknowns[key].get("status") != "RESOLVED":
            gap("unknown_remaining", unknown_ref=key, status=status)
        elif status != "SOURCE_FOUND":
            # Only a source-backed, rule-linked finding can close a question.
            err("semantic_unknown_resolved_without_source", unknown_ref=key, status=status)
    for key in set(unknowns) - set(audits):
        err("semantic_unknown_unaudited", unknown_ref=key)

    for ident in set(subjects) - set(mappings):
        err("semantic_subject_unmapped", id=ident)
    for ident, mapping in mappings.items():
        if ident not in subjects:
            err("semantic_mapping_orphan", id=ident)
            continue
        state = mapping.get("status")
        if state not in {"REPRESENTED", "UNKNOWN", "EXCLUDED", "MISSING", "CONTRADICTED", "UNREVIEWED"}:
            err("semantic_mapping_status_invalid", id=ident)
        if not mapping.get("rationale"):
            err("semantic_mapping_rationale_missing", id=ident)
        material = importance(subjects[ident], ident) == "material"
        if state == "REPRESENTED":
            rids = mapping.get("rule_ids", [])
            if not rids:
                err("semantic_represented_without_rule", id=ident)
            for rule in rids:
                if rule not in by_rule or ident not in by_rule[rule].get("detail", {}).get("semantic_refs", []):
                    err("semantic_mapping_not_reciprocal", id=ident, rule_id=rule)
        elif state == "UNKNOWN":
            ref = mapping.get("unknown_ref", {})
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get('system_id'), str)
                or not isinstance(ref.get('unknown_id'), str)
            ):
                err('semantic_unknown_ref_invalid', id=ident, expected='{system_id, unknown_id}')
                continue
            key = (ref.get("system_id"), ref.get("unknown_id"))
            if key not in audits or audits[key].get("status") in {"NOT_INVESTIGATED", "SOURCE_FOUND"}:
                err("semantic_unknown_mapping_unsupported", id=ident)
            if material:
                gap("material_unknown", id=ident)
        elif state == "EXCLUDED":
            if not mapping.get("impact"):
                err("semantic_exclusion_impact_missing", id=ident)
            if material and ident not in scope.get("excluded_subject_ids", []):
                err("semantic_material_exclusion_not_declared", id=ident)
            if material:
                gap("material_excluded", id=ident)
        elif material:
            err("semantic_material_obligation_open", id=ident, status=state)
    for rule in rules:
        for ident in rule.get("detail", {}).get("semantic_refs", []):
            if ident not in mappings or rule["id"] not in mappings[ident].get("rule_ids", []):
                err("semantic_rule_reference_not_reciprocal", rule_id=rule["id"], id=ident)

    contents = {(sid, c.get("id")) for sid, a in analyses.items() for c in a.get("content", [])}
    bindings = {}
    for item in doc.get("data_bindings", []):
        key = (item.get("system_id"), item.get("content_id"))
        if key in bindings:
            err("semantic_duplicate_data_binding", content_ref=key)
        bindings[key] = item
        if key not in contents:
            err("semantic_binding_orphan", content_ref=key)
        state = item.get("status")
        if state not in {"BOUND", "PARTIAL", "UNBOUND", "NON_MECHANIC"} or not item.get("rationale"):
            err("semantic_binding_invalid", content_ref=key)
        if state == "BOUND" and not item.get("relation_ids"):
            err("semantic_binding_relation_missing", content_ref=key)
        for ident in item.get("relation_ids", []):
            if ident not in relations:
                err("semantic_binding_unknown_relation", id=ident)
            elif state == "BOUND" and (
                relations[ident].get("trace_status") != "TRACED"
                or mappings.get(ident, {}).get("status") != "REPRESENTED"
            ):
                err("semantic_bound_content_trace_open", content_ref=key, id=ident,
                    trace_status=relations[ident].get("trace_status"), mapping_status=mappings.get(ident, {}).get("status"),
                    hint="BOUND needs every bound relation TRACED and REPRESENTED; otherwise record the binding as PARTIAL")
        if state in {"UNBOUND", "PARTIAL"}:
            gap("content_binding_open", content_ref=key)
    for key in contents - set(bindings):
        err("semantic_content_unaccounted", content_ref=key)
    claim = doc.get("completeness_claim")
    if claim not in {"PARTIAL", "IN_SCOPE_REVIEWED"}:
        err("semantic_completeness_claim_invalid")
    if claim == "IN_SCOPE_REVIEWED" and remaining:
        err("semantic_false_completion", remaining=len(remaining))
    completion = {
        "functions_reviewed": len(
            {fid for fid, f in entries.items() if f.get('status') == 'REVIEWED'}
            & trace_supported
            & reviewed_functions
            & author_supported
            & expected
        ),
        "functions_declared_reviewed": sum(f.get('status') == 'REVIEWED' for f in entries.values()),
        "functions_total": len(expected),
        "functions_unanalyzed": sum(f.get('status') == 'UNANALYZED' for f in entries.values()),
        "functions_surveyed": sum(f.get('status') == 'SURVEYED' for f in entries.values()),
        "functions_trace_supported": len(trace_supported),
        "functions_trace_unsupported": sum(
            f.get('status') in {'TRACED', 'REVIEWED'} and fid not in trace_supported for fid, f in entries.items()
        ),
        "source_review_functions_traced": len(reviewed_functions & expected),
        "material_relations_untraced": sum(
            importance(r, ident) == 'material' and r.get('trace_status') != 'TRACED' for ident, r in relations.items()
        ),
        "material_relations_total": sum(importance(r, ident) == 'material' for ident, r in relations.items()),
        "unknowns_remaining": sum(u.get('status') != 'RESOLVED' for u in unknowns.values()),
        "scope_modules": len(set(module_ids) & set(by_module)),
        "owned_modules": len(owned),
        "deferred_modules": len(excluded_by_kind["deferred"]),
        "framework_modules": len(excluded_by_kind["framework"]),
        "functions_deferred": sum(f["bytecode_sha256"] in deferred_hashes for f in functions),
        "unknowns_total": len(unknowns),
    }
    return {
        "status": "FAILED" if errors else ("PARTIAL" if remaining or claim == "PARTIAL" else "IN_SCOPE_REVIEWED"),
        "errors": errors,
        "warnings": warnings,
        "remaining": remaining,
        "completion": completion,
        "summary": {
            "scope_modules": len(module_ids),
            "registry_functions_in_scope": len(expected),
            "function_states": dict(Counter(f.get("status") for f in entries.values())),
            "observations": len(observations),
            "relations": len(relations),
            "mapping_states": dict(Counter(m.get("status") for m in mappings.values())),
            "source_reviews": len(reviews),
            "unknown_audits": len(audits),
            "data_bindings": len(bindings),
        },
        "checked_evidence": checked_evidence,
        "assurance": "Agent-authored obligations and reciprocal links checked against pinned evidence; no automatic discovery, prose entailment or whole-game completeness proof",
    }
