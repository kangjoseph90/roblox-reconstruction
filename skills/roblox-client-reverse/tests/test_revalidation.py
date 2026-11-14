import copy
from pathlib import Path
import sys
import struct
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_collector import revalidation_reason, observe_path, capture_buffers


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


if __name__ == "__main__":
    unittest.main()
