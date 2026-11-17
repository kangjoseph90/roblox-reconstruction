#!/usr/bin/env python3
"""Capture owned fixture bytecode in an upstream VM; this is not a Player dumper."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pipeline import extract, new_output, save_json, sha
from bootstrap_tools import cmake_path, validate_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-root", help="setup directory containing tools.json and pinned luau sources")
    parser.add_argument("--tools", help="explicit tools.json; requires --luau-source without --tool-root")
    parser.add_argument("--luau-source", help="pinned Luau source directory, such as native/luau")
    parser.add_argument("--out", required=True)
    a = parser.parse_args()
    if not a.tool_root and (not a.tools or not a.luau_source):
        parser.error('provide --tool-root, or both --tools and --luau-source')
    tool_root = Path(a.tool_root).resolve() if a.tool_root else None
    tools_path = Path(a.tools).resolve() if a.tools else tool_root / 'tools.json'
    tools = validate_tools(json.loads(tools_path.read_text(encoding='utf-8-sig')))
    runtime = Path(tools.get('runner', {}).get('argv', [''])[0])
    if not runtime.is_file() or sha(runtime.read_bytes()) != tools.get('runner', {}).get('sha256'):
        raise ValueError('owned-fixture VM runner missing or hash changed')
    out = new_output(a.out)
    skill = Path(__file__).resolve().parents[1]
    luau = Path(a.luau_source).resolve() if a.luau_source else tool_root / 'luau'
    if not (luau / 'CMakeLists.txt').is_file():
        raise ValueError('Luau source directory must contain CMakeLists.txt')
    source = skill / "assets/owned-luau-host.cpp"
    fixture = skill / "assets/fixtures/structured.luau"
    binary = out / 'bin' / ('owned-luau-host.exe' if os.name == 'nt' else 'owned-luau-host')
    cmake = cmake_path()
    quote = lambda p: '"' + str(p).replace('\\', '/').replace('"', '\\"') + '"'
    cmake_source = ('cmake_minimum_required(VERSION 3.15)\nproject(OwnedLuauHost LANGUAGES C CXX)\n'
        'set(LUAU_BUILD_TESTS OFF CACHE BOOL "" FORCE)\nset(LUAU_BUILD_CLI OFF CACHE BOOL "" FORCE)\n'
        f'add_subdirectory({quote(luau)} luau)\nadd_executable(owned-luau-host {quote(source)})\n'
        'target_compile_features(owned-luau-host PRIVATE cxx_std_17)\n'
        'target_link_libraries(owned-luau-host PRIVATE Luau.Compiler Luau.VM)\n'
        f'set_target_properties(owned-luau-host PROPERTIES RUNTIME_OUTPUT_DIRECTORY {quote(out / "bin")} RUNTIME_OUTPUT_DIRECTORY_RELEASE {quote(out / "bin")})\n')
    (out / 'CMakeLists.txt').write_text(cmake_source, encoding='utf-8')
    configure = [cmake, '-S', str(out), '-B', str(out / 'build'), '-DCMAKE_BUILD_TYPE=Release']
    if os.name == 'nt': configure += ['-A', 'x64']  # newest installed Visual Studio generator
    command = [cmake, '--build', str(out / 'build'), '--config', 'Release', '--target', 'owned-luau-host', '--parallel', '4']
    subprocess.run(configure, check=True, timeout=120)
    subprocess.run(command, check=True, timeout=300)
    captured = out / "captured.luac"
    p = subprocess.run([str(binary), str(fixture), str(captured)], check=True, capture_output=True, timeout=20)
    (out / "host.stdout").write_bytes(p.stdout)
    (out / "host.log").write_bytes(p.stderr)
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
               "roblox_player_instrumentation": "not_run", "build_command": command, "configure_command": configure,
               "luau_source": str(luau), "tools": str(tools_path)}
    save_json(out / "experiment.json", summary)
    # The linked third-party libraries are not redistributed as a packaged executable.
    binary.unlink()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
