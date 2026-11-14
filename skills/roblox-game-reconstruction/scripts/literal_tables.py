#!/usr/bin/env python3
"""Conservative static assembler for one literal-only Luau proto. Never executes bytecode."""
from __future__ import annotations
import argparse, copy, json, math, struct
from collections import Counter
from pathlib import Path

SOURCE_FILE=""
ALLOWED = {"PREPVARARGS", "NOP", "COVERAGE", "LOADNIL", "LOADB", "LOADN", "LOADK", "LOADKX", "MOVE", "NEWTABLE", "DUPTABLE", "SETTABLEKS", "SETTABLE", "SETTABLEN", "SETLIST", "RETURN"}
SKIP = {"PREPVARARGS", "NOP", "COVERAGE"}

class Reject(ValueError): pass

def bits_of_float(x: float) -> str:
    return struct.pack("<d", x).hex()

def f64_from_le(hexstr: str) -> float:
    if len(hexstr) != 16: raise Reject(f"bad f64 numericBitsLE: {hexstr!r}")
    return struct.unpack("<d", bytes.fromhex(hexstr))[0]

def const_value(constants, idx, pc, line, op):
    if not isinstance(idx, int) or idx < 0 or idx >= len(constants): raise Reject(f"pc {pc}: constant index out of range {idx}")
    c = constants[idx]
    if c.get("index") != idx: raise Reject(f"pc {pc}: constant index field mismatch at K{idx}")
    kind = c.get("kind")
    prov = {"source_file": SOURCE_FILE, "source_line": line, "origin_pc": pc, "opcode": op, "constant_index": idx}
    if kind == "string":
        if "stringBytes" not in c: raise Reject(f"pc {pc}: string constant lacks stringBytes")
        return {"type":"string", "value":c["stringBytes"], "provenance":prov}
    if kind == "number":
        if c.get("numericEncoding") != "f64" or "numericBitsLE" not in c: raise Reject(f"pc {pc}: numeric constant is not pinned f64")
        val = f64_from_le(c["numericBitsLE"])
        if not math.isfinite(val): raise Reject(f"pc {pc}: non-finite numeric constant unsupported")
        prov.update({"numeric_encoding":"f64", "f64_bits_le":c["numericBitsLE"], "f64_bits":"0x" + bytes.fromhex(c["numericBitsLE"])[::-1].hex()})
        return {"type":"number", "decimal":repr(val), "value":val, "provenance":prov}
    if kind == "boolean":
        return {"type":"boolean", "value":(c.get("text") == "true"), "provenance":prov}
    if kind == "nil": return {"type":"nil", "value":None, "provenance":prov}
    raise Reject(f"pc {pc}: unsupported K{idx} constant kind {kind!r}")

def imm_value(value, pc, line, op):
    n = float(value)
    prov={"source_file":SOURCE_FILE,"source_line":line,"origin_pc":pc,"opcode":op}
    if op == "LOADN":
        prov.update({"numeric_encoding":"f64", "f64_bits_le":bits_of_float(n), "f64_bits":"0x"+bytes.fromhex(bits_of_float(n))[::-1].hex()})
        return {"type":"number","decimal":str(int(value)) if int(value)==value else repr(n),"value":n,"provenance":prov}
    return {"type":"boolean","value":bool(value),"provenance":prov}

def scalar_json(v):
    if v is None: return None
    if isinstance(v, dict) and v.get("_kind")=="table": return table_json(v)
    if not isinstance(v,dict) or "type" not in v: raise Reject("internal nonliteral value")
    out={k:copy.deepcopy(x) for k,x in v.items() if k != "value" or v.get("type") != "number"}
    if v["type"] == "number": out["decimal"] = v["decimal"]
    elif v["type"] in ("string","boolean"): out["value"] = v["value"]
    return out

def table_json(t):
    entries=[]
    for k,v in t["entries"].items():
        entries.append({"key":k,"value":scalar_json(v),"field_assignment":t.get("assignments",{}).get(k)})
    return {"kind":"typed_table","identity":t["identity"],"creation":{"source_file":SOURCE_FILE,"source_line":t["line"],"origin_pc":t["created_pc"],"opcode":t["created_op"],"template_constant_index":t.get("template_constant_index")},"entries":entries}

def friendly_value(v):
    if isinstance(v,dict) and v.get("_kind")=="table":
        # Tables with consecutive positive integer keys are represented as arrays; other tables as objects.
        keys=list(v["entries"])
        if all(isinstance(k,int) and k>0 for k in keys) and sorted(keys)==list(range(1,len(keys)+1)):
            return [friendly_value(v["entries"][k]) for k in range(1,len(keys)+1)]
        return {str(k):friendly_value(x) for k,x in v["entries"].items()}
    if isinstance(v,dict) and v.get("type")=="number": return v["decimal"]
    if isinstance(v,dict) and v.get("type")=="nil": return None
    if isinstance(v,dict): return v.get("value")
    return v

