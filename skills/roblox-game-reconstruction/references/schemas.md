# File contracts v1

0.1.1 adds the source-review/v1 and rgr-semantic-coverage/v1 contracts in [completeness.md](completeness.md). These are required for newly prepared runs; the ordinary file contracts below remain compatible. Claims/contracts use `semantic_refs:["obligation-id"]` and the ledger maps those IDs back to canonical `system:claim-id` entries. Every content item has a data_bindings disposition. Every unknown has an unknown_audits record, including NOT_INVESTIGATED when work remains.

0.1.2 provides scaffold and validate-worker. Generated drafts carry `draft:true`, actual system/module/function IDs and empty semantic fields. Finish the analysis and set draft false before validation; the key must stay present, and removing it does not finish a worker. Citation `line_start`/`line_end` count LF-separated source lines (a final LF does not start another line), the same numbering as the packaged source pages. Analysis `coverage.functions[]` must account for every scoped registry function. The older function_ids_analyzed field may be retained as metadata but cannot replace function accounting. 0.1.3 requires the function trace contract below for TRACED/REVIEWED states. Source reviews require every scoped function, citation scope and trace fields to validate before freeze. A relation with unresolved attribution uses `trace_status:UNRESOLVED` plus trace_limit instead of invented function IDs. Validation never fills in rules or asserts completeness.

All files are UTF-8 JSON. Keep IDs stable within a run, paths repository-relative, and nonfinite values as strings. Use decimal/literal strings when JSON number conversion could lose precision. Extra domain-specific fields are retained. Empty arrays mean no observed entries, not a universal absence claim.

## Catalog and plan

`catalog.json`:

```json
{
  "title": "Name derived from this input",
  "systems": [
    {"id":"system-a","title":"Discovered system","module_ids":["m:actual-id"],
     "discovery_basis":"Specific paths, state owner and callers read in this repository"}
  ],
  "exclusions": [
    {"module_id":"m:another-id","reason":"Framework/presentation dependency; semantic analysis deferred","impact":"What is not specified"}
  ]
}
```

Every module, including missing-source entries, is owned once or explicitly excluded. Module IDs must exist in the input registry. Use nonempty unique system IDs made from ASCII letters, digits, hyphens and underscores; the first character is alphanumeric. Missing-source entries remain in shared inventory and unknowns. `analysis-plan.json` has `assignments[]` with `system_id`, `worker`, `output_dir` (`analysis/<id>`), `scope`, `dependencies`, `remaining_functions` (initially optional) and `mode` (`parallel_agent`, `sequential_agent`, or `coordinator`). These are actual execution records, not evidence that spawning occurred; preserve host logs separately.

## Worker analysis

`analysis/<id>/analysis.json`:

```json
{
  "system_id":"system-a",
  "claims":[
    {"id":"A-1","kind":"CODE_OBSERVED","entry_kind":"implementation_rule",
     "statement":"Concrete source-backed rule",
     "evidence":[{"source_file":"src/module/init.luau","function_id":"actual shared function_id","line_start":12,"line_end":18}]}
  ],
  "contracts":[
    {"id":"A-call","kind":"CODE_OBSERVED","signature":"Method(arg)","inputs":{},"state":{},
     "steps":["Actual ordered operations and guards"],"outputs":{},"errors":[],
     "transitions":[],"side_effects":[],"cleanup":[],
     "evidence":[{"source_file":"src/module/init.luau","function_id":"actual shared function_id","line_start":12,"line_end":18}]}
  ],
  "content":[],"interfaces":[],"relationships":[],"tests":[],"unknowns":[],
  "coverage":{"module_ids":["m:actual-id"],"functions":[{"function_id":"actual shared function_id","status":"UNANALYZED","remaining":[]}],"remaining":[]}
}
```

The example uses placeholders only to explain fields; real runs use inventory IDs and real claims. Include `spec.md` with detailed implementation procedures. Claims and contracts must not be prose-only module inventories.

Claim `kind` is `CODE_OBSERVED`, `STATIC_INFERENCE`, `UNVERIFIED_BEHAVIOR`, or `INDEPENDENT_DESIGN`. `STATIC_INFERENCE` needs `derivation` and `assumptions`; `INDEPENDENT_DESIGN` needs `rationale` and must be separated from recovered facts. Optional `entry_kind` distinguishes `implementation_rule`, `function_evidence_record` and `function_contract`; counting evidence records is not counting recovered gameplay rules. Worker contracts should include `kind` when they are inferred rather than directly observed.

