"""Worker scaffolds and read-only source/contract checks; no semantic inference."""

from __future__ import annotations

import re
from pathlib import Path

from inventory import draft_finished, load, save, safe_file, sha, source_line_count
from function_trace import validate_trace

SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*\Z')
STATES = {'UNANALYZED', 'SURVEYED', 'TRACED', 'REVIEWED'}


def context(run):
    run = Path(run).resolve()
    if not (run / 'config.json').is_file():
        raise ValueError('--run must be the prepared directory containing config.json')
    config = load(run / 'config.json')
    repo = Path(config['repo']).resolve()
    if sha((repo / 'report.json').read_bytes()) != config['input_report_sha256']:
        raise ValueError('input report changed')
    pin = run / 'shared-manifest.json'
    if sha(pin.read_bytes()) != config.get('shared_manifest_sha256'):
        raise ValueError('shared manifest pin changed')
    manifest = {v['path']: v['sha256'] for v in load(pin)['files']}
    for name in ('modules.json', 'functions.json', 'input-manifest.json'):
        if sha((run / 'shared' / name).read_bytes()) != manifest.get(name):
            raise ValueError('shared registry changed: ' + name)
    modules = {v['module_id']: v for v in load(run / 'shared/modules.json')['modules']}
    functions = {v['function_id']: v for v in load(run / 'shared/functions.json')['functions']}
    return run, repo, modules, functions


def system_scope(run, system_id):
    if not isinstance(system_id, str) or not SAFE_ID.fullmatch(system_id):
        raise ValueError('safe --system ID required')
    catalog = load(Path(run) / 'catalog.json')
    matches = [v for v in catalog.get('systems', []) if v.get('id') == system_id]
    if len(matches) != 1:
        raise ValueError('--system must identify one catalog entry')
    return matches[0].get('module_ids', [])


