# Changelog

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
