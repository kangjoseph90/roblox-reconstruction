"""Bounded, read-only layout discovery with held-out relationship/payload checks.

Supported ABI assumptions are explicit: x64 pointers/vtable at zero, a shared
Instance layout, descriptor name at +8 and MSVC std::string. No offset is accepted
because of an executable version, a name alone, or a cache hit.
"""

import collections
import copy
import hashlib
import json
from pathlib import Path
import struct
import time

from rsb1 import unwrap
from zstandard import ZstdError
from windows_memory import Inventory, hits, q, string

ALGORITHM = 'rcr-layout/1'
HEADER = 0x180
SCRIPT_BYTES = 0x600
ROOT_BYTES = 0x400
CONTAINER_BYTES = 0x80
MIN_CLASS = 6  # three discovery + three held-out script instances per class
SAMPLE = 24
CLASSES = ('ModuleScript', 'LocalScript')


class LayoutError(RuntimeError):
    stage = 'adaptive_structure'

    def __init__(self, reason, **evidence):
        self.detail = dict(reason=reason, **evidence)
        super().__init__(reason)


def pointer(value):
    return 0x10000 <= value < 0x7FFFFFFFFFFF and value % 8 == 0


def header_size(layout):
    return max(128, max(layout[k] for k in ('self', 'class_descriptor', 'name', 'parent')) + 8)


def private(region):
    return (
        region['type'] == 0x20000
        and region['state'] == 0x1000
        and not region['protect'] & 0x101
        and region['protect'] & 0xFF in (2, 4, 8, 0x20, 0x40, 0x80)
    )


def discover_instances(reader, profile, base, image_size, budget, progress):
    """Find base/self/descriptor triples independently of the historical layout."""
    descriptors = {base + r: n for n, r in profile['class_descriptor_rvas'].items()}
    groups, vtables = {}, {}
    coverage = dict(
        private_bytes_requested=0,
        bulk_bytes_read=0,
        unreadable_bulk_bytes=0,
        budget_bytes=budget,
        budget_exhausted=False,
        candidate_limit_reached=False,
        discovery_deadline_reached=False,
    )
    regions = list(reader.regions())
    coverage['region_walk_incomplete'] = not getattr(reader, 'regions_complete', True)
    coverage['readable_private_bytes'] = sum(r['size'] for r in regions if private(r))
    started = time.monotonic()
    for region in regions:
        if not private(region):
            continue
        tail = b''
        for offset in range(0, region['size'], 8 * 1024**2):
            remaining = budget - coverage['private_bytes_requested']
            if remaining <= 0:
                coverage['budget_exhausted'] = True
                break
            if time.monotonic() - started > 180:
                coverage['discovery_deadline_reached'] = True
                break
            count = min(8 * 1024**2, region['size'] - offset, remaining)
            raw = reader.read(region['base'] + offset, count)
            coverage['private_bytes_requested'] += count
            coverage['bulk_bytes_read'] += len(raw)
            coverage['unreadable_bulk_bytes'] += count - len(raw)
            data = tail + raw
            origin = region['base'] + offset - len(tail)
            for descriptor, cls in descriptors.items():
                for found in hits(data, struct.pack('<Q', descriptor), limit=len(data)):
                    ref = origin + found
                    if ref % 8:
                        continue
                    for cd_offset in range(8, HEADER, 8):
                        start = found - cd_offset
                        address = ref - cd_offset
                        if start < 0 or start + HEADER > len(data):
                            continue
                        table = q(data, start)
                        if not base <= table < base + image_size:
                            continue
                        if table not in vtables:
                            # Observed image-owned anchor relation. This is not
                            # an assertion that the first slot is executable:
                            # protected Player images do not expose a reliable
                            # static/live execute-permission classification.
                            anchor = q(reader.read(table, 8), 0)
                            vtables[table] = base <= anchor < base + image_size
                        if not vtables[table]:
                            continue
                        block = data[start : start + HEADER]
                        for self_offset in range(8, HEADER, 8):
                            if self_offset == cd_offset or q(block, self_offset) != address:
                                continue
                            group = groups.setdefault((cd_offset, self_offset), {})
                            group[address] = dict(address=address, **{'class': cls})
                            if len(group) > 200000:
                                coverage['candidate_limit_reached'] = True
                                raise LayoutError('instance candidate limit reached', coverage=coverage)
            # Only join contiguous complete reads; never bridge an unreadable gap.
            tail = raw[-2 * HEADER :] if len(raw) == count else b''
        if any(coverage[k] for k in ('budget_exhausted', 'discovery_deadline_reached')):
            break
    viable = {}
    for pair, nodes in groups.items():
        counts = collections.Counter(n['class'] for n in nodes.values())
        if counts['DataModel'] and all(counts[c] >= MIN_CLASS for c in CLASSES):
            viable[pair] = nodes
    progress('instance_layout_candidates', dict(hypotheses=len(viable), **coverage))
    if not viable:
        raise LayoutError('no independently witnessed Instance base/self/descriptor layout', coverage=coverage)
    return viable, coverage


