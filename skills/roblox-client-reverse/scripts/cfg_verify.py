"""Conservative compile-roundtrip certificates; never execute input code.

Certify only an exact reachable instruction graph under a bijective register
renaming, constant-pool renumbering, jump threading and predicate inversion.
FASTCALL builtin identities, arguments and both CFG edges are retained.
A concrete counterexample is CFG_MISMATCH; anything inconclusive is UNVERIFIED.
This deliberately does not infer equivalence from matching branch counts.
"""
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Declared bytecode format -> luaudec disassembler flag. Vanilla Luau is "--standard".
DISASSEMBLER_FLAGS = {"luau": "--standard", "standard": "--standard", "roblox": "--roblox"}


class ToolFailure(RuntimeError):
    """The disassembler could not be run; no CFG comparison was attempted."""


def disassemble(executable, path, encoding):
    if encoding not in DISASSEMBLER_FLAGS:
        raise ToolFailure("unsupported disassembler encoding: %r" % (encoding,))
    p = subprocess.run([str(executable), DISASSEMBLER_FLAGS[encoding], "--json", str(path)], capture_output=True, timeout=60)
    if p.returncode:
        raise ToolFailure(p.stderr.decode("utf-8", "replace"))
    return json.loads(p.stdout)


# Instructions with an exact certificate rule; anything else is UNVERIFIED.
SUPPORTED_OPS = frozenset({"LOADNIL", "LOADB", "LOADN", "LOADK", "LOADKX", "MOVE", "GETGLOBAL", "SETGLOBAL", "GETUPVAL", "SETUPVAL", "CLOSEUPVALS", "GETIMPORT", "GETTABLE", "SETTABLE", "GETTABLEKS", "SETTABLEKS", "GETTABLEN", "SETTABLEN", "NEWCLOSURE", "NAMECALL", "CALL", "CALLFB", "RETURN", "ADD", "SUB", "MUL", "DIV", "IDIV", "MOD", "POW", "AND", "OR", "CONCAT", "ADDK", "SUBK", "MULK", "DIVK", "IDIVK", "MODK", "POWK", "ANDK", "ORK", "SUBRK", "DIVRK", "NOT", "MINUS", "LENGTH", "NEWTABLE", "DUPTABLE", "SETLIST", "GETVARARGS", "DUPCLOSURE", "CAPTURE", "JUMPIF", "JUMPIFNOT", "JUMPIFEQ", "JUMPIFNOTEQ", "JUMPIFLT", "JUMPIFNOTLT", "JUMPIFLE", "JUMPIFNOTLE", "JUMPXEQKNIL", "JUMPXEQKB", "JUMPXEQKN", "JUMPXEQKS", "FORNPREP", "FORNLOOP", "FORGPREP", "FORGPREP_NEXT", "FORGPREP_INEXT", "FORGLOOP"} | {"FASTCALL", "FASTCALL1", "FASTCALL2", "FASTCALL2K", "FASTCALL3"})
LABEL_REF = re.compile(r"\bL-?\d+")
REGISTER_REF = re.compile(r"\bR(\d+)")
CONSTANT_REF = re.compile(r"\bK(\d+)")


class Unverified(ValueError):
    pass


