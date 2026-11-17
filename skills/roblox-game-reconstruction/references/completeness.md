# Source-first completeness and evidence audit

This protocol audits declared source coverage. It is not a search engine, a call-graph engine or a proof that all possible game rules were discovered. Use existing `rg`, file reads, shared functions and dependency candidates. Retain scope and cost. Important rules concern outcomes, acceptance conditions, state/units, choices, effects and lifecycle; purely cosmetic details may be excluded with reasons. Do not make every line equally important.

## 1. Bounded function and interaction analysis

Choose modules from actual source and state ownership. Account for every registry function in selected modules as `UNANALYZED`, `SURVEYED`, `TRACED` or `REVIEWED`. Reading a declaration or dependency is SURVEYED. TRACED means its meaningful inputs/branches/data/calls/effects were followed and recorded in a function-specific trace with checked own-body citations. An unexplored meaningful path remains in trace.limits/remaining; reading a declaration or isolated call alone stays SURVEYED. REVIEWED additionally needs a supported author trace, a frozen independent trace for the same body and a comparison decision. See the trace contract in schemas.md. Do not infer these states from citation counts, assigned module counts or file existence.

For each consequential interaction, trace the actual receiver and connect trigger → state/data → predicate/selection → call/request → result/effect → cleanup/reset. Follow imports, constructors, setters/getters, callers, event subscriptions and data consumers until a supported endpoint or an explicit boundary. Keep omitted/default arguments, closures, nil/false, units and effect order. State the relation, not just two disconnected function summaries. Client checks, confirmation text, local state updates and a server request are different evidence; none alone proves a server transaction.

Content numbers require a consumer/meaning binding or an explicit unresolved/non-mechanic disposition. Never infer units, eligibility or resource identity from magnitude, formatting or a neighboring table. Do not convert incomplete evidence into an implicit implementation choice. Independent designs are separate alternatives, not recovered facts.

## 2. Independent source-only pass, then comparison

An omission scout receives only this skill, raw repository/shared registry, selected entrypoints and an output directory. It does **not** receive the suspected omission, expected answer, existing claims, complete reference specification or parent chat. First enumerate material source observations and relationships, inspect alternatives and record functions not traced. Freeze this source-only inventory before exposing the specification. A prompt allowlist is not an OS sandbox; record access limitations honestly.

Source-first file contract (`source-review/v1`):

```json
{
  "schema":"source-review/v1", "reviewer":"agent-id", "specification_seen":false,
  "scope":{"module_ids":[],"entrypoints":[],"limits":"bounded slice"},
  "functions":[{"function_id":"actual-id","status":"TRACED","observation_ids":["O1"],"relation_ids":["E1"],"trace":{"inputs":[],"steps":[{"action":"Specific source operation","evidence":[{"source_file":"src/example.luau","function_id":"actual-id","line_start":1,"line_end":1}]}],"outputs":[],"limits":[]},"remaining":[]}],
  "observations":[{"id":"O1","category":"state","importance":"material","statement":"Concrete observed fact","evidence":[]}],
  "relations":[{"id":"E1","from":"O1","to":"O2","kind":"guard_controls_action","importance":"material","statement":"Concrete relationship","trace_function_ids":["actual-id"],"evidence":[]}],
  "unknown_candidates":[], "cost":{"files_read":0,"functions_traced":0,"searches":0,"elapsed_seconds":0},
  "limitations":[]
}
```

IDs are unique within the file. Categories and relation kinds are descriptive, not a fixed game-system list. Evidence uses existing repository-relative `source_file`, line ranges and source-body `function_id`; if unmapped, use null and `mapping_limit`. Cross-module relationships cite both ends and actual bindings, not equal method names alone. Keep unsupported interpretation as `candidate`, not a material fact. Record measured cost or null with a reason; do not invent timing/token metrics.

Run `python scripts/reconstruct.py freeze-review --run RUN --id REVIEW_ID --input FILE`. It preserves the source-only bytes, their hash and input identity. Then compare the frozen obligations with the specification. Seek missing edges, wrong receivers/units/defaults, contradictory effects and counterexamples; checking that an existing citation is valid is insufficient. Preserve before/after corrections and rejected false alarms. Another author must not overwrite the frozen inventory.

