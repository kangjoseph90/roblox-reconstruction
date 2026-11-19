# Native decompiler

`setup` builds the pinned Luau compiler, patched luaudec, the fixture VM and the native CFG/emitter invariant tests. The build pins are in `scripts/bootstrap_tools.py`, and `assets/luaudec-source/` preserves the modified native sources. See [installation](../INSTALL.md) and [tool configuration](formats.md#trusted-local-tool-configuration).

## Lifting and emission

- The lifter uses reachability, dominators, post-dominators and loop boundaries to select closed branch regions and structure loop exits. Return, break and continue terminate their lifted ranges.
- Boolean materialization distinguishes condition contexts from value contexts. For example, `(x and true) or false` produces a Boolean, while a value-carrying short-circuit expression preserves its operand values.
- At joins and loop exits, the lifter preserves incoming values needed on bypass paths. A register write on one successor cannot establish that the register is overwritten on every path to the merge.
- The emitter inserts statement separators where needed. Undefined-register diagnostics are emitted after lifting so they do not alter speculative condition matching.

## Output and checks

Recovery stores the original bytecode and the generated source with its diagnostics. A compiler check establishes syntax validity. A bundled-tool CFG audit compares the original bytecode with the recompiled candidate. See [CFG verification](cfg-verification.md) for the comparison rules and the scope of each result.

`setup` runs `assets/native_tests.cpp`, which prints the number of CFG/emitter invariants that passed.

## Bundled assets

- `assets/luaudec-v0.3.5.patch` is the patch `setup` applies; `luaudec-source/` and `luaudec-source-manifest.json` preserve and hash the resulting sources. The older `v0.3.2`/`v0.3.3` patches are kept as history and are not applied.
- [`LUAUDEC-NOTICE.md`](../assets/LUAUDEC-NOTICE.md) records the upstream decompiler's license status.
- `assets/fixtures/` and `assets/interop-fixtures/` are authored inputs for tests and experiments.
- `assets/studio-export.luau` is a Studio command-bar snippet that returns an `rcr-dump/v1` JSON string for an owned or permitted place.
- `assets/owned-luau-host.cpp` with `tests/run_host_experiment.py`, and `tests/run_experiments.py`, rebuild and exercise owned fixtures; `tests/compare_snapshots.py` compares two raw capture reports. `assets/rbx-dom.Cargo.lock` pins the optional rbx-dom utility build.

The authored semantic suite exercises Boolean values and truthiness in `tests/semantic/cases/boolean_vs_truthiness.luau`. `tests/test_initializers.py` checks string, Boolean and numeric defaults across O0/O1/O2 and debug levels 0/1. These fixtures exercise lifting and emission independently of recovered game behavior.
