# Artifact and adapter contracts

`rcr.py inspect INPUT` identifies one artifact. `recover INPUT --out OUTPUT` accepts one file or recursively inventories a directory. Symlinks are skipped. Originals include unrecognized regular files, which are not interpreted as source. Limits: 128 MiB per file and expanded binary, 512 MiB across inputs, 200,000 instances per artifact, 256 hierarchy levels. Output must be new/empty and outside the input directory.

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

For Roblox input, provide `decompiler.roblox_argv` with the explicit `--roblox` mode. A standard-only adapter is rejected. Setup configurations record executable `sha256`. All recovery paths enforce declared compiler/decompiler hashes before use; saved-capture recovery additionally requires both tools and a decompiler `patch_sha256` matching the bundled patch, whether the configuration comes from the setup cache or `--tools`. With a hash-declared decompiler, `roblox_argv` must run the same executable or declare `roblox_sha256` for a separate one. Offline custom adapters may omit hashes, so those configurations remain caller-trusted. For tools writing a file, put `{output}` in argv and set `output: "file"`. No shell expansion is used. Placeholders replace literal substrings in each argument. The adapter checks exit status, UTF-8 output and configured version gates, preserves stdout, then compiles the output. Configuration is executable trust: use a locally reviewed file, not one embedded in an untrusted dump. Timeout is capped at 120 seconds. `rsb1.py` verifies the captured wrapper's XXH32, bounded Zstd output and payload header; arbitrary unknown wrappers stay opaque.

## Repository/report

- `originals/`: exact supplied artifacts; SHA-256 and byte count in `report.json`.
- `instances.json`: class/name/parent, original paths, capture metadata, serialized metadata where available.
- `src/`: escaped paths plus unique identity suffixes, normalized `init.luau`, original bytecode and decompiler stdout. Full logical paths survive in report even when on-disk paths are shortened.
- `report.json`: `rcr-report/v1`, operation, coverage, artifacts, individual scripts when applicable, validation statuses and summary. [report.schema.json](report.schema.json) specifies required fields.
- `files.json`: SHA-256 integrity baseline; excludes only itself. It is not signed authenticity evidence.
- `index.html`: escaped static source index. HTML characters in instance names are escaped. Source links open source files; no recovered game code is executed.

`validate` verifies file hashes, presence and unexpected files, not syntax or semantics. Syntax statuses are stored per script from the optional compiler. `decompiled_candidate` means compiler not configured; `decompiled_syntax_valid` means syntax passed, with semantics still unverified.

Saved-capture and supplied-file recovery write mutable `recovery.json` (`rcr-recovery/v1`) and one immutable `repository/`; [recovery.schema.json](recovery.schema.json) describes that outer report. A game directory instead keeps mutable Place states in `places.json`, raw data in `captures/<PlaceID>/`, a single recovered `repository/`, and the latest recovery summary/changes in `recovery.json`. `variants.json` is emitted inside the repository only for conflicting code versions. Raw captured buffers stay in their original capture directory; decoded bytecode, candidate source and CFG evidence are preserved in the repository. It does not create another permanent merged copy.

`source_files` counts candidate source files; `syntax_validated` is separate. Neither establishes behavior equivalence. Bytecode work is reused across aliases, while full logical module paths/versions remain separate in maps and citations.

See [CFG verification](cfg-verification.md) for audit evidence and [format and runtime background](research.md) for source references.