## 3. Integration ledger

0.1.2 preflights each source review before freezing: schema/types, IDs, module/function scope, exact source hashes/ranges, endpoints and trace references must pass before any frozen file or pin is written. A traced relation needs nonempty `trace_function_ids`. For a genuinely unsupported boundary use `trace_status:UNRESOLVED` and `trace_limit`, rather than inventing an ID. Account for all registry functions in scope. Scaffolds start with `draft:true` and UNANALYZED functions; perform analysis and set draft false before validation. These checks do not establish callee identity or prose entailment.

`scaffold --kind analysis --system SYSTEM` creates the canonical worker directory. `scaffold --kind source-review --id REVIEW --module-id MODULE_ID` creates a source-only draft. Both take the prepared root as --run and refuse overwrites. `validate-worker` checks one worker without completed neighboring outputs or file mutation. See INSTALL.md for full commands.

Use `scaffold --kind integration` and finish its explicit decisions, then `integrate --input FILE` to join worker traces and frozen obligations and rebuild reciprocal rule links. The command does not decide which rule represents an observation or whether an unknown was resolved. See schemas.md.

`semantic-coverage.json` (`rgr-semantic-coverage/v1`) has:

- `scope`: `module_ids`, rationale, `excluded_subject_ids` for any explicitly excluded material obligations, and `declared_full_game:false` unless fully justified. Exclusions require impact and keep the result PARTIAL. Every source-backed module owned by a catalog system counts toward completion: omitting one from `module_ids` records `scope_omits_owned_module` and keeps the result PARTIAL. Only catalog exclusions with reason and impact are outside this denominator.
- `functions[]`: actual registry `function_id`, status above, `observation_ids`, `relation_ids`, `remaining` and the required function-specific `trace` for TRACED/REVIEWED. A rationale cannot replace that trace. Include UNANALYZED entries, not only successful ones. No reliable registry mapping means coverage remains limited, not complete.
- `observations[]` and `relations[]`: merge the source-only records without silently dropping them. Prefix IDs by review ID when necessary and retain `origin:{review_id,item_id}`. A relation has `from`, `to`, `kind`, `statement`, `importance`, source `evidence`, `trace_function_ids` and `trace_status` (`TRACED`, `UNRESOLVED` or `EXCLUDED`). A useful scope boundary is an explicit unresolved trace, not a guessed connection.
- `mappings[]`: one per observation/relation, with `subject_id`, `status` (`REPRESENTED`, `UNKNOWN`, `EXCLUDED`, `MISSING`, `CONTRADICTED`, `UNREVIEWED`), `rule_ids`, rationale and optional `unknown_ref`. For UNKNOWN, use `unknown_ref:{"system_id":"owner-system","unknown_id":"local-unknown-id"}` (an object, not a combined string). Represented records must be cross-linked from the actual claim/contract's `semantic_refs`; IDs alone are not semantic proof. Review the mapped statement's meaning. Material MISSING/CONTRADICTED/UNREVIEWED blocks a clean audit until fixed or explicitly moved outside scope with impact. UNKNOWN requires an investigated question and cannot erase available client evidence.
- `source_reviews[]`: `review_id` for each frozen file. Audit imports its obligation list and detects silent integration loss; hashes check preservation, not mental independence.
- `unknown_audits[]`: every `analysis/<system>/analysis.json` unknown gets `system_id`, `unknown_id`, `status`, `question`, `searched_sources`, `queries`, `inspected_function_ids`, evidence, `resolved_rule_ids`, `remaining_question` and rationale. Status is `SOURCE_FOUND`, `PARTIALLY_SUPPORTED`, `NOT_FOUND_IN_SEARCHED_SCOPE`, `UNAVAILABLE_SOURCE` or `NOT_INVESTIGATED`. Searches are actual bounded logs (tool/query/scope/results), not a claim of repository-wide absence. SOURCE_FOUND must resolve the matching unknown or split it into supported client facts and a precisely narrower remaining question. Marking the analysis unknown `status:RESOLVED` under any other audit status fails the audit. A server-only question may remain unavailable; distinguish it from an unexamined client rule.
- `data_bindings[]`: for each content item, `system_id`, `content_id`, `status` (`BOUND`, `PARTIAL`, `UNBOUND`, `NON_MECHANIC`), `relation_ids` and rationale. Unbound important content is incomplete even if every scalar was extracted.
- `completeness_claim`: `PARTIAL` or `IN_SCOPE_REVIEWED`. The latter requires all selected functions reviewed and all material obligations/traces resolved. It never means universal semantic equivalence or full server knowledge.

