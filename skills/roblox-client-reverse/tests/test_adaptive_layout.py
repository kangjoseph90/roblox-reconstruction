"""Adversarial, read-only memory fixtures for bounded adaptive layout discovery."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from adaptive_layout import ALGORITHM, LayoutError, discover, load_cache, store_cache, validate
from rsb1 import unwrap


ASSETS = Path(os.environ["RCR_TEST_CAPTURE"]) / "originals" if os.environ.get("RCR_TEST_CAPTURE") else None
PRIVATE_BASE = 0x10000000
IMAGE_BASE = 0x50000000
PLACE_ID = 173921847
UNIVERSE_ID = 892731645


class FakeReadOnlyMemory:
    """Small VirtualQueryEx-shaped reader with no write/process API."""
    def __init__(self):
        self.blocks = {}
        self.private = bytearray(0x100000)
        self.blocks[PRIVATE_BASE] = self.private
        self.image = bytearray(0x10000)
        self.blocks[IMAGE_BASE] = self.image

    def put(self, address, data):
        raw = bytes(data)
        for base, block in self.blocks.items():
            if base <= address and address + len(raw) <= base + len(block):
                block[address - base:address - base + len(raw)] = raw
                return
        raise AssertionError(f"fixture allocation outside mapped regions: {address:#x}")

    def read(self, address, size):
        for base, block in self.blocks.items():
            if base <= address < base + len(block):
                start = address - base
                return bytes(block[start:start + size])
        return b""

    def regions(self):
        return [
            dict(base=IMAGE_BASE, size=len(self.image), state=0x1000, protect=0x20, type=0x1000000),
            dict(base=PRIVATE_BASE, size=len(self.private), state=0x1000, protect=0x04, type=0x20000),
        ]


class AdaptiveFixture:
    """Multiple independently spaced Instances backed by original RSB1 files."""
    def __init__(self, variant=0, *, ambiguous=False):
        self.reader = FakeReadOnlyMemory()
        # Two materially different layouts, deliberately unrelated to historical offsets.
        self.offsets = [
            dict(self=0x18, class_descriptor=0x30, name=0x78, parent=0xA8,
                 children=0xC8, ModuleScript_buffer=0x240, LocalScript_buffer=0x2A8,
                 container_string=0x20),
            dict(self=0x48, class_descriptor=0x68, name=0xB0, parent=0xD8,
                 children=0xF0, ModuleScript_buffer=0x338, LocalScript_buffer=0x3A0,
                 container_string=0x48),
        ][variant]
        self.identity_offsets = ((0x180, 0x188) if variant == 0 else (0x1A0, 0x1C8))
        self.root = 0x10001000
        self.services = [0x10002000, 0x10003000]
        self.scripts = {"ModuleScript": [], "LocalScript": []}
        self.parent_by_script = {}
        self.vtable_rva = 0x100
        self.table = IMAGE_BASE + self.vtable_rva
        self.reader.put(self.table, struct.pack("<Q", IMAGE_BASE + 0x200))

        class_names = ["DataModel", "Workspace", "ReplicatedStorage", "Folder", "ModuleScript", "LocalScript"]
        self.descriptor_rvas = {name: 0x1000 + i * 0x100 for i, name in enumerate(class_names)}
        for name, rva in self.descriptor_rvas.items():
            descriptor = IMAGE_BASE + rva
            self.reader.put(descriptor, struct.pack("<QQ", self.table, self._alloc_string(name)))

        self._instance(self.root, "DataModel", "game", 0)
        self.reader.put(self.root + self.identity_offsets[0], struct.pack("<Q", PLACE_ID))
        self.reader.put(self.root + self.identity_offsets[1], struct.pack("<Q", UNIVERSE_ID))
        self._instance(self.services[0], "Workspace", "Workspace", self.root)
        self._instance(self.services[1], "ReplicatedStorage", "ReplicatedStorage", self.root)

        originals = []
        for file in sorted(ASSETS.glob("*.roblox-buffer")):
            data = file.read_bytes()
            try:
                payload, decoded = unwrap(data)
            except Exception:
                continue
            originals.append((data, hashlib.sha256(payload).hexdigest(), decoded))
        if len(originals) < 4:
            raise AssertionError("expected integrity-valid original regression RSB1 buffers")
        self.original_buffers = [x[0] for x in originals]
        self.profile = dict(
            id="fixture-baseline", descriptor_vtable_rva=self.vtable_rva,
            class_descriptor_rvas=self.descriptor_rvas,
            bytecode_versions=sorted({x[2]["bytecode_version"] for x in originals}),
            type_versions=sorted({x[2]["type_version"] for x in originals}),
        )

        # Six scripts of each class. Their parents are unique; service ancestry is
        # balanced so both the discovery and held-out splits cover two services.
        for cls_index, cls in enumerate(("ModuleScript", "LocalScript")):
            for index in range(6):
                parent = 0x10004000 + (cls_index * 6 + index) * 0x1000
                service = self.services[(index // 2 + cls_index) % 2]
                self._instance(parent, "Folder", f"Container{cls_index}_{index}", service)
                address = 0x10010000 + (cls_index * 6 + index) * 0x1000
                self._instance(address, cls, f"Script{cls_index}_{index}", parent)
                self.scripts[cls].append(address)
                self.parent_by_script[address] = parent
                buffer = originals[(cls_index * 3 + index) % len(originals)][0]
                self._script_buffer(address, cls, buffer)

        # One alternative descriptor field yields a second viable descriptor
        # hypothesis while preserving the exact same object identity witnesses.
        if ambiguous:
            for address, cls in self.all_instances:
                if cls in ("ModuleScript", "LocalScript", "Folder", "Workspace", "ReplicatedStorage", "DataModel"):
                    self.reader.put(address + self.alt_descriptor, struct.pack("<Q", IMAGE_BASE + self.descriptor_rvas[cls]))

    def _alloc_string(self, value):
        address = self.string_cursor if hasattr(self, "string_cursor") else 0x10030000
        self.string_cursor = address + 0x80
        data = value.encode("utf-8")
        storage = bytearray(32)
        if len(data) <= 15:
            storage[:len(data)] = data
            struct.pack_into("<QQ", storage, 16, len(data), 15)
        else:
            blob = address + 0x40
            self.reader.put(blob, data + b"\0")
            struct.pack_into("<QQQQ", storage, 0, blob, 0, len(data), len(data))
        self.reader.put(address, storage)
        return address

    def _instance(self, address, cls, name, parent):
        if not hasattr(self, "all_instances"):
            self.all_instances = []
        self.all_instances.append((address, cls))
        if not hasattr(self, "alt_descriptor"):
            self.alt_descriptor = self.offsets["class_descriptor"] + 8
        data = bytearray(0x600)
        struct.pack_into("<Q", data, 0, self.table)
        struct.pack_into("<Q", data, self.offsets["self"], address)
        descriptor = IMAGE_BASE + self.descriptor_rvas[cls]
        struct.pack_into("<Q", data, self.offsets["class_descriptor"], descriptor)
        struct.pack_into("<Q", data, self.offsets["name"], self._alloc_string(name))
        struct.pack_into("<Q", data, self.offsets["parent"], parent)
        self.reader.put(address, data)

    def _script_buffer(self, address, cls, data):
        field = self.offsets[cls + "_buffer"]
        container = 0x10060000 + (address - 0x10010000) // 0x1000 * 0x100
        blob = 0x10080000 + (address - 0x10010000) // 0x1000 * 0x4000
        self.reader.put(address + field, struct.pack("<Q", container))
        self.reader.put(container + self.offsets["container_string"], struct.pack("<QQQQ", blob, len(data), len(data), len(data)))
        self.reader.put(blob, data)

    def build_children(self, missing_child=None):
        children = {}
        for child, cls in self.all_instances:
            if child == self.root or struct.unpack("<Q", self.reader.read(child + self.offsets["parent"], 8))[0] == 0:
                continue
            parent = struct.unpack("<Q", self.reader.read(child + self.offsets["parent"], 8))[0]
            children.setdefault(parent, []).append(child)
        for parent, addresses in children.items():
            if missing_child in addresses:
                addresses.remove(missing_child)
            vector = 0x10050000 + ((parent >> 12) & 0xFF) * 0x100
            array = vector + 0x40
            raw = b"".join(struct.pack("<Q", child) for child in addresses)
            self.reader.put(array, raw)
            self.reader.put(vector, struct.pack("<QQQ", array, array + len(raw), array + len(raw)))
            self.reader.put(parent + self.offsets["children"], struct.pack("<Q", vector))

    def candidate(self):
        return dict(self.profile, layout=dict(
            self=self.offsets["self"], class_descriptor=self.offsets["class_descriptor"],
            name=self.offsets["name"], parent=self.offsets["parent"],
            children=self.offsets["children"], child_stride=8,
            place_id=self.identity_offsets[0], universe_id=self.identity_offsets[1],
            ModuleScript_buffer=self.offsets["ModuleScript_buffer"],
            LocalScript_buffer=self.offsets["LocalScript_buffer"],
            container_string=self.offsets["container_string"],
        ))

    def add_decoy_datamodel(self):
        address = 0x1001C000
        self._instance(address, "DataModel", "other-game", 0)
        self.reader.put(address + self.identity_offsets[0], struct.pack("<Q", PLACE_ID + 77))
        self.reader.put(address + self.identity_offsets[1], struct.pack("<Q", UNIVERSE_ID + 99))

    def nodes(self):
        return {address: dict(address=address, **{"class": cls}) for address, cls in self.all_instances}


@unittest.skipUnless(ASSETS is not None and ASSETS.is_dir(), "RCR_TEST_CAPTURE must name a capture directory")
class AdaptiveLayoutTests(unittest.TestCase):
    def discover_fixture(self, fixture, *, place=PLACE_ID, universe=UNIVERSE_ID, budget=0x40000):
        fixture.build_children()
        return discover(fixture.reader, fixture.profile, IMAGE_BASE, len(fixture.reader.image),
                        place, universe, len(fixture.reader.private) if budget == 0x40000 else budget,
                        lambda *_: None)

    def test_discovers_two_distinct_layouts_and_validates_held_out_original_payloads(self):
        for variant in (0, 1):
            with self.subTest(variant=variant):
                fixture = AdaptiveFixture(variant)
                candidate, proof, coverage = self.discover_fixture(fixture)
                self.assertTrue(proof["passed"])
                self.assertEqual(proof["algorithm"], ALGORITHM)
                self.assertEqual(proof["witness_instances"], 6)
                self.assertEqual(coverage["private_bytes_requested"], len(fixture.reader.private))
                layout = candidate["layout"]
                self.assertEqual(layout["self"], fixture.offsets["self"])
                self.assertEqual(layout["class_descriptor"], fixture.offsets["class_descriptor"])
                self.assertEqual(layout["name"], fixture.offsets["name"])
                self.assertEqual(layout["parent"], fixture.offsets["parent"])
                self.assertEqual(layout["ModuleScript_buffer"], fixture.offsets["ModuleScript_buffer"])
                self.assertEqual(layout["LocalScript_buffer"], fixture.offsets["LocalScript_buffer"])
                self.assertEqual(layout["container_string"], fixture.offsets["container_string"])
                self.assertEqual((layout["place_id"], layout["universe_id"]), fixture.identity_offsets)
                for cls in ("ModuleScript", "LocalScript"):
                    class_hashes = {item["bytecode_sha256"] for item in proof["payload_witnesses"] if item["script_class"] == cls}
                    self.assertGreaterEqual(len(class_hashes), 2)

    def test_missing_or_corrupt_held_out_identity_payload_and_reciprocity_reject(self):
        for fault in ("parent", "payload", "reciprocity"):
            with self.subTest(fault=fault):
                fixture = AdaptiveFixture()
                held_out = fixture.scripts["ModuleScript"][1]
                fixture.build_children(missing_child=held_out if fault == "reciprocity" else None)
                if fault == "parent":
                    fixture.reader.put(held_out + fixture.offsets["parent"], struct.pack("<Q", 0x10060000))
                elif fault == "payload":
                    field = fixture.offsets["ModuleScript_buffer"]
                    container = struct.unpack("<Q", fixture.reader.read(held_out + field, 8))[0]
                    header = fixture.reader.read(container + fixture.offsets["container_string"], 32)
                    blob = struct.unpack_from("<Q", header)[0]
                    original = fixture.reader.read(blob, struct.unpack_from("<Q", header, 16)[0])
                    fixture.reader.put(blob, original[:-1] + bytes([original[-1] ^ 0x80]))
                with self.assertRaises(LayoutError):
                    discover(fixture.reader, fixture.profile, IMAGE_BASE, len(fixture.reader.image),
                             PLACE_ID, UNIVERSE_ID, 0x40000, lambda *_: None)

    def test_wrong_datamodel_identity_ambiguous_layout_and_budget_exhaustion_reject(self):
        wrong_target = AdaptiveFixture()
        wrong_target.add_decoy_datamodel()
        for fixture, kwargs in (
            (wrong_target, dict(place=PLACE_ID + 77, universe=UNIVERSE_ID + 99)),
            (AdaptiveFixture(), dict(place=PLACE_ID + 1, universe=UNIVERSE_ID)),
            (AdaptiveFixture(ambiguous=True), dict(place=PLACE_ID, universe=UNIVERSE_ID)),
            (AdaptiveFixture(), dict(place=PLACE_ID, universe=UNIVERSE_ID, budget=8)),
        ):
            fixture.build_children()
            with self.assertRaises(LayoutError):
                discover(fixture.reader, fixture.profile, IMAGE_BASE, len(fixture.reader.image),
                         kwargs["place"], kwargs["universe"], kwargs.get("budget", 0x40000), lambda *_: None)

    def test_cache_only_persists_validated_profile_and_never_skips_live_validation(self):
        fixture = AdaptiveFixture(1)
        candidate, proof, _ = self.discover_fixture(fixture)
        image = dict(sha256="a" * 64, pe_machine=34404)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                store_cache(directory, image, candidate, dict(proof, passed=False))
            with self.assertRaises(ValueError):
                store_cache(directory, image, candidate, dict(proof, algorithm="other"))
            path = Path(store_cache(directory, image, candidate, proof))
            body = json.loads(path.read_text(encoding="utf-8"))["body"]
            self.assertEqual(set(body), {"algorithm", "player_sha256", "pe_machine", "validation_passed", "profile"})
            self.assertTrue(body["validation_passed"])
            self.assertNotIn("target_datamodel", json.dumps(body))
            self.assertNotIn(hex(IMAGE_BASE + 0x10001000), json.dumps(body))
            loaded, status = load_cache(directory, image)
            self.assertEqual(status, "candidate_requires_live_revalidation")
            self.assertEqual(loaded, candidate)
            nodes = fixture.nodes()
            self.assertTrue(validate(fixture.reader, loaded, IMAGE_BASE, nodes, PLACE_ID, UNIVERSE_ID)["passed"])
            self.assertEqual(load_cache(directory, dict(image, sha256="b" * 64))[1], "miss")

            # A checksum-valid file under the wrong executable hash cannot confer trust.
            altered = copy.deepcopy(body)
            altered["player_sha256"] = "b" * 64
            encoded = json.dumps(altered, sort_keys=True, separators=(",", ":")).encode()
            path.write_text(json.dumps(dict(body=altered, sha256=hashlib.sha256(encoded).hexdigest())), encoding="utf-8")
            self.assertTrue(load_cache(directory, image)[1].startswith("rejected:"))
            path.write_text("{corrupt", encoding="utf-8")
            self.assertTrue(load_cache(directory, image)[1].startswith("rejected:"))


if __name__ == "__main__":
    unittest.main()
