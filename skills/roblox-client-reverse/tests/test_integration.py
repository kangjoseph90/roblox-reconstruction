"""Integration invariants without joining or inspecting a user's game process."""
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import struct
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compatibility import profiles, select_profile, choose_profile, runtime
from windows_collector import resolve_metadata, CollectionError
from pipeline import extract, validate_repository
from rsb1 import unwrap, xxh32
from workflow import replay_capture, run_live, ui_confirmation, wait_for_ui

FIXTURE = Path(os.environ["RCR_TEST_CAPTURE"]) if os.environ.get("RCR_TEST_CAPTURE") else None


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_known_profile_lookup_keeps_verified_and_unverified_distinct(self):
        profile = profiles()[0]
        image = {"sha256": profile["player_sha256"], "pe_machine": profile["pe_machine"]}
        self.assertEqual(select_profile(image, "Windows", "AMD64")["id"], profile["id"])
        self.assertIsNone(select_profile(dict(image, sha256="0" * 64), "Windows", "AMD64"))
        self.assertIsNone(select_profile(image, "Darwin", "AMD64"))
        self.assertIsNone(select_profile(image, "Windows", "ARM64"))

    def test_unknown_hash_gets_an_unverified_attempt_profile(self):
        known = profiles()[0]
        image = {"sha256": "0" * 64, "pe_machine": known["pe_machine"]}
        candidate = choose_profile(image, "Windows", "AMD64")
        self.assertEqual(candidate["compatibility_status"], "unverified")
        self.assertEqual(candidate["player_sha256"], image["sha256"])
        self.assertEqual(candidate["baseline_profile"], known["id"])
        self.assertIsNone(choose_profile(image, "Darwin", "AMD64"))
        self.assertEqual(choose_profile(dict(image, sha256=known["player_sha256"]), "Windows", "AMD64")["compatibility_status"], "verified")

    def test_unknown_build_runtime_continues_instead_of_rejecting_version(self):
        image = {"path": "new-player.exe", "sha256": "0" * 64, "pe_machine": 34404}
        with patch("compatibility.platform.system", return_value="Windows"), \
                patch("compatibility.platform.machine", return_value="AMD64"), \
                patch("compatibility.fingerprint", return_value=image):
            result = runtime("new-player.exe")
        self.assertTrue(result["compatible"])
        self.assertEqual(result["compatibility_status"], "unverified")
        self.assertIsNone(result["reason"])

    def test_relocated_descriptors_are_discovered_without_a_known_image_hash(self):
        base, heap, size = 0x100000, 0x800000, 0x3000
        table = base + 0x800
        image = bytearray(size)
        struct.pack_into("<Q", image, 0x800, base + 0x100)
        strings, expected = {}, {}
        for i, name in enumerate(("DataModel", "ModuleScript", "LocalScript")):
            address, pointer = base + 0x1800 + i * 0x100, heap + i * 0x100
            struct.pack_into("<QQQ", image, address - base, table, pointer, 0)
            strings[pointer] = name.encode().ljust(16, b"\0") + struct.pack("<QQ", len(name), 15)
            expected[name] = address - base
        class FakeReader:
            def read(self, address, count):
                if base <= address and address + count <= base + size:
                    return bytes(image[address - base:address - base + count])
                return strings.get(address, b"")[:count]
            def regions(self):
                return iter([{"base": base, "size": size, "state": 0x1000, "protect": 4, "type": 0x1000000}])
        profile = choose_profile({"sha256": "0" * 64, "pe_machine": 34404}, "Windows", "AMD64")
        resolved = resolve_metadata(FakeReader(), profile, base, size)
        self.assertEqual(resolved["class_descriptor_rvas"], expected)
        self.assertEqual(resolved["descriptor_vtable_rva"], table - base)
        self.assertEqual(resolved["metadata_resolution"]["method"], "live_descriptor_discovery")
        self.assertEqual(resolved["compatibility_status"], "unverified")

    def test_actual_unresolvable_metadata_reports_structure_failure(self):
        class EmptyReader:
            def read(self, *args):
                return b""
            def regions(self):
                return iter([])
        profile = choose_profile({"sha256": "0" * 64, "pe_machine": 34404}, "Windows", "AMD64")
        with self.assertRaises(CollectionError) as failed:
            resolve_metadata(EmptyReader(), profile, 0x100000, 0x3000)
        self.assertEqual(failed.exception.stage, "class_metadata")
        self.assertIn("resolved uniquely", failed.exception.detail["reason"])

    def test_known_metadata_keeps_the_validated_fast_path(self):
        profile, base = profiles()[0], 0x100000
        table = base + profile["descriptor_vtable_rva"]
        memory = {}
        for i, (name, rva) in enumerate(profile["class_descriptor_rvas"].items()):
            pointer = 0x800000 + i * 0x100
            memory[base + rva] = struct.pack("<QQQQ", table, pointer, 0, 0)
            memory[pointer] = name.encode().ljust(16, b"\0") + struct.pack("<QQ", len(name), 15)
        class Reader:
            def read(self, address, size):
                return memory.get(address, b"")[:size]
            def regions(self):
                raise AssertionError("matching metadata must not require image rediscovery")
        resolved = resolve_metadata(Reader(), profile, base, 0x10000000)
        self.assertEqual(resolved["class_descriptor_rvas"], profile["class_descriptor_rvas"])
        self.assertEqual(resolved["metadata_resolution"]["method"], "candidate_RVAs_validated")

    def test_computer_use_evidence_must_be_fresh_and_for_requested_target(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=1)
        value = {"observed_at": datetime.now(timezone.utc).isoformat(), "place_id": 123,
                 "game_scene_visible": True, "window": {"id": 5, "app": "process:C:\\RobloxPlayerBeta.exe"}}
        self.assertTrue(ui_confirmation(value, 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, place_id=456), 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, game_scene_visible=False), 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, observed_at=(started - timedelta(seconds=10)).isoformat()), 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, window={"id": 5, "app": "browser.exe"}), 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, window='invalid shape'), 123, started.isoformat()))
        self.assertFalse(ui_confirmation(dict(value, observed_at=123), 123, started.isoformat()))

    def test_ui_rejection_is_reported_once_and_a_corrected_file_is_accepted(self):
        started = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        valid = {"observed_at": datetime.now(timezone.utc).isoformat(), "place_id": 123,
                 "game_scene_visible": True, "window": {"id": 5, "app": "RobloxPlayerBeta.exe"}}
        invalid = dict(valid, window={"id": 5, "app": "Roblox"})
        path = self.root / 'ui-confirmed.json'
        path.write_text('{}')
        log = io.StringIO()
        with patch.object(Path, 'read_text', side_effect=[json.dumps(invalid), json.dumps(invalid), json.dumps(valid)]), \
             patch('workflow.time.sleep'), contextlib.redirect_stderr(log):
            observed = wait_for_ui(path, 123, started, 5)
        self.assertEqual(observed, valid)
        events = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['reason'], 'window_app_mismatch')

    def test_ui_timeout_keeps_the_rejection_reason(self):
        path = self.root / 'ui-confirmed.json'
        path.write_text('{invalid JSON', encoding='utf-8')
        with patch('workflow.time.monotonic', side_effect=[0, 0, 0, 2]), patch('workflow.time.sleep'), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(TimeoutError, 'last rejection: invalid_json'):
            wait_for_ui(path, 123, datetime.now(timezone.utc).isoformat(), 1)

    @unittest.skipUnless(FIXTURE is not None and (FIXTURE / "capture-report.json").is_file(), "RCR_TEST_CAPTURE must name a capture directory")
    def test_real_native_envelope_matches_recorded_payload_and_rejects_corruption(self):
        record = next(r for r in json.loads((FIXTURE / "capture-report.json").read_text())["scripts"] if "raw_sha256" in r)
        raw = (FIXTURE / "originals" / (record["raw_sha256"] + ".roblox-buffer")).read_bytes()
        payload, info = unwrap(raw)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), record["bytecode_sha256"])
        self.assertTrue(info["xxh32_seed42_verified"])
        changed = bytearray(raw)
        changed[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            unwrap(bytes(changed))

    def test_roblox_never_falls_back_to_standard_opcode_adapter(self):
        data = {"schema": "rcr-dump/v1", "instances": [{"id": "a", "class": "ModuleScript", "name": "A",
                "bytecode_base64": base64.b64encode(b"\x0e\x03binary").decode(), "bytecode_format": "roblox"}]}
        path = self.root / "dump.json"
        path.write_text(json.dumps(data))
        tools = {"decompiler": {"argv": ["standard-only", "{input}"], "bytecode_versions": [14]}}
        with patch("pipeline.tool_run", side_effect=AssertionError("must not call a standard-only adapter")):
            result = extract(path, self.root / "out", tools)
        self.assertEqual(result["scripts"][0]["recovery"], "unsupported_bytecode_encoding")
        self.assertEqual(result["summary"]["source_files"], 0)

    def test_duplicate_code_retains_separate_paths_and_one_tool_invocation(self):
        payload = base64.b64encode(b"\x0e\x03binary").decode()
        path = self.root / "dump.json"
        path.write_text(json.dumps({"schema": "rcr-dump/v1", "instances": [
            {"id": name, "class": "ModuleScript", "name": name, "bytecode_base64": payload, "bytecode_format": "roblox"}
            for name in ("One", "Two")]}))
        tools = {"decompiler": {"argv": ["standard"], "roblox_argv": ["roblox"], "bytecode_versions": [14], "type_versions": [3]},
                 "compiler": {"argv": ["compiler"]}}
        with patch("pipeline.tool_run", return_value=({"status": "ok", "exit_code": 0}, b"return 42\n")) as decompiler, \
                patch("pipeline.compile_check", return_value={"status": "syntax_pass", "semantic_equivalence": "not_tested"}) as compiler:
            result = extract(path, self.root / "out", tools)
        self.assertEqual(decompiler.call_count, 1)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual({tuple(s["instance_path"]) for s in result["scripts"]}, {("One",), ("Two",)})
        self.assertEqual(len({s["source"] for s in result["scripts"]}), 2)
        self.assertEqual(result["summary"]["duplicate_bytecode_instances"], 1)
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")

    def test_missing_ui_stops_before_native_collection_and_preserves_failure_report(self):
        with patch("workflow.runtime", return_value={"compatible": True, "image": {"path": "player.exe"}, "profile": {}}), \
                patch("workflow.tools_config", return_value={}), patch("workflow.os.startfile", create=True), \
                patch("workflow.wait_for_ui", side_effect=TimeoutError("no UI observation")), \
                patch("windows_collector.collect", side_effect=AssertionError("must not inspect Player without UI")):
            result = run_live("123", self.root / "out")
        self.assertEqual(result["summary"]["failure_stage"], "awaiting_computer_use")
        self.assertFalse(result["new_live_capture"])
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")

    def authored_capture(self):
        """A synthetic envelope for replay tests, not Player capture evidence."""
        import zstandard
        payload = b'\x0e\x03authored replay fixture, not executable Luau'
        envelope = b'RSB1' + struct.pack('<I', len(payload)) + zstandard.ZstdCompressor().compress(payload)
        key = xxh32(envelope).to_bytes(4, 'little')
        encoded = bytes(value ^ ((key[i % 4] + i * 41) & 255) for i, value in enumerate(envelope))
        digest = hashlib.sha256(encoded).hexdigest()
        source = self.root / 'capture'
        (source / 'originals').mkdir(parents=True)
        (source / 'originals' / (digest + '.roblox-buffer')).write_bytes(encoded)
        capture = {'scripts': [
            {'address': 0x100, 'raw_sha256': digest, 'bytecode_sha256': hashlib.sha256(payload).hexdigest()},
            {'address': 0x200, 'error': 'unavailable in authored snapshot'}],
            'status_counts': {'roblox_bytecode_captured': 1, 'unavailable': 1},
            'note': 'Synthetic fixture: \uac80\uc99d'}
        capture_raw = (json.dumps(capture, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        (source / 'capture-report.json').write_bytes(capture_raw)
        (source / 'client-dump.json').write_text(json.dumps({'schema': 'rcr-dump/v1', 'instances': [
            {'id': hex(address), 'class': 'ModuleScript', 'name': name}
            for address, name in [(0x100, 'Captured'), (0x200, 'Unavailable')]]}), encoding='utf-8')
        return source, capture_raw, digest, payload

    def test_replay_failure_after_output_creation_writes_a_terminal_report(self):
        source, _, _, _ = self.authored_capture()
        out = self.root / 'out'
        with patch('workflow.tools_config', return_value={}), \
                patch('workflow.restore', side_effect=ValueError('repository integrity failed')):
            result = replay_capture(source, out)
        self.assertEqual(result['summary']['status'], 'failed')
        self.assertEqual(result['summary']['failure_stage'], 'restore')
        saved = json.loads((out / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['errors'][0]['error'], 'repository integrity failed')
        self.assertTrue((out / 'evidence/capture.json').is_file())
        self.assertEqual(validate_repository(out)['status'], 'pass')
        # The CLI maps this terminal failure to exit 2, not an invocation error.
        import rcr
        with patch('workflow.tools_config', return_value={}), \
                patch('workflow.restore', side_effect=ValueError('repository integrity failed')), \
                patch.object(sys, 'argv', ['rcr.py', 'replay', str(source), '--out', str(self.root / 'cli-out')]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rcr.main(), 2)

    def test_replay_input_errors_do_not_create_output(self):
        out = self.root / 'out'
        with patch('workflow.tools_config', return_value={}), self.assertRaises(OSError):
            replay_capture(self.root / 'missing-capture', out)
        self.assertFalse(out.exists())

    def test_malformed_raw_hash_is_a_per_buffer_error(self):
        source, capture_raw, _, _ = self.authored_capture()
        capture = json.loads(capture_raw)
        capture['scripts'].append({'address': 0x300, 'raw_sha256': '../escape'})
        (source / 'capture-report.json').write_text(json.dumps(capture), encoding='utf-8')
        dump = json.loads((source / 'client-dump.json').read_text(encoding='utf-8'))
        dump['instances'].append({'id': hex(0x300), 'class': 'ModuleScript', 'name': 'Escape'})
        (source / 'client-dump.json').write_text(json.dumps(dump), encoding='utf-8')
        with patch('workflow.tools_config', return_value={}):
            result = replay_capture(source, self.root / 'out')
        self.assertEqual([e['error'] for e in result['replay_errors']], ['invalid raw content hash'])
        self.assertNotEqual(result['summary'].get('failure_stage'), 'replay')

    def test_replay_output_can_be_replayed_without_the_original_input(self):
        source, capture_raw, digest, payload = self.authored_capture()
        first, second = self.root / 'first', self.root / 'second'
        with patch('workflow.tools_config', return_value={}):
            replay_capture(source, first)
            source.rename(self.root / 'original-offline')
            self.assertFalse(source.exists())
            result = replay_capture(first, second)
        self.assertEqual(result['replay_errors'], [])
        self.assertFalse(result['new_live_capture'])
        for output in (first, second):
            self.assertEqual((output / 'evidence/capture.json').read_bytes(), capture_raw)
            encoded = (output / 'capture/originals' / (digest + '.roblox-buffer')).read_bytes()
            self.assertEqual(unwrap(encoded)[0], payload)
            report = json.loads((output / 'repository/report.json').read_text(encoding='utf-8'))
            captured = next(s for s in report['scripts'] if s['id'] == '0x100')
            self.assertEqual((output / 'repository' / captured['bytecode']['path']).read_bytes(), payload)
            self.assertEqual(validate_repository(output)['status'], 'pass')

    def test_chained_replay_still_rejects_a_tampered_original_buffer(self):
        source, capture_raw, digest, _ = self.authored_capture()
        first = self.root / 'first'
        with patch('workflow.tools_config', return_value={}):
            replay_capture(source, first)
            raw = first / 'capture/originals' / (digest + '.roblox-buffer')
            raw.write_bytes(raw.read_bytes() + b'tampered')
            result = replay_capture(first, self.root / 'second')
        self.assertEqual(len(result['replay_errors']), 1)
        self.assertIn('hash mismatch', result['replay_errors'][0]['error'])
        self.assertEqual(result['summary']['source_files'], 0)
        self.assertEqual((self.root / 'second/evidence/capture.json').read_bytes(), capture_raw)

    def test_replay_only_recovers_code_from_verified_original_buffers(self):
        source, _, _, payload = self.authored_capture()
        path = source / 'client-dump.json'
        dump = json.loads(path.read_text(encoding='utf-8'))
        dump['instances'].append({'id':'0x300', 'class':'LocalScript', 'name':'Unrecorded'})
        for node in dump['instances']:
            node.update(source='return "injected source"', bytecode=[14, 3],
                        bytecode_base64=base64.b64encode(payload).decode(), bytecode_format='roblox')
        path.write_text(json.dumps(dump), encoding='utf-8')
        with patch('workflow.tools_config', return_value={}):
            result = replay_capture(source, self.root / 'out')
        self.assertEqual(result['summary']['source_files'], 0)
        report = json.loads((self.root / 'out/repository/report.json').read_text(encoding='utf-8'))
        scripts = {s['id']:s for s in report['scripts']}
        self.assertEqual(scripts['0x100']['bytecode']['sha256'], hashlib.sha256(payload).hexdigest())
        for ident in ('0x200', '0x300'):
            self.assertNotIn('source', scripts[ident])
            self.assertNotIn('bytecode', scripts[ident])
            self.assertTrue(scripts[ident]['capture_errors'])

    @unittest.skipUnless(FIXTURE is not None and (FIXTURE / "capture-report.json").is_file(), "RCR_TEST_CAPTURE must name a capture directory")
    def test_replay_tampering_cannot_be_hidden_by_valid_decoded_bytes(self):
        record = next(r for r in json.loads((FIXTURE / "capture-report.json").read_text())["scripts"] if "raw_sha256" in r)
        source = self.root / "input"
        (source / "originals").mkdir(parents=True)
        raw = FIXTURE / "originals" / (record["raw_sha256"] + ".roblox-buffer")
        (source / "originals" / raw.name).write_bytes(raw.read_bytes() + b"tampered")
        (source / "capture-report.json").write_text(json.dumps({"scripts": [record], "status_counts": {}}))
        (source / "client-dump.json").write_text(json.dumps({"schema": "rcr-dump/v1", "instances": [
            {"id": hex(record["address"]), "class": "ModuleScript", "name": "Actual", "bytecode_format": "roblox",
             "bytecode_base64": base64.b64encode(b"\x0e\x03deceptively-present").decode()}]}))
        with patch("workflow.tools_config", return_value={}), patch("pipeline.tool_run", side_effect=AssertionError("corrupt capture must not reach tools")):
            result = replay_capture(source, self.root / "out")
        self.assertEqual(len(result["replay_errors"]), 1)
        self.assertEqual(result["summary"]["source_files"], 0)
        self.assertIn("hash mismatch", result["replay_errors"][0]["error"])
        self.assertEqual(validate_repository(self.root / "out")["status"], "pass")


if __name__ == "__main__":
    unittest.main()