class CanonicalModule:
    def __init__(self, module):
        self.module, self.memo, self.graphs, self.slot_maps = module, {}, {}, {}

    def constant(self, proto, index, slots):
        c = proto["constants"][index]
        kind = c["kind"]
        if kind == "vector" or (kind == "number" and "nan" in c["text"].lower()):
            if 'numericBitsLE' not in c:
                raise Unverified("exact vector/NaN bits are not exported by this metadata format")
            return [kind, c['numericEncoding'], c['numericBitsLE']]
        if kind == "string":
            if "stringBytes" not in c:
                raise Unverified("full string bytes unavailable")
            return [kind, c["stringBytes"]]
        if kind == "import":
            return [kind, c["importPath"]]
        if kind == "closure":
            child = int(re.match(r"function (\d+)", c["text"])[1])
            slot = slots.setdefault(index, len(slots))
            return [kind, slot, self.reference(child)]
        if kind == "table":
            slot = slots.setdefault(index, len(slots))
            return [kind, slot, [self.constant(proto, k, slots) for k in c["keys"]],
                    [None if v < 0 else self.constant(proto, v, slots) for v in c["values"]]]
        if kind not in {"nil", "boolean", "number", "integer", "vector"}:
            raise Unverified("unsupported constant certificate: " + kind)
        return [kind, c["text"]]

    def signature(self, index):
        if index in self.memo:
            return self.memo[index]
        # Body hashes alone must not merge distinct prototypes or cached
        # closure slots: DUPCLOSURE identity is observable through equality.
        self.proto_ids, self.definitions = {}, []
        self.reference(index)
        value = self.definitions
        self.graphs[index] = value
        self.memo[index] = digest(value)
        return self.memo[index]

    def reference(self, index):
        if index not in self.proto_ids:
            identity = len(self.proto_ids)
            self.proto_ids[index] = identity
            self.definitions.append(None)
            self.definitions[identity] = self.graph(index)
        return self.proto_ids[index]

    def graph(self, index):
        p = self.module["protos"][index]
        insns = p["instructions"]
        by_pc = {i["pc"]: n for n, i in enumerate(insns)}
        regs = {r: r for r in range(p["params"])}
        slots = {}
        ids, queue, graph = {}, [], []

        def reg(r):
            if r not in regs:
                regs[r] = len(regs)
            return regs[r]

        def follow(n):
            seen = set()
            while n < len(insns):
                if n in seen:
                    raise Unverified("empty jump cycle")
                seen.add(n)
                i = insns[n]
                if i["op"] in {"JUMP", "JUMPBACK", "JUMPX"}:
                    n = by_pc[i["target"]]
                elif i["op"] in {"NOP", "COVERAGE", "PREPVARARGS"}:
                    n += 1
                else:
                    return n
            raise Unverified("fallthrough beyond function")

        def edge(n):
            n = follow(n)
            if n not in ids:
                ids[n] = len(ids)
                queue.append(n)
            return ids[n]

        edge(0)
        negative = {"JUMPIFNOT": "JUMPIF", "JUMPIFNOTEQ": "JUMPIFEQ", "JUMPIFNOTLT": "JUMPIFLT", "JUMPIFNOTLE": "JUMPIFLE"}
        for n in queue:
            i = insns[n]
            op, a, b, c = i["op"], i["a"], i["b"], i["c"]
            if op not in SUPPORTED_OPS:
                raise Unverified("unsupported instruction certificate: " + op)
            text = i["text"].split(" [", 1)[0]
            if op == "FORGLOOP":
                # Keep iterator mode and all AUX bits after stripping display annotations.
                text += " aux=" + str(i["aux"] & 0xffffffff)
            text = LABEL_REF.sub("@", text)
            text = REGISTER_REF.sub(lambda m: "r" + str(reg(int(m[1]))), text)
            text = CONSTANT_REF.sub(lambda m: json.dumps(self.constant(p, int(m[1]), slots), separators=(",", ":")), text)
            if op == "GETIMPORT":
                text = "GETIMPORT r%d %s" % (reg(a), json.dumps(self.constant(p, i["d"], slots)))
            if op == "DUPTABLE":
                text = "DUPTABLE r%d %s" % (reg(a), json.dumps(self.constant(p, i["d"], slots)))
            if op == "NEWCLOSURE":
                text = "NEWCLOSURE r%d proto%d" % (reg(a), self.reference(p["children"][i["d"]]))
            if op == "CALLFB":
                text = text.replace("CALLFB", "CALL", 1)
            implicit = []
            # Preserve all implicitly addressed registers, not merely the base.
            if op in {"CALL", "CALLFB"}:
                implicit = [list(range(a, a + b)) if b else list(range(a, p["maxstack"])),
                            list(range(a, a + c - 1)) if c else list(range(a, p["maxstack"]))]
            elif op == "RETURN":
                implicit = [list(range(a, a + b - 1)) if b else list(range(a, p["maxstack"]))]
            elif op == "NAMECALL":
                implicit = [[a, a + 1]]
            elif op.startswith("FORN"):
                implicit = [list(range(a, a + 3))]
            elif op.startswith("FORG"):
                implicit = [list(range(a, a + 3 + (i["aux"] & 255 if op == "FORGLOOP" else 0)))]
            elif op == "GETVARARGS":
                implicit = [list(range(a, a + b - 1)) if b else list(range(a, p["maxstack"]))]
            elif op == "SETLIST":
                implicit = [list(range(b, b + c - 1)) if c else list(range(b, p["maxstack"]))]
                text += " start=" + str(i["aux"])
            elif op == "CONCAT":
                implicit = [list(range(b, c + 1))]
            elif op == "CLOSEUPVALS":
                implicit = [list(range(a, p["maxstack"]))]
            if op.startswith("FASTCALL"):
                after = by_pc[i["target"]]
                fallback = insns[after - 1] if after else {}
                if fallback.get("op") not in {"CALL", "CALLFB"}:
                    raise Unverified("FASTCALL target does not follow its guarded fallback CALL")
                base, argc, results = fallback["a"], fallback["b"], fallback["c"]
                implicit = [list(range(base, base + argc)) if argc else list(range(base, p["maxstack"])),
                            list(range(base, base + results - 1)) if results else list(range(base, p["maxstack"]))]
            implicit = [[reg(r) for r in group] for group in implicit]
            if op == "RETURN":
                succs = []
            elif op.startswith("JUMP") or op.startswith("FASTCALL") or op in {"FORNPREP", "FORNLOOP", "FORGLOOP"}:
                target, fall = by_pc[i["target"]], n + 1
                inverted = op in negative or (op.startswith("JUMPXEQ") and i["aux"] >> 31)
                if op in negative:
                    text = text.replace(op, negative[op], 1)
                if inverted:
                    text = text.replace(" NOT", "")
                succs = [edge(fall), edge(target)] if inverted else [edge(target), edge(fall)]
            elif op.startswith("FORGPREP"):
                succs = [edge(by_pc[i["target"]])]
            elif op == "LOADB" and c:
                text = re.sub(r" \+\d+$", "", text)
                succs = [edge(by_pc[i["target"]])]
            else:
                succs = [edge(n + 1)]
            graph.append([text, implicit, succs])
        self.slot_maps[index] = dict(slots)
        return [p["params"], p["upvalues"], p["vararg"], graph]