Evidence requires `source_file`, `line_start`, `line_end`. `function_id` is the **recompiled source-body** ID in shared/functions.json. If no mapping exists, use `function_id: null`, give `function` and `mapping_limit`; the auditor leaves function CFG UNVERIFIED. A worker may provide expected `source_sha256`, `bytecode_sha256`, and `claimed_cfg`; mismatches are reported. Do not map an original proto index to source by numerical equality. A source span outside the body's recorded own-instruction range needs separate citations/unknown mapping, not an invented lexical bound.

Optional `optimization` selects an existing, pinned comparison artifact at O1/O2 (the levels roblox-client-reverse audits). The packager includes it; unavailable artifacts fail the audit. Function IDs remain bound to the registry's selected optimization. Selecting another artifact does not transfer its body/module certification and is labeled UNVERIFIED/UNKNOWN in that citation.

`content[]` entries: `id`, `value`, `units`, `evidence`, optionally `literal`, `expression`, `data_ref` and `fidelity`. Dynamic expressions remain strings with dependencies. Raw constructor files can be referenced instead of copied, but domain-specific interpretation must identify keys and consumers.

`interfaces[]`: `id`, `inputs`, `outputs`, `direction`, `state_owner`, `evidence` or `claim_ids`; remotes additionally need `endpoint` (`name`, `parent` expression/scope) and, beside it on the interface (not inside `endpoint`), `positional_args` and `unknowns` arrays and nonempty `result` and `authority_limit` text. `validate-worker` enforces these fields; an interface with `endpoint` is treated as a remote. Empty arrays/objects are valid for inputs/outputs. Wrapper identity must be traced before engine API meaning is applied.

`relationships[]`: `source`, `target`, `relation`, `claim_ids` or `evidence`, `scope`. Relation is explicit (`calls`, `reads`, `writes`, `order_precedes`, `display_dependency`, etc.). Do not turn observed order/display/request into `requires` or committed server state without evidence.

`tests[]`: `id`, `inputs`, `expected` (or explicit unknown), `claim_ids`/`evidence`, `status: SPECIFIED_NOT_EXECUTED`, `executed: false`. Authored reference-model executions belong in separate logged test evidence; do not relabel game behavior.

`unknowns[]`: `id`, `question`, `impact`, `missing_evidence`, optional affected `claim_ids` and `evidence`, and `status` (`OPEN` by default, or `RESOLVED`). Only an unknown whose `unknown_audits` record is `SOURCE_FOUND` with linked `resolved_rule_ids` may be `RESOLVED`; every other unknown stays open and keeps the analysis PARTIAL. Coverage lists the modules/functions actually analyzed and remaining gaps; listing an assigned module does not imply all of its functions are understood.

## Function analysis records

Every TRACED/REVIEWED entry in worker `coverage.functions`, source-review `functions` and final ledger `functions` requires:

```json
{
  "function_id":"actual source-body ID",
  "status":"TRACED",
  "trace":{
    "inputs":["Actual parameters, captured state or event input"],
    "steps":[{"action":"Specific guard, operation or effect in execution order",
              "evidence":[{"source_file":"src/module/init.luau","function_id":"actual source-body ID","line_start":12,"line_end":18}]}],
    "outputs":["Return value, mutation, request or event; distinguish server authority"],
    "limits":["Unresolved branch, receiver, environment or recovered-source uncertainty"]
  },
  "remaining":[]
}
```

Inputs/outputs/limits are arrays of nonempty descriptions and may be empty when appropriate. Steps must be nonempty; every step needs an action and citations to **that same function's** registered source-body range and source hash. Null/unmapped or another function's citations cannot support its analysis state. Cite a callee separately in its own trace/relationship. Follow meaningful guards, default/early-exit paths, data and effect order. If only a declaration or an isolated call was inspected, use SURVEYED; assignment, file reads, linked obligations and a generic rationale do not establish TRACED.

These checks establish a recorded, attributed analysis, not prose truth or exhaustive path coverage. Existing CFG/behavior status is never promoted. REVIEWED additionally requires a supported author trace and a supported trace for the same body in a frozen independent source pass; comparison/adjudication is still a human/agent judgment. An author or scout's status alone does not automatically promote final REVIEWED. source_review_functions_traced counts supported frozen trace records, not proof of reviewer independence; retain the access protocol and host records. Earlier records lacking traces fail the strengthened checks when re-audited; preserve old packages and frozen bytes, then perform follow-up analysis or lower unsupported coverage rather than inventing a trace.

