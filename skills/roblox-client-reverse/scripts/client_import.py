"""Read UniversalSynSaveInstance artifacts without executing their contents.

This is an independent format adapter, not an executor or a live collector.
Protocol observed at upstream commit 888b5f318d1e035434c473c7b952bd1f85b767ab.
"""
import base64
import hashlib
import re

PRODUCER = "UniversalSynSaveInstance"
REVISION = "888b5f318d1e035434c473c7b952bd1f85b767ab"
BANNER = re.compile(r"\A\s*-- Saved by UniversalSynSaveInstance[^\r\n]*(?:\r?\n|$)")
BYTECODE = re.compile(r"(?m)^-- Bytecode \(Base64\):\r?\n-- ([^\r\n]*)")
PLACEHOLDERS = {
    "-- Decompiling is disabled": "decompilation_disabled",
    "-- Your Executor does NOT have a Decompiler": "decompiler_unavailable",
    "-- The Script is Empty": "collector_reported_empty",
    "-- Not found in already decompiled ScriptCache": "cache_miss",
    "-- Ignored": "collector_ignored",
    "-- [FilteringEnabled] Server Scripts are IMPOSSIBLE to save": "server_source_unavailable",
}


def has_code(value, unclosed=False):
    """Find a noncomment token; diagnostic-only source is not recovered code.

    `unclosed` is returned for an unterminated long comment (the text after it belongs to that comment)."""
    pos = 0
    while pos < len(value):
        if value[pos].isspace():
            pos += 1
        elif value.startswith("--", pos):
            match = re.match(r"--\[(=*)\[", value[pos:])
            if match:
                end = value.find("]" + match.group(1) + "]", pos + len(match.group(0)))
                if end < 0:
                    return unclosed
                pos = end + len(match.group(1)) + 2
            else:
                end = value.find("\n", pos)
                pos = len(value) if end < 0 else end + 1
        else:
            return True
    return False


def adapt(nodes, info):
    count = 0
    for n in nodes:
        source = n.get("source")
        if not isinstance(source, str) or not BANNER.match(source):
            continue
        count += 1
        n["source_origin"] = "collector_output; may be decompiled/cached; game provenance unverified"
        meta = n.setdefault("metadata", {})
        meta["client_import"] = {"producer_claim": PRODUCER, "adapter_protocol_revision": REVISION,
            "original_source_property_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "hierarchy": "serialized tree; collector may reparent/rename instances"}
        body = BANNER.sub("", source, count=1)
        marker = BYTECODE.search(body)
        if marker and has_code(body[:marker.start()], unclosed=True):
            marker = None  # collector markers precede the output; the same text inside code is source
        if marker:
            try:
                bytecode = base64.b64decode(marker.group(1), validate=True)
                if not bytecode:
                    raise ValueError("empty bytecode")
                if "bytecode" in n and n["bytecode"] != bytecode:
                    raise ValueError("embedded bytecode conflicts with serialized bytecode property")
                n["bytecode"] = bytecode
                n["bytecode_format"] = "unknown"
                meta["client_import"]["embedded_bytecode"] = "decoded_base64; encoding_unestablished"
            except (ValueError, TypeError) as e:
                n.setdefault("capture_errors", []).append("embedded_bytecode: " + str(e))
            body = body[:marker.start()] + body[marker.end():]
        elif "-- Bytecode (Base64):" in body and not has_code(body[:body.index("-- Bytecode (Base64):")], unclosed=True):
            n.setdefault("capture_errors", []).append("embedded_bytecode: malformed marker or missing payload line")
        # Upstream may add a one-line original asset URL before the output.
        body = re.sub(r"\A\s*-- Original Source: [^\r\n]*(?:\r?\n|$)", "", body)
        stripped = body.strip()
        status = PLACEHOLDERS.get(stripped)
        if stripped.startswith("--[[ Failed to decompile. Reason:") and stripped.endswith("]]"):
            status = "decompilation_failed"
        if stripped == "-- Decompile ignored" or stripped.startswith("-- Ignored by DecompileIgnore"):
            status = "collector_ignored"
        if status or not has_code(body):
            n.pop("source", None)
            n["capture_status"] = status or "collector_comment_only_output"
        else:
            n["source"] = body
            n["capture_status"] = "collector_supplied_source"
    if count:
        info["client_import"] = {"producer_claim": PRODUCER, "scripts_detected": count,
            "adapter_protocol_revision": REVISION, "live_collection_verified": False,
            "provenance": "content markers identify an output convention; they do not attest who collected it"}
    return nodes, info
