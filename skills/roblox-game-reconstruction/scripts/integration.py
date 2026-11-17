"""Mechanical ownership checks and explicit-decision ledger integration."""

from collections import Counter
from copy import deepcopy
from pathlib import Path

from inventory import draft_finished, load, save
from worker_io import SAFE_ID, context, validate_worker


def check_plan(run):
    run, _, modules, _ = context(run)
    catalog, plan = load(run / 'catalog.json'), load(run / 'analysis-plan.json')
    missing = load(run / 'shared/modules.json').get('missing', [])
    return check_ownership(catalog, plan, set(modules) | {v['module_id'] for v in missing})


def check_ownership(catalog, plan, inventory):
    errors = []

    def error(issue, **detail):
        errors.append({'issue': issue, **detail})

    systems = catalog.get('systems', [])
    ids = [s.get('id') for s in systems]
    if not ids:
        error('systems_missing')
    for sid in ids:
        if not isinstance(sid, str) or not SAFE_ID.fullmatch(sid):
            error('invalid_system_id', system_id=sid)
        elif ids.count(sid) != 1:
            error('duplicate_system_id', system_id=sid)
    owned = [mid for s in systems for mid in s.get('module_ids', [])]
    excluded = [v.get('module_id') for v in catalog.get('exclusions', [])]
    for mid in sorted(inventory | set(owned) | set(excluded)):
        if mid not in inventory:
            error('unknown_inventory_module_id', module_id=mid)
        count = owned.count(mid) + excluded.count(mid)
        if count != 1:
            error('module_ownership', module_id=mid, count=count)
    for entry in catalog.get('exclusions', []):
        if not entry.get('reason') or not entry.get('impact'):
            error('exclusion_reason_and_impact_required', module_id=entry.get('module_id'))
    planned = [a.get('system_id') for a in plan.get('assignments', [])]
    for sid in set(ids) | set(planned):
        if sid not in ids or planned.count(sid) != 1:
            error('system_plan_coverage', system_id=sid, count=planned.count(sid))
    return {
        'status': 'failed' if errors else 'pass',
        'errors': errors,
        'summary': {
            'inventory_modules': len(inventory),
            'owned_modules': len(set(owned) & inventory),
            'excluded_modules': len(set(excluded) & inventory),
            'systems': len(ids),
        },
        'assurance': 'Ownership and plan accounting, not semantic discovery or worker execution',
    }


def inputs(run):
    run, repo, modules, functions = context(run)
    checked = check_plan(run)
    if checked['errors']:
        raise ValueError('plan preflight failed: ' + str(checked['errors']))
    analyses = {}
    for system in load(run / 'catalog.json')['systems']:
        sid = system['id']
        analysis = load(run / 'analysis' / sid / 'analysis.json')
        result = validate_worker(run, 'analysis', analysis, sid)
        if result['errors']:
            raise ValueError('worker preflight failed: ' + sid + ': ' + str(result['errors']))
        analyses[sid] = analysis
    return run, repo, modules, functions, analyses


