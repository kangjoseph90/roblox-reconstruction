"""Replay actual saved client buffers, including known partial/failed results."""
import json
from pathlib import Path
from pipeline import finish, save_json
from workflow import replay_capture


def certificate_regression(baseline, candidate, validated_protos):
    """Counts cannot hide one formerly certified function being replaced by another."""
    return ((baseline['status']=='CFG_VALIDATED' and candidate.get('status')!='CFG_VALIDATED')
            or candidate.get('matched_functions',0)<baseline['matched_functions']
            or candidate.get('total_functions')!=baseline['total_functions']
            or candidate.get('status')=='CFG_MISMATCH'
            or not set(baseline.get('validated_original_protos',[])).issubset(validated_protos))


def check_certificates(expected, repository, output):
    actual={s['bytecode']['sha256']:s.get('cfg_audit',{}) for s in repository['scripts']if 'bytecode'in s}
    regressions=[]
    for digest,baseline in expected.get('cfg_certificates',{}).items():
        candidate=actual.get(digest,{})
        validated=set()
        if baseline.get('validated_original_protos') and candidate.get('evidence'):
            path=Path(output)/'repository'/candidate['evidence']/('comparison-O%d.json'%candidate['best_optimization'])
            comparison=json.loads(path.read_text(encoding='utf-8'))
            validated={f['proto']for f in comparison['functions']if f['status']=='CFG_VALIDATED'}
        if certificate_regression(baseline,candidate,validated):
            regressions.append({'bytecode_sha256':digest,'expected':baseline,'actual':candidate,
                                'lost_original_protos':sorted(set(baseline.get('validated_original_protos',[]))-validated)})
    return regressions


def run_regression(output, tools=None, tools_dir=None, dataset="sell-lemons"):
    if dataset not in {"sell-lemons", "place-189707"}:
        raise ValueError("unknown regression dataset")
    fixture = Path(__file__).resolve().parents[1] / "assets/regression" / dataset
    expected = json.loads((fixture / "expected.json").read_text(encoding="utf-8"))
    report = replay_capture(fixture, output, tools, tools_dir)
    out = Path(output)
    repository = json.loads((out / "repository/report.json").read_text(encoding="utf-8"))
    actual = {}
    for script in repository["scripts"]:
        if "bytecode" in script:
            actual[script["bytecode"]["sha256"]] = {"status": script["recovery"], "source_sha256": script.get("source_sha256")}
    mismatches = [{"bytecode_sha256": digest, "expected": baseline, "actual": actual.get(digest)}
                  for digest, baseline in expected["payloads"].items() if actual.get(digest) != baseline]
    unexpected = sorted(set(actual) - set(expected["payloads"]))
    cfg_regressions=check_certificates(expected,repository,out)
    passed = not mismatches and not unexpected and not report["replay_errors"] and not cfg_regressions
    checks = {"status": "pass" if passed else "failed", "baseline_payloads": len(expected["payloads"]),
              "actual_payloads": len(actual), "mismatches": mismatches, "unexpected_payloads": unexpected,
              "dataset": dataset, "expected_partial_result": dataset == "sell-lemons", "source_equivalence": "not_tested",
              "cfg_regressions":cfg_regressions,
              "meaning": "syntax/state/hash regression and retained module/function certificate coverage; behavior is not tested"}
    save_json(out / "evidence/regression.json", checks)
    report["operation"], report["regression"] = "regression", checks
    report["summary"]["recovery_status"] = report["summary"]["status"]
    report["summary"]["status"] = checks["status"]
    finish(out, report)
    return report
