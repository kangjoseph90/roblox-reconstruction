# Windows structure validation and compatibility

Live `capture` is limited to Windows x64 and uses bounded, observational structure discovery. `runtime_validated` describes only the recorded checks; it is separate from historical build support, bytecode syntax, CFG evidence and behavior.

## Resolution and acceptance

The collector fingerprints the selected executable and verifies the actual PID image. Historical class metadata is a hint, not an allowlist. If a hinted layout does not match live observations, bounded discovery seeks an alternative; an unlisted binary can be attempted but is not certified by its version.

Discovery validates live class names, Instance relationships, DataModel identity, and stable payload witnesses. It requires a unique target DataModel matching a catalogue Place ID and the independently fetched Universe ID. Name/parent/children relationships must form valid ancestor chains with reciprocal membership. Script-buffer candidates are scoped to the target and must pass repeated stable reads, RSB1 XXH32 integrity, bounded decompression, supported bytecode/type headers and independent witness checks. After buffer collection, a DataModel-only scan rechecks the selected root and IDs. Ambiguous candidates or failed identity/integrity checks stop capture. No recovered code executes.

A structure-cache entry, if used, is only a candidate: identity, relationships and payload witnesses are rechecked in the current process. A rejected cache may trigger bounded fresh discovery, never fallback to guessed offsets. Only validated structure fields are reusable; process IDs, absolute pointers and game identities are not. See `capture --help` for the optional `--structure-cache` setting.

## Fixed assumptions and search bounds

- Windows x64 pointer alignment, an image-owned Instance anchor at zero, uniform common Instance fields, descriptor name pointer at +8, and MSVC `std::string` length/capacity representation are fixed ABI assumptions. They are not dynamically rediscovered; incompatible representations fail.
- Common fields are searched within `0x180`, DataModel identity within `0x400`, script fields within `0x600`, and container headers within `0x80`. These are bounds, not accepted offsets. Children containers support pointer/shared-pointer vectors with stride 8/16. Parent chains are bounded to 128 nodes.
- Discovery needs multiple observed instances of both script classes, diverse payloads, multiple parents/services, and sufficient target-scope witnesses. Sparse, unloaded, ambiguous or unsupported patterns may be rejected.
- Memory-region snapshots are non-atomic. Unreadable ranges and unknown game completeness remain unknown. The configured scan budget applies per pass; structural discovery and final enumeration are separate observations.
- Supported RSB1/Luau envelopes and encodings are explicit. A changed format is unsupported until independently implemented; never guess a decoder or label transformed Roblox bytecode as standard Luau.

## Scope and results

Capture targets observed replicated LocalScript/ModuleScript containers. It does not collect server-only code, RunContext Client `Script` containers, establish which code executed, prove complete replication, or automatically cover arbitrary teleports. Per-Place reports retain observed instances, unavailable records, capture errors, validation evidence and file integrity data. A process that exits normally may still have partial or failed capture.

Treat missing or opaque observations as unverified, not as equal to code observed at another Place. Compare full logical paths, class and bytecode/encoding evidence across captures. A lower count in one snapshot does not prove deletion. Runtime layout checks do not prove source semantics or behavior.
