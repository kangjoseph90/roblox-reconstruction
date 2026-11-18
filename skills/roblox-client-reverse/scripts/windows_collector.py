"""Read-only collection with verified or explicitly unverified runtime profiles."""
import base64
import collections
import ctypes as C
import hashlib
from pathlib import Path
import struct
import time

from compatibility import fingerprint, choose_profile
from pipeline import now, save_json
from rsb1 import unwrap
from windows_access_probe import kernel, owned_control, player_probe
from windows_memory import Inventory, Reader, hits, q, string
from adaptive_layout import discover, validate as validate_layout, load_cache, store_cache, header_size, LayoutError


class CollectionError(RuntimeError):
    def __init__(self, stage, detail):
        self.stage, self.detail = stage, detail
        super().__init__(str(detail))


def readable_private(region):
    return (region["type"] == 0x20000 and region["state"] == 0x1000
            and not region["protect"] & (0x100 | 1)
            and region["protect"] & 0xff in (2, 4, 8, 0x20, 0x40, 0x80))


def resolve_metadata(reader, profile, base, image_size, progress=lambda *args: None):
    """Validate candidate RVAs, or discover relocated class descriptors live.

    A different executable hash does not fail collection. Baseline object-layout
    fields are still candidates; subsequent instance/path/buffer checks remain.
    """
    table = base + profile["descriptor_vtable_rva"]
    expected = set(profile["class_descriptor_rvas"])
    if all(q(block := reader.read(base + rva, 32), 0) == table
           and string(reader, q(block, 8)) == name
           for name, rva in profile["class_descriptor_rvas"].items()):
        return dict(profile, metadata_resolution={"method": "candidate_RVAs_validated"})
    progress("discovering_class_metadata", {"compatibility_status": profile.get("compatibility_status", "unverified")})
    groups, strings = {}, {}
    requested, inspected, started = 0, 0, time.monotonic()
    byte_limit, probe_limit, deadline = 256 * 1024**2, 50000, 30
    limit_reached = False
    for region in reader.regions():
        if not (base <= region["base"] < base + image_size and region["state"] == 0x1000
                and not region["protect"] & (0x100 | 1)
                and region["protect"] & 0xff in (2, 4, 8, 0x20, 0x40, 0x80)):
            continue
        tail = b""
        for offset in range(0, min(region["size"], base + image_size - region["base"]), 8 * 1024**2):
            if requested >= byte_limit or inspected >= probe_limit or time.monotonic() - started >= deadline:
                limit_reached = True
                break
            size = min(8 * 1024**2, region["size"] - offset, byte_limit - requested)
            data = reader.read(region["base"] + offset, size)
            requested += size
            address = region["base"] + offset - len(tail)
            block = tail + data
            words = memoryview(block[:len(block) // 8 * 8]).cast("Q")
            for index in range(len(words) - 2):
                vtable, name_pointer = words[index], words[index + 1]
                if (not base <= vtable < base + image_size or words[index + 2] != 0
                        or not 0x10000 <= name_pointer < 0x7fffffffffff
                        or base <= name_pointer < base + image_size or name_pointer % 8):
                    continue
                if name_pointer not in strings:
                    if inspected >= probe_limit:
                        limit_reached = True
                        break
                    strings[name_pointer] = string(reader, name_pointer)
                    inspected += 1
                name = strings[name_pointer]
                if name not in expected:
                    continue
                method = q(reader.read(vtable, 8), 0)
                if not base <= method < base + image_size:
                    continue
                groups.setdefault(vtable, {}).setdefault(name, set()).add(address + index * 8)
            tail = data[-24:] if data else b""
            if limit_reached:
                break
        if limit_reached:
            break
    matches = [(table, names) for table, names in groups.items()
               if set(names) == expected and all(len(values) == 1 for values in names.values())]
    evidence = {"method": "live_descriptor_discovery", "image_bytes_requested": requested,
                "name_objects_inspected": inspected, "limit_reached": limit_reached,
                "matching_descriptor_groups": len(matches)}
    if len(matches) != 1:
        raise CollectionError("class_metadata", {"reason": "live class metadata could not be resolved uniquely", **evidence})
    table, names = matches[0]
    return dict(profile, descriptor_vtable_rva=table - base,
                class_descriptor_rvas={name: next(iter(values)) - base for name, values in names.items()},
                metadata_resolution=evidence)


def scan(reader, profile, base, image_size, budget, progress):
    layout = profile["layout"]
    table = base + profile["descriptor_vtable_rva"]
    descriptors = {base + rva: name for name, rva in profile["class_descriptor_rvas"].items()}
    for address, name in descriptors.items():
        block = reader.read(address, 32)
        if q(block, 0) != table or string(reader, q(block, 8)) != name:
            raise CollectionError("class_metadata", {"reason": "profile descriptor identity mismatch", "class": name})
    needles = {struct.pack("<Q", address): name for address, name in descriptors.items()}
    candidates, names, vtables = {}, {}, {}
    coverage = {"private_bytes_requested": 0, "bulk_bytes_read": 0, "unreadable_bulk_bytes": 0,
                "budget_bytes": budget, "budget_exhausted": False, "candidate_limit_reached": False}
    all_regions = list(reader.regions())
    coverage["readable_private_bytes"] = sum(r["size"] for r in all_regions if readable_private(r))
    for region in all_regions:
        if not readable_private(region):
            continue
        remaining = budget - coverage["private_bytes_requested"]
        if remaining <= 0:
            coverage["budget_exhausted"] = True
            break
        wanted = min(region["size"], remaining)
        for offset in range(0, wanted, 8 * 1024**2):
            address = region["base"] + offset
            size = min(8 * 1024**2, wanted - offset)
            data = reader.read(address, size)
            coverage["private_bytes_requested"] += size
            coverage["bulk_bytes_read"] += len(data)
            coverage["unreadable_bulk_bytes"] += size - len(data)
            for needle, cls in needles.items():
                # Every occurrence is examined; no silent per-page hit limit.
                for found in hits(data, needle, limit=len(data)):
                    ref = address + found
                    if ref % 8:
                        continue
                    instance = ref - layout["class_descriptor"]
                    start = found - layout["class_descriptor"]
                    needed = header_size(layout)
                    block = data[start:start + needed] if 0 <= start <= len(data) - needed else reader.read(instance, needed)
                    if q(block, layout["self"]) != instance or q(block, layout["class_descriptor"]) not in descriptors:
                        continue
                    vtable = q(block, 0)
                    if vtable not in vtables:
                        method = q(reader.read(vtable, 8), 0) if base <= vtable < base + image_size else 0
                        vtables[vtable] = base <= method < base + image_size
                    if not vtables[vtable]:
                        continue
                    ptr = q(block, layout["name"])
                    if ptr not in names:
                        names[ptr] = string(reader, ptr)
                    if names[ptr] is None:
                        continue
                    candidates.setdefault(instance, {"address": instance, "class": cls, "name": names[ptr],
                                            "parent_address": q(block, layout["parent"]), "observed_at": now(),
                                            "self_pointer": q(block, layout["self"]), "vtable": q(block, 0)})
                    if len(candidates) >= 200000:
                        coverage["candidate_limit_reached"] = True
                        return candidates, table, coverage
        if wanted < region["size"]:
            coverage["budget_exhausted"] = True
    progress("instances_scanned", {"candidates": len(candidates), **coverage})
    return candidates, table, coverage


def inventory_target(reader, candidates, table, profile, place_id):
    layout = profile["layout"]
    inventory = Inventory(reader, table, layout["class_descriptor"], layout["name"], layout["parent"], layout["self"])
    models = []
    for address, candidate in candidates.items():
        if candidate["class"] != "DataModel":
            continue
        block = reader.read(address, max(layout["place_id"], layout["universe_id"]) + 8)
        models.append(dict(candidate, place_id=q(block, layout["place_id"]), universe_id=q(block, layout["universe_id"])))
    roots = [m for m in models if m["place_id"] == place_id]
    if len(roots) != 1:
        raise CollectionError("target_identity", {"reason": "expected one DataModel matching requested Place ID", "models": models})
    target = roots[0]
    scripts, nodes, counts = [], {}, collections.Counter()
    for address, candidate in candidates.items():
        if candidate["class"] == "DataModel":
            continue
        chain = inventory.chain(address)
        if chain is None:
            counts["invalid_parent_chain"] += 1
            continue
        if not chain or chain[-1]["address"] != target["address"]:
            counts["other_datamodel_or_unparented"] += 1
            continue
        path = list(reversed(chain))
        service = path[1]["name"] if len(path) > 1 else ""
        if service in {"CoreGui", "CorePackages", "RobloxReplicatedStorage"}:
            counts["excluded_platform_service"] += 1
            continue
        scripts.append(dict(candidate, path=[n["name"] for n in path],
                            parent_chain=[n["address"] for n in path], service=service))
        for node in chain:
            nodes[node["address"]] = node
    return {"created_at": now(), "place_id": place_id, "universe_id": target["universe_id"],
            "target_datamodel": target["address"], "data_models": models,
            "counts": dict(counts), "scripts": scripts, "nodes": list(nodes.values())}


def fresh_chain(reader, table, layout, address):
    # A new instance cache prevents a previous script's stale parent/name from
    # validating a later script after streaming or reparenting.
    return Inventory(reader, table, layout["class_descriptor"], layout["name"], layout["parent"], layout["self"]).chain(address)


def observe_path(reader, table, layout, address):
    """Record the same reads used for validation, without a second diagnostic read."""
    observations = {}
    class RecordingReader:
        def read(self, pointer, size):
            data = reader.read(pointer, size)
            if size == header_size(layout):
                observations[pointer] = (now(), data)
            return data
    inv = Inventory(RecordingReader(), table, layout["class_descriptor"], layout["name"], layout["parent"], layout["self"])
    chain = inv.chain(address)
    timestamp, raw = observations.get(address, (now(), b""))
    node = inv.cache.get(address) or {}
    return {"observed_at": timestamp, "address": address, "read_bytes": len(raw),
            "expected_read_bytes": header_size(layout), "self_pointer": q(raw, layout["self"]), "vtable": q(raw, 0),
            "class": node.get("class"), "name": node.get("name"), "parent_address": q(raw, layout["parent"]),
            "chain": chain,
            "unreadable_nodes": [p for p, (_, data) in observations.items() if len(data) != header_size(layout)]}


def revalidation_reason(observation, script, target_datamodel):
    if observation["read_bytes"] != observation.get("expected_read_bytes", 128):
        return "node_unreadable"
    if observation["self_pointer"] != script["address"]:
        return "object_destroyed_or_replaced"
    if observation["class"] is not None and observation["class"] != script["class"]:
        return "class_changed"
    if observation["name"] is not None and observation["name"] != script["name"]:
        return "name_changed"
    if observation["class"] is None or observation["name"] is None:
        return "node_unreadable"
    chain = observation["chain"]
    if not chain:
        return "parent_chain_invalid"
    if chain[-1]["address"] != target_datamodel:
        return "wrong_datamodel"
    if [n["address"] for n in reversed(chain)] != script["parent_chain"]:
        return "parent_chain_invalid"
    if [n["name"] for n in reversed(chain)] != script["path"]:
        return "name_changed"
    if script.get("vtable") is not None and observation["vtable"] != script["vtable"]:
        return "object_destroyed_or_replaced"
    return None


def capture_buffers(reader, inventory, table, profile, out, identity):
    layout = profile["layout"]
    raw_dir, bytecode_dir = out / "originals", out / "bytecode"
    raw_dir.mkdir(parents=True)
    bytecode_dir.mkdir()
    records, by_address = [], {}
    # Transient Workspace objects are checked before stable replicated modules.
    for script in sorted(inventory["scripts"], key=lambda s: s.get("service") != "Workspace"):
        address = script["address"]
        record = dict(script, status="unavailable", captured_at=now())
        records.append(record)
        observation = observe_path(reader, table, layout, address)
        record["first_observation"] = {k: script.get(k) for k in ("observed_at", "address", "class", "name", "parent_address", "self_pointer", "vtable")}
        record["before_buffer_read"] = observation
        reason = revalidation_reason(observation, script, inventory["target_datamodel"])
        chain = observation["chain"]
        if reason:
            record["error"] = "instance or parent path changed since inventory"
            record.update(revalidation_reason=reason, failure_stage="before_buffer_read")
            continue
        field = layout[script["class"] + "_buffer"]
        owner = reader.read(address + field, 8)
        container = q(owner, 0)
        header = reader.read(container + layout["container_string"], 32)
        size, capacity = q(header, 16), q(header, 24)
        if len(header) != 32 or not 16 <= size <= 16 * 1024**2 or not size <= capacity <= 32 * 1024**2:
            record["error"] = "empty, unreadable or invalid script-owned buffer"
            continue
        pointer = q(header, 0)
        data, repeated = reader.read(pointer, size), reader.read(pointer, size)
        after_observation = observe_path(reader, table, layout, address)
        record["after_buffer_read"] = after_observation
        after = after_observation["chain"]
        if (len(data) != size or data != repeated or reader.read(address + field, 8) != owner
                or reader.read(container + layout["container_string"], 32) != header or chain != after):
            record["error"] = "short read or script/path changed during double read"
            record.update(revalidation_reason=revalidation_reason(after_observation, script, inventory["target_datamodel"]) or "unknown",
                          failure_stage="during_buffer_read",
                          stability={"complete_buffer": len(data) == size, "identical_reads": data == repeated,
                                     "identical_chain": chain == after})
            continue
        raw_sha = hashlib.sha256(data).hexdigest()
        raw = raw_dir / (raw_sha + ".roblox-buffer")
        raw.write_bytes(data)
        record.update(status="opaque_buffer", raw_sha256=raw_sha,
                      original=raw.relative_to(out).as_posix(), encoded_size=size, stable_double_read=True)
        payload, encoding = data, "unknown"
        try:
            payload, decoded = unwrap(data)
            record["decoded"] = decoded
            if decoded["bytecode_version"] not in profile["bytecode_versions"] or decoded["type_version"] not in profile["type_versions"]:
                raise ValueError("payload bytecode/type version is not approved by the matched profile")
            encoding = "roblox"
            digest = hashlib.sha256(payload).hexdigest()
            path = bytecode_dir / (digest + ".roblox-luau")
            path.write_bytes(payload)
            record.update(status="roblox_bytecode_captured", bytecode_sha256=digest, bytecode=path.relative_to(out).as_posix())
        except Exception as exc:
            payload = data
            record["decode_error"] = str(exc)
        by_address[address] = (payload, encoding, record)
    nodes = []
    failures = {r["address"]: r for r in records}
    for node in inventory["nodes"]:
        address, parent = node["address"], node["parent_address"]
        result = {"id": hex(address), "class": node["class"], "name": node["name"],
                  "parent": hex(parent) if parent else None,
                  "metadata": {"place_id": inventory["place_id"], "pid": identity["pid"],
                               "compatibility_status": profile.get("compatibility_status", "unverified"),
                               "profile": profile["id"], "player_sha256": identity["image"]["sha256"],
                               "capture_method": "ReadProcessMemory; verified parent chain; stable double read"}}
        if address in by_address:
            payload, encoding, record = by_address[address]
            result.update(bytecode_base64=base64.b64encode(payload).decode(), bytecode_format=encoding)
            result["metadata"].update({key: record[key] for key in ("raw_sha256", "bytecode_sha256", "original", "captured_at") if key in record})
            if record.get("decode_error"):
                result["capture_errors"] = [record["decode_error"]]
        elif address in failures:
            result["capture_errors"] = [failures[address].get("error", "unavailable")]
        nodes.append(result)
    dump = {"schema": "rcr-dump/v1", "capture": dict(identity, created_at=now(), place_id=inventory["place_id"],
             universe_id=inventory["universe_id"], profile=profile["id"], game_completeness="unknown", atomic=False), "instances": nodes}
    dump_path = out / "client-dump.json"
    save_json(dump_path, dump)
    result = {"created_at": now(), "place_id": inventory["place_id"], "profile": profile["id"],
              "status_counts": dict(collections.Counter(r["status"] for r in records)),
              "unique_original_buffers": len(list(raw_dir.iterdir())),
              "unique_bytecode_payloads": len(list(bytecode_dir.iterdir())), "scripts": records}
    result["revalidation_reason_counts"] = dict(collections.Counter(r["revalidation_reason"] for r in records if r.get("revalidation_reason")))
    counts = result['status_counts']
    captured = counts.get('roblox_bytecode_captured', 0)
    result['collection_result'] = dict(status='failed' if not captured else 'pass' if captured == len(records) else 'partial',
        observed_script_instances=len(records), integrity_valid_instances=captured,
        unavailable_instances=counts.get('unavailable', 0), opaque_instances=counts.get('opaque_buffer', 0),
        decoder_failures=dict(collections.Counter(r.get('decode_error') for r in records if r.get('decode_error'))),
        game_completeness='unknown', meaning='Validated observed snapshot only; process exit/normal termination is not collection success')
    save_json(out / "report.json", result)
    return dump_path, result


def collect(pid, executable, profile, place_id, output, budget_mib=4096, progress=lambda *args: None, structure_cache=None):
    out = Path(output)
    image = fingerprint(executable)
    actual = choose_profile(image)
    if not actual or actual["id"] != profile["id"]:
        raise CollectionError("process_identity", "running executable changed since environment selection")
    k = kernel()
    control = owned_control(k)
    access = player_probe(k, pid)
    save_json(out / "evidence/access.json", {"owned_control": control, "player": access})
    if not control["memory_read"].get("matches_expected") or not access.get("memory_read", {}).get("matches_expected"):
        raise CollectionError("process_access", {"owned_control": control, "player": access})
    if Path(access["image_path"]).resolve() != Path(executable).resolve():
        raise CollectionError("process_identity", "PID image differs from selected executable")
    base = int(access["main_module"]["base_address"], 16)
    reader = Reader(pid)
    try:
        from network import get
        import json
        url = 'https://apis.roblox.com/universes/v1/places/' + str(place_id) + '/universe'
        identity_meta, identity_raw = get(url)
        save_json(out / 'evidence/target-identity.json', dict(url=url, response=identity_meta,
                  body=identity_raw.decode('utf-8', 'replace')))
        try:
            universe_id = json.loads(identity_raw)['universeId']
            if identity_meta.get('status') != 200 or type(universe_id) is not int or universe_id <= 0:
                raise ValueError('invalid independent universe identity')
        except (ValueError, KeyError, TypeError) as exc:
            raise CollectionError('target_identity', 'independent Place/Universe identity unavailable') from exc
        cache_directory = Path(structure_cache) if structure_cache else out.parent / '.rcr-structures'
        cached, cache_status = load_cache(cache_directory, image)
        resolution = dict(cache_status=cache_status, player_sha256=image['sha256'])
        # Cached layouts are hypotheses. Metadata and all relationship/payload
        # witnesses must pass again in this process before collection can begin.
        if cached:
            try:
                # The cache supplies layout hypotheses only; approved encoding versions stay
                # those of the bundled compatibility profile.
                layout = {k: v for k, v in cached.items() if k not in ('bytecode_versions', 'type_versions')}
                candidate = resolve_metadata(reader, dict(profile, **layout), base, access['main_module']['image_size'], progress)
                candidates, table, coverage = scan(reader, candidate, base, access['main_module']['image_size'], budget_mib * 1024**2, progress)
                proof = validate_layout(reader, candidate, base, candidates, place_id, universe_id)
                profile = candidate
                resolution.update(method='cache_revalidated', validation=proof)
            except (LayoutError, CollectionError) as exc:
                resolution.update(cache_status='live_revalidation_rejected', cache_rejection=getattr(exc, 'detail', str(exc)))
                cached = None
        if not cached:
            profile = resolve_metadata(reader, profile, base, access['main_module']['image_size'], progress)
            progress('discovering_object_fields')
            profile, proof, discovery_coverage = discover(reader, profile, base, access['main_module']['image_size'],
                                                         place_id, universe_id, budget_mib * 1024**2, progress)
            candidates, table, coverage = scan(reader, profile, base, access['main_module']['image_size'], budget_mib * 1024**2, progress)
            resolution.update(method='discovered_and_held_out_validated', validation=proof, discovery_coverage=discovery_coverage)
        profile['structure_status'] = 'runtime_validated'
        if fingerprint(executable)['sha256'] != image['sha256']:
            raise CollectionError('process_identity', 'executable changed during structure validation; cache not committed')
        save_json(out / 'evidence/structure-validation.json', resolution)
        resolution['cache_path'] = store_cache(cache_directory, image, profile, proof)
        save_json(out / 'evidence/structure-validation.json', resolution)
        save_json(out / "evidence/resolved-profile.json", profile)
        inventory = inventory_target(reader, candidates, table, profile, place_id)
        if inventory['universe_id'] != universe_id:
            raise CollectionError('target_identity', 'resolved DataModel universe differs from independent target identity')
        if not inventory['scripts']:
            raise CollectionError('capture_empty', 'validated layout yielded no target script containers')
        progress("capturing_buffers", {"scripts": len(inventory["scripts"])})
        try:
            dump, result = capture_buffers(reader, inventory, table, profile, out, {"pid": pid, "image": image})
        finally:
            save_json(out / "evidence/inventory.json", inventory)
        result.update(coverage=coverage, native_api_stats=dict(reader.stats))
        if resolution.get('discovery_coverage'):
            result['structure_discovery_coverage'] = resolution['discovery_coverage']
            if any(resolution['discovery_coverage'].get(k) for k in ('budget_exhausted', 'candidate_limit_reached', 'discovery_deadline_reached', 'unreadable_bulk_bytes')):
                result['collection_result']['status'] = 'partial' if result['collection_result']['integrity_valid_instances'] else 'failed'
        if any(coverage.get(k) for k in ('budget_exhausted', 'candidate_limit_reached', 'unreadable_bulk_bytes')):
            result['collection_result']['status'] = 'partial' if result['collection_result']['integrity_valid_instances'] else 'failed'
        save_json(out / "report.json", result)
        return dump, result
    except LayoutError as exc:
        save_json(out / 'evidence/structure-validation.json', dict(status='failed', **exc.detail))
        raise CollectionError(exc.stage, exc.detail) from exc
    finally:
        reader.close()