def scaffold_integration(run):
    run, _, modules, _, analyses = inputs(run)
    output = run / 'integration-decisions.json'
    if output.exists():
        raise ValueError('integration draft already exists; refusing overwrite')
    config = load(run / 'config.json')
    observations, relations, reviews = [], [], []
    for rid in sorted(config.get('source_review_pins', {})):
        if not SAFE_ID.fullmatch(rid):
            raise ValueError('unsafe review ID')
        review = load(run / 'source-reviews' / rid / 'source-first.json')
        names = {v['id']: rid + ':' + v['id'] for v in review['observations'] + review['relations']}
        for key, dest in (('observations', observations), ('relations', relations)):
            for value in review[key]:
                value = deepcopy(value)
                original = value['id']
                value.update(id=names[original], origin={'review_id': rid, 'item_id': original})
                if key == 'relations':
                    for endpoint in ('from', 'to'):
                        value[endpoint] = names[value[endpoint]]
                    value.setdefault('trace_status', 'TRACED')
                dest.append(value)
        reviews.append({'review_id': rid})
    if not reviews:
        raise ValueError('freeze a source-only review before integration')
    counts = Counter(f['function_id'] for a in analyses.values() for f in a['coverage']['functions'])
    mids = sorted({mid for a in analyses.values() for mid in a['coverage']['module_ids'] if mid in modules})
    doc = {
        'schema': 'rgr-integration/v1',
        'draft': True,
        'scope': {'module_ids': mids, 'rationale': '', 'declared_full_game': False},
        'function_owners': {fid: '' for fid, count in counts.items() if count > 1},
        'reviewed_function_ids': [],
        'observations': observations,
        'relations': relations,
        'source_reviews': reviews,
        'mappings': [
            {'subject_id': s['id'], 'status': 'MISSING', 'rule_ids': [], 'rationale': ''}
            for s in observations + relations
        ],
        'unknown_audits': [
            {
                'system_id': sid,
                'unknown_id': u['id'],
                'question': u['question'],
                'status': 'NOT_INVESTIGATED',
                'rationale': 'Investigation not yet recorded.',
                'searched_sources': [],
                'queries': [],
                'inspected_function_ids': [],
                'evidence': [],
            }
            for sid, a in analyses.items()
            for u in a['unknowns']
        ],
        'data_bindings': [
            {
                'system_id': sid,
                'content_id': c['id'],
                'status': 'UNBOUND',
                'relation_ids': [],
                'rationale': 'Consumer binding not yet recorded.',
            }
            for sid, a in analyses.items()
            for c in a['content']
        ],
        'completeness_claim': 'PARTIAL',
    }
    save(output, doc)
    return {
        'status': 'draft_created',
        'output': str(output),
        'next': 'Compare source obligations with claims; record explicit mappings, investigations and review decisions; set draft false, then integrate.',
    }


