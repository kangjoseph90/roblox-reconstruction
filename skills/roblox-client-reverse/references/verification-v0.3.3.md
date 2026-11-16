# Verification v0.3.3

`scripts/cfg_verify.py` retains the v0.3.2 exact reachable instruction/prototype certificate and adds `cfg_normalize.py`. `cfg_diagnose.py` ranks possible function correspondences for diagnostics; similarity never grants a certificate. The compiler and native JSON exporter remain pinned by setup. Every comparison preserves both bytecodes, native instruction metadata, optimization level and function results.

The new checker constructs a product of two symbolic executions. Administrative copies, exact scalar loads, Boolean materialization and effect-free branch regions are normalized into reaching SSA values and Boolean path partitions. Every abstract path must be covered. Register numbers and basic-block boundaries need not match. Reachability, basic blocks, dominators, post-dominators and natural-loop boundaries are computed; certification uses relational transitions rather than graph shape alone.

Calls, reads/writes, allocation, unknown arithmetic/comparisons, metamethod-capable operations and loop primitives remain ordered events. Arguments, result arity, constants, branch outcomes and subsequent data uses must match. FASTCALL identities and guarded fallback paths remain observable in the model. Only exact small-integer constant arithmetic is folded; unknown arithmetic is not reordered. Loops close only when the paired live-value/alias invariant repeats and all outgoing transitions have been checked. Generalizing opaque results discards path facts conservatively; exhausted budgets remain `UNVERIFIED`.

Closures retain ordered capture modes/values and bijective prototype and cached-object identities. REF captures and recursive self capture require the exact fallback; normalized lifetime reasoning for those patterns is unsupported. Native numeric metadata now preserves raw little-endian payloads, including NaN/vector bits; missing payload metadata never certifies those values. NaN/vector normalization is still unsupported. Other unsupported operations (including FASTPCALL), unresolved correspondence and bounded-analysis exhaustion stay `UNVERIFIED`.

Each function has independent syntax, CFG and behavior fields. Standalone function certificates assume the same ordered external upvalue/cache environment and do not prove its surrounding capture bindings. Module certification requires the entry and every referenced prototype/cache identity to discharge. Some individual body matches never certify an entire module. Context-derived counterexamples take precedence over a body match elsewhere.

`CFG_MISMATCH` requires a concrete witness in the explicitly interpreted pure-bytecode subset; opaque symbolic differences are `UNVERIFIED`. Heuristic function pair witnesses prove that those two bodies differ, not that they are corresponding original/recovered functions. A decompiler-error claim needs entry/parent correspondence or an independently controlled reproduction. No game code is executed by the checker and game behavior remains `NOT_TESTED`.

These are certificates under a specified VM model, not a general mathematical proof of all Luau/Roblox behavior. Assumptions: same external environment and coupled call outcomes, ordered upvalue slots, no asynchronous mutation between VM instructions. Exclusions: debug/source/stack location observations, timing/profiling/coverage, allocation failure and engine scheduling differences. Behavior tests cover only their recorded controlled inputs, state/output/error observations; they do not prove all inputs or third-party behavior.

## Native semantic correction

The lifter previously reused truthiness-only `a and true → a` / `a or false → a` rewrites in value-producing contexts. At O0, `(x and true) or false` could recover as `x`, returning `7` instead of Boolean `true` or `nil` instead of Boolean `false`. The lifter now separates condition and value contexts and emits Boolean coercion when materializing a Boolean. Value-carrying short-circuit expressions keep their operands. This is an AST/lifting correction, not source-text repair. `tests/semantic/cases/boolean_vs_truthiness.luau` checks nil, false, true, zero, numbers and strings at O0/O1/O2.

## Automated checks

The v0.3.5 lifter also preserves pending incoming values when a construct writes their registers and the merge still needs them. A successor's live-in set is insufficient: that successor may overwrite the register while a bypass path retains its pre-branch value. Keeping a redundant literal on fully overwriting paths is conservative; removing it requires an all-path definite-assignment proof. `test_initializers.py` checks authored string, Boolean and numeric defaults at O0/O1/O2 and debug levels 0/1. This is independent of syntax or game behavior certification.

```powershell
$env:RCR_TEST_TOOLS=(Resolve-Path work/tools/tools.json).Path
python -X utf8 -m unittest discover -s tests -v
python -X utf8 tests/semantic/run_suite.py --tools work/tools/tools.json --out work/semantic.json
python -X utf8 tests/semantic_expanded/run_semantic_expanded.py --tools work/tools/tools.json --out work/expanded
python -X utf8 tests/normalizer_adversarial/run_matrix.py --tools work/tools/tools.json --out work/adversarial
```

The expanded suite retains deterministic generator seeds and bytecode round trips plus CFG and observed behavior separately. The adversarial matrix retains equivalent and deliberately inequivalent pairs, rejects observed false certifications, and reports false-negative/unknown cases without promoting them.
