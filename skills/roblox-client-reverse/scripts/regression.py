"""Recover saved client buffers and compare an explicit expected baseline."""
import json
from pathlib import Path
from pipeline import finish, not_completed, save_json
from recovery import recover_capture


def certificate_regression(baseline, candidate, validated_protos):
    """Counts cannot hide one formerly certified function being replaced by another."""
    return ((baseline['status']=='CFG_VALIDATED' and candidate.get('status')!='CFG_VALIDATED')
            or candidate.get('matched_functions',0)<baseline.get('matched_functions',0)
            or ('total_functions' in baseline and candidate.get('total_functions')!=baseline['total_functions'])
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


def run_regression(input_path, output, tools=None, tools_dir=None, baseline=None):
    fixture = Path(input_path).resolve()
    if not fixture.is_dir():
        raise ValueError("capture directory does not exist: " + str(fixture))
    if baseline is None:
        raise ValueError("an explicit baseline JSON path is required")
    baseline_path = Path(baseline).resolve()
    expected = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(expected.get("payloads"), dict):
        raise ValueError("baseline must contain a payloads object")
    report = recover_capture(fixture, output, tools, tools_dir)
    out = Path(output)
    (out / "evidence").mkdir(exist_ok=True)
    if report["summary"].get("status") == "failed" and "failure_stage" in report["summary"]:
        # The replay already wrote a terminal report; record that no comparison ran.
        checks = {"status": "failed", "reason": "replay_failed", "failure_stage": report["summary"]["failure_stage"],
                  "input": str(fixture), "baseline": str(baseline_path), "source_equivalence": "not_tested"}
        save_json(out / "evidence/regression.json", checks)
        report["operation"], report["regression"] = "regression", checks
        finish(out, report)
        return report
    repository = json.loads((out / "repository/report.json").read_text(encoding="utf-8"))
    actual = {}
    for script in repository["scripts"]:
        if "bytecode" in script:
            actual[script["bytecode"]["sha256"]] = {"status": script["recovery"], "source_sha256": script.get("source_sha256")}
    mismatches = [{"bytecode_sha256": digest, "expected": baseline, "actual": actual.get(digest)}
                  for digest, baseline in expected["payloads"].items() if actual.get(digest) != baseline]
    unexpected = sorted(set(actual) - set(expected["payloads"]))
    cfg_regressions=check_certificates(expected,repository,out)
    input_errors = [e for e in report["recovery_errors"] if e.get("kind") != "capture_unavailable"]
    # A requested check that failed (compiler or CFG audit) leaves the comparison incomplete.
    incomplete = [s["instance_path"] for s in repository["scripts"] if not_completed(s)]
    recovery_failed = report["summary"].get("status") == "failed"
    passed = (not mismatches and not unexpected and not input_errors and not cfg_regressions and not recovery_failed
              and not incomplete)
    checks = {"status": "pass" if passed else "failed", "baseline_payloads": len(expected["payloads"]),
              "actual_payloads": len(actual), "mismatches": mismatches, "unexpected_payloads": unexpected,
              "input": str(fixture), "baseline": str(baseline_path), "source_equivalence": "not_tested",
              "cfg_regressions":cfg_regressions, "input_errors": input_errors, "recovery_failed": recovery_failed, "incomplete_checks": incomplete,
              "meaning": "syntax/state/hash regression and retained module/function certificate coverage; behavior is not tested"}
    save_json(out / "evidence/regression.json", checks)
    report["operation"], report["regression"] = "regression", checks
    report["summary"]["recovery_status"] = report["summary"]["status"]
    report["summary"]["status"] = checks["status"]
    finish(out, report, validated="repository")
    return report
