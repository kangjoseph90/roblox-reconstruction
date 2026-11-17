"""Agent-facing input/output helpers: bounded CLI results, registry queries and citation shorthand.

None of these helpers make semantic decisions. Queries only filter the prepared registry;
normalization only expands shorthand citations to the canonical form validate-worker checks.
"""
from collections import Counter
import json
from pathlib import Path

from inventory import load, posix_path, safe_file, save, source_line_count
from worker_io import context, system_scope, validate_worker

LIST_LIMIT = 20


def summarize(result, full_path=None, limit=LIST_LIMIT):
    """Bound top-level issue lists in a CLI result. Counts by issue are kept so nothing
    disappears silently; `full_path` names where the complete result was written, if anywhere."""
    if not isinstance(result, dict):
        return result
    truncated = {}
    out = {}
    for key, value in result.items():
        if isinstance(value, list) and len(value) > limit:
            counts = Counter(v.get('issue', '?') if isinstance(v, dict) else '?' for v in value)
            truncated[key] = {'total': len(value), 'shown': limit, 'by_issue': dict(counts.most_common())}
            out[key] = value[:limit]
        else:
            out[key] = value
    if truncated:
        out['truncated'] = truncated
        out['full_result'] = str(full_path) if full_path else 'rerun with --full'
    return out


def _registry(run):
    run = Path(run).resolve()
    modules = load(run / 'shared/modules.json')['modules']
    functions = load(run / 'shared/functions.json')['functions']
    catalog = load(run / 'catalog.json') if (run / 'catalog.json').is_file() else {}
    return run, modules, functions, catalog


def _rows(header, rows, limit):
    lines = ['\t'.join(header)]
    lines += ['\t'.join('' if v is None else str(v) for v in row) for row in rows[:limit]]
    if len(rows) > limit:
        lines.append('# %d more rows; narrow the filter or raise --limit' % (len(rows) - limit))
    return '\n'.join(lines)


def cfg_records(run, function_id):
    """Full comparison records for one registry function: certificates that match its recompiled
    proto and diagnoses of original protos that were compared with it."""
    run, modules, functions, _ = _registry(run)
    fn = next((f for f in functions if f['function_id'] == function_id), None)
    if fn is None:
        raise ValueError('unknown function_id: ' + str(function_id))
    module = next((m for m in modules if m['bytecode_sha256'] == fn['bytecode_sha256'] and m.get('cfg_evidence')), None)
    if module is None:
        return {'function_id': function_id, 'comparison': None, 'certified': [], 'unverified': [], 'note': 'no CFG evidence recorded'}
    optimization = function_id.rsplit(':', 2)[1].lstrip('O')
    rel = '%s/comparison-O%s.json' % (module['cfg_evidence'], optimization)
    comparison = load(safe_file(Path(load(run / 'config.json')['repo']), rel, required=True))
    index = fn['recompiled_proto']
    records = comparison.get('functions', [])
    return {'function_id': function_id, 'comparison': rel,
            'certified': [r for r in records if r.get('status') == 'CFG_VALIDATED' and index in r.get('matching_recovered_protos', [])],
            'unverified': [r for r in records if r.get('status') != 'CFG_VALIDATED' and r.get('recompiled_proto') == index]}


