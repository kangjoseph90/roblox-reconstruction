# Raw Player capture

`capture --pid PID [--place PLACE_ID] --game GAME` captures the Place the Player is in into `GAME/captures/<PlaceID>/`. It does not launch a Player, build recovery tools, decompile bytecode, or overwrite an existing capture. See [player-control.md](player-control.md) for the discover/open/screenshot sequence and [acquisition.md](acquisition.md) for recovery.

## Current Place and transfers

Games may move a joining Player to another Place of the same Universe (for example to a saved world). Capture therefore does not trust the requested Place. Structure validation accepts the Universe ID together with exactly one Place ID from `places.json`, and the DataModel holding them names the current Place. This happens after the memory scan and before any script buffer is read:

| Current Place | Result |
|---|---|
| The requested Place | captured as usual |
| Another catalogue Place without a capture | captured into `captures/<actual>`; the result has `requested_place_id`; the requested Place's `entry` becomes `redirected` with `redirected_to` |
| Another catalogue Place that already has a capture | `skipped` before collecting script buffers; the requested Place is still marked `redirected` |
| An existing failed or unfinished attempt without `--retry-failed` | `blocked` (exit 2) with the reason |
| No catalogue Place, or another Universe | failed structure/identity validation |

Without `--place`, capture takes whichever catalogue Place is open. The actual Place keeps its own `entry` record. After collecting buffers, capture scans for DataModels again and reads the Place and Universe from memory. The attempt fails if the matching DataModel is missing, ambiguous, or differs from the first observation. `evidence/place-validation.json` records both observations and the final scan coverage. These are two observations of a non-atomic snapshot. Refresh `places` first if a game may move Players to a Place the catalogue does not yet list.

Each attempt is written to `captures/.attempt-*` and moved to its Place directory once that Place is known. A failure before the Place is identified is stored under the requested Place when that directory is free; otherwise the attempt stays under its `.attempt-*` name and the result names it in `path` and `kept_aside`. Interrupted attempts may leave `.attempt-*` directories; they are not referenced by `places.json` and can be removed.

If a prior attempt failed before any raw/bytecode buffers were written, `--retry-failed` can explicitly retry that same directory after its integrity passes. Previous diagnostic bytes are retained in `evidence/attempt-errors.json`; no capture-version folders are created. The flag cannot replace a capture containing buffers.

The collector uses read-only process access. It validates the executable, supported structure, target Place and Universe identity, stable script buffers, and supported RSB1 integrity/encoding before saving raw observations. Unlisted executable hashes may be attempted, but are not certified by version. Ambiguous layouts, unsupported ABI/encoding, insufficient witnesses, unreadable data or failed identity checks stop or limit capture. Normal process exit does not establish success; reports retain partial/missing/unknown scope. See [adaptive-builds.md](adaptive-builds.md) for assumptions and bounds.

Each capture preserves the observed instance metadata and provenance, original encoded byte buffers, capture evidence and file inventory. Recovery later checks hashes and approvals on those original buffers. Convenience fields such as embedded source or decoded bytecode do not authorize recovery.

Per-script diagnostics record the inventoried address, class, name and parent, along with the before/after observations and timestamps. Path revalidation can report `node_unreadable`, `class_changed`, `name_changed`, `parent_chain_invalid`, `wrong_datamodel`, `object_destroyed_or_replaced` or `unknown`. The destruction/replacement label denotes a self-pointer or vtable mismatch. Workspace scripts are checked first because their instances can be transient.

The snapshot is non-atomic and covers observed replicated LocalScript/ModuleScript containers. It does not establish full replication, script execution, server-side logic, coverage of Places a game never transfers to, or RunContext Client Script support. No recovered game code is run. Keep syntax, function CFG, module CFG and behavior states distinct; game behavior remains untested unless supported by separate direct evidence.
