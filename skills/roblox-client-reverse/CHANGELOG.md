# Changelog

## 0.6.0

- `capture` reads the Place the Player is actually in. Structure validation accepts the Universe ID with exactly one Place ID from `places.json`; the DataModel holding them names the current Place before any buffer is read.
- When a game transfers the Player to another catalogue Place, the requested Place's entry becomes `redirected` (`redirected_to`) and the actual Place is captured under its own ID; a Place that already has a capture is reported as `skipped` without reading buffers. Existing failed or unfinished attempts without `--retry-failed` are reported as `blocked` (exit 2) instead of raising.
- `--place` is optional. Attempts are written under `captures/.attempt-*` and moved once the Place is known; a transfer during capture fails the attempt.
- Not yet exercised against a live Player transfer; covered by authored memory fixtures and capture tests.

## 0.5.1

- An interrupted `capture` (for example Ctrl+C) is recorded as a failed attempt with its report, manifest and Place state before the interrupt is re-raised. An attempt killed before writing its report can be retried with `--retry-failed` when it holds no buffers; otherwise `capture` explains why the data is kept.
- `recover GAME` lists unfinished and failed captures under `capture_failures` and continues with the other Places. A failed capture is never merged, even when it wrote a dump.
- The previous repository is moved to `GAME/.repository-previous` during replacement and stays there if both the swap and its rollback fail; the next `recover` restores it. An existing repository without `files.json` is reported instead of raising a missing-file error.
- The reuse fingerprint includes `cfg_diagnose.py`, which selects function CFG correspondences.

## 0.5.0

- Replaces the combined launch/collection workflow with `places`, `players`, `open`, `screenshot`, `capture`, `close` and `recover`. Removes `run`, `acquire`, `extract`, `replay`, `import` and `decompile` commands, their backend shim, UI observation-file gate and obsolete Computer Use bridge.
- Adds explicit `capture --retry-failed` for failed attempts with no buffers; prior diagnostics remain in that same capture directory and successful/opaque raw captures stay protected.
- Stores one raw capture per Place in `captures/<PlaceID>/`, independently of native recovery tools. Official metadata refresh preserves separate entry/capture summaries. Capture identity, raw hashes and RSB1 validation remain mandatory.
- Builds one repository for one or many Places, merging exact code only at the same logical path/class/encoding. Conflicting modules get `@PlaceID` names; variants alone carry repeated Place labels. Missing observations remain explicit.
- Reuses verified prior source/CFG outputs only under matching raw code, encoding and tool/configuration/code fingerprints. Updates use a temporary transaction and retain the prior repository if replacement cannot complete. Citation remaps and added modules are reported for follow-up analysis.
- Preserves distinct logical paths in maps and checks. Shared function names do not count as explicit mentions of another code version. Ambiguous original-path citations are rejected.
- Splits official artifact downloading into `fetch`, with no decompilation or repository creation. Supplied files and existing raw captures use `recover` input adapters.
- Adds optional HWND-targeted WGC screenshots, blank-frame/target-loss handling, normal selected-window close and explicit selected-process force fallback. Native compiler/decompiler revisions and algorithms are unchanged.

## 0.4.3

- Adds independent `players`, `open`, `screenshot` and `close` commands. Process creation identity and HWND ownership keep selection and control scoped to the observed Player.
- Screenshots use optional HWND-targeted Windows Graphics Capture in a bounded worker, save PNG/hash metadata and reject minimized, missing or blank content. Image acquisition is separate from game-entry confirmation.
- Launch summaries are recorded in `places.json`; capture summaries are preserved. Normal close uses SC_CLOSE; forced termination requires `--force`.
- Existing combined collection/recovery remains unchanged and still uses its Computer Use observation; separate capture integration follows independently.

## 0.4.2

- `places TARGET --out GAME_DIRECTORY` discovers official Universe/Place metadata with pagination and no Player, credentials or native tool prerequisite.
- `places.json` keeps entry and capture summaries independent and preserves them across API refreshes. Incomplete requests remain explicit; removed Places are retained with `listed:false` after a complete refresh.
- The current live collector and decompiler are unchanged. Player-control commands and catalogue integration into capture are not part of this release.

## 0.4.1

- `repo-map RUN --out FILE` writes a folder map of the distinct recovered modules (one per bytecode) with their most common names.
- `check-analysis --map FILE` writes the modules a document never mentions in the same folder-map form.
- `check-analysis` also checks unquoted citations without spaces, such as those inside pipe-separated tables.
- `references/analysis.md` describes the delegated workflow: an analysis subagent with the map, the check, and a new follow-up subagent with the document body and the unmentioned map. Analysis subagents do not start subagents.

## 0.4.0

- `check-analysis RUN DOCUMENT` checks a design document written from a recovered repository: every `` `instance/path:LINE[-END]` `` citation must resolve to a recovered script and a line inside it, and distinct modules (one per bytecode, default player scripts excluded) whose names never appear are listed by folder as candidates for a follow-up pass.
- `references/analysis.md` gives the whole-game analysis prompt, the follow-up pass and the limits of the checks.
- This repository no longer ships `roblox-game-reconstruction`; its last release is tagged `roblox-game-reconstruction-0.1.5`.

## 0.3.6

The native compiler/decompiler pins and the v0.3.5 decompiler patch are unchanged.

Fixes
- CFG audits of vanilla Luau bytecode (`--bytecode-format luau`) pass the supported `--standard` disassembler flag; a disassembler failure is recorded as `cfg_audit.failure: tool_error`, separate from an inconclusive comparison.
- RSB1 decoding rejects Zstd frames whose declared size disagrees with the envelope, caps output at the envelope size and rejects trailing data. Frames without a declared content size are accepted in RSB1 envelopes and binary place files.
- Explicit `--tools` configurations are checked against the bundled patch hash; a hash-declared decompiler's `roblox_argv` must use the same executable or declare `roblox_sha256`.
- `replay` and `regression` resolve inputs and tools before creating the output and write a terminal failed report (exit 2) for later failures. `replay` decodes only buffers the live collector approved.
- The structure cache stores layout only; approved bytecode/type versions come from the bundled compatibility profile.

Statuses
- Syntax: `SYNTAX_VALID`, `SYNTAX_INVALID` (compile failure), `NOT_CHECKED` (no compiler), `SYNTAX_UNVERIFIED`.
- CFG: inconclusive checks are `UNVERIFIED`; the legacy `CFG_UNVERIFIED` value is no longer accepted.

Performance
- Distinct bytecodes are recovered in parallel worker processes (`RCR_WORKERS`, default one per CPU; `1` for sequential), with results identical to sequential recovery.
- Tools are awaited without sleep polling, tool executables are hashed once per process, and the root manifest reuses the validated repository manifest.

Build
- `setup` uses the newest installed Visual Studio (2022 or later) and runs the native CFG/emitter invariant tests on every platform.

## 0.3.5

- The lifter preserves incoming values needed on a branch's bypass path at the merge; the exact CFG fallback retains FORGLOOP iterator-mode AUX bits.

## 0.3.4

- Adaptive Windows structure discovery with a binary-SHA256 layout cache that is revalidated in every process.

## 0.3.3

- Relational SSA/CFG verification in addition to the exact certificate.

## 0.3.2

- Exact reachable instruction/prototype graph certificate.
