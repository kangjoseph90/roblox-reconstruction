---
name: roblox-client-reverse
description: Launch a Roblox experience with Computer Use, collect client LocalScript/ModuleScript bytecode on Windows x64, and restore a source repository with separate syntax, CFG, and behavior evidence. Also recover local exports, replay saved captures offline, and check game design documents written from a recovered repository.
---

# Roblox client reverse

Version **0.4.0**. `check-analysis` checks citations and module mentions of a design document written from a recovered repository; see [analysis.md](references/analysis.md) and [CHANGELOG.md](CHANGELOG.md). Recovery decompiles, compiles and CFG-audits distinct bytecodes in parallel worker processes with results identical to sequential recovery. The decompiler patch is unchanged from v0.3.5, whose lifter preserves incoming values needed on a branch's bypass path at the merge. Collection uses the v0.3.4 adaptive Windows discovery described in [adaptive-builds.md](references/adaptive-builds.md). The pinned compiler and conservative CFG verification model remain those of [v0.3.3](references/verification-v0.3.3.md).

Use `scripts/rcr.py` as the single entrypoint. Runtime collection and offline recovery share the same parser, decompiler adapters, path handling and integrity validation. Do not substitute public metadata or fixtures for a live capture.

## Prerequisites

Read [INSTALL.md](INSTALL.md) for a new environment. Python 3.9+, the pinned Python packages, Git, Visual Studio 2022 or later C++ tools and CMake are required for Windows live runs. Native compiler/decompiler sources are fetched at pinned commits and built locally; binaries are not bundled. A logged-in, installed Roblox Player and a host Computer Use tool are required. The CLI cannot observe the desktop by itself.

Run `python scripts/rcr.py doctor`. Live collection is Windows x64. Hashes in [compatibility.json](references/compatibility.json) are historical evidence and metadata hints, not an allowlist. On a cache miss, discover Instance identity/name/parent/children, DataModel identity and script-buffer fields independently of the historical object layout. Require unique candidates, reciprocal parent/children relationships, independent Place/Universe identity and stable integrity-valid bytecode witnesses. Cache only a structure that passes these checks. A cache hit must pass live checks again; a rejected cache may trigger one bounded fresh discovery, never a fallback to guessed old offsets. Failed or ambiguous validation stops collection. See the reference for fixed ABI assumptions and search bounds. An unlisted build is attempted, not certified by its version. Offline modes remain available on other platforms.

## URL → repository

On a fresh host, read the Computer Use reference and initialize that tool before starting the CLI, so setup of the observer does not consume the game-entry timeout. Slow first renders can use `--ui-timeout 360`; never confirm a scene that has not rendered.

1. Start one asynchronous CLI process with a new output directory:

   ```powershell
   python scripts/rcr.py run 'https://www.roblox.com/games/PLACE_ID/name' --out work/run
   ```

   Use the host shell tool with a short yield. Keep its session ID. If tools are absent, this process builds them in `work/.rcr-tools`; `--tools-dir` selects another cache, and `--tools tools.json` selects an existing hash-checked configuration. Structures default to the output parent's `.rcr-structures`; `--structure-cache DIR` selects a persistent binary-SHA256 cache. `--player-exe PATH` launches that exact executable with the normal experience URI; it does not disable updates. `acquire --backend auto/player` is an alias of this workflow. `--backend official` is a separate API path.

2. Follow `work/run/state.json`. At `awaiting_computer_use`, read [references/computer-use.md](references/computer-use.md), select the actual Roblox window and observe the joined game using the host's Computer Use APIs. The CLI already launched the exact experience URI; finish normal app navigation if needed. Do not confirm a home screen, launcher or pending loading screen as game entry. Do not automate login/security dialogs, purchases or reward buttons.

3. After observing the game scene, write the **actual observation** to the confirmation path in `launch.json`, normally `work/run/ui-confirmed.json`:

   ```json
   {"observed_at":"CURRENT_UTC_ISO_TIMESTAMP","place_id":12345,"game_scene_visible":true,"window":{"id":123,"app":"RETURNED_WINDOW_APP"}}
   ```

   Use the actual returned window ID and app identity. If a provider returns a PID/name instead of an executable path, follow the explicit PID-to-image mapping in the Computer Use reference and preserve the original provider fields. Use the requested Place ID and current UTC time. If entry fails, or normal updating launches another binary than the requested test build, record `terminal_error` and do not certify that build's game entry. A fresh game-scene observation is mandatory; native checks independently verify Place and Universe IDs.

4. Resume/poll the **same CLI process**. It selects the compatible PID, verifies access and current-build class metadata, captures stable script buffers, verifies RSB1 integrity/decompression, decompiles with explicit Roblox encoding, compiles candidates for syntax, and writes `repository/` plus root manifests. No second collection/decompilation command is required. If several compatible processes exist, inspect them and rerun in a fresh output with `--pid`; do not choose an unrelated process.

5. Read `report.json`, open `index.html` / `repository/index.html`, and run `python scripts/rcr.py validate work/run`. If this task launched the game, close that game window through Computer Use after collection; observe any app exit prompt before acting. Leave user-owned unrelated windows alone.

