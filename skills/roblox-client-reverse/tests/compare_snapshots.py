"""Compare observed capture snapshots; counts/paths do not prove completeness."""
import argparse
import collections
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def snapshot(root):
    root = Path(root)
    report = load(root / 'report.json') if (root / 'report.json').is_file() else {}
    path = root / 'report.json' if report.get('schema') == 'rcr-capture/v1' else root / 'evidence/capture.json'
    if not path.is_file() or not (root / 'client-dump.json').is_file() and not (root / 'capture/client-dump.json').is_file():
        return dict(capture_performed=False, root=str(root), run_summary=report.get('summary'), errors=report.get('errors'))
    capture = load(path)
    groups = collections.defaultdict(list)
    for row in capture['scripts']:
        groups[(tuple(row['path']), row['class'])].append(dict(status=row['status'], bytecode_sha256=row.get('bytecode_sha256'),
                                                             raw_sha256=row.get('raw_sha256')))
    return dict(capture_performed=True, root=str(root), place_id=capture['place_id'],
                counts=capture.get('status_counts', {}), instances=len(capture['scripts']),
                bytecodes={r['bytecode_sha256'] for r in capture['scripts'] if r.get('bytecode_sha256')},
                raw_buffers={r['raw_sha256'] for r in capture['scripts'] if r.get('raw_sha256')}, groups=groups,
                coverage=capture.get('coverage'), discovery_coverage=capture.get('structure_discovery_coverage'),
                collection_result=capture.get('collection_result'), revalidation=capture.get('revalidation_reason_counts', {}),
                run_summary=report.get('summary'),
                image=load(root / ('client-dump.json' if (root / 'client-dump.json').is_file() else 'capture/client-dump.json')).get('capture', {}).get('image'))


def compare(reference, candidate):
    left, right = snapshot(reference), snapshot(candidate)
    def public(value):
        return {k: len(v) if k in ('bytecodes', 'raw_buffers') else v
                for k,v in value.items() if k != 'groups'}
    result = dict(reference=public(left), candidate=public(right),
                  scope='Observed snapshots only. Game coverage/behavior equivalence and cause of absence are not established.')
    if not left['capture_performed'] or not right['capture_performed']:
        return dict(result, comparison_status='NOT_PERFORMED', reason='At least one run never captured scripts; no zero-script success or numeric loss is inferred.')
    if left['place_id'] != right['place_id']:
        return dict(result, comparison_status='INCOMPARABLE_TARGET', reason='Different requested Place IDs')
    def row(key, before, after):
        return dict(path=list(key[0]), script_class=key[1], reference_instances=len(before), candidate_instances=len(after),
                    duplicate_path_ambiguity=len(before)>1 or len(after)>1, reference=before, candidate=after,
                    cause='unknown; not observed/equal in this non-atomic snapshot')
    added, missing, changed = [], [], []
    for key in sorted(set(left['groups']) | set(right['groups'])):
        a, b = left['groups'].get(key, []), right['groups'].get(key, [])
        if not a: added.append(row(key, a, b))
        elif not b: missing.append(row(key, a, b))
        elif (sorted((r['status'], r.get('bytecode_sha256') or '', r.get('raw_sha256') or '') for r in a)
              != sorted((r['status'], r.get('bytecode_sha256') or '', r.get('raw_sha256') or '') for r in b)):
            changed.append(row(key, a, b))
    return dict(result, comparison_status='OBSERVED_SNAPSHOT_DELTA',
                instance_count_delta=right['instances']-left['instances'],
                unique_bytecode_delta=len(right['bytecodes'])-len(left['bytecodes']),
                bytecode_hashes_not_observed=sorted(left['bytecodes']-right['bytecodes']),
                bytecode_hashes_newly_observed=sorted(right['bytecodes']-left['bytecodes']),
                path_groups_not_observed=missing, path_groups_newly_observed=added, path_groups_changed=changed,
                path_group_counts=dict(not_observed=len(missing), newly_observed=len(added), changed=len(changed)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True); parser.add_argument('--candidate', required=True); parser.add_argument('--out', required=True)
    args=parser.parse_args()
    out=Path(args.out).resolve()
    for source in (args.reference, args.candidate):
        if Path(source).resolve() in out.parents:
            parser.error('--out must be outside the compared captures')
    result=compare(args.reference,args.candidate)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x',encoding='utf-8') as stream:  # never replace an existing report
        stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('comparison_status','instance_count_delta','unique_bytecode_delta','path_group_counts')}))
    return 0 if result['comparison_status']=='OBSERVED_SNAPSHOT_DELTA' else 2


if __name__=='__main__':
    raise SystemExit(main())
