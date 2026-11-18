# Raw Player capture

`capture --pid PID --place PLACE_ID --game GAME` captures one Place into `GAME/captures/<PlaceID>/`. It does not launch a Player, build recovery tools, decompile bytecode, or overwrite an existing capture. See [player-control.md](player-control.md) for the discover/open/screenshot sequence and [acquisition.md](acquisition.md) for recovery.

If a prior attempt failed before any raw/bytecode buffers were written, `--retry-failed` can explicitly retry that same directory after its integrity passes. Previous diagnostic bytes are retained in `evidence/attempt-errors.json`; no capture-version folders are created. The flag cannot replace a capture containing buffers.

The collector uses read-only process access. It validates the executable, supported structure, target Place and Universe identity, stable script buffers, and supported RSB1 integrity/encoding before saving raw observations. Unlisted executable hashes may be attempted, but are not certified by version. Ambiguous layouts, unsupported ABI/encoding, insufficient witnesses, unreadable data or failed identity checks stop or limit capture. Normal process exit does not establish success; reports retain partial/missing/unknown scope. See [adaptive-builds.md](adaptive-builds.md) for assumptions and bounds.

Each capture preserves the observed instance metadata and provenance, original encoded byte buffers, capture evidence and file inventory. Recovery later checks hashes and approvals on those original buffers. Convenience fields such as embedded source or decoded bytecode do not authorize recovery.

The snapshot is non-atomic and covers observed replicated LocalScript/ModuleScript containers. It does not establish full replication, script execution, server-side logic, arbitrary teleport coverage or RunContext Client Script support. No recovered game code is run. Keep syntax, function CFG, module CFG and behavior states distinct; game behavior remains untested unless supported by separate direct evidence.
