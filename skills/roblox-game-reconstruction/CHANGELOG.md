# Changelog

## 0.1.4

Runs prepared by earlier releases are not audited; prepare a new run.

Validation
- `audit` accepts only runs whose `config.json` `skill_version` matches this release; a missing or malformed version fails instead of selecting a legacy contract. Frozen source reviews whose traced functions lack traces fail.
- An analysis UNKNOWN may be `RESOLVED` only when its audit is `SOURCE_FOUND` with linked rules.
- Every source-backed module a catalog system owns counts toward completion; a narrower semantic scope stays PARTIAL (`scope_omits_owned_module`). Completion reports `scope_modules`/`owned_modules`.
- `validate-worker` checks the documented interface fields; remote fields nested inside `endpoint` are reported as misplaced. It warns when a worker's own records cite a function still listed as UNANALYZED (`cited_function_unanalyzed`).
- One definition of source line counting (LF only), finished drafts (explicit `draft:false`) and repository path spelling across all stages.
- Source-only records are listed as unsupported missing modules instead of stopping `prepare`.
- Malformed integration input is a structured CLI error; `package` and `compare` refuse to overwrite outputs.

Agent input and output
- `audit`, `check-plan`, `integrate`, `validate-worker` and `normalize-worker` print at most 20 entries per issue list with counts by issue; `--full` prints everything and `audit.json` keeps the complete result.
- `query modules|functions|remotes|requires|cfg` returns compact rows filtered by system, module, path, name or `FILE:LINE`; `--unowned` lists modules the catalog neither owns nor excludes.
- `cite FILE:START[-END]` prints the canonical citation for the innermost registered function.
- Trace citations may use `{"lines":[start,end]}`; `normalize-worker` expands them and validates. `validate-worker` stays read-only.
- Diagnostics show cited and allowed line ranges, the values involved and how to fix them.

Outputs
- Large registry files are compact JSON with one record per line.
- `functions.json` schema v2 states behavior, mapping scope and certificate scope text once; full certificate and diagnosis records are read with `query cfg --function ID`.
- `implementation-spec.md` starts with the system specifications, then generated tables of interfaces, content and open questions, then remaining work summarized by kind.

Performance
- `audit` and `integrate` load the registry once; quadratic scans in inventory, semantic audit and integration are indexed.

## 0.1.3

- Every TRACED/REVIEWED function requires a function-specific trace with ordered steps citing its own body.

## 0.1.2

- `scaffold` and `validate-worker`; source reviews are preflighted before freeze.
- `audit` status is separate from `analysis_status`; packages report completion metrics.

## 0.1.1

- Independent source-first review (`source-review/v1`), `freeze-review`, and the semantic-coverage ledger and audit.

## 0.1.0

- Inventory, parallel analysis, evidence audit and packaging.
