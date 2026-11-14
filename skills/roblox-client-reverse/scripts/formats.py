"""Static Roblox artifact readers. Never loads or executes Luau code."""
import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET

MAX_BYTES = 128 * 1024 * 1024
MAX_NODES = 200000
MAGIC = b"<roblox!\x89\xff\r\n\x1a\n"
SCRIPT_CLASSES = {"LocalScript", "ModuleScript", "Script"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def text(data):
    return data.decode("utf-8", errors="strict")


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("truncated artifact")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u8(self):
        return self.take(1)[0]

    def string(self):
        return self.take(self.u32())

    def array(self, count, signed=False, delta=False):
        if count > MAX_NODES:
            raise ValueError("instance count exceeds limit")
        raw = self.take(count * 4)
        values, previous = [], 0
        for i in range(count):
            v = int.from_bytes(bytes(raw[i + j * count] for j in range(4)), "big")
            if signed:
                v = (v >> 1) ^ -(v & 1)
            if delta:
                v += previous
                previous = v
            values.append(v)
        return values

    def end(self):
        if self.pos != len(self.data):
            raise ValueError("unexpected trailing chunk data")


def node(ref, cls, name=None, parent=None):
    return {"id": str(ref), "class": cls, "name": name or cls,
            "parent": None if parent is None else str(parent), "metadata": {}}


def parse_binary(data):
    """Version-0 script-focused decoder, including opaque type 0x1d preservation.

    Full geometry/physics decoding is deliberately outside this reader. Every
    undecoded property is inventoried and the original file remains authoritative.
    """
    r = Reader(data)
    if r.take(14) != MAGIC or r.take(2) != b"\0\0":
        raise ValueError("unsupported Roblox binary header/version")
    class_count, instance_count = r.u32(), r.u32()
    r.take(8)
    if class_count > MAX_NODES or instance_count > MAX_NODES:
        raise ValueError("binary instance limit exceeded")
    chunks, total, ended = [], 0, False
    while r.pos < len(data):
        tag, compressed, expanded, reserved = r.take(4), r.u32(), r.u32(), r.u32()
        total += expanded
        if total > MAX_BYTES or reserved:
            raise ValueError("invalid chunk size/reserved field or expansion limit")
        payload = r.take(compressed or expanded)
        codec = "none"
        if compressed:
            if payload.startswith(b"\x28\xb5\x2f\xfd"):
                try:
                    import zstandard
                except ImportError:
                    raise ValueError("ZSTD chunk requires pip install zstandard==0.25.0")
                frame_size = zstandard.frame_content_size(payload)
                if frame_size not in (expanded, zstandard.CONTENTSIZE_UNKNOWN):
                    raise ValueError("ZSTD frame size disagrees with chunk header")
                payload = zstandard.ZstdDecompressor().decompress(payload, max_output_size=expanded)
                codec = "zstd"
            else:
                try:
                    import lz4.block
                except ImportError:
                    raise ValueError("LZ4 chunk requires pip install lz4==4.4.5")
                payload = lz4.block.decompress(payload, uncompressed_size=expanded)
                codec = "lz4"
        if len(payload) != expanded:
            raise ValueError("decompressed chunk size mismatch")
        chunks.append((tag, payload, codec))
        if tag == b"END\0":
            if compressed or payload != b"</roblox>":
                raise ValueError("invalid END chunk")
            ended = True
            break
    if not ended or r.pos != len(data):
        raise ValueError("missing END chunk or trailing file data")
    classes, nodes, shared, properties, warnings = {}, {}, [], [], []
    for tag, payload, codec in chunks:
        c = Reader(payload)
        if tag == b"SSTR":
            if c.u32() != 0:
                raise ValueError("unsupported shared string version")
            n = c.u32()
            if n > MAX_NODES:
                raise ValueError("too many shared strings")
            for _ in range(n):
                c.take(16)
                shared.append(c.string())
            c.end()
        elif tag == b"INST":
            cid, cls, service, n = c.u32(), text(c.string()), c.u8(), c.u32()
            refs = c.array(n, signed=True, delta=True)
            if cid in classes or service not in (0, 1):
                raise ValueError("duplicate class or unsupported object format")
            if service:
                c.take(n)
            c.end()
            classes[cid] = (cls, refs)
            for ref in refs:
                if str(ref) in nodes or ref < 0:
                    raise ValueError("duplicate/negative instance referent")
                nodes[str(ref)] = node(ref, cls)
        elif tag not in {b"META", b"PROP", b"PRNT", b"END\0", b"SIGN"}:
            warnings.append("unknown chunk " + repr(tag))
    if len(classes) != class_count or len(nodes) != instance_count:
        raise ValueError("header counts disagree with instance chunks")
    for tag, payload, codec in chunks:
        if tag != b"PROP":
            continue
        c = Reader(payload)
        cid, prop, typ = c.u32(), text(c.string()), c.u8()
        if cid not in classes:
            raise ValueError("property refers to unknown class")
        cls, refs = classes[cid]
        entry = {"class": cls, "property": prop, "type_id": typ,
                 "count": len(refs), "payload_sha256": sha(payload[c.pos:]), "decoded": True}
        if typ in (1, 0x1d):
            values = [c.string() for _ in refs]
        elif typ == 0x1c:
            values = [shared[i] for i in c.array(len(refs))]
        elif typ == 2:
            values = [bool(c.u8()) for _ in refs]
        elif typ in (3, 0x12):
            values = c.array(len(refs), signed=(typ == 3))
        elif typ == 0x13:
            values = c.array(len(refs), signed=True, delta=True)
        else:
            entry["decoded"] = False
            properties.append(entry)
            continue
        c.end()
        properties.append(entry)
        for ref, value in zip(refs, values):
            item = nodes[str(ref)]
            if prop == "Name" and isinstance(value, bytes):
                item["name"] = text(value)
            elif typ == 0x1d or prop in {"Bytecode", "ScriptBytecode"}:
                if not isinstance(value, bytes):
                    raise ValueError("invalid bytecode property type")
                item["bytecode"] = value
                item["bytecode_format"] = "unknown"
            elif prop == "Source" and isinstance(value, bytes):
                try:
                    item["source"] = text(value)
                except UnicodeDecodeError:
                    item["opaque_source"] = value
            elif isinstance(value, bytes):
                try:
                    item["metadata"][prop] = text(value)
                except UnicodeDecodeError:
                    item["metadata"][prop] = {"encoding": "base64", "value": base64.b64encode(value).decode()}
            else:
                item["metadata"][prop] = value
    parent_seen = set()
    parent_chunks = 0
    for tag, payload, codec in chunks:
        if tag == b"PRNT":
            parent_chunks += 1
            c = Reader(payload)
            if c.u8() != 0:
                raise ValueError("unsupported PRNT version")
            n = c.u32()
            children = c.array(n, signed=True, delta=True)
            parents = c.array(n, signed=True, delta=True)
            c.end()
            for child, parent in zip(children, parents):
                key = str(child)
                if key not in nodes or key in parent_seen or (parent != -1 and str(parent) not in nodes):
                    raise ValueError("invalid/duplicate parent reference")
                parent_seen.add(key)
                nodes[key]["parent"] = None if parent == -1 else str(parent)
    if parent_chunks != 1 or len(parent_seen) != len(nodes):
        raise ValueError("incomplete hierarchy")
    return list(nodes.values()), {"format": "roblox-binary-v0", "warnings": warnings,
        "property_inventory": properties, "metadata_coverage": "script-focused; undecoded properties inventoried",
        "compression": sorted(set(c[2] for c in chunks))}


def parse_xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations are unsupported")
    root = ET.fromstring(data)
    if root.tag != "roblox" or root.get("version") != "4":
        raise ValueError("expected Roblox XML version 4")
    shared = {}
    for el in root.findall("./SharedStrings/SharedString"):
        shared[el.get("md5")] = base64.b64decode("".join((el.text or "").split()), validate=True)
    nodes, refs = [], set()
    stack = [(el, None) for el in reversed(root.findall("Item"))]
    while stack:
        el, parent = stack.pop()
        ref = el.get("referent", "xml-" + str(len(nodes)))
        if ref in refs or len(nodes) >= MAX_NODES:
            raise ValueError("duplicate referent or instance limit")
        refs.add(ref)
        item = node(ref, el.get("class", "Unknown"), parent=parent)
        for p in el.findall("./Properties/*"):
            name, value = p.get("name", ""), p.text or ""
            if p.tag == "SharedString":
                value = shared[value]
            if name == "Name":
                item["name"] = text(value) if isinstance(value, bytes) else value
            elif name == "Source" and p.tag in {"ProtectedString", "string", "SharedString"}:
                try:
                    item["source"] = text(value) if isinstance(value, bytes) else value
                except UnicodeDecodeError:
                    item["opaque_source"] = value
            elif p.tag == "Bytecode" or name in {"Bytecode", "ScriptBytecode"}:
                item["bytecode"] = value if isinstance(value, bytes) else base64.b64decode("".join(value.split()), validate=True)
                item["bytecode_format"] = "unknown"
            else:
                # Preserve type and nested XML for all properties, including attributes/packages.
                item["metadata"][name] = {"type": p.tag, "text": value if isinstance(value, str)
                    else {"base64": base64.b64encode(value).decode()}, "xml": ET.tostring(p, encoding="unicode")}
        nodes.append(item)
        stack.extend((child, ref) for child in reversed(el.findall("Item")))
    return nodes, {"format": "roblox-xml-v4", "warnings": [],
                   "metadata_coverage": "all serialized properties as typed XML"}


def parse_dump(data):
    d = json.loads(data)
    if not isinstance(d, dict) or d.get("schema") != "rcr-dump/v1":
        raise ValueError("unsupported JSON; expected schema rcr-dump/v1")
    if not isinstance(d.get("capture", {}), dict):
        raise ValueError("capture must be an object")
    items = d.get("instances")
    if not isinstance(items, list) or len(items) > MAX_NODES:
        raise ValueError("invalid instance list")
    result = []
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("id", "class", "name")):
            raise ValueError("instance requires string id/class/name")
        if item.get("parent") is not None and not isinstance(item["parent"], str):
            raise ValueError("parent must be a string or null")
        if "source" in item and not isinstance(item["source"], str):
            raise ValueError("source must be a string")
        if item.get("bytecode_format", "unknown") not in {"unknown", "luau", "roblox"}:
            raise ValueError("bytecode_format must be unknown, luau or roblox")
        item = dict(item)
        item.setdefault("metadata", {})
        if not isinstance(item["metadata"], dict):
            raise ValueError("metadata must be an object")
        if "bytecode_base64" in item:
            item["bytecode"] = base64.b64decode(item.pop("bytecode_base64"), validate=True)
        # No embedded file paths are opened; this format is entirely self-contained.
        result.append(item)
    return result, {"format": "rcr-dump/v1", "warnings": [], "capture": d.get("capture", {}),
                    "metadata_coverage": "as supplied; capture claims unverified"}


def parse_artifact(data, suffix):
    if len(data) > MAX_BYTES:
        raise ValueError("file exceeds 128 MiB limit")
    if data.startswith(MAGIC):
        return parse_binary(data)
    if data.lstrip(b"\xef\xbb\xbf \r\n\t").startswith((b"<roblox", b"<?xml")):
        return parse_xml(data)
    if suffix == ".json":
        return parse_dump(data)
    item = node("file", "ModuleScript", "standalone")
    item["metadata"]["instance_class_inferred"] = True
    if suffix in {".luac", ".bc", ".bytecode"}:
        item["bytecode"], item["bytecode_format"] = data, "unknown"
        fmt = "opaque-bytecode-file"
    elif suffix in {".lua", ".luau"}:
        item["source"] = text(data)
        fmt = "source-file"
    else:
        raise ValueError("unrecognized artifact; extensions do not substitute for valid Roblox headers")
    return [item], {"format": fmt, "warnings": [], "metadata_coverage": "no original instance hierarchy"}
