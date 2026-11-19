"""Authored-only adversarial matrix for cfg_normalize, with outcome oracle.

Usage: python run_matrix.py --tools path/to/final-tools.json --out results-dir
The output directory retains all authored source, compiled bytecode, native
JSON disassemblies, outcomes, and per-pair proof diagnostics.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from cfg_normalize import ModuleProof
from cfg_verify import disassemble


def cases():
    out = []
    def add(cat, left, right, eq):
        out.append({"id": f"{cat}-{sum(x['category']==cat for x in out):03d}",
                    "category": cat, "left": left, "right": right,
                    "expected_equal": eq})

    # Each family has nine distinct equivalent and nine inequivalent pairs.
    for n in range(9):
        add("copy_register", f"local a={n}; local b=a; return b", f"local x={n}; return x", True)
        add("copy_register", f"local a={n}; return a", f"local a={n}; return a+1", False)
        add("boolean", f"local x=({n}%2==0); local b=not not x; if b then return 1 else return 2 end",
            f"local x=({n}%2==0); if x then return 1 else return 2 end", True)
        add("boolean", f"local x=({n}%2==0); if x then return 1 else return 2 end",
            f"local x=({n}%2==0); if not x then return 1 else return 2 end", False)
        add("loop", f"local s=0; for i=1,{n+2} do s=s+i end; return s",
            f"local s=0; for j=1,{n+2} do local k=j; s=s+k end; return s", True)
        add("loop", f"local s=0; for i=1,{n+2} do s=s+i end; return s",
            f"local s=0; for i=1,{n+3} do s=s+i end; return s", False)
        # Coupled external calls are intentionally modeled as ordered effects.
        add("effect_order", f"local a=f({n}); local b=g({n}); return a,b",
            f"local x=f({n}); local y=g({n}); return x,y", True)
        add("effect_order", f"local a=f({n}); local b=g({n}); return a,b",
            f"local b=g({n}); local a=f({n}); return a,b", False)
        add("metamethod", f"local x={{}}; setmetatable(x,{{__add=function(a,b) return {n+4} end}}); return x+1",
            f"local x={{}}; setmetatable(x,{{__add=function(a,b) return {n+4} end}}); local y=x; return y+1", True)
        add("metamethod", f"local x={{}}; setmetatable(x,{{__add=function(a,b) return {n+4} end}}); return x+1",
            f"local x={{}}; setmetatable(x,{{__add=function(a,b) return {n+5} end}}); return x+1", False)
        add("table_alias", f"local t={{x={n}}}; local u=t; return u.x",
            f"local t={{x={n}}}; return t.x", True)
        add("table_alias", f"local t={{x={n}}}; local u=t; u.x=u.x+1; return t.x",
            f"local t={{x={n}}}; local u={{x=t.x}}; u.x=u.x+1; return t.x", False)
        add("vararg_multiret", f"local a,b=...; return a,b", f"local x,y=...; return x,y", True)
        add("vararg_multiret", f"local a,b=...; return a,b", f"local a,b=...; return b,a", False)
        add("closure_cache", f"local x={n}; local f=function(y) return x+y end; return f(10)",
            f"local z={n}; local g=function(q) return z+q end; return g(10)", True)
        add("closure_cache", f"local x={n}; return function(y) return x+y end",
            f"local x={n}; return function(y) return x+y+1 end", False)
        # Error/coroutine behavior is observed via pcall and coroutine.resume.
        add("errors_coroutines", f"local ok,v=pcall(function() return {n}+1 end); return ok,v",
            f"local function f() return {n+1} end; local ok,v=pcall(f); return ok,v", True)
        add("errors_coroutines", f"local co=coroutine.create(function() return {n} end); local ok,v=coroutine.resume(co); return ok,v",
            f"local co=coroutine.create(function() return {n+1} end); local ok,v=coroutine.resume(co); return ok,v", False)

    # Unknown-type relational/metamethod comparison and runtime errors.
    for n in range(9):
        add("metamethod_compare", f"local x,y={{}},{{}}; local m={{__lt=function() return true end}}; setmetatable(x,m); setmetatable(y,m); if x<y then return 1 else return 0 end",
            f"local x,y={{}},{{}}; local m={{__lt=function() return true end}}; setmetatable(x,m); setmetatable(y,m); if x<y then local z=1; return z else return 0 end", True)
        add("metamethod_compare", f"local x,y={{}},{{}}; local m={{__lt=function() return true end}}; setmetatable(x,m); setmetatable(y,m); if x<y then return 1 else return 0 end",
            f"local x,y={{}},{{}}; local m={{__lt=function() return false end}}; setmetatable(x,m); setmetatable(y,m); if x<y then return 1 else return 0 end", False)
        add("errors_coroutines", f"local ok=pcall(function() return nil+{n} end); return ok",
            f"local ok=pcall(function() return nil+{n} end); return not ok", False)
        add("errors_coroutines", f"local co=coroutine.create(function() coroutine.yield({n}); return 8 end); local a,x=coroutine.resume(co); local b,y=coroutine.resume(co); return a,x,b,y",
            f"local co=coroutine.create(function() coroutine.yield({n}); return 8 end); local a,x=coroutine.resume(co); local b,y=coroutine.resume(co); return a,x,b,y", True)
    return out


def run(argv, source):
    run_source = source.with_name(source.stem + ".run.luau")
    run_source.write_text("local __trace = {}\nf = function(...) table.insert(__trace, 'f'); return 'F' end\n" +
                          "g = function(...) table.insert(__trace, 'g'); return 'G' end\n" +
                          "local function __matrix_run(...)\n" + source.read_text(encoding="utf-8") +
                          "\nend\nlocal __result = table.pack(__matrix_run(7, 11))\n" +
                          "-- Authored returned closures in this matrix accept one numeric argument.\n" +
                          "for i=1,__result.n do if type(__result[i])=='function' then __result[i]=__result[i](10) end end\n" +
                          "print(table.concat(__trace, ','))\nprint(table.unpack(__result, 1, __result.n))\n", encoding="utf-8")
    command = [str(run_source) if arg == "{input}" else arg for arg in argv]
    if not any(arg == str(run_source) for arg in command): command.append(str(run_source))
    p = subprocess.run(command, capture_output=True, timeout=30)
    clean = lambda value: re.sub(r"(?:\.\\|\./)?[^\r\n]*?\.luau", "<source>.luau", value)
    return {"returncode": p.returncode,
            "stdout": clean(p.stdout.decode("utf-8", "replace")),
            "stderr": clean(p.stderr.decode("utf-8", "replace"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        ap.error("--out must be a new or empty directory")
    tools = json.loads(args.tools.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, case in enumerate(cases()):
        folder = args.out / case["id"]
        folder.mkdir(exist_ok=True)
        modules, executions = [], []
        for side in ("left", "right"):
            source = folder / f"{side}.luau"
            source.write_text(case[side] + "\n", encoding="utf-8")
            executions.append(run(tools["runner"]["argv"], source))
            opt = index % 3
            comp = [str(source) if arg == "{input}" else arg for arg in tools["compiler"]["argv"]]
            comp.insert(1, f"-O{opt}")
            cp = subprocess.run(comp, capture_output=True, timeout=30)
            if cp.returncode:
                raise RuntimeError(f"compiler failed for {case['id']} {side}: {cp.stderr!r}")
            bytecode = source.with_suffix(".bytecode")
            bytecode.write_bytes(cp.stdout)
            module = disassemble(tools["decompiler"]["argv"][0], bytecode, "standard")
            native = folder / f"{side}.native.json"
            native.write_text(json.dumps(module, indent=2, ensure_ascii=False), encoding="utf-8")
            modules.append(module)
        observed_equal = executions[0] == executions[1]
        proof = ModuleProof(*modules).run(modules[0]["mainProto"], modules[1]["mainProto"])
        row = {**case, "optimization": index % 3, "observed_equal": observed_equal,
               "executions": executions, "proof": proof,
               "source_sha256": {s: hashlib.sha256((folder / f"{s}.luau").read_bytes()).hexdigest() for s in ("left", "right")}}
        (folder / "result.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        rows.append(row)
        if proof["status"] == "CFG_VALIDATED" and not observed_equal:
            print("FALSE_POSITIVE", case["id"], json.dumps(row, ensure_ascii=False), flush=True)
    statuses = sorted({x["proof"]["status"] for x in rows})
    summary = {"count": len(rows), "tools": tools,
               "counts": {status: sum(x["proof"]["status"] == status for x in rows) for status in statuses},
               "observed_mismatches_from_expected": [x["id"] for x in rows if x["observed_equal"] != x["expected_equal"]],
               "false_positives": [x["id"] for x in rows if x["proof"]["status"] == "CFG_VALIDATED" and not x["observed_equal"]],
               "accepted_equivalent": [x["id"] for x in rows if x["proof"]["status"] == "CFG_VALIDATED" and x["observed_equal"]],
               "unverified_equivalent": [x["id"] for x in rows if x["proof"]["status"] == "UNVERIFIED" and x["observed_equal"]],
               "proven_different": [x["id"] for x in rows if x["proof"]["status"] == "CFG_MISMATCH"],
               "unverified_different": [x["id"] for x in rows if x["proof"]["status"] == "UNVERIFIED" and not x["observed_equal"]]}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    false_mismatches=[x["id"] for x in rows if x["proof"]["status"] == "CFG_MISMATCH" and x["observed_equal"]]
    return 1 if summary["false_positives"] or summary["observed_mismatches_from_expected"] or false_mismatches else 0

if __name__ == "__main__": raise SystemExit(main())
