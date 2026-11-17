# Evidence and semantic limits

Use the input report as the provenance root. Hash source and bytecode before analysis, preserve exact source bytes and instance identities, and record existing proof-file hashes. Never use a report's aggregate success count to certify an individual source function.

The reusable inventory reads **recompiled** proto metadata to locate source bodies, then follows `matching_recovered_protos` in existing comparison JSON to original body certificates. Original and recompiled indices/names can differ. Declaration and own-instruction lines are not full lexical boundaries; nested callbacks need their own evidence. A root/module certificate must include its referenced prototypes/cache identities, not merely one matching body elsewhere.

Audit citations use explicit function IDs. Name similarity, require-path candidates and source containment are investigative leads only. If CFG data is missing, source can be described with an explicit mapping limit; its status stays UNVERIFIED. If a worker writes CFG_VALIDATED but no certificate supports that body, remove the assertion and retain the metadata correction. No static helper grants BEHAVIOR_VERIFIED to game code.

Separate evidence categories:

- CODE_OBSERVED: recovered implementation text, with syntax/CFG trust stated separately.
- STATIC_INFERENCE: derived formula/path reasoning; show the derivation and assumptions.
- UNVERIFIED_BEHAVIOR: not established by available static evidence/runtime; do not turn a UI hint or request into a server rule.
- INDEPENDENT_DESIGN: a chosen implementation policy, explicitly not recovered original behavior.

When source appears inconsistent, first check input/hash/optimization and receiver/function correspondence. Read existing original and recompiled instruction JSON. Trace concrete reachable PCs, register definitions, branch targets, call arities, effects and constants as needed. A local argument/branch correction proves only that local path, not the entire function. Write it to conflicts.json with immutable raw source, original JSON/bytecode provenance, derivation, assumptions and unresolved scope. Do not insert guessed defaults, rewrite recovered source or suppress a warning to make the audit pass.

Literal extraction preserves numeric text and, when supplied, original IEEE payloads. A source table constructor is not an evaluated final module: later writes, overrides, closures and engine constructors may change results. The optional reused literal-only assembler can establish a table's field values from supported linear original instructions; it rejects calls/control flow and is not a general interpreter, CFG checker or game executor. Keep data validation distinct from function/behavior statuses.

Important semantic review checks from the actual workflow:

- Encoded values versus ordinary counts/times/factors; false/nil versus zero/empty string in Luau.
- Initial availability versus reactive callbacks and UI buttons: predicates can legitimately differ.
- Affordability presentation versus server validation; request signal versus committed mutation.
- Same remote name with different parent Instances; custom wrapper versus engine method.
- Property text, notification duration and client chart bounds versus server policy/expiry/payout.
- Purchase ordering versus hard prerequisites; persisted-looking replicated state versus save format.
- Config rows versus absent instance attributes; place overrides versus base tables.
- Cleanup only on success paths, reentry guards spanning awaits, and state prediction rollback.

These are review targets, not assumptions that any particular game contains those systems.

Since 0.1.1, valid evidence alone is insufficient: an important source observation or relationship must also have a reviewed disposition and a matching implementation rule. Use completeness.md for the independent source-first pass, frozen inventory, reciprocal semantic_refs and UNKNOWN audit. The semantic auditor exposes the same body/module/behavior boundaries in checked_evidence; its coverage state is not a new CFG or behavior certificate. A superficially reciprocal but semantically wrong mapping must be caught by independent comparison and counterexamples, not certified by matching IDs.
