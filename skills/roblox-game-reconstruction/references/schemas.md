# File contracts v1

V1.1 adds the source-review/v1 and rgr-semantic-coverage/v1 contracts in [completeness.md](completeness.md). These are required for newly prepared runs; the ordinary file contracts below remain compatible. Claims/contracts use `semantic_refs:["obligation-id"]` and the ledger maps those IDs back to canonical `system:claim-id` entries. Every content item has a data_bindings disposition. Every unknown has an unknown_audits record, including NOT_INVESTIGATED when work remains.

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
  "coverage":{"module_ids":["m:actual-id"],"function_ids_analyzed":[],"remaining":[]}
}
```

The example uses placeholders only to explain fields; real runs use inventory IDs and real claims. Include `spec.md` with detailed implementation procedures. Claims and contracts must not be prose-only module inventories.

Claim `kind` is `CODE_OBSERVED`, `STATIC_INFERENCE`, `UNVERIFIED_BEHAVIOR`, or `INDEPENDENT_DESIGN`. `STATIC_INFERENCE` needs `derivation` and `assumptions`; `INDEPENDENT_DESIGN` needs `rationale` and must be separated from recovered facts. Optional `entry_kind` distinguishes `implementation_rule`, `function_evidence_record` and `function_contract`; counting evidence records is not counting recovered gameplay rules. Worker contracts should include `kind` when they are inferred rather than directly observed.

Evidence requires `source_file`, `line_start`, `line_end`. `function_id` is the **recompiled source-body** ID in shared/functions.json. If no mapping exists, use `function_id: null`, give `function` and `mapping_limit`; the auditor leaves function CFG UNVERIFIED. A worker may provide expected `source_sha256`, `bytecode_sha256`, and `claimed_cfg`; mismatches are reported. Do not map an original proto index to source by numerical equality. A source span outside the body's recorded own-instruction range needs separate citations/unknown mapping, not an invented lexical bound.

`content[]` entries: `id`, `value`, `units`, `evidence`, optionally `literal`, `expression`, `data_ref` and `fidelity`. Dynamic expressions remain strings with dependencies. Raw constructor files can be referenced instead of copied, but domain-specific interpretation must identify keys and consumers.

`interfaces[]`: `id`, `inputs`, `outputs`, `direction`, `state_owner`, `evidence` or `claim_ids`; remotes additionally need `endpoint` (`name`, `parent` expression/scope), `positional_args`, `result`, `authority_limit` and `unknowns`. Wrapper identity must be traced before engine API meaning is applied.

`relationships[]`: `source`, `target`, `relation`, `claim_ids` or `evidence`, `scope`. Relation is explicit (`calls`, `reads`, `writes`, `order_precedes`, `display_dependency`, etc.). Do not turn observed order/display/request into `requires` or committed server state without evidence.

`tests[]`: `id`, `inputs`, `expected` (or explicit unknown), `claim_ids`/`evidence`, `status: SPECIFIED_NOT_EXECUTED`, `executed: false`. Authored reference-model executions belong in separate logged test evidence; do not relabel game behavior.

`unknowns[]`: `id`, `question`, `impact`, `missing_evidence`, optional affected `claim_ids` and `evidence`. Coverage lists the modules/functions actually analyzed and remaining gaps; listing an assigned module does not imply all of its functions are understood.

## Review and conflicts

`review.json`: `mode` (`independent_agent`, `coordinator_only`, `mixed`), `reviewer`, `checked_system_ids`, `checked_scope`, `findings[]`, `limitations`. Each finding has `id`, `status` (`consistent`, `conflict`, `unresolved`, `resolved`, `missing_coverage`), `severity`, `affected_claim_ids`, `detail`, `evidence` when sourced, `resolution` or `unknown_id`. Keep before/after correction history when changing claims. Reviewer coverage is not full semantic proof.

`conflicts.json` is always present with `conflicts[]` (possibly empty). Each suspected source/bytecode discrepancy records `id`, `source_file`, source lines, `original_proto`, `original_pcs`, existing original JSON path/hash, `observation`, `derivation`, `specification_override` or `unresolved`, `scope_limit`, and affected claims. It never modifies a source snapshot or issues a new CFG/behavior certificate. Unknowns track unresolved consequences.

## Audit/package outputs

`audit.json` reports integrity errors, unsupported certainty claims, ownership/review coverage and warnings. Canonical `rules.json` keeps worker assertions separate from computed source/body/module/behavior evidence. The package contains those ledgers, catalog, per-system docs/data/contracts, input/evidence hashes, original bytecode when supplied, dependency graph, content/interface/test/unknown JSON, a browsable index, `implementation-spec.md`, `summary.json`, `manifest.json`, and ZIP. `validate` rechecks relocation-safe file hashes and local source/proof targets. It is not a replacement for cross-review.