def query(run, what, system=None, module=None, path=None, name=None, line=None, method=None, limit=200, unowned=False):
    """Compact tab-separated rows from the prepared registry instead of whole JSON files."""
    run, modules, functions, catalog = _registry(run)
    selected = None
    if system:
        matches = [s for s in catalog.get('systems', []) if s.get('id') == system]
        if len(matches) != 1:
            raise ValueError('--system must identify one catalog entry')
        selected = set(matches[0].get('module_ids', []))
    path_of = lambda m: '/'.join(m['instance_path'])

    def module_selected(m):
        return ((selected is None or m['module_id'] in selected) and (module is None or m['module_id'] == module)
                and (path is None or path.lower() in path_of(m).lower() or path.lower() in m['original_source'].lower()))

    chosen = [m for m in modules if module_selected(m)]
    if unowned:
        if what != 'modules':
            raise ValueError('--unowned applies to modules')
        owned = {mid for s in catalog.get('systems', []) for mid in s.get('module_ids', [])}
        owned |= {e.get('module_id') for e in catalog.get('exclusions', [])}
        missing = load(run / 'shared/modules.json').get('missing', [])
        rows = [(m['module_id'], path_of(m), m['original_source'], 'source') for m in chosen if m['module_id'] not in owned]
        rows += [(m['module_id'], '/'.join(m['instance_path']), m.get('source', ''), 'missing: ' + m.get('cause', ''))
                 for m in missing if m['module_id'] not in owned and (path is None or path.lower() in '/'.join(m['instance_path']).lower())]
        return _rows(['module_id', 'instance_path', 'source_file', 'kind'], rows, limit)
    if what == 'modules':
        counts = Counter(f['bytecode_sha256'] for f in functions)
        rows = [(m['module_id'], path_of(m), m['original_source'], m.get('source_line_count'),
                 counts.get(m['bytecode_sha256'], 0), m.get('module_cfg')) for m in chosen]
        return _rows(['module_id', 'instance_path', 'source_file', 'lines', 'functions', 'module_cfg'], rows, limit)
    if what == 'functions':
        source_line = None
        if line:
            source_file, _, number = line.rpartition(':')
            if not source_file or not number.isdigit():
                raise ValueError('--line must be SOURCE_FILE:LINE')
            source_line = (posix_path(source_file), int(number))
            chosen = [m for m in chosen if m['original_source'] == source_line[0]]
        sources = {}
        for m in chosen:  # first chosen module per bytecode supplies the citable source file
            sources.setdefault(m['bytecode_sha256'], m['original_source'])
        rows = []
        for f in functions:
            source = sources.get(f['bytecode_sha256'])
            if source is None or (name and name.lower() not in (f.get('name') or '').lower()):
                continue
            if source_line and not f['source_line_start'] <= source_line[1] <= f['source_line_end']:
                continue
            rows.append((f['function_id'], f.get('name') or '', source, f['source_line_start'], f['source_line_end'], f.get('cfg_status')))
        if source_line:  # innermost body first
            rows.sort(key=lambda r: r[4] - r[3])
        return _rows(['function_id', 'name', 'source_file', 'line_start', 'line_end', 'cfg_status'], rows, limit)
    if what == 'remotes':
        calls = load(run / 'shared/remote-candidates/calls.json')
        sources = {}
        for m in chosen:
            sources.setdefault(m['bytecode_sha256'], m['original_source'])
        rows = [(c['method'], c['form'], sources[c['bytecode_sha256']], c['line_start'])
                for c in calls if c['bytecode_sha256'] in sources and (method is None or c['method'] == method)]
        return _rows(['method', 'form', 'source_file', 'line'], rows, limit)
    if what == 'requires':
        rows = [(m['module_id'], path_of(m), r.get('line_start'), '/'.join(r.get('resolved_instance_path') or []) or r.get('expression', ''),
                 r.get('resolution'), ','.join(r.get('target_module_ids', [])))
                for m in chosen for r in m.get('requires', [])]
        return _rows(['module_id', 'instance_path', 'line', 'target_path', 'resolution', 'target_module_ids'], rows, limit)
    raise ValueError('unknown query: ' + str(what))


