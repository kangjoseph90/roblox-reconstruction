# Acquisition and offline inputs

Acquisition and recovery are separate operations. Live `capture` stores one raw Place observation and never builds native tools or decompiles. `recover` verifies inputs, runs the recovery pipeline, and writes source. `fetch` downloads an officially accessible artifact only; it does not recover it.

## Live Place capture

```sh
python scripts/rcr.py places PLACE_ID --out GAME
python scripts/rcr.py open PLACE_ID --game GAME
python scripts/rcr.py screenshot --pid PID --out player.png
python scripts/rcr.py capture --pid PID [--place PLACE_ID] --game GAME
python scripts/rcr.py recover GAME
```

Capture is scoped to one Place and saves one raw capture under `GAME/captures/<PlaceID>/`, containing `client-dump.json`, `originals/`, `bytecode/`, `evidence/`, `report.json`, and `files.json`. It checks that the observed Place and Universe match the catalogue. Existing captures are never silently overwritten. Normal process exit is not a successful capture; inspect the report's partial/missing/unknown scope and access errors. See [player-acquisition.md](player-acquisition.md) and [adaptive-builds.md](adaptive-builds.md).

## Recovery

`recover GAME` updates only `GAME/repository` and `GAME/recovery.json` from all available Place captures, using the same pipeline whether one or several Places were captured. It does not create a recovered repository per Place or a permanent merge copy. `recovery.json` records changes, including `citation_remaps` and `added_modules`; use these to update affected citations only where the matched original code supports them. Refresh the map and follow up on newly added or changed scope. Do not rewrite analysis claims automatically.

Recovery accepts only hashed, approved original buffers from raw captures. Embedded source and decoded convenience fields are ignored. Supplied older raw capture directory layouts are input adapters, not separate CLI commands. Offline sources are recovered with `recover INPUT --out DIR`, optionally with `--tools`/`--tools-dir`, `--include-server`, and `--bytecode-format` (`luau`, `roblox`, or `unknown`). Unknown or opaque content remains unverified.

Prior module results are reused only when bytecode and encoding match exactly, the previous repository passes integrity validation, and tool/configuration/code fingerprints match. Distinct paths keep their identity while expensive processing can be reused. Modules at the same logical path merge only when bytecode, encoding and class match. Conflicting versions receive `@PlaceID`; if multiple conflicts share a Place, a short hash disambiguates. `variants.json` appears only when there are conflicts. Raw captures retain detailed provenance; normal repository modules do not repeat Place labels. Missing or opaque observations are not assumed to contain the same code.

If a generated variant name collides with a pre-existing module name, recovery stops rather than silently aliasing different modules. The raw captures and existing repository are retained.

## Official download

```sh
python scripts/rcr.py fetch TARGET --out DIR [--kind experience|asset] [--api-key-env NAME] [--asset-version N]
```

Fetch downloads an officially accessible artifact and writes its report. It does not run recovery or use alternate domains, proxies, or session-cookie workarounds. Permission denials and API errors remain in the report. Run `inspect FILE` to inspect a supplied local file, then `recover FILE --out DIR` if recovery is wanted.

## Evidence limits

The Windows collector reads supported replicated LocalScript/ModuleScript containers under bounded ABI and format assumptions. It does not collect server-only code, establish script execution, or prove complete replication. HTTP metadata, a successful window launch, syntax validity, CFG checks and game behavior are distinct evidence. Preserve missing buffers, decoding failures, and partial coverage as such.
