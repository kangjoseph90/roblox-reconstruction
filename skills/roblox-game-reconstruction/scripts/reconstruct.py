#!/usr/bin/env python3
"""Portable mechanical stages; the host agent performs discovery and analysis."""
import argparse
import collections
import datetime
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys
import zipfile

from inventory import VERSION, build, load, save, safe_file, sha


def repo_path(value):
    p = Path(value).resolve()
    if (p / 'repository/report.json').is_file():
        p = p / 'repository'
    if not (p / 'report.json').is_file():
        raise ValueError('expected recovered report.json or repository/report.json')
    return p


def new_dir(p):
    p = Path(p).resolve()
    if p.exists() and any(p.iterdir()):
        raise ValueError('output must be new/empty: ' + str(p))
    p.mkdir(exist_ok=True, parents=True)
    return p


def prepare(repo, run):
    repo = repo_path(repo)
    run = Path(run).resolve()
    if run == repo or repo in run.parents:
        raise ValueError('run directory must be outside the immutable repository')
    run = new_dir(run)
    result = build(repo, run / 'shared')
    shared_files = [
        {'path': p.relative_to(run / 'shared').as_posix(), 'sha256': sha(p.read_bytes())}
        for p in sorted((run / 'shared').rglob('*'))
        if p.is_file()
    ]
    save(run / 'shared-manifest.json', {'files': shared_files})
    save(
        run / 'config.json',
        {
            'schema': 'rgr-run/v1',
            'skill_version': VERSION,
            'repo': str(repo),
            'input_report_sha256': sha((repo / 'report.json').read_bytes()),
            'shared_manifest_sha256': sha((run / 'shared-manifest.json').read_bytes()),
            'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
    )
    save(
        run / 'workflow.json',
        {
            'stage': 'inventory_complete',
            'analysis_execution': 'NOT_STARTED',
            'next': [
                'discover systems in this repository',
                'write catalog.json and analysis-plan.json',
                'dispatch isolated workers',
                'cross-review',
                'audit',
                'package',
            ],
            'inventory': result,
        },
    )
    return {
        'status': 'awaiting_agent_analysis',
        'run': str(run),
        'inventory': result,
        'meaning': 'Inventory is complete; semantic discovery, parallel analysis and review still require the host agent.',
    }


def audit_run(run):
    from evidence import audit

    return audit(Path(run).resolve())


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def source_page(path, target, title):
    text = path.read_text(encoding='utf-8')
    lines = ''.join(
        '<span id="L%d"><a href="#L%d">%d</a> %s</span>' % (n, n, n, html.escape(line))
        for n, line in enumerate(text.split('\n'), 1)
    )
    body = (
        '<!doctype html><meta charset="utf-8"><title>'
        + html.escape(title)
        + '</title><style>body{font:14px/1.6 monospace;margin:24px}span{display:block;white-space:pre-wrap;overflow-wrap:anywhere}span:target{background:#fff0ac}a{color:#52718d}</style><h1>'
        + html.escape(title)
        + '</h1><p>Recovered source, not original authored source. Static CFG scope is separate; behavior NOT_TESTED.</p>'
        + lines
    )
    target.write_text(body, encoding='utf-8')


def package(run, out):
    run = Path(run).resolve()
    checked = audit_run(run)
    if checked['status'] != 'pass':
        raise ValueError('audit failed; inspect ' + str(run / 'audit.json'))
    cfg = load(run / 'config.json')
    repo = repo_path(cfg['repo'])
    out = Path(out).resolve()
    if out == repo or repo in out.parents or out == run or run in out.parents:
        raise ValueError('package must be outside input and run directories')
    if out.with_name(out.name + '.zip').exists():
        raise ValueError('package archive already exists: ' + str(out.with_name(out.name + '.zip')))
    out = new_dir(out)
    catalog = load(run / 'catalog.json')
    rules = load(run / 'rules.json')
    if isinstance(rules, list):
        rules = {'rules': rules}
    modules = load(run / 'shared/modules.json')['modules']
    by_source = {m['original_source']: m for m in modules}
    shutil.copytree(run / 'shared', out / 'evidence/shared', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(run / 'analysis', out / 'analysis', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ['catalog.json', 'analysis-plan.json', 'review.json', 'conflicts.json', 'audit.json']:
        copy_file(run / name, out / name)
    for name in ['semantic-coverage.json', 'semantic-audit.json', 'integration-decisions.json']:
        if (run / name).is_file():
            copy_file(run / name, out / name)
    if (run / 'source-reviews').is_dir():
        shutil.copytree(run / 'source-reviews', out / 'source-reviews')
    copy_file(run / 'config.json', out / 'provenance/run-config.json')
    manifest_input = load(run / 'shared/input-manifest.json')
    for entry in manifest_input['files_used']:
        source = safe_file(repo, entry['path'], required=True)
        if sha(source.read_bytes()) != entry['sha256']:
            raise ValueError('input changed during packaging: ' + entry['path'])
        copy_file(source, out / 'evidence/repository' / entry['path'])
    copy_file(repo / 'report.json', out / 'evidence/repository/report.json')
    for m in {m['source_snapshot']: m for m in modules}.values():
        snapshot = out / 'evidence/shared' / m['source_snapshot']
        source_page(snapshot, snapshot.with_suffix('.html'), '/'.join(m['instance_path']))
    for r in rules['rules']:
        for e in r.get('evidence', []):
            source = e.get('source_file')
            m = by_source.get(source)
            if not m:
                continue
            e['package_source'] = 'evidence/repository/' + source
            e['source_snapshot'] = 'evidence/shared/' + m['source_snapshot']
            e['source_html'] = (
                str(Path(e['source_snapshot']).with_suffix('.html')).replace('\\', '/')
                + '#L'
                + str(e.get('line_start', 1))
            )
            e['package_bytecode'] = (
                'evidence/repository/' + m['original_bytecode']
                if m.get('original_bytecode') and (out / 'evidence/repository' / m['original_bytecode']).is_file()
                else None
            )
            if m.get('cfg_evidence'):
                opt = e.get('optimization', m.get('best_optimization'))
                candidate = out / 'evidence/repository' / m['cfg_evidence'] / ('comparison-O%s.json' % opt)
                if candidate.is_file():
                    e['package_comparison'] = candidate.relative_to(out).as_posix()
    save(out / 'rules.json', rules)
    analyses = {s['id']: load(run / 'analysis' / s['id'] / 'analysis.json') for s in catalog['systems']}
    for field, filename in [
        ('content', 'content.json'),
        ('interfaces', 'interfaces.json'),
        ('relationships', 'system-graph.json'),
        ('tests', 'test-vectors.json'),
        ('unknowns', 'unknowns.json'),
    ]:
        save(
            out / filename,
            {
                'schema': 'rgr-' + field + '/v1',
                'systems': {sid: a.get(field, []) for sid, a in analyses.items()},
                'scope': 'Agent-authored, source-backed contracts and explicitly tracked limits; not verified server behavior',
            },
        )
    copy_file(run / 'shared/dependency-graph.json', out / 'module-dependency-candidates.json')
    save(
        out / 'coverage.json',
        {
            'systems': {sid: a.get('coverage', {}) for sid, a in analyses.items()},
            'exclusions': catalog.get('exclusions', []),
            'completion': checked['completion'],
            'source_less_instances': load(run / 'shared/modules.json').get('missing', []),
            'scope': 'Assigned/mentioned modules do not imply all functions were analyzed. Candidate graph edges are not semantic or hard-unlock proofs.',
        },
    )
    kinds = collections.Counter(r.get('kind', 'UNKNOWN') for r in rules['rules'])
    summary = {
        'schema': 'rgr-package/v1',
        'skill_version': VERSION,
        'title': catalog.get('title', 'Recovered game specification'),
        'systems': len(analyses),
        'rule_entries': len(rules['rules']),
        'entry_kinds': dict(collections.Counter(r.get('entry_kind', 'implementation_rule') for r in rules['rules'])),
        'evidence_kinds': dict(kinds),
        'citations': sum(len(r.get('evidence', [])) for r in rules['rules']),
        'inventory': load(run / 'shared/summary.json'),
        'review_mode': load(run / 'review.json')['mode'],
        'unknowns': sum(len(a.get('unknowns', [])) for a in analyses.values()),
        'excluded_modules': len(catalog.get('exclusions', [])),
        'semantic_status': checked['summary'].get('semantic_status', 'NOT_ASSESSED'),
        'analysis_status': checked['analysis_status'],
        'completion': checked['completion'],
        'semantic_coverage': checked['summary'].get('semantic', {}),
        'third_party_behavior': 'NOT_TESTED',
        'audit': checked['status'],
        'limitations': [
            'Mechanical audit does not certify completeness or semantic equivalence',
            'No server/reset/save behavior is inferred from client requests or UI',
            'Review scope and remaining functions are in review.json and coverage.json',
        ],
    }
    save(out / 'summary.json', summary)
    title = catalog.get('title', 'Recovered game specification')
    text = '# ' + title + '\n\nGenerated with roblox-game-reconstruction ' + VERSION + '.\n\n'
    progress = checked['completion']
    number = lambda key: str(progress[key]) if progress.get(key) is not None else 'not assessed'
    completion_text = (
        'Semantic scope: '
        + number('scope_modules')
        + '/'
        + number('owned_modules')
        + ' catalog-owned modules; reviewed functions in scope: '
        + number('functions_reviewed')
        + '/'
        + number('functions_total')
        + '; supported function traces: '
        + number('functions_trace_supported')
        + '; surveyed: '
        + number('functions_surveyed')
        + '; unanalyzed: '
        + number('functions_unanalyzed')
        + '; source-review traces: '
        + number('source_review_functions_traced')
        + '; material relations not traced: '
        + number('material_relations_untraced')
        + '; open questions: '
        + number('unknowns_remaining')
    )
    text += (
        '**Analysis: '
        + checked['analysis_status']
        + ' · Evidence audit: '
        + checked['status']
        + '**\n\n'
        + completion_text
        + '\n\n'
    )
    text += 'This specification preserves source observations, explicit inference, unverified behavior and independent design separately. Missing server or instance data remains unknown. The input source was not modified.\n\n'
    semantic = load(run / 'semantic-audit.json')
    text += (
        '## Semantic scope and remaining work\n\nStatus: **'
        + semantic['status']
        + '**. This is evidence accounting, not automatic semantic proof.\n\n```json\n'
        + json.dumps(semantic['summary'], ensure_ascii=False, indent=2)
        + '\n```\n\n'
    )
    for item in semantic.get('remaining', []):
        text += '- ' + json.dumps(item, ensure_ascii=False) + '\n'
    if (run / 'semantic-coverage.json').is_file():
        ledger = load(run / 'semantic-coverage.json')
        text += '\n### Observed relationships and their specification mappings\n\n'
        mappings = {m['subject_id']: m for m in ledger.get('mappings', [])}
        for rel in ledger.get('relations', []):
            mapping = mappings.get(rel['id'], {})
            text += (
                '- **'
                + rel['id']
                + '** ('
                + rel.get('trace_status', 'UNRESOLVED')
                + '): '
                + rel['statement']
                + ' — '
                + mapping.get('status', 'UNMAPPED')
                + ' '
                + ', '.join(mapping.get('rule_ids', []))
                + '\n'
            )
        text += '\nSee [semantic-coverage.json](semantic-coverage.json) for observations, source locations, content bindings and UNKNOWN search records; [semantic-audit.json](semantic-audit.json) for unresolved scope. Frozen source-first reviews are under source-reviews/.\n\n'
    text += '## Systems and implementation contracts\n\n'
    for s in catalog['systems']:
        text += '### ' + s['title'] + '\n\nDiscovery: ' + str(s.get('discovery_basis', 'recorded in catalog')) + '\n\n'
        text += (
            '[System specification](analysis/'
            + s['id']
            + '/spec.md) · [Structured analysis](analysis/'
            + s['id']
            + '/analysis.json)\n\n'
        )
        text += (run / 'analysis' / s['id'] / 'spec.md').read_text(encoding='utf-8') + '\n\n'
    text += '## Evidence, review and limits\n\nSee rules.json for source lines and computed CFG scope; review.json/conflicts.json for semantic findings; unknowns.json and coverage.json for missing authority/data and unexamined functions. Test vectors are specified, not game execution results. module-dependency-candidates.json contains static path candidates, not a proved call graph.\n'
    (out / 'implementation-spec.md').write_text(text, encoding='utf-8')
    # A small static browser is sufficient; no browser/API dependency or external assets.
    body = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + html.escape(title)
        + '</title><style>body{font:16px/1.6 system-ui;max-width:1150px;margin:35px auto;padding:20px;color:#183245}article{border:1px solid #ccd8e2;border-radius:8px;padding:16px;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#075e9a}.limits{background:#fff1cf;padding:16px}</style><h1>'
        + html.escape(title)
        + '</h1><p class="limits">Source-based specification with explicit limitations. Server behavior and semantic completeness are not automatically certified.</p><p><a href="implementation-spec.md">Implementation specification</a> · <a href="catalog.json">System catalog</a> · <a href="interfaces.json">Interfaces</a> · <a href="content.json">Content</a> · <a href="unknowns.json">Unknowns</a> · <a href="test-vectors.json">Test vectors</a></p><pre>'
        + html.escape(json.dumps(summary, indent=2, ensure_ascii=False))
        + '</pre>'
    )
    banner = (
        '<section class="limits" aria-label="Analysis completion"><h2>Analysis: '
        + html.escape(checked['analysis_status'])
        + '</h2><p>Evidence audit: '
        + checked['status']
        + '</p><p>'
        + html.escape(completion_text)
        + '</p></section>'
    )
    body = body.replace('</h1>', '</h1>' + banner, 1)
    for s in catalog['systems']:
        body += (
            '<article><h2>'
            + html.escape(s['title'])
            + '</h2><a href="analysis/'
            + s['id']
            + '/analysis.json">Structured function/state contracts</a><pre>'
            + html.escape((run / 'analysis' / s['id'] / 'spec.md').read_text(encoding='utf-8'))
            + '</pre></article>'
        )
    body += (
        '<h2>Semantic coverage and open work</h2><p><a href="semantic-audit.json">Checked scope</a> · <a href="semantic-coverage.json">Observations, relationships, mappings and UNKNOWN investigations</a></p><pre>'
        + html.escape(json.dumps(semantic, ensure_ascii=False, indent=2))
        + '</pre>'
    )
    body += '<h2>Rule evidence</h2>'
    for r in rules['rules']:
        body += (
            '<details><summary>'
            + html.escape(r['id'])
            + '</summary><pre>'
            + html.escape(str(r.get('statement', r.get('signature', ''))))
            + '</pre>'
        )
        for e in r.get('evidence', []):
            body += (
                '<p><a href="'
                + html.escape(e.get('source_html', 'rules.json'), quote=True)
                + '">'
                + html.escape(str(e.get('source_file')))
                + ':'
                + str(e.get('line_start'))
                + '</a> — '
                + html.escape(str(e.get('function_cfg_status', e.get('function_cfg', 'UNVERIFIED'))))
                + '</p>'
            )
        body += '</details>'
    (out / 'index.html').write_text(body + '</html>', encoding='utf-8')
    entries = []
    for p in sorted(out.rglob('*')):
        if p.is_file():
            entries.append(
                {'path': p.relative_to(out).as_posix(), 'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size}
            )
    save(out / 'manifest.json', {'schema': 'rgr-files/v1', 'files': entries})
    verified = validate_package(out)
    if verified['status'] != 'pass':
        raise ValueError('generated package validation failed: ' + str(verified))
    archive = out.with_name(out.name + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                z.write(p, p.relative_to(out.parent).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip():
            raise ValueError('ZIP integrity check failed')
    return {
        'status': 'pass',
        'analysis_status': checked['analysis_status'],
        'completion': checked['completion'],
        'out': str(out),
        'archive': str(archive),
        'archive_sha256': sha(archive.read_bytes()),
        'summary': summary,
    }


def validate_package(folder):
    folder = Path(folder).resolve()
    errors = []
    manifest = load(folder / 'manifest.json')
    for entry in manifest['files']:
        p = safe_file(folder, entry['path'])
        if not p or sha(p.read_bytes()) != entry['sha256']:
            errors.append({'path': entry['path'], 'issue': 'missing_or_changed_manifest_file'})
    for rule in load(folder / 'rules.json')['rules']:
        for e in rule.get('evidence', []):
            p = safe_file(folder, e.get('package_source', ''))
            if not p or (e.get('source_sha256') and sha(p.read_bytes()) != e['source_sha256']):
                errors.append({'rule': rule['id'], 'issue': 'source_snapshot_mismatch'})
            if e.get('package_bytecode'):
                p = safe_file(folder, e['package_bytecode'])
                if not p or (e.get('bytecode_sha256') and sha(p.read_bytes()) != e['bytecode_sha256']):
                    errors.append({'rule': rule['id'], 'issue': 'bytecode_mismatch'})
            if e.get('behavior', 'NOT_TESTED') != 'NOT_TESTED':
                errors.append({'rule': rule['id'], 'issue': 'unsupported_game_behavior_certificate'})
    return {
        'status': 'pass' if not errors else 'failed',
        'files_checked': len(manifest['files']),
        'errors': errors,
        'meaning': 'relocation-safe file/citation integrity; not semantic equivalence',
    }


def compare(reference, candidate, out):
    """Answer-aware comparison must run after independent candidate is frozen."""
    reference = Path(reference).resolve()
    candidate = Path(candidate).resolve()

    def metrics(p):
        s = load(p / 'summary.json')
        rules = load(p / 'rules.json')
        rules = rules.get('rules', rules) if isinstance(rules, dict) else rules

        def sources():
            return {e.get('source_sha256') for r in rules for e in r.get('evidence', []) if e.get('source_sha256')}

        values = {
            'systems': s.get('systems', s.get('system_groups')),
            'rules': len(rules),
            'citations': sum(len(r.get('evidence', [])) for r in rules),
            'source_hashes': sorted(sources()),
            'summary': s,
        }
        return values

    a, b = metrics(reference), metrics(candidate)
    result = {
        'mode': 'ANSWER_AWARE_POST_FREEZE',
        'reference_summary_sha256': sha((reference / 'summary.json').read_bytes()),
        'candidate_summary_sha256': sha((candidate / 'summary.json').read_bytes()),
        'reference': a,
        'candidate': b,
        'retained_cited_source_hashes': len(set(a['source_hashes']) & set(b['source_hashes'])),
        'missing_cited_source_hashes': sorted(set(a['source_hashes']) - set(b['source_hashes'])),
        'warning': 'Counts/identity retention are descriptive, not semantic success. A reviewer must compare grouping, actual rules/data and unknowns.',
    }
    out = Path(out)
    if out.exists():
        raise ValueError('comparison output already exists: ' + str(out))
    save(out, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version='roblox-game-reconstruction ' + VERSION)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('prepare')
    q.add_argument('--repo', required=True)
    q.add_argument('--run', required=True)
    full = 'print every issue instead of the first %d per list with counts by issue'
    q = sub.add_parser('audit')
    q.add_argument('--run', required=True)
    q.add_argument('--full', action='store_true', help=full % 20)
    q = sub.add_parser('check-plan')
    q.add_argument('--run', required=True)
    q.add_argument('--full', action='store_true', help=full % 20)
    q = sub.add_parser('integrate')
    q.add_argument('--run', required=True)
    q.add_argument('--input', required=True, help='finished integration-decisions.json')
    q.add_argument('--full', action='store_true', help=full % 20)
    q = sub.add_parser('query', help='compact tab-separated rows from the prepared registry')
    q.add_argument('what', choices=['modules', 'functions', 'remotes', 'requires'])
    q.add_argument('--run', required=True)
    q.add_argument('--system', help='catalog system ID')
    q.add_argument('--module', help='module ID')
    q.add_argument('--path', help='substring of instance path or source file')
    q.add_argument('--name', help='substring of function name')
    q.add_argument('--line', help='SOURCE_FILE:LINE; functions whose body contains the line, innermost first')
    q.add_argument('--method', help='remote method name')
    q.add_argument('--limit', type=int, default=200)
    q = sub.add_parser('normalize-worker', help='expand {"lines":[a,b]} trace citations, write the file, validate')
    q.add_argument('--run', required=True)
    q.add_argument('--kind', choices=['analysis', 'source-review'], required=True)
    q.add_argument('--system', help='required for analysis workers')
    q.add_argument('--input', help='defaults to analysis/SYSTEM/analysis.json for analysis')
    q.add_argument('--full', action='store_true', help=full % 20)
    q = sub.add_parser('package')
    q.add_argument('--run', required=True)
    q.add_argument('--out', required=True)
    q = sub.add_parser('validate')
    q.add_argument('--package', required=True)
    q = sub.add_parser('compare')
    q.add_argument('--reference', required=True)
    q.add_argument('--candidate', required=True)
    q.add_argument('--out', required=True)
    q = sub.add_parser('freeze-review')
    q.add_argument('--run', required=True)
    q.add_argument('--id', required=True)
    q.add_argument('--input', required=True)
    q = sub.add_parser('scaffold')
    q.add_argument('--run', required=True)
    q.add_argument('--kind', choices=['analysis', 'source-review', 'integration'], required=True)
    q.add_argument('--system')
    q.add_argument('--id')
    q.add_argument('--module-id', action='append', default=[])
    q.add_argument('--reviewer', default='')
    q = sub.add_parser('validate-worker')
    q.add_argument('--run', required=True)
    q.add_argument('--kind', choices=['analysis', 'source-review'], required=True)
    q.add_argument('--system', help='required for analysis workers, including with --input')
    q.add_argument('--input')
    q.add_argument('--full', action='store_true', help=full % 20)
    args = p.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.repo, args.run)
        elif args.command == 'audit':
            result = audit_run(args.run)
        elif args.command == 'check-plan':
            from integration import check_plan

            result = check_plan(args.run)
        elif args.command == 'integrate':
            from integration import integrate

            result = integrate(args.run, args.input)
        elif args.command == 'package':
            result = package(args.run, args.out)
        elif args.command == 'validate':
            result = validate_package(args.package)
        elif args.command == 'freeze-review':
            from semantic import freeze_review

            result = freeze_review(args.run, args.id, args.input)
        elif args.command == 'scaffold':
            if args.kind == 'integration':
                from integration import scaffold_integration

                result = scaffold_integration(args.run)
            else:
                from worker_io import scaffold

                result = scaffold(args.run, args.kind, args.system, args.id, args.module_id, args.reviewer)
        elif args.command == 'validate-worker':
            from worker_io import validate_worker, system_scope

            if args.kind == 'analysis' and not args.system:
                raise ValueError('--system is required for analysis, including with --input')
            if args.input:
                source = Path(args.input)
            elif args.kind == 'analysis':
                system_scope(args.run, args.system)
                source = Path(args.run) / 'analysis' / args.system / 'analysis.json'
            else:
                raise ValueError('--input is required for source-review')
            result = validate_worker(args.run, args.kind, load(source), args.system)
        elif args.command == 'query':
            from agent_io import query

            print(query(args.run, args.what, args.system, args.module, args.path, args.name, args.line, args.method, args.limit))
            return 0
        elif args.command == 'normalize-worker':
            from agent_io import normalize_worker

            if args.input:
                source = Path(args.input)
            elif args.kind == 'analysis' and args.system:
                source = Path(args.run) / 'analysis' / args.system / 'analysis.json'
            else:
                raise ValueError('--input is required unless --kind analysis --system is given')
            result = normalize_worker(args.run, args.kind, source, args.system)
        else:
            result = compare(args.reference, args.candidate, args.out)
        if not getattr(args, 'full', True):
            from agent_io import summarize

            full_path = Path(args.run).resolve() / 'audit.json' if args.command == 'audit' else None
            result = summarize(result, full_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') == 'failed' else 0
    except (ValueError, KeyError, OSError, TypeError, AttributeError) as e:
        # Malformed agent-authored JSON is an input error: report it, never a traceback.
        print(
            json.dumps({'status': 'error', 'error': type(e).__name__ + ': ' + str(e)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