A frozen review whose TRACED/REVIEWED function lacks a trace fails the audit (`semantic_source_review_trace_missing`); freeze-review always requires the current trace contract. Do not alter frozen pins or add invented traces; freeze a new source review instead.

Citation shorthand: within a function's own `trace.steps[].evidence`, `{"lines":[START,END]}` stands for that function's canonical citation. `normalize-worker` expands it (choosing the first source file in the worker's scope with that function's bytecode), writes the file and validates it; `validate-worker` rejects unexpanded shorthand (`citation_shorthand_not_normalized`). Shorthand is not accepted anywhere else, including claims and relations.

## Integration decisions

`scaffold --kind integration --run RUN` reads validated worker outputs and frozen source reviews and writes `integration-decisions.json` (`rgr-integration/v1`, `draft:true`). It namespaces frozen IDs as `review-id:item-id`, retains origins, and creates undecided mappings, UNKNOWN audits and content bindings. No completed decisions are generated. Finish the scope rationale, comparison mappings, investigations, bindings and draft flag; extra source-backed coordinator observations/relations may be added without altering frozen subjects.

Run `integrate --run RUN --input RUN/integration-decisions.json`. The file contains the ledger's scope/observations/relations/source_reviews/mappings/unknown_audits/data_bindings/completeness_claim plus `function_owners` and `reviewed_function_ids`. For a shared function ID across systems, explicitly select its primary system in function_owners; no highest-status heuristic resolves conflicting work. Reviewed IDs require both recorded traces and an actual comparison decision; the CLI checks support, not the comparison's truth.

The command imports worker traces, associates subjects by their cited function IDs, and rebuilds only claim/contract semantic_refs from explicit canonical `system:claim-id` mapping decisions. Local claim IDs can coincide in different systems. It validates the proposed ledger and frozen pins before writing semantic-coverage.json and the reciprocal metadata in analysis JSON. Validation failure writes neither. On repeat execution it rebuilds generated links, preserving source, frozen reviews, rule text and worker traces; filesystem write failures are not a multi-file transaction. Run the full audit afterward for rule/certificate/review/conflict checks. Keep integration-decisions.json in the run for reproducibility; package generation includes it when present.

## Review and conflicts

`review.json`: `mode` (`independent_agent`, `coordinator_only`, `mixed`), `reviewer`, `checked_system_ids`, `checked_scope`, `findings[]`, `limitations`. Each finding has `id`, `status` (`consistent`, `conflict`, `unresolved`, `resolved`, `missing_coverage`), `severity`, `affected_claim_ids`, `detail`, `evidence` when sourced, `resolution` or `unknown_id`. Keep before/after correction history when changing claims. Reviewer coverage is not full semantic proof.

`conflicts.json` is always present with `conflicts[]` (possibly empty). Each suspected source/bytecode discrepancy records `id`, `source_file`, source lines, `original_proto`, `original_pcs`, existing original JSON path/hash, `observation`, `derivation`, `specification_override` or `unresolved`, `scope_limit`, and affected claims. It never modifies a source snapshot or issues a new CFG/behavior certificate. Unknowns track unresolved consequences.

## Audit/package outputs

`audit.json` reports integrity errors, unsupported certainty claims, ownership/review coverage and warnings. Canonical `rules.json` keeps worker assertions separate from computed source/body/module/behavior evidence. The package contains those ledgers, catalog, per-system docs/data/contracts, input/evidence hashes, original bytecode when supplied, dependency graph, content/interface/test/unknown JSON, a browsable index, `implementation-spec.md`, `summary.json`, `manifest.json`, and ZIP. `validate` rechecks relocation-safe file hashes and local source/proof targets. It is not a replacement for cross-review.

Top-level `status` is the mechanical operation/audit result. `analysis_status` is independently PARTIAL, IN_SCOPE_REVIEWED, FAILED or NOT_ASSESSED. `completion` reports functions_reviewed/functions_total (supported author + frozen trace); functions_declared_reviewed records the raw claim even on failed audits. Other metrics are functions_unanalyzed, functions_surveyed, functions_trace_supported, functions_trace_unsupported, source_review_functions_traced, material_relations_untraced/material_relations_total, unknowns_remaining/unknowns_total and scope_modules/owned_modules (semantic scope versus source-backed modules the catalog assigns to systems). Packages and reports display these metrics prominently; a passing audit does not erase remaining work.
