#!/usr/bin/env python3
"""Roblox client acquisition and independent offline source recovery."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys

from formats import VERSION, parse_artifact
from pipeline import extract, hierarchy, validate_repository
from network import acquire
from backends import capabilities, player_acquire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="roblox-client-reverse " + VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="report static pipeline dependencies")
    inspect = commands.add_parser("inspect", help="identify and inventory one artifact without network")
    inspect.add_argument("input")
    p = commands.add_parser("extract", aliases=["import", "decompile"], help="offline import/recovery; recognizes saveinstance artifacts; no Roblox connection")
    p.add_argument("input")
    p.add_argument("--out", required=True)
    p.add_argument("--tools", help="explicit local JSON compiler/decompiler adapter configuration")
    p.add_argument("--include-server", action="store_true", help="also extract server-side code from authorized exports")
    p.add_argument("--bytecode-format", choices=["luau", "roblox"], help="explicit encoding assertion; roblox requires a Roblox-profile adapter")
    def live_options(p):
        p.add_argument("--tools-dir", help="pinned local build/cache directory")
        p.add_argument("--player-exe", help="installed Player executable if outside the default location")
        p.add_argument("--pid", type=int, help="select one compatible process when several Players are running")
        p.add_argument("--ui-timeout", type=float, default=180)
        p.add_argument("--memory-budget-mib", type=int, default=4096)
        p.add_argument("--structure-cache", help="binary-SHA256 layout cache directory; every candidate is live-revalidated")
    p = commands.add_parser("run", help="launch, await Computer Use, collect, decompile and validate")
    p.add_argument("target")
    p.add_argument("--out", required=True)
    p.add_argument("--tools")
    live_options(p)
    p = commands.add_parser("setup", help="build pinned compiler/decompiler without global installation")
    p.add_argument("--tools-dir", required=True)
    p = commands.add_parser("replay", help="revalidate native buffers and run recovery offline")
    p.add_argument("input")
    p.add_argument("--out", required=True)
    p.add_argument("--tools")
    p.add_argument("--tools-dir")
    p = commands.add_parser("regression", help="replay an explicit capture directory and compare an explicit baseline")
    p.add_argument("input", help="capture directory or run root")
    p.add_argument("--baseline", required=True, help="expected payload states and CFG certificates JSON")
    p.add_argument("--out", required=True)
    p.add_argument("--tools")
    p.add_argument("--tools-dir")
    p = commands.add_parser("acquire", help="alias of live run, or explicit official asset acquisition")
    p.add_argument("target", help="Roblox URL or numeric Place/Asset ID")
    p.add_argument("--kind", choices=["experience", "asset"], default="experience")
    p.add_argument("--backend", choices=["auto", "player", "official"], default="auto", help="auto/player run the live workflow; official is a separate asset-delivery path")
    p.add_argument("--out", required=True)
    p.add_argument("--api-key-env", help="name of an explicitly provided Open Cloud API-key environment variable")
    p.add_argument("--asset-version", type=int)
    p.add_argument("--tools")
    live_options(p)
    p = commands.add_parser("validate", help="verify the repository integrity manifest")
    p.add_argument("repository")
    p = commands.add_parser("check-analysis", help="check citations and module mentions of an analysis document")
    p.add_argument("repository", help="run root or its repository/ directory")
    p.add_argument("document", help="analysis document (Markdown)")
    p.add_argument("--full", action="store_true", help="list every issue and unmentioned module")
    args = parser.parse_args()
    try:
        tools = {}
        if getattr(args, "tools", None) and (args.command in {"extract", "import", "decompile"} or
                                            (args.command == "acquire" and args.backend == "official")):
            tools = json.loads(Path(args.tools).read_text(encoding="utf-8-sig"))
        if args.command == "doctor":
            result = {"python": platform.python_version(), "platform": platform.platform(), "offline_core": "available",
                "lz4": importlib.util.find_spec("lz4") is not None,
                "zstandard": importlib.util.find_spec("zstandard") is not None,
                "runtime_injection": "not_implemented", "decompiler": "built by setup, or automatically by run/replay; --tools selects an existing hash-checked configuration", "acquisition": capabilities()}
        elif args.command == "setup":
            from bootstrap_tools import build_tools
            config = build_tools(args.tools_dir)
            result = {"status": "ready", "tools": str(Path(args.tools_dir).resolve() / "tools.json"),
                      "compiler_revision": config["compiler"]["revision"], "decompiler_revision": config["decompiler"]["revision"]}
        elif args.command in {"run", "replay", "regression"}:
            from workflow import run_live, replay_capture
            if args.command == "run":
                report = run_live(args.target, args.out, args.tools, args.tools_dir, args.player_exe,
                                  args.pid, args.ui_timeout, args.memory_budget_mib, args.structure_cache)
            elif args.command == "regression":
                from regression import run_regression
                report = run_regression(args.input, args.out, args.tools, args.tools_dir, args.baseline)
            else:
                report = replay_capture(args.input, args.out, args.tools, args.tools_dir)
            result = dict(report["summary"], report=str(Path(args.out).resolve() / "report.json"))
        elif args.command == "inspect":
            path = Path(args.input)
            if path.is_symlink() or path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("input is a symlink or exceeds size limit")
            nodes, info = parse_artifact(path.read_bytes(), path.suffix.lower())
            hierarchy(nodes)
            result = dict(info, instances=len(nodes), script_classes=[n["class"] for n in nodes if n["class"] in {"Script", "ModuleScript", "LocalScript"}])
        elif args.command in {"extract", "import", "decompile"}:
            report = extract(args.input, args.out, tools, args.include_server, args.bytecode_format)
            result = dict(report["summary"], report=str(Path(args.out).resolve() / "report.json"))
        elif args.command == "acquire":
            if args.asset_version is not None and args.asset_version <= 0:
                raise ValueError("asset version must be positive")
            if args.backend == "official":
                report = acquire(args.target, args.out, args.kind, args.api_key_env, args.asset_version, tools)
            else:
                if args.kind != "experience" or args.api_key_env or args.asset_version:
                    raise ValueError("asset IDs/keys/versions require --backend official")
                report = player_acquire(args.target, args.out, args.backend, tools=args.tools, tools_dir=args.tools_dir,
                    player_exe=args.player_exe, pid=args.pid, ui_timeout=args.ui_timeout, memory_budget_mib=args.memory_budget_mib,
                    structure_cache=args.structure_cache)
            result = dict(report["summary"], report=str(Path(args.out).resolve() / "report.json"))
        elif args.command == "check-analysis":
            from analysis_check import check_analysis
            result = check_analysis(args.repository, args.document, None if args.full else 40)
        else:
            result = validate_repository(args.repository)
        print(json.dumps(result, indent=2))
        return 2 if result.get("status") in {"partial", "metadata_only", "no_artifact", "no_source", "failed", "collector_unavailable", "invalid_citations"} else 0
    except Exception as e:
        print(json.dumps({"status": "error", "error": type(e).__name__ + ": " + str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