def compare_exact(original, recovered):
    left, right = CanonicalModule(original), CanonicalModule(recovered)
    candidates, errors = {}, {}
    for p in recovered["protos"]:
        try:
            candidates.setdefault(right.signature(p["index"]), []).append(p["index"])
        except (Unverified, KeyError, IndexError) as e:
            errors[p["index"]] = str(e)
    records = []
    for p in original["protos"]:
        try:
            sig = left.signature(p["index"])
            matches = candidates.get(sig, [])
            records.append({"proto": p["index"], "name": p["name"], "signature": sig,
                            "matching_recovered_protos": matches,
                            "status": "CFG_VALIDATED" if matches else "UNVERIFIED",
                            "scope": "reachable instruction/prototype graph; fixed ordered upvalue and external closure-cache environment unless parent also matches"})
        except (Unverified, KeyError, IndexError) as e:
            records.append({"proto": p["index"], "name": p["name"], "status": "UNVERIFIED", "reason": str(e)})
    main = next(r for r in records if r["proto"] == original["mainProto"])
    complete = recovered["mainProto"] in main.get("matching_recovered_protos", [])
    return {"status": "CFG_VALIDATED" if complete else "UNVERIFIED", "method": "exact reachable instruction/prototype graph modulo documented normalizations/v3; FASTCALL edges and cached-object identities retained",
            "behavior": "NOT_TESTED", "functions": records, "original_proto_count": len(original["protos"]),
            "recovered_proto_count": len(recovered["protos"]), "recovered_certificate_limits": errors}


