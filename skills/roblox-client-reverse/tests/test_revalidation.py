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

    def test_vtable_change_during_the_double_read_rejects_the_buffer(self):
        reader, layout, memory, _ = self.simulated_path()
        layout["ModuleScript_buffer"], layout["container_string"] = 200, 0
        memory[0x10000 + 200] = struct.pack("<Q", 0xb0000)
        memory[0xb0000] = struct.pack("<QQQQ", 0xc0000, 0, 16, 16)
        payload = b"\x00" * 16
        real = reader.read
        def read(address, count):
            if address == 0xc0000:
                # The object is replaced in place while its buffer is read: same address, path and buffer.
                memory[0x10000] = struct.pack("<Q", 0x91000) + memory[0x10000][8:]
                return payload
            return real(address, count)
        reader.read = read
        script = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'path': ['Game', 'A'], 'vtable': 0x90000,
                  'parent_chain': [0x20000, 0x10000], 'service': 'Workspace', 'parent_address': 0x20000}
        inventory = {'scripts': [script], 'target_datamodel': 0x20000, 'nodes': [], 'place_id': 1, 'universe_id': 2}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'evidence').mkdir()
            _, report = capture_buffers(reader, inventory, 0x70000, {'layout': layout, 'id': 'simulation'}, out, {'pid': 1})
        self.assertEqual(report['scripts'][0]['status'], 'unavailable')
        self.assertEqual(report['scripts'][0]['revalidation_reason'], 'object_destroyed_or_replaced')

    def test_stable_buffer_is_written_once_and_inventoried_from_the_stored_file(self):
        reader, layout, memory, _ = self.simulated_path()
        layout["ModuleScript_buffer"], layout["container_string"] = 200, 0
        memory[0x10000 + 200] = struct.pack("<Q", 0xb0000)
        memory[0xb0000] = struct.pack("<QQQQ", 0xc0000, 0, 16, 16)
        memory[0xc0000] = bytes(range(16))
        script = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'path': ['Game', 'A'], 'vtable': 0x90000,
                  'parent_chain': [0x20000, 0x10000], 'service': 'Workspace', 'parent_address': 0x20000}
        node = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'parent_address': 0x20000}
        inventory = {'scripts': [script], 'target_datamodel': 0x20000, 'nodes': [node], 'place_id': 1, 'universe_id': 2}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'evidence').mkdir()
            identity = {'pid': 1, 'image': {'sha256': 'x'}}
            dump_path, report = capture_buffers(reader, inventory, 0x70000, {'layout': layout, 'id': 'simulation'}, out, identity)
            dump = json.loads(dump_path.read_text())
        import base64
        self.assertEqual(report['scripts'][0]['status'], 'opaque_buffer')
        self.assertEqual(base64.b64decode(dump['instances'][0]['bytecode_base64']), bytes(range(16)))
        self.assertEqual(dump['instances'][0]['bytecode_format'], 'unknown')

    def test_collect_reads_place_again_after_the_buffer_stage(self):
        expected = {"stable": "pass", "incomplete_scan": "incomplete", "unread_other": "incomplete", "first_incomplete": "incomplete",
                    "first_short_other": "incomplete"}
        for scenario in ("stable", "changed", "short_read", "two_roots", "foreign_root", "incomplete_scan",
                         "unread_other", "second_changed", "first_incomplete", "first_short_other"):
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
                if scenario == "first_short_other":
                    # The first scan reads another DataModel's header only in part, so a root seen in full later
                    # cannot be called new.
                    memory[0x24000] = memory[0x20000][:100]
                    candidates[0x24000] = after_candidates[0x24000] = dict(root, address=0x24000)
                if scenario == "second_changed":
                    # A second, non-catalogue root known before the capture changes its Place in place.
                    other = bytearray(memory[0x20000])
                    struct.pack_into("<Q", other, layout["self"], 0x23000)
                    struct.pack_into("<Q", other, layout["place_id"], 99)
                    memory[0x23000] = bytes(other)
                    candidates[0x23000] = after_candidates[0x23000] = dict(root, address=0x23000)
                out = Path(tmp)
                (out / "evidence").mkdir()

                def read_buffers(*args):
                    if scenario == "changed":
                        memory[0x20000] = memory[0x20000][:128] + struct.pack("<QQ", 12, 21)
                    elif scenario == "short_read":
                        memory[0x20000] = memory[0x20000][:132]
                    elif scenario == "first_short_other":
                        full = bytearray(memory[0x20000])
                        struct.pack_into("<Q", full, layout["self"], 0x24000)
                        struct.pack_into("<Q", full, layout["place_id"], 99)
                        memory[0x24000] = bytes(full)
                    elif scenario == "unread_other":
                        memory[0x22000] = memory[0x20000][:100]  # a second root whose header cannot be read in full
                        after_candidates[0x22000] = dict(root, address=0x22000)
                    elif scenario == "second_changed":
                        changed = bytearray(memory[0x23000])
                        struct.pack_into("<Q", changed, layout["place_id"], 98)
                        memory[0x23000] = bytes(changed)
                    elif scenario in ("two_roots", "foreign_root", "first_incomplete"):
                        # foreign_root: the new root holds a Place outside the catalogue.
                        replacement = bytearray(memory[0x20000])
                        struct.pack_into("<Q", replacement, layout["self"], 0x21000)
                        struct.pack_into("<Q", replacement, layout["place_id"], 12 if scenario == "two_roots" else 99)
                        memory[0x21000] = bytes(replacement)
                        after_candidates[0x21000] = dict(root, address=0x21000)
                    return None, {"collection_result": {"status": "pass", "integrity_valid_instances": 1}}

                def scan(*args, data_models_only=False):
                    coverage = ({"budget_exhausted": True} if (data_models_only and scenario == "incomplete_scan")
                                or (not data_models_only and scenario == "first_incomplete") else {})
                    return (after_candidates if data_models_only else candidates), 0x70000, coverage

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
                    if scenario in expected:
                        _, report = collector.collect(1, exe, profile, 11, out, place_ids=[11, 12])
                        self.assertEqual(report["place_validation"]["status"], expected[scenario])
                    else:
                        with self.assertRaises(collector.CollectionError) as error:
                            collector.collect(1, exe, profile, 11, out, place_ids=[11, 12])
                        self.assertEqual(error.exception.stage, "target_identity")
                evidence = json.loads((out / "evidence/place-validation.json").read_text(encoding="utf-8"))
                self.assertEqual(evidence["before"]["read_bytes"], 144)
                self.assertEqual(evidence["before"]["place_id"], 11)
                self.assertEqual(evidence["status"], expected.get(scenario, "failed"))
                self.assertEqual(reader.calls.count((0x20000, 144)), 2 if scenario != "second_changed" else 2)
                stored = json.loads((out / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["collection_result"]["status"],
                                 {"pass": "pass", "incomplete": "partial"}.get(evidence["status"], "failed"))
                if scenario == "changed":
                    self.assertEqual(evidence["after"]["place_id"], 12)
                elif scenario == "short_read":
                    self.assertEqual(evidence["error"]["models"][0]["read_bytes"], 132)
                elif scenario == "two_roots":
                    self.assertEqual({m["place_id"] for m in evidence["error"]["models"]}, {11, 12})
                elif scenario == "foreign_root":
                    self.assertEqual([m["place_id"] for m in evidence["error"]["models"]], [99])
                replacements["capture_buffers"].assert_called_once()

    def test_interrupted_region_walk_marks_the_scan_incomplete(self):
        reader, layout, memory, name = self.simulated_path()
        memory[0x30000] = struct.pack('<QQQQ', 0x70000, 0xa0000, 0, 0)
        reader.regions = lambda: iter([])
        reader.regions_complete = False
        profile = {"layout": layout, "descriptor_vtable_rva": 0x60000, "class_descriptor_rvas": {"DataModel": 0x20000}}
        _, _, coverage = collector.scan(reader, profile, 0x10000, 0, 1024, lambda *a: None, data_models_only=True)
        self.assertTrue(coverage["region_walk_incomplete"])

    def test_storage_failure_is_not_labelled_decoded(self):
        reader, layout, memory, _ = self.simulated_path()
        layout["ModuleScript_buffer"], layout["container_string"] = 200, 0
        memory[0x10000 + 200] = struct.pack("<Q", 0xb0000)
        memory[0xb0000] = struct.pack("<QQQQ", 0xc0000, 0, 16, 16)
        memory[0xc0000] = bytes(range(16))
        script = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'path': ['Game', 'A'], 'vtable': 0x90000,
                  'parent_chain': [0x20000, 0x10000], 'service': 'Workspace', 'parent_address': 0x20000}
        node = {'address': 0x10000, 'class': 'ModuleScript', 'name': 'A', 'parent_address': 0x20000}
        inventory = {'scripts': [script], 'target_datamodel': 0x20000, 'nodes': [node], 'place_id': 1, 'universe_id': 2}
        profile = {'layout': layout, 'id': 'simulation', 'bytecode_versions': [0], 'type_versions': [1]}
        decoded = (b"\x00\x01payload", {"bytecode_version": 0, "type_version": 1})
        real = Path.write_bytes
        def failing(path, data):
            if path.suffix == ".roblox-luau":
                raise OSError("disk full")
            return real(path, data)
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector, "unwrap", return_value=decoded), \
                patch.object(Path, "write_bytes", failing):
            out = Path(tmp)
            (out / 'evidence').mkdir()
            dump_path, report = capture_buffers(reader, inventory, 0x70000, profile, out, {'pid': 1, 'image': {'sha256': 'x'}})
            dump = json.loads(dump_path.read_text())
        record = report['scripts'][0]
        self.assertEqual((record['status'], record['storage_error']), ('opaque_buffer', 'disk full'))
        self.assertEqual(dump['instances'][0]['bytecode_format'], 'unknown')

    def test_partial_file_from_an_earlier_write_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.bin"
            target.write_bytes(b"abc")  # what a failed earlier write left behind
            import hashlib
            data = bytes(range(16))
            collector.store_once(target, data, hashlib.sha256(data).hexdigest())
            self.assertEqual(target.read_bytes(), data)

    def test_window_class_read_failure_is_reported(self):
        import windows_player
        class FakeUser32:
            def IsWindow(self, hwnd): return 1
            def GetWindowThreadProcessId(self, hwnd, owner): owner._obj.value = 7; return 1
            def GetWindowTextLengthW(self, hwnd): return 0
            def GetWindowTextW(self, *a): return 0
            def GetClassNameW(self, *a): return 0  # failure
            def GetWindowRect(self, *a): return 1
            def IsWindowVisible(self, hwnd): return 1
            def IsIconic(self, hwnd): return 0
            def GetWindow(self, *a): return 0
        session = windows_player.Session.__new__(windows_player.Session)
        session.u, session.identity, session.wait = FakeUser32(), {"pid": 7}, lambda seconds=0: False
        with patch.object(windows_player, "_error", side_effect=lambda op: OSError(op + " failed")), \
                self.assertRaisesRegex(OSError, "GetClassNameW"):
            session.window_info(88)

    def test_window_read_errors_reach_the_player_inventory(self):
        import windows_player
        from player_control import list_players
        class FakeUser32:
            callback_type = staticmethod(lambda function: function)
            def EnumWindows(self, visit, _): visit(88, 0); return 1
            def GetWindowThreadProcessId(self, hwnd, owner): owner._obj.value = 7; return 1
            def IsWindow(self, hwnd): return 1
        session = windows_player.Session.__new__(windows_player.Session)
        session.u, session.identity = FakeUser32(), {"pid": 7, "created_at_ticks": 1}
        session.window_info = Mock(side_effect=OSError("GetClassNameW failed"))
        session.__enter__ = lambda: session
        class Backend(windows_player.WindowsPlayer):
            def require_available(self): pass
            def session(self, pid):
                return session
        session.close = lambda: None
        with patch.object(windows_player, "running_players", return_value=[{"pid": 7}]):
            result = list_players(Backend())
        self.assertEqual(result["status"], "partial")
        self.assertIn("GetClassNameW", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