def flatten_leaves(v, path=()):
    if isinstance(v,dict) and v.get("_kind")=="table":
        for k,x in v["entries"].items(): yield from flatten_leaves(x,path+(k,))
    else: yield path,v

def canonical_key(k): return str(k) if isinstance(k,(int,float)) else k

def assemble(source):
    if not isinstance(source,dict) or not isinstance(source.get("protos"),list): raise Reject("input is not exported Luau JSON")
    mi=source.get("mainProto")
    if not isinstance(mi,int) or not 0 <= mi < len(source["protos"]): raise Reject("invalid mainProto")
    proto=source["protos"][mi]
    if proto.get("children"): raise Reject("main proto has children; literal-only scope requires one proto")
    code=proto.get("instructions")
    constants=proto.get("constants")
    if not isinstance(code,list) or not isinstance(constants,list): raise Reject("proto lacks instructions/constants")
    regs={}; tables=[]; counts=Counter(); returned=None; expected_pc=0
    def reg(n,pc):
        if n not in regs: raise Reject(f"pc {pc}: read of uninitialized R{n}")
        return regs[n]
    def table_for(n,pc):
        x=reg(n,pc)
        if not isinstance(x,dict) or x.get("_kind")!="table": raise Reject(f"pc {pc}: R{n} is not a literal table")
        return x
    def put(t,k,v,pc,ins):
        if k is None: raise Reject(f"pc {pc}: nil table key rejected")
        if isinstance(k,dict):
            if k.get("type")=="nil": raise Reject(f"pc {pc}: nil table key rejected")
            if k.get("type")=="number": k=k["value"]
            elif k.get("type") in ("string","boolean"): k=k["value"]
            else: raise Reject(f"pc {pc}: unsupported literal table key")
        if isinstance(k,bool): raise Reject("Boolean table keys are outside this adapter subset")
        if isinstance(k,float) and k.is_integer(): k=int(k)
        # Luau table assignment of nil deletes an entry; preserve no nonexistent leaf.
        if isinstance(v,dict) and v.get("type")=="nil":
            t["entries"].pop(k,None); t.get("assignments",{}).pop(k,None)
        else:
            t["entries"][k]=v
            t.setdefault("assignments",{})[k]={"source_file":SOURCE_FILE,"source_line":ins.get("line"),"assignment_pc":pc,"opcode":ins.get("op")}
    for ins in code:
        pc=ins.get("pc"); op=ins.get("op")
        if not isinstance(pc,int) or pc != expected_pc: raise Reject(f"instruction pc {pc!r} does not follow expected pc {expected_pc}")
        length=ins.get("len")
        if not isinstance(length,int) or length < 1: raise Reject(f"pc {pc}: invalid instruction length {length!r}")
        expected_pc=pc+length; counts[op]+=1
        if op not in ALLOWED: raise Reject(f"pc {pc}: unsupported/nonliteral opcode {op}")
        a,b,c,aux=[ins.get(k) for k in ("a","b","c","aux")]; line=ins.get("line")
        if op in SKIP: continue
        if op == "LOADNIL": regs[a]={"type":"nil","value":None,"provenance":{"source_file":SOURCE_FILE,"source_line":line,"origin_pc":pc,"opcode":op}}
        elif op == "LOADB":
            if c != 0: raise Reject(f"pc {pc}: LOADB skip form rejected (C={c})")
            if b not in (0,1): raise Reject(f"pc {pc}: LOADB literal must be 0 or 1")
            regs[a]=imm_value(b,pc,line,op)
        elif op == "LOADN": regs[a]=imm_value(ins.get("d"),pc,line,op)
        elif op == "LOADK": regs[a]=const_value(constants,ins.get("d"),pc,line,op)
        elif op == "LOADKX": regs[a]=const_value(constants,aux,pc,line,op)
        elif op == "MOVE": regs[a]=reg(b,pc) # preserves table identity exactly
        elif op == "NEWTABLE":
            t={"_kind":"table","identity":len(tables),"created_pc":pc,"created_op":op,"line":line,"entries":{},"assignments":{}}
            tables.append(t); regs[a]=t
        elif op == "DUPTABLE":
            idx=ins.get("d")
            if not isinstance(idx,int) or idx<0 or idx>=len(constants) or constants[idx].get("kind")!="table": raise Reject(f"pc {pc}: DUPTABLE does not reference table template")
            template=constants[idx]; entries={}
            template_keys=template.get("keys",[]); template_values=template.get("values",[])
            if not isinstance(template_keys,list) or not isinstance(template_values,list): raise Reject(f"pc {pc}: malformed DUPTABLE template")
            if template_values and len(template_values)!=len(template_keys): raise Reject(f"pc {pc}: template key/value length mismatch")
            for pos,ki in enumerate(template_keys):
                key=const_value(constants,ki,pc,line,op)
                if key["type"]=="nil" or key["type"] not in ("string","number","boolean"): raise Reject(f"pc {pc}: nil/non-scalar template key rejected")
                keyval=key["value"]
                if isinstance(keyval,bool): raise Reject("Boolean table keys are outside this adapter subset")
                if isinstance(keyval,float) and keyval.is_integer(): keyval=int(keyval)
                if keyval in entries: raise Reject(f"pc {pc}: duplicate DUPTABLE template key {keyval!r}")
                value_idx=template_values[pos] if template_values else -1
                if value_idx == -1:
                    entries[keyval]=None
                elif not isinstance(value_idx,int) or value_idx < 0:
                    raise Reject(f"pc {pc}: malformed DUPTABLE template value index {value_idx!r}")
                else:
                    val=const_value(constants,value_idx,pc,line,op)
                    if val["type"]=="nil": entries[keyval]=None
                    else:
                        val["provenance"]["template_value"]=True
                        entries[keyval]=val
            t={"_kind":"table","identity":len(tables),"created_pc":pc,"created_op":op,"line":line,"template_constant_index":idx,"entries":entries,"assignments":{}}
            tables.append(t); regs[a]=t
        elif op == "SETTABLEKS":
            val=reg(a,pc); t=table_for(b,pc)
            key=const_value(constants,aux,pc,line,op)
            if key["type"]!="string": raise Reject(f"pc {pc}: SETTABLEKS key is not string")
            put(t,key["value"],val,pc,ins)
        elif op == "SETTABLE":
            val=reg(a,pc); t=table_for(b,pc); key=reg(c,pc); put(t,key,val,pc,ins)
        elif op == "SETTABLEN":
            val=reg(a,pc); t=table_for(b,pc)
            if not isinstance(c,int) or c<0: raise Reject(f"pc {pc}: bad SETTABLEN index {c}")
            put(t,c+1,val,pc,ins)
        elif op == "SETLIST":
            t=table_for(a,pc); count= c-1 if isinstance(c,int) else -1; start=aux
            if count<0 or count>len(regs) or not isinstance(start,int) or start<1: raise Reject(f"pc {pc}: SETLIST must have fixed count and 1-based start")
            for off in range(count): put(t,start+off,reg(b+off,pc),pc,ins)
        elif op == "RETURN":
            if returned is not None: raise Reject(f"pc {pc}: multiple RETURNs")
            # fixed-one result: B=2 means one returned register in Luau encoding
            if b != 2: raise Reject(f"pc {pc}: RETURN must return fixed one value (B=2), got B={b}")
            returned=reg(a,pc)
            if code[-1] is not ins: raise Reject(f"pc {pc}: RETURN not final instruction")
    if expected_pc != proto.get("codeSize",expected_pc): raise Reject(f"decoded instruction end {expected_pc} != codeSize {proto.get('codeSize')}")
    if returned is None: raise Reject("no fixed-one RETURN")
    if not isinstance(returned,dict) or returned.get("_kind")!="table": raise Reject("returned value is not literal table")
    return returned,tables,counts,proto