def cite(run, spec):
    """Canonical citation for FILE:START[-END]: the innermost registry function whose body
    contains the span. Without one, function_id is null and mapping_limit is left empty for
    the author to explain (validation rejects an empty limit)."""
    run, modules, functions, _ = _registry(run)
    source, _, span = spec.rpartition(':')
    start, _, end = span.partition('-')
    if not source or not start.isdigit() or (end and not end.isdigit()):
        raise ValueError('expected FILE:START or FILE:START-END')
    source, start, end = posix_path(source), int(start), int(end or start)
    module = next((m for m in modules if m['original_source'] == source), None)
    if module is None:
        raise ValueError('source file is not in the registry: ' + source)
    config = load(run / 'config.json')
    lines = source_line_count(safe_file(Path(config['repo']), source, required=True).read_bytes())
    if not 1 <= start <= end <= lines:
        raise ValueError('span %d-%d is outside %s (1-%d)' % (start, end, source, lines))
    bodies = [f for f in functions if f['bytecode_sha256'] == module['bytecode_sha256']
              and f['source_line_start'] <= start <= end <= f['source_line_end']]
    citation = {'source_file': source, 'function_id': None, 'line_start': start, 'line_end': end}
    if bodies:
        citation['function_id'] = min(bodies, key=lambda f: f['source_line_end'] - f['source_line_start'])['function_id']
    else:
        citation['mapping_limit'] = ''
    return citation


def _scope_modules(run, kind, doc, system_id):
    if kind == 'analysis':
        return system_scope(run, system_id)
    scope = doc.get('scope') if isinstance(doc, dict) else None
    return scope.get('module_ids', []) if isinstance(scope, dict) else []


def normalize_worker(run, kind, path, system_id=None):
    """Expand trace citation shorthand {"lines": [start, end]} into canonical citations of the
    trace's own function, write the file, then validate it with the unchanged worker contract.
    Nothing is written when an expansion is impossible."""
    ctx = context(run)
    run, _, modules, functions = ctx
    path = Path(path)
    doc = load(path)
    if kind == 'analysis' and not system_id:
        raise ValueError('--system is required for analysis')
    scoped = [mid for mid in _scope_modules(run, kind, doc, system_id) if mid in modules]
    ordered = [modules[mid] for mid in scoped] + list(modules.values())
    source_by_bytecode = {}
    for m in ordered:  # worker scope first, then registry order
        source_by_bytecode.setdefault(m['bytecode_sha256'], m['original_source'])
    entries = (doc.get('coverage') or {}).get('functions', []) if kind == 'analysis' else doc.get('functions', [])
    expanded, problems = 0, []
    for entry in entries if isinstance(entries, list) else []:
        fid = entry.get('function_id') if isinstance(entry, dict) else None
        trace = entry.get('trace') if isinstance(entry, dict) else None
        steps = trace.get('steps') if isinstance(trace, dict) else None
        for s, step in enumerate(steps if isinstance(steps, list) else []):
            evidence = step.get('evidence') if isinstance(step, dict) else None
            for e, ev in enumerate(evidence if isinstance(evidence, list) else []):
                if not isinstance(ev, dict) or 'lines' not in ev:
                    continue
                where = '%s.trace.steps[%d].evidence[%d]' % (fid, s, e)
                lines = ev['lines']
                if set(ev) != {'lines'} or not (isinstance(lines, list) and len(lines) == 2 and all(type(v) is int for v in lines)):
                    problems.append({'issue': 'citation_shorthand_invalid', 'location': where})
                    continue
                if fid not in functions or functions[fid]['bytecode_sha256'] not in source_by_bytecode:
                    problems.append({'issue': 'citation_shorthand_function_unknown', 'location': where})
                    continue
                evidence[e] = {'source_file': source_by_bytecode[functions[fid]['bytecode_sha256']],
                               'function_id': fid, 'line_start': lines[0], 'line_end': lines[1]}
                expanded += 1
    if problems:
        return {'status': 'failed', 'written': False, 'expanded': 0, 'errors': problems}
    if expanded:
        save(path, doc)
    result = validate_worker(run, kind, doc, system_id, ctx)
    return dict(result, written=bool(expanded), expanded=expanded, output=str(path))