def validate_worker(run, kind, doc, system_id=None, ctx=None):
    """ctx is a context(run) result reused across several workers of one run."""
    run, repo, modules, functions = ctx if ctx is not None else context(run)
    errors, warnings = [], []

    def error(issue, location, **detail):
        errors.append({'issue': issue, 'location': location, **detail})

    def array(obj, key, location, required=True):
        value = obj.get(key)
        if value is None and not required:
            return []
        if not isinstance(value, list):
            error('array_required', location + '.' + key)
            return []
        return value

    def records(items, key, location):
        result = {}
        for i, value in enumerate(items):
            if not isinstance(value, dict):
                error('object_required', f'{location}[{i}]')
                continue
            ident = value.get(key)
            if not isinstance(ident, str) or not ident or ident in result:
                error('duplicate_or_missing_id', f'{location}[{i}].{key}', id=ident)
            else:
                result[ident] = value
        return result

    def text(item, key, location):
        if not isinstance(item.get(key), str) or not item[key].strip():
            error('text_required', location + '.' + key)

    if not isinstance(doc, dict):
        return {'status': 'failed', 'errors': [{'issue': 'object_required', 'location': '$'}], 'warnings': []}
    if not draft_finished(doc):
        error('draft_not_finished', '$.draft')
    if kind == 'source-review':
        if doc.get('schema') != 'source-review/v1':
            error('source_review_schema_invalid', '$.schema')
        if doc.get('specification_seen') is not False:
            error('source_review_not_blind', '$.specification_seen')
        text(doc, 'reviewer', '$')
        scope = doc.get('scope') if isinstance(doc.get('scope'), dict) else {}
        mids = array(scope, 'module_ids', '$.scope')
        text(scope, 'limits', '$.scope')
        if 'mappings' in doc:
            error('source_review_contains_spec_mappings', '$.mappings')
    elif kind == 'analysis':
        mids = system_scope(run, system_id)
        if doc.get('system_id') != system_id:
            error('analysis_system_id_mismatch', '$.system_id')
    else:
        raise ValueError('kind must be analysis or source-review')
    if any(not isinstance(mid, str) for mid in mids):
        error('module_id_string_required', '$.scope.module_ids')
    mids = [mid for mid in mids if isinstance(mid, str)]
    if not mids or len(mids) != len(set(mids)):
        error('module_scope_empty_or_duplicate', '$.scope.module_ids')
    for mid in mids:
        if mid not in modules:
            error('unknown_module_id', '$.scope.module_ids', module_id=mid)
    hashes = {modules[mid]['bytecode_sha256'] for mid in mids if mid in modules}
    expected = {fid for fid, fn in functions.items() if fn['bytecode_sha256'] in hashes}
    sources = {m['original_source']: m for m in modules.values()}
    source_cache = {}
    evidence_count = 0

    def evidence(item, location, required=True):
        nonlocal evidence_count
        evs = array(item, 'evidence', location, required=required)
        if required and not evs:
            error('evidence_required', location + '.evidence')
        for i, ev in enumerate(evs):
            pos = f'{location}.evidence[{i}]'
            evidence_count += 1
            if not isinstance(ev, dict):
                error('object_required', pos)
                continue
            if 'lines' in ev and 'source_file' not in ev:
                error('citation_shorthand_not_normalized', pos, hint='run normalize-worker')
                continue
            source = ev.get('source_file')
            module = sources.get(source) if isinstance(source, str) else None
            if not module:
                error('source_not_in_registry', pos + '.source_file')
                continue
            if source not in source_cache:
                raw = safe_file(repo, source, required=True).read_bytes()
                if sha(raw) != module['source_sha256']:
                    error('source_hash_mismatch', pos)
                source_cache[source] = source_line_count(raw)
            start, end = ev.get('line_start'), ev.get('line_end')
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= source_cache[source]:
                error('invalid_source_span', pos)
                continue
            fid = ev.get('function_id')
            if fid is None:
                if not ev.get('mapping_limit'):
                    error('unmapped_evidence_without_limit', pos)
                else:
                    warnings.append({'issue': 'source_body_unmapped', 'location': pos})
            elif (
                not isinstance(fid, str)
                or fid not in functions
                or functions[fid]['bytecode_sha256'] != module['bytecode_sha256']
            ):
                error('function_source_mismatch', pos + '.function_id', function_id=fid)
            elif not functions[fid]['source_line_start'] <= start <= end <= functions[fid]['source_line_end']:
                error('citation_outside_function', pos, function_id=fid)

    def coverage(items, location, require_all):
        by_id = records(items, 'function_id', location)
        for fid, fn in by_id.items():
            if fid not in expected:
                error('function_outside_scope', location, function_id=fid)
            if not isinstance(fn.get('status'), str) or fn['status'] not in STATES:
                error('function_status_invalid', location, function_id=fid)
            if not isinstance(fn.get('remaining', []), list):
                error('array_required', location + '.remaining')
            validate_trace(fn, evidence, error, location + '.' + fid)
        if require_all:
            for fid in sorted(expected - set(by_id)):
                error('function_unaccounted', location, function_id=fid)
        return by_id

    if kind == 'source-review':
        obs = records(array(doc, 'observations', '$'), 'id', '$.observations')
        rels = records(array(doc, 'relations', '$'), 'id', '$.relations')
        if set(obs) & set(rels):
            error('subject_id_collision', '$')
        subjects = {**obs, **rels}
        for ident, item in subjects.items():
            pos = '$.subjects.' + ident
            text(item, 'statement', pos)
            if not isinstance(item.get('importance'), str) or item['importance'] not in {
                'material',
                'supporting',
                'candidate',
            }:
                error('importance_invalid', pos)
            if 'rule_ids' in item:
                error('source_review_contains_spec_mappings', pos + '.rule_ids')
            evidence(item, pos)
        for ident, rel in rels.items():
            pos = '$.relations.' + ident
            text(rel, 'kind', pos)
            if (
                not isinstance(rel.get('from'), str)
                or not isinstance(rel.get('to'), str)
                or rel['from'] not in obs
                or rel['to'] not in obs
            ):
                error('relation_endpoint_missing', pos)
            trace = array(rel, 'trace_function_ids', pos)
            state = rel.get('trace_status', 'TRACED')
            if not isinstance(state, str) or state not in {'TRACED', 'UNRESOLVED', 'EXCLUDED'}:
                error('trace_status_invalid', pos)
            if not trace and state == 'TRACED':
                error('trace_functions_required', pos + '.trace_function_ids')
            if state != 'TRACED' and not rel.get('trace_limit'):
                error('unresolved_trace_limit_required', pos)
            for fid in trace:
                if not isinstance(fid, str) or fid not in functions:
                    error('unknown_trace_function', pos, function_id=fid)
        fns = coverage(array(doc, 'functions', '$'), '$.functions', True)
        for fid, fn in fns.items():
            for key, collection in (('observation_ids', obs), ('relation_ids', rels)):
                for ident in array(fn, key, '$.functions.' + fid):
                    if not isinstance(ident, str) or ident not in collection:
                        error('function_subject_missing', '$.functions.' + fid, subject_id=ident)
        for i, unknown in enumerate(array(doc, 'unknown_candidates', '$')):
            if not isinstance(unknown, dict):
                error('object_required', f'$.unknown_candidates[{i}]')
            else:
                text(unknown, 'question', f'$.unknown_candidates[{i}]')
        array(doc, 'limitations', '$')
    else:
        rules = {}
        for collection in ('claims', 'contracts'):
            rows = records(array(doc, collection, '$'), 'id', '$.' + collection)
            for ident, item in rows.items():
                pos = '$.' + collection + '.' + ident
                if ident in rules:
                    error('rule_id_collision', pos)
                rules[ident] = item
                label = item.get('kind')
                if not isinstance(label, str) or label not in {
                    'CODE_OBSERVED',
                    'STATIC_INFERENCE',
                    'UNVERIFIED_BEHAVIOR',
                    'INDEPENDENT_DESIGN',
                }:
                    error('claim_kind_invalid', pos)
                if collection == 'claims':
                    text(item, 'statement', pos)
                else:
                    text(item, 'signature', pos)
                if label == 'STATIC_INFERENCE' and (not item.get('derivation') or not item.get('assumptions')):
                    error('inference_basis_required', pos)
                if label == 'INDEPENDENT_DESIGN':
                    text(item, 'rationale', pos)
                evidence(item, pos, required=label != 'INDEPENDENT_DESIGN')
        unknowns = records(array(doc, 'unknowns', '$'), 'id', '$.unknowns')
        for ident, unknown in unknowns.items():
            pos = '$.unknowns.' + ident
            for key in ('question', 'impact'):
                text(unknown, key, pos)
            missing = unknown.get('missing_evidence')
            if not (
                (isinstance(missing, str) and missing.strip())
                or (isinstance(missing, list) and missing and all(isinstance(v, str) and v.strip() for v in missing))
            ):
                error('text_required', pos + '.missing_evidence')
            evidence(unknown, pos, required=False)
            if unknown.get('status', 'OPEN') not in ('OPEN', 'RESOLVED'):
                error('unknown_status_invalid', '$.unknowns.' + ident + '.status')
        for collection in ('content', 'interfaces', 'relationships', 'tests'):
            for i, item in enumerate(array(doc, collection, '$')):
                pos = f'$.{collection}[{i}]'
                if not isinstance(item, dict):
                    error('object_required', pos)
                    continue
                if collection == 'content':
                    evidence(item, pos)
                elif item.get('evidence'):
                    evidence(item, pos)
                for cid in array(item, 'claim_ids', pos, required=False):
                    if not isinstance(cid, str):
                        error('claim_reference_invalid', pos)
                        continue
                    owner, separator, local = cid.partition(':')
                    if separator and owner != system_id:
                        owners = {s['id'] for s in load(run / 'catalog.json').get('systems', [])}
                        if owner not in owners or not local:
                            error('claim_reference_missing', pos, claim_id=cid)
                        else:
                            warnings.append(
                                {
                                    'issue': 'cross_system_reference_requires_integration',
                                    'location': pos,
                                    'claim_id': cid,
                                }
                            )
                    elif (local if separator else cid) not in rules:
                        error('claim_reference_missing', pos, claim_id=cid)
                if collection == 'tests' and (
                    item.get('executed') is not False or item.get('status') != 'SPECIFIED_NOT_EXECUTED'
                ):
                    error('test_execution_claim_forbidden', pos)
        cv = doc.get('coverage') if isinstance(doc.get('coverage'), dict) else {}
        declared = array(cv, 'module_ids', '$.coverage')
        if any(not isinstance(mid, str) for mid in declared) or set(
            mid for mid in declared if isinstance(mid, str)
        ) != set(mids):
            error('coverage_scope_mismatch', '$.coverage.module_ids')
        coverage(array(cv, 'functions', '$.coverage'), '$.coverage.functions', True)
        for fid in array(cv, 'function_ids_analyzed', '$.coverage', required=False):
            if not isinstance(fid, str) or fid not in expected:
                error('function_outside_scope', '$.coverage.function_ids_analyzed', function_id=fid)
    return {
        'status': 'pass' if not errors else 'failed',
        'analysis_status': 'NOT_ASSESSED',
        'kind': kind,
        'errors': errors,
        'warnings': warnings,
        'summary': {
            'scope_modules': len(mids),
            'registry_functions': len(expected),
            'citations_checked': evidence_count,
        },
        'assurance': 'Schema, provenance and references only; not semantic entailment or analysis completion',
    }


