"""Portable input handling and declared tool-integrity regression controls."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rcr
from formats import parse_xml
from pipeline import extract, safe_component, validate_repository


class PortabilityTests(unittest.TestCase):
    def test_cli_accepts_utf8_bom_tool_configuration_and_non_ascii_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'\uC785\uB825.luau'; source.write_text('return 1', encoding='utf-8')
            tools = root/'tools.json'
            tools.write_text(json.dumps({'compiler': {'argv': [sys.executable],
                'sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()}}), encoding='utf-8-sig')
            with patch.object(sys, 'argv', ['rcr','recover',str(source),'--tools',str(tools),'--out',str(root/'out')]), \
                 patch('recovery.tools_config', return_value={'compiler': {'argv': [sys.executable]}}), patch('pipeline.compile_check', return_value={'status':'syntax_pass'}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rcr.main(), 0)

    def test_offline_import_rejects_a_changed_declared_tool_before_running_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'input.luau'; source.write_text('return 1')
            for name in ('compiler', 'decompiler'):
                with self.subTest(tool=name), patch('pipeline.tool_run') as invoke, self.assertRaisesRegex(ValueError, 'hash changed'):
                    extract(source, root/name, {name:{'argv':[sys.executable], 'sha256':'0'*64}})
                invoke.assert_not_called()

    def test_shared_string_reference_ignores_surrounding_xml_whitespace(self):
        source = b'return 1\n'
        digest = base64.b64encode(hashlib.md5(source).digest()).decode()
        xml = ('<roblox version="4"><SharedStrings><SharedString md5="'+digest+'">'+base64.b64encode(source).decode()+
               '</SharedString></SharedStrings><Item class="ModuleScript" referent="m"><Properties>'+
               '<SharedString name="Source">\n '+digest+' \n</SharedString></Properties></Item></roblox>')
        nodes, _ = parse_xml(xml.encode())
        self.assertEqual(nodes[0]['source'], source.decode())

    def test_all_windows_reserved_device_names_are_escaped(self):
        for stem in ['CON','PRN','AUX','NUL']+[p+str(i) for p in ('COM','LPT') for i in range(1,10)]:
            for suffix in ('','.luau'):
                with self.subTest(name=stem+suffix): self.assertTrue(safe_component(stem+suffix).startswith('_'))

    def test_validator_accepts_utf8_bom_manifests_and_rejects_drive_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.dumps({'scripts':[], 'note':'\uAC80\uC99D'}, ensure_ascii=False).encode('utf-8-sig')
            (root/'report.json').write_bytes(raw)
            manifest = {'files':[{'path':'report.json','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}
            (root/'files.json').write_text(json.dumps(manifest), encoding='utf-8-sig')
            self.assertEqual(validate_repository(root)['status'], 'pass')
            manifest['files'][0]['path'] = (root.drive or 'C:')+'report.json'
            (root/'files.json').write_text(json.dumps(manifest), encoding='utf-8-sig')
            self.assertTrue(any('unsafe path' in issue for issue in validate_repository(root)['failures']))