def compare(original, recovered):
    """Retain the v0.3.2 certificate; add independently discharged SSA proofs."""
    from cfg_normalize import ModuleProof
    from cfg_diagnose import candidates, classify
    result = compare_exact(original, recovered)
    result['exact_baseline_status'] = result['status']
    result['method'] = 'exact v0.3.2 fallback + relational SSA/CFG product/v0.3.3'
    root_proof = None
    if result['status'] != 'CFG_VALIDATED':
        root_proof = ModuleProof(original, recovered).run(original['mainProto'], recovered['mainProto'])
        result['status'] = root_proof['status']
        result['module_proof'] = root_proof
    aligned = {}
    if root_proof:
        for f in root_proof.get('functions', []):
            aligned[f['original_proto']] = f
    for record in result['functions']:
        record['exact_baseline_status'] = record['status']
        record['syntax'] = 'SYNTAX_VALID'  # comparison is invoked only after successful recompilation
        record['behavior'] = 'NOT_TESTED'
        if record['status']=='CFG_VALIDATED':
            record['certificate'] = 'exact_instruction_prototype_graph'
            record['correspondence'] = 'standalone_body_match; module context is separate'
            if record['proto'] in aligned and aligned[record['proto']]['status']=='CFG_MISMATCH':
                record['standalone_body_status']='CFG_VALIDATED'
                record['status']='CFG_MISMATCH'
                record['context_counterexample']=aligned[record['proto']]
            continue
        index=record['proto'];record['status']='UNVERIFIED'
        attempts=[]
        for candidate,confidence,basis in candidates(original,recovered,index):
            proof = root_proof if index==original['mainProto'] and candidate==recovered['mainProto'] else ModuleProof(original,recovered).run(index,candidate)
            if proof is None:continue
            attempt={'recompiled_proto':candidate,'mapping_confidence':confidence,'mapping_basis':basis,'proof':proof,
                     'diagnosis':classify(original['protos'][index],recovered['protos'][candidate],proof,proof['status']=='CFG_VALIDATED')}
            if basis!='module_entry':
                attempt['diagnosis']['actual_decompiler_error']='not_established; candidate correspondence requires parent evidence'
            attempts.append(attempt)
            if proof['status']=='CFG_VALIDATED':
                record.update(status='CFG_VALIDATED',certificate='relational_ssa_cfg',matching_recovered_protos=[candidate],
                              scope='ordered effects/data flow; fixed ordered external upvalue/cache environment; excludes debug/timing/OOM')
                break
        record['normalization_attempts']=attempts
        if attempts:
            best=next((a for a in attempts if a['proof']['status']=='CFG_VALIDATED'),attempts[0])
            record['diagnosis']=best['diagnosis']
            record['recompiled_proto']=best['recompiled_proto']
        else:record['diagnosis']={'categories':['unsupported_pattern'],'certainty':'correspondence_unresolved','reason':'no supported candidate interface/correspondence','actual_decompiler_error':'not_established'}
        if index in aligned and aligned[index]['status']=='CFG_MISMATCH':
            record['status']='CFG_MISMATCH';record['context_counterexample']=aligned[index]
        elif index==original['mainProto'] and root_proof and root_proof['status']=='CFG_MISMATCH':record['status']='CFG_MISMATCH'
    result['function_summary']=dict(__import__('collections').Counter(f['status']for f in result['functions']))
    result['normalization_assumptions']=['ordered effects, exceptions and unknown metamethod operations retained',
        'only exact small-integer arithmetic folds; no reassociation or unknown-type arithmetic reordering',
        'Boolean path partitions cover all abstract paths; bounded analysis yields UNVERIFIED',
        'loop invariants preserve equality/alias relations and are checked on every product transition',
        'REF captures require exact fallback; unsupported patterns remain UNVERIFIED',
        'standalone function certificates assume ordered external upvalue/closure-cache environment',
        'module certification requires all referenced prototypes and cached identities to match',
        'debug/source names, timing, allocation failure and asynchronous mutation excluded']
    return result


def audit_source(bytecode, source, tools, encoding, folder):
    """Compile only; preserve both CFGs and the exact comparison in folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    executable = tools["decompiler"]["argv"][0]
    original = disassemble(executable, bytecode, encoding)
    (folder / "original.json").write_text(json.dumps(original), encoding="utf-8")
    reports = []
    for opt in (1, 2):
        argv = [s.replace("{input}", str(source)) for s in tools["compiler"]["argv"]]
        argv.insert(1, "-O" + str(opt))
        p = subprocess.run(argv, capture_output=True, timeout=60)
        target = folder / ("recompiled-O%d.bytecode" % opt)
        target.write_bytes(p.stdout)
        if p.returncode:
            raise ValueError("roundtrip compile failed: " + p.stderr.decode("utf-8", "replace"))
        recovered = disassemble(executable, target, "standard")
        (folder / ("recompiled-O%d.json" % opt)).write_text(json.dumps(recovered), encoding="utf-8")
        result = dict(compare(original, recovered), optimization=opt)
        (folder / ("comparison-O%d.json" % opt)).write_text(json.dumps(result, indent=2), encoding="utf-8")
        reports.append(result)
    return max(reports, key=lambda r: (r["status"] == "CFG_VALIDATED", sum(f["status"] == "CFG_VALIDATED" for f in r["functions"])))
