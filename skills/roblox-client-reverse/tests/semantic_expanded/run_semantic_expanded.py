import argparse
import hashlib
import json
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OPT_LEVELS = (0, 1, 2)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    return digest_bytes(path.read_bytes())


def norm(data):
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def run(cmd, cwd, timeout):
    try:
        cp = subprocess.run([str(x) for x in cmd], cwd=str(cwd), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
        return cp.returncode, cp.stdout, cp.stderr, False
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or b"", (exc.stderr or b"") + b"\nTIMEOUT", True


def property_source(seed):
    rng = random.Random(seed)
    limit = rng.randint(5, 11)
    pivot = rng.randint(1, limit)
    mod = rng.randint(2, 5)
    residue = rng.randrange(mod)
    cutoff = rng.randint(3, limit + 2)
    bias = rng.randint(-9, 9)
    mul = rng.choice((-3, -2, 1, 2, 3))
    return f'''-- deterministic authored property case seed={seed}
local shared = {{sum = {bias}, visits = 0, path = {{}}}}
local alias = shared
local function run(limitArg)
    local localSum = 0
    for i = 1, limitArg do
        if i <= {pivot} then
            if (i % {mod}) == {residue} then
                alias.visits += 1
                alias.sum += i * ({mul})
                alias.path[#alias.path + 1] = "A" .. tostring(i)
            else
                localSum += i + {bias}
            end
        else
            if i == {cutoff} then
                alias.path[#alias.path + 1] = "B" .. tostring(i)
                continue
            elseif i > {cutoff} + 2 then
                break
            elseif (i % 2) == 0 then
                alias.sum -= i
                alias.path[#alias.path + 1] = "C" .. tostring(i)
            else
                localSum -= i * 2
            end
        end
        if localSum > 250 then return "early", localSum end
    end
    return "done", localSum
end
local status, total = run({limit})
local same = shared == alias
print("OBS|state=" .. status .. "," .. tostring(total) .. "," .. tostring(shared.sum) .. "," .. tostring(shared.visits) .. "," .. tostring(same))
print("OBS|path=" .. table.concat(shared.path, ","))
'''


def save_run(directory, label, result):
    code, out, err, timed_out = result
    (directory / f"{label}.stdout").write_bytes(out)
    (directory / f"{label}.stderr").write_bytes(err)
    return {"exit_code": code, "stdout_path": f"{label}.stdout", "stdout_sha256": digest_bytes(out),
            "stderr_path": f"{label}.stderr", "stderr_sha256": digest_bytes(err), "timed_out": timed_out,
            "normalized_stdout": norm(out).decode("utf-8", "replace")}


def generate(cases_dir, count):
    cases_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    for p in sorted((HERE / "fixtures").glob("*.luau")):
        sources.append((p.stem, None, p.read_text(encoding="utf-8-sig")))
    for seed in range(count):
        name = f"property_seed_{seed:03d}"
        content = property_source(seed)
        p = cases_dir / f"{name}.luau"
        p.write_text(content, encoding="utf-8", newline="\n")
        sources.append((name, seed, content))
    return sources


def main():
    ap = argparse.ArgumentParser(description="Authored-only differential Luau round-trip suite")
    ap.add_argument("--tools", required=True, help="tools config containing compiler/runner/decompiler argv")
    ap.add_argument("--out", required=True, help="directory for full run artifacts and report.json")
    ap.add_argument("--property-count", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()
    tools_path = Path(args.tools).resolve()
    config = json.loads(tools_path.read_text(encoding="utf-8"))
    executables = {k: Path(config[k]["argv"][0]).resolve() for k in ("compiler", "runner", "decompiler")}
    missing = [str(p) for p in executables.values() if not p.is_file()]
    if missing:
        ap.error("missing executable(s): " + ", ".join(missing))
    cwd = tools_path.parent
    sys.path.insert(0, str(HERE.parents[1] / "scripts"))
    from cfg_verify import compare, disassemble
    out = Path(args.out).resolve()
    cases_dir = out / "generated_sources"
    artifacts = out / "artifacts"
    cases = generate(cases_dir, args.property_count)
    records = []
    for name, seed, source in cases:
        src_path = (HERE / "fixtures" / f"{name}.luau") if seed is None else cases_dir / f"{name}.luau"
        case_dir = artifacts / name
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "original.luau").write_text(source, encoding="utf-8", newline="\n")
        source_hash = digest_file(case_dir / "original.luau")
        levels = []
        for opt in OPT_LEVELS:
            od = case_dir / f"O{opt}"
            od.mkdir(exist_ok=True)
            bytecode = od / "original.bytecode"
            recovered = od / "recovered.luau"
            recomp = od / "recompiled.bytecode"
            original_native = od / "original.native.json"
            recompiled_native = od / "recompiled.native.json"
            cfg_comparison = od / "cfg-comparison.json"
            compile_cmd = [executables["compiler"], "--binary", f"-O{opt}", src_path]
            comp = run(compile_cmd, cwd, args.timeout)
            bytecode.write_bytes(comp[1])
            decomp_cmd = [executables["decompiler"], "--standard", "--no-header", bytecode]
            decomp = run(decomp_cmd, cwd, args.timeout)
            recovered.write_bytes(decomp[1])
            recomp_cmd = [executables["compiler"], "--binary", f"-O{opt}", recovered]
            recomp_result = run(recomp_cmd, cwd, args.timeout) if decomp[0] == 0 else (125, b"", b"decompile failed", False)
            recomp.write_bytes(recomp_result[1])
            syntax_cmd = [executables["compiler"], "--only-parse", recovered]
            syntax = run(syntax_cmd, cwd, args.timeout) if decomp[0] == 0 else (125, b"", b"decompile failed", False)
            cfg = {"status": "UNVERIFIED", "reason": "recompile failed or decompile failed"}
            if comp[0] == 0 and recomp_result[0] == 0:
                try:
                    native_original = disassemble(executables["decompiler"], bytecode, "standard")
                    native_recompiled = disassemble(executables["decompiler"], recomp, "standard")
                    original_native.write_text(json.dumps(native_original, indent=2), encoding="utf-8")
                    recompiled_native.write_text(json.dumps(native_recompiled, indent=2), encoding="utf-8")
                    cfg = compare(native_original, native_recompiled)
                except Exception as exc:
                    cfg = {"status": "UNVERIFIED", "reason": f"{type(exc).__name__}: {exc}"}
            cfg_comparison.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            original_cmd = [executables["runner"], f"-O{opt}", src_path]
            recovered_cmd = [executables["runner"], f"-O{opt}", recovered]
            original = run(original_cmd, cwd, args.timeout)
            candidate = run(recovered_cmd, cwd, args.timeout) if decomp[0] == 0 else (125, b"", b"decompile failed", False)
            original_record = save_run(od, "original", original)
            recovered_record = save_run(od, "recovered", candidate)
            equal = original[0] == candidate[0] and norm(original[1]) == norm(candidate[1])
            behavior_verified = original[0] == 0 and candidate[0] == 0 and equal
            syntax_valid = syntax[0] == 0
            # Only fixture-caught errors are compared; process traceback paths/lines are intentionally excluded.
            level_record = {
                "optimization": opt,
                "seed": seed,
                "source_sha256": source_hash,
                "compile": {"exit_code": comp[0], "stdout_sha256": digest_bytes(comp[1]), "stderr": comp[2].decode("utf-8", "replace"), "bytecode_sha256": digest_file(bytecode)},
                "decompile": {"exit_code": decomp[0], "stderr": decomp[2].decode("utf-8", "replace"), "recovered_sha256": digest_file(recovered)},
                "recompile": {"exit_code": recomp_result[0], "stderr": recomp_result[2].decode("utf-8", "replace"), "bytecode_sha256": digest_file(recomp)},
                "syntax": {"exit_code": syntax[0], "passed": syntax_valid, "stdout": syntax[1].decode("utf-8", "replace"), "stderr": syntax[2].decode("utf-8", "replace")},
                "cfg": {"status": cfg.get("status", "UNVERIFIED"), "comparison_path": str(cfg_comparison),
                        "original_native_json_path": str(original_native) if original_native.exists() else None,
                        "recompiled_native_json_path": str(recompiled_native) if recompiled_native.exists() else None},
                "verification": {"syntax": "SYNTAX_VALID" if syntax_valid else "SYNTAX_INVALID",
                                "cfg": cfg.get("status", "UNVERIFIED"),
                                "behavior": "BEHAVIOR_VERIFIED" if behavior_verified else "BEHAVIOR_FAILED"},
                "commands": {"compile": list(map(str, compile_cmd)), "decompile": list(map(str, decomp_cmd)), "recompile": list(map(str, recomp_cmd)), "syntax_check": list(map(str, syntax_cmd)), "run_original": list(map(str, original_cmd)), "run_recovered": list(map(str, recovered_cmd))},
                "original": original_record, "recovered": recovered_record,
                "behavior_match": equal,
                "passed": comp[0] == 0 and decomp[0] == 0 and recomp_result[0] == 0 and syntax_valid and behavior_verified,
            }
            levels.append(level_record)
        records.append({"case": name, "kind": "authored" if seed is None else "seeded_property", "seed": seed,
                        "source_path": str(src_path), "source_sha256": source_hash,
                        "minimal_counterexample": str(case_dir / "original.luau"), "optimization_levels": levels})
    failed = [dict(case=x["case"], seed=x["seed"], optimization=l["optimization"],
                   source_path=x["minimal_counterexample"], original=l["original"], recovered=l["recovered"],
                   compile=l["compile"], decompile=l["decompile"], recompile=l["recompile"])
              for x in records for l in x["optimization_levels"] if not l["passed"]]
    report = {"created_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "authored and deterministic generated Luau only; no Roblox connection or real game captures",
              "tools_config": str(tools_path), "executables": {k: {"path": str(p), "sha256": digest_file(p)} for k, p in executables.items()},
              "generator": {"seed_range": [0, args.property_count - 1], "property_count": args.property_count,
                            "dimensions": ["nested predicates", "early return", "break", "continue", "shared alias mutation"]},
              "optimization_levels": list(OPT_LEVELS), "comparison": "normalized stdout bytes plus process exit status; fixtures print OBS records containing state/event/error observations",
              "exclusions": ["Dynamic process traceback paths and source line numbers are not compared. Error fixture catches pcall and serializes stable authored error category/content in stdout."],
              "counts": {"cases": len(records), "authored_cases": sum(x["kind"] == "authored" for x in records),
                         "property_cases": sum(x["kind"] == "seeded_property" for x in records), "runs": len(records) * len(OPT_LEVELS),
                         "passed": sum(l["passed"] for x in records for l in x["optimization_levels"]), "failures": len(failed),
                         "syntax_valid": sum(l["verification"]["syntax"] == "SYNTAX_VALID" for x in records for l in x["optimization_levels"]),
                         "behavior_verified": sum(l["verification"]["behavior"] == "BEHAVIOR_VERIFIED" for x in records for l in x["optimization_levels"]),
                         "cfg_validated": sum(l["verification"]["cfg"] == "CFG_VALIDATED" for x in records for l in x["optimization_levels"]),
                         "cfg_mismatch": sum(l["verification"]["cfg"] == "CFG_MISMATCH" for x in records for l in x["optimization_levels"]),
                         "cfg_unverified": sum(l["verification"]["cfg"] not in {"CFG_VALIDATED", "CFG_MISMATCH"} for x in records for l in x["optimization_levels"])},
              "cases": records, "counterexamples": failed}
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(out / "report.json"), **report["counts"]}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