Package these ledgers, canonical checked results and frozen reviews with the ordinary specifications. Show unresolved relations/UNKNOWNs and remaining functions prominently. Their existence is permitted in a partial package; hiding them as complete is not. A run without this ledger fails the audit. `config.json` must carry the `skill_version` written by `prepare` for this release; a missing, malformed or different version fails the audit. Runs from other releases are not migrated or audited under older contracts: prepare a new run.

### Attribution errata and semantic corrections

Keep frozen errors inspectable. A ledger subject may add `citation_erratum:{rationale,evidence:[...]}` to correct a mistaken source-body ID while retaining the original evidence unchanged. Corrected evidence must preserve each source file and line range exactly; the auditor rechecks the corrected body/source association and records a warning and checked CFG scope. A relation may add `trace_id_erratum:{rationale,function_ids:[...]}` with the same number of corrected IDs, each supported by its effective citations. Correct registry spellings in the function accounting with an explicit rationale; do not claim a new blind pass. These narrow attribution corrections cannot change code, statements, endpoints, importance, or frozen bytes. A wrong semantic statement remains in the raw review with an explicit excluded/superseded disposition and impact; add a new source-backed correction obligation/claim and retain the before/after comparison. Never publish a known wrong raw statement as a recovered rule just to preserve counts.

For a frozen relation originally marked `UNRESOLVED`, follow-up tracing uses `trace_resolution:{rationale,function_ids:[...],evidence:[...]}` on the integrated relation, plus `trace_status:TRACED`. Keep its original `trace_function_ids` and other frozen fields unchanged. Resolution IDs must retain any known trace IDs and be supported by checked source/body citations; the original review bytes/pin stay intact. This is follow-up analysis, not an additional blind review or a new CFG certificate. Invalid or missing evidence fails the audit, and unchanged unresolved traces remain PARTIAL.

For an invalid classification spelling, `classification_erratum:{rationale,importance}` may normalize to material/supporting/candidate while retaining the raw label. It cannot demote an original material obligation; use explicit scope exclusion with impact for that. This is a schema correction, not a shortcut around independent semantic judgment. Retain attribution/classification corrections prominently in review history and audit warnings.

## 4. Recheck UNKNOWNs and evaluate errors

Before accepting a missing-rule finding, inspect the catalog, ownership and all relevant published system outputs; an economy checkpoint alone cannot establish that a rule owned elsewhere is missing. Before accepting a call relationship, cite the actual call and receiver/require binding; same-purpose helper names are not evidence. For recovery/reactivation claims, trace identical/repeated inputs, early-return guards, nil/reset paths and lifecycle replacement. Record the tested counterexample and source branch, or leave the interpretation unresolved. These are reviewer checks, not automated semantic certificates.

Before assigning UNKNOWN, inspect the source that reads/checks/displays/requests/writes the same state; follow related caller/callee and content consumer. Ask which part is unknown. Reuse an already recorded investigation when it addresses the same question, but keep its scope/evidence. If evidence exists, add the fact and relation instead of retaining a blanket unknown. Do not certify server behavior from client intent.

Evaluation freezes source-only output before answer-aware comparison. Report discovered omissions, rejected false positives and known missed sentinels separately, with measured files/functions/searches/time. Seeded missing-edge, wrong-link, false-UNKNOWN and false-completion controls test bookkeeping; they do not prove semantic understanding. Whole-game analysis is unnecessary for a bounded regression. Keep game-specific fixtures and reference answers outside the portable skill and source workers' allowlists.
