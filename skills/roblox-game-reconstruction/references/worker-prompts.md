# Self-contained worker prompts

Replace braces from the discovered catalog, not from a previous game's system list. Every prompt supplies only permitted local evidence and an isolated output directory.

## Domain worker

Use the skill at `{skill}`. Analyze only `{system_id}` with modules `{module_ids}` in recovered repository `{repo}`. Read `{run}/shared` and the assigned catalog entry. Write only `{run}/analysis/{system_id}`. Other workers own `{adjacent_systems}`; consume their published interfaces rather than redoing them. No game connection, web/wiki expansion, recovered-code execution or input edits.

Create `analysis.json` and an implementation-ready `spec.md` using references/schemas.md. Trace actual functions: state, inputs, outputs, branches, units/formulas, effect order, transitions/unlocks, reset/storage reads vs writers, remote parent/name/argument tuple, errors, yielding/reentry and cleanup. Extract content values with provenance, retain dynamic expressions and numeric precision limits. Use explicit function IDs from shared/functions.json for body certificates; original and recompiled proto indices are not interchangeable.

Record each claim as CODE_OBSERVED, STATIC_INFERENCE, UNVERIFIED_BEHAVIOR or INDEPENDENT_DESIGN. State what the code actually proves. Missing server logic or instance attributes are unknowns, not invented defaults. Link every sourced claim/contract/test expectation to evidence. Tests are specified/unexecuted unless there is a separately logged authored test; do not run game candidates. Enumerating modules or prose features alone is insufficient.

Send boundary interfaces and important contradictions early. Persist progress and remaining functions before context runs out. Report coverage honestly; do not claim complete analysis merely because all JSON files exist.

Apply references/completeness.md: record connected observations/relations, actual receiver/import bindings and content consumers. Include every registry function in your scope with its actual analysis state. Add semantic_refs to claims/contracts after the coordinator assigns obligation IDs. Recheck UNKNOWNs with recorded bounded searches and inspected functions. A displayed value or number table without its input/unit/action relationship is an open trace.

## Independent omission scout — source-only first

Use only `{skill}`, immutable `{repo}`, `{run}/shared`, entrypoints `{entrypoints}` and your new output directory `{output}`. Do not read prior specifications, claims, reference answers, parent conversation or other analysts' results. Read references/completeness.md. Follow material interactions from source, including state/conditions, data consumers, actual receivers/imports, calls/effects and lifecycle. Seek alternatives and counterexamples. Use existing shell search and registry, not new analysis tooling. Account for traced and untraced functions and cross-module boundaries. Write source-review/v1 observations, explicit relationships, source citations, limits and actual cost to `{output}/source-first.json`, with a source-read/access log. Do not include rule IDs or specification mappings. Stop before comparison. The coordinator freezes this artifact, then supplies the specification in a separate task.

## Omission comparison — only after freeze

Compare frozen `{source_review}` with `{specification}`. Preserve frozen bytes. For each observation AND relationship, identify a matching implementation rule, a real omission, contradiction, excluded low-impact detail, or precisely scoped UNKNOWN. Check what the claim actually says, not just whether the source is cited. Look for disconnected endpoints, wrong receiver/units/default/effect order, and unavailable server behavior incorrectly claimed from client intent. Produce mappings, before/after correction proposals and false alarms with reasons. Keep newly discovered facts in an additional review, never silently edit the frozen inventory. Log adjudicated omissions, false positives and known missed sentinels separately. Integration must retain every source obligation and reciprocal semantic_refs in the actual claims.

## Cross-system reviewer

Use the skill at `{skill}`. Review analysis outputs `{systems}` against `{repo}` and `{run}/shared`. Write findings only under `{review_output}`; do not rewrite domain files. Check concrete high-impact rules and cross-system interfaces: formulas/units/defaults, initial vs reactive gates, request vs server commit, endpoint receiver/parent, time/expiry interpretations, reset/retention and content completeness. Include source evidence and affected claim IDs for each finding. Distinguish true contradiction, scoped difference, missing coverage and uncertain source behavior. Compare critical suspicious source paths with existing original/recompiled instruction JSON when available; preserve source and record narrow evidence, not a broad equivalence claim. Summarize checked scope and untested areas. Do not use completed reference answers in a blind run.

## Independent workflow evaluator

Use only the skill at `{fresh_skill}` and recovered repository `{raw_repo}`. In `{new_run}`, perform the default skill workflow through discovered systems, parallel analysis, separate cross-review, audit and package. You may use existing shell search tools. The input allowlist excludes previous completed specifications, domain analyses, baseline summaries and the parent chat. Do not read them or contact authors for intended answers. Log input paths/hashes, source reads, worker scopes and exact commands. Report actual completion and limitations. Freeze and hash output before a separate baseline comparator receives it.

## Baseline comparator (after freeze)

Read frozen candidate `{candidate}` and reference `{reference}`. Do not alter blind artifacts. Compare input/hash coverage, system concepts and explicit exclusions, high-impact rules/formulas, content rows/units, evidence integrity and unknown authority. Counts are descriptive, not target quotas or proof of correctness. Identify omissions and disagreements with evidence; a new run may group systems or split claims differently. Keep full-data mechanical replay, blind semantic samples and full independent end-to-end runs separately labeled.