`run`, `replay` and `regression` write terminal failure reports and preserve completed evidence once the output directory exists; unreadable inputs and tool configuration fail with exit 1 before it is created. Exit 0 is recovered/pass; 2 is partial, no-source, failed collection or integrity failure; 1 is invocation/configuration error. Read `collection_result`, syntax and CFG states; normal process termination is not collection success. Partial is an inspectable result, not a reason for blind retries or a whole-game claim. Computer Use timeout defaults to 180 seconds. Bulk scans have an explicit per-pass `--memory-budget-mib` (default 4096); cold discovery and final enumeration are separate passes and both coverage records must be inspected. Failed/interrupted runs are preserved; retry only with a concrete reason and a fresh output directory.

## Offline recovery and regression

```powershell
python scripts/rcr.py import export.rbxlx --out work/import
python scripts/rcr.py replay saved-run --out work/replay --tools work/.rcr-tools/tools.json
python scripts/rcr.py regression CAPTURE_DIRECTORY --baseline EXPECTED_JSON --out OUTPUT_DIRECTORY --tools TOOLS_JSON
python tests/semantic/run_suite.py --tools work/.rcr-tools/tools.json --out work/semantic.json
python -m unittest discover -s tests -v
```

`replay` accepts a previous run root, or a capture directory containing `client-dump.json`, `capture-report.json` and `originals/`. It verifies encoded buffers before interpreting bytes, decodes only buffers the live collector recorded as `roblox_bytecode_captured` and preserves the original capture report in `evidence/capture.json`, so its output can be replayed independently of the input directory. It is never a new live acquisition. `regression` takes the capture directory and baseline JSON as explicit paths, compares payload states, source hashes and CFG certificates, and does not launch Roblox. No named dataset or in-repository fixture directory is assumed.

Unknown encoding stays opaque. An explicit `roblox` encoding uses only a configured `roblox_argv` adapter; never pass it to `--standard` or label it vanilla. `--bytecode-format luau` asserts vanilla encoding. Never run recovered game code to validate it. Compile-only syntax success does not prove semantic equivalence.

Read each script's `verification`: `SYNTAX_VALID` means parser/compiler success, `SYNTAX_INVALID` a compile failure, `NOT_CHECKED` that no compiler was configured and `SYNTAX_UNVERIFIED` any other inconclusive check; `CFG_VALIDATED` requires the conservative exact or relational SSA/CFG certificate, with preserved evidence in `cfg-audit/`; `UNVERIFIED` means the checker could not prove equivalence; `CFG_MISMATCH` requires a concrete counterexample under the stated model. Matching individual functions does not certify the whole module. `BEHAVIOR_VERIFIED` is reserved for successful authored fixtures under the stated inputs and `BEHAVIOR_FAILED` for authored fixtures whose outputs differ; game results remain `NOT_TESTED`. A warning-free candidate is still a candidate.

## Analysing the repository

Give the recovered `repository/` to one agent with the whole-game prompt in [references/analysis.md](references/analysis.md), asking for `instance/path:LINE` evidence. Then run:

```powershell
python scripts/rcr.py check-analysis work/run DESIGN.md
```

It reports citations that do not resolve to a recovered script line (status `invalid_citations`, exit 2) and distinct modules the document never mentions, grouped by folder. Run one follow-up pass when unmentioned folders hold game logic. These checks are mechanical: a resolving citation does not show that the line supports the claim, and server code is not in the repository.

## Outputs and limits

- `capture/originals/`: exact encoded buffers, content-addressed by SHA-256.
- `capture/client-dump.json`: full observed ancestor paths/IDs, per-script capture errors, bytecode and origin metadata.
- `evidence/`: environment/profile, actual API errors, Computer Use observation, capture inventory, coverage and tool hashes.
- `evidence/structure-validation.json`: discovery/cache disposition, rejected hypotheses and validation witnesses. `resolved-profile.json` records actual discovered fields; `target-identity.json` preserves the independent identity response. A runtime-validated layout is an observational certificate under stated ABI/bounds, not proof of arbitrary future compatibility.
- `repository/`: the existing source repository format, now with explicit Roblox decompilation and cached duplicate processing. Each script keeps a separate path/status even when code is identical. Directories are bounded for Windows; exact complete instance paths remain in `instances.json` and `report.json`.
- root `report.json`, `index.html`, `files.json`: run status, repository link and integrity manifest.

Collection is a non-atomic snapshot of replicated LocalScript/ModuleScript containers. It excludes platform Core services but may still include default character/camera scripts and shared libraries. It does not collect server-only code or RunContext Client `Script` containers, establish which code executed, prove streaming completeness, handle arbitrary teleports automatically, or compare against unavailable developer originals. Keep missing buffers, invalid decompilation, duplicates, syntax-valid candidates and unknown coverage distinct. Preserve original buffers and failed outputs.

No process injection, target-memory writes, privilege escalation, protection/anti-tamper changes, credential extraction or remote decompilation services are implemented. The user's requested target defines task scope; the skill itself does not confer game ownership or additional permissions.

