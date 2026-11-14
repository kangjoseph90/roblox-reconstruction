#!/usr/bin/env python3
"""Build pinned local compiler and optional decompiler; no global installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

LUAU = "f46d879e28302a430029304f7f6a162aed66f103"
LUAUDEC = "6d0a7a2ef292171f306bf022ad56362139ac738e"
RBXDOM = "7a6ca296e25ea4dddd44b08449ceac58ae5c2476"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
PATCH = ASSETS / "luaudec-v0.3.3.patch"


def run(args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, stdout=sys.stderr)


def checkout(url, revision, path):
    path.mkdir()
    run(["git", "init", "-q", str(path)])
    run(["git", "-C", str(path), "config", "core.autocrlf", "false"])
    run(["git", "-C", str(path), "remote", "add", "origin", url])
    run(["git", "-C", str(path), "fetch", "--depth", "1", "origin", revision])
    run(["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cmake_path():
    found = shutil.which("cmake")
    if found:
        return found
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if vswhere.is_file():
        result = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"], capture_output=True, text=True, check=True)
        path = Path(result.stdout.strip()) / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
        if path.is_file():
            return str(path)
    raise ValueError("CMake and Visual Studio 2022 C++ tools are required; see INSTALL.md")


def validate_tools(config):
    for name in ("compiler", "decompiler"):
        tool = config.get(name)
        if not tool:
            raise ValueError("tool configuration requires " + name)
        executable = Path(tool["argv"][0])
        if not executable.is_file() or digest(executable) != tool.get("sha256"):
            raise ValueError("tool executable missing or hash changed: " + name)
    return config


def build_tools(destination, decompiler=True, rbx_util=False):
    dest = Path(destination).resolve()
    config_path = dest / "tools.json"
    if config_path.is_file():
        config = validate_tools(json.loads(config_path.read_text(encoding="utf-8")))
        if decompiler and config.get("decompiler", {}).get("patch_sha256") != digest(PATCH):
            raise ValueError("cached decompiler is not v0.3.3; use a new --tools-dir to build the bundled patch")
        return config
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("tools directory must be new/empty or contain a valid tools.json")
    if not shutil.which("git"):
        raise ValueError("Git is required")
    windows = platform.system() == "Windows"
    cmake = cmake_path() if windows else None
    if not windows:
        for command in ("make", "clang++"):
            if not shutil.which(command):
                raise ValueError("missing build dependency: " + command)
    dest.mkdir(parents=True, exist_ok=True)
    compiler = dest / "luau"
    checkout("https://github.com/luau-lang/luau.git", LUAU, compiler)
    source = None
    if decompiler:
        decoder = dest / "luaudec"
        checkout("https://github.com/stolevchristian/LuauDecompiler.git", LUAUDEC, decoder)
        run(["git", "-C", str(decoder), "apply", "--check", str(PATCH)])
        run(["git", "-C", str(decoder), "apply", str(PATCH)])
        manifest = json.loads((ASSETS / "luaudec-source-manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest.items():
            if digest(decoder / relative) != expected:
                raise ValueError("patched source differs from preserved source: " + relative)
        source = decoder / "src"
    files = ["main.cpp", "bytecode/Bytecode.cpp", "disasm/Disasm.cpp", "cfg/CFG.cpp", "lift/Lift.cpp", "emit/Emit.cpp"]
    if windows:
        # Same sources and flags as the successful native Windows build.
        cmake_text = '''cmake_minimum_required(VERSION 3.15)
project(RCRWindowsTools LANGUAGES C CXX)
add_compile_options("$<$<CXX_COMPILER_ID:MSVC>:/utf-8>")
set(LUAU_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(luau)
'''
        targets = ["Luau.Compile.CLI", "Luau.Repl.CLI"]
        if decompiler:
            cmake_text += "add_executable(luaudec " + " ".join("luaudec/src/" + f for f in files) + ")\n"
            cmake_text += "target_compile_features(luaudec PRIVATE cxx_std_17)\n"
            cmake_text += "target_compile_definitions(luaudec PRIVATE _CRT_SECURE_NO_WARNINGS)\n"
            cmake_text += "target_include_directories(luaudec PRIVATE luaudec/src luau/Common/include)\n"
            targets.append("luaudec")
            shutil.copy(ASSETS / "native_tests.cpp", dest / "native_tests.cpp")
            test_files = [f for f in files if f not in {"main.cpp", "lift/Lift.cpp"}]
            cmake_text += "add_executable(rcr-native-tests native_tests.cpp " + " ".join("luaudec/src/" + f for f in test_files) + ")\n"
            cmake_text += "target_compile_features(rcr-native-tests PRIVATE cxx_std_17)\n"
            cmake_text += "target_include_directories(rcr-native-tests PRIVATE luaudec/src luau/Common/include)\n"
            targets.append("rcr-native-tests")
        (dest / "CMakeLists.txt").write_text(cmake_text, encoding="utf-8")
        run([cmake, "-S", str(dest), "-B", str(dest / "build"), "-G", "Visual Studio 17 2022", "-A", "x64"])
        run([cmake, "--build", str(dest / "build"), "--config", "Release", "--target", *targets, "--parallel", "6"])
        binary = dest / "build/luau/Release/luau-compile.exe"
        decoder_binary = dest / "build/Release/luaudec.exe"
        runner = dest / "build/luau/Release/luau.exe"
        if decompiler:
            run([str(dest / "build/Release/rcr-native-tests.exe")])
    else:
        run(["make", "config=release", "-j2", "luau-compile", "luau"], cwd=compiler)
        binary = compiler / "build/release/luau-compile"
        runner = compiler / "build/release/luau"
        decoder_binary = dest / "luaudec/luaudec"
        if decompiler:
            run(["clang++", "-std=c++17", "-O2", "-I" + str(source), "-I" + str(compiler / "Common/include")]
                + [str(source / f) for f in files] + ["-o", str(decoder_binary)])
    config = {"compiler": {"argv": [str(binary), "--binary", "{input}"], "revision": LUAU, "sha256": digest(binary), "timeout": 30}}
    config["runner"] = {"argv": [str(runner)], "revision": LUAU, "sha256": digest(runner), "use": "authored fixtures only"}
    if decompiler:
        config["decompiler"] = {"argv": [str(decoder_binary), "--standard", "--no-header", "{input}"],
            "roblox_argv": [str(decoder_binary), "--roblox", "--no-header", "{input}"],
            "revision": LUAUDEC, "sha256": digest(decoder_binary), "bytecode_versions": [13, 14],
            "type_versions": [3], "output": "stdout", "timeout": 30,
            "patch_sha256": digest(PATCH), "skill_version": "0.3.3", "cfg_audit": True,
            "compatibility": "Roblox v14/type3 live capture regression; vanilla v13/v14 owned fixtures"}
    if rbx_util:
        dom = dest / "rbx-dom"
        checkout("https://github.com/rojo-rbx/rbx-dom.git", RBXDOM, dom)
        shutil.copy(Path(__file__).resolve().parents[1] / "assets/rbx-dom.Cargo.lock", dom / "Cargo.lock")
        run(["cargo", "build", "--release", "--locked", "-p", "rbx_util"], cwd=dom)
        path = dom / ("target/release/rbx-util.exe" if windows else "target/release/rbx-util")
        config["rbx_util"] = {"path": str(path), "revision": RBXDOM}
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, help="new local build directory")
    parser.add_argument("--decompiler", action="store_true", help="build pinned luaudec with the bundled v0.3.3 patch")
    parser.add_argument("--rbx-util", action="store_true", help="also build rbx-dom interoperability utility; requires Rust/Cargo")
    args = parser.parse_args()
    build_tools(args.dest, args.decompiler, args.rbx_util)
    print("Built local tools; adapter config: " + str(Path(args.dest).resolve() / "tools.json"))


if __name__ == "__main__":
    main()