def json_friendly(root): return friendly_value(root)


def run_self_tests():
    def synthetic(ins, constants=None):
        return {"mainProto":0,"protos":[{"children":[],"constants":constants or [],"instructions":ins}]}
    def i(pc,op,**kw): return {"pc":pc,"op":op,"line":1,"len":1,**kw}
    def must_reject(label, data):
        try: assemble(data)
        except Reject: return
        raise AssertionError(f"negative control accepted: {label}")
    # Valid empty table returned.
    root,_,_,_=assemble(synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"RETURN",a=0,b=2,c=0,aux=0)]))
    assert root["entries"]=={}
    # SETLIST literal values and MOVE table alias retain identity and source origins.
    cs=[{"index":0,"kind":"number","text":"3","numericEncoding":"f64","numericBitsLE":bits_of_float(3.0)}]
    root,ts,_,_=assemble(synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"LOADK",a=1,d=0),i(2,"MOVE",a=2,b=0),i(3,"SETLIST",a=2,b=1,c=2,aux=1),i(4,"RETURN",a=0,b=2,c=0,aux=0)],cs))
    assert root is ts[0] and root["entries"][1]["provenance"]["origin_pc"]==1
    # DUPTABLE constant keys are literal strings; assignment provenance is separate from source value origin.
    tconst=[{"index":0,"kind":"string","text":"'x'","stringBytes":"x"},{"index":1,"kind":"table","text":"{...}","keys":[0],"values":[]}]
    root,_,_,_=assemble(synthetic([i(0,"DUPTABLE",a=0,d=1),i(1,"LOADN",a=1,d=4),i(2,"SETTABLEKS",a=1,b=0,aux=0),i(3,"RETURN",a=0,b=2,c=0,aux=0)],tconst))
    assert root["entries"]["x"]["provenance"]["origin_pc"]==1 and root["assignments"]["x"]["assignment_pc"]==2
    valued=[{"index":0,"kind":"string","text":"'x'","stringBytes":"x"},{"index":1,"kind":"number","text":"6","numericEncoding":"f64","numericBitsLE":bits_of_float(6.0)},{"index":2,"kind":"table","text":"{...}","keys":[0],"values":[1]}]
    root,_,_,_=assemble(synthetic([i(0,"DUPTABLE",a=0,d=2),i(1,"RETURN",a=0,b=2,c=0,aux=0)],valued))
    assert root["entries"]["x"]["value"]==6 and root["entries"]["x"]["provenance"]["template_value"]
    must_reject("nil DUPTABLE template key", synthetic([i(0,"DUPTABLE",a=0,d=0),i(1,"RETURN",a=0,b=2,c=0,aux=0)],[{"index":0,"kind":"table","text":"{...}","keys":[1],"values":[]},{"index":1,"kind":"nil","text":"nil"}]))
    must_reject("unknown/call opcode", synthetic([i(0,"CALL",a=0,b=0,c=0)]))
    must_reject("LOADB with skip", synthetic([i(0,"LOADB",a=0,b=1,c=1),i(1,"RETURN",a=0,b=2,c=0,aux=0)]))
    # nil dynamic key is rejected, even if loaded by supported literal opcode.
    must_reject("nil key", synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"LOADNIL",a=1),i(2,"LOADNIL",a=2),i(3,"SETTABLE",a=1,b=0,c=2)]))
    # Alias should be mutable as same literal object, not silently copied.
    root,ts,_,_=assemble(synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"MOVE",a=1,b=0),i(2,"LOADN",a=2,d=8),i(3,"SETTABLEN",a=2,b=1,c=0),i(4,"RETURN",a=0,b=2,c=0,aux=0)]))
    assert root is ts[0] and root["entries"][1]["value"]==8
    # RETURN shape, nonfinal return and table template validation.
    must_reject("empty main is invalid", synthetic([]))
    must_reject("SETLIST unknown register", synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"SETLIST",a=0,b=1,c=2,aux=1),i(2,"RETURN",a=0,b=2,c=0,aux=0)]))
    must_reject("SETLIST multret", synthetic([i(0,"NEWTABLE",a=0,b=0,c=0,aux=0),i(1,"SETLIST",a=0,b=1,c=0,aux=1),i(2,"RETURN",a=0,b=2,c=0,aux=0)]))
    print("literal assembler self-tests: PASS (12 assertion controls)")