def samples(nodes):
    # Interleave by address deterministically; hold-outs never select an offset.
    result = {}
    for cls in CLASSES:
        all_nodes = sorted((n for n in nodes.values() if n['class'] == cls), key=lambda n: n['address'])
        if len(all_nodes) < MIN_CLASS:
            raise LayoutError('insufficient independent class witnesses', script_class=cls)
        sides = (all_nodes[::2], all_nodes[1::2])
        result[cls] = tuple(side[:: max(1, len(side) // (SAMPLE // 2))][: SAMPLE // 2] for side in sides)
    return result


def inv(reader, table, layout):
    return Inventory(reader, table, layout['class_descriptor'], layout['name'], layout['parent'], layout['self'])


def place_set(place_ids):
    """One requested Place ID or the official catalogue of one Universe."""
    ids = {place_ids} if isinstance(place_ids, int) else set(place_ids)
    if not ids or any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError('positive Place IDs required')
    return ids


def root_fields(reader, nodes, place_ids, universe_id):
    """Find the one DataModel holding the Universe ID and exactly one catalogue Place ID.

    Returns (address, place offset, universe offset, observed Place ID). The observed Place ID is the
    Player's current Place, which may differ from the requested one after a Place transfer."""
    expected = place_set(place_ids)
    matches = []
    for n in nodes.values():
        if n['class'] != 'DataModel':
            continue
        block = reader.read(n['address'], ROOT_BYTES)
        places = [o for o in range(0, len(block) - 7, 8) if q(block, o) in expected]
        universes = [o for o in range(0, len(block) - 7, 8) if q(block, o) == universe_id]
        if len(places) == len(universes) == 1 and places[0] != universes[0]:
            matches.append((n['address'], places[0], universes[0], q(block, places[0])))
    if len(matches) != 1:
        raise LayoutError(
            'DataModel identity fields are absent or ambiguous',
            matching_datamodels=len(matches),
            expected_place_ids=sorted(expected),
            expected_universe_id=universe_id,
        )
    return matches[0]


def chains(reader, table, layout, witnesses, root, strict=False):
    parents, services, depths, result = set(), set(), [], []
    for node in witnesses:
        # Independent cache per witness; repeat raw identity/path observations later.
        chain = inv(reader, table, layout).chain(node['address'])
        if not chain or chain[0]['class'] != node['class'] or chain[-1]['class'] != 'DataModel':
            return None
        if chain[-1]['address'] != root or chain[-1]['parent_address'] != 0:
            return None
        if len(chain) < 3:
            return None
        parents.add(chain[0]['parent_address'])
        services.add(chain[-2]['class'])
        depths.append(len(chain))
        result.append(chain)
    if strict and (len(parents) < 3 or len(services) < 2):
        return None
    return result


def target_witnesses(reader, table, layout, candidates, root):
    """Exclude other DataModels/unparented objects, not payload failures.

    This eligibility step reads only identity/ancestry. It never selects a
    held-out witness because its bytecode happens to validate.
    """
    result = [n for n in candidates if chains(reader, table, layout, [n], root)]
    counts = collections.Counter(n['class'] for n in result)
    return result if all(counts[c] >= 3 for c in CLASSES) else []


def payload_witnesses(reader, table, layout, nodes, root):
    """Partition before inspecting fields, then select by target/scope only.

    The full Instance population includes the app shell, detached objects and
    platform CoreScript modules. Buffer validation uses the same target/service
    scope as collection. Payload validity never influences witness selection.
    """
    result = ({}, {})
    view = inv(reader, table, layout)
    for cls in CLASSES:
        ordered = sorted((n for n in nodes.values() if n['class'] == cls), key=lambda n: n['address'])
        eligible = ([], [])
        for index, n in enumerate(ordered):
            chain = view.chain(n['address'])
            if not chain or len(chain) < 3 or chain[0]['class'] != cls or chain[-1]['address'] != root:
                continue
            if chain[-2]['name'] in {'CoreGui', 'CorePackages', 'RobloxReplicatedStorage'}:
                continue
            eligible[index % 2].append(n)
        for side in (0, 1):
            choices = eligible[side]
            if len(choices) < 3:
                raise LayoutError(
                    'insufficient scope-eligible independently partitioned script witnesses',
                    script_class=cls,
                    partition=side,
                    count=len(choices),
                )
            result[side][cls] = choices[:: max(1, len(choices) // (SAMPLE // 2))][: SAMPLE // 2]
    return result


def child_relations(reader, chain_list):
    """Find one common children-vector field, then verify reciprocal membership.

    Children storage is restricted to vector<ptr/shared_ptr> (8/16 byte stride).
    Unsupported container layouts are rejected rather than guessed.
    """
    pairs = sorted({(c[i + 1]['address'], c[i]['address']) for c in chain_list for i in range(len(c) - 1)})
    # Multiple children of the same parent do not count as independent parents.
    selected = {}
    vectors = {}
    for parent, child in pairs:
        selected.setdefault(parent, child)
    if len(selected) < 4:
        return []
    for parent, child in list(selected.items())[:12]:
        block = reader.read(parent, HEADER)
        found = set()
        for field in range(8, len(block) - 7, 8):
            container = q(block, field)
            if not pointer(container):
                continue
            if container not in vectors:
                vectors[container] = reader.read(container, 24)
            vector = vectors[container]
            begin, end = q(vector, 0), q(vector, 8)
            if not pointer(begin) or end < begin or end - begin > 8 * 1024**2:
                continue
            for stride in (8, 16):
                if not end > begin or (end - begin) % stride:
                    continue
                data = reader.read(begin, end - begin)
                if len(data) != end - begin:
                    continue
                if any(q(data, i) == child for i in range(0, len(data), stride)):
                    found.add((field, stride))
        if not found:
            return []
        if parent == next(iter(selected)):
            common = found
        else:
            common &= found
        if not common:
            return []
    # A 16-byte vector also matches 8-byte stepping; only accept the 16-byte
    # hypothesis when all odd slots are non-Instance control pointers (below).
    return sorted(common)


def validate_children(reader, chain_list, field, stride, layout, table):
    checked = set()
    for chain in chain_list:
        for index in range(len(chain) - 1):
            child, parent = chain[index]['address'], chain[index + 1]['address']
            if (parent, child) in checked:
                continue
            checked.add((parent, child))
            vector = reader.read(q(reader.read(parent + field, 8), 0), 24)
            begin, end = q(vector, 0), q(vector, 8)
            if not pointer(begin) or not begin < end <= begin + 8 * 1024**2 or (end - begin) % stride:
                return False
            data = reader.read(begin, end - begin)
            if len(data) != end - begin or child not in {q(data, i) for i in range(0, len(data), stride)}:
                return False
            # Distinguish ptr vectors from shared_ptr vectors using all slots,
            # not by selecting whichever stride happens to contain the child.
            view = inv(reader, table, layout)
            for i in range(0, len(data), stride):
                node = view.node(q(data, i))
                if not node or node['parent_address'] != parent:
                    return False
    return len(checked) >= 4


def buffer_at(reader, address, field, string_offset):
    owner = reader.read(address + field, 8)
    container = q(owner, 0)
    header = reader.read(container + string_offset, 32)
    size, capacity = q(header, 16), q(header, 24)
    if len(header) != 32 or not 16 <= size <= 16 * 1024**2 or not size <= capacity <= 32 * 1024**2:
        return None
    data = reader.read(q(header, 0), size)
    if len(data) != size:
        return None
    try:
        payload, decoded = unwrap(data)
    except (ValueError, ZstdError):
        return None
    if reader.read(address + field, 8) != owner or reader.read(container + string_offset, 32) != header:
        return None
    if reader.read(q(header, 0), size) != data:
        return None
    return dict(
        raw_sha256=hashlib.sha256(data).hexdigest(), bytecode_sha256=hashlib.sha256(payload).hexdigest(), **decoded
    )


def instance_extent(reader, address, layout):
    """Do not treat a subsequent adjacent Instance's buffer as an owned field."""
    block = reader.read(address, SCRIPT_BYTES)
    view = inv(reader, 0, layout)  # descriptor table assigned by caller below
    # The caller's descriptor is itself live metadata, with the common vtable.
    descriptor = q(block, layout['class_descriptor'])
    view.descriptor_vtable = q(reader.read(descriptor, 8), 0)
    for offset in range(8, max(8, len(block) - header_size(layout) + 1), 8):
        if q(block, offset + layout['self']) != address + offset:
            continue
        if view.node(address + offset):
            return offset
    return len(block)


def buffer_candidates(reader, node, layout=None):
    block = reader.read(node['address'], SCRIPT_BYTES)
    result = set()
    extent = instance_extent(reader, node['address'], layout) if layout else len(block)
    for field in range(8, min(len(block), extent) - 7, 8):
        container = q(block, field)
        if not pointer(container):
            continue
        for offset in range(0, CONTAINER_BYTES - 31, 8):
            if buffer_at(reader, node['address'], field, offset):
                result.add((field, offset))
    return result


def discover(reader, profile, base, image_size, place_ids, universe_id, budget, progress):
    viable, coverage = discover_instances(reader, profile, base, image_size, budget, progress)
    accepted, diagnostics = [], []
    table = base + profile['descriptor_vtable_rva']
    for (descriptor, self_offset), nodes in viable.items():
        try:
            selected = samples(nodes)
            train = [n for cls in CLASSES for n in selected[cls][0]]
            held = [n for cls in CLASSES for n in selected[cls][1]]
            root, place, universe, _ = root_fields(reader, nodes, place_ids, universe_id)
            name_fields = []
            for name in range(8, HEADER, 8):
                if name in (descriptor, self_offset):
                    continue
                if all(string(reader, q(reader.read(n['address'] + name, 8), 0)) is not None for n in train):
                    name_fields.append(name)
            layouts = []
            for name in name_fields:
                for parent in range(8, HEADER, 8):
                    if parent in (descriptor, self_offset, name):
                        continue
                    layout = dict(
                        self=self_offset,
                        class_descriptor=descriptor,
                        name=name,
                        parent=parent,
                        place_id=place,
                        universe_id=universe,
                    )
                    eligible = target_witnesses(reader, table, layout, train, root)
                    observed = chains(reader, table, layout, eligible, root, strict=True) if eligible else None
                    if not observed:
                        continue
                    for children, stride in child_relations(reader, observed):
                        if validate_children(reader, observed, children, stride, layout, table):
                            layouts.append(dict(layout, children=children, child_stride=stride))
            if len(layouts) != 1:
                raise LayoutError('name/parent/reciprocal-children hypothesis is not unique', hypotheses=len(layouts))
            layout = layouts[0]
            buffer_train, buffer_held = payload_witnesses(reader, table, layout, nodes, root)
            train_target = [n for cls in CLASSES for n in buffer_train[cls]]
            held_target = [n for cls in CLASSES for n in buffer_held[cls]]
            offsets = {}
            for cls in CLASSES:
                common = None
                for n in [n for n in train_target if n['class'] == cls][:3]:
                    found = buffer_candidates(reader, n, layout)
                    common = found if common is None else common & found
                offsets[cls] = common or set()
                if len(offsets[cls]) != 1:
                    raise LayoutError(
                        'script buffer field is not unique', script_class=cls, hypotheses=len(offsets[cls])
                    )
            module_field, string_field = next(iter(offsets['ModuleScript']))
            local_field, local_string = next(iter(offsets['LocalScript']))
            if local_string != string_field:
                raise LayoutError('script container layouts differ between classes; unsupported')
            layout.update(
                ModuleScript_buffer=module_field, LocalScript_buffer=local_field, container_string=string_field
            )
            candidate = dict(profile, layout=layout)
            proof = validate(reader, candidate, base, nodes, place_ids, universe_id, witnesses=held_target)
            proof.update(
                training_instances=len(train_target),
                eligibility='address-parity partition then target ancestry/service scope, before testing held-out payloads',
            )
            accepted.append((candidate, proof))
        except LayoutError as exc:
            diagnostics.append(dict(class_descriptor=descriptor, self=self_offset, **exc.detail))
    if len(accepted) != 1:
        raise LayoutError(
            'no unique held-out validated structure', hypotheses=len(accepted), rejected=diagnostics, coverage=coverage
        )
    candidate, proof = accepted[0]
    return candidate, dict(proof, discovery_rejections=diagnostics), coverage


def validate(reader, profile, base, nodes, place_ids, universe_id, witnesses=None):
    layout = profile['layout']
    table = base + profile['descriptor_vtable_rva']
    root, place, universe, observed_place = root_fields(reader, nodes, place_ids, universe_id)
    if (place, universe) != (layout['place_id'], layout['universe_id']):
        raise LayoutError('cached identity offsets changed')
    if witnesses is None:
        splits = payload_witnesses(reader, table, layout, nodes, root)
        witnesses = [n for split in splits for cls in CLASSES for n in split[cls]]
    observed = chains(reader, table, layout, witnesses, root, strict=True)
    if not observed or not validate_children(
        reader, observed, layout['children'], layout['child_stride'], layout, table
    ):
        raise LayoutError('held-out identity/path/reciprocal membership validation failed')
    payloads = []
    for n, chain in zip(witnesses, observed):
        vtable = q(reader.read(n['address'], 8), 0)
        if not vtable or not q(reader.read(vtable, 8), 0):
            raise LayoutError('Instance image anchor is unreadable', script_class=n['class'])
        if layout[n['class'] + '_buffer'] + 8 > instance_extent(reader, n['address'], layout):
            raise LayoutError(
                'buffer candidate lies in a subsequent Instance or unreadable memory', script_class=n['class']
            )
        value = buffer_at(reader, n['address'], layout[n['class'] + '_buffer'], layout['container_string'])
        after = inv(reader, table, layout).chain(n['address'])
        if not value or chain != after:
            raise LayoutError('held-out script-owned bytecode or path validation failed', script_class=n['class'])
        if (
            value['bytecode_version'] not in profile['bytecode_versions']
            or value['type_version'] not in profile['type_versions']
        ):
            raise LayoutError('validated envelope contains unsupported bytecode/type version', **value)
        payloads.append(dict(script_class=n['class'], **value))
    for cls in CLASSES:
        if len({p['bytecode_sha256'] for p in payloads if p['script_class'] == cls}) < 2:
            raise LayoutError('insufficient distinct held-out bytecode witnesses', script_class=cls)
    return dict(
        passed=True,
        algorithm=ALGORITHM,
        target_datamodel=root,
        observed_place_id=observed_place,
        identity_witness=dict(place_ids=sorted(place_set(place_ids)), universe_id=universe_id),
        witness_instances=len(witnesses),
        payload_witnesses=payloads,
        scope='bounded held-out structural and stable RSB1 witnesses; not execution/coverage equivalence',
        assumptions=[
            'x64 vtable at zero',
            'descriptor name pointer at +8',
            'MSVC string ABI',
            'uniform Instance fields',
            'ptr/shared_ptr children vector',
        ],
        bounds=dict(
            instance_header_bytes=HEADER,
            datamodel_bytes=ROOT_BYTES,
            script_bytes=SCRIPT_BYTES,
            container_bytes=CONTAINER_BYTES,
        ),
    )


def cache_path(directory, digest):
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('invalid image hash')
    return Path(directory) / (digest + '.json')


def load_cache(directory, image):
    path = cache_path(directory, image['sha256'])
    if not path.is_file():
        return None, 'miss'
    try:
        if path.is_symlink() or path.stat().st_size > 128 * 1024:
            raise ValueError('unsafe cache file')
        value = json.loads(path.read_text(encoding='utf-8'))
        body = value['body']
        encoded = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
        if hashlib.sha256(encoded).hexdigest() != value['sha256']:
            raise ValueError('checksum mismatch')
        if (
            body['algorithm'] != ALGORITHM
            or body['player_sha256'] != image['sha256']
            or body['pe_machine'] != image['pe_machine']
        ):
            raise ValueError('cache identity mismatch')
        if body['validation_passed'] is not True:
            raise ValueError('unvalidated cache')
        layout = body['profile']['layout']
        required = {
            'self',
            'class_descriptor',
            'name',
            'parent',
            'place_id',
            'universe_id',
            'children',
            'child_stride',
            'ModuleScript_buffer',
            'LocalScript_buffer',
            'container_string',
        }
        if set(layout) != required or any(type(v) is not int or not 0 <= v < 0x1000 or v % 8 for v in layout.values()):
            raise ValueError('invalid layout schema')
        if layout['child_stride'] not in (8, 16):
            raise ValueError('invalid vector stride')
        profile = body['profile']
        if type(profile.get('descriptor_vtable_rva')) is not int or not 0 <= profile['descriptor_vtable_rva'] < 2**31:
            raise ValueError('invalid metadata vtable RVA')
        descriptors = profile.get('class_descriptor_rvas')
        if not isinstance(descriptors, dict) or not {'DataModel', *CLASSES} <= set(descriptors):
            raise ValueError('missing class descriptors')
        if any(
            not isinstance(k, str) or not k or len(k) > 128 or type(v) is not int or not 0 <= v < 2**31
            for k, v in descriptors.items()
        ):
            raise ValueError('invalid descriptor RVAs')
        return body['profile'], 'candidate_requires_live_revalidation'
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return None, 'rejected: ' + str(exc)


def store_cache(directory, image, profile, proof):
    if proof.get('passed') is not True or proof.get('algorithm') != ALGORITHM:
        raise ValueError('cannot cache unvalidated structure')
    # Persist RVAs and field offsets only, never process/instance absolute pointers.
    kept = {k: copy.deepcopy(profile[k]) for k in ('id', 'layout', 'descriptor_vtable_rva', 'class_descriptor_rvas')}
    body = dict(
        algorithm=ALGORITHM,
        player_sha256=image['sha256'],
        pe_machine=image['pe_machine'],
        validation_passed=True,
        profile=kept,
    )
    encoded = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    path = cache_path(directory, image['sha256'])
    path.parent.mkdir(parents=True, exist_ok=True)
    import os, tempfile

    handle, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(dict(body=body, sha256=hashlib.sha256(encoded).hexdigest()), stream, indent=2)
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    return str(path)
