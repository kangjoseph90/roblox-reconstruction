#!/usr/bin/env python3
"""Generate owned fixtures. rbx-util is an independent optional binary writer."""
import argparse
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import json


def fixture(out, theme):
    root = ET.Element("roblox", version="4")
    def add(parent, cls, name, ref, source=None, context=None):
        el = ET.SubElement(parent, "Item", {"class": cls, "referent": ref})
        props = ET.SubElement(el, "Properties")
        ET.SubElement(props, "string", name="Name").text = name
        if source is not None:
            ET.SubElement(props, "ProtectedString", name="Source").text = source
        if context is not None:
            ET.SubElement(props, "token", name="RunContext").text = str(context)
        return el
    storage = add(root, "ReplicatedStorage", "ReplicatedStorage", "r1")
    add(storage, "ModuleScript", theme + "Core", "r2", 'local M = {}\nfunction M.sum(a: number, b: number): number\n    return a + b\nend\nreturn M\n')
    add(storage, "Script", "ClientBoot", "r3", 'local M = require(script.Parent.' + theme + 'Core)\nprint(M.sum(20, 22))\n', 2)
    add(storage, "ModuleScript", "Duplicate", "r4", 'return {label = "first"}\n')
    add(storage, "ModuleScript", "Duplicate", "r5", 'return {label = "second"}\n')
    # A Folder named ServerStorage must not be mistaken for the server service.
    fake = add(storage, "Folder", "ServerStorage", "r6")
    add(fake, "ModuleScript", "../CON/<script>", "r7", 'return "safe path ✓"\n')
    starter = add(root, "StarterPlayer", "StarterPlayer", "r8")
    scripts = add(starter, "StarterPlayerScripts", "StarterPlayerScripts", "r9")
    add(scripts, "LocalScript", "Input", "r10", 'local hits = 0\nfor i = 1, 4 do hits += i end\nprint(hits)\n')
    add(storage, "ModuleScript", "Empty", "r11", "")
    add(storage, "ModuleScript", "Missing", "r12")
    server = add(root, "ServerStorage", "ServerStorage", "r13")
    add(server, "ModuleScript", "Private", "r14", 'return "server-private"\n')
    server_scripts = add(root, "ServerScriptService", "ServerScriptService", "r15")
    add(server_scripts, "Script", "ServerMain", "r16", 'print("server only")\n')
    ET.SubElement(storage.find("Properties"), "BinaryString", name="AttributesSerialize").text = ""
    path = out / (theme + ".rbxlx")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--rbx-util")
    a = parser.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ["Workshop", "Signals"]:
        source = fixture(out, name)
        if a.rbx_util:
            subprocess.run([a.rbx_util, "convert", str(source), str(source.with_suffix(".rbxl"))], check=True)
    dump = {"schema": "rcr-dump/v1", "capture": {"mode": "synthetic-metadata-only", "completeness": "partial"},
        "instances": [{"id": "root", "class": "ReplicatedStorage", "name": "ReplicatedStorage"},
            {"id": "script", "parent": "root", "class": "LocalScript", "name": "LateBound", "metadata": {"source_error": "not available"}}]}
    (out / "metadata-only.json").write_text(json.dumps(dump), encoding="utf-8")


if __name__ == "__main__":
    main()
