# Native decompiler

`setup` builds the pinned Luau compiler, patched luaudec, the fixture VM and the native CFG/emitter invariant tests. The build pins are in `scripts/bootstrap_tools.py`, and `assets/luaudec-source/` preserves the modified native sources. See [installation](../INSTALL.md) and [tool configuration](formats.md#trusted-local-tool-configuration).

## Lifting and emission

- The lifter uses reachability, dominators, post-dominators and loop boundaries to select closed branch regions and structure loop exits. Return, break and continue terminate their lifted ranges.
- Boolean materialization distinguishes condition contexts from value contexts. For example, `(x and true) or false` produces a Boolean, while a value-carrying short-circuit expression preserves its operand values.
- At joins and loop exits, the lifter preserves incoming values needed on bypass paths. A register write on one successor cannot establish that the register is overwritten on every path to the merge.
- The emitter inserts statement separators where needed. Undefined-register diagnostics are emitted after lifting so they do not alter speculative condition matching.

## Output and checks

Recovery stores the original bytecode and the generated source with its diagnostics. A compiler check establishes syntax validity. A bundled-tool CFG audit compares the original bytecode with the recompiled candidate. See [CFG verification](cfg-verification.md) for the comparison rules and the scope of each result.

The authored semantic suite exercises Boolean values and truthiness in `tests/semantic/cases/boolean_vs_truthiness.luau`. `tests/test_initializers.py` checks string, Boolean and numeric defaults across O0/O1/O2 and debug levels 0/1. These fixtures exercise lifting and emission independently of recovered game behavior.
