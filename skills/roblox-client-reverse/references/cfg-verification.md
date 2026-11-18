# CFG verification

With the bundled tools, recovery recompiles a syntax-valid decompilation at O1 and O2 and compares each result with the original bytecode. `cfg-audit/` preserves the instruction and prototype metadata, recompiled bytecode, and per-optimization comparisons. The report prefers module certification, then the highest number of validated functions. It keeps syntax, CFG and behavior evidence separate.

## Exact and relational comparisons

`scripts/cfg_verify.py` compares reachable instructions and referenced prototypes. The exact certificate allows bijective register renaming, changed constant-pool indices, unconditional jump threading, predicate inversion with successor exchange, and ignored coverage/prologue instructions. It checks call arguments, return arities, full string bytes, and distinct prototype references and cached closure/table slots. FASTCALL builtin IDs and both edges, including the guarded fallback call, remain part of the comparison.

When the exact graph does not match, `scripts/cfg_normalize.py` compares paired symbolic executions. It normalizes administrative copies, exact scalar loads, Boolean materialization and effect-free branch regions into reaching values and Boolean path partitions. Every abstract path must be covered. Certification compares transitions and ordered effects across the two programs, so register numbers and basic-block boundaries may differ.

Calls, reads/writes, allocation, unknown arithmetic and comparisons, metamethod-capable operations and loop primitives remain ordered. Arguments, result arity, constants, branch outcomes and subsequent data uses must match. Only exact small-integer constant arithmetic is folded. Loops close when the paired live-value and alias invariant repeats and all outgoing transitions have been checked. Generalizing opaque results discards path facts conservatively.

Closure comparison preserves ordered capture modes and values, along with prototype and cached-object identities. REF captures and recursive self capture require an exact graph match. The native exporter provides raw little-endian numeric payloads for NaN and vector constants. The exact comparison requires those bits. The relational comparison leaves those constants unsupported.

## Reading the result

| Status | Meaning |
|---|---|
| `CFG_VALIDATED` | A certificate was established within the comparison model. A whole-module certificate requires the entry point and all referenced prototype and cache identities. |
| `CFG_MISMATCH` | A concrete witness distinguishes the compared bodies in the supported pure-bytecode subset. A candidate-pair witness needs entry/parent correspondence or an independent reproduction to establish a decompiler error. |
| `UNVERIFIED` | No certificate was established, for example due to an unsupported opcode, unresolved correspondence, exhausted analysis budget or tool failure. Tool and audit errors appear in `cfg_audit.failure`. |
| `NOT_CHECKED` | No CFG audit was performed, for example because a custom adapter did not enable `cfg_audit`. |

`scripts/cfg_diagnose.py` ranks candidate function correspondences. A similarity score only selects comparison candidates. Standalone function proofs assume the same ordered external upvalues and cache environment. They do not certify the parent bindings, and a matching body does not certify an entire module. A counterexample in an established module context takes precedence over a standalone match.

The model assumes the same external environment, coupled call outcomes, ordered upvalue slots, and no asynchronous mutation between instructions. It excludes debug/source/stack observations, timing, profiling, coverage, allocation failure and engine scheduling. The audit leaves recovered game behavior as `NOT_TESTED`.

## Authored suites

In addition to the [development checks](../INSTALL.md#development-checks), run these with the bundled tool configuration:

```powershell
python tests/semantic_expanded/run_semantic_expanded.py --tools work/.rcr-tools/tools.json --out work/expanded
python tests/normalizer_adversarial/run_matrix.py --tools work/.rcr-tools/tools.json --out work/adversarial
```

The expanded suite records deterministic seeds, bytecode round trips, CFG results and observed behavior separately. The adversarial matrix uses equivalent and deliberately inequivalent pairs to check for false certificates and report inconclusive cases. The VM runs authored fixtures with recorded inputs and observations.
