#!/usr/bin/env python3
"""Reproduce owned fixture pipeline checks; never executes supplied game code."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import extract, new_output, save_json, sha, tool_run, validate_repository
from make_fixtures import fixture



def check(condition, message="check failed"):
    """Unlike assert, kept under python -O."""
    if not condition:
        raise SystemExit("experiment failed: " + str(message))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--runtime", required=True, help="built upstream Luau interpreter; owned fixtures only")
    parser.add_argument("--rbx-util", help="independent binary fixture serializer")
    parser.add_argument("--out", required=True)
    a = parser.parse_args()
    tools = json.loads(Path(a.tools).read_text())
    out = new_output(a.out)
    fixtures = Path(__file__).resolve().parents[1] / "assets/fixtures"
    matrix = []
    for name, opt, debug in [("simple", 1, 1), ("structured", 1, 2), ("structured", 0, 0), ("structured", 2, 0)]:
        case = name + "-O" + str(opt)
        directory = out / case
        directory.mkdir()
        source = fixtures / (name + ".luau")
        compiler = dict(tools["compiler"], argv=[tools["compiler"]["argv"][0], "--binary", "-O" + str(opt), "-g" + str(debug), "{input}"])
        result, data = tool_run(compiler, source.resolve())
        check(result["status"] == "ok", result)
        bytecode = directory / "input.luac"
        bytecode.write_bytes(data)
        report = extract(bytecode, directory / "repository", tools=tools, bytecode_format="luau")
        script = report["scripts"][0]
        check(script["recovery"] == "decompiled_syntax_valid", script)
        recovered = directory / "repository" / script["source"]
        # These inputs are exactly the two owned fixture files bundled with the skill.
        original_stdout = subprocess.run([a.runtime, str(source)], check=True, capture_output=True, timeout=20).stdout
        recovered_stdout = subprocess.run([a.runtime, str(recovered)], check=True, capture_output=True, timeout=20).stdout
        (directory / "original.stdout").write_bytes(original_stdout)
        (directory / "recovered.stdout").write_bytes(recovered_stdout)
        match = original_stdout == recovered_stdout
        check(match, "behavior mismatch: " + case)
        check(validate_repository(directory / "repository")["status"] == "pass")
        matrix.append({"case": case, "version": data[0], "type_version": data[1], "bytecode_sha256": sha(data),
                       "recompiled": True, "stdout_match": match, "stdout": original_stdout.decode()})
    for name in ["Workshop", "Signals"]:
        source = fixture(out, name)
        r = extract(source, out / (name + "-xml"), tools=tools)
        check(r["summary"]["source_files"] == 7 and r["summary"]["syntax_validated"] == 7)
        if a.rbx_util:
            binary = source.with_suffix(".rbxl")
            subprocess.run([a.rbx_util, "convert", str(source), str(binary)], check=True, timeout=30)
            r = extract(binary, out / (name + "-binary"), tools=tools)
            check(r["summary"]["source_files"] == 8 and r["summary"]["syntax_validated"] == 8)
            check(validate_repository(out / (name + "-binary"))["status"] == "pass")
    summary = {"schema": "rcr-experiment/v1", "controlled_inputs_only": True, "decompiler_cases": matrix,
               "format_cases": 4 if a.rbx_util else 2, "roblox_player_instrumentation": "not_run"}
    save_json(out / "experiment.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
