#!/usr/bin/env python3
"""Capture owned fixture bytecode in an upstream VM; this is not a Player dumper."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import extract, new_output, save_json, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-root", required=True, help="local directory made by bootstrap_tools.py --decompiler")
    parser.add_argument("--out", required=True)
    a = parser.parse_args()
    tool_root = Path(a.tool_root).resolve()
    out = new_output(a.out)
    skill = Path(__file__).resolve().parents[1]
    luau = tool_root / "luau"
    source = skill / "assets/owned-luau-host.cpp"
    fixture = skill / "assets/fixtures/structured.luau"
    binary = out / "owned-luau-host"
    command = ["clang++", "-std=c++17", "-O2"]
    command += ["-I" + str(luau / p / "include") for p in ["Compiler", "Bytecode", "Ast", "VM", "Common"]]
    command += [str(source), "-L" + str(luau / "build/release")]
    command += ["-lluaucompiler", "-lluaubytecode", "-lluauast", "-lluauvm", "-lluaucommon", "-lpthread", "-o", str(binary)]
    subprocess.run(command, check=True, timeout=120)
    captured = out / "captured.luac"
    runtime = luau / "build/release/luau"
    p = subprocess.run([str(binary), str(fixture), str(captured)], check=True, capture_output=True, timeout=20)
    (out / "host.stdout").write_bytes(p.stdout)
    (out / "host.log").write_bytes(p.stderr)
    tools = json.loads((tool_root / "tools.json").read_text())
    report = extract(captured, out / "repository", tools=tools, bytecode_format="luau")
    s = report["scripts"][0]
    assert s["recovery"] == "decompiled_syntax_valid", "profile must include observed v13/type-v3; " + s["recovery"]
    recovered = out / "repository" / s["source"]
    original = subprocess.run([str(runtime), str(fixture)], check=True, capture_output=True, timeout=20).stdout
    result = subprocess.run([str(runtime), str(recovered)], check=True, capture_output=True, timeout=20).stdout
    (out / "reference.stdout").write_bytes(original)
    (out / "recovered.stdout").write_bytes(result)
    assert p.stdout == original == result, "owned-host capture behavioral mismatch"
    data = captured.read_bytes()
    summary = {"schema": "rcr-host-experiment/v1", "controlled_input": str(fixture),
               "capture_boundary": "immediately-before-luau_load", "bytes": len(data), "version": data[0], "type_version": data[1],
               "sha256": sha(data), "syntax_pass": True, "stdout_match": True,
               "roblox_player_instrumentation": "not_run", "build_command": command}
    save_json(out / "experiment.json", summary)
    # The linked third-party libraries are not redistributed as a packaged executable.
    binary.unlink()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
