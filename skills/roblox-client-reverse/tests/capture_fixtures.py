"""Authored RSB1 fixtures, never evidence of live game collection."""
import base64
import hashlib
import json
from pathlib import Path
import struct

import zstandard
from rsb1 import xxh32


def write_capture(root, place, modules, universe=200, root_name="Game"):
    root = Path(root)
    raw_dir = root / "originals"
    raw_dir.mkdir(parents=True)
    nodes = [{"id": "0x1", "class": "DataModel", "name": root_name},
             {"id": "0x2", "class": "ReplicatedStorage", "name": "ReplicatedStorage", "parent": "0x1"}]
    records = []
    for i, (name, payload) in enumerate(modules):
        address = 0x100 + i
        node = {"id": hex(address), "class": "ModuleScript", "name": name, "parent": "0x2"}
        row = {"address": address, "status": "unavailable", "error": "authored unavailable observation"}
        if payload is not None:
            envelope = b"RSB1" + struct.pack("<I", len(payload)) + zstandard.ZstdCompressor().compress(payload)
            key = xxh32(envelope).to_bytes(4, "little")
            encoded = bytes(v ^ ((key[i % 4] + i * 41) & 255) for i, v in enumerate(envelope))
            digest = hashlib.sha256(encoded).hexdigest()
            (raw_dir / (digest + ".roblox-buffer")).write_bytes(encoded)
            row = {"address": address, "status": "roblox_bytecode_captured", "raw_sha256": digest,
                   "bytecode_sha256": hashlib.sha256(payload).hexdigest()}
            # Recovery must ignore this decoded convenience field and use the original buffer.
            node.update(bytecode_base64=base64.b64encode(payload).decode(), bytecode_format="roblox")
        nodes.append(node)
        records.append(row)
    report = {"schema": "rcr-capture/v1", "place_id": place, "universe_id": universe, "scripts": records,
              "collection_result": {"status": "partial" if any(p is None for _, p in modules) else "pass"}}
    dump = {"schema": "rcr-dump/v1", "capture": {"place_id": place, "universe_id": universe}, "instances": nodes}
    for name, value in [("report.json", report), ("client-dump.json", dump)]:
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    seal(root)
    return root


def seal(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "files.json":
            data = path.read_bytes()
            rows.append({"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (root / "files.json").write_text(json.dumps({"schema": "rcr-files/v1", "files": rows}), encoding="utf-8")