def integrate(run, decision_file):
    """Preflight the entire proposed join before changing generated run metadata.

    Only semantic_refs on claims/contracts and semantic-coverage.json are written.
    Frozen reviews, rule prose, source and worker function traces are preserved.
    """
    run, _, modules, functions, analyses = inputs(run)
    doc = load(Path(decision_file))
    if not isinstance(doc, dict) or doc.get('schema') != 'rgr-integration/v1' or not draft_finished(doc):
        raise ValueError('finished rgr-integration/v1 decisions with draft:false required')
    if (
        not isinstance(doc.get('scope'), dict)
        or not isinstance(doc['scope'].get('module_ids'), list)
        or any(not isinstance(mid, str) for mid in doc['scope']['module_ids'])
    ):
        raise ValueError('scope.module_ids must be an array of registry IDs')
    for key in ('observations', 'relations', 'source_reviews', 'mappings', 'unknown_audits', 'data_bindings'):
        if not isinstance(doc.get(key), list) or any(not isinstance(v, dict) for v in doc[key]):
            raise ValueError(key + ' must be an array of objects')
    if doc.get('completeness_claim') not in {'PARTIAL', 'IN_SCOPE_REVIEWED'}:
        raise ValueError('completeness_claim must be PARTIAL or IN_SCOPE_REVIEWED')
    for s in doc['observations'] + doc['relations']:
        erratum = s.get('citation_erratum')
        if erratum is not None and (
            not isinstance(erratum, dict)
            or not isinstance(erratum.get('evidence', []), list)
            or any(not isinstance(e, dict) for e in erratum.get('evidence', []))
        ):
            raise ValueError('citation_erratum must be an object with an evidence array: ' + str(s.get('id')))
        evidence = s.get('evidence', [])
        if not isinstance(evidence, list) or any(not isinstance(e, dict) for e in evidence):
            raise ValueError('evidence must be an array of objects: ' + str(s.get('id')))
    for m in doc['mappings']:
        if not isinstance(m.get('rule_ids', []), list) or any(not isinstance(v, str) for v in m.get('rule_ids', [])):
            raise ValueError('mapping rule_ids must be an array of canonical rule IDs')
    ledger = {
        k: deepcopy(doc[k])
        for k in (
            'scope',
            'observations',
            'relations',
            'source_reviews',
            'mappings',
            'unknown_audits',
            'data_bindings',
            'completeness_claim',
        )
    }
    ledger['schema'] = 'rgr-semantic-coverage/v1'
    hashes = {modules[mid]['bytecode_sha256'] for mid in ledger['scope']['module_ids'] if mid in modules}
    expected = {fid for fid, f in functions.items() if f['bytecode_sha256'] in hashes}
    candidates = {}
    for sid, a in analyses.items():
        for f in a['coverage']['functions']:
            if f['function_id'] in expected:
                candidates.setdefault(f['function_id'], {})[sid] = f
    owners = doc.get('function_owners', {})
    if not isinstance(owners, dict) or any(
        not isinstance(fid, str) or not isinstance(owner, str) for fid, owner in owners.items()
    ):
        raise ValueError('function_owners must map registry IDs to system IDs')
    for fid, owner in owners.items():
        if fid not in candidates or owner not in candidates[fid]:
            raise ValueError('invalid function owner: ' + fid)
    reviewed = doc.get('reviewed_function_ids', [])
    if (
        not isinstance(reviewed, list)
        or any(not isinstance(fid, str) for fid in reviewed)
        or len(set(reviewed)) != len(reviewed)
        or set(reviewed) - expected
    ):
        raise ValueError('reviewed_function_ids must be unique in-scope registry IDs')
    subjects = ledger['observations'] + ledger['relations']
    ledger['functions'] = []
    for fid in sorted(expected):
        choices = candidates.get(fid, {})
        owner = owners.get(fid) or (next(iter(choices)) if len(choices) == 1 else None)
        if owner not in choices:
            raise ValueError('explicit function owner required: ' + fid)
        entry = deepcopy(choices[owner])
        entry['system_id'] = owner
        if entry['status'] == 'REVIEWED':
            entry['status'] = 'TRACED'
        if fid in reviewed:
            if entry['status'] != 'TRACED':
                raise ValueError('review decision lacks author trace: ' + fid)
            entry['status'] = 'REVIEWED'
        entry['observation_ids'], entry['relation_ids'] = [], []
        for s in subjects:
            citations = s.get('citation_erratum', {}).get('evidence', s.get('evidence', []))
            if any(e.get('function_id') == fid for e in citations):
                entry['relation_ids' if 'from' in s else 'observation_ids'].append(s['id'])
        ledger['functions'].append(entry)
    # Rebuild only the reciprocal metadata from the explicit adjudications.
    updated = deepcopy(analyses)
    rules = []
    for sid, a in updated.items():
        for key in ('claims', 'contracts'):
            for rule in a[key]:
                rule['semantic_refs'] = sorted(
                    {m['subject_id'] for m in ledger['mappings'] if sid + ':' + rule['id'] in m.get('rule_ids', [])}
                )
                rules.append({'id': sid + ':' + rule['id'], 'detail': rule})
    from semantic import audit_semantics

    result = audit_semantics(
        run, load(run / 'config.json'), list(modules.values()), list(functions.values()), rules, updated, ledger=ledger
    )
    if result['errors']:
        return {'status': 'failed', 'errors': result['errors'], 'writes': 0}
    for sid, analysis in updated.items():
        save(run / 'analysis' / sid / 'analysis.json', analysis)
    save(run / 'semantic-coverage.json', ledger)
    return {
        'status': 'pass',
        'analysis_status': result['status'],
        'completion': result['completion'],
        'functions': len(ledger['functions']),
        'mappings': len(ledger['mappings']),
        'assurance': 'Explicit decisions joined and checked; no automatic semantic judgment or review promotion',
    }
