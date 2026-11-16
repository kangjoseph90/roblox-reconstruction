# Artifact and adapter contracts

`rcr.py inspect INPUT` identifies one artifact. `extract INPUT --out OUTPUT` accepts one file or recursively inventories a directory. Symlinks are skipped. Originals include unrecognized regular files, which are not interpreted as source. Limits: 128 MiB per file and expanded binary, 512 MiB across inputs, 200,000 instances per artifact, 256 hierarchy levels. Output must be new/empty and outside the input directory.

## Supported inputs

| Input | Decoder | Important limits |
|---|---|---|
| `.rbxl`, `.rbxm` | Roblox binary header/version 0, INST/PROP/PRNT/SSTR, LZ4/ZSTD | Script-focused property decoder; geometry/other unknown property types inventoried, not decoded. Type `0x1d` bytes preserved. |
| `.rbxlx`, `.rbxmx` | XML version 4 | Source ProtectedString/string/SharedString, hierarchy, all typed property XML. DTD/entities rejected. XML parser normalizes XML line endings; original bytes preserved. |
| `.lua`, `.luau` | UTF-8 source | Class inferred as ModuleScript for inventory; original instance identity and game authenticity unknown. |
| `.bc`, `.luac`, `.bytecode` | Opaque bytes | Requires an explicit `luau` or `roblox` encoding and a matching adapter before decompiler use. Header/version candidate is not complete format validation. |
| `.json` | `rcr-dump/v1` | Explicit self-contained schema below. Arbitrary JSON or executor-specific dump formats need a separate documented conversion. No embedded filesystem paths are opened. |

Binary property types decoded for metadata: byte strings, booleans, Int32, enums, references, SharedStrings. AttributesSerialize, tags and package-related bytes may remain serialized rather than semantically decoded. All raw data remains available. A failed artifact does not stop independent inputs.

## JSON dump

```json
{
  "schema": "rcr-dump/v1",
  "capture": {"mode": "authorized-client-snapshot", "completeness": "partial"},
  "instances": [
    {"id": "1", "parent": null, "class": "ReplicatedStorage", "name": "ReplicatedStorage"},
    {"id": "2", "parent": "1", "class": "ModuleScript", "name": "Example",
     "source": "return 42\n", "metadata": {"tags": ["Demo"]}}
  ]
}
```

IDs/classes/names are strings. Parent is absent/null or an existing ID. Duplicate IDs, cycles and dangling parents fail. For bytecode, use `bytecode_base64` and an established `bytecode_format` (`luau`, `roblox`, or `unknown`). Roblox means a decoded serialized payload with Roblox opcodes, not an RSB1 wrapper and not vanilla encoding. Absent source means unavailable; an empty string means a supplied empty Source value. Imported capture fields remain provenance claims; the live workflow separately records its actual API evidence. See [dump.schema.json](dump.schema.json).

## Trusted local tool configuration

```json
{
  "compiler": {
    "argv": ["/absolute/path/luau-compile", "--binary", "{input}"],
    "revision": "pinned-upstream-commit", "timeout": 30
  },
  "decompiler": {
    "argv": ["/absolute/path/luaudec", "--standard", "{input}"],
    "revision": "pinned-upstream-commit", "bytecode_versions": [14],
    "type_versions": [3], "output": "stdout", "timeout": 30
  }
}
```

For Roblox input, provide `decompiler.roblox_argv` with the explicit `--roblox` mode. A standard-only adapter is rejected. Setup configurations record executable `sha256`. All recovery paths enforce declared compiler/decompiler hashes before use; live/replay additionally require both tools. Offline custom adapters may omit hashes, so those configurations remain caller-trusted. For tools writing a file, put `{output}` in argv and set `output: "file"`. No shell expansion is used. Placeholders replace literal substrings in each argument. The adapter checks exit status, UTF-8 output and configured version gates, preserves stdout, then compiles the output. Configuration is executable trust: use a locally reviewed file, not one embedded in an untrusted dump. Timeout is capped at 120 seconds. `rsb1.py` verifies the captured wrapper's XXH32, bounded Zstd output and payload header; arbitrary unknown wrappers stay opaque.

## Repository/report

- `originals/`: exact supplied artifacts; SHA-256 and byte count in `report.json`.
- `instances.json`: class/name/parent, original paths, capture metadata, serialized metadata where available.
- `src/`: escaped paths plus unique identity suffixes, normalized `init.luau`, original bytecode and decompiler stdout. Full logical paths survive in report even when on-disk paths are shortened.
- `report.json`: `rcr-report/v1`, operation, coverage, artifacts, individual scripts, validation statuses, acquisition methods and summary. [report.schema.json](report.schema.json) specifies required fields.
- `files.json`: SHA-256 integrity baseline; excludes only itself. It is not signed authenticity evidence.
- `index.html`: escaped static source index. HTML characters in instance names are escaped. Source links open source files; no recovered game code is executed.

`validate` verifies file hashes, presence and unexpected files, not syntax or semantics. Syntax statuses are stored per script from the optional compiler. `decompiled_candidate` means compiler not configured; `decompiled_syntax_valid` means syntax passed, with semantics still unverified.

The root live/replay report uses `rcr-run/v1` and [run.schema.json](run.schema.json). Its repository link points to the unchanged `rcr-report/v1` structure above. Root `source_files` counts syntax-valid instance outputs; `source_candidates` includes invalid candidates. Unique payload/content counts and duplicate-instance references are separate. Each instance keeps its full path and provenance even when tool work is reused.
