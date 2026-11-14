# v0.3.3 normalized checker adversarial matrix

Run with:

```powershell
python tests/normalizer_adversarial/run_matrix.py --tools work/tools/tools.json --out work/adversarial
```

The matrix contains 198 authored pairs: 99 intended equivalent and 99 intended inequivalent. Each pair is executed with the pinned Luau CLI, then compiled at a deterministic optimization level cycling O0/O1/O2 (same level for both sides), disassembled through the pinned native `luaudec`, and checked with `ModuleProof`. The runner wraps each authored function so all return values are printed, supplies `(7, 11)` to vararg functions, and defines observable `f`/`g` stubs to expose call order. Runtime source paths are normalized before comparing errors. No random inputs are used.

`verified/<pair>/` retains both authored sources, runner wrappers, bytecode files, native JSON disassemblies, and `result.json` with observed outcomes, hashes, and the exact proof diagnostic. `summary.json` retains pinned tool metadata and aggregate classifications.

## Results

| Proof status | Pairs | Meaning |
|---|---:|---|
| `CFG_VALIDATED` | 81 | Accepted; every accepted pair had equal observed outcomes. |
| `CFG_MISMATCH` | 45 | The checker reported a concrete mismatch; all 45 had different observed outcomes. |
| `UNVERIFIED` | 72 | No proof either way; 18 were observed equivalent and 54 differed. Do not count these as proven inequivalent. |

There were no expected-outcome oracle mismatches and no observed false positives in this matrix. The 18 equivalent `UNVERIFIED` cases and 54 different `UNVERIFIED` cases remain separately listed in `summary.json`.

Coverage includes copy/register renaming, Boolean materialization, numeric loops, call order, metamethod arithmetic and comparison, table aliasing, varargs/multiple returns, closures, errors, and coroutines. Several accepted equivalent cases reached the pinned exact bytecode fallback; see each `result.json` for the method.

This is a finite authored matrix, not a general soundness proof. It executes only controlled fixtures with the pinned local Luau tools; it does not involve game captures, reconnection, or external code.

The packaged runner invokes authored returned numeric closures with argument 10 before comparing their results; raw function addresses are not used as the outcome oracle. Repeated product states at acyclic joins do not imply loop structure.
