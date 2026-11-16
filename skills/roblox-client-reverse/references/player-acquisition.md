# Player acquisition and client-dump import

## Live Windows collection

`rcr run URL --out OUTPUT` and `rcr acquire URL --backend player --out OUTPUT` (also `--backend auto`) use the same Windows x64 workflow: launch the Player, await a host Computer Use observation of the joined game, discover and validate its current structure, capture stable script buffers, and restore a repository. The observer and CLI coordinate through `state.json`, `launch.json` and `ui-confirmed.json`; see [computer-use.md](computer-use.md).

Collection uses read-only process access. Layout candidates are checked against live class/parent/children relationships, independent target identity and integrity-valid payload witnesses. An unlisted binary hash is attempted; unsupported ABI/encoding, insufficient witnesses, ambiguity or failed validation stops collection. A cached structure is revalidated. See [adaptive-builds.md](adaptive-builds.md) for the actual assumptions, bounds and coverage fields.

`rcr doctor` reports current environment/backend availability. An available backend does not prove that a game was joined or scripts were collected. Read the run's collection result, unavailable instances and scan limits separately from syntax and CFG outcomes. Computer Use timeout/failure preserves the failed run; use a new output directory for a justified retry.

`acquire --backend official` selects the separate official asset-delivery API. It does not launch the Player or invoke the memory collector. Its method list records the Player path as `not_selected`, not as an unimplemented feature. See [acquisition.md](acquisition.md).

## Read-only access diagnostic

The optional probe tests process access, not collection:

```powershell
python scripts/windows_access_probe.py --out owned-control.json
python scripts/windows_access_probe.py --pid PLAYER_PID --out windows-access.json
```

The optional PID must identify RobloxPlayerBeta.exe. The probe compares its first 64 image bytes against the file on disk and runs an owned-child marker control. It exposes no arbitrary address or memory-scan option. Exit 0 means those comparisons passed; exit 2 means a prerequisite or comparison failed. Neither status means script code was captured. On Windows the owned control uses the interpreter's pipe-reported PID/address because a virtual-environment launcher can have a different PID.

## Offline import and replay

```powershell
python scripts/rcr.py import client-dump.rbxlx --out NEW_REPOSITORY
python scripts/rcr.py replay SAVED_RUN --out NEW_REPLAY --tools TOOLS_JSON
python scripts/rcr.py validate NEW_REPLAY
```

Import preserves supplied artifacts and logical instance paths, and distinguishes source text, captured-decompiler output, missing code and opaque bytecode. Unknown encoding stays opaque; never assert vanilla encoding merely to force transformed Player bytes through a standard decoder. [formats.md](formats.md) defines the accepted containers and provenance fields.

The importer supports the Source convention of pinned [UniversalSynSaveInstance](https://github.com/luau/UniversalSynSaveInstance/tree/888b5f318d1e035434c473c7b952bd1f85b767ab), including optional Base64 bytecode and disabled/failed decompiler markers. The format reader is independent; this skill does not load or redistribute that collector or supply its host runtime. Its upstream [license](https://github.com/luau/UniversalSynSaveInstance/blob/888b5f318d1e035434c473c7b952bd1f85b767ab/LICENSE) remains separate from the reader.

A replay preserves the input capture report byte-for-byte in `evidence/capture.json`, copies verified original buffers and records that no new live capture occurred. Its output is a valid input to a later replay even when the earlier input directory is unavailable. Each replay verifies encoded and decoded hashes again; retained decoded bytes do not replace the integrity checks.

## Coverage and platforms

Live collection is Windows x64. Other hosts can use offline readers/recovery when their dependencies and tool adapters are available; no macOS live backend is implemented. Collector availability, importer support and decompiler compatibility are separate capabilities.

A snapshot covers observed replicated LocalScript/ModuleScript containers. It does not establish complete replication, script execution, server-only logic, arbitrary teleport coverage or RunContext Client Script support. Original buffers, per-instance failures and coverage remain inspectable. Syntax success, function CFG evidence, module CFG evidence and behavior verification remain distinct.
