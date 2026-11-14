# Roles, file sharing and context limits

The coordinator owns system discovery, task boundaries, reconciliation and acceptance. It must perform useful source reads; it does not delegate architecture decisions to a blanket prompt. Discover this input's systems before selecting workers. Use host concurrency limits and the user's model/delegation preferences; the skill does not require a particular provider/model.

Workers own independent slices with substantial work. Give each an exact module-ID/path list, relevant dependencies and one `analysis/SYSTEM/` directory. They read common immutable files and write only their own output. Communicate cross-system inputs/outputs early; let one worker own a shared formula and other workers cite its contract. Do not have the coordinator redo a delegated analysis while it is running.

Common files:

- `shared/modules.json`: instance paths, hashes, source snapshot paths, module status.
- `shared/functions.json`: recompiled source-body IDs and actual original-body certificate matches; not index equality.
- `shared/tables/` and `numeric-constants/`: constructor data and raw original payloads. Dynamic expressions and later mutations are not evaluated.
- `catalog.json` and `analysis-plan.json`: ownership, discovery reasons, exclusions, interfaces to inspect.
- `analysis/SYSTEM/analysis.json`, `spec.md`, optional `progress.json`: results and remaining work.
- `review.json`, `conflicts.json`: coordinator/reviewer decisions. Workers do not silently rewrite other systems.
- `source-reviews/ID/`: frozen source-only obligations and pinned manifest; compared only after freezing.
- `semantic-coverage.json`: coordinator-integrated observations/relationships, rule mappings, function states, UNKNOWN investigations and content bindings. Preserve source-review origins; do not silently drop disagreements.
- `semantic-audit.json`: checked accounting and explicit remaining work. It cannot judge prose truth or discover all omitted rules.

Bound assignments by state ownership and dependency surface, not an arbitrary equal file count. Large configuration/UI groups may need follow-up waves. Give workers function lists and relevant data paths rather than whole files in the task prompt. Have a worker persist checkpoints before its context limit; resume from files and its remaining-function list. A second worker should consume completed work, not rediscover the same slice.

Reviewer allocation is separate. It checks high-impact cross-system invariants and missing coverage rather than repeating the entire analysis. Target units, operator ordering, signal vs commit, request parent/name/payload, default and false/nil branches, time domains, place-specific overrides and CFG scope. Escalate narrow defects to the owning worker, accept its diff/evidence, and record what changed.

Allocate an omission scout before specification exposure. Its distinct task is source-first material-obligation discovery, not repeating the author's document. Bound the source slice and account for untouched functions. Freeze its output, then give it the actual specification for missing-edge and counterexample comparison. A coordinator integrates ledger IDs and actual claim semantic_refs. Assign follow-up tracing to one owner per unresolved boundary. Review independence is a recorded access protocol, not an OS-enforced guarantee.

If concurrent workers fail or are unavailable, continue with the existing artifacts and independent remaining work. Mark which slices/reviews were coordinator-only, sampled, partial or untested. Never fabricate a successful subagent run.
