# Stages and completion conditions

## Input contract

Provide a recovered repository with `report.json` containing `scripts[]`. A run root with `repository/report.json` is also accepted. Each source record identifies `id`, `instance_path`, `class`, relative `source`, `source_sha256`, and `bytecode.sha256`. This v1 adapter uses the RCR content hash as identity and does not accept a source-only record without that declared hash. The raw `bytecode.path` and existing `cfg_audit.evidence`/`best_optimization` are optional; when supplied they identify original bytes, original/recompiled JSON and comparison certificates. Paths must stay inside the supplied repository. Preserve record-level capture errors and source-less instances.

Rich inputs include `original.bytecode`, `cfg-audit/original.json`, `recompiled-O1/O2.json` and `comparison-O1/O2.json`. The skill consumes these; it does not rebuild or re-decompile. Without instruction metadata, code can be described but no new bytecode-based correction/certificate can be asserted. Without a comparison mapping, an original proto index or matching name is not enough to associate a certificate with source.

## 1. Inventory

Input: immutable recovered repository. Output: run `config.json`, shared source/module/function registry, literal constructor ASTs, original numeric constants and static require/call/attribute candidates. These are reused extraction heuristics, not a new semantic index or call-graph engine. Module paths and require expressions are leads; workers verify meaningful relationships in source.

Done: all report records accounted for, source hashes checked, missing bytecode/CFG and parse errors recorded, source copies preserved. A function's declaration/own-instruction line interval is not its complete lexical extent. Duplicate content can share one source snapshot while instance identities remain separate.

## 2. Discovery and plan

Input: shared inventory and targeted source reads. Output: `catalog.json`, `analysis-plan.json`. Identify systems from this repository's module clusters, state ownership and actual callers. Include cross-cutting framework, configuration and presentation groups only where appropriate. An input need not have a building, reset or farming system.

Done: every module ID appears in exactly one owning assignment or an explicit exclusion/coverage gap; no overlapping write scopes. Worker system IDs are filesystem-safe and unique. Explain group discovery and interface boundaries. A module can be a dependency of many systems while only one worker owns its primary analysis. Run check-plan before dispatch; the same ownership checks run in audit. The CLI checks IDs/ownership, not the semantic quality of system discovery.

## 3. Parallel implementation analysis

Input per worker: skill instructions, assignment, relevant source paths, shared registry, output path. Output: `analysis/<system>/analysis.json` plus `spec.md`, using schemas.md. Workers may add clearly named content/contract JSON referenced by their analysis. No global context dump or reference answer is needed.

Done: all assigned meaningful functions traced or marked unresolved; each implementation claim and contract has source evidence. Describe exact data flow, predicates, units, operation order, boundaries, async/reentry/cleanup and endpoints. Coverage records distinguish analyzed functions, dependencies and unexamined helpers. TRACED/REVIEWED require per-function inputs, ordered cited steps, outputs and limits; generic domain rationales cannot stand in for traces. Enumerating modules alone is not completion.

## 4. Cross-validation and reconciliation

Input: worker artifacts and source evidence. Output: `review.json`, updated claims, `conflicts.json`. Compare shared data definitions, formula units, argument order, default branches, place overrides and reset/authority assertions. Inspect coverage gaps and critical unverified functions. A separate reviewer should be independent of the domain author; if unavailable, mark coordinator-only review honestly.

Done: every worker slice has a review disposition; every conflict has a resolution or a tracked unknown with affected claims and impact. A source/bytecode conflict is a separate record with original/recompiled proto/PC evidence. No helper edits recovered source or promotes local branch reasoning to whole-function CFG/behavior proof.

Since 0.1.1, a separate source-only scout first inspects bounded raw source without seeing claims. Freeze its source-review/v1 inventory via `freeze-review`, then expose the specification for comparison. Use scaffold --kind integration and integrate --input FILE for the explicit-decision join. Every observation and relation must survive in semantic-coverage.json with its origin/disposition; the CLI does not make semantic comparison decisions. Follow references/completeness.md for function denominators, connected traces, content bindings and UNKNOWN investigations. Do not give a suspected regression's answer to the scout. Cross-review includes discovery of rules absent from the specification, not just checking existing citations.

## 5. Evidence audit

Input: immutable registry, explicit `function_id` citations and worker/review JSON. Output: `audit.json`, canonical `rules.json`. The auditor checks file/hash/line/source-body association and derives CFG from the existing registry. It keeps worker claims separate from computed evidence. Missing or malformed citations fail; missing certificates remain UNVERIFIED. A selected source span must be within the cited body's recorded instruction-line scope; split broader claims into separate citations.

Done: no integrity/schema errors; all claims cite source or explicitly identify an independent design with requirements. Review checks semantics separately. Source text can be syntactically valid yet wrong, so inspect suspicious control flow and reused variables against existing original metadata when available.

Since 0.1.1, audit additionally writes semantic-audit.json. It checks frozen-obligation retention, reciprocal rule mappings, scope/function accounting, UNKNOWN investigation and content bindings. It does not infer semantics or prove prose entailment. A passing PARTIAL audit permits packaging explicitly recorded gaps; it must not be reported as IN_SCOPE_REVIEWED. A run without a semantic ledger fails the audit.

## 6. Packaging

Input: completed catalog, plan, analysis, review, conflict/unknown ledger and passing audit. Output: implementation specification, system catalog, rules, content, interfaces, relationship graph, contract vectors, unknowns, coverage, evidence snapshots, manifest and ZIP.

Done: package validation passes, source/bytecode snapshots and certificates survive relocation, all system summaries link to their analysis, tests distinguish specified vs executed, and final coverage is reported. Unknown server reset/save/award policies are legitimate blockers, not blanks to fill with plausible defaults. A mechanically valid package is not automatically an implementation-complete game specification.

Preserve semantic-coverage.json, semantic-audit.json and frozen source-reviews in the package. implementation-spec.md renders explicit relationships with mappings and remaining work; summary.json reports function states and material obligation dispositions. Include unresolved boundaries in implementation handoffs so missing mechanics cannot silently become independent design.