def scaffold(run, kind, system_id=None, review_id=None, module_ids=None, reviewer=''):
    run, repo, modules, functions = context(run)
    if kind == 'analysis':
        mids = system_scope(run, system_id)
        output = run / 'analysis' / system_id / 'analysis.json'
        spec = output.with_name('spec.md')
        if output.exists() or spec.exists():
            raise ValueError('worker output already exists; refusing overwrite')
    elif kind == 'source-review':
        if not isinstance(review_id, str) or not SAFE_ID.fullmatch(review_id):
            raise ValueError('safe --id required')
        mids = list(module_ids or [])
        output = run / 'review-drafts' / (review_id + '.json')
        if output.exists():
            raise ValueError('review draft already exists; refusing overwrite')
    else:
        raise ValueError('unknown scaffold kind')
    if not mids or any(mid not in modules for mid in mids):
        raise ValueError('known module IDs required')
    hashes = {modules[mid]['bytecode_sha256'] for mid in mids}
    fns = [
        {'function_id': fid, 'status': 'UNANALYZED', 'remaining': []}
        for fid, fn in functions.items()
        if fn['bytecode_sha256'] in hashes
    ]
    if kind == 'source-review':
        for fn in fns:
            fn.update(observation_ids=[], relation_ids=[])
        doc = {
            'schema': 'source-review/v1',
            'draft': True,
            'reviewer': reviewer,
            'specification_seen': False,
            'scope': {
                'module_ids': mids,
                'entrypoints': [modules[mid]['original_source'] for mid in mids],
                'limits': '',
            },
            'functions': fns,
            'observations': [],
            'relations': [],
            'unknown_candidates': [],
            'cost': {'files_read': None, 'functions_traced': None, 'searches': None, 'elapsed_seconds': None},
            'limitations': [],
        }
    else:
        doc = {
            'schema': 'rgr-analysis/v1',
            'draft': True,
            'system_id': system_id,
            'claims': [],
            'contracts': [],
            'content': [],
            'interfaces': [],
            'relationships': [],
            'tests': [],
            'unknowns': [],
            'coverage': {'module_ids': mids, 'functions': fns, 'remaining': []},
        }
    save(output, doc)
    if kind == 'analysis':
        spec.write_text('# ' + system_id + '\n\nAnalysis pending.\n', encoding='utf-8')
    return {
        'status': 'draft_created',
        'kind': kind,
        'output': str(output),
        'functions': len(fns),
        'next': 'Perform source analysis, finish draft fields, then validate-worker before handoff or freeze-review.',
    }
