---
name: roblox-client-reverse
description: Discover Roblox Places through official APIs, control a Windows Player, save raw client captures, recover client source repositories, and mechanically check analysis documents.
---

# Roblox client reverse

Version **0.6.2**. Use `scripts/rcr.py` as the single entry point. Available commands are `doctor`, `setup`, `places`, `players`, `open`, `screenshot`, `record-entry`, `capture`, `close`, `recover`, `fetch`, `inspect`, `validate`, `regression`, `repo-map`, and `check-analysis`.

## Standard workflow

1. Use official APIs to discover the Universe and Places. Never infer Place IDs from decompiled code. See [places.md](references/places.md).
2. Start with the input Place. Open it, save a screenshot of the identified Player window inside GAME, inspect it, and use `record-entry` for the observed result. Capture verifies the actual Place separately. If entry/access fails, proceed to the next Place when appropriate. `close --pid PID --game GAME --place PLACE_ID` closes only the Player that `open` recorded as a new process for that Place. See [player-control.md](references/player-control.md).
3. After the screenshot shows a loaded game scene, use `capture --pid PID --place PLACE_ID --game GAME` to save raw buffers. Capture reads the Place the Player is actually in from its DataModel. If a game transferred the Player to another catalogue Place, capture records the requested Place as `redirected` (only for the Player `open` recorded as a new process for it; otherwise the result reports `requested_place_unconfirmed`), captures the actual Place under its own ID, or reports `skipped` when that Place already has a capture; continue with the next uncaptured Place. Capture does not build native tools or decompile and never overwrites an existing capture. A failed attempt without buffers can be retried explicitly with `--retry-failed`, preserving diagnostics; see [player-acquisition.md](references/player-acquisition.md).
4. Use `recover GAME` to update the single `GAME/repository` from all available raw Place captures. For recovery of supplied files or a standalone capture, use `recover INPUT --out DIR`. See [acquisition.md](references/acquisition.md) and [player-acquisition.md](references/player-acquisition.md).
5. Create a module map and follow [analysis.md](references/analysis.md) for the whole-game analysis and a fresh, independent follow-up pass over omissions.

If a user later enters a previously inaccessible Place manually, list Players and inspect its screenshot, capture that existing PID without relaunching it (omit `--place`; capture reads the current Place), recover the same GAME, refresh the map, and add an analysis addendum. Leave the user's Player open; `close` refuses Players that `open` did not record as a new process.

## Evidence boundaries

Raw capture provenance remains in each Place capture. Recovery merges only identical logical paths with matching bytecode, encoding and class; conflicting versions get Place-qualified module paths, with `variants.json` emitted only when needed. Unavailable and opaque observations remain explicit and unverified. Recovery trusts only hashed, approved original buffers, never embedded source or decoded convenience fields. Prior results are reused only when bytecode and encoding match, prior integrity passes, and tool/configuration/code fingerprints match. See [adaptive-builds.md](references/adaptive-builds.md).

Syntax validity, function CFG evidence, module CFG evidence and observed behavior are distinct. Record their actual scopes separately. Missing data leaves equivalence and coverage unverified. See [CFG verification](references/cfg-verification.md) for status meanings and [formats.md](references/formats.md) for artifact contracts.

For command details and setup, read [INSTALL.md](INSTALL.md). Native revisions, source hashes, patches and notices remain pinned; do not alter them without rebuilding and verifying the tools.
