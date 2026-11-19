# Acquisition and offline inputs

Acquisition and recovery are separate operations. Live `capture` stores one raw Place observation and never builds native tools or decompiles. `recover` verifies inputs, runs the recovery pipeline, and writes source. `fetch` downloads an officially accessible artifact only; it does not recover it.

## Live Place capture

```sh
python scripts/rcr.py places PLACE_ID --out GAME
python scripts/rcr.py open PLACE_ID --game GAME
python scripts/rcr.py screenshot --pid PID --out GAME/evidence/PLACE_ID-entry.png
python scripts/rcr.py record-entry --game GAME --place PLACE_ID --outcome OBSERVED_OUTCOME --screenshot GAME/evidence/PLACE_ID-entry.png
python scripts/rcr.py capture --pid PID [--place PLACE_ID] --game GAME
python scripts/rcr.py recover GAME
```

Capture is scoped to one Place and saves one raw capture under `GAME/captures/<PlaceID>/`, containing `client-dump.json`, `originals/`, `bytecode/`, `evidence/`, `report.json`, and `files.json`. It checks that the observed Place and Universe match the catalogue. Existing captures are never silently overwritten. Normal process exit is not a successful capture; inspect the report's partial/missing/unknown scope and access errors. See [player-acquisition.md](player-acquisition.md) and [adaptive-builds.md](adaptive-builds.md).

## Recovery

`recover GAME` updates only `GAME/repository` and `GAME/recovery.json` from all available Place captures, using the same pipeline whether one or several Places were captured. It does not create a recovered repository per Place or a permanent merge copy. `recovery.json` records changes, including `citation_remaps`, `added_modules` and `changed_sources` (same module and path, different recovered text, so line citations may shift); use these to update affected citations only where the matched original code supports them. If an earlier run was interrupted before writing `recovery.json`, its changes are kept under `earlier_changes`. `earlier_changes` appears only for runs whose own `recovery.json` was never written (an interruption after the repository swap); runs are identified by `repository_created_at`, and a recorded run's changes are not repeated. A rebuild that would leave a previously recovered module without source keeps the existing repository and fails. Only one `recover GAME` runs per GAME at a time (`GAME/.recover.lock`; remove a leftover lock only if no recovery is running). `--tools-dir` must be outside the raw captures and the recovered repository. Without `--tools`/`--tools-dir`, `recover GAME` builds pinned tools under `GAME/.tools` and supplied-input recovery under `<out parent>/.rcr-tools`; that build uses the network even though the extraction report's `network_used` describes only the extraction. Refresh the map and follow up on newly added or changed scope. Do not rewrite analysis claims automatically.

Recovery accepts only hashed, approved original buffers from raw captures. Embedded source and decoded convenience fields are ignored. Supplied older raw capture directory layouts are input adapters, not separate CLI commands. Offline sources are recovered with `recover INPUT --out DIR`, optionally with `--tools`/`--tools-dir`, `--include-server`, and `--bytecode-format` (`luau`, `roblox`, or `unknown`). Unknown or opaque content remains unverified.

Prior module results are reused only when bytecode and encoding match exactly, the previous repository passes integrity validation, and tool/configuration/code fingerprints match. Distinct paths keep their identity while expensive processing can be reused. Modules at the same logical path merge only when bytecode, encoding and class match. Conflicting versions receive `@PlaceID`; if multiple conflicts share a Place, a short hash disambiguates. `variants.json` appears only when there are conflicts. Raw captures retain detailed provenance; normal repository modules do not repeat Place labels. Missing or opaque observations are not assumed to contain the same code.

Natural instance paths take priority over generated variant names. A colliding variant gets a hash suffix, followed by a numeric suffix if needed. Recovery keeps previously assigned names where they remain available. If a later capture takes an old variant name for a natural path, `citation_remaps` links the displaced variant to its new path using the original path, class, encoding and bytecode hash.

## Official download

```sh
python scripts/rcr.py fetch TARGET --out DIR [--kind experience|asset] [--api-key-env NAME] [--asset-version N]
```

Fetch downloads an officially accessible artifact and writes its report. It does not run recovery or use alternate domains, proxies (including environment proxy settings), or session-cookie workarounds. Permission denials and API errors remain in the report and are final: a location in a denied response is not followed. Only a body that the artifact parser used by `inspect` accepts as a Roblox binary or `<roblox>` XML document is saved as an artifact; a truncated body, a body shorter than its `Content-Length`, or a transport error is a failed request. Public metadata that the API returns as an error object is recorded as an error, not as metadata. `--asset-version` must be a positive integer. Run `inspect FILE` to inspect a supplied local file, then `recover FILE --out DIR` if recovery is wanted.

## Regression

```sh
python scripts/rcr.py regression CAPTURE --baseline BASELINE.json --out DIR [--tools FILE | --tools-dir DIR]
```

Recovers a saved capture into a new `DIR` and compares it with an explicit baseline. The baseline is a JSON object whose `payloads` maps each decoded bytecode SHA-256 to the expected `{"status": RECOVERY, "source_sha256": HASH}`; optional `cfg_certificates` maps a bytecode SHA-256 to the expected CFG audit (`status`, `matched_functions`, `total_functions`, `validated_original_protos`). The result in `DIR/evidence/regression.json` fails on mismatched, missing or unexpected payloads, input errors, lost CFG certificates, a failed recovery, or a script whose requested checks did not complete cleanly (`incomplete_checks`: a compiler that could not run, a failed CFG audit level, or a `CFG_MISMATCH`). It checks recovery states and hashes, not behavior.

## Evidence limits

The Windows collector reads supported replicated LocalScript/ModuleScript containers under bounded ABI and format assumptions. It does not collect server-only code, establish script execution, or prove complete replication. HTTP metadata, a successful window launch, syntax validity, CFG checks and game behavior are distinct evidence. Preserve missing buffers, decoding failures, and partial coverage as such.
