import copy
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_collector import revalidation_reason, observe_path, capture_buffers
import windows_collector as collector


class RevalidationTests(unittest.TestCase):
    def setUp(self):
        self.script = {"address": 100, "class": "ModuleScript", "name": "A", "path": ["Game", "A"], "parent_chain": [200, 100], "vtable": 300}
        self.observation = {"address": 100, "read_bytes": 128, "self_pointer": 100, "class": "ModuleScript", "name": "A", "vtable": 300,
                            "chain": [{"address": 100, "name": "A"}, {"address": 200, "name": "Game"}]}

    def reason(self, **changes):
        return revalidation_reason(dict(copy.deepcopy(self.observation), **changes), self.script, 200)

    def test_stable_identity_is_accepted(self):
        self.assertIsNone(self.reason())

    def test_unreadable_is_not_reported_as_proven_destruction(self):
        self.assertEqual(self.reason(read_bytes=0), "node_unreadable")

    def test_changed_self_and_vtable_are_identity_failures(self):
        self.assertEqual(self.reason(self_pointer=999), "object_destroyed_or_replaced")
        self.assertEqual(self.reason(vtable=999), "object_destroyed_or_replaced")

    def test_class_and_name_changes_are_separate(self):
        self.assertEqual(self.reason(**{"class": "LocalScript"}), "class_changed")
        self.assertEqual(self.reason(name="B"), "name_changed")

    def test_parent_failure_and_wrong_model_are_separate(self):
        self.assertEqual(self.reason(chain=None), "parent_chain_invalid")
        self.assertEqual(self.reason(chain=[{"address": 100, "name": "A"}, {"address": 201, "name": "Game"}]), "wrong_datamodel")
        self.assertEqual(self.reason(chain=[{"address": 100, "name": "A"}, {"address": 150, "name": "Folder"}, {"address": 200, "name": "Game"}]), "parent_chain_invalid")

    def test_ancestor_rename_rejects_code_association(self):
        self.assertEqual(self.reason(chain=[{"address": 100, "name": "A"}, {"address": 200, "name": "Renamed"}]), "name_changed")

    def simulated_path(self):
        layout = {"self": 8, "class_descriptor": 24, "name": 120, "parent": 80}
        def node(address, descriptor, name, parent):
            data = bytearray(128)
            for offset, value in [(0, 0x90000), (8, address), (24, descriptor), (120, name), (80, parent)]:
                struct.pack_into('<Q', data, offset, value)
            return bytes(data)
        def name(value):
            data = value.encode()
            return data.ljust(16, b'\0') + struct.pack('<QQ', len(data), 15)
        memory = {0x10000: node(0x10000, 0x30000, 0x50000, 0x20000),
                  0x20000: node(0x20000, 0x40000, 0x60000, 0),
                  0x30000: struct.pack('<QQQQ', 0x70000, 0x80000, 0, 0),
                  0x40000: struct.pack('<QQQQ', 0x70000, 0xa0000, 0, 0),
                  0x50000: name('A'), 0x60000: name('Game'), 0x80000: name('ModuleScript'), 0xa0000: name('DataModel')}
        class Reader:
            def __init__(self): self.calls = []
            def read(self, address, count):
                self.calls.append((address, count))
                return memory.get(address, b'')[:count]
        return Reader(), layout, memory, name

    def test_snapshot_records_validation_reads_without_rereading_target(self):
        reader, layout, _, _ = self.simulated_path()
        snapshot = observe_path(reader, 0x70000, layout, 0x10000)
        self.assertEqual(snapshot['class'], 'ModuleScript')
        self.assertEqual(snapshot['name'], 'A')
        self.assertEqual(snapshot['parent_address'], 0x20000)
        self.assertEqual(reader.calls.count((0x10000, 128)), 1)
        self.assertIn('T', snapshot['observed_at'])

    def test_changed_identity_cannot_receive_a_script_buffer(self):
        reader, layout, memory, name = self.simulated_path()
        memory[0x50000] = name('Renamed')
        script = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'path': ['Game', 'A'],
                  'parent_chain': [0x20000, 0x10000], 'service': 'Workspace', 'parent_address': 0x20000}
        inventory = {'scripts': [script], 'target_datamodel': 0x20000, 'nodes': [], 'place_id': 1, 'universe_id': 2}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'evidence').mkdir()
            _, report = capture_buffers(reader, inventory, 0x70000, {'layout': layout, 'id': 'simulation'}, out, {'pid': 1})
            self.assertEqual(report['scripts'][0]['revalidation_reason'], 'name_changed')
            self.assertEqual(report['scripts'][0]['status'], 'unavailable')
            self.assertEqual(report['unique_original_buffers'], 0)
            self.assertFalse(any(count == 8 for _, count in reader.calls))

    def test_collect_reads_place_again_after_the_buffer_stage(self):
        for scenario in ("stable", "changed", "short_read", "two_roots"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                reader, layout, memory, _ = self.simulated_path()
                reader.close = Mock()
                reader.stats = {}
                layout.update(place_id=128, universe_id=136)
                memory[0x20000] += struct.pack("<QQ", 11, 21)
                profile = {"id": "simulation", "layout": layout, "descriptor_vtable_rva": 0x60000,
                           "class_descriptor_rvas": {"DataModel": 0x30000}}
                root = {"address": 0x20000, "class": "DataModel", "vtable": 0x90000}
                candidates = {0x10000: dict(self.script, address=0x10000), 0x20000: root}
                after_candidates = {0x20000: root}
                out = Path(tmp)
                (out / "evidence").mkdir()

                def read_buffers(*args):
                    if scenario == "changed":
                        memory[0x20000] = memory[0x20000][:128] + struct.pack("<QQ", 12, 21)
                    elif scenario == "short_read":
                        memory[0x20000] = memory[0x20000][:132]
                    elif scenario == "two_roots":
                        replacement = bytearray(memory[0x20000])
                        struct.pack_into("<Q", replacement, layout["self"], 0x21000)
                        struct.pack_into("<Q", replacement, layout["place_id"], 12)
                        memory[0x21000] = bytes(replacement)
                        after_candidates[0x21000] = dict(root, address=0x21000)
                    return None, {"collection_result": {"status": "pass", "integrity_valid_instances": 1}}

                def scan(*args, data_models_only=False):
                    return (after_candidates if data_models_only else candidates), 0x70000, {}

                read_access = {"matches_expected": True}
                exe = "synthetic-player.exe"
                replacements = {
                    "fingerprint": Mock(return_value={"sha256": "simulation"}),
                    "choose_profile": Mock(return_value=profile),
                    "kernel": Mock(),
                    "owned_control": Mock(return_value={"memory_read": read_access}),
                    "player_probe": Mock(return_value={"image_path": exe, "memory_read": read_access,
                                                       "main_module": {"base_address": "0x10000", "image_size": 0x80000}}),
                    "Reader": Mock(return_value=reader),
                    "load_cache": Mock(return_value=(profile, "simulation")),
                    "resolve_metadata": Mock(return_value=profile),
                    "scan": Mock(side_effect=scan),
                    "validate_layout": Mock(return_value={"passed": True}),
                    "store_cache": Mock(return_value=None),
                    "capture_buffers": Mock(side_effect=read_buffers),
                }
                with patch.multiple(collector, **replacements), \
                        patch("network.get", return_value=({"status": 200}, b'{"universeId":21}')):
                    if scenario == "stable":
                        _, report = collector.collect(1, exe, profile, 11, out, place_ids=[11, 12])
                        self.assertEqual(report["place_validation"]["status"], "pass")
                    else:
                        with self.assertRaises(collector.CollectionError) as error:
                            collector.collect(1, exe, profile, 11, out, place_ids=[11, 12])
                        self.assertEqual(error.exception.stage, "target_identity")
                evidence = json.loads((out / "evidence/place-validation.json").read_text(encoding="utf-8"))
                self.assertEqual(evidence["before"]["read_bytes"], 144)
                self.assertEqual(evidence["before"]["place_id"], 11)
                self.assertEqual(evidence["status"], "pass" if scenario == "stable" else "failed")
                self.assertEqual(reader.calls.count((0x20000, 144)), 2)
                stored = json.loads((out / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["collection_result"]["status"], evidence["status"])
                if scenario == "changed":
                    self.assertEqual(evidence["after"]["place_id"], 12)
                elif scenario == "short_read":
                    self.assertEqual(evidence["error"]["models"][0]["read_bytes"], 132)
                elif scenario == "two_roots":
                    self.assertEqual({m["place_id"] for m in evidence["error"]["models"]}, {11, 12})
                replacements["capture_buffers"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
