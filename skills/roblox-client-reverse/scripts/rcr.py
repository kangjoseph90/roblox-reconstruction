#!/usr/bin/env python3
"""Roblox Player control, raw capture, offline recovery and analysis checks."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys

from formats import VERSION, parse_artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="roblox-client-reverse " + VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="report platform and optional dependencies")
    p = commands.add_parser("setup", help="build pinned recovery tools")
    p.add_argument("--tools-dir", required=True)
    p = commands.add_parser("places", help="refresh official Place metadata without resetting capture state")
    p.add_argument("target"); p.add_argument("--out", required=True)
    commands.add_parser("players", help="list running Windows Players and their windows")
    p = commands.add_parser("open", help="request Player launch; game entry remains unverified")
    p.add_argument("target"); p.add_argument("--game", required=True)
    p.add_argument("--player-exe"); p.add_argument("--timeout", type=float, default=30)
    p = commands.add_parser("screenshot", help="save an HWND-targeted WGC image")
    p.add_argument("--pid", type=int, required=True); p.add_argument("--hwnd", type=int)
    p.add_argument("--out", required=True); p.add_argument("--timeout", type=float, default=10)
    p = commands.add_parser("capture", help="save raw client buffers for one Place; no decompilation")
    p.add_argument("--pid", type=int, required=True); p.add_argument("--place", type=int, required=True)
    p.add_argument("--game", required=True)
    p.add_argument("--memory-budget-mib", type=int, default=4096); p.add_argument("--structure-cache")
    p.add_argument("--retry-failed", action="store_true", help="retry a failed capture only if it contains no raw/bytecode buffers")
    p = commands.add_parser("close", help="request selected Player normal shutdown")
    p.add_argument("--pid", type=int, required=True); p.add_argument("--timeout", type=float, default=10)
    p.add_argument("--force", action="store_true", help="allow selected-process termination if normal close fails")
    p = commands.add_parser("recover", help="recover/update one repository from a game, capture or local input")
    p.add_argument("input"); p.add_argument("--out", help="required for supplied files/captures; omit for GAME")
    p.add_argument("--tools"); p.add_argument("--tools-dir")
    p.add_argument("--include-server", action="store_true", help="include server-scoped scripts from supplied offline files")
    p.add_argument("--bytecode-format", choices=["luau", "roblox", "unknown"])
    p = commands.add_parser("fetch", help="download an officially accessible artifact without recovery")
    p.add_argument("target"); p.add_argument("--out", required=True)
    p.add_argument("--kind", choices=["experience", "asset"], default="experience")
    p.add_argument("--api-key-env"); p.add_argument("--asset-version", type=int)
    p = commands.add_parser("regression", help="recover a capture and compare an explicit baseline")
    p.add_argument("input"); p.add_argument("--baseline", required=True); p.add_argument("--out", required=True)
    p.add_argument("--tools"); p.add_argument("--tools-dir")
    p = commands.add_parser("inspect", help="inspect a supplied local file")
    p.add_argument("input")
    p = commands.add_parser("validate", help="check game/capture/repository file integrity")
    p.add_argument("path")
    p = commands.add_parser("repo-map", help="write the recovered module map")
    p.add_argument("repository"); p.add_argument("--out", required=True)
    p = commands.add_parser("check-analysis", help="check citations and candidate module coverage")
    p.add_argument("repository"); p.add_argument("document"); p.add_argument("--full", action="store_true")
    p.add_argument("--map", metavar="FILE")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            from compatibility import runtime
            try:
                environment = runtime()
            except Exception as exc:
                environment = {"compatible": False, "reason": str(exc)}
            result = {"python": platform.python_version(), "platform": platform.platform(), "environment": environment,
                      "computer_use_required": False, "windows_capture": importlib.util.find_spec("windows_capture") is not None,
                      "lz4": importlib.util.find_spec("lz4") is not None, "zstandard": importlib.util.find_spec("zstandard") is not None}
        elif args.command == "setup":
            from bootstrap_tools import build_tools
            config = build_tools(args.tools_dir)
            result = {"status": "ready", "tools": str(Path(args.tools_dir).resolve() / "tools.json"),
                      "compiler_revision": config["compiler"]["revision"], "decompiler_revision": config["decompiler"]["revision"]}
        elif args.command == "places":
            from place_catalog import refresh_places
            result = refresh_places(args.target, args.out)
        elif args.command in {"players", "open", "screenshot", "close"}:
            from player_control import list_players, open_player, screenshot, close_player
            if args.command == "players": result = list_players()
            elif args.command == "open": result = open_player(args.target, args.game, args.player_exe, args.timeout)
            elif args.command == "screenshot": result = screenshot(args.pid, args.out, args.timeout, args.hwnd)
            else: result = close_player(args.pid, args.force, args.timeout)
        elif args.command == "capture":
            from capture_store import capture_player
            result = capture_player(args.pid, args.place, args.game, args.memory_budget_mib, args.structure_cache, args.retry_failed)
        elif args.command == "recover":
            from recovery import recover_input
            report = recover_input(args.input, args.out, args.tools, args.tools_dir, args.include_server, args.bytecode_format)
            report_path = Path(args.out or args.input) / "repository/report.json"
            if not report_path.is_file() and report["summary"].get("failure_stage"):
                report_path = Path(args.out or args.input) / "report.json"
            result = dict(report["summary"], report=str(report_path.resolve()), changes=report.get("changes", {}))
        elif args.command == "fetch":
            from network import fetch
            report = fetch(args.target, args.out, args.kind, args.api_key_env, args.asset_version)
            result = dict(report["summary"], report=str(Path(args.out).resolve() / "report.json"))
        elif args.command == "regression":
            from regression import run_regression
            report = run_regression(args.input, args.out, args.tools, args.tools_dir, args.baseline)
            result = dict(report["summary"], report=str(Path(args.out).resolve() / "report.json"))
        elif args.command == "inspect":
            from pipeline import hierarchy
            path = Path(args.input)
            if path.is_symlink() or path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("input is a symlink or exceeds size limit")
            nodes, info = parse_artifact(path.read_bytes(), path.suffix.lower())
            hierarchy(nodes)
            result = dict(info, instances=len(nodes), script_classes=[n["class"] for n in nodes if n["class"] in {"Script", "ModuleScript", "LocalScript"}])
        elif args.command == "validate":
            from recovery import validate_path
            result = validate_path(args.path)
        else:
            from analysis_check import check_analysis, repository_map
            if args.command == "check-analysis":
                result = check_analysis(args.repository, args.document, None if args.full else 40, args.map)
            else:
                text = repository_map(args.repository)
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(text, encoding="utf-8")
                result = {"status": "written", "map": str(Path(args.out).resolve())}
        print(json.dumps(result, indent=2))
        return 2 if result.get("status") in {"partial", "metadata_only", "no_artifact", "no_source", "failed", "invalid_citations"} else 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__ + ": " + str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
