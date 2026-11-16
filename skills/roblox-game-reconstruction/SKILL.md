---
name: roblox-game-reconstruction
description: Reconstruct implementation specifications from a recovered Roblox Luau repository using parallel analysis, independent source-first omission discovery, connected rule/data traces, UNKNOWN rechecks and source/CFG evidence audits. Use after code recovery, not for collection, decompilation or API indexing.
---

# Roblox Game Reconstruction

Version **0.1.2**. Turn a recovered repository into an evidence-backed implementation specification. The default task includes source-first completeness review, cross-module traces and UNKNOWN rechecks as well as parallel analysis, evidence audit and packaging. Inventory or valid citations alone are not completeness.

Read [input and stages](references/workflow.md) first. Inputs are a `roblox-client-reverse` repository or a run containing `repository/report.json`. Work on an immutable input and a separate new run directory. No network, game connection or recovered-code execution is needed.

## Execute the workflow

1. Run `python scripts/reconstruct.py prepare --repo INPUT --run NEW_RUN`. Read `shared/summary.json`, `modules.json`, `functions.json` and candidate data. Use existing shell search and source reads to discover this game's systems. The helper inventories paths and existing evidence; **the agent identifies semantic systems**. Do not impose a previous game's system names, counts or progression.
2. Write `catalog.json` and `analysis-plan.json` under the run using [schemas](references/schemas.md). Assign every captured module an owner or an explicit exclusion/coverage gap. Separate framework/vendor/presentation code from gameplay. Derive system boundaries from this input, dependencies and state ownership.
3. Use available subagents concurrently for independent substantial slices. Follow [worker roles and handoffs](references/parallel-analysis.md) and [worker prompts](references/worker-prompts.md). Give each worker only the skill, its assignment, relevant repository paths and shared registry; give it one output directory. Keep formulas/state ownership in one slice and describe cross-system interfaces. Schedule further waves when slots are limited. If delegation is unavailable, record that limitation and perform sequential analysis; do not claim parallel or independent testing.
4. Create worker files with `scaffold --run RUN --kind analysis --system SYSTEM`. It writes canonical `analysis/SYSTEM/analysis.json` and `spec.md` with actual scoped function IDs; it does not perform analysis. Workers finish the draft and run `validate-worker --run RUN --kind analysis --system SYSTEM` before handoff. Trace connected interactions: trigger, state/data, conditions, values/units, actual receiver/callee, result/effects and lifecycle. Follow require bindings. A table without its consumer or a guard without its action remains an open relation. See [completeness contracts](references/completeness.md).
5. Assign a separate omission scout raw source and a bounded scope **before showing the specification**. Use `scaffold --run RUN --kind source-review --id REVIEW --module-id MODULE_ID --reviewer NAME` (repeat module IDs), finish its source-only draft, and `validate-worker --run RUN --kind source-review --input FILE`. `freeze-review` independently runs that preflight before writing a pin. Only then compare against all relevant systems, checking actual callee bindings, ownership and guard/default counterexamples before accepting findings. Integrate every observation/relation into `semantic-coverage.json`, recheck UNKNOWNs and account for unexamined functions. See the [worker prompts](references/worker-prompts.md). Preserve source/bytecode conflicts separately; do not edit recovered source to make a specification look complete.
6. Run `python scripts/reconstruct.py audit --run RUN`. It checks evidence integrity and the declared semantic-coverage ledger: unmapped material observations/relations, untraced boundaries, false completion and unaudited UNKNOWNs cannot silently pass. It cannot discover all missing semantics automatically or prove a mapping's prose is true. Fix records and actual omissions; retain explicit partial scope and genuinely unavailable facts. Reviewer judgments and counterexamples remain necessary.
7. Run `python scripts/reconstruct.py package --run RUN --out NEW_OUTPUT`. Inspect top-level `analysis_status` and `completion` as well as `status`. HTML, Markdown and JSON show reviewed/total functions, material relations not traced and open questions. An evidence audit can pass while analysis remains PARTIAL. Validate the package and deliver the directory and ZIP with actual coverage and limitations.

## Trust boundaries

- Distinguish `CODE_OBSERVED`, `STATIC_INFERENCE`, `UNVERIFIED_BEHAVIOR` and `INDEPENDENT_DESIGN`. Derived rules need steps and assumptions; independent designs are not recovered facts.
- Source syntax success is not semantic equivalence. Function body CFG evidence assumes its recorded external environment; it does not certify a module or server execution. Keep `BEHAVIOR_VERIFIED` out of third-party static-analysis claims.
- Purchase order, presentation state and a remote request do not prove prerequisites, committed server effects, reset retention or on-disk schema. Do not guess absent attributes, timers or server handlers.
- Same method name does not imply same API. Use existing require/constructor/assignment context to distinguish wrappers from engine calls. There is no API index or new call-graph engine in this skill.
- Keep literal spellings, units, numeric payloads and dynamic expressions. Constructor extraction does not evaluate a module's final state. Optional [literal bytecode reuse](references/existing-scripts.md) accepts only the already-tested literal subset and never certifies an entire function.

## Installation and evaluation

Copy the whole skill directory into the host's skill directory; see [INSTALL.md](INSTALL.md). Python 3.9+ standard library is sufficient. Multi-agent orchestration is supplied by the host agent, not a background CLI service. The helpers do not modify the input repository or `roblox-client-reverse`.

For regression/independent verification, follow [validation and leakage control](references/validation.md). Keep completed reference specifications outside blind workers' allowed inputs, freeze blind results before comparison, and distinguish full-run inventory from sampled semantic validation. Do not claim another game or a full independent workflow was tested unless logs and artifacts show it.