def main():
    global SOURCE_FILE
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--source-label',required=True)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args();SOURCE_FILE=args.source_label
    if args.self_test:run_self_tests()
    data=json.loads(args.input.read_text(encoding='utf-8-sig'))
    root,tables,counts,proto=assemble(data)
    # Omitted template fields are nil, not materialized content values.
    for table in tables:
        table['entries']={k:v for k,v in table['entries'].items()if v is not None}
    def walk(table,active):
        if table['identity']in active:raise Reject('cyclic literal tables are outside the export subset')
        for v in table['entries'].values():
            if isinstance(v,dict)and v.get('_kind')=='table':walk(v,active|{table['identity']})
    walk(root,set())
    args.out.mkdir(parents=True,exist_ok=True)
    result={'schema':'rgr-literal-table/v1','scope':'linear literal original-instruction data only; no CFG/behavior certificate',
            'source_label':SOURCE_FILE,'original_json_sha256':__import__('hashlib').sha256(args.input.read_bytes()).hexdigest(),
            'original_proto':proto.get('index',data['mainProto']),'instructions':len(proto['instructions']),
            'opcode_counts':dict(counts),'table':table_json(root),'friendly':json_friendly(root)}
    (args.out/'literal-data.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'pass','instructions':result['instructions'],'table_identities':len(tables),'scope':result['scope']}))

if __name__=='__main__':main()
